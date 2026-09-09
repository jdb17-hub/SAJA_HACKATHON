"""Motor de inferencia QVAC.

Toda la IA de esta aplicacion pasa por aqui, y todo ocurre en este dispositivo:
el SDK arranca un worker local (Bare) que carga un GGUF desde ~/.qvac/models y
ejecuta la inferencia en CPU/GPU local. No hay ninguna llamada a una API de
inferencia en la nube. Lo unico que sale a red es la descarga inicial del
fichero del modelo; despues la app funciona en modo avion.

Streamlit re-ejecuta el script en cada interaccion, asi que el cliente QVAC vive
en un hilo propio con su event loop, y los modelos se cargan una sola vez.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config


def _preparar_entorno() -> str | None:
    """Deja QVAC_SDK_DIR listo antes de importar el SDK (necesario en Windows)."""
    sdk_dir = config.resolve_qvac_sdk_dir()
    if sdk_dir:
        os.environ.setdefault("QVAC_SDK_DIR", sdk_dir)
    return sdk_dir


@dataclass
class EstadoMotor:
    listo: bool = False
    error: str | None = None
    sdk_dir: str | None = None
    llm_model: str = ""
    stt_model: str = ""
    llm_cargado: bool = False
    stt_cargado: bool = False
    segundos_carga: float = 0.0
    ultima_latencia: float = 0.0
    inferencias: int = 0
    ficheros_modelo: list[str] = field(default_factory=list)


class MotorQVAC:
    """Envoltorio sincrono sobre el SDK asincrono de QVAC."""

    def __init__(self) -> None:
        self.estado = EstadoMotor(llm_model=config.LLM_MODEL, stt_model=config.STT_MODEL)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._hilo: threading.Thread | None = None
        self._cliente = None
        self._transport = None
        self._llm_id: str | None = None
        self._stt_id: str | None = None
        self._lock = threading.Lock()
        self.progreso_descarga: float | None = None

    # --- ciclo de vida ------------------------------------------------------

    @staticmethod
    def _nuevo_loop() -> asyncio.AbstractEventLoop:
        """Un event loop capaz de lanzar subprocesos.

        QVAC arranca el worker como proceso hijo (`bare.exe`). En Windows eso
        solo funciona sobre ProactorEventLoop, y Tornado -- del que depende
        Streamlit -- instala la politica Selector al importarse, que no lo
        soporta y falla con un NotImplementedError sin mensaje. Por eso el loop
        se construye a mano en vez de con `new_event_loop()`.
        """
        if sys.platform == "win32":
            return asyncio.ProactorEventLoop()
        return asyncio.new_event_loop()

    def _arrancar_loop(self) -> None:
        if self._hilo is not None:
            return
        listo = threading.Event()

        def correr() -> None:
            self._loop = self._nuevo_loop()
            asyncio.set_event_loop(self._loop)
            listo.set()
            self._loop.run_forever()

        self._hilo = threading.Thread(target=correr, name="qvac-loop", daemon=True)
        self._hilo.start()
        listo.wait(timeout=10)

    def _ejecutar(self, coro, timeout: float = 600.0):
        self._arrancar_loop()
        assert self._loop is not None
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    def iniciar(self) -> EstadoMotor:
        """Arranca el worker y carga el modelo de lenguaje. Idempotente."""
        with self._lock:
            if self.estado.listo:
                return self.estado
            t0 = time.time()
            try:
                self.estado.sdk_dir = _preparar_entorno()
                from tetherto.qvac_sdk import Client, load_model
                from tetherto.qvac_sdk import models as catalogo

                if self._cliente is None:
                    self._cliente = Client()
                    self._ejecutar(self._cliente.__aenter__(), timeout=120)
                    self._transport = self._cliente.transport

                def progreso(p: Any) -> None:
                    self.progreso_descarga = getattr(p, "percentage", None)

                src = getattr(catalogo, config.LLM_MODEL)
                self._llm_id = self._ejecutar(
                    load_model(self._transport, model_src=src, on_progress=progreso),
                    timeout=3600,
                )
                self.progreso_descarga = None
                self.estado.llm_cargado = True
                self.estado.listo = True
                self.estado.error = None
                self.estado.segundos_carga = time.time() - t0
                self.estado.ficheros_modelo = _ficheros_modelo()
            except Exception as exc:  # noqa: BLE001 - se muestra en la UI
                self.estado.error = f"{type(exc).__name__}: {exc}"
                self.estado.listo = False
                # Un cliente a medias haria que el siguiente intento se saltara
                # el arranque y fallara mas tarde con un error sin relacion
                # ("NoneType has no attribute call_stream"), escondiendo la causa.
                self._cliente = None
                self._transport = None
            return self.estado

    def asegurar_stt(self) -> bool:
        """Carga Whisper la primera vez que se usa la voz."""
        with self._lock:
            if self._stt_id:
                return True
            try:
                from tetherto.qvac_sdk import load_model
                from tetherto.qvac_sdk import models as catalogo

                def progreso(p: Any) -> None:
                    self.progreso_descarga = getattr(p, "percentage", None)

                self._stt_id = self._ejecutar(
                    load_model(
                        self._transport,
                        model_src=getattr(catalogo, config.STT_MODEL),
                        model_type="whisper",
                        on_progress=progreso,
                    ),
                    timeout=3600,
                )
                self.progreso_descarga = None
                self.estado.stt_cargado = True
                self.estado.ficheros_modelo = _ficheros_modelo()
                return True
            except Exception as exc:  # noqa: BLE001
                self.estado.error = f"STT: {type(exc).__name__}: {exc}"
                return False

    def cerrar(self) -> None:
        try:
            if self._cliente is not None:
                self._ejecutar(self._cliente.__aexit__(None, None, None), timeout=30)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._cliente = None
            self.estado.listo = False

    # --- inferencia ---------------------------------------------------------

    def _completar(self, history: list[dict], response_format: dict | None, max_tokens: int) -> str:
        from tetherto.qvac_sdk import completion

        params = dict(config.GENERATION_PARAMS)
        params["predict"] = max_tokens

        async def tarea() -> str:
            run = completion(
                self._transport,
                model_id=self._llm_id,
                history=history,
                stream=False,
                generation_params=params,
                response_format=response_format,
            )
            return await run.text()

        t0 = time.time()
        salida = self._ejecutar(tarea(), timeout=300)
        self.estado.ultima_latencia = time.time() - t0
        self.estado.inferencias += 1
        return salida or ""

    def json_estructurado(
        self,
        *,
        sistema: str,
        mensajes: list[dict],
        esquema: dict,
        nombre: str = "resultado",
        max_tokens: int = 900,
    ) -> dict:
        """Completion con decodificacion restringida por JSON Schema.

        QVAC aplica el esquema durante el muestreo, asi que la salida es JSON
        valido y con los enums correctos por construccion: no hay que reparar
        texto ni reintentar por formato.
        """
        if not self.estado.listo:
            raise RuntimeError("El motor QVAC no esta iniciado")
        history = [{"role": "system", "content": sistema}, *mensajes]
        crudo = self._completar(
            history,
            {
                "type": "json_schema",
                "json_schema": {"name": nombre, "schema": esquema, "strict": True},
            },
            max_tokens,
        )
        return _parsear_json(crudo)

    def texto(self, *, sistema: str, mensajes: list[dict], max_tokens: int = 300) -> str:
        if not self.estado.listo:
            raise RuntimeError("El motor QVAC no esta iniciado")
        history = [{"role": "system", "content": sistema}, *mensajes]
        return self._completar(history, None, max_tokens).strip()

    def transcribir(self, ruta_wav: str | Path, prompt: str | None = None) -> str:
        """Voz -> texto con Whisper, tambien en el dispositivo."""
        if not self.asegurar_stt():
            raise RuntimeError(self.estado.error or "No se pudo cargar el modelo de voz")
        from tetherto.qvac_sdk import TranscribeRequest, transcribe

        peticion = TranscribeRequest(
            model_id=self._stt_id,
            audio_chunk={"type": "filePath", "value": str(ruta_wav)},
            prompt=prompt,
        )

        async def tarea() -> str:
            partes: list[str] = []
            async for evento in transcribe(self._transport, peticion):
                if getattr(evento, "error", None):
                    raise RuntimeError(evento.error)
                if getattr(evento, "text", None):
                    partes.append(evento.text)
            return "".join(partes)

        t0 = time.time()
        salida = self._ejecutar(tarea(), timeout=600)
        self.estado.ultima_latencia = time.time() - t0
        return (salida or "").strip()


# --- utilidades -------------------------------------------------------------


def _ficheros_modelo() -> list[str]:
    """Los GGUF en cache local: la prueba fisica de que la inferencia es local."""
    carpeta = Path.home() / ".qvac" / "models"
    if not carpeta.exists():
        return []
    return [
        f"{p.name}  ({p.stat().st_size / 1e6:.0f} MB)"
        for p in sorted(carpeta.glob("*"))
        if p.is_file()
    ]


def _parsear_json(crudo: str) -> dict:
    """El esquema garantiza JSON valido, pero algunos modelos anteponen <think>."""
    texto = (crudo or "").strip()
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio != -1 and fin > inicio:
        try:
            return json.loads(texto[inicio : fin + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"El modelo no devolvio JSON utilizable: {texto[:300]}")


_MOTOR: MotorQVAC | None = None


def obtener_motor() -> MotorQVAC:
    """Instancia unica por proceso."""
    global _MOTOR
    if _MOTOR is None:
        _MOTOR = MotorQVAC()
    return _MOTOR
