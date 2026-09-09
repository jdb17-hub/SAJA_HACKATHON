"""Modelo de datos. Una fila = un grupo de equipos (modalidad + rango de edad)
de un cliente, igual que la hoja `Dummy Installed Base` del Excel."""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

DESCONOCIDO = "Unknown"


class Modalidad(str, Enum):
    MR = "MR"
    CT = "CT"
    ULTRASOUND = "Ultrasound"
    XRAY = "X-Ray"
    MONITORING = "Patient Monitoring"
    IGT = "Image Guided Therapy"
    UNKNOWN = "Unknown"


class Estado(str, Enum):
    """Estado por observación, tal y como pide el reto."""

    CONFIRMADO = "Confirmed"
    REPORTADO = "Reported"
    ESTIMADO = "Estimated"
    DESCONOCIDO = "Unknown"


class Confianza(str, Enum):
    ALTA = "High"
    MEDIA = "Medium"
    BAJA = "Low"


ETIQUETA_ES = {
    "Confirmed": "Confirmado",
    "Reported": "Reportado",
    "Estimated": "Estimado",
    "Unknown": "Desconocido",
    "High": "Alta",
    "Medium": "Media",
    "Low": "Baja",
    "MR": "Resonancia (MR)",
    "CT": "Tomografía (CT)",
    "Ultrasound": "Ecografía",
    "X-Ray": "Rayos X",
    "Patient Monitoring": "Monitorización",
    "Image Guided Therapy": "Terapia guiada por imagen",
}


class Equipo(BaseModel):
    """Un grupo de equipos observado. `quantity=0` y `Unknown` significan
    'no se sabe todavía' — nunca se inventa un valor."""

    modality: Modalidad = Modalidad.UNKNOWN
    quantity: int = 0
    brand: str = DESCONOCIDO
    model: str = DESCONOCIDO
    age_years: int = 0
    status: Estado = Estado.DESCONOCIDO
    notes: str = ""

    @property
    def anio_instalacion(self) -> Optional[int]:
        if self.age_years <= 0:
            return None
        return date.today().year - self.age_years


class Borrador(BaseModel):
    """Lo que el agente entendió de una sola intervención del colaborador,
    antes de confirmarse y guardarse."""

    customer: str = DESCONOCIDO
    city: str = DESCONOCIDO
    country: str = DESCONOCIDO
    items: list[Equipo] = Field(default_factory=list)
    notes: str = ""

    def campos_faltantes(self) -> list[str]:
        faltan = []
        if es_desconocido(self.customer):
            faltan.append("customer")
        if es_desconocido(self.country):
            faltan.append("country")
        if es_desconocido(self.city):
            faltan.append("city")
        if not self.items:
            faltan.append("modality")
        for i, it in enumerate(self.items):
            if it.modality == Modalidad.UNKNOWN:
                faltan.append(f"items.{i}.modality")
            if it.quantity <= 0:
                faltan.append(f"items.{i}.quantity")
            if es_desconocido(it.brand):
                faltan.append(f"items.{i}.brand")
            if it.age_years <= 0:
                faltan.append(f"items.{i}.age_years")
            if es_desconocido(it.model):
                faltan.append(f"items.{i}.model")
        return faltan


class Observacion(BaseModel):
    """Fila persistida. Refleja 1:1 las columnas del Excel de referencia."""

    observation_id: Optional[int] = None
    country: str = DESCONOCIDO
    city: str = DESCONOCIDO
    customer: str = DESCONOCIDO
    observer: str = DESCONOCIDO
    visit_date: str = ""
    modality: str = Modalidad.UNKNOWN.value
    quantity: int = 0
    brand: str = DESCONOCIDO
    model: str = DESCONOCIDO
    age_years: int = 0
    install_year: Optional[int] = None
    confidence: str = Confianza.BAJA.value
    confidence_score: int = 0
    status: str = Estado.DESCONOCIDO.value
    source: str = "Text"
    raw_input: str = ""
    notes: str = ""
    created_at: str = ""


def es_desconocido(valor: str | None) -> bool:
    return not valor or valor.strip().lower() in {"unknown", "desconocido", "n/a", "-", ""}


# Esquema JSON que se le impone al modelo con `response_format`. Al ser decodificación
# restringida, la salida es JSON válido y con los enums correctos por construcción.
ESQUEMA_EXTRACCION = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "customer": {"type": "string"},
        "city": {"type": "string"},
        "country": {"type": "string"},
        "notes": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "modality": {"type": "string", "enum": [m.value for m in Modalidad]},
                    "quantity": {"type": "integer", "minimum": 0, "maximum": 200},
                    "brand": {"type": "string"},
                    "model": {"type": "string"},
                    "age_years": {"type": "integer", "minimum": 0, "maximum": 40},
                    "status": {"type": "string", "enum": [e.value for e in Estado]},
                },
                "required": ["modality", "quantity", "brand", "model", "age_years", "status"],
            },
        },
    },
    "required": ["customer", "city", "country", "items", "notes"],
}
