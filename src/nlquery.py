"""Consultas en lenguaje natural sobre la base instalada.

"clientes en Brasil con resonadores de mas de siete anos"

El modelo no genera SQL ni codigo: traduce la pregunta a un filtro con forma
fija, impuesta por JSON Schema durante el muestreo, y ese filtro se aplica en
Python. Asi una consulta rara devuelve resultados vacios en vez de ejecutar
algo inesperado, y la respuesta siempre sale de los datos reales y no de lo
que el modelo recuerde.
"""
from __future__ import annotations

import re

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
        "edad_max": {"type": ["integer", "null"], "minimum": 0, "maximum": 40},
        "cantidad_min": {"type": "integer", "minimum": 0, "maximum": 200},
        "cantidad_max": {"type": ["integer", "null"], "minimum": 0, "maximum": 200},
        "solo_oportunidades": {"type": "boolean"},
        "solo_sin_verificar": {"type": "boolean"},
        "agrupar_por": {"type": "string", "enum": ["", "cliente", "pais", "modalidad", "marca"]},
        "ordenar_por": {"type": "string", "enum": ["edad", "cantidad", "confianza", "cliente"]},
    },
    "required": [
        "pais", "ciudad", "cliente", "modalidad", "marca", "edad_min", "edad_max",
        "cantidad_min", "cantidad_max", "solo_oportunidades", "solo_sin_verificar", "agrupar_por", "ordenar_por",
    ],
}

SISTEMA = f"""You translate a question about a medical imaging installed base into a filter.
The question may be in Spanish or English. Return JSON only.

Empty strings mean no text filter. Null maximums mean no upper bound; zero is a real upper bound. Minimum zero means no lower restriction. Never invent a value the question
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
    '"edad_min":8,"edad_max":null,"cantidad_min":0,"cantidad_max":null,"solo_oportunidades":false,'
    '"solo_sin_verificar":false,"agrupar_por":"cliente","ordenar_por":"edad"}'
)

FILTRO_VACIO = {
    "pais": "", "ciudad": "", "cliente": "", "modalidad": "", "marca": "",
    "edad_min": 0, "edad_max": None, "cantidad_min": 0, "cantidad_max": None,
    "solo_oportunidades": False, "solo_sin_verificar": False,
    "agrupar_por": "", "ordenar_por": "cliente",
}


def _pais_canonico(value):
    clave = " ".join(N.clave(value or "").split())
    aliases = {**{N.clave(p):p for p in N.PAISES}, **N.PAIS_ES}
    return aliases.get(clave, (value or "").strip())


def _pais_explicito(pregunta):
    texto = " ".join(N.clave(pregunta).split())
    for ciudad in sorted(N.CIUDAD_ES, key=len, reverse=True):
        texto = re.sub(r"\b" + re.escape(ciudad) + r"\b", " ciudad ", texto)
    aliases = {**{N.clave(p):p for p in N.PAISES}, **N.PAIS_ES}
    patron = r"\b(?:" + "|".join(re.escape(a) for a in sorted(aliases,key=len,reverse=True)) + r")\b"
    matches = list(re.finditer(patron,texto))
    paises = {aliases[m.group()] for m in matches}
    if len(paises)>1:
        raise ValueError("Consulta un país a la vez para aplicar un filtro inequívoco.")
    if paises and re.search(r"\b(?:excepto|excluye|menos|fuera|except|excluding|not)\b",texto) and not re.search(r"menos de|less than",texto):
        raise ValueError("La exclusión de países no está disponible. Indica el país que quieres incluir.")
    return next(iter(paises),""),re.sub(patron," ",texto)


def _solo_pais(resto):
    return set(resto.split()) <= set("muestrame muestra mostrar ensename ver dame quiero necesito consultar buscar busca solo solamente unicamente todos todas todo los las el la de del en para pais equipos clientes hospitales clinicas registros observaciones datos instalados instaladas base instalada por favor show me only all equipment customers in from".split())


def interpretar(pregunta: str, motor) -> dict:
    """Pregunta en lenguaje natural -> filtro estructurado."""
    filtro = dict(FILTRO_VACIO)
    if not pregunta.strip():
        return filtro
    pais, resto = _pais_explicito(pregunta)
    if pais and _solo_pais(resto):
        return {**filtro, "pais": pais}
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
    filtro["pais"], _ = _pais_explicito(pregunta)
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
    pais, _ = _pais_explicito(pregunta)
    filtro["pais"] = pais or _pais_canonico(filtro["pais"])
    # Rebuild numeric constraints from their units, avoiding quantity -> age confusion.
    filtro.update(edad_min=0, edad_max=None, cantidad_min=0, cantidad_max=None)
    normal = " ".join(t.split())
    for palabra in normal.split():
        numero = N.a_numero(palabra)
        if numero is not None:
            normal = re.sub(r"\b"+re.escape(palabra)+r"\b",str(numero),normal)
    comparador = r"(mas de|mayores de|mayor de|superior a|more than|older than|over|menos de|menores de|less than|under|al menos|at least|como minimo|hasta|a lo sumo)"
    unidad = r"(anos?|years?|equipos?|resonadores?|tomografos?|ecografos?|mr|ct|ultrasounds?|unidades?)"
    for m in re.finditer(comparador+r"\s+(\d+)\s+"+unidad+r"\b",normal):
        op,n,unit=m[1],int(m[2]),m[3]
        campo='edad' if re.fullmatch(r'anos?|years?',unit) else 'cantidad'
        if op in ('mas de','mayores de','mayor de','superior a','more than','older than','over'):
            filtro[campo+'_min']=n+1
        elif op in ('menos de','menores de','less than','under'):
            if n==0:
                raise ValueError('No existen cantidades o edades negativas.')
            filtro[campo+'_max']=n-1
        elif op in ('hasta','a lo sumo'):
            filtro[campo+'_max']=n
        else:
            filtro[campo+'_min']=n
    for m in re.finditer(r"entre (\d+) y (\d+) "+unidad+r"\b",normal):
        campo='edad' if re.fullmatch(r'anos?|years?',m[3]) else 'cantidad'
        if int(m[1])>int(m[2]):
            raise ValueError('El mínimo no puede superar al máximo.')
        filtro[campo+'_min'],filtro[campo+'_max']=int(m[1]),int(m[2])
    for alias, ciudad in N.CIUDAD_ES.items():
        if re.search(r"\b"+re.escape(alias)+r"\b",t):
            filtro['ciudad']=ciudad
            break
    if filtro["marca"]:
        filtro["marca"] = next((m for m in N.catalogo_marcas() if N.clave(m) == N.clave(filtro["marca"])), filtro["marca"])
    if filtro["edad_max"] is not None and filtro["edad_min"] > filtro["edad_max"]:
        raise ValueError("El mínimo de edad supera al máximo solicitado.")
    return filtro


def aplicar(filtro: dict, filas: list[dict]) -> list[dict]:
    """Aplica el filtro en Python. Sin SQL generado, sin eval."""
    resultado = []
    for f in filas:
        if filtro["pais"] and N.clave(_pais_canonico(f.get("country"))) != N.clave(_pais_canonico(filtro["pais"])):
            continue
        if filtro["ciudad"] and N.clave(filtro["ciudad"]) not in N.clave(f.get("city", "")):
            continue
        if filtro["cliente"] and N.clave(filtro["cliente"]) not in N.clave(f.get("customer", "")):
            continue
        if filtro["modalidad"] and f.get("modality") != filtro["modalidad"]:
            continue
        if filtro["marca"] and N.clave(f.get("brand", "")) != N.clave(filtro["marca"]):
            continue
        if (filtro["edad_min"] or filtro["edad_max"] is not None) and f.get("age_years") is None:
            continue
        edad = int(f.get("age_years") or 0)
        if filtro["edad_min"] and edad < filtro["edad_min"]:
            continue
        if filtro["edad_max"] is not None and edad > filtro["edad_max"]:
            continue
        if filtro["solo_oportunidades"] and not es_oportunidad_renovacion(f):
            continue
        if filtro["solo_sin_verificar"] and not sin_verificar(f):
            continue
        resultado.append(f)

    if filtro['cantidad_min'] or filtro.get('cantidad_max') is not None:
        def grupo(row):
            return (N.clave(row.get('customer','')),N.clave(row.get('city','')),
                    _pais_canonico(row.get('country','')),row.get('modality'))
        totales = {}
        for row in resultado:
            k=grupo(row)
            if row.get('quantity') is None:
                totales[k]=None
            elif k not in totales or totales[k] is not None:
                totales[k]=totales.get(k,0)+row['quantity']
        resultado=[r for r in resultado if totales[grupo(r)] is not None
            and totales[grupo(r)]>=filtro['cantidad_min']
            and (filtro.get('cantidad_max') is None or totales[grupo(r)]<=filtro['cantidad_max'])]
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
    if filtro["edad_max"] is not None:
        partes.append(f"edad <= {filtro['edad_max']} años")
    if filtro["cantidad_min"]:
        partes.append(f"cantidad >= {filtro['cantidad_min']}")
    if filtro.get("cantidad_max") is not None:
        partes.append(f"cantidad <= {filtro['cantidad_max']}")
    if filtro["solo_oportunidades"]:
        partes.append(f"solo equipos de {EDAD_RENOVACION}+ años")
    if filtro["solo_sin_verificar"]:
        partes.append("solo datos sin verificar")
    return " · ".join(partes) if partes else "sin filtros (toda la base)"


def resumir(pregunta: str, filas: list[dict], motor=None) -> str:
    """Respuesta en una frase, calculada de los datos y no del modelo."""
    if not filas:
        return "No hay observaciones que cumplan esa condición."

    clientes = {(N.clave(f["customer"]), N.clave(f.get("city", "")), _pais_canonico(f.get("country", ""))) for f in filas}
    unidades = sum(int(f.get("quantity") or 0) for f in filas)
    modalidades = sorted({f["modality"] for f in filas})
    edades = [int(f.get("age_years") or 0) for f in filas if int(f.get("age_years") or 0) > 0]
    paises = {_pais_canonico(f["country"]) for f in filas}

    frase = (
        f"{len(filas)} observaciones · {unidades} unidades · "
        f"{len(clientes)} cliente(s) en {len(paises)} país(es) · "
        f"modalidades: {', '.join(modalidades)}"
    )
    desconocidas = sum(f.get("quantity") is None for f in filas)
    if desconocidas:
        frase += f" · {desconocidas} grupo(s) con cantidad desconocida; unidades conocidas"
    if edades:
        frase += f" · edad media {sum(edades) / len(edades):.1f} años"
    return frase
