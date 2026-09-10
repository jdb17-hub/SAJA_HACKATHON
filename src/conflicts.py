"""Equipos sobre los que las visitas no se ponen de acuerdo.

Un duplicado es que dos personas cuenten lo mismo; un conflicto es que cuenten
cosas distintas del mismo equipo. El documento del reto pide detectar ambos, y
son problemas diferentes: el duplicado infla la cuenta, el conflicto la hace
dudosa.

Este módulo no detecta nada por su cuenta: el almacén ya lo hace. Cada vez que
una visita se consolida contra un grupo existente, `store.compatible()` decide
si lo dicho encaja con lo que había y se guarda una fila de evidencia con ese
veredicto. Aquí solo se leen las que no encajaron y se explica en qué difieren,
que es lo que no se ve desde la ficha del cliente.

Tampoco resuelve nada por su cuenta, y es a propósito. El almacén es de solo
añadir: no se edita ni se borra una observación, se registra una visita nueva
que la supersede. Un botón que "arreglara" el conflicto por detrás rompería esa
trazabilidad, que es justo lo que hace defendible el dato.
"""
from __future__ import annotations

from . import store
from .confidence import dias_desde
from .schema import es_desconocido, etiqueta

# Campos cuyo desacuerdo hace que dos observaciones no describan la misma flota.
CAMPOS_COMPARADOS = (
    ("quantity", "cantidad"),
    ("age_years", "antigüedad"),
    ("brand", "marca"),
    ("model", "modelo"),
)


def _valor(fila: dict, campo: str) -> str:
    valor = fila.get(campo)
    if campo in ("quantity", "age_years"):
        return str(valor) if valor else "?"
    return valor if not es_desconocido(valor) else "?"


def _diferencias(actual: dict, propuesta: dict) -> list[str]:
    """En qué no coinciden dos versiones del mismo grupo de equipos."""
    motivos = []
    for campo, nombre in CAMPOS_COMPARADOS:
        a, b = _valor(actual, campo), _valor(propuesta, campo)
        if a != "?" and b != "?" and a != b:
            motivos.append(f"{nombre}: {a} frente a {b}")
    return motivos


def _gravedad(fila: dict, motivos: list[str]) -> int:
    """Cuánto importa este conflicto.

    Pesa más el desacuerdo en cantidad, porque mueve el tamaño de la
    oportunidad, y cuantos más equipos haya en juego, más urge resolverlo.
    """
    puntos = 0
    for motivo in motivos:
        if motivo.startswith("cantidad"):
            puntos += 40
        elif motivo.startswith("antigüedad"):
            puntos += 25
        else:
            puntos += 10
    return puntos + int(fila.get("quantity") or 0) * 2


def abiertos(filas: list[dict]) -> list[dict]:
    """Grupos vigentes con evidencia que no encajó, del más grave al menos.

    `filas` son las que devuelve `store.todas()`; el contador `conflicts` viene
    del almacén y cuenta las visitas cuya versión no era compatible.
    """
    salida = []
    for fila in filas:
        if not int(fila.get("conflicts") or 0):
            continue
        versiones = _versiones_en_desacuerdo(fila)
        motivos = sorted({m for _, ms in versiones for m in ms})
        salida.append(
            {
                "fila": fila,
                "versiones": versiones,
                "motivos": motivos,
                "gravedad": _gravedad(fila, motivos),
                "clave": str(fila.get("observation_id")),
            }
        )
    return sorted(salida, key=lambda c: -c["gravedad"])


def _versiones_en_desacuerdo(fila: dict) -> list[tuple[dict, list[str]]]:
    """Las visitas que reportaron algo distinto de lo que hoy está vigente.

    Los datos salen de las revisiones de tipo `extracción`, que guardan el
    borrador entero de cada visita. Las de tipo `consolidar` solo dejan
    constancia de la acción —el modo y los destinos— y no sirven para saber qué
    dijo cada quien.
    """
    equipment_id = fila.get("observation_id")
    if equipment_id is None:
        return []
    try:
        revisiones = store.historial(int(equipment_id))
    except Exception:  # noqa: BLE001 - el historial no debe tumbar la pantalla
        return []

    versiones = []
    for revision in revisiones:
        payload = revision.get("payload") or {}
        borrador = payload.get("borrador")
        if not isinstance(borrador, dict):
            continue
        for item in borrador.get("items", []):
            if not isinstance(item, dict) or item.get("modality") != fila.get("modality"):
                continue
            propuesta = {
                **item,
                "customer": borrador.get("customer", ""),
                "observer": payload.get("observer", ""),
                "visit_date": payload.get("visit_date", ""),
                "_original": revision.get("original", ""),
                "_visit_id": revision.get("visit_id"),
            }
            motivos = _diferencias(fila, propuesta)
            if motivos:
                versiones.append((propuesta, motivos))
    return versiones


def resumen(fila: dict) -> str:
    """Una línea que describe una versión, para poder compararlas de un vistazo."""
    partes = [f"{fila.get('quantity') or '?'} × {etiqueta(fila.get('modality'))}"]
    if not es_desconocido(fila.get("brand")):
        partes.append(str(fila["brand"]))
    if int(fila.get("age_years") or 0) > 0:
        partes.append(f"{fila['age_years']} años")

    origen = f"{fila.get('observer') or 'sin observador'} · {fila.get('visit_date') or 's/f'}"
    dias = dias_desde(fila)
    if dias is not None:
        origen += f" (hace {dias} días)"
    return " · ".join(partes) + f" — {origen}"


COMO_SE_RESUELVE = (
    "Para cerrarlo, vuelve a **Capturar** y registra la visita más reciente como "
    "**recuento**: eso jubila el grupo anterior y deja el nuevo como vigente, sin "
    "borrar nada. Si las dos versiones son correctas —dos flotas parecidas, o "
    "equipos añadidos entre visitas— guárdalas como grupos separados."
)
