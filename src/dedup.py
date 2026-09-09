"""Deteccion de duplicados entre observaciones.

Es el problema central de capturar en campo: tres personas visitan el mismo
hospital y las tres reportan "dos resonadores". Sin deteccion, la base dice
seis. El objetivo no es descartar automaticamente (una segunda observacion
independiente vale mucho: confirma), sino avisar antes de guardar y dejar que
el colaborador decida entre confirmar, actualizar o registrar aparte.
"""
from __future__ import annotations

from . import normalize as N
from .confidence import dias_desde
from .schema import DESCONOCIDO, Equipo, es_desconocido

VENTANA_DIAS = 365  # más allá de un ano, se considera una re-verificacion, no un duplicado


def _similitud(equipo: Equipo, fila: dict, cliente: str) -> tuple[float, list[str]]:
    """Devuelve (0-1, motivos). Solo compara dentro del mismo cliente."""
    if N.clave(cliente) != N.clave(fila.get("customer", "")):
        return 0.0, []
    if equipo.modality.value != (fila.get("modality") or ""):
        return 0.0, []

    puntaje, motivos = 0.55, ["mismo cliente y modalidad"]

    marca_fila = fila.get("brand") or DESCONOCIDO
    if not es_desconocido(equipo.brand) and not es_desconocido(marca_fila):
        if N.clave(equipo.brand) == N.clave(marca_fila):
            puntaje += 0.20
            motivos.append(f"misma marca ({equipo.brand})")
        else:
            # Marcas distintas: probablemente son flotas distintas del mismo hospital.
            return 0.25, ["misma modalidad pero marca distinta"]

    cantidad_fila = int(fila.get("quantity") or 0)
    if equipo.quantity > 0 and cantidad_fila > 0:
        if equipo.quantity == cantidad_fila:
            puntaje += 0.15
            motivos.append(f"misma cantidad ({equipo.quantity})")
        else:
            puntaje -= 0.10
            motivos.append(f"cantidad distinta ({equipo.quantity} vs {cantidad_fila})")

    edad_fila = int(fila.get("age_years") or 0)
    if equipo.age_years > 0 and edad_fila > 0 and abs(equipo.age_years - edad_fila) <= 2:
        puntaje += 0.10
        motivos.append("edad compatible")

    dias = dias_desde(fila)
    if dias is not None and dias > VENTANA_DIAS:
        puntaje -= 0.20
        motivos.append(f"observación antigua ({dias} días)")

    return max(0.0, min(1.0, puntaje)), motivos


def buscar_duplicados(
    equipo: Equipo, cliente: str, existentes: list[dict], umbral: float = 0.6
) -> list[dict]:
    """Filas existentes que probablemente describen el mismo equipo.

    Cada resultado trae `_score`, `_motivos` y `_accion` sugerida.
    """
    candidatos = []
    for fila in existentes:
        puntaje, motivos = _similitud(equipo, fila, cliente)
        if puntaje < umbral:
            continue
        candidatos.append(
            {
                **fila,
                "_score": round(puntaje, 2),
                "_motivos": motivos,
                "_accion": _accion_sugerida(equipo, fila, puntaje),
            }
        )
    return sorted(candidatos, key=lambda c: -c["_score"])


def _accion_sugerida(equipo: Equipo, fila: dict, puntaje: float) -> str:
    """Que conviene hacer con este par."""
    cantidad_fila = int(fila.get("quantity") or 0)
    aporta = (
        (not es_desconocido(equipo.brand) and es_desconocido(fila.get("brand")))
        or (equipo.age_years > 0 and int(fila.get("age_years") or 0) == 0)
        or (not es_desconocido(equipo.model) and es_desconocido(fila.get("model")))
    )
    if aporta:
        return "enriquecer"
    if equipo.quantity and cantidad_fila and equipo.quantity != cantidad_fila:
        return "revisar"
    if puntaje >= 0.8:
        return "confirmar"
    return "revisar"


DESCRIPCION_ACCION = {
    "confirmar": "Ya existe y coincide. Confirmarla sube su confianza sin duplicar la cuenta.",
    "enriquecer": "Ya existe pero tu observación aporta datos nuevos. Conviene completarla.",
    "revisar": "Se parece a una observación existente, pero algo no cuadra. Revisa antes de guardar.",
}


def confirmar(fila_id: int, observador: str) -> None:
    """Registra una confirmacion independiente sobre una fila existente.

    No duplica la cuenta de equipos: solo refresca la fecha y anota quien lo
    confirmo, que es lo que hace subir el puntaje de confianza.
    """
    from datetime import date

    from . import store

    fila = next((f for f in store.todas() if f["observation_id"] == fila_id), None)
    if not fila:
        return
    nota = (fila.get("notes") or "").strip()
    marca = f"Confirmado por {observador} el {date.today().isoformat()}"
    store.actualizar(
        fila_id,
        visit_date=date.today().isoformat(),
        status="Confirmed",
        notes=f"{nota} | {marca}".strip(" |"),
    )
    store.recalcular_confianza()


def enriquecer(fila_id: int, equipo: Equipo, observador: str) -> None:
    """Rellena en una fila existente solo los campos que estaban vacios."""
    from datetime import date

    from . import store

    fila = next((f for f in store.todas() if f["observation_id"] == fila_id), None)
    if not fila:
        return
    cambios: dict = {}
    if es_desconocido(fila.get("brand")) and not es_desconocido(equipo.brand):
        cambios["brand"] = equipo.brand
    if es_desconocido(fila.get("model")) and not es_desconocido(equipo.model):
        cambios["model"] = equipo.model
    if int(fila.get("age_years") or 0) == 0 and equipo.age_years > 0:
        cambios["age_years"] = equipo.age_years
        cambios["install_year"] = date.today().year - equipo.age_years
    if int(fila.get("quantity") or 0) == 0 and equipo.quantity > 0:
        cambios["quantity"] = equipo.quantity
    if not cambios:
        return
    nota = (fila.get("notes") or "").strip()
    cambios["visit_date"] = date.today().isoformat()
    cambios["notes"] = f"{nota} | Completado por {observador}".strip(" |")
    store.actualizar(fila_id, **cambios)
    store.recalcular_confianza()
