"""Preparación local de una observación con texto, documentos y audio."""
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from . import documents
from .audio import guardar_audio, duracion_segundos
from .config import STT_PROMPT

AUDIO_EXTENSIONS = {'.wav', '.ogg', '.mp3', '.m4a', '.flac', '.aac'}
EXTENSIONS = documents.EXTENSIONES | AUDIO_EXTENSIONS

@dataclass
class Entrada:
    texto: str
    nombres: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    fuente: str = 'Text'


def preparar(texto, archivos, grabacion, motor):
    """No guarda inventario. Un error no descarta silenciosamente un adjunto."""
    adjuntos = list(archivos)
    if grabacion is not None:
        adjuntos.append(grabacion)
    if sum(len(f.getvalue()) for f in adjuntos) > documents.MAX_BYTES:
        raise ValueError('Los adjuntos no deben superar 25 MB en total.')
    partes = [texto.strip()] if texto.strip() else []
    nombres, avisos = [], []
    tiene_documentos = False
    tiene_audio = False
    for archivo in adjuntos:
        nombre = archivo.name
        extension = Path(nombre).suffix.lower()
        if archivo is grabacion or extension in AUDIO_EXTENSIONS:
            tiene_audio = True
            with TemporaryDirectory(prefix='qvac-captura-') as carpeta:
                ruta = guardar_audio(archivo.getvalue(), Path(carpeta)/'audio')
                duracion = duracion_segundos(ruta)
                if 0 < duracion < 0.6:
                    raise ValueError(f'{nombre}: la grabación es demasiado corta.')
                contenido = motor.transcribir(ruta, prompt=STT_PROMPT).strip()
            if not contenido:
                raise ValueError(f'{nombre}: no se reconoció voz. Revisa el audio.')
        elif extension in documents.EXTENSIONES:
            tiene_documentos = True
            doc = documents.leer(nombre, archivo.getvalue())
            contenido = doc.texto
            avisos.extend(f'{nombre}: {a}' for a in doc.avisos)
        else:
            raise ValueError(f'{nombre}: formato no compatible.')
        nombres.append(nombre)
        partes.append(f'[Adjunto: {nombre}]\n{contenido}')
    return Entrada('\n\n'.join(partes), nombres, avisos,
                   'Document' if tiene_documentos else 'Voice' if tiene_audio else 'Text')
