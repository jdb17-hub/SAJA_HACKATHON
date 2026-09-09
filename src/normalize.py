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


# Palabras que aparecen en el nombre de casi cualquier centro sanitario, y por
# tanto no identifican a ninguno.
# Solo el tipo de centro. Palabras como "nacional", "general" o "regional" no
# entran aqui: forman parte de nombres reales ("Hospital Nacional"), y tratarlas
# como relleno haria irreconocibles a clientes legitimos.
GENERICOS = {
    "hospital", "hospitales", "clinica", "clinicas", "centro", "centros",
    "medico", "medica", "instituto", "policlinica", "policlinico", "sanatorio",
    "diagnostico", "diagnostica",
    "clinic", "medical", "center", "centre", "institute",
}

# Relleno que sobrevive a la transcripcion pero no forma parte de un nombre
# propio: "la clinica de aqui al lado" no es el nombre de ningun cliente.
RELLENO = {
    "de", "del", "la", "las", "los", "el", "un", "una", "y", "que", "aqui",
    "alla", "ahi", "al", "lado", "cerca", "este", "esta", "esa", "ese", "mismo",
    "misma", "otro", "otra", "nuevo", "nueva", "viejo", "vieja", "grande",
    "pequeno", "donde", "estoy", "visite", "the", "of", "here", "next", "door",
    "nearby", "this", "that", "one",
}


def tokens_distintivos(nombre: str) -> list[str]:
    """Palabras del nombre que de verdad identifican a un cliente."""
    return [
        t for t in clave(nombre).split()
        if len(t) > 2 and t not in GENERICOS and t not in RELLENO
    ]


def es_nombre_identificable(nombre: str) -> bool:
    """Si no queda nada tras quitar lo generico y el relleno, no es un nombre.

    "el hospital", "la clinica de aqui al lado" o "centro" describen un sitio
    pero no nombran a ningun cliente. Guardarlos ensucia la base con filas que
    nadie podra reconciliar despues, asi que es mejor dejarlo vacio y preguntar.
    """
    return bool(tokens_distintivos(nombre))


def normalizar_cliente(valor: str) -> tuple[str, dict | None]:
    """Empareja contra el catalogo de clientes conocidos.

    Devuelve (nombre, ficha del catalogo) donde la ficha es None si el cliente
    no estaba en la base -- un cliente nuevo, que se registra igual. El nombre
    es DESCONOCIDO cuando lo dicho no identifica a nadie.

    El emparejado va por palabras distintivas y no por parecido de cadenas: con
    parecido, "Hospital" solo o "DemoCare" (que comparten los trece clientes del
    catalogo) se enganchaban al primero de la lista e inventaban un cliente que
    nadie menciono.
    """
    if not valor or valor.strip().lower() in {"unknown", "desconocido", ""}:
        return DESCONOCIDO, None

    propios = tokens_distintivos(valor)
    if not propios:
        return DESCONOCIDO, None

    clientes = catalogo_clientes()
    puntuados: list[tuple[float, dict]] = []
    for ficha in clientes:
        del_catalogo = tokens_distintivos(ficha["customer"])
        comunes = len(set(propios) & set(del_catalogo))
        if comunes:
            puntuados.append((comunes / max(len(propios), len(del_catalogo)), ficha))

    puntuados.sort(key=lambda p: -p[0])
    if puntuados:
        mejor, ficha = puntuados[0]
        segundo = puntuados[1][0] if len(puntuados) > 1 else 0.0
        # Se exige que gane con claridad: si varios clientes empatan, lo dicho no
        # basta para elegir entre ellos y forzarlo seria adivinar.
        if mejor >= 0.5 and mejor > segundo:
            return ficha["customer"], ficha
        if mejor >= 0.5:
            # Empate: lo dicho apunta al catalogo pero no distingue a cual. No es
            # un cliente nuevo -- es uno conocido dicho a medias, asi que se
            # pregunta en vez de crear un duplicado con el nombre incompleto.
            return DESCONOCIDO, None

    return valor.strip(), None
