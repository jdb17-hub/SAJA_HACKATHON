"""Configuración global. Todo lo que toque red se limita a la descarga inicial
del modelo; la inferencia siempre ocurre en este dispositivo."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "observaciones.db"

# --- QVAC -------------------------------------------------------------------
# El SDK de Python arranca un worker Bare local. En Windows no consigue resolver
# `npm root -g` (busca `npm`, no `npm.cmd`), así que apuntamos al paquete global.
_DEFAULT_SDK_DIRS = [
    Path(os.path.expanduser("~")) / "AppData" / "Roaming" / "npm" / "node_modules" / "@qvac" / "sdk",
    Path(os.path.expanduser("~")) / ".cache" / "qvac" / "worker" / "0.19.0" / "node_modules" / "@qvac" / "sdk",
]


def resolve_qvac_sdk_dir() -> str | None:
    """Devuelve el directorio de @qvac/sdk, o None si el SDK sabe encontrarlo solo."""
    explicit = os.environ.get("QVAC_SDK_DIR")
    if explicit:
        return explicit
    for candidate in _DEFAULT_SDK_DIRS:
        if (candidate / "dist" / "src" / "worker" / "index.js").exists():
            return str(candidate)
    return None


# Modelo de lenguaje para extracción y preguntas. Se puede cambiar por env var.
# QWEN3_1_7B_INST_Q4 entiende español mucho mejor que LLAMA_3_2_1B a igual tamaño.
LLM_MODEL = os.environ.get("QVAC_LLM_MODEL", "QWEN3_1_7B_INST_Q4")

# Modelo de transcripción (voz -> texto), también on-device.
STT_MODEL = os.environ.get("QVAC_STT_MODEL", "WHISPER_BASE_Q8_0")

# Idioma del dictado, ISO 639-1. Es obligatorio fijarlo: whisper.cpp asume "en"
# por defecto y, ante una nota en español, la *traduce* al inglés en vez de
# transcribirla. "auto" también funciona, pero declarar el idioma da mejor
# resultado en grabaciones cortas y ruidosas, que es el caso de una visita.
STT_IDIOMA = os.environ.get("QVAC_STT_IDIOMA", "es")

# initial_prompt de whisper.cpp: sesga el vocabulario hacia lo que esperamos oír.
# Sin esto, "resonador" sale como "resonado" y las marcas ficticias del catálogo
# se transcriben como cualquier cosa.
STT_PROMPT = (
    "Nota de visita a un hospital sobre equipos médicos de imagen. "
    "Vocabulario: resonador, resonancia magnética, tomógrafo, tomografía, TAC, "
    "ecógrafo, ecografía, ultrasonido, rayos X, angiógrafo, monitor de paciente. "
    "Marcas: NovaMed, Aurelia Health, BluePeak Medical, Orion Imaging, HelixCare, "
    "Zenith MedTech."
)

GENERATION_PARAMS = {"temp": 0.0, "top_p": 1.0, "predict": 900, "seed": 7}

# --- Reglas de negocio ------------------------------------------------------
# Un equipo de imagen se considera candidato a renovación a partir de esta edad.
EDAD_RENOVACION = 10
# Una observación se marca como "sin verificar" pasado este número de días.
DIAS_SIN_VERIFICAR = 180
