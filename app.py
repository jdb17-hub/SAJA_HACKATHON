"""Base Instalada QVAC - captura conversacional de equipamiento medico.

Interfaz Streamlit. Toda la inferencia (extraccion, seguimiento, consultas y
transcripcion de voz) corre en este dispositivo a traves de QVAC.
"""
from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from src import dedup, followup, nlquery, store
from src.config import DIAS_SIN_VERIFICAR, EDAD_RENOVACION
from src.confidence import alertas, desglose, dias_desde, es_oportunidad_renovacion, sin_verificar
from src.extract import extraer
from src.qvac_engine import obtener_motor
from src.schema import ETIQUETA_ES, Borrador, Equipo, Estado, Modalidad, Observacion, es_desconocido

st.set_page_config(page_title="Base Instalada QVAC", page_icon="🏥", layout="wide")

COLOR_MODALIDAD = {
    "MR": "#3b6fd4", "CT": "#2e9e83", "Ultrasound": "#c8863c",
    "X-Ray": "#8a5cc4", "Patient Monitoring": "#4a8fb5", "Image Guided Therapy": "#b5514a",
}


# --- recursos compartidos ---------------------------------------------------


@st.cache_resource(show_spinner=False)
def motor_qvac():
    """Una sola instancia por proceso: el modelo se carga una vez, no por rerun."""
    return obtener_motor()


@st.cache_resource(show_spinner=False)
def sembrar_una_vez() -> int:
    return store.sembrar()


def estado_inicial() -> None:
    ss = st.session_state
    ss.setdefault("borrador", None)
    ss.setdefault("texto_original", "")
    ss.setdefault("fuente", "Text")
    ss.setdefault("omitidas", set())
    ss.setdefault("conversacion", [])
    ss.setdefault("observador", "Field User 01")
    ss.setdefault("pregunta_actual", None)
    ss.setdefault("ultimo_guardado", None)
    ss.setdefault("revision", 0)


def df_observaciones() -> pd.DataFrame:
    filas = store.todas()
    if not filas:
        return pd.DataFrame()
    df = pd.DataFrame(filas)
    df["antiguedad_dias"] = [dias_desde(f) for f in filas]
    df["oportunidad"] = [es_oportunidad_renovacion(f) for f in filas]
    df["sin_verificar"] = [sin_verificar(f) for f in filas]
    return df


# --- barra lateral ----------------------------------------------------------


def barra_lateral(motor) -> None:
    with st.sidebar:
        st.markdown("### 🏥 Base Instalada QVAC")
        st.caption("Inteligencia de base instalada, capturada como una conversacion.")

        estado = motor.estado
        if estado.listo:
            st.success(f"QVAC activo · {estado.llm_model}")
        elif estado.error:
            st.error("QVAC no disponible")
        else:
            st.warning("QVAC sin iniciar")

        st.markdown("🔒 **Inferencia 100% en el dispositivo**")
        st.caption("Ninguna nota de cliente sale de este equipo.")

        if not estado.listo:
            if st.button("Iniciar motor QVAC", type="primary", width="stretch"):
                with st.spinner("Cargando modelo en el dispositivo..."):
                    motor.iniciar()
                st.rerun()

        st.divider()
        st.session_state.observador = st.text_input("Colaborador", st.session_state.observador)
        st.caption(f"{store.contar()} observaciones en la base")


# --- pagina: capturar -------------------------------------------------------


def pagina_capturar(motor) -> None:
    st.header("Capturar observacion")
    st.caption(
        "Cuenta lo que viste como se lo contarias a un companero. "
        "El agente extrae los datos, pregunta lo que falte y avisa si ya estaba registrado."
    )

    if st.session_state.borrador is None:
        _entrada_nueva(motor)
    else:
        _revisar_borrador(motor)


def _entrada_nueva(motor) -> None:
    pestana_texto, pestana_voz = st.tabs(["✍️ Escribir", "🎙️ Dictar"])

    with pestana_texto:
        with st.form("form_texto"):
            texto = st.text_area(
                "Que observaste en la visita?",
                height=130,
                placeholder="Estoy en Hospital DemoCare Pacific, en Panama. Tienen dos resonadores "
                "y un tomografo. Uno de los resonadores parece de unos ocho anos.",
            )
            enviado = st.form_submit_button("Extraer datos", type="primary")
        if enviado and texto.strip():
            _procesar(texto, "Text", motor)

        st.caption("Ejemplos del set de pruebas del reto:")
        ejemplos = [
            "Estoy en Hospital DemoCare Pacific, en Panama. Tienen dos resonadores y un tomografo. "
            "Uno de los resonadores parece de unos ocho anos.",
            "En Hospital DemoCare Horizon vi tres resonadores. Dos se ven viejos y uno mucho mas nuevo.",
            "Clinica DemoCare Light tiene dos tomografos Orion Imaging de unos once anos.",
            "Centro Medico DemoCare Valley tiene un MR y dos CTs. No se las marcas.",
        ]
        for i, ejemplo in enumerate(ejemplos):
            if st.button(ejemplo[:78] + "...", key=f"ej{i}", width="stretch"):
                _procesar(ejemplo, "Text", motor)

    with pestana_voz:
        if not motor.estado.listo:
            st.info("Inicia el motor QVAC en la barra lateral para poder dictar.")
        audio = st.audio_input("Graba tu nota de voz al salir de la visita")
        if audio is not None and st.button("Transcribir y extraer", type="primary"):
            _procesar_audio(audio, motor)


def _procesar_audio(audio, motor) -> None:
    from src.audio import duracion_segundos, guardar_audio
    from src.config import STT_PROMPT

    if not motor.estado.listo:
        st.warning("Inicia el motor QVAC en la barra lateral antes de dictar.")
        return

    datos = audio.getvalue()
    ruta = None
    with st.spinner("Transcribiendo en el dispositivo (Whisper)..."):
        try:
            # Se guarda tal cual: QVAC decodifica el formato del navegador.
            ruta = guardar_audio(datos, Path(tempfile.gettempdir()) / "qvac_nota")
            duracion = duracion_segundos(ruta)
            if 0 < duracion < 0.6:
                st.warning("La grabacion es demasiado corta. Habla un par de segundos mas.")
                return
            texto = motor.transcribir(ruta, prompt=STT_PROMPT)
        except Exception as exc:  # noqa: BLE001
            # Se adjunta que llego exactamente: sin esto, un fallo de dictado es
            # imposible de diagnosticar sin reproducirlo.
            st.error(f"No se pudo transcribir: {type(exc).__name__}: {exc}")
            st.caption(
                f"Diagnostico — {len(datos)} bytes, cabecera {datos[:4]!r}, "
                f"fichero {ruta.name if ruta else 'no escrito'}, "
                f"modelo {motor.estado.stt_model}"
            )
            return

    if not texto:
        st.warning(
            "Whisper no encontro voz en la grabacion. Prueba a hablar mas cerca del microfono."
        )
        st.caption(f"Diagnostico — {len(datos)} bytes, {duracion:.1f}s de audio, {ruta.suffix}")
        return

    _procesar(texto, "Voice", motor)


def _procesar(texto: str, fuente: str, motor) -> None:
    with st.spinner("Extrayendo datos en el dispositivo..."):
        borrador = extraer(texto, motor if motor.estado.listo else None)
    st.session_state.borrador = borrador
    st.session_state.texto_original = texto
    st.session_state.fuente = fuente
    st.session_state.omitidas = set()
    st.session_state.conversacion = [("colaborador", texto)]
    st.session_state.pregunta_actual = followup.siguiente_pregunta(borrador, set())
    st.session_state.revision += 1
    st.rerun()


def _revisar_borrador(motor) -> None:
    borrador: Borrador = st.session_state.borrador

    es_voz = st.session_state.fuente == "Voice"
    st.markdown("#### Transcripcion" if es_voz else "#### Nota original")

    if es_voz:
        # Whisper se equivoca con nombres propios, y una palabra mal oida
        # arrastra toda la extraccion. Poder corregirla y reprocesar evita
        # tener que repetir la grabacion entera.
        st.caption("Si el dictado se entendio mal, corrigelo aqui y vuelve a extraer.")
        corregida = st.text_area(
            "Transcripcion", st.session_state.texto_original, height=90,
            label_visibility="collapsed", key=f"trans{st.session_state.revision}",
        )
        if corregida.strip() and corregida != st.session_state.texto_original:
            if st.button("Volver a extraer con el texto corregido", type="primary"):
                _procesar(corregida, "Voice", motor)
    else:
        st.info(st.session_state.texto_original)

    izquierda, derecha = st.columns([3, 2])

    with izquierda:
        st.markdown("#### Datos extraidos")
        _editor_borrador(borrador)

    with derecha:
        _panel_seguimiento(borrador, motor)

    st.divider()
    _panel_guardado(borrador, motor)


def _estado_cliente(nombre: str) -> None:
    """Dice de donde sale el cliente, para no tener que fiarse a ciegas.

    Importa la diferencia: uno del catalogo hereda ciudad y pais conocidos y
    entra en la deteccion de duplicados; uno nuevo abre ficha y conviene
    revisar como esta escrito antes de guardarlo.
    """
    from src.normalize import normalizar_cliente

    if es_desconocido(nombre):
        st.caption("⚠️ Sin cliente. No se entendio el nombre, asi que no se ha rellenado nada.")
        return
    _, ficha = normalizar_cliente(nombre)
    if ficha:
        st.caption(f"✓ Cliente conocido — {ficha.get('city')}, {ficha.get('country')}")
    else:
        st.caption("🆕 Cliente nuevo: no estaba en la base. Revisa el nombre antes de guardar.")


def _editor_borrador(borrador: Borrador) -> None:
    # Streamlit conserva el valor de un widget mientras su `key` no cambie, y ese
    # valor guardado gana al argumento por defecto. Sin versionar las claves, el
    # editor volveria a escribir el valor viejo encima de lo que el agente acaba
    # de completar en el borrador. La revision sube con cada cambio programatico.
    rev = st.session_state.revision

    c1, c2, c3 = st.columns(3)
    borrador.customer = c1.text_input("Cliente", borrador.customer, key=f"cli{rev}")
    borrador.city = c2.text_input("Ciudad", borrador.city, key=f"ciu{rev}")
    borrador.country = c3.text_input("Pais", borrador.country, key=f"pai{rev}")
    _estado_cliente(borrador.customer)

    for i, equipo in enumerate(borrador.items):
        with st.container(border=True):
            f1, f2, f3 = st.columns([2, 1, 2])
            modalidades = [m.value for m in Modalidad]
            equipo.modality = Modalidad(
                f1.selectbox(
                    "Modalidad", modalidades, modalidades.index(equipo.modality.value),
                    key=f"m{rev}_{i}",
                )
            )
            equipo.quantity = f2.number_input("Cantidad", 0, 200, equipo.quantity, key=f"q{rev}_{i}")
            equipo.brand = f3.text_input("Marca", equipo.brand, key=f"b{rev}_{i}")

            g1, g2, g3 = st.columns([2, 1, 2])
            equipo.model = g1.text_input("Modelo", equipo.model, key=f"mo{rev}_{i}")
            equipo.age_years = g2.number_input("Edad (anos)", 0, 40, equipo.age_years, key=f"e{rev}_{i}")
            estados = [e.value for e in Estado]
            equipo.status = Estado(
                g3.selectbox(
                    "Estado", estados, estados.index(equipo.status.value),
                    format_func=lambda v: ETIQUETA_ES.get(v, v), key=f"s{rev}_{i}",
                )
            )
            if st.button("Quitar este grupo", key=f"del{rev}_{i}"):
                borrador.items.pop(i)
                _tocar_borrador()

    if st.button("Anadir otro tipo de equipo"):
        borrador.items.append(Equipo())
        _tocar_borrador()

    borrador.notes = st.text_area("Notas", borrador.notes, height=70, key=f"nota{rev}")


def _tocar_borrador() -> None:
    """Marca el borrador como cambiado por codigo y refresca la interfaz."""
    st.session_state.revision += 1
    st.rerun()


def _panel_seguimiento(borrador: Borrador, motor) -> None:
    st.markdown("#### El agente pregunta")
    pendientes = followup.pendientes(borrador, st.session_state.omitidas)

    if not pendientes:
        st.success("No falta ningun dato relevante.")
        st.caption(followup.resumen(borrador))
        return

    pregunta = pendientes[0]
    st.caption(f"Quedan {len(pendientes)} datos por completar. Se pregunta primero el mas valioso.")

    with st.container(border=True):
        st.markdown(f"**{pregunta.texto}**")
        with st.form(f"form_seg_{pregunta.clave}", clear_on_submit=True):
            respuesta = st.text_input("Tu respuesta", label_visibility="collapsed",
                                      placeholder="Escribe aqui, o pulsa 'No lo se'")
            c1, c2 = st.columns(2)
            responder = c1.form_submit_button("Responder", type="primary", width="stretch")
            omitir = c2.form_submit_button("No lo se", width="stretch")

        if responder and respuesta.strip():
            nuevo, ok = followup.aplicar_respuesta(borrador, pregunta, respuesta, motor)
            st.session_state.borrador = nuevo
            st.session_state.conversacion.append(("agente", pregunta.texto))
            st.session_state.conversacion.append(("colaborador", respuesta))
            if not ok:
                # No se pudo interpretar: se marca omitida para no repetirla.
                st.session_state.omitidas.add(pregunta.clave)
            _tocar_borrador()
        if omitir:
            st.session_state.omitidas.add(pregunta.clave)
            st.session_state.conversacion.append(("agente", pregunta.texto))
            st.session_state.conversacion.append(("colaborador", "No lo se"))
            _tocar_borrador()

    if len(pendientes) > 1:
        with st.expander(f"Otras {len(pendientes) - 1} preguntas pendientes"):
            for p in pendientes[1:8]:
                st.write(f"- {p.texto}  ·  valor {p.valor:.0f}")

    if len(st.session_state.conversacion) > 1:
        with st.expander("Conversacion"):
            for quien, mensaje in st.session_state.conversacion:
                st.chat_message("user" if quien == "colaborador" else "assistant").write(mensaje)


def _panel_guardado(borrador: Borrador, motor) -> None:
    st.markdown("#### Confirmar y guardar")

    if not borrador.items:
        # Puede pasar tras resolver duplicados: cada grupo se aplico a una fila
        # que ya existia, asi que no queda nada nuevo que archivar.
        st.success("Listo. Toda la observacion se aplico sobre filas existentes.")
        if st.button("Capturar otra visita", type="primary"):
            _reiniciar()
        return

    st.info(followup.resumen(borrador))

    if es_desconocido(borrador.customer):
        st.error("Falta el cliente: sin el, la observacion no se puede archivar.")
        if st.button("Descartar y empezar de nuevo"):
            _reiniciar()
        return

    existentes = store.por_cliente(borrador.customer)
    hay_duplicados = False
    for i, equipo in enumerate(borrador.items):
        candidatos = dedup.buscar_duplicados(equipo, borrador.customer, existentes)
        if not candidatos:
            continue
        hay_duplicados = True
        mejor = candidatos[0]
        with st.container(border=True):
            st.warning(
                f"**Posible duplicado** — {equipo.modality.value} x{equipo.quantity} "
                f"se parece a la observacion #{mejor['observation_id']} "
                f"({mejor['modality']} x{mejor['quantity']}, {mejor['brand']}, "
                f"{mejor['observer']}, {mejor['visit_date']}) · similitud {mejor['_score']}"
            )
            st.caption("Coincide en: " + ", ".join(mejor["_motivos"]))
            st.caption(dedup.DESCRIPCION_ACCION[mejor["_accion"]])
            c1, c2, c3 = st.columns(3)
            if c1.button("Confirmar la existente", key=f"conf{i}"):
                dedup.confirmar(mejor["observation_id"], st.session_state.observador)
                borrador.items.pop(i)
                _tocar_borrador()
            if c2.button("Completar la existente", key=f"enr{i}"):
                dedup.enriquecer(mejor["observation_id"], equipo, st.session_state.observador)
                borrador.items.pop(i)
                _tocar_borrador()
            c3.caption("O guarda igualmente como observacion nueva mas abajo.")

    c1, c2 = st.columns([1, 3])
    etiqueta = "Guardar de todas formas" if hay_duplicados else "Guardar observacion"
    if c1.button(etiqueta, type="primary", width="stretch"):
        ids = _guardar(borrador)
        st.session_state.ultimo_guardado = ids
        _reiniciar()
    if c2.button("Descartar"):
        _reiniciar()


def _guardar(borrador: Borrador) -> list[int]:
    ids = []
    for equipo in borrador.items:
        obs = Observacion(
            country=borrador.country,
            city=borrador.city,
            customer=borrador.customer,
            observer=st.session_state.observador,
            visit_date=date.today().isoformat(),
            modality=equipo.modality.value,
            quantity=equipo.quantity,
            brand=equipo.brand,
            model=equipo.model,
            age_years=equipo.age_years,
            status=equipo.status.value,
            source=st.session_state.fuente,
            raw_input=st.session_state.texto_original,
            notes=" | ".join(p for p in [borrador.notes, equipo.notes] if p),
        )
        ids.append(store.guardar(obs))
    store.recalcular_confianza()
    return ids


def _reiniciar() -> None:
    for k in ["borrador", "texto_original", "omitidas", "conversacion", "pregunta_actual"]:
        st.session_state.pop(k, None)
    st.rerun()


# --- pagina: cliente --------------------------------------------------------


def pagina_cliente() -> None:
    st.header("Base instalada por cliente")
    df = df_observaciones()
    if df.empty:
        st.info("Todavia no hay observaciones. Captura una en la pestana anterior.")
        return

    clientes = sorted(df["customer"].unique())
    cliente = st.selectbox("Cliente", clientes)
    sub = df[df["customer"] == cliente].copy()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Unidades registradas", int(sub["quantity"].sum()))
    c2.metric("Modalidades", sub["modality"].nunique())
    edades = sub.loc[sub["age_years"] > 0, "age_years"]
    c3.metric("Edad media", f"{edades.mean():.1f} anos" if len(edades) else "sin datos")
    c4.metric("Confianza media", f"{sub['confidence_score'].mean():.0f}/100")

    ciudad = sub["city"].iloc[0]
    pais = sub["country"].iloc[0]
    st.caption(f"{ciudad}, {pais} · ultima visita {sub['visit_date'].max()}")

    st.markdown("#### Equipos")
    for _, fila in sub.sort_values("modality").iterrows():
        avisos = alertas(fila.to_dict())
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 2, 2])
            c1.markdown(
                f"**{ETIQUETA_ES.get(fila['modality'], fila['modality'])}** × {fila['quantity']}  \n"
                f"{fila['brand']} · {fila['model']}"
            )
            edad = f"{fila['age_years']} anos" if fila["age_years"] else "edad desconocida"
            instal = f" (aprox. {fila['install_year']})" if fila["install_year"] else ""
            c2.markdown(
                f"{edad}{instal}  \n"
                f"{ETIQUETA_ES.get(fila['status'], fila['status'])} · "
                f"observado por {fila['observer']}"
            )
            c3.markdown(
                f"Confianza **{fila['confidence_score']}/100** "
                f"({ETIQUETA_ES.get(fila['confidence'], fila['confidence'])})  \n"
                f"visto el {fila['visit_date']}"
            )
            if avisos:
                st.caption("⚠️ " + " · ".join(avisos))
            with st.expander("De donde sale este dato"):
                st.write(f"**Nota original:** {fila['raw_input'] or '(no registrada)'}")
                if fila["notes"]:
                    st.write(f"**Notas:** {fila['notes']}")
                st.write("**Puntaje de confianza:**")
                st.table(pd.DataFrame([desglose(fila.to_dict(), store.todas())]))


# --- pagina: panorama -------------------------------------------------------


def pagina_panorama() -> None:
    st.header("Panorama entre clientes")
    df = df_observaciones()
    if df.empty:
        st.info("Todavia no hay observaciones.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Clientes", df["customer"].nunique())
    c2.metric("Unidades", int(df["quantity"].sum()))
    c3.metric("Paises", df["country"].nunique())
    oportunidades = df[df["oportunidad"]]
    c4.metric(f"Equipos de {EDAD_RENOVACION}+ anos", int(oportunidades["quantity"].sum()))

    st.markdown("#### Unidades por pais y modalidad")
    por_pais = df.groupby(["country", "modality"], as_index=False)["quantity"].sum()
    grafico = (
        alt.Chart(por_pais)
        .mark_bar()
        .encode(
            x=alt.X("quantity:Q", title="Unidades"),
            y=alt.Y("country:N", title=None, sort="-x"),
            color=alt.Color(
                "modality:N", title="Modalidad",
                scale=alt.Scale(domain=list(COLOR_MODALIDAD), range=list(COLOR_MODALIDAD.values())),
            ),
            tooltip=["country", "modality", "quantity"],
        )
        .properties(height=max(220, 34 * df["country"].nunique()))
    )
    st.altair_chart(grafico, width="stretch")

    izq, der = st.columns(2)
    with izq:
        st.markdown("#### Antiguedad de la flota")
        con_edad = df[df["age_years"] > 0]
        if not con_edad.empty:
            hist = (
                alt.Chart(con_edad)
                .mark_bar()
                .encode(
                    x=alt.X("age_years:Q", bin=alt.Bin(step=2), title="Edad (anos)"),
                    y=alt.Y("sum(quantity):Q", title="Unidades"),
                    color=alt.Color(
                        "modality:N", legend=None,
                        scale=alt.Scale(domain=list(COLOR_MODALIDAD), range=list(COLOR_MODALIDAD.values())),
                    ),
                    tooltip=["modality", "sum(quantity)"],
                )
                .properties(height=260)
            )
            st.altair_chart(hist, width="stretch")
        else:
            st.caption("Sin edades registradas todavia.")

    with der:
        st.markdown("#### Marcas instaladas")
        marcas = df[df["brand"] != "Unknown"].groupby("brand", as_index=False)["quantity"].sum()
        if not marcas.empty:
            st.altair_chart(
                alt.Chart(marcas).mark_bar(color="#3b6fd4").encode(
                    x=alt.X("quantity:Q", title="Unidades"),
                    y=alt.Y("brand:N", title=None, sort="-x"),
                    tooltip=["brand", "quantity"],
                ).properties(height=260),
                width="stretch",
            )
        else:
            st.caption("Sin marcas registradas todavia.")

    st.markdown(f"#### Oportunidades de renovacion (equipos de {EDAD_RENOVACION}+ anos)")
    if oportunidades.empty:
        st.caption("Ninguna por ahora.")
    else:
        vista = oportunidades.sort_values("age_years", ascending=False)[
            ["customer", "country", "modality", "quantity", "brand", "age_years", "confidence_score"]
        ].rename(columns={
            "customer": "Cliente", "country": "Pais", "modality": "Modalidad",
            "quantity": "Unidades", "brand": "Marca", "age_years": "Edad",
            "confidence_score": "Confianza",
        })
        st.dataframe(vista, width="stretch", hide_index=True)

    st.markdown(f"#### Datos sin verificar en {DIAS_SIN_VERIFICAR}+ dias")
    viejas = df[df["sin_verificar"]]
    if viejas.empty:
        st.caption("Toda la base esta razonablemente fresca.")
    else:
        vista = viejas.sort_values("antiguedad_dias", ascending=False)[
            ["customer", "modality", "quantity", "observer", "visit_date", "antiguedad_dias"]
        ].rename(columns={
            "customer": "Cliente", "modality": "Modalidad", "quantity": "Unidades",
            "observer": "Observador", "visit_date": "Ultima visita", "antiguedad_dias": "Dias",
        })
        st.dataframe(vista, width="stretch", hide_index=True)

    st.divider()
    st.markdown("#### Dataset completo")
    st.dataframe(df.drop(columns=["oportunidad", "sin_verificar"]), width="stretch", hide_index=True)
    ruta = Path(tempfile.gettempdir()) / "base_instalada.csv"
    store.exportar_csv(ruta)
    st.download_button("Descargar CSV", ruta.read_bytes(), "base_instalada.csv", "text/csv")


# --- pagina: preguntar ------------------------------------------------------


def pagina_preguntar(motor) -> None:
    st.header("Preguntar a la base instalada")
    st.caption("En lenguaje natural. El modelo traduce la pregunta a un filtro; los numeros salen de los datos.")

    ejemplos = [
        "clientes en Brasil con resonadores de mas de siete anos",
        "donde hay oportunidades de renovacion",
        "cuantos ecografos hay en Mexico",
        "que equipos llevan mas de medio ano sin verificar",
    ]
    cols = st.columns(len(ejemplos))
    for col, ejemplo in zip(cols, ejemplos):
        if col.button(ejemplo, width="stretch"):
            st.session_state["consulta"] = ejemplo

    pregunta = st.text_input("Tu pregunta", st.session_state.get("consulta", ""))
    if not pregunta.strip():
        return

    with st.spinner("Interpretando en el dispositivo..."):
        filtro = nlquery.interpretar(pregunta, motor if motor.estado.listo else None)
    resultados = nlquery.aplicar(filtro, store.todas())

    st.caption(f"Filtro entendido: **{nlquery.describir_filtro(filtro)}**")
    st.success(nlquery.resumir(pregunta, resultados, motor))

    if resultados:
        vista = pd.DataFrame(resultados)[
            ["customer", "country", "city", "modality", "quantity", "brand", "age_years",
             "status", "confidence_score", "visit_date"]
        ].rename(columns={
            "customer": "Cliente", "country": "Pais", "city": "Ciudad", "modality": "Modalidad",
            "quantity": "Unidades", "brand": "Marca", "age_years": "Edad", "status": "Estado",
            "confidence_score": "Confianza", "visit_date": "Visita",
        })
        st.dataframe(vista, width="stretch", hide_index=True)

        if filtro.get("agrupar_por"):
            columna = {"cliente": "customer", "pais": "country", "modalidad": "modality", "marca": "brand"}[
                filtro["agrupar_por"]
            ]
            agrupado = pd.DataFrame(resultados).groupby(columna, as_index=False)["quantity"].sum()
            st.altair_chart(
                alt.Chart(agrupado).mark_bar(color="#2e9e83").encode(
                    x=alt.X("quantity:Q", title="Unidades"),
                    y=alt.Y(f"{columna}:N", title=None, sort="-x"),
                ).properties(height=max(180, 32 * len(agrupado))),
                width="stretch",
            )


# --- pagina: sistema --------------------------------------------------------


def pagina_sistema(motor) -> None:
    st.header("Motor y cumplimiento")
    estado = motor.estado

    c1, c2, c3 = st.columns(3)
    c1.metric("Estado", "Activo" if estado.listo else "Detenido")
    c2.metric("Inferencias", estado.inferencias)
    c3.metric("Ultima latencia", f"{estado.ultima_latencia:.2f} s" if estado.ultima_latencia else "-")

    st.markdown("#### Regla tecnica del reto")
    st.markdown(
        "> *La inferencia corre en el dispositivo o entre pares. Nunca en la nube.*\n\n"
        "Como se cumple aqui:"
    )
    st.markdown(
        f"""
- **SDK**: `tetherto-qvac-sdk` (Python), que arranca un worker local Bare.
- **Modelo de lenguaje**: `{estado.llm_model}`, un GGUF cargado desde disco.
- **Modelo de voz**: `{estado.stt_model}` (Whisper), tambien local.
- **Extraccion, seguimiento, consultas y transcripcion**: todo pasa por `src/qvac_engine.py`,
  el unico punto del codigo que ejecuta inferencia.
- **Sin claves de API**: no hay ninguna credencial de proveedor en el proyecto.
- **Red**: solo la descarga inicial del fichero del modelo. Despues la app funciona
  en modo avion; puedes desconectar el wifi y seguir capturando.
"""
    )

    st.markdown("#### Modelos en cache local")
    if estado.ficheros_modelo:
        for fichero in estado.ficheros_modelo:
            st.code(fichero, language=None)
        st.caption("Directorio: ~/.qvac/models")
    else:
        st.caption("Inicia el motor para ver los modelos cargados.")

    if estado.sdk_dir:
        st.caption(f"Worker QVAC: {estado.sdk_dir}")
    if estado.segundos_carga:
        st.caption(f"Carga del modelo: {estado.segundos_carga:.1f} s")
    if estado.error:
        st.error(estado.error)

    st.divider()
    st.markdown("#### Datos")
    c1, c2 = st.columns(2)
    if c1.button("Recargar semilla del Excel (borra lo capturado)"):
        store.sembrar(forzar=True)
        st.success("Semilla recargada.")
        st.rerun()
    if c2.button("Recalcular confianza"):
        store.recalcular_confianza()
        st.success("Confianza recalculada.")


# --- main -------------------------------------------------------------------


def main() -> None:
    estado_inicial()
    sembrar_una_vez()
    motor = motor_qvac()
    barra_lateral(motor)

    if st.session_state.ultimo_guardado:
        st.toast(f"Guardadas {len(st.session_state.ultimo_guardado)} observaciones")
        st.session_state.ultimo_guardado = None

    capturar, cliente, panorama, preguntar, sistema = st.tabs(
        ["🎙️ Capturar", "🏥 Cliente", "🌎 Panorama", "💬 Preguntar", "⚙️ Motor"]
    )
    with capturar:
        pagina_capturar(motor)
    with cliente:
        pagina_cliente()
    with panorama:
        pagina_panorama()
    with preguntar:
        pagina_preguntar(motor)
    with sistema:
        pagina_sistema(motor)


if __name__ == "__main__":
    main()
