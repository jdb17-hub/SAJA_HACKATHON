"""Prueba de la ruta de voz: fichero de audio -> Whisper -> extraccion.

Cubre los tres fallos que rompieron el dictado en su dia, para que no vuelvan:
  1. WAV en coma flotante de 32 bits (lo que graba el navegador).
  2. La peticion de transcripcion sin el campo `type`.
  3. Whisper traduciendo al ingles por no declarar el idioma.

    python scripts/test_audio.py [ruta_de_audio.ogg ...]
"""
from __future__ import annotations

import math
import struct
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.audio import AudioNoSoportado, detectar_extension, duracion_segundos, guardar_audio  # noqa: E402
from src.config import STT_PROMPT  # noqa: E402
from src.extract import extraer  # noqa: E402
from src.qvac_engine import obtener_motor  # noqa: E402

TMP = Path(tempfile.gettempdir())


def wav_float32(segundos: float = 2.0, sr: int = 48000) -> bytes:
    """Un WAV float32 estereo como el que produce el grabador del navegador."""
    n = int(sr * segundos)
    cuerpo = b"".join(
        struct.pack("<ff", 0.2 * math.sin(2 * math.pi * 440 * i / sr), 0.0) for i in range(n)
    )
    cabecera = b"RIFF" + struct.pack("<I", 36 + len(cuerpo)) + b"WAVE"
    cabecera += b"fmt " + struct.pack("<IHHIIHH", 16, 3, 2, sr, sr * 8, 8, 32)
    cabecera += b"data" + struct.pack("<I", len(cuerpo))
    return cabecera + cuerpo


def probar_formatos() -> bool:
    """Que el WAV float32 del navegador ya no se atragante."""
    print("=" * 70)
    print("1. FORMATOS (sin modelo)")
    print("=" * 70)
    ok = True
    datos = wav_float32()
    try:
        ruta = guardar_audio(datos, TMP / "prueba_nota")
        dur = duracion_segundos(ruta)
        bien = ruta.suffix == ".wav" and 1.9 < dur < 2.1
        print(f"  [{'OK ' if bien else 'FAIL'}] WAV float32 estereo 48 kHz -> {ruta.name}, {dur:.2f}s")
        ok &= bien
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] WAV float32 -> {type(exc).__name__}: {exc}")
        ok = False

    for firma, esperado in [(b"OggS" + b"\0" * 60, ".ogg"), (b"ID3" + b"\0" * 60, ".mp3")]:
        try:
            bien = detectar_extension(firma) == esperado
            print(f"  [{'OK ' if bien else 'FAIL'}] cabecera {firma[:4]!r} -> {esperado}")
            ok &= bien
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] {firma[:4]!r} -> {exc}")
            ok = False

    try:
        detectar_extension(b"\x1aE\xdf\xa3" + b"\0" * 60)  # webm, no soportado
        print("  [FAIL] un contenedor no soportado deberia dar error claro")
        ok = False
    except AudioNoSoportado:
        print("  [OK ] un contenedor no soportado da un error claro y no un crash")
    return ok


def probar_transcripcion(rutas: list[Path]) -> bool:
    print()
    print("=" * 70)
    print("2. TRANSCRIPCION CON QVAC (en el dispositivo)")
    print("=" * 70)
    motor = obtener_motor()
    estado = motor.iniciar()
    if not estado.listo:
        print(f"  QVAC no disponible: {estado.error}")
        return False
    if not motor.asegurar_stt():
        print(f"  Whisper no disponible: {estado.error}")
        return False
    print(f"  modelo de voz: {estado.stt_model}\n")

    ok = True
    for ruta in rutas:
        if not ruta.exists():
            print(f"  [skip] {ruta.name}: no existe")
            continue
        inicio = time.time()
        try:
            texto = motor.transcribir(ruta, prompt=STT_PROMPT)
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] {ruta.name} -> {type(exc).__name__}: {exc}")
            ok = False
            continue
        print(f"  [{'OK ' if texto else 'FAIL'}] {ruta.name} ({time.time() - inicio:.1f}s)")
        print(f"         {texto[:200]!r}")
        if texto:
            borrador = extraer(texto, motor)
            equipos = [(e.modality.value, e.quantity, e.brand) for e in borrador.items]
            print(f"         -> cliente: {borrador.customer} | equipos: {equipos}")
        else:
            ok = False
        print()
    motor.cerrar()
    return ok


def main() -> None:
    rutas = [Path(a) for a in sys.argv[1:]]
    formatos_ok = probar_formatos()
    transcripcion_ok = probar_transcripcion(rutas) if rutas else True
    if not rutas:
        print("\n  (sin ficheros de audio: pasa uno como argumento para probar la transcripcion)")
    print("\nRESULTADO:", "todo OK" if (formatos_ok and transcripcion_ok) else "hay fallos")
    sys.exit(0 if (formatos_ok and transcripcion_ok) else 1)


if __name__ == "__main__":
    main()
