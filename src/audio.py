"""Preparacion del audio para Whisper.

Whisper espera PCM de 16 bits, mono, a 16 kHz. El micro del navegador entrega
otra cosa (48 kHz, a veces estereo), asi que hay que convertir. `audioop`
desaparecio en Python 3.13, de modo que la conversion va con numpy, que ademas
deja el remuestreo explicito y facil de revisar.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

FRECUENCIA_OBJETIVO = 16_000


def preparar_wav(datos: bytes, destino: Path) -> Path:
    """Escribe `datos` como WAV mono de 16 bits a 16 kHz y devuelve la ruta."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    crudo = destino.with_suffix(".orig.wav")
    crudo.write_bytes(datos)

    with wave.open(str(crudo), "rb") as w:
        canales, ancho, frecuencia = w.getnchannels(), w.getsampwidth(), w.getframerate()
        muestras = np.frombuffer(w.readframes(w.getnframes()), dtype=_dtype(ancho))

    señal = _a_float(muestras, ancho)
    if canales > 1:
        señal = señal.reshape(-1, canales).mean(axis=1)
    if frecuencia != FRECUENCIA_OBJETIVO:
        señal = _remuestrear(señal, frecuencia, FRECUENCIA_OBJETIVO)

    pcm = np.clip(señal, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    with wave.open(str(destino), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FRECUENCIA_OBJETIVO)
        w.writeframes(pcm.tobytes())

    crudo.unlink(missing_ok=True)
    return destino


def _dtype(ancho: int):
    return {1: np.uint8, 2: np.int16, 4: np.int32}.get(ancho, np.int16)


def _a_float(muestras: np.ndarray, ancho: int) -> np.ndarray:
    if ancho == 1:  # WAV de 8 bits es sin signo, centrado en 128
        return (muestras.astype(np.float32) - 128.0) / 128.0
    maximo = float(np.iinfo(_dtype(ancho)).max)
    return muestras.astype(np.float32) / maximo


def _remuestrear(señal: np.ndarray, origen: int, destino: int) -> np.ndarray:
    """Remuestreo lineal. Suficiente para voz cercana y sin dependencias extra."""
    if origen == destino or señal.size == 0:
        return señal
    n_destino = int(round(señal.size * destino / origen))
    posiciones = np.linspace(0, señal.size - 1, n_destino, dtype=np.float64)
    return np.interp(posiciones, np.arange(señal.size), señal).astype(np.float32)


def duracion_segundos(ruta: Path) -> float:
    try:
        with wave.open(str(ruta), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (wave.Error, OSError):
        return 0.0
