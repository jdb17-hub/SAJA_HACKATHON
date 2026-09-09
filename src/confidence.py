"""Puntaje de confianza y alertas de frescura.

Una base instalada construida con observaciones de campo tiene calidad
desigual: alguien que leyo la placa del equipo y alguien que conto de memoria
producen la misma fila. El puntaje hace explicita esa diferencia para que un
comercial sepa de que dato puede fiarse.

Se compone de cuatro senales, todas deterministas y explicables:
  completitud             hasta 40 pts  - cuantos campos utiles tiene
  estado de la observacion hasta 25 pts  - Confirmado > Reportado > Estimado
  frescura                hasta 20 pts  - decae con la antiguedad del dato
  confirmaciones          hasta 15 pts  - observadores distintos que coinciden
"""
from __future__ import annotations

from datetime import date, datetime

from .config import DIAS_SIN_VERIFICAR, EDAD_RENOVACION
from .schema import Confianza, Estado, es_desconocido

PESO_ESTADO = {
    Estado.CONFIRMADO.value: 25,
    Estado.REPORTADO.value: 16,
    Estado.ESTIMADO.value: 8,
    Estado.DESCONOCIDO.value: 0,
}


def _fecha(valor: str) -> date | None:
    for formato in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime((valor or "").strip()[:19], formato).date()
        except ValueError:
            continue
    return None


def dias_desde(fila: dict) -> int | None:
    f = _fecha(fila.get("verified_date") or fila.get("visit_date", ""))
    return (date.today() - f).days if f else None


def _completitud(fila: dict) -> int:
    """40 puntos repartidos segun lo util que es cada campo comercialmente."""
    puntos = 0
    if not es_desconocido(fila.get("modality")):
        puntos += 8
    if fila.get("quantity") is not None:
        puntos += 10
    if not es_desconocido(fila.get("brand")):
        puntos += 10
    if fila.get("age_years") is not None:
        puntos += 8
    if not es_desconocido(fila.get("model")):
        puntos += 4
    return puntos


def _frescura(fila: dict) -> int:
    dias = dias_desde(fila)
    if dias is None:
        return 0
    if dias <= 90:
        return 20
    if dias <= 180:
        return 14
    if dias <= 365:
        return 8
    if dias <= 730:
        return 3
    return 0


def _confirmaciones(fila: dict, todas: list[dict]) -> int:
    """Diversidad de observadores en evidencia vinculada y compatible; no prueba independencia real."""
    n = fila.get("independent_observers", 0)
    return 15 if n >= 3 else 10 if n == 2 else 0


def calcular_confianza(fila: dict, todas: list[dict]) -> tuple[int, str]:
    """Devuelve (puntaje 0-100, etiqueta High/Medium/Low)."""
    puntaje = (
        _completitud(fila)
        + PESO_ESTADO.get((fila.get("status") or "").strip(), 0)
        + _frescura(fila)
        + _confirmaciones(fila, todas)
    )
    puntaje = max(0, min(100, puntaje))
    if puntaje >= 70:
        etiqueta = Confianza.ALTA.value
    elif puntaje >= 45:
        etiqueta = Confianza.MEDIA.value
    else:
        etiqueta = Confianza.BAJA.value
    return puntaje, etiqueta


def desglose(fila: dict, todas: list[dict]) -> dict[str, int]:
    """Los cuatro componentes por separado, para poder explicar el numero."""
    return {
        "Completitud": _completitud(fila),
        "Estado": PESO_ESTADO.get((fila.get("status") or "").strip(), 0),
        "Frescura": _frescura(fila),
        "Confirmaciones": _confirmaciones(fila, todas),
    }


# --- alertas ----------------------------------------------------------------


def sin_verificar(fila: dict) -> bool:
    dias = dias_desde(fila)
    return dias is None or dias > DIAS_SIN_VERIFICAR


def es_oportunidad_renovacion(fila: dict) -> bool:
    """Equipo de imagen con edad por encima del umbral de renovacion."""
    return int(fila.get("age_years") or 0) >= EDAD_RENOVACION


def alertas(fila: dict) -> list[str]:
    avisos: list[str] = []
    dias = dias_desde(fila)
    if dias is None:
        avisos.append("Fecha de visita desconocida")
    elif sin_verificar(fila):
        avisos.append(f"Sin verificar desde hace {dias} días")
    if es_oportunidad_renovacion(fila):
        edad = int(fila.get("age_years") or 0)
        avisos.append(f"Oportunidad de renovación: {edad} años")
    if es_desconocido(fila.get("brand")):
        avisos.append("Marca desconocida")
    if fila.get("conflicts", 0):
        avisos.append("Hay evidencia contradictoria pendiente de revisión")
    if fila.get("quantity") is None:
        avisos.append("Cantidad sin confirmar")
    return avisos
