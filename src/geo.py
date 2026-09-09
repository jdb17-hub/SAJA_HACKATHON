"""Geografía de la base instalada.

El documento del reto pide una navegación Región → País → Ciudad → Cliente →
Equipos, así que aquí vive todo lo que convierte las filas planas de la base en
esa jerarquía, más las coordenadas para poder pintarlas en un mapa.

Las coordenadas son una tabla local a propósito. Geocodificar contra un servicio
externo mandaría los nombres de los clientes fuera del equipo, que es justo lo
que esta aplicación evita, y además dejaría de funcionar sin conexión.
"""
from __future__ import annotations

from . import normalize as N
from .confidence import es_oportunidad_renovacion, sin_verificar

# Ciudad -> (latitud, longitud). Cubre el dataset del reto y las capitales de la
# región; una ciudad que falte no rompe nada, simplemente no sale en el mapa.
COORDENADAS: dict[str, tuple[float, float]] = {
    "panama city": (8.9824, -79.5199),
    "sao paulo": (-23.5505, -46.6333),
    "campinas": (-22.9099, -47.0626),
    "rio de janeiro": (-22.9068, -43.1729),
    "brasilia": (-15.7939, -47.8828),
    "mexico city": (19.4326, -99.1332),
    "monterrey": (25.6866, -100.3161),
    "guadalajara": (20.6597, -103.3496),
    "santiago": (-33.4489, -70.6693),
    "valparaiso": (-33.0472, -71.6127),
    "buenos aires": (-34.6037, -58.3816),
    "cordoba": (-31.4201, -64.1888),
    "rosario": (-32.9442, -60.6505),
    "bogota": (4.7110, -74.0721),
    "medellin": (6.2442, -75.5812),
    "cali": (3.4516, -76.5320),
    "barranquilla": (10.9685, -74.7813),
    "lima": (-12.0464, -77.0428),
    "arequipa": (-16.4090, -71.5375),
    "san jose": (9.9281, -84.0907),
    "santo domingo": (18.4861, -69.9312),
    "santiago de los caballeros": (19.4517, -70.6970),
    "quito": (-0.1807, -78.4678),
    "guayaquil": (-2.1894, -79.8891),
    "montevideo": (-34.9011, -56.1645),
    "asuncion": (-25.2637, -57.5759),
    "la paz": (-16.4897, -68.1193),
    "santa cruz de la sierra": (-17.7833, -63.1821),
    "guatemala city": (14.6349, -90.5069),
    "tegucigalpa": (14.0723, -87.1921),
    "san salvador": (13.6929, -89.2182),
    "managua": (12.1150, -86.2362),
    "caracas": (10.4806, -66.9036),
    "san juan": (18.4655, -66.1057),
    "madrid": (40.4168, -3.7038),
    "barcelona": (41.3874, 2.1686),
    "lisboa": (38.7223, -9.1393),
    "miami": (25.7617, -80.1918),
}

# País -> centro aproximado, para cuando se mira el mapa a nivel de país.
CENTRO_PAIS: dict[str, tuple[float, float]] = {
    "panama": (8.5380, -80.7821),
    "brazil": (-14.2350, -51.9253),
    "mexico": (23.6345, -102.5528),
    "chile": (-35.6751, -71.5430),
    "argentina": (-38.4161, -63.6167),
    "colombia": (4.5709, -74.2973),
    "peru": (-9.1900, -75.0152),
    "costa rica": (9.7489, -83.7534),
    "dominican republic": (18.7357, -70.1627),
    "ecuador": (-1.8312, -78.1834),
    "uruguay": (-32.5228, -55.7658),
    "paraguay": (-23.4425, -58.4438),
    "bolivia": (-16.2902, -63.5887),
    "guatemala": (15.7835, -90.2308),
    "honduras": (15.2000, -86.2419),
    "el salvador": (13.7942, -88.8965),
    "nicaragua": (12.8654, -85.2072),
    "venezuela": (6.4238, -66.5897),
    "spain": (40.4637, -3.7492),
    "portugal": (39.3999, -8.2245),
    "united states": (37.0902, -95.7129),
}

# Agrupación por región, el primer nivel de la jerarquía del reto.
REGIONES: dict[str, str] = {
    "mexico": "Norteamérica",
    "united states": "Norteamérica",
    "panama": "Centroamérica y Caribe",
    "costa rica": "Centroamérica y Caribe",
    "guatemala": "Centroamérica y Caribe",
    "honduras": "Centroamérica y Caribe",
    "el salvador": "Centroamérica y Caribe",
    "nicaragua": "Centroamérica y Caribe",
    "dominican republic": "Centroamérica y Caribe",
    "brazil": "Sudamérica",
    "argentina": "Sudamérica",
    "chile": "Sudamérica",
    "colombia": "Sudamérica",
    "peru": "Sudamérica",
    "ecuador": "Sudamérica",
    "uruguay": "Sudamérica",
    "paraguay": "Sudamérica",
    "bolivia": "Sudamérica",
    "venezuela": "Sudamérica",
    "spain": "Europa",
    "portugal": "Europa",
}

NOMBRE_PAIS_ES: dict[str, str] = {
    "brazil": "Brasil",
    "mexico": "México",
    "panama": "Panamá",
    "peru": "Perú",
    "dominican republic": "República Dominicana",
    "united states": "Estados Unidos",
    "spain": "España",
}

REGION_DESCONOCIDA = "Sin región asignada"


def region_de(pais: str) -> str:
    return REGIONES.get(N.clave(pais), REGION_DESCONOCIDA)


def pais_es(pais: str) -> str:
    """Nombre del país en español, conservando el original si no está mapeado."""
    return NOMBRE_PAIS_ES.get(N.clave(pais), pais)


def coordenadas_de(ciudad: str, pais: str) -> tuple[float, float] | None:
    """Coordenadas de la ciudad; si no se conoce, el centro del país."""
    punto = COORDENADAS.get(N.clave(ciudad))
    if punto:
        return punto
    return CENTRO_PAIS.get(N.clave(pais))


def sedes(filas: list[dict]) -> list[dict]:
    """Una entrada por cliente, con sus coordenadas y sus totales.

    El mapa se pinta por sede y no por observación: un hospital con cuatro
    filas es un solo punto, con la suma de sus equipos.
    """
    agrupado: dict[str, dict] = {}
    for fila in filas:
        cliente = (fila.get("customer") or "").strip()
        if not cliente:
            continue
        entrada = agrupado.setdefault(
            cliente,
            {
                "customer": cliente,
                "city": fila.get("city", ""),
                "country": fila.get("country", ""),
                "region": region_de(fila.get("country", "")),
                "unidades": 0,
                "modalidades": set(),
                "edades": [],
                "confianzas": [],
                "oportunidades": 0,
                "sin_verificar": 0,
                "observaciones": 0,
                "ultima_visita": "",
            },
        )
        entrada["unidades"] += int(fila.get("quantity") or 0)
        entrada["modalidades"].add(fila.get("modality", ""))
        if int(fila.get("age_years") or 0) > 0:
            entrada["edades"].append(int(fila["age_years"]))
        entrada["confianzas"].append(int(fila.get("confidence_score") or 0))
        entrada["oportunidades"] += 1 if es_oportunidad_renovacion(fila) else 0
        entrada["sin_verificar"] += 1 if sin_verificar(fila) else 0
        entrada["observaciones"] += 1
        visita = fila.get("visit_date") or ""
        if visita > entrada["ultima_visita"]:
            entrada["ultima_visita"] = visita

    salida = []
    for entrada in agrupado.values():
        punto = coordenadas_de(entrada["city"], entrada["country"])
        if punto is None:
            continue
        entrada["lat"], entrada["lon"] = punto
        entrada["modalidades"] = sorted(m for m in entrada["modalidades"] if m)
        entrada["edad_media"] = (
            sum(entrada["edades"]) / len(entrada["edades"]) if entrada["edades"] else 0.0
        )
        entrada["confianza_media"] = (
            sum(entrada["confianzas"]) / len(entrada["confianzas"]) if entrada["confianzas"] else 0
        )
        entrada["pais_es"] = pais_es(entrada["country"])
        salida.append(entrada)
    return sorted(salida, key=lambda s: (-s["unidades"], s["customer"]))


def sin_coordenadas(filas: list[dict]) -> list[str]:
    """Clientes que no se pueden pintar por no conocer su ciudad.

    Se enseñan aparte en vez de desaparecer: un cliente ausente del mapa sin
    explicación parece un dato perdido.
    """
    faltan = set()
    for fila in filas:
        cliente = (fila.get("customer") or "").strip()
        if cliente and coordenadas_de(fila.get("city", ""), fila.get("country", "")) is None:
            faltan.add(cliente)
    return sorted(faltan)
