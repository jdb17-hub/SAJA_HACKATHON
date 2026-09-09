"""Capa deterministica.

Un modelo de 1-2 B corriendo en un portatil se equivoca con los numerales en
espanol ("dos resonadores" -> quantity 0) y a veces inventa marcas. Todo lo que
se puede resolver con reglas se resuelve con reglas; el LLM solo se usa donde
hace falta criterio. Este modulo no llama a ningun modelo.
"""
from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher

from .config import DATA_DIR
from .schema import DESCONOCIDO, Estado, Modalidad

# --- utilidades de texto ----------------------------------------------------


def sin_acentos(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def clave(texto: str) -> str:
    """Forma canonica para comparar nombres: sin acentos, minusculas, sin signos."""
    return re.sub(r"[^a-z0-9 ]+", " ", sin_acentos(texto or "").lower()).strip()


def parecido(a: str, b: str) -> float:
    return SequenceMatcher(None, clave(a), clave(b)).ratio()


# --- numerales --------------------------------------------------------------

NUMEROS = {
    "un": 1, "uno": 1, "una": 1, "one": 1, "single": 1, "unico": 1,
    "dos": 2, "two": 2, "par": 2, "couple": 2,
    "tres": 3, "three": 3,
    "cuatro": 4, "four": 4,
    "cinco": 5, "five": 5,
    "seis": 6, "six": 6,
    "siete": 7, "seven": 7,
    "ocho": 8, "eight": 8,
    "nueve": 9, "nine": 9,
    "diez": 10, "ten": 10,
    "once": 11, "eleven": 11,
    "doce": 12, "twelve": 12,
    "trece": 13, "thirteen": 13,
    "catorce": 14, "fourteen": 14,
    "quince": 15, "fifteen": 15,
    "dieciseis": 16, "sixteen": 16,
    "diecisiete": 17, "seventeen": 17,
    "dieciocho": 18, "eighteen": 18,
    "diecinueve": 19, "nineteen": 19,
    "veinte": 20, "twenty": 20,
}


def a_numero(token: str) -> int | None:
    t = sin_acentos(token).lower().strip()
    if t.isdigit():
        return int(t)
    return NUMEROS.get(t)


# --- modalidades ------------------------------------------------------------

SINONIMOS_MODALIDAD: dict[Modalidad, list[str]] = {
    Modalidad.MR: [
        "resonador", "resonadores", "resonancia", "resonancias",
        "resonancia magnetica", "mr", "mri", "rm", "rmn", "magnetic resonance",
    ],
    Modalidad.CT: [
        "tomografo", "tomografos", "tomografia", "tomografias", "tac", "tc",
        "ct", "cts", "ct scanner", "ct scanners", "cat scan", "computed tomography",
    ],
    Modalidad.ULTRASOUND: [
        "ecografo", "ecografos", "ecografia", "ecografias", "ultrasonido",
        "ultrasonidos", "ecocardiografo", "ultrasound", "ultrasounds", "us",
    ],
    Modalidad.XRAY: [
        "rayos x", "rayo x", "radiografia", "radiografias", "rx",
        "x ray", "xray", "radiography",
    ],
    Modalidad.MONITORING: [
        "monitor", "monitores", "monitorizacion", "monitoreo",
        "monitor de paciente", "monitores de paciente", "patient monitoring",
        "patient monitor", "monitors",
    ],
    Modalidad.IGT: [
        "angiografo", "angiografos", "angiografia", "hemodinamia", "sala hibrida",
        "arco en c", "image guided therapy", "igt", "cath lab", "angiography",
    ],
}

# Se ordena por longitud descendente para que "ct scanner" gane a "ct".
_MODALIDAD_POR_TERMINO: list[tuple[str, Modalidad]] = sorted(
    ((clave(t), m) for m, ts in SINONIMOS_MODALIDAD.items() for t in ts),
    key=lambda p: -len(p[0]),
)


_TERMINO_EXACTO: dict[str, Modalidad] = {
    t: m for t, m in _MODALIDAD_POR_TERMINO
}


def detectar_modalidad(texto: str) -> Modalidad:
    """Busca un termino de modalidad en cualquier parte del texto."""
    t = f" {clave(texto)} "
    for termino, modalidad in _MODALIDAD_POR_TERMINO:
        if f" {termino} " in t:
            return modalidad
    return Modalidad.UNKNOWN


def terminos_de(modalidad: Modalidad) -> list[str]:
    """Formas canonicas con las que se nombra una modalidad."""
    return [clave(t) for t in SINONIMOS_MODALIDAD.get(modalidad, [])]


def modalidad_exacta(frase: str) -> Modalidad:
    """La frase completa *es* un termino de modalidad.

    Hace falta al contar unidades: recorriendo n-gramas, 'dos resonadores y'
    contiene una modalidad pero no empieza en ella, y usar la posicion
    equivocada desplaza el numero que le corresponde.
    """
    return _TERMINO_EXACTO.get(clave(frase), Modalidad.UNKNOWN)


# --- senales de incertidumbre ----------------------------------------------

SENALES_ESTIMADO = [
    "quiza", "quizas", "tal vez", "creo", "parece", "parecen", "aproximadamente",
    "aprox", "mas o menos", "alrededor", "cerca de", "unos", "unas",
    "diria", "estimo", "no estoy seguro", "puede que", "posiblemente", "casi",
    "maybe", "around", "about", "roughly", "approximately", "i think", "seems",
    "looks", "estimate", "not sure", "probably", "several", "many",
]
SENALES_CONFIRMADO = [
    "confirme", "confirmado", "verifique", "vi la placa", "lei la etiqueta",
    "conte", "contamos", "exactamente", "seguro", "sin duda",
    "confirmed", "verified", "i counted", "exactly", "definitely", "saw the label",
]


def detectar_estado(texto: str) -> Estado:
    """Confirmado / Reportado / Estimado segun como de seguro suena el colaborador."""
    t = clave(texto)
    if any(clave(s) in t for s in SENALES_CONFIRMADO):
        return Estado.CONFIRMADO
    if any(clave(s) in t for s in SENALES_ESTIMADO):
        return Estado.ESTIMADO
    return Estado.REPORTADO


# --- catalogos --------------------------------------------------------------


def _cargar(nombre: str, por_defecto):
    ruta = DATA_DIR / nombre
    if not ruta.exists():
        return por_defecto
    return json.loads(ruta.read_text(encoding="utf-8"))


def catalogo_marcas() -> list[str]:
    return _cargar("reference_lists.json", {}).get("Brand", [])


def catalogo_clientes() -> list[dict]:
    """Clientes conocidos (nombre, ciudad, pais) a partir de la semilla."""
    vistos: dict[str, dict] = {}
    for fila in _cargar("seed_installed_base.json", []):
        nombre = (fila.get("Customer / Hospital") or "").strip()
        if nombre and nombre not in vistos:
            vistos[nombre] = {
                "customer": nombre,
                "city": fila.get("City", DESCONOCIDO),
                "country": fila.get("Country", DESCONOCIDO),
            }
    return list(vistos.values())


def catalogo_modelos() -> list[str]:
    modelos = {(f.get("Dummy Model") or "").strip() for f in _cargar("seed_installed_base.json", [])}
    return sorted(m for m in modelos if m)


PAISES = [
    "Panama", "Brazil", "Mexico", "Chile", "Argentina", "Colombia", "Peru",
    "Costa Rica", "Dominican Republic", "Ecuador", "Uruguay", "Paraguay",
    "Bolivia", "Guatemala", "Honduras", "El Salvador", "Nicaragua", "Venezuela",
    "Spain", "Portugal", "United States",
]
PAIS_ES = {
    "panama": "Panama", "brasil": "Brazil", "brazil": "Brazil", "mexico": "Mexico",
    "chile": "Chile", "argentina": "Argentina", "colombia": "Colombia", "peru": "Peru",
    "costa rica": "Costa Rica", "republica dominicana": "Dominican Republic",
    "dominican republic": "Dominican Republic", "ecuador": "Ecuador",
    "uruguay": "Uruguay", "paraguay": "Paraguay", "bolivia": "Bolivia",
    "guatemala": "Guatemala", "honduras": "Honduras", "el salvador": "El Salvador",
    "nicaragua": "Nicaragua", "venezuela": "Venezuela", "espana": "Spain",
    "spain": "Spain", "portugal": "Portugal", "estados unidos": "United States",
}
CIUDAD_ES = {
    "ciudad de panama": "Panama City", "panama city": "Panama City",
    "sao paulo": "Sao Paulo", "san pablo": "Sao Paulo", "campinas": "Campinas",
    "ciudad de mexico": "Mexico City", "cdmx": "Mexico City",
    "mexico city": "Mexico City", "monterrey": "Monterrey", "santiago": "Santiago",
    "buenos aires": "Buenos Aires", "bogota": "Bogota", "medellin": "Medellin",
    "lima": "Lima", "san jose": "San Jose", "santo domingo": "Santo Domingo",
    "quito": "Quito", "guayaquil": "Guayaquil", "cali": "Cali",
    "guadalajara": "Guadalajara",
}


def emparejar(valor: str, opciones: list[str], umbral: float = 0.82) -> str | None:
    """Devuelve la opcion del catalogo mas parecida, o None si ninguna convence."""
    if not valor or not opciones:
        return None
    mejor, puntaje = None, 0.0
    v = clave(valor)
    if not v:
        return None
    for opcion in opciones:
        o = clave(opcion)
        # Contencion exacta: "DemoCare Pacific" dentro de "Hospital DemoCare Pacific".
        p = 1.0 if (v in o or o in v) else SequenceMatcher(None, v, o).ratio()
        if p > puntaje:
            mejor, puntaje = opcion, p
    return mejor if puntaje >= umbral else None


def normalizar_marca(valor: str) -> str:
    if not valor or valor.strip().lower() in {"unknown", "desconocido", "", "n/a"}:
        return DESCONOCIDO
    return emparejar(valor, catalogo_marcas(), 0.80) or valor.strip()


def normalizar_pais(valor: str) -> str:
    if not valor or valor.strip().lower() in {"unknown", "desconocido", ""}:
        return DESCONOCIDO
    return PAIS_ES.get(clave(valor)) or emparejar(valor, PAISES, 0.85) or valor.strip()


def normalizar_ciudad(valor: str) -> str:
    if not valor or valor.strip().lower() in {"unknown", "desconocido", ""}:
        return DESCONOCIDO
    return CIUDAD_ES.get(clave(valor)) or valor.strip()


def normalizar_cliente(valor: str) -> tuple[str, dict | None]:
    """Empareja contra el catalogo de clientes conocidos.

    Devuelve (nombre normalizado, ficha del catalogo o None si es cliente nuevo).
    """
    if not valor or valor.strip().lower() in {"unknown", "desconocido", ""}:
        return DESCONOCIDO, None
    clientes = catalogo_clientes()
    match = emparejar(valor, [c["customer"] for c in clientes], 0.80)
    if match:
        return match, next(c for c in clientes if c["customer"] == match)
    return valor.strip(), None
