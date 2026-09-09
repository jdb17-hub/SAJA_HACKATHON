"""Preparacion del audio para la transcripcion.

QVAC trae su propio decodificador (ffmpeg), asi que acepta el fichero tal cual
lo entrega el navegador: wav, ogg, mp3, m4a, flac o aac, con cualquier
frecuencia de muestreo y numero de canales. No hay que convertir nada.

Esto no siempre estuvo asi en este proyecto. La version anterior remuestreaba a
16 kHz mono con el modulo `wave`, y eso rompia con el audio real del micro: el
grabador del navegador produce WAV en coma flotante de 32 bits (formato PCM 3),
que `wave` no sabe leer y rechaza con `unknown format: 3`. La grabacion moria
antes de llegar al modelo. Quitar la conversion arregla el fallo y ademas
soporta mas formatos.
"""
from __future__ import annotations

import struct
from pathlib import Path

# Formatos que QVAC sabe decodificar (SupportedAudioFormat del SDK).
FIRMAS: list[tuple[bytes, str]] = [
    (b"RIFF", ".wav"),
    (b"OggS", ".ogg"),
    (b"fLaC", ".flac"),
    (b"ID3", ".mp3"),
    (b"\xff\xfb", ".mp3"),
    (b"\xff\xf3", ".mp3"),
    (b"\xff\xf2", ".mp3"),
    (b"\xff\xf1", ".aac"),
    (b"\xff\xf9", ".aac"),
]

TAMANO_MINIMO = 2_000  # bytes; por debajo de esto no hay ni una palabra


class AudioNoSoportado(ValueError):
    """El contenedor no es uno de los que QVAC puede decodificar."""


def detectar_extension(datos: bytes) -> str:
    """Extension real segun los bytes de cabecera, no segun como se llame."""
    for firma, extension in FIRMAS:
        if datos.startswith(firma):
            return extension
    # ftyp en el offset 4 identifica a los contenedores MP4 / M4A.
    if len(datos) > 12 and datos[4:8] == b"ftyp":
        return ".m4a"
    raise AudioNoSoportado(
        "El navegador entrego un formato de audio que QVAC no decodifica "
        f"(cabecera {datos[:4]!r}). Formatos validos: wav, ogg, mp3, m4a, flac, aac."
    )


def guardar_audio(datos: bytes, destino_base: Path) -> Path:
    """Escribe la grabacion con la extension que le corresponde.

    La extension importa: el worker de QVAC decide el decodificador por ella,
    asi que un fichero ogg llamado .wav no se abre.
    """
    if not datos or len(datos) < TAMANO_MINIMO:
        raise ValueError("La grabacion esta vacia o es demasiado corta.")
    destino = destino_base.with_suffix(detectar_extension(datos))
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(datos)
    return destino


def duracion_segundos(ruta: Path) -> float:
    """Duracion aproximada. Devuelve 0.0 si el formato no permite saberla barato.

    Se parsea el RIFF a mano en vez de usar el modulo `wave` justamente porque
    `wave` no admite WAV en coma flotante, que es lo que graba el navegador.
    """
    try:
        datos = ruta.read_bytes()
    except OSError:
        return 0.0
    if not datos.startswith(b"RIFF"):
        return 0.0

    bytes_por_segundo = 0
    posicion = 12  # se salta "RIFF", el tamano y "WAVE"
    tamano_datos = 0
    while posicion + 8 <= len(datos):
        identificador = datos[posicion : posicion + 4]
        (tamano,) = struct.unpack("<I", datos[posicion + 4 : posicion + 8])
        cuerpo = posicion + 8
        if identificador == b"fmt " and tamano >= 16:
            _, _, _, bytes_por_segundo = struct.unpack("<HHII", datos[cuerpo : cuerpo + 12])
        elif identificador == b"data":
            tamano_datos = min(tamano, len(datos) - cuerpo)
            break
        posicion = cuerpo + tamano + (tamano % 2)  # los chunks van alineados a 2

    if not bytes_por_segundo or not tamano_datos:
        return 0.0
    return tamano_datos / bytes_por_segundo
