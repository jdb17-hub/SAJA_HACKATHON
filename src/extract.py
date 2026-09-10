"""Extraccion hibrida: reglas + LLM en el dispositivo.

El reparto de trabajo importa. Un modelo de 1-2 B que cabe en un portatil sin
GPU es bueno interpretando la *estructura* de una frase ("dos se ven viejos y
uno mas nuevo" son dos grupos de equipos) y malo con el detalle literal
(numerales en espanol, nombres propios, y sobre todo inventarse marcas).

Asi que:
  1. Las reglas leen el texto y sacan lo que es literal: cantidades, modalidades,
     marcas del catalogo, edades, ubicacion, nivel de certeza.
  2. El LLM de QVAC propone la estructura completa, con el JSON Schema impuesto
     durante el muestreo.
  3. La fusion se queda con lo mejor de cada uno y aplica el guardarrail
     importante: un valor solo sobrevive si esta anclado en el texto original.
     Si el modelo se inventa "Siemens" y esa palabra no aparece, se descarta.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import normalize as N
from .schema import DESCONOCIDO, Borrador, Equipo, Estado, Modalidad, es_desconocido

VENTANA_CANTIDAD = 4  # palabras antes de la modalidad donde se busca el numero


# --- 1. capa de reglas ------------------------------------------------------


@dataclass
class Pistas:
    """Lo que las reglas pueden afirmar del texto sin ayuda de ningun modelo."""

    customer: str = DESCONOCIDO
    ficha_cliente: dict | None = None
    city: str = DESCONOCIDO
    country: str = DESCONOCIDO
    conteos: dict[Modalidad, list[int]] = field(default_factory=dict)
    marcas: list[str] = field(default_factory=list)
    modelos: list[str] = field(default_factory=list)
    edades: list[int] = field(default_factory=list)
    estado: Estado = Estado.REPORTADO

    def resumen(self) -> str:
        """Se inyecta en el prompt para anclar al modelo."""
        partes = []
        if not es_desconocido(self.customer):
            partes.append(f"customer={self.customer}")
        if not es_desconocido(self.country):
            partes.append(f"country={self.country}")
        if not es_desconocido(self.city):
            partes.append(f"city={self.city}")
        for modalidad, cantidades in self.conteos.items():
            partes.append(f"{modalidad.value} counts={cantidades}")
        if self.marcas:
            partes.append(f"brands seen={self.marcas}")
        if self.modelos:
            partes.append(f"models seen={self.modelos}")
        if self.edades:
            partes.append(f"ages mentioned={self.edades}")
        return "; ".join(partes) if partes else "(nothing certain)"


def _tokens(texto: str) -> list[str]:
    return N.clave(texto).split()


def _conteos_por_modalidad(texto: str) -> dict[Modalidad, list[int]]:
    """Busca pares (numero, modalidad): 'dos resonadores', '3 CT scanners'.

    Recorre la frase y, al encontrar un termino de modalidad, mira hacia atras
    unas pocas palabras a por un numeral. Un numeral solo se consume una vez,
    para que "dos resonadores y un tomografo" no asigne 2 a ambos.
    """
    tokens = _tokens(texto)
    conteos: dict[Modalidad, list[int]] = {}
    consumidos: set[int] = set()

    i = 0
    while i < len(tokens):
        # Se prueban primero los terminos de dos palabras ("ct scanner", "rayos x").
        # La coincidencia es exacta sobre el n-grama: si se aceptara una
        # coincidencia parcial, "dos resonadores y" contaria como modalidad en la
        # posicion de "dos" y el numero se asignaria al equipo equivocado.
        modalidad, avance = Modalidad.UNKNOWN, 1
        for largo in (3, 2, 1):
            if i + largo > len(tokens):
                continue
            candidato = N.modalidad_exacta(" ".join(tokens[i : i + largo]))
            if candidato != Modalidad.UNKNOWN:
                modalidad, avance = candidato, largo
                break
        if modalidad == Modalidad.UNKNOWN:
            i += 1
            continue

        cantidad = None
        for j in range(i - 1, max(-1, i - 1 - VENTANA_CANTIDAD), -1):
            if j in consumidos:
                continue
            valor = N.a_numero(tokens[j])
            if valor is not None:
                cantidad, _ = valor, consumidos.add(j)
                break
        if cantidad is not None:
            conteos.setdefault(modalidad, []).append(cantidad)
        else:
            conteos.setdefault(modalidad, [])
        i += avance
    return conteos


def _edades(texto: str, fecha=None) -> list[int]:
    """Numeros que van acompanados de una unidad de tiempo, o un ano de instalacion."""
    t = N.clave(texto)
    encontrados: list[int] = []
    patron = r"([a-z0-9]+)\s+(?:anos|ano|years|year|yrs)"
    for token in re.findall(patron, t):
        valor = N.a_numero(token)
        if valor is not None and 0 < valor <= 40:
            encontrados.append(valor)
    from datetime import date

    for anio in re.findall(r"\b(19[89]\d|20[0-4]\d)\b", t):
        edad = (fecha or date.today()).year - int(anio)
        if 0 < edad <= 40:
            encontrados.append(edad)
    return encontrados


def _menciones(texto: str, catalogo: list[str]) -> list[str]:
    """Entradas del catalogo realmente mencionadas en el texto."""
    t = N.clave(texto)
    return [op for op in catalogo if N.clave(op) and N.clave(op) in t]


def _ubicacion(texto: str) -> tuple[str, str]:
    t = N.clave(texto)
    ciudad = next((v for k, v in N.CIUDAD_ES.items() if k in t), DESCONOCIDO)
    pais = next((v for k, v in N.PAIS_ES.items() if k in t), DESCONOCIDO)
    return ciudad, pais


def _cliente(texto: str) -> tuple[str, dict | None]:
    """Empareja contra clientes conocidos por solapamiento de palabras.

    Nombres como 'Hospital DemoCare Pacific' se reconocen aunque el colaborador
    diga solo 'DemoCare Pacific' o los rodee de otras palabras.
    """
    tokens = set(N.clave(texto).split())
    candidatos = []
    for ficha in N.catalogo_clientes():
        propios = set(N.tokens_distintivos(ficha["customer"]))
        if propios and propios <= tokens:
            candidatos.append((len(propios), ficha))
    candidatos.sort(key=lambda x: -x[0])
    if candidatos and (len(candidatos) == 1 or candidatos[0][0] > candidatos[1][0]):
        ficha = candidatos[0][1]
        return ficha["customer"], ficha
    return DESCONOCIDO, None


def leer_pistas(texto: str, fecha=None) -> Pistas:
    """Pasada deterministica completa. No usa ningun modelo."""
    ciudad, pais = _ubicacion(texto)
    cliente, ficha = _cliente(texto)
    if ficha:
        # Si conocemos al cliente, su ubicacion de catalogo rellena lo que falte.
        ciudad = ciudad if ciudad != DESCONOCIDO else ficha.get("city", DESCONOCIDO)
        pais = pais if pais != DESCONOCIDO else ficha.get("country", DESCONOCIDO)
    return Pistas(
        customer=cliente,
        ficha_cliente=ficha,
        city=ciudad,
        country=pais,
        conteos=_conteos_por_modalidad(texto),
        marcas=_menciones(texto, N.catalogo_marcas()),
        modelos=_menciones(texto, N.catalogo_modelos()),
        edades=_edades(texto, fecha),
        estado=N.detectar_estado(texto),
    )


# --- 2. capa LLM ------------------------------------------------------------

SISTEMA = """You convert a field engineer's hospital visit note into structured medical
imaging installed-base data. The note may be in Spanish or English. Answer with JSON only.

Modality mapping:
  MR         <- resonador, resonancia, resonancia magnetica, MRI, RM
  CT         <- tomografo, tomografia, TAC, scanner, CT
  Ultrasound <- ecografo, ecografia, ultrasonido, US
  X-Ray      <- rayos X, radiografia, RX
  Patient Monitoring <- monitor de paciente, monitorizacion
  Image Guided Therapy <- angiografo, hemodinamia, cath lab, arco en C

Spanish numerals: un/uno/una=1 dos=2 tres=3 cuatro=4 cinco=5 seis=6 siete=7
ocho=8 nueve=9 diez=10 once=11 doce=12 trece=13.

Rules:
- One array item per (modality, age group). Split one modality into two items ONLY
  when the note says some units are older and others newer.
- quantity: number of units in that group. 0 if the note does not say.
- brand / model: copy ONLY if the note states it. Otherwise "Unknown".
  Never guess a manufacturer. An invented brand is worse than an empty field.
- age_years: only if the note states an age or an installation year. Otherwise 0.
- status: Confirmed if directly counted/verified, Reported if stated plainly,
  Estimated if hedged (parece, unos, quizas, maybe, around, about), else Unknown.
- customer: hospital or clinic name as written. city / country: "Unknown" if absent.
- notes: one short sentence in Spanish with anything useful that does not fit a field."""

EJEMPLO_ENTRADA = (
    "Clinica DemoCare Light tiene dos tomografos Orion Imaging de unos once anos, "
    "y tambien unos cinco ecografos."
)
EJEMPLO_SALIDA = (
    '{"customer":"Clinica DemoCare Light","city":"Unknown","country":"Unknown",'
    '"items":['
    '{"modality":"CT","quantity":2,"brand":"Orion Imaging","model":"Unknown","age_years":11,"status":"Estimated"},'
    '{"modality":"Ultrasound","quantity":5,"brand":"Unknown","model":"Unknown","age_years":0,"status":"Estimated"}'
    '],"notes":"Cantidad de ecografos aproximada."}'
)


def _mensajes(texto: str, pistas: Pistas) -> list[dict]:
    return [
        {"role": "user", "content": EJEMPLO_ENTRADA},
        {"role": "assistant", "content": EJEMPLO_SALIDA},
        {
            "role": "user",
            "content": f"Deterministic parse of this note (trust it): {pistas.resumen()}\n\nNote: {texto}",
        },
    ]


# --- 3. fusion --------------------------------------------------------------


def _anclado(valor: str, texto: str) -> bool:
    """El valor aparece de verdad en el texto original (tolerando acentos y plurales)."""
    if es_desconocido(valor):
        return False
    v, t = N.clave(valor), N.clave(texto)
    if v in t:
        return True
    # Marcas de dos palabras que el colaborador abrevia: "Orion" por "Orion Imaging".
    palabras = [p for p in v.split() if len(p) > 3]
    return bool(palabras) and all(p in t for p in palabras)


def _si_anclado(valor: str, texto: str, normalizar) -> str:
    """Normaliza el valor solo si de verdad se dijo. Si no, DESCONOCIDO."""
    return normalizar(valor) if _anclado(valor, texto) else DESCONOCIDO


def _fusionar(propuesta: dict, pistas: Pistas, texto: str) -> Borrador:
    """Combina la propuesta del LLM con las reglas y descarta lo no anclado."""
    borrador = Borrador()

    # --- cliente y ubicacion: las reglas mandan si reconocieron al cliente.
    if not es_desconocido(pistas.customer):
        borrador.customer = pistas.customer
    else:
        # Se ancla la propuesta *tal cual la dijo el modelo*, antes de
        # normalizarla. Encajar con el catalogo no es prueba de nada: solo dice
        # que el nombre existe, no que aparezca en esta nota. Sin esta
        # comprobacion, un dictado mal entendido acaba archivando los equipos en
        # el hospital equivocado, que es peor que dejarlo vacio y preguntar.
        # Ademas de estar anclado, tiene que ser un nombre: "la clinica de aqui
        # al lado" describe el sitio pero no nombra a nadie, y guardarlo crea una
        # fila que despues no se puede reconciliar con ningun cliente real.
        candidato = str(propuesta.get("customer") or "")
        if _anclado(candidato, texto) and N.es_nombre_identificable(candidato):
            nombre, ficha = N.normalizar_cliente(candidato)
            borrador.customer = nombre
            if ficha:
                pistas.ficha_cliente = ficha
        else:
            borrador.customer = DESCONOCIDO

    # Ciudad y pais tambien se anclan en el texto. Sin esta comprobacion el
    # modelo rellenaba el hueco a su gusto: una nota que solo decia "estoy en el
    # hospital" volvia con ciudad Santander y pais Spain, que nadie menciono.
    borrador.city = (
        pistas.city if not es_desconocido(pistas.city)
        else _si_anclado(str(propuesta.get("city") or ""), texto, N.normalizar_ciudad)
    )
    borrador.country = (
        pistas.country if not es_desconocido(pistas.country)
        else _si_anclado(str(propuesta.get("country") or ""), texto, N.normalizar_pais)
    )
    if pistas.ficha_cliente:
        if es_desconocido(borrador.city):
            borrador.city = pistas.ficha_cliente.get("city", DESCONOCIDO)
        if es_desconocido(borrador.country):
            borrador.country = pistas.ficha_cliente.get("country", DESCONOCIDO)

    # --- equipos
    crudos = propuesta.get("items") or []
    equipos: list[Equipo] = []
    for bruto in crudos:
        if not isinstance(bruto, dict):
            continue
        try:
            modalidad = Modalidad(bruto.get("modality", "Unknown"))
        except ValueError:
            modalidad = Modalidad.UNKNOWN
        if modalidad == Modalidad.UNKNOWN:
            modalidad = N.detectar_modalidad(str(bruto.get("modality", "")))

        marca = N.normalizar_marca(str(bruto.get("brand") or ""))
        if not _anclado(marca, texto):
            marca = DESCONOCIDO  # guardarrail anti-invencion

        modelo = str(bruto.get("model") or DESCONOCIDO).strip()
        if not _anclado(modelo, texto):
            modelo = DESCONOCIDO

        edad = _entero(bruto.get("age_years"))
        if edad and edad not in pistas.edades:
            edad = 0  # una edad que no se menciono en el texto no vale

        try:
            estado = Estado(bruto.get("status", "Unknown"))
        except ValueError:
            estado = Estado.DESCONOCIDO

        equipos.append(
            Equipo(
                modality=modalidad,
                quantity=max(0, _entero(bruto.get("quantity"))),
                brand=marca,
                model=modelo,
                age_years=edad,
                status=estado,
            )
        )

    equipos = _corregir_cantidades(equipos, pistas)
    equipos = _completar_desde_pistas(equipos, pistas, texto)
    equipos = _desambiguar_marcas(equipos, texto)
    equipos = _solo_modalidades_mencionadas(equipos, texto)
    equipos = _unir_gemelos(equipos)

    for eq in equipos:
        if eq.status in (Estado.DESCONOCIDO,):
            eq.status = pistas.estado
    borrador.items = [e for e in equipos if e.modality != Modalidad.UNKNOWN or e.quantity]
    borrador.notes = str(propuesta.get("notes") or "").strip()
    borrador.location_source = "catálogo" if pistas.ficha_cliente else "observación"
    borrador.raw_response = propuesta
    return _validar_grupos(borrador, pistas, texto)


def _posiciones(tokens: list[str], frase: str) -> list[int]:
    """Indices donde empieza `frase` dentro de la lista de tokens."""
    objetivo = N.clave(frase).split()
    if not objetivo:
        return []
    return [
        i for i in range(len(tokens) - len(objetivo) + 1)
        if tokens[i : i + len(objetivo)] == objetivo
    ]


def _posiciones_modalidad(tokens: list[str], modalidad: Modalidad) -> list[int]:
    posiciones: list[int] = []
    for termino in N.terminos_de(modalidad):
        posiciones.extend(_posiciones(tokens, termino))
    return posiciones


def _desambiguar_marcas(equipos: list[Equipo], texto: str) -> list[Equipo]:
    """Una marca mencionada una vez pertenece a una sola modalidad.

    En "un resonador y tres tomografos Zenith MedTech" la marca califica solo a
    los tomografos, pero el modelo tiende a copiarla a los dos grupos. Como la
    palabra si aparece en el texto, el guardarrail de anclaje no la detecta:
    hace falta mirar donde aparece. Se queda con el grupo cuya modalidad esta
    mas cerca de la mencion y se vacia el resto.
    """
    tokens = _tokens(texto)
    por_marca: dict[str, list[Equipo]] = {}
    for eq in equipos:
        if not es_desconocido(eq.brand):
            por_marca.setdefault(N.clave(eq.brand), []).append(eq)

    for marca, grupos in por_marca.items():
        if len(grupos) < 2:
            continue
        menciones = _posiciones(tokens, marca)
        if len(menciones) >= len(grupos):
            continue  # la marca se repite tantas veces como grupos: sin conflicto

        distancias: list[tuple[int, Equipo]] = []
        for eq in grupos:
            posiciones = _posiciones_modalidad(tokens, eq.modality)
            if not posiciones or not menciones:
                distancias.append((10_000, eq))
                continue
            distancias.append((min(abs(p - m) for p in posiciones for m in menciones), eq))

        distancias.sort(key=lambda d: d[0])
        for _, eq in distancias[len(menciones) :]:
            eq.brand = DESCONOCIDO
            eq.model = DESCONOCIDO
    return equipos


def _solo_modalidades_mencionadas(equipos: list[Equipo], texto: str) -> list[Equipo]:
    """Descarta las modalidades que nadie nombró.

    El guardarrail de anclaje cubría marca, modelo, edad y ubicación, pero no la
    modalidad, y el modelo llega a inventarse un equipo entero: de "tres
    ecógrafos y dos ultrasonidos" salía además un CT que no aparece por ningún
    lado. Un equipo fantasma es peor que uno que falta, porque nadie lo
    cuestiona al leer la ficha.
    """
    tokens = _tokens(texto)
    conservados = []
    for eq in equipos:
        if eq.modality == Modalidad.UNKNOWN or _posiciones_modalidad(tokens, eq.modality):
            conservados.append(eq)
    return conservados


def _unir_gemelos(equipos: list[Equipo]) -> list[Equipo]:
    """Junta grupos de la misma modalidad que no se distinguen en nada.

    "Ecógrafo" y "ultrasonido" son la misma cosa, así que "tres ecógrafos y dos
    ultrasonidos" son cinco equipos de una modalidad, no dos flotas distintas.
    Las cantidades se suman porque a estas alturas ya están conciliadas con el
    texto: cada mención aportó su número.

    Solo se unen los indistinguibles. Si difieren en marca, modelo o edad se
    dejan aparte, porque ahí la separación sí significa algo: es la que
    distingue "dos viejos y uno nuevo".
    """
    unidos: list[Equipo] = []
    for eq in equipos:
        gemelo = next(
            (
                u for u in unidos
                if u.modality == eq.modality
                and N.clave(u.brand) == N.clave(eq.brand)
                and N.clave(u.model) == N.clave(eq.model)
                and u.age_years == eq.age_years
            ),
            None,
        )
        if gemelo is None:
            unidos.append(eq)
            continue
        if eq.quantity is not None:
            gemelo.quantity = (gemelo.quantity or 0) + eq.quantity
        if eq.notes and eq.notes not in gemelo.notes:
            gemelo.notes = f"{gemelo.notes} {eq.notes}".strip()
    return unidos


def _entero(valor) -> int:
    try:
        n = int(valor)
    except (TypeError, ValueError):
        return 0
    return n if n >= 0 else 0


def _cantidad_probable(cantidades: list[int]) -> int:
    """Cuantas unidades hay, a partir de las menciones numericas de una modalidad.

    Una sola mencion es el conteo. Varias son casi siempre la misma flota
    mirada dos veces ("dos resonadores... uno de ellos es nuevo"), no dos
    flotas distintas, asi que se toma la mayor y no la suma.
    """
    if not cantidades:
        return 0
    return cantidades[0] if len(cantidades) == 1 else max(cantidades)


def _corregir_cantidades(equipos: list[Equipo], pistas: Pistas) -> list[Equipo]:
    """Concilia las cantidades de las reglas con las del LLM.

    Cuando el texto menciona una modalidad una sola vez, las reglas son
    infalibles y mandan. Cuando la menciona varias veces es ambiguo: "dos
    resonadores... uno de los resonadores parece viejo" son 2 equipos, no 3,
    y solo entendiendo la frase se sabe. Ahi las reglas fijan el rango valido
    [mayor mencion, suma de menciones] y el LLM elige dentro de el.
    """
    for modalidad, cantidades in pistas.conteos.items():
        grupos = [e for e in equipos if e.modality == modalidad]
        if not grupos or not cantidades:
            continue
        piso, techo = max(cantidades), sum(cantidades)
        propuesto = sum(g.quantity or 0 for g in grupos)

        # Mencion repetida: se acepta al LLM si cae en el rango plausible.
        if len(cantidades) > 1 and piso <= propuesto <= techo:
            continue
        objetivo = _cantidad_probable(cantidades)

        if len(grupos) == 1:
            grupos[0].quantity = objetivo
            continue
        if propuesto == objetivo:
            continue

        # El LLM partio la modalidad en grupos. Se cuadra el total con el que
        # dicta el texto, sin inventar ni perder unidades.
        if propuesto < objetivo:
            faltan = [g for g in grupos if (g.quantity or 0) <= 0] or grupos[-1:]
            base, extra = divmod(objetivo - propuesto, len(faltan))
            for i, g in enumerate(faltan):
                g.quantity = (g.quantity or 0) + base + (1 if i < extra else 0)
        else:
            # Se pasa: pasa al partir "tres resonadores, dos viejos y uno nuevo"
            # en 3 + 1. El grupo mayor es el que suele arrastrar el total.
            sobra = propuesto - objetivo
            for g in sorted(grupos, key=lambda x: -(x.quantity or 0)):
                quita = min(sobra, max(0, (g.quantity or 0) - 1))
                g.quantity -= quita
                sobra -= quita
                if sobra <= 0:
                    break
    return equipos


def _completar_desde_pistas(equipos: list[Equipo], pistas: Pistas, texto: str) -> list[Equipo]:
    """Anade modalidades que las reglas vieron y el LLM se salto."""
    presentes = {e.modality for e in equipos}
    for modalidad, cantidades in pistas.conteos.items():
        if modalidad in presentes:
            continue
        equipos.append(
            Equipo(
                modality=modalidad,
                quantity=_cantidad_probable(cantidades),
                status=pistas.estado,
            )
        )

    # Marca, modelo y edad sueltos solo se pueden atribuir sin ambiguedad cuando
    # hay un unico grupo de equipos. Con dos modalidades en la frase, "de unos
    # ocho anos" puede referirse a cualquiera: se deja vacio y se pregunta.
    if len(equipos) == 1:
        unico = equipos[0]
        if len(pistas.marcas) == 1 and es_desconocido(unico.brand):
            unico.brand = pistas.marcas[0]
        if len(pistas.modelos) == 1 and es_desconocido(unico.model):
            unico.model = pistas.modelos[0]
        if len(pistas.edades) == 1 and (unico.age_years or 0) <= 0:
            unico.age_years = pistas.edades[0]
    return equipos


# --- API del modulo ---------------------------------------------------------


def extraer(texto: str, motor=None, estricto: bool = False, fecha=None) -> Borrador:
    """Convierte una nota de campo en un borrador estructurado.

    Si el motor QVAC no esta disponible, las reglas solas ya producen un
    borrador util: la app degrada, no se cae. Lo que nunca hace es salir a la
    nube a por inferencia.
    """
    texto = (texto or "").strip()
    if not texto:
        return Borrador()

    if estricto and (motor is None or not motor.estado.listo):
        raise RuntimeError("QVAC no está listo; no se ejecutó inferencia.")
    pistas = leer_pistas(texto, fecha)
    propuesta: dict = {}
    if motor is not None and motor.estado.listo:
        from .schema import ESQUEMA_EXTRACCION

        try:
            propuesta = motor.json_estructurado(
                sistema=SISTEMA,
                mensajes=_mensajes(texto, pistas),
                esquema=ESQUEMA_EXTRACCION,
                nombre="observacion",
                max_tokens=900,
            )
        except Exception:
            if estricto:
                raise
            propuesta = {}
    borrador = _fusionar(propuesta, pistas, texto)
    borrador.inference = "QVAC" if propuesta else "reglas"
    borrador.model_id = getattr(motor.estado, "llm_model", "") if motor is not None and propuesta else ""
    return borrador


def extraer_solo_reglas(texto: str) -> Borrador:
    """Version sin modelo, util para tests y para comparar en la demo."""
    return _fusionar({}, leer_pistas(texto), texto)


def _validar_grupos(borrador: Borrador, pistas: Pistas, texto: str) -> Borrador:
    """Separate total and explicitly quantified age subsets; never propagate a partial age."""
    normal = N.sin_acentos(texto).lower()
    for modalidad, conteos in pistas.conteos.items():
        grupos = [e for e in borrador.items if e.modality == modalidad]
        if not grupos:
            continue
        terminos = "|".join(re.escape(t) for t in sorted(N.terminos_de(modalidad), key=len, reverse=True))
        patron = rf"\b(\w+) de (?:los |las )?(?:{terminos})\b[^.;]*?\b(\w+) (?:anos?|years?)\b"
        detalle = re.search(patron, normal)
        if detalle and conteos:
            cantidad, edad = N.a_numero(detalle[1]), N.a_numero(detalle[2])
            total = max(conteos)
            if cantidad is not None and edad is not None and 0 < cantidad <= total and 0 <= edad <= 40:
                borrador.items = [e for e in borrador.items if e.modality != modalidad]
                borrador.items.append(Equipo(modality=modalidad, quantity=cantidad, age_years=edad,
                    status=N.detectar_estado(detalle[0])))
                if total > cantidad:
                    borrador.items.append(Equipo(modality=modalidad, quantity=total-cantidad,
                        status=Estado.REPORTADO))
                continue
        # The status describes this modality's clause, not uncertainty elsewhere in the note.
        clausulas = re.split(r"[.;]|\by\b|\band\b", texto, flags=re.I)
        propias = [c for c in clausulas if N.detectar_modalidad(c) == modalidad]
        if len(propias) == 1 and len(grupos) == 1:
            grupos[0].status = N.detectar_estado(propias[0])
    for eq in borrador.items:
        if eq.quantity == 0:
            eq.quantity = 0 if 0 in pistas.conteos.get(eq.modality, []) else None
        if eq.age_years == 0 or eq.age_years is None:
            clausulas = re.split(r"[.;]|\by\b|\band\b", texto, flags=re.I)
            explicito = any(N.detectar_modalidad(c) == eq.modality and
                re.search(r"\b(?:0|cero|zero) (?:anos?|years?)\b", N.clave(c)) for c in clausulas)
            eq.age_years = 0 if explicito and sum(e.modality == eq.modality for e in borrador.items) == 1 else None
    return borrador
