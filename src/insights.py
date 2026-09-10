"""Vistas agregadas y analíticas del panel.

Aquí vive lo que el documento del reto llama *Customer 360* y *Dashboard &
Analytics*: convertir las observaciones sueltas en el resumen que alguien de
negocio puede leer de un vistazo.

La diferencia con `store.py` es de nivel: allí hay filas, aquí hay lecturas.
"""
from __future__ import annotations

from .confidence import dias_desde, es_oportunidad_renovacion, sin_verificar
from .schema import NO_IDENTIFICADO, es_desconocido, etiqueta

# Diferencia de años a partir de la cual una flota deja de tener "una edad" y
# pasa a ser mixta. Por debajo, dar un rango de un año no aporta nada.
UMBRAL_EDAD_MIXTA = 6


def rango_edad(edades: list[int]) -> str:
    """La edad de un grupo en lenguaje de negocio.

    El documento del reto no pide un número: sus ejemplos dicen "4–10 years" o
    "Mixed". Una flota comprada en tandas distintas no tiene *una* edad, y
    promediarla esconde justo lo interesante, que es que hay equipos viejos.
    """
    reales = sorted(e for e in edades if e and e > 0)
    if not reales:
        return NO_IDENTIFICADO
    menor, mayor = reales[0], reales[-1]
    if menor == mayor:
        return f"{menor} años"
    if mayor - menor >= UMBRAL_EDAD_MIXTA:
        return f"Mixta ({menor}–{mayor} años)"
    return f"{menor}–{mayor} años"


def _etiqueta_confianza(puntajes: list[int]) -> str:
    if not puntajes:
        return NO_IDENTIFICADO
    media = sum(puntajes) / len(puntajes)
    if media >= 70:
        return "Alta"
    if media >= 45:
        return "Media"
    return "Baja"


def resumen_cliente(filas: list[dict]) -> list[dict]:
    """El panorama de equipos de un cliente, una fila por modalidad.

    Es la tabla que dibuja el documento en su ejemplo de Customer 360:
    tipo de equipo, cantidad, edad aproximada y confianza. Las observaciones
    individuales siguen estando debajo, para poder bajar al detalle.
    """
    por_modalidad: dict[str, dict] = {}
    for fila in filas:
        modalidad = fila.get("modality") or "Unknown"
        entrada = por_modalidad.setdefault(
            modalidad,
            {
                "modalidad": modalidad,
                "cantidad": 0,
                "edades": [],
                "confianzas": [],
                "marcas": set(),
                "observaciones": 0,
                "oportunidad": False,
                "sin_verificar": False,
                "ultima_visita": "",
            },
        )
        entrada["cantidad"] += int(fila.get("quantity") or 0)
        if int(fila.get("age_years") or 0) > 0:
            entrada["edades"].append(int(fila["age_years"]))
        entrada["confianzas"].append(int(fila.get("confidence_score") or 0))
        if not es_desconocido(fila.get("brand")):
            entrada["marcas"].add(fila["brand"])
        entrada["observaciones"] += 1
        entrada["oportunidad"] |= es_oportunidad_renovacion(fila)
        entrada["sin_verificar"] |= sin_verificar(fila)
        visita = fila.get("visit_date") or ""
        if visita > entrada["ultima_visita"]:
            entrada["ultima_visita"] = visita

    salida = []
    for entrada in por_modalidad.values():
        salida.append(
            {
                "Tipo de equipo": etiqueta(entrada["modalidad"]),
                "Cantidad": entrada["cantidad"],
                "Edad aprox.": rango_edad(entrada["edades"]),
                "Marcas": ", ".join(sorted(entrada["marcas"])) or NO_IDENTIFICADO,
                "Confianza": _etiqueta_confianza(entrada["confianzas"]),
                "Observaciones": entrada["observaciones"],
                "Última visita": entrada["ultima_visita"] or NO_IDENTIFICADO,
                "_oportunidad": entrada["oportunidad"],
                "_sin_verificar": entrada["sin_verificar"],
            }
        )
    return sorted(salida, key=lambda r: -r["Cantidad"])


# --- analíticas del panel ---------------------------------------------------

# Campos que hacen falta para que una observación sea accionable comercialmente.
CAMPOS_CLAVE = ("brand", "age_years", "model")


def _huecos(fila: dict) -> list[str]:
    faltan = []
    if es_desconocido(fila.get("brand")):
        faltan.append("marca")
    if int(fila.get("age_years") or 0) <= 0:
        faltan.append("antigüedad")
    if es_desconocido(fila.get("model")):
        faltan.append("modelo")
    if int(fila.get("quantity") or 0) <= 0:
        faltan.append("cantidad")
    return faltan


def clientes_incompletos(filas: list[dict]) -> list[dict]:
    """Clientes cuya ficha tiene huecos, ordenados por cuánto falta.

    Es uno de los insights que pide el reto ("customers with incomplete
    information") y en la práctica es la lista de tareas del equipo de campo:
    a quién conviene volver a preguntar en la próxima visita.
    """
    por_cliente: dict[str, dict] = {}
    for fila in filas:
        cliente = (fila.get("customer") or "").strip()
        if not cliente:
            continue
        entrada = por_cliente.setdefault(
            cliente,
            {
                "Cliente": cliente,
                "País": fila.get("country", ""),
                "Ciudad": fila.get("city", ""),
                "_huecos": [],
                "_campos": 0,
                "_total": 0,
                "Unidades": 0,
            },
        )
        huecos = _huecos(fila)
        entrada["_huecos"].extend(huecos)
        entrada["_campos"] += len(huecos)
        entrada["_total"] += 4  # los cuatro campos que se revisan por fila
        entrada["Unidades"] += int(fila.get("quantity") or 0)

    salida = []
    for entrada in por_cliente.values():
        if not entrada["_campos"]:
            continue
        completitud = 100 - round(100 * entrada["_campos"] / max(entrada["_total"], 1))
        faltantes = sorted(set(entrada["_huecos"]))
        salida.append(
            {
                "Cliente": entrada["Cliente"],
                "País": entrada["País"],
                "Ciudad": entrada["Ciudad"],
                "Unidades": entrada["Unidades"],
                "Completitud": f"{completitud}%",
                "Qué falta": ", ".join(faltantes),
                "_completitud": completitud,
            }
        )
    return sorted(salida, key=lambda r: r["_completitud"])


def sitios_recientes(filas: list[dict], limite: int = 10) -> list[dict]:
    """Clientes visitados más recientemente.

    El otro insight del reto ("recently updated customer sites"). Sirve para lo
    contrario que la lista anterior: enseñar que la base está viva y quién la
    está alimentando.
    """
    por_cliente: dict[str, dict] = {}
    for fila in filas:
        cliente = (fila.get("customer") or "").strip()
        if not cliente:
            continue
        visita = fila.get("visit_date") or ""
        entrada = por_cliente.get(cliente)
        if entrada is None or visita > entrada["_visita"]:
            por_cliente[cliente] = {
                "Cliente": cliente,
                "País": fila.get("country", ""),
                "Última visita": visita,
                "Observador": fila.get("observer", ""),
                "Origen": fila.get("source", ""),
                "_visita": visita,
                "_dias": dias_desde(fila),
            }

    ordenado = sorted(por_cliente.values(), key=lambda r: r["_visita"], reverse=True)
    for entrada in ordenado:
        dias = entrada.pop("_dias", None)
        entrada.pop("_visita", None)
        entrada["Hace"] = f"{dias} días" if dias is not None else NO_IDENTIFICADO
    return ordenado[:limite]


def salud_de_la_base(filas: list[dict]) -> dict[str, int]:
    """Cuatro números que resumen si se puede confiar en el dataset."""
    if not filas:
        return {"clientes": 0, "completas": 0, "sin_verificar": 0, "oportunidades": 0}
    return {
        "clientes": len({f.get("customer") for f in filas if f.get("customer")}),
        "completas": sum(1 for f in filas if not _huecos(f)),
        "sin_verificar": sum(1 for f in filas if sin_verificar(f)),
        "oportunidades": sum(1 for f in filas if es_oportunidad_renovacion(f)),
    }
