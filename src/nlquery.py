"""Consultas en lenguaje natural sobre la base instalada.

"clientes en Brasil con resonadores de mas de siete anos"

El modelo no genera SQL ni codigo: traduce la pregunta a un filtro con forma
fija, impuesta por JSON Schema durante el muestreo, y ese filtro se aplica en
Python. Asi una consulta rara devuelve resultados vacios en vez de ejecutar
algo inesperado, y la respuesta siempre sale de los datos reales y no de lo
que el modelo recuerde.
"""
from __future__ import annotations

from . import normalize as N
from .config import EDAD_RENOVACION
from .confidence import es_oportunidad_renovacion, sin_verificar
from .schema import Modalidad, es_desconocido

ESQUEMA_FILTRO = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "pais": {"type": "string"},
        "ciudad": {"type": "string"},
        "cliente": {"type": "string"},
        "modalidad": {"type": "string", "enum": [*[m.value for m in Modalidad], ""]},
        "marca": {"type": "string"},
        "edad_min": {"type": "integer", "minimum": 0, "maximum": 40},
        "edad_max": {"type": "integer", "minimum": 0, "maximum": 40},
        "cantidad_min": {"type": "integer", "minimum": 0, "maximum": 100},
        "solo_oportunidades": {"type": "boolean"},
        "solo_sin_verificar": {"type": "boolean"},
        "agrupar_por": {"type": "string", "enum": ["", "cliente", "pais", "modalidad", "marca"]},
        "ordenar_por": {"type": "string", "enum": ["edad", "cantidad", "confianza", "cliente"]},
    },
    "required": [
        "pais", "ciudad", "cliente", "modalidad", "marca", "edad_min", "edad_max",
        "cantidad_min", "solo_oportunidades", "solo_sin_verificar", "agrupar_por", "ordenar_por",
    ],
}

SISTEMA = f"""You translate a question about a medical imaging installed base into a filter.
The question may be in Spanish or English. Return JSON only.

Fields left as "" or 0 mean "no filter on this". Never invent a value the question
does not ask for.

  pais / ciudad / cliente : names in English as stored (Brazil, Mexico, Panama...)
  modalidad : MR (resonador/resonancia), CT (tomografo/TAC), Ultrasound (ecografo),
              X-Ray, Patient Monitoring, Image Guided Therapy
  edad_min / edad_max : age of the equipment in years. "more than 7 years" -> edad_min=8.
                        "less than 5" -> edad_max=4. "between 5 and 10" -> 5 and 10.
  solo_oportunidades : true only if the question is about renewal / replacement /
                       aging fleet opportunities (equipment >= {EDAD_RENOVACION} years).
  solo_sin_verificar : true only if the question is about stale or unverified data.
  agrupar_por : set only if the question asks for a breakdown or a count by something.
  ordenar_por : "edad" by default when the question is about old equipment.

Spanish numerals: dos=2 tres=3 cuatro=4 cinco=5 seis=6 siete=7 ocho=8 nueve=9 diez=10."""

EJEMPLO_ENTRADA = "clientes en Brasil con resonadores de mas de siete anos"
EJEMPLO_SALIDA = (
    '{"pais":"Brazil","ciudad":"","cliente":"","modalidad":"MR","marca":"",'
    '"edad_min":8,"edad_max":0,"cantidad_min":0,"solo_oportunidades":false,'
    '"solo_sin_verificar":false,"agrupar_por":"cliente","ordenar_por":"edad"}'
)

FILTRO_VACIO = {
    "pais": "", "ciudad": "", "cliente": "", "modalidad": "", "marca": "",
    "edad_min": 0, "edad_max": 0, "cantidad_min": 0,
    "solo_oportunidades": False, "solo_sin_verificar": False,
    "agrupar_por": "", "ordenar_por": "cliente",
}


def interpretar(pregunta: str, motor) -> dict:
    """Pregunta en lenguaje natural -> filtro estructurado."""
    filtro = dict(FILTRO_VACIO)
    if not pregunta.strip():
        return filtro
    if motor is None or not motor.estado.listo:
        return _interpretar_con_reglas(pregunta, filtro)
    try:
        salida = motor.json_estructurado(
            sistema=SISTEMA,
            mensajes=[
                {"role": "user", "content": EJEMPLO_ENTRADA},
                {"role": "assistant", "content": EJEMPLO_SALIDA},
                {"role": "user", "content": pregunta},
            ],
            esquema=ESQUEMA_FILTRO,
            nombre="filtro",
            max_tokens=350,
        )
        filtro.update({k: v for k, v in salida.items() if k in filtro})
    except Exception:  # noqa: BLE001
        return _interpretar_con_reglas(pregunta, filtro)
    return _sanear(filtro, pregunta)


def _interpretar_con_reglas(pregunta: str, filtro: dict) -> dict:
    """Respaldo sin modelo, para que la pagina nunca se quede muda."""
    t = N.clave(pregunta)
    modalidad = N.detectar_modalidad(pregunta)
    if modalidad != Modalidad.UNKNOWN:
        filtro["modalidad"] = modalidad.value
    for k, v in N.PAIS_ES.items():
        if k in t:
            filtro["pais"] = v
            break
    marca = next((m for m in N.catalogo_marcas() if N.clave(m) in t), "")
    filtro["marca"] = marca
    if any(p in t for p in ["renovacion", "renovar", "reemplaz", "oportunidad", "viejo", "antiguo", "renewal"]):
        filtro["solo_oportunidades"] = True
    if any(p in t for p in ["sin verificar", "desactualizado", "stale", "no verificado"]):
        filtro["solo_sin_verificar"] = True
    return _sanear(filtro, pregunta)


def _sanear(filtro: dict, pregunta: str) -> dict:
    """Corrige el filtro con lo que el texto dice literalmente.

    El modelo se equivoca sobre todo en el umbral de edad ("mas de siete" ->
    edad_min 7 en vez de 8), y ahi las reglas son exactas.
    """
    t = N.clave(pregunta)
    numeros = [n for n in (N.a_numero(tok) for tok in t.split()) if n is not None]
    if numeros:
        n = numeros[0]
        if any(p in t for p in ["mas de", "mayores de", "superior", "more than", "older than", "over"]):
            filtro["edad_min"], filtro["edad_max"] = n + 1, 0
        elif any(p in t for p in ["menos de", "menores de", "less than", "under", "newer than"]):
            filtro["edad_min"], filtro["edad_max"] = 0, max(0, n - 1)
        elif any(p in t for p in ["al menos", "at least", "o mas", "or more"]):
            filtro["edad_min"] = n

    filtro["pais"] = N.normalizar_pais(filtro["pais"]) if filtro["pais"] else ""
    if filtro["pais"] and es_desconocido(filtro["pais"]):
        filtro["pais"] = ""
    if filtro["marca"]:
        filtro["marca"] = N.emparejar(filtro["marca"], N.catalogo_marcas(), 0.7) or ""
    if filtro["edad_max"] and filtro["edad_min"] > filtro["edad_max"]:
        filtro["edad_min"], filtro["edad_max"] = filtro["edad_max"], filtro["edad_min"]
    return filtro


def aplicar(filtro: dict, filas: list[dict]) -> list[dict]:
    """Aplica el filtro en Python. Sin SQL generado, sin eval."""
    resultado = []
    for f in filas:
        if filtro["pais"] and N.clave(f.get("country", "")) != N.clave(filtro["pais"]):
            continue
        if filtro["ciudad"] and N.clave(filtro["ciudad"]) not in N.clave(f.get("city", "")):
            continue
        if filtro["cliente"] and N.clave(filtro["cliente"]) not in N.clave(f.get("customer", "")):
            continue
        if filtro["modalidad"] and f.get("modality") != filtro["modalidad"]:
            continue
        if filtro["marca"] and N.clave(f.get("brand", "")) != N.clave(filtro["marca"]):
            continue
        edad = int(f.get("age_years") or 0)
        if filtro["edad_min"] and edad < filtro["edad_min"]:
            continue
        if filtro["edad_max"] and (edad == 0 or edad > filtro["edad_max"]):
            continue
        if filtro["cantidad_min"] and int(f.get("quantity") or 0) < filtro["cantidad_min"]:
            continue
        if filtro["solo_oportunidades"] and not es_oportunidad_renovacion(f):
            continue
        if filtro["solo_sin_verificar"] and not sin_verificar(f):
            continue
        resultado.append(f)

    orden = {
        "edad": lambda r: -int(r.get("age_years") or 0),
        "cantidad": lambda r: -int(r.get("quantity") or 0),
        "confianza": lambda r: -int(r.get("confidence_score") or 0),
        "cliente": lambda r: N.clave(r.get("customer", "")),
    }
    return sorted(resultado, key=orden.get(filtro.get("ordenar_por"), orden["cliente"]))


def describir_filtro(filtro: dict) -> str:
    """El filtro en castellano, para que se vea que entendio el agente."""
    partes = []
    if filtro["pais"]:
        partes.append(f"país = {filtro['pais']}")
    if filtro["ciudad"]:
        partes.append(f"ciudad = {filtro['ciudad']}")
    if filtro["cliente"]:
        partes.append(f"cliente ~ {filtro['cliente']}")
    if filtro["modalidad"]:
        partes.append(f"modalidad = {filtro['modalidad']}")
    if filtro["marca"]:
        partes.append(f"marca = {filtro['marca']}")
    if filtro["edad_min"]:
        partes.append(f"edad >= {filtro['edad_min']} años")
    if filtro["edad_max"]:
        partes.append(f"edad <= {filtro['edad_max']} años")
    if filtro["cantidad_min"]:
        partes.append(f"cantidad >= {filtro['cantidad_min']}")
    if filtro["solo_oportunidades"]:
        partes.append(f"solo equipos de {EDAD_RENOVACION}+ años")
    if filtro["solo_sin_verificar"]:
        partes.append("solo datos sin verificar")
    return " · ".join(partes) if partes else "sin filtros (toda la base)"


def resumir(pregunta: str, filas: list[dict], motor=None) -> str:
    """Respuesta en una frase, calculada de los datos y no del modelo."""
    if not filas:
        return "No hay observaciones que cumplan esa condición."

    clientes = sorted({f["customer"] for f in filas})
    unidades = sum(int(f.get("quantity") or 0) for f in filas)
    modalidades = sorted({f["modality"] for f in filas})
    edades = [int(f.get("age_years") or 0) for f in filas if int(f.get("age_years") or 0) > 0]
    paises = sorted({f["country"] for f in filas})

    frase = (
        f"{len(filas)} observaciones · {unidades} unidades · "
        f"{len(clientes)} cliente(s) en {len(paises)} país(es) · "
        f"modalidades: {', '.join(modalidades)}"
    )
    if edades:
        frase += f" · edad media {sum(edades) / len(edades):.1f} años"
    return frase
