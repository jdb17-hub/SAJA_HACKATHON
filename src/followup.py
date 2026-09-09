"""Preguntas de seguimiento.

El colaborador acaba de salir de un hospital y tiene un minuto, no diez. Asi
que no se le repasa el formulario entero: se le pregunta primero por el dato
que mas valor tiene y que todavia falta, y se para en cuanto deja de compensar.

El valor de un dato no es el orden de la columna. Saber la marca de cinco
ecografos vale mas que saber el modelo de uno, porque la marca dice quien es el
competidor instalado y la cantidad multiplica el tamano de la oportunidad.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from . import normalize as N
from .schema import DESCONOCIDO, Borrador, Equipo, Modalidad, es_desconocido

# Peso base por campo: cuanto aporta a una decision comercial.
PESO_CAMPO = {
    "customer": 100,  # sin cliente la observacion no se puede archivar
    "modality": 90,
    "quantity": 70,
    "brand": 55,  # competidor instalado
    "age_years": 50,  # antiguedad -> ventana de renovacion
    "country": 30,
    "city": 20,
    "model": 12,
}


@dataclass
class Pregunta:
    campo: str
    texto: str
    indice: int | None = None  # posicion del equipo al que se refiere
    valor: float = 0.0

    @property
    def clave(self) -> str:
        return self.campo if self.indice is None else f"items.{self.indice}.{self.campo}"


PLURAL_MODALIDAD = {
    Modalidad.MR: "resonadores",
    Modalidad.CT: "tomógrafos",
    Modalidad.ULTRASOUND: "ecógrafos",
    Modalidad.XRAY: "equipos de rayos X",
    Modalidad.MONITORING: "monitores de paciente",
    Modalidad.IGT: "angiógrafos",
    Modalidad.UNKNOWN: "equipos",
}


def _etiqueta_modalidad(m: Modalidad) -> str:
    return f"los {PLURAL_MODALIDAD[m]}"


def _candidatas(borrador: Borrador) -> list[Pregunta]:
    """Todas las preguntas pendientes, con su valor calculado."""
    pendientes: list[Pregunta] = []

    if es_desconocido(borrador.customer):
        pendientes.append(Pregunta("customer", "¿Qué hospital o clínica visitaste?", None, PESO_CAMPO["customer"]))
    if es_desconocido(borrador.country):
        pendientes.append(Pregunta("country", "¿En qué país está el cliente?", None, PESO_CAMPO["country"]))
    if es_desconocido(borrador.city):
        pendientes.append(Pregunta("city", "¿En qué ciudad está?", None, PESO_CAMPO["city"]))
    if not borrador.items:
        pendientes.append(Pregunta("modality", "¿Qué tipo de equipos viste?", None, PESO_CAMPO["modality"]))

    for i, eq in enumerate(borrador.items):
        etiqueta = _etiqueta_modalidad(eq.modality)
        # Un grupo grande multiplica el valor de conocer su marca o su edad:
        # la marca de cinco ecografos vale mas que la de uno.
        escala = 1.0 + math.log1p(max(eq.quantity, 1)) / 2

        if eq.modality == Modalidad.UNKNOWN:
            pendientes.append(Pregunta("modality", "¿Qué tipo de equipo era exactamente?", i, PESO_CAMPO["modality"]))
        if eq.quantity <= 0:
            pendientes.append(Pregunta("quantity", f"¿Cuántos {PLURAL_MODALIDAD[eq.modality]} viste?", i, PESO_CAMPO["quantity"]))
        if es_desconocido(eq.brand):
            pendientes.append(
                Pregunta("brand", f"¿Sabes de qué marca son {etiqueta}?", i, PESO_CAMPO["brand"] * escala)
            )
        if eq.age_years <= 0:
            pendientes.append(
                Pregunta("age_years", f"¿Qué antigüedad aproximada tienen {etiqueta}?", i, PESO_CAMPO["age_years"] * escala)
            )
        if es_desconocido(eq.model) and not es_desconocido(eq.brand):
            # El modelo solo se pregunta si ya sabemos la marca; si no, sobra.
            pendientes.append(Pregunta("model", f"¿Recuerdas el modelo de {etiqueta}?", i, PESO_CAMPO["model"]))

    return sorted(pendientes, key=lambda p: -p.valor)


def siguiente_pregunta(borrador: Borrador, omitidas: set[str] | None = None) -> Pregunta | None:
    """La pregunta pendiente mas valiosa, o None si ya no compensa preguntar."""
    omitidas = omitidas or set()
    for pregunta in _candidatas(borrador):
        if pregunta.clave not in omitidas:
            return pregunta
    return None


def pendientes(borrador: Borrador, omitidas: set[str] | None = None) -> list[Pregunta]:
    omitidas = omitidas or set()
    return [p for p in _candidatas(borrador) if p.clave not in omitidas]


# --- interpretar la respuesta ----------------------------------------------

NEGATIVAS = [
    "no se", "no lo se", "ni idea", "no recuerdo", "desconozco", "no sabria",
    "no pude ver", "no se ve", "no estaba", "sin datos", "no aplica",
    "dont know", "do not know", "no idea", "not sure", "cannot tell", "unknown",
]


def es_negativa(respuesta: str) -> bool:
    """El colaborador dice que no lo sabe: se marca Unknown y no se vuelve a preguntar."""
    t = N.clave(respuesta)
    return any(n in t for n in (N.clave(x) for x in NEGATIVAS))


ESQUEMA_RESPUESTA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "valor": {"type": "string"},
        "sabe": {"type": "boolean"},
    },
    "required": ["valor", "sabe"],
}

SISTEMA_RESPUESTA = """You read a field engineer's short answer to one question about hospital
imaging equipment and return just the value asked for. Spanish or English input.
Set sabe=false if the answer means the person does not know. Never invent a value:
if the answer does not contain it, sabe=false. Return the value verbatim from the
answer, with no extra words."""


def aplicar_respuesta(
    borrador: Borrador, pregunta: Pregunta, respuesta: str, motor=None
) -> tuple[Borrador, bool]:
    """Mete la respuesta en el borrador.

    Devuelve (borrador, se_pudo_usar). Si no se pudo, la interfaz marca la
    pregunta como omitida para no entrar en bucle.
    """
    respuesta = (respuesta or "").strip()
    if not respuesta or es_negativa(respuesta):
        return borrador, False

    campo, i = pregunta.campo, pregunta.indice
    equipo = borrador.items[i] if i is not None and i < len(borrador.items) else None

    # 1. Reglas: resuelven la mayoria de respuestas cortas sin gastar inferencia.
    if campo == "quantity" and equipo is not None:
        valor = _primer_numero(respuesta)
        if valor:
            equipo.quantity = valor
            equipo.status = N.detectar_estado(respuesta)
            return borrador, True

    if campo == "age_years" and equipo is not None:
        valor = _primera_edad(respuesta)
        if valor:
            equipo.age_years = valor
            equipo.status = N.detectar_estado(respuesta)
            return borrador, True

    if campo == "brand" and equipo is not None:
        marca = N.emparejar(respuesta, N.catalogo_marcas(), 0.62)
        if marca:
            equipo.brand = marca
            return borrador, True

    if campo == "model" and equipo is not None:
        modelo = N.emparejar(respuesta, N.catalogo_modelos(), 0.62)
        if modelo:
            equipo.model = modelo
            return borrador, True

    if campo == "modality":
        modalidad = N.detectar_modalidad(respuesta)
        if modalidad != Modalidad.UNKNOWN:
            if equipo is not None:
                equipo.modality = modalidad
            else:
                borrador.items.append(
                    Equipo(modality=modalidad, quantity=_primer_numero(respuesta) or 0,
                           status=N.detectar_estado(respuesta))
                )
            return borrador, True

    if campo == "country":
        pais = N.normalizar_pais(respuesta)
        if not es_desconocido(pais):
            borrador.country = pais
            return borrador, True

    if campo == "city":
        borrador.city = N.normalizar_ciudad(respuesta)
        return borrador, True

    if campo == "customer":
        nombre, ficha = N.normalizar_cliente(respuesta)
        if not es_desconocido(nombre):
            borrador.customer = nombre
            if ficha:
                if es_desconocido(borrador.city):
                    borrador.city = ficha.get("city", DESCONOCIDO)
                if es_desconocido(borrador.country):
                    borrador.country = ficha.get("country", DESCONOCIDO)
            return borrador, True

    # 2. LLM en el dispositivo, para respuestas menos directas
    #    ("creo que era la misma que los otros, la de siempre").
    if motor is not None and motor.estado.listo:
        try:
            salida = motor.json_estructurado(
                sistema=SISTEMA_RESPUESTA,
                mensajes=[{"role": "user", "content": f"Question: {pregunta.texto}\nAnswer: {respuesta}"}],
                esquema=ESQUEMA_RESPUESTA,
                nombre="respuesta",
                max_tokens=120,
            )
            if salida.get("sabe") and salida.get("valor"):
                limpia = Pregunta(campo, pregunta.texto, i, 0)
                return aplicar_respuesta(borrador, limpia, str(salida["valor"]), motor=None)
        except Exception:  # noqa: BLE001
            pass

    # 3. Texto libre: no se pierde, se guarda como nota.
    borrador.notes = f"{borrador.notes} | {pregunta.texto} {respuesta}".strip(" |")
    return borrador, False


def _primer_numero(texto: str) -> int:
    for token in N.clave(texto).split():
        valor = N.a_numero(token)
        if valor is not None:
            return valor
    return 0


def _primera_edad(texto: str) -> int:
    """Acepta '7', 'siete anos', '5-7 anos' (toma el punto medio) o un ano de instalacion."""
    t = N.clave(texto)
    rango = re.search(r"(\d+)\s*(?:-|a|y)\s*(\d+)", t)
    if rango:
        a, b = int(rango.group(1)), int(rango.group(2))
        if 0 < a <= 40 and 0 < b <= 40:
            return round((a + b) / 2)
    from datetime import date

    anio = re.search(r"\b(19[89]\d|20[0-4]\d)\b", t)
    if anio:
        edad = date.today().year - int(anio.group(1))
        if 0 < edad <= 40:
            return edad
    valor = _primer_numero(texto)
    return valor if 0 < valor <= 40 else 0


# --- resumen de confirmacion ------------------------------------------------


def describir_equipos(borrador: Borrador) -> str:
    """Los equipos en lenguaje corriente, sin mencionar al cliente.

    Se usa suelto cuando todavia no hay cliente: asi se puede ensenar lo que si
    se entendio sin construir una frase con un hueco dentro.
    """
    if not borrador.items:
        return "Todavía no hay ningún equipo registrado."
    partes = []
    for eq in borrador.items:
        cantidad = eq.quantity if eq.quantity else "?"
        trozo = f"{cantidad} {PLURAL_MODALIDAD[eq.modality]}"
        if not es_desconocido(eq.brand):
            trozo += f" {eq.brand}"
        if not es_desconocido(eq.model):
            trozo += f" {eq.model}"
        if eq.age_years:
            trozo += f", aprox. {eq.age_years} años"
        partes.append(trozo)
    return "; ".join(partes)


def resumen(borrador: Borrador) -> str:
    """Frase de cierre antes de guardar, como pide el paso 12 del Excel.

    Solo tiene sentido con un cliente identificado. Sin el saldria un "En
    Unknown: ..." que ademas repite un problema ya senalado justo debajo, asi
    que en ese caso se describe unicamente lo capturado.
    """
    if not borrador.items:
        return "Todavía no hay ningún equipo registrado."
    equipos = describir_equipos(borrador)
    if es_desconocido(borrador.customer):
        return f"Se entendió esto: {equipos}."
    lugar = borrador.customer
    if not es_desconocido(borrador.city):
        lugar += f" ({borrador.city})"
    return f"En {lugar}: {equipos}. ¿Es correcto?"
