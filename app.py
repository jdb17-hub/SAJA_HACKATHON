"""Base Instalada QVAC - captura conversacional de equipamiento medico.

Interfaz Streamlit. Toda la inferencia (extraccion, seguimiento, consultas y
transcripción de voz) corre en este dispositivo a traves de QVAC.
"""
from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from src import capture, conflicts, documents, followup, geo, insights, nlquery, store
from src.config import DIAS_SIN_VERIFICAR, EDAD_RENOVACION
from src.confidence import alertas, desglose, dias_desde, es_oportunidad_renovacion, sin_verificar
from src.extract import extraer
from src.qvac_engine import obtener_motor
from src.schema import (
    DESCONOCIDO,
    NO_IDENTIFICADO,
    Borrador,
    Equipo,
    Estado,
    Modalidad,
    Observacion,
    es_desconocido,
    etiqueta,
)

st.set_page_config(page_title="Base Instalada QVAC", layout="wide")

COLOR_MODALIDAD = {
    "MR": "#3b6fd4", "CT": "#2e9e83", "Ultrasound": "#c8863c",
    "X-Ray": "#8a5cc4", "Patient Monitoring": "#4a8fb5", "Image Guided Therapy": "#b5514a",
}


# --- recursos compartidos ---------------------------------------------------


@st.cache_resource(show_spinner=False)
def motor_qvac():
    """Una sola instancia por proceso: el modelo se carga una vez, no por rerun."""
    motor = obtener_motor()
    motor.iniciar_en_segundo_plano()
    return motor


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
    ss.setdefault("visit_id", None)
    ss.setdefault("visit_version", None)
    ss.setdefault("visit_date", date.today())
    ss.setdefault("inicio_captura", None)
    ss.setdefault("segundos_captura", None)


# Columnas cuyo hueco significa "no se pudo determinar". Se traducen al pintar.
# Las de texto libre (notas, nota original) quedan fuera a proposito: vacias
# significan que no habia nada que anadir, no que falte un dato.
COLUMNAS_TRADUCIBLES = {
    "modality", "status", "confidence", "brand", "model",
    "customer", "city", "country", "observer",
    "Modalidad", "Estado", "Confianza", "Marca", "Modelo",
    "Cliente", "Ciudad", "País", "Observador",
}


def para_mostrar(df: pd.DataFrame) -> pd.DataFrame:
    """Traduce enums y huecos antes de pintar una tabla.

    Solo afecta a lo que se ve: el dataset y el CSV conservan los valores del
    Excel del reto ("Unknown", "Confirmed", "MR"...), que es lo que espera quien
    reciba el export.
    """
    if df.empty:
        return df
    copia = df.copy()
    for columna in copia.columns:
        if columna in COLUMNAS_TRADUCIBLES and copia[columna].dtype == object:
            copia[columna] = copia[columna].map(
                lambda v: etiqueta(v) if isinstance(v, str) else v
            )
    return copia


# Etiquetas del menú de las gráficas. Lo dibuja Vega-Embed, no Streamlit, y sale
# en inglés salvo que se le pasen traducidas dentro del propio spec.
MENU_GRAFICA = {
    "embedOptions": {
        "i18n": {
            "PNG_ACTION": "Guardar como PNG",
            "SVG_ACTION": "Guardar como SVG",
            "SOURCE_ACTION": "Ver el código",
            "COMPILED_ACTION": "Ver el Vega compilado",
            "EDITOR_ACTION": "Abrir en el editor de Vega",
        }
    }
}


def grafica(chart, **kwargs) -> None:
    """Pinta una gráfica con el menú en español."""
    st.altair_chart(chart.properties(usermeta=MENU_GRAFICA), **kwargs)


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


@st.fragment(run_every="1s")
def panel_estado_motor(motor) -> None:
    estado = motor.estado
    fase = "listo" if estado.listo else "error" if estado.error else "cargando"
    anterior = st.session_state.get("fase_motor")
    st.session_state.fase_motor = fase
    if anterior is not None and anterior != fase:
        st.rerun()
    if estado.listo:
        st.success("Captura lista")
    elif estado.error:
        st.error("No se pudo preparar la captura. Reintenta el inicio.")
        with st.expander("Detalle del error"):
            st.code(estado.error, language=None)
        if st.button("Reintentar", type="primary", width="stretch"):
            motor.iniciar_en_segundo_plano(reintentar=True)
            st.rerun()
    else:
        st.info("Preparando captura… Puedes escribir mientras carga.")
        st.caption("El primer inicio puede tardar más si necesita descargar el modelo.")


def barra_lateral(motor) -> None:
    with st.sidebar:
        st.markdown("### Base Instalada QVAC")
        st.caption("Inteligencia de base instalada, capturada como una conversación.")

        panel_estado_motor(motor)
        st.markdown("**Inferencia 100% en el dispositivo**")
        st.caption("Ninguna nota de cliente sale de este equipo.")

        st.divider()
        st.session_state.observador = st.text_input("Colaborador", st.session_state.observador)
        st.caption(f"{store.contar()} grupos vigentes en la base")
        if any(r.get("source") == "Seed" for r in store.todas()):
            st.caption("Incluye datos ficticios de demostración del Excel del reto.")


# --- pagina: capturar -------------------------------------------------------


def pagina_capturar(motor) -> None:
    st.header("Capturar observación")

    if st.session_state.borrador is None:
        drafts = store.borradores()
        if drafts:
            elegido = st.selectbox("Borradores guardados", drafts,
                format_func=lambda d: f"#{d['id']} · {d['original'][:80]}")
            if st.button("Retomar borrador"):
                payload = elegido['payload']
                st.session_state.borrador = Borrador.model_validate(payload['borrador'])
                st.session_state.texto_original = payload.get('texto_original', elegido['original'])
                st.session_state.conversacion = payload.get('conversacion', [])
                st.session_state.omitidas = set(payload.get('omitidas', []))
                st.session_state.observador = payload.get('observer', 'Unknown')
                st.session_state.fuente = payload.get('source', 'Text')
                st.session_state.visit_date = date.fromisoformat(payload['visit_date']) if payload.get('visit_date') else None
                st.session_state.visit_id, st.session_state.visit_version = elegido['id'], elegido['version']
                _tocar_borrador()
        _entrada_nueva(motor)
    else:
        _revisar_borrador(motor)


def _entrada_nueva(motor) -> None:
    st.session_state.visit_date = st.date_input(
        "Fecha de la visita", value=st.session_state.visit_date, max_value=date.today())
    st.caption("Describe una observación, adjunta documentos o audio con +, o graba con el micrófono. Las consultas al inventario siguen en Preguntar.")
    pendiente = st.session_state.get("captura_pendiente")
    # Dentro de un contenedor, el compositor queda en Capturar, no fijo sobre las otras pestañas.
    with st.container():
        envio = st.chat_input(
            "Describe lo que observaste en la visita…", key="barra_captura",
            accept_file="multiple", file_type=[e.lstrip('.') for e in sorted(capture.EXTENSIONS)],
            accept_audio=True, audio_sample_rate=16000, max_upload_size=25,
            disabled=pendiente is not None,
        )
    st.caption("Documentos: PDF con texto, Word, Excel, CSV y texto. Audio: WAV, MP3, M4A, OGG, FLAC y AAC. Máximo 25 MB por envío. Los PDF escaneados requieren OCR.")
    if envio is not None:
        pendiente = {"texto": envio if isinstance(envio, str) else envio.text,
            "archivos": [] if isinstance(envio, str) else list(envio.files),
            "audio": None if isinstance(envio, str) else envio.audio}
        st.session_state.captura_pendiente = pendiente
        st.session_state.pop("captura_preparada", None)
        st.session_state.pop("captura_error", None)
    if pendiente is None:
        return
    if st.button("Descartar envío"):
        _limpiar_envio()
        st.rerun()
    if not motor.estado.listo:
        st.info("Tu observación está pendiente. Se preparará cuando QVAC esté listo.")
        return
    if "captura_preparada" not in st.session_state and "captura_error" not in st.session_state:
        with st.spinner("Preparando la observación en el dispositivo…"):
            try:
                st.session_state.captura_preparada = capture.preparar(
                    pendiente['texto'], pendiente['archivos'], pendiente['audio'], motor)
            except Exception as exc:
                st.session_state.captura_error = str(exc)
    if st.session_state.get("captura_error"):
        st.error(st.session_state.captura_error)
        if st.button("Reintentar preparación"):
            st.session_state.pop("captura_error", None)
            st.rerun()
        return
    entrada = st.session_state.captura_preparada
    if entrada.nombres:
        st.caption("Adjuntos: " + " · ".join(entrada.nombres))
    for aviso in entrada.avisos:
        st.warning(aviso)
    # La revisión conserva el contenido después de un fallo y permite corregir transcripciones.
    texto = st.text_area("Observación para revisar", entrada.texto, height=200, key="revision_envio")
    clientes = documents.clientes_mencionados(texto)
    cliente = None
    if len(clientes) > 1:
        st.warning("Se mencionan varios clientes. Selecciona cuál registrar en esta observación.")
        cliente = st.selectbox("Cliente que vas a capturar", [None, *clientes],
            format_func=lambda x: x or "Selecciona un cliente", key="cliente_envio")
    if st.button("Extraer observación", type="primary",
                 disabled=not texto.strip() or (len(clientes) > 1 and cliente is None)):
        if entrada.fuente == 'Document' or len(clientes) > 1:
            _procesar_documento(texto, ', '.join(entrada.nombres) or 'Observación', cliente, motor)
        else:
            _procesar(texto, entrada.fuente, motor)


def _limpiar_envio():
    for key in ('captura_pendiente', 'captura_preparada', 'captura_error', 'revision_envio', 'cliente_envio'):
        st.session_state.pop(key, None)


def _procesar_documento(texto: str, nombre: str, cliente: str | None, motor) -> None:
    if not motor.estado.listo:
        st.info("La captura aun no esta lista. El documento sigue cargado mientras arranca.")
        return
    if st.session_state.inicio_captura is None:
        st.session_state.inicio_captura = time.time()

    with st.spinner("Leyendo el documento en el dispositivo..."):
        try:
            lectura = documents.analizar(
                texto, motor, fecha=st.session_state.visit_date, cliente=cliente
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"No se pudo interpretar con QVAC: {exc}. El documento sigue cargado.")
            return

    borrador = lectura.borrador
    if not borrador.items and es_desconocido(borrador.customer):
        st.warning(
            "No se reconocio ningun equipo en el documento. Revisa el texto extraido: "
            "puede que la informacion este en una tabla que no se leyo bien."
        )
        return

    st.session_state.borrador = borrador
    st.session_state.texto_original = texto
    st.session_state.fuente = "Document"
    st.session_state.omitidas = set()
    st.session_state.conversacion = [("colaborador", f"[Documento: {nombre}]")]
    st.session_state.pregunta_actual = followup.siguiente_pregunta(borrador, set())
    _persistir_borrador()
    st.session_state.revision += 1
    st.rerun()


def _procesar(texto: str, fuente: str, motor) -> None:
    if not motor.estado.listo:
        st.info("La captura aún no está lista. Tu texto se conserva mientras carga.")
        return
    # El cronómetro responde a la pregunta de adopción del reto: por qué alguien
    # usaría esto después de cada visita. La respuesta tiene que ser un número, y
    # solo cuenta si empieza en la nota y acaba cuando el dato queda guardado.
    if st.session_state.inicio_captura is None:
        st.session_state.inicio_captura = time.time()
    with st.spinner("Extrayendo datos en el dispositivo..."):
        try:
            borrador = extraer(texto, motor, estricto=True, fecha=st.session_state.visit_date)
        except Exception as exc:
            st.error(f"No se pudo interpretar con QVAC: {exc}. Tu nota sigue disponible para reintentar.")
            return
    st.session_state.borrador = borrador
    st.session_state.texto_original = texto
    st.session_state.fuente = fuente
    st.session_state.omitidas = set()
    st.session_state.conversacion = [("colaborador", texto)]
    st.session_state.pregunta_actual = followup.siguiente_pregunta(borrador, set())
    _persistir_borrador()
    st.session_state.revision += 1
    st.rerun()


def _revisar_borrador(motor) -> None:
    borrador: Borrador = st.session_state.borrador

    fuente = st.session_state.fuente
    es_voz = fuente == "Voice"
    es_doc = fuente == "Document"

    st.markdown(
        "#### Transcripción" if es_voz
        else "#### Texto del documento" if es_doc
        else "#### Nota original"
    )

    if es_voz or es_doc:
        # Una palabra mal leida arrastra toda la extraccion, tanto si viene de
        # Whisper como de un PDF mal maquetado. Poder corregirla y reprocesar
        # evita repetir la grabacion o volver a subir el fichero.
        st.caption(
            "Si el dictado se entendió mal, corrígelo aquí y vuelve a extraer."
            if es_voz else
            "Si el documento se leyó mal, corrígelo o recórtalo aquí y vuelve a extraer."
        )
        corregida = st.text_area(
            "Texto de origen", st.session_state.texto_original,
            height=90 if es_voz else 200,
            label_visibility="collapsed", key=f"trans{st.session_state.revision}",
        )
        if corregida.strip() and corregida != st.session_state.texto_original:
            if st.button("Volver a extraer con el texto corregido", type="primary"):
                if es_doc:
                    _procesar_documento(corregida, "texto corregido", borrador.customer, motor)
                else:
                    _procesar(corregida, "Voice", motor)
    else:
        st.info(st.session_state.texto_original)

    izquierda, derecha = st.columns([3, 2])

    with izquierda:
        st.markdown("#### Datos extraídos")
        _editor_borrador(borrador)

    with derecha:
        _panel_seguimiento(borrador, motor)

    st.divider()
    _panel_guardado(borrador, motor)


def _campo(contenedor, titulo: str, valor: str, key: str) -> str:
    """Campo de texto que enseña el hueco vacío en vez de la palabra "Unknown".

    Un campo vacío con "No identificado" en gris se entiende y se rellena de un
    tirón; uno que ya trae texto obliga a borrarlo primero. Hacia fuera sigue
    devolviendo DESCONOCIDO, que es lo que se guarda.
    """
    escrito = contenedor.text_input(
        titulo,
        "" if es_desconocido(valor) else valor,
        key=key,
        placeholder=NO_IDENTIFICADO,
    )
    return escrito.strip() or DESCONOCIDO


def _estado_cliente(nombre: str) -> None:
    """Dice de donde sale el cliente, para no tener que fiarse a ciegas.

    Importa la diferencia: uno del catalogo hereda ciudad y país conocidos y
    entra en la deteccion de duplicados; uno nuevo abre ficha y conviene
    revisar como está escrito antes de guardarlo.
    """
    from src.normalize import normalizar_cliente

    if es_desconocido(nombre):
        st.caption("Sin cliente. No se entendió el nombre, así que no se ha rellenado nada.")
        return
    _, ficha = normalizar_cliente(nombre)
    if ficha:
        st.caption(f"Cliente conocido — {ficha.get('city')}, {ficha.get('country')}")
    else:
        st.caption("Cliente nuevo: no estaba en la base. Revisa el nombre antes de guardar.")


def _editor_borrador(borrador: Borrador) -> None:
    # Streamlit conserva el valor de un widget mientras su `key` no cambie, y ese
    # valor guardado gana al argumento por defecto. Sin versionar las claves, el
    # editor volveria a escribir el valor viejo encima de lo que el agente acaba
    # de completar en el borrador. La revision sube con cada cambio programatico.
    rev = st.session_state.revision

    c1, c2, c3 = st.columns(3)
    borrador.customer = _campo(c1, "Cliente", borrador.customer, f"cli{rev}")
    borrador.city = _campo(c2, "Ciudad", borrador.city, f"ciu{rev}")
    borrador.country = _campo(c3, "País", borrador.country, f"pai{rev}")
    _estado_cliente(borrador.customer)
    st.session_state.visit_date = st.date_input("Fecha real de visita", value=st.session_state.visit_date,
        max_value=date.today(), key=f"fecha{rev}")
    st.caption("Deja la fecha vacía si no la conoces. Una cantidad o edad vacía significa desconocida; cero es un valor explícito.")

    for i, equipo in enumerate(borrador.items):
        with st.container(border=True):
            f1, f2, f3 = st.columns([2, 1, 2])
            modalidades = [m.value for m in Modalidad]
            equipo.modality = Modalidad(
                f1.selectbox(
                    "Modalidad", modalidades, modalidades.index(equipo.modality.value),
                    format_func=etiqueta, key=f"m{rev}_{i}",
                )
            )
            equipo.quantity = f2.number_input("Cantidad", min_value=0, max_value=200, value=equipo.quantity, key=f"q{rev}_{i}")
            equipo.brand = _campo(f3, "Marca", equipo.brand, f"b{rev}_{i}")

            g1, g2, g3 = st.columns([2, 1, 2])
            equipo.model = _campo(g1, "Modelo", equipo.model, f"mo{rev}_{i}")
            equipo.age_years = g2.number_input("Edad (años)", min_value=0, max_value=40, value=equipo.age_years, key=f"e{rev}_{i}")
            estados = [e.value for e in Estado]
            equipo.status = Estado(
                g3.selectbox(
                    "Estado", estados, estados.index(equipo.status.value),
                    format_func=etiqueta, key=f"s{rev}_{i}",
                )
            )
            if st.button("Quitar este grupo", key=f"del{rev}_{i}"):
                borrador.items.pop(i)
                _tocar_borrador()

    if st.button("Añadir otro tipo de equipo"):
        borrador.items.append(Equipo())
        _tocar_borrador()

    borrador.notes = st.text_area("Notas", borrador.notes, height=70, key=f"nota{rev}")


def _tocar_borrador() -> None:
    """Marca el borrador como cambiado por código y refresca la interfaz."""
    _persistir_borrador()
    st.session_state.revision += 1
    st.rerun()


def _panel_seguimiento(borrador: Borrador, motor) -> None:
    st.markdown("#### El agente pregunta")
    pendientes = followup.pendientes(borrador, st.session_state.omitidas)

    if not pendientes:
        st.success("No quedan preguntas pendientes; los datos omitidos permanecen desconocidos.")
        st.caption(followup.resumen(borrador))
        return

    pregunta = pendientes[0]
    st.caption(f"Quedan {len(pendientes)} datos por completar. Se pregunta primero el más valioso.")

    with st.container(border=True):
        st.markdown(f"**{pregunta.texto}**")
        with st.form(f"form_seg_{pregunta.clave}", clear_on_submit=True):
            respuesta = st.text_input("Tu respuesta", label_visibility="collapsed",
                                      placeholder="Escribe aquí, o pulsa 'No lo sé'")
            c1, c2 = st.columns(2)
            responder = c1.form_submit_button("Responder", type="primary", width="stretch")
            omitir = c2.form_submit_button("No lo sé", width="stretch")

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
            st.session_state.conversacion.append(("colaborador", "No lo sé"))
            _tocar_borrador()

    if len(pendientes) > 1:
        with st.expander(f"Otras {len(pendientes) - 1} preguntas pendientes"):
            for p in pendientes[1:8]:
                st.write(f"- {p.texto}  ·  valor {p.valor:.0f}")

    if len(st.session_state.conversacion) > 1:
        with st.expander("Conversación"):
            for quien, mensaje in st.session_state.conversacion:
                with st.container(border=True):
                    st.caption("Colaborador" if quien == "colaborador" else "Agente")
                    st.write(mensaje)


def _persistir_borrador():
    ss = st.session_state
    if ss.borrador is None:
        return
    payload = {"borrador": ss.borrador.model_dump(mode="json"), "texto_original": ss.texto_original,
        "observer": ss.observador, "source": ss.fuente,
        "visit_date": ss.visit_date.isoformat() if ss.visit_date else "",
        "conversacion": ss.conversacion, "omitidas": sorted(ss.omitidas)}
    ss.visit_id, ss.visit_version = store.guardar_borrador(payload, ss.visit_id, ss.visit_version)


def _panel_guardado(borrador: Borrador, motor) -> None:
    st.markdown("#### Revisar y consolidar")
    try:
        _persistir_borrador()
    except ValueError as exc:
        st.error(str(exc))
        return
    if st.button("Descartar borrador"):
        store.descartar(st.session_state.visit_id)
        _reiniciar()
    if not borrador.items or es_desconocido(borrador.customer):
        st.info("Identifica el hospital y al menos una modalidad antes de consolidar.")
        return
    st.info(followup.resumen(borrador))
    st.caption("Cada grupo debe representar unidades distintas. No incluyas el total y sus subconjuntos como grupos separados.")
    # Antes habia que elegir a mano entre cuatro acciones sobre el inventario y,
    # para dos de ellas, el grupo de destino de cada modalidad. Se decide solo:
    # lo que ya existe entra como evidencia sobre su grupo y lo que no, se crea.
    # Asi guardar es un clic, y las discrepancias salen en Conflictos en vez de
    # frenar al colaborador justo al terminar la visita.
    existentes = [f for f in store.todas() if store.identidad(f) == store.identidad(borrador.model_dump())]
    if existentes:
        st.caption("Este cliente ya está en la base. Lo que coincida se añade como evidencia del grupo existente.")
        st.dataframe(para_mostrar(pd.DataFrame(existentes)[['observation_id','modality','quantity','brand','age_years','visit_date']]),hide_index=True)
        _avisar_discrepancias(borrador, existentes)

    if st.button("Confirmar y guardar", type="primary"):
        try:
            _persistir_borrador()
            ids = store.consolidar(st.session_state.visit_id, st.session_state.visit_version, "auto")
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.ultimo_guardado = ids
            if st.session_state.inicio_captura:
                st.session_state.segundos_captura = time.time() - st.session_state.inicio_captura
            _reiniciar()


def _avisar_discrepancias(borrador: Borrador, existentes: list[dict]) -> None:
    """Adelanta qué grupos no cuadran con lo que ya había.

    Se dice antes de guardar, no después: si el colaborador se equivocó al
    dictar, corregirlo ahora cuesta un segundo; descubrirlo mañana en la lista
    de conflictos cuesta una llamada al hospital.
    """
    choques = []
    for equipo in borrador.items:
        for fila in existentes:
            if fila["modality"] != equipo.modality.value:
                continue
            if not store.compatible(equipo.model_dump(), fila):
                choques.append(
                    f"{etiqueta(equipo.modality.value)}: dices {equipo.quantity or '?'} "
                    f"y en la base hay {fila['quantity']}"
                )
            break
    if choques:
        st.warning(
            "Esto no cuadra con lo registrado: " + " · ".join(choques)
            + ". Se guardará como evidencia y quedará en **Conflictos** para revisarlo; "
            "el inventario no cambia hasta que alguien decida."
        )


def _reiniciar() -> None:
    _limpiar_envio()
    st.session_state.inicio_captura = None
    for k in ["borrador", "texto_original", "omitidas", "conversacion", "pregunta_actual", "visit_id", "visit_version", "visit_date"]:
        st.session_state.pop(k, None)
    st.rerun()


# --- pagina: cliente --------------------------------------------------------


def pagina_cliente() -> None:
    st.header("Base instalada por cliente")
    df = df_observaciones()
    if df.empty:
        st.info("Todavía no hay observaciones. Captura una en la pestaña anterior.")
        return

    clientes = df.drop_duplicates('customer_key').to_dict('records')
    cliente = st.selectbox("Cliente", clientes,
        format_func=lambda r: f"{r['customer']} · {r['city']} · {r['country']}")
    sub = df[df['customer_key'] == cliente['customer_key']].copy()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Unidades registradas", int(sub["quantity"].sum()))
    c2.metric("Modalidades", sub["modality"].nunique())
    edades = sub.loc[sub["age_years"] > 0, "age_years"]
    c3.metric("Edad media", f"{edades.mean():.1f} años" if len(edades) else "sin datos")
    c4.metric("Confianza media", f"{sub['confidence_score'].mean():.0f}/100")

    ciudad = sub["city"].iloc[0]
    pais = sub["country"].iloc[0]
    st.caption(f"{ciudad}, {pais} · última visita {sub['visit_date'].max()}")
    st.caption(f"Grupos con cantidad desconocida: {sub['quantity'].isna().sum()}. Las unidades son solo las conocidas.")

    with st.expander("Historial del inventario del cliente"):
        st.dataframe(para_mostrar(pd.DataFrame(store.historial_cliente(cliente['customer_key']))), hide_index=True)

    # El panorama primero, el detalle debajo: quien abre la ficha quiere saber
    # que hay instalado, no leer grupo por grupo. La edad va en rango porque una
    # flota comprada en tandas distintas no tiene una sola edad.
    st.markdown("#### Panorama de equipos")
    resumen = insights.resumen_cliente(sub.to_dict("records"))
    st.dataframe(
        pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in resumen]),
        width="stretch", hide_index=True,
    )
    if any(r["_oportunidad"] for r in resumen):
        modalidades = ", ".join(r["Tipo de equipo"] for r in resumen if r["_oportunidad"])
        st.caption(f"Ventana de renovación en {modalidades}")
    if any(r["_sin_verificar"] for r in resumen):
        st.caption(f"Hay datos sin verificar desde hace más de {DIAS_SIN_VERIFICAR} días")

    st.markdown("#### Grupos individuales")
    for _, fila in sub.sort_values("modality").iterrows():
        avisos = alertas(fila.to_dict())
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 2, 2])
            c1.markdown(
                f"**{etiqueta(fila['modality'])}** × {int(fila['quantity']) if pd.notna(fila['quantity']) else 'cantidad desconocida'}  \n"
                f"{etiqueta(fila['brand'])} · {etiqueta(fila['model'])}"
            )
            edad = f"{fila['age_years']} años" if pd.notna(fila["age_years"]) else "Edad no identificada"
            instal = f" (aprox. {fila['install_year']})" if pd.notna(fila["install_year"]) else ""
            c2.markdown(
                f"{edad}{instal}  \n"
                f"{etiqueta(fila['status'])} · observado por {fila['observer']}"
            )
            c3.markdown(
                f"Confianza **{fila['confidence_score']}/100** "
                f"({etiqueta(fila['confidence'])})  \n"
                f"Visita: {fila['visit_date'] or 'sin fecha'} · Verificación: {fila['verified_date'] or 'sin fecha'}"
            )
            if avisos:
                st.caption(" · ".join(avisos))
            with st.expander("De dónde sale este dato"):
                st.write(f"**Nota original:** {fila['raw_input'] or '(no registrada)'}")
                if fila["notes"]:
                    st.write(f"**Notas:** {fila['notes']}")
                st.write("**Historial de visitas y correcciones:**")
                st.json(store.historial(int(fila["observation_id"])), expanded=False)
                st.write("**Puntaje de confianza:**")
                st.table(pd.DataFrame([desglose(fila.to_dict(), store.todas())]))


# --- pagina: mapa -----------------------------------------------------------

TODOS = "Todos"


def pagina_mapa() -> None:
    """Navegación Región → País → Ciudad → Cliente, como pide el reto."""

    filas = store.todas()
    if not filas:
        st.info("Todavía no hay observaciones.")
        return

    sedes = geo.sedes(filas)
    if not sedes:
        st.warning("Ninguna observación tiene una ciudad que se pueda situar en el mapa.")
        return

    # --- filtros encadenados: cada nivel restringe al siguiente
    f1, f2, f3 = st.columns(3)
    regiones = [TODOS] + sorted({s["region"] for s in sedes})
    region = f1.selectbox("Región", regiones)
    visibles = [s for s in sedes if region in (TODOS, s["region"])]

    paises = [TODOS] + sorted({s["pais_es"] for s in visibles})
    pais = f2.selectbox("País", paises)
    visibles = [s for s in visibles if pais in (TODOS, s["pais_es"])]

    ciudades = [TODOS] + sorted({s["city"] for s in visibles if s["city"]})
    ciudad = f3.selectbox("Ciudad", ciudades)
    visibles = [s for s in visibles if ciudad in (TODOS, s["city"])]

    if not visibles:
        st.info("No hay clientes con esa combinación.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Clientes", len(visibles))
    c2.metric("Unidades", sum(s["unidades"] for s in visibles))
    c3.metric("Ciudades", len({s["city"] for s in visibles}))
    c4.metric("Con flota envejecida", sum(1 for s in visibles if s["oportunidades"]))

    _dibujar_mapa(visibles)

    ausentes = geo.sin_coordenadas(filas)
    if ausentes:
        # Un cliente que desaparece del mapa sin avisar parece un dato perdido.
        st.caption(f"Sin ubicar por falta de ciudad: {', '.join(ausentes)}")

    st.markdown("#### Clientes en la selección")
    tabla = pd.DataFrame(
        [
            {
                "Cliente": s["customer"],
                "Ciudad": s["city"],
                "País": s["pais_es"],
                "Unidades": s["unidades"],
                "Modalidades": ", ".join(etiqueta(m) for m in s["modalidades"]),
                "Edad media": f"{s['edad_media']:.1f} años" if s["edad_media"] else NO_IDENTIFICADO,
                "Confianza": f"{s['confianza_media']:.0f}/100",
                "Última visita": s["ultima_visita"],
            }
            for s in visibles
        ]
    )
    st.dataframe(tabla, width="stretch", hide_index=True)

    # --- ultimo nivel de la jerarquia: el equipo instalado de un cliente
    st.markdown("#### Equipos del cliente")
    elegido = st.selectbox(
        "Cliente", [s["customer"] for s in visibles], key="cliente_mapa",
        label_visibility="collapsed",
    )
    detalle = insights.resumen_cliente(store.por_cliente(elegido))
    st.dataframe(
        pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in detalle]),
        width="stretch", hide_index=True,
    )


def _dibujar_mapa(sedes: list[dict]) -> None:
    """Pinta las sedes con pydeck.

    Los mosaicos del mapa se descargan de internet. Son un recurso de interfaz,
    no inferencia, así que no tocan la regla del reto; pero para que la demo sin
    conexión no se vea rota, si no cargan quedan los puntos sobre fondo liso.
    """
    import pydeck as pdk

    datos = [
        {
            "lat": s["lat"],
            "lon": s["lon"],
            "cliente": s["customer"],
            "ciudad": s["city"],
            "pais": s["pais_es"],
            "unidades": s["unidades"],
            "modalidades": ", ".join(etiqueta(m) for m in s["modalidades"]),
            "confianza": f"{s['confianza_media']:.0f}/100",
            "radio": 24_000 + s["unidades"] * 9_000,
            # Rojo cuando hay equipos en ventana de renovación, azul si no.
            "color": [201, 61, 58, 215] if s["oportunidades"] else [59, 111, 212, 200],
        }
        for s in sedes
    ]

    latitudes = [d["lat"] for d in datos]
    longitudes = [d["lon"] for d in datos]
    vista = pdk.ViewState(
        latitude=sum(latitudes) / len(latitudes),
        longitude=sum(longitudes) / len(longitudes),
        zoom=2.4 if len(datos) > 3 else 5,
    )
    capa = pdk.Layer(
        "ScatterplotLayer",
        data=datos,
        get_position="[lon, lat]",
        get_fill_color="color",
        get_radius="radio",
        pickable=True,
        opacity=0.75,
        stroked=True,
        get_line_color=[255, 255, 255, 120],
        line_width_min_pixels=1,
    )
    st.pydeck_chart(
        pdk.Deck(
            layers=[capa],
            initial_view_state=vista,
            map_style="light",
            tooltip={
                "html": "<b>{cliente}</b><br/>{ciudad}, {pais}<br/>"
                "{unidades} unidades · {modalidades}<br/>Confianza {confianza}",
            },
        )
    )
    st.caption("Rojo: con equipos en ventana de renovación. Azul: flota reciente.")


# --- pagina: conflictos -----------------------------------------------------


def pagina_conflictos() -> None:
    """Grupos sobre los que las visitas no se ponen de acuerdo."""
    st.header("Conflictos entre observaciones")
    st.caption(
        "Un duplicado es que dos personas cuenten lo mismo; un conflicto es que "
        "cuenten cosas distintas. Mientras siga abierto, el dato se enseña como dudoso."
    )

    filas = store.todas()
    abiertos = conflicts.abiertos(filas)

    if not abiertos:
        st.success("No hay conflictos abiertos: las observaciones son compatibles entre sí.")
        st.caption(
            "Aparecerán aquí en cuanto una visita registre cantidades, antigüedades "
            "o marcas que no cuadren con el grupo vigente del mismo cliente."
        )
        return

    st.warning(f"{len(abiertos)} grupo(s) con evidencia en desacuerdo, del más grave al menos.")

    for conflicto in abiertos:
        fila = conflicto["fila"]
        with st.container(border=True):
            st.markdown(
                f"**{fila['customer']} — {etiqueta(fila['modality'])}**  ·  "
                f"no cuadra en {', '.join(conflicto['motivos'])}"
            )
            st.markdown(f"**Vigente ahora**  \n{conflicts.resumen(fila)}")

            for propuesta, motivos in conflicto["versiones"]:
                st.markdown(f"**Otra visita dijo**  \n{conflicts.resumen(propuesta)}")
                st.caption("Difiere en: " + ", ".join(motivos))
                if propuesta.get("_original"):
                    st.caption(f"Nota original: {propuesta['_original']}")

            st.info(conflicts.COMO_SE_RESUELVE)
            with st.expander("Historial completo de este grupo"):
                st.json(store.historial(int(fila["observation_id"])), expanded=False)


# --- pagina: panorama -------------------------------------------------------


def pagina_panorama() -> None:
    st.header("Análisis entre clientes")
    df = df_observaciones()
    if df.empty:
        st.info("Todavía no hay observaciones.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Clientes", df["customer_key"].nunique())
    c2.metric("Unidades", int(df["quantity"].sum()))
    c3.metric("Países", df["country"].nunique())
    oportunidades = df[df["oportunidad"]]
    c4.metric(f"Equipos de {EDAD_RENOVACION}+ años", int(oportunidades["quantity"].sum()))

    st.markdown("#### Unidades por país y modalidad")
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
    grafica(grafico, width="stretch")

    izq, der = st.columns(2)
    with izq:
        st.markdown("#### Antigüedad de la flota")
        con_edad = df[df["age_years"] > 0]
        if not con_edad.empty:
            hist = (
                alt.Chart(con_edad)
                .mark_bar()
                .encode(
                    x=alt.X("age_years:Q", bin=alt.Bin(step=2), title="Edad (años)"),
                    y=alt.Y("sum(quantity):Q", title="Unidades"),
                    color=alt.Color(
                        "modality:N", legend=None,
                        scale=alt.Scale(domain=list(COLOR_MODALIDAD), range=list(COLOR_MODALIDAD.values())),
                    ),
                    tooltip=["modality", "sum(quantity)"],
                )
                .properties(height=260)
            )
            grafica(hist, width="stretch")
        else:
            st.caption("Sin edades registradas todavía.")

    with der:
        st.markdown("#### Marcas instaladas")
        marcas = df[df["brand"] != "Unknown"].groupby("brand", as_index=False)["quantity"].sum()
        if not marcas.empty:
            grafica(
                alt.Chart(marcas).mark_bar(color="#3b6fd4").encode(
                    x=alt.X("quantity:Q", title="Unidades"),
                    y=alt.Y("brand:N", title=None, sort="-x"),
                    tooltip=["brand", "quantity"],
                ).properties(height=260),
                width="stretch",
            )
        else:
            st.caption("Sin marcas registradas todavía.")

    st.markdown(f"#### Oportunidades de renovación (equipos de {EDAD_RENOVACION}+ años)")
    if oportunidades.empty:
        st.caption("Ninguna por ahora.")
    else:
        vista = oportunidades.sort_values("age_years", ascending=False)[
            ["customer", "country", "modality", "quantity", "brand", "age_years", "confidence_score"]
        ].rename(columns={
            "customer": "Cliente", "country": "País", "modality": "Modalidad",
            "quantity": "Unidades", "brand": "Marca", "age_years": "Edad",
            "confidence_score": "Confianza",
        })
        st.dataframe(para_mostrar(vista), width="stretch", hide_index=True)

    st.markdown(f"#### Datos sin fecha o sin verificar en {DIAS_SIN_VERIFICAR}+ días")
    viejas = df[df["sin_verificar"]]
    if viejas.empty:
        st.caption("No hay registros que superen el plazo de verificación.")
    else:
        vista = viejas.sort_values("antiguedad_dias", ascending=False)[
            ["customer", "modality", "quantity", "observer", "visit_date", "antiguedad_dias"]
        ].rename(columns={
            "customer": "Cliente", "modality": "Modalidad", "quantity": "Unidades",
            "observer": "Observador", "visit_date": "Última visita", "antiguedad_dias": "Días",
        })
        st.dataframe(para_mostrar(vista), width="stretch", hide_index=True)

    filas_panel = df.to_dict("records")
    izq2, der2 = st.columns(2)

    with izq2:
        st.markdown("#### Clientes con información incompleta")
        incompletos = insights.clientes_incompletos(filas_panel)
        if not incompletos:
            st.caption("Ninguno: todas las fichas tienen marca, modelo, cantidad y antigüedad.")
        else:
            st.dataframe(
                pd.DataFrame(
                    [{k: v for k, v in r.items() if not k.startswith("_")} for r in incompletos]
                ),
                width="stretch", hide_index=True,
            )

    with der2:
        st.markdown("#### Sitios actualizados recientemente")
        recientes = insights.sitios_recientes(filas_panel)
        if not recientes:
            st.caption("Todavía no hay visitas registradas.")
        else:
            st.dataframe(para_mostrar(pd.DataFrame(recientes)), width="stretch", hide_index=True)

    st.divider()
    st.markdown("#### Base de datos completa")
    st.dataframe(
        para_mostrar(df.drop(columns=["oportunidad", "sin_verificar"])),
        width="stretch", hide_index=True,
    )
    csv_data = pd.DataFrame(store.todas()).to_csv(index=False).encode("utf-8-sig")
    st.download_button("Descargar CSV", csv_data, "base_instalada.csv", "text/csv")


# --- pagina: preguntar ------------------------------------------------------


def pagina_preguntar(motor) -> None:
    st.header("Hazle preguntas a la base de datos")

    ejemplos = [
        "Muéstrame solo los de Panamá",
        "clientes en Brasil con resonadores de más de siete años",
        "¿Dónde hay oportunidades de renovación?",
        "¿Cuántos ecógrafos hay en México?",
        "¿Qué equipos llevan más de medio año sin verificar?",
    ]
    cols = st.columns(len(ejemplos))
    for col, ejemplo in zip(cols, ejemplos):
        if col.button(ejemplo, width="stretch"):
            st.session_state["consulta"] = ejemplo

    pregunta = st.text_input("Tu pregunta", st.session_state.get("consulta", ""))
    if not pregunta.strip():
        return

    with st.spinner("Interpretando en el dispositivo..."):
        try:
            filtro = nlquery.interpretar(pregunta, motor if motor.estado.listo else None)
        except ValueError as exc:
            st.info(str(exc))
            return
    resultados = nlquery.aplicar(filtro, store.todas())

    st.caption(f"Filtro entendido: **{nlquery.describir_filtro(filtro)}**")
    if not resultados and filtro["pais"]:
        st.info(f"No hay observaciones en {filtro['pais']} que cumplan los filtros indicados.")
    else:
        st.success(nlquery.resumir(pregunta, resultados, motor))

    if resultados:
        vista = pd.DataFrame(resultados)[
            ["customer", "country", "city", "modality", "quantity", "brand", "age_years",
             "status", "confidence_score", "visit_date"]
        ].rename(columns={
            "customer": "Cliente", "country": "País", "city": "Ciudad", "modality": "Modalidad",
            "quantity": "Unidades", "brand": "Marca", "age_years": "Edad", "status": "Estado",
            "confidence_score": "Confianza", "visit_date": "Visita",
        })
        st.dataframe(para_mostrar(vista), width="stretch", hide_index=True)

        if filtro.get("agrupar_por"):
            columna = {"cliente": "customer", "pais": "country", "modalidad": "modality", "marca": "brand"}[
                filtro["agrupar_por"]
            ]
            agrupado = pd.DataFrame(resultados).groupby(columna, as_index=False)["quantity"].sum()
            grafica(
                alt.Chart(agrupado).mark_bar(color="#2e9e83").encode(
                    x=alt.X("quantity:Q", title="Unidades"),
                    y=alt.Y(f"{columna}:N", title=None, sort="-x"),
                ).properties(height=max(180, 32 * len(agrupado))),
                width="stretch",
            )


# --- pagina: sistema --------------------------------------------------------


# Las siete etapas que el reto pide demostrar de punta a punta, con el fichero
# que hace cada una. Sirve para que se vea de un golpe que el recorrido completo
# está cubierto y dónde mirar en el código.
PIPELINE = [
    ("Capturar", "Voz o texto, como se lo contarías a un compañero", "app.py"),
    ("Entender", "Whisper y el LLM, en este dispositivo", "qvac_engine.py"),
    ("Estructurar", "Reglas + JSON Schema, sin inventar datos", "extract.py"),
    ("Validar", "Preguntas por lo que falta, duplicados y conflictos", "followup.py · conflicts.py"),
    ("Guardar", "Grupos vigentes con evidencia e historial", "store.py"),
    ("Visualizar", "Ficha de cliente, mapa y panel", "insights.py · geo.py"),
    ("Generar valor", "Oportunidades y consultas en lenguaje natural", "nlquery.py"),
]


def _diagrama_pipeline() -> None:
    st.markdown("#### El recorrido completo")
    st.caption(
        "Capturar → Entender → Estructurar → Validar → Guardar → Visualizar → Generar valor"
    )
    columnas = st.columns(len(PIPELINE))
    for columna, (etapa, que_hace, donde) in zip(columnas, PIPELINE):
        with columna:
            st.markdown(f"**{etapa}**")
            st.caption(que_hace)
            st.caption(f"`{donde}`")


def pagina_sistema(motor) -> None:
    st.header("Sistema y cumplimiento")
    estado = motor.estado

    c1, c2, c3 = st.columns(3)
    c1.metric("Estado", "Activo" if estado.listo else "Error" if estado.error else "Preparando")
    c2.metric("Inferencias", estado.inferencias)
    c3.metric("Última latencia", f"{estado.ultima_latencia:.2f} s" if estado.ultima_latencia else "-")

    _diagrama_pipeline()

    st.markdown("#### Regla técnica del reto")
    st.markdown(
        "> *La inferencia corre en el dispositivo o entre pares. Nunca en la nube.*\n\n"
        "Cómo se cumple aquí:"
    )
    st.markdown(
        f"""
- **SDK**: `tetherto-qvac-sdk` (Python), que arranca un worker local Bare.
- **Modelo de lenguaje**: `{estado.llm_model}`, un GGUF cargado desde disco.
- **Modelo de voz**: `{estado.stt_model}` (Whisper), también local.
- **Extracción, seguimiento, consultas y transcripción**: todo pasa por `src/qvac_engine.py`,
  el único punto del código que ejecuta inferencia.
- **Sin claves de API**: no hay ninguna credencial de proveedor en el proyecto.
- **Red**: solo la descarga inicial del fichero del modelo. Después la app funciona
  en modo avión; puedes desconectar el wifi y seguir capturando.
"""
    )

    st.markdown("#### Modelos en caché local")
    if estado.ficheros_modelo:
        for fichero in estado.ficheros_modelo:
            st.code(fichero, language=None)
        st.caption("Directorio: ~/.qvac/models")
    else:
        st.caption("Los modelos aparecerán cuando termine la preparación.")

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
    st.session_state.fase_motor = (
        "listo" if motor.estado.listo else "error" if motor.estado.error else "cargando"
    )
    barra_lateral(motor)

    if st.session_state.ultimo_guardado:
        cuantas = len(st.session_state.ultimo_guardado)
        segundos = st.session_state.segundos_captura
        tiempo = f" en {segundos:.0f} s" if segundos else ""
        st.toast(f"Guardadas {cuantas} observaciones{tiempo}")
        st.session_state.ultimo_guardado = None
        st.session_state.segundos_captura = None

    capturar, cliente, mapa, panorama, conflictos, preguntar, sistema = st.tabs(
        ["Capturar", "Cliente", "Mapa", "Análisis", "Conflictos", "Consultas", "Sistema"]
    )
    with capturar:
        pagina_capturar(motor)
    with cliente:
        pagina_cliente()
    with mapa:
        pagina_mapa()
    with panorama:
        pagina_panorama()
    with conflictos:
        pagina_conflictos()
    with preguntar:
        pagina_preguntar(motor)
    with sistema:
        pagina_sistema(motor)


if __name__ == "__main__":
    main()
