"""Lectura de documentos de visita.

Un informe de visita, un acta o un inventario en hoja de cálculo traen la misma
información que una nota dictada, solo que ya escrita. Este módulo los convierte
en texto plano para que sigan exactamente el mismo camino que el resto: reglas,
LLM en el dispositivo, preguntas por lo que falte y consolidación.

Esto es *parsing*, no inferencia: se abre el fichero y se saca lo que ya está
escrito dentro. Nada sale del equipo, y no hace falta ningún modelo para esta
parte.

Deliberadamente no hay OCR. Un PDF escaneado es una imagen, y leerlo pediría un
modelo de visión con sus propios modos de fallo. Cuando un PDF no trae texto
seleccionable se dice claramente en vez de devolver una página en blanco.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# Extensiones que se saben leer, agrupadas por cómo se abren.
EXT_PDF = {".pdf"}
EXT_WORD = {".docx"}
EXT_HOJA = {".xlsx", ".xlsm", ".xltx"}
EXT_TABLA = {".csv", ".tsv"}
EXT_TEXTO = {".txt", ".md", ".log"}
EXTENSIONES = EXT_PDF | EXT_WORD | EXT_HOJA | EXT_TABLA | EXT_TEXTO

# Un .doc antiguo es un formato binario distinto de .docx y no se puede abrir
# con las mismas herramientas; conviene decirlo en vez de fallar de forma rara.
EXT_CONOCIDAS_NO_SOPORTADAS = {
    ".doc": "Word antiguo (.doc). Ábrelo y guárdalo como .docx.",
    ".pages": "Pages de Apple. Expórtalo a .docx o .pdf.",
    ".odt": "OpenDocument. Expórtalo a .docx.",
    ".rtf": "Texto enriquecido. Guárdalo como .docx o .txt.",
}

MAX_BYTES = 25 * 1024 * 1024


class DocumentoNoSoportado(ValueError):
    """El formato no se puede leer, o el fichero no trae texto."""


@dataclass
class Documento:
    """El texto de un fichero, con lo que hizo falta saber para leerlo."""

    nombre: str
    extension: str
    texto: str
    paginas: int = 0
    avisos: list[str] = field(default_factory=list)

    @property
    def palabras(self) -> int:
        return len(self.texto.split())


# --- lectores ---------------------------------------------------------------


def _leer_pdf(datos: bytes) -> tuple[str, int, list[str]]:
    from pypdf import PdfReader

    lector = PdfReader(io.BytesIO(datos))
    if lector.is_encrypted:
        try:
            lector.decrypt("")
        except Exception as exc:  # noqa: BLE001
            raise DocumentoNoSoportado(
                "El PDF está protegido con contraseña. Ábrelo, quítasela y vuelve a subirlo."
            ) from exc

    partes, vacias = [], 0
    for pagina in lector.pages:
        try:
            texto = pagina.extract_text() or ""
        except Exception:  # noqa: BLE001 - una página rota no tumba el resto
            texto = ""
        if texto.strip():
            partes.append(texto)
        else:
            vacias += 1

    avisos = []
    if vacias and partes:
        avisos.append(
            f"{vacias} de {len(lector.pages)} páginas no traen texto seleccionable "
            "(probablemente escaneadas) y se han saltado."
        )
    if not partes:
        raise DocumentoNoSoportado(
            "Este PDF no tiene texto seleccionable: parece un escaneo o una foto. "
            "Copia el texto a mano, o dicta la observación."
        )
    return "\n\n".join(partes), len(lector.pages), avisos


def _leer_word(datos: bytes) -> tuple[str, int, list[str]]:
    """Extrae párrafos y tablas de un .docx sin dependencias externas.

    Un .docx es un zip con XML dentro, así que se lee directamente en vez de
    añadir una librería solo para esto.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise DocumentoNoSoportado(
            "El fichero no parece un .docx válido. Si es un .doc antiguo, "
            "guárdalo como .docx."
        ) from exc

    # Tabuladores y saltos se conservan: en un acta separan campos de su valor.
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    xml = re.sub(r"<w:(?:br|cr)[^>]*/>", "\n", xml)

    lineas = []
    for parrafo in re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, re.S):
        texto = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", parrafo, re.S))
        texto = _desescapar(texto).strip()
        if texto:
            lineas.append(texto)

    if not lineas:
        raise DocumentoNoSoportado("El documento de Word está vacío o solo contiene imágenes.")
    return "\n".join(lineas), 0, []


def _desescapar(texto: str) -> str:
    for entidad, caracter in (
        ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&apos;", "'"),
    ):
        texto = texto.replace(entidad, caracter)
    return texto


def _leer_hoja(datos: bytes) -> tuple[str, int, list[str]]:
    """Convierte cada hoja en líneas legibles.

    Un inventario en Excel se lee mejor como "Cliente: X | Modalidad: MR |
    Cantidad: 2" que como una rejilla, porque así cada fila es una frase que el
    extractor puede interpretar igual que una nota escrita.
    """
    import openpyxl

    libro = openpyxl.load_workbook(io.BytesIO(datos), data_only=True, read_only=True)
    bloques, avisos = [], []
    for hoja in libro.worksheets:
        filas = [
            ["" if c is None else str(c).strip() for c in fila]
            for fila in hoja.iter_rows(values_only=True)
        ]
        filas = [f for f in filas if any(f)]
        if not filas:
            continue
        bloques.append(f"### Hoja: {hoja.title}")
        bloques.extend(_filas_a_lineas(filas))
    libro.close()

    if not bloques:
        raise DocumentoNoSoportado("La hoja de cálculo no tiene filas con datos.")
    return "\n".join(bloques), len(libro.worksheets), avisos


def _leer_tabla(datos: bytes) -> tuple[str, int, list[str]]:
    texto = _decodificar(datos)
    delimitador = "\t" if texto.count("\t") > texto.count(",") else ","
    filas = [
        [c.strip() for c in fila]
        for fila in csv.reader(io.StringIO(texto), delimiter=delimitador)
    ]
    filas = [f for f in filas if any(f)]
    if not filas:
        raise DocumentoNoSoportado("El fichero no tiene filas con datos.")
    return "\n".join(_filas_a_lineas(filas)), 0, []


# Cabeceras que significan "antigüedad en años". Importa detectarlas: el
# extractor solo acepta una edad si el texto la nombra como años, y en una hoja
# la unidad está en la cabecera, no en la celda. Sin esto un "Antigüedad: 11" se
# descarta por no poder distinguirlo de cualquier otro número.
CABECERAS_EDAD = ("antigued", "antigüed", "edad", "age", "anos de uso", "años de uso", "years")


def _es_columna_edad(cabecera: str) -> bool:
    texto = cabecera.strip().lower()
    if "instala" in texto or "install" in texto:  # eso es un año, no una edad
        return False
    return any(clave in texto for clave in CABECERAS_EDAD)


def _valor_con_unidad(cabecera: str, valor: str) -> str:
    """Añade "años" cuando la columna lo dice y la celda es un número suelto."""
    if not _es_columna_edad(cabecera):
        return valor
    limpio = valor.strip().replace(",", ".")
    try:
        numero = float(limpio)
    except ValueError:
        return valor
    # Un valor de cuatro cifras es un año de instalación, no una antigüedad.
    if numero != int(numero) or not 0 < numero <= 40:
        return valor
    return f"{int(numero)} años"


def _filas_a_lineas(filas: list[list[str]]) -> list[str]:
    """Cada fila de datos como "Columna: valor | Columna: valor"."""
    cabecera = filas[0]
    # Sin una cabecera con texto, etiquetar los valores inventaría nombres.
    tiene_cabecera = sum(1 for c in cabecera if c and not c.replace(".", "").isdigit()) >= 2
    if not tiene_cabecera:
        return [" | ".join(c for c in fila if c) for fila in filas]

    lineas = []
    for fila in filas[1:]:
        campos = [
            f"{cabecera[i]}: {_valor_con_unidad(cabecera[i], valor)}"
            for i, valor in enumerate(fila)
            if valor and i < len(cabecera) and cabecera[i]
        ]
        if campos:
            lineas.append(" | ".join(campos))
    return lineas or [" | ".join(c for c in cabecera if c)]


def _leer_texto(datos: bytes) -> tuple[str, int, list[str]]:
    texto = _decodificar(datos)
    if not texto.strip():
        raise DocumentoNoSoportado("El fichero está vacío.")
    return texto, 0, []


def _decodificar(datos: bytes) -> str:
    for codificacion in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return datos.decode(codificacion)
        except UnicodeDecodeError:
            continue
    return datos.decode("utf-8", errors="replace")


_LECTORES = [
    (EXT_PDF, _leer_pdf),
    (EXT_WORD, _leer_word),
    (EXT_HOJA, _leer_hoja),
    (EXT_TABLA, _leer_tabla),
    (EXT_TEXTO, _leer_texto),
]


# --- API --------------------------------------------------------------------


def leer(nombre: str, datos: bytes) -> Documento:
    """Fichero -> texto plano. Lanza DocumentoNoSoportado con un motivo claro."""
    extension = Path(nombre).suffix.lower()

    if extension in EXT_CONOCIDAS_NO_SOPORTADAS:
        raise DocumentoNoSoportado(EXT_CONOCIDAS_NO_SOPORTADAS[extension])
    if extension not in EXTENSIONES:
        soportadas = ", ".join(sorted(EXTENSIONES))
        raise DocumentoNoSoportado(f"No sé leer «{extension or 'sin extensión'}». Formatos: {soportadas}.")
    if not datos:
        raise DocumentoNoSoportado("El fichero está vacío.")
    if len(datos) > MAX_BYTES:
        raise DocumentoNoSoportado(
            f"El fichero pesa {len(datos) / 1e6:.0f} MB y el límite son {MAX_BYTES // 1_000_000} MB."
        )

    for extensiones, lector in _LECTORES:
        if extension in extensiones:
            texto, paginas, avisos = lector(datos)
            return Documento(
                nombre=nombre,
                extension=extension,
                texto=limpiar(texto),
                paginas=paginas,
                avisos=avisos,
            )
    raise DocumentoNoSoportado(f"No sé leer «{extension}».")


def limpiar(texto: str) -> str:
    """Quita el ruido de maquetación que confunde al extractor.

    Los PDF parten palabras al final de línea y dejan líneas sueltas de
    numeración; eso rompe los patrones de las reglas ("dos resona- dores").
    """
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    texto = re.sub(r"(\w)-\n(\w)", r"\1\2", texto)  # palabra partida por guion
    texto = re.sub(r"[ \t]+", " ", texto)
    lineas = [l.strip() for l in texto.split("\n")]
    # Una línea que solo tiene un número suele ser el número de página.
    lineas = [l for l in lineas if l and not re.fullmatch(r"\d{1,4}", l)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lineas)).strip()


# --- troceado ---------------------------------------------------------------

# Palabras por trozo. Qwen3 1.7B aguanta bastante más, pero cuanto más largo es
# el contexto peor atiende al detalle, y aqui el detalle es justo lo que importa.
PALABRAS_POR_TROZO = 320
SOLAPE = 40


def trocear(texto: str, palabras: int = PALABRAS_POR_TROZO, solape: int = SOLAPE) -> list[str]:
    """Parte el texto en trozos que quepan holgados en el modelo.

    Se corta por párrafos y no por número de palabras a secas, para no partir
    una frase por la mitad y perder la relación entre el número y el equipo. Los
    trozos se solapan un poco por lo mismo: si el corte cae entre "tienen dos" y
    "resonadores", el solape lo recupera.
    """
    texto = (texto or "").strip()
    if not texto:
        return []

    parrafos = [p.strip() for p in re.split(r"\n{2,}|\n(?=###)", texto) if p.strip()]
    if not parrafos:
        parrafos = [texto]

    trozos: list[str] = []
    actual: list[str] = []
    contador = 0
    for parrafo in parrafos:
        n = len(parrafo.split())
        if contador and contador + n > palabras:
            trozos.append("\n\n".join(actual))
            # Se arrastra el final del trozo anterior como contexto.
            cola = "\n\n".join(actual).split()[-solape:]
            actual, contador = ([" ".join(cola)], len(cola)) if solape else ([], 0)
        actual.append(parrafo)
        contador += n

    if actual:
        trozos.append("\n\n".join(actual))

    # Un párrafo suelto más largo que el límite se parte por palabras.
    finales: list[str] = []
    for trozo in trozos:
        palabras_trozo = trozo.split()
        if len(palabras_trozo) <= palabras * 1.5:
            finales.append(trozo)
            continue
        for i in range(0, len(palabras_trozo), palabras):
            finales.append(" ".join(palabras_trozo[i : i + palabras]))
    return finales


# --- extraccion sobre el documento entero -----------------------------------


@dataclass
class Lectura:
    """El resultado de interpretar un documento completo."""

    borrador: object  # Borrador; sin tipar para no importar en circulo
    trozos: int = 0
    clientes_detectados: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def clientes_mencionados(texto: str) -> list[str]:
    """Todos los clientes del catálogo que aparecen en el documento.

    Un inventario puede listar varios hospitales, y el flujo de captura archiva
    una visita a *un* cliente. Detectarlos antes evita el peor fallo posible
    aquí: repartir los equipos de un hospital en la ficha de otro.

    No se reutiliza el detector de `extract`, que está hecho para elegir *un*
    cliente y se queda callado cuando hay empate. Aquí interesa lo contrario:
    listarlos todos, aunque compartan palabras.
    """
    from . import normalize as N

    tokens = set(N.clave(texto).split())
    encontrados = []
    for ficha in N.catalogo_clientes():
        propios = set(N.tokens_distintivos(ficha["customer"]))
        if propios and propios <= tokens:
            encontrados.append(ficha["customer"])
    return sorted(encontrados)


def trozos_de(texto: str, cliente: str | None = None, varios: bool = False) -> list[str]:
    """Los trozos del documento, quedándose solo con los de un cliente.

    El filtrado va por líneas y solo cuando el documento habla de varios
    clientes. En un informe normal el nombre aparece una vez en la cabecera y
    filtrar por él dejaría fuera justo los equipos; en un inventario, en cambio,
    cada línea lleva su cliente y es la única forma de no mezclarlos.
    """
    from . import normalize as N

    if not cliente or not varios:
        return trocear(texto)

    objetivo = set(N.tokens_distintivos(cliente))
    propias = [l for l in texto.split("\n") if objetivo and objetivo <= set(N.clave(l).split())]
    if not propias:
        return trocear(texto)
    return trocear("\n\n".join(propias))


def analizar(texto: str, motor=None, fecha=None, cliente: str | None = None) -> Lectura:
    """Documento -> borrador, troceando y fusionando.

    Cada trozo pasa por el mismo extractor que una nota escrita a mano, y los
    resultados se funden. La fusión es el punto delicado: los trozos se solapan
    a propósito, así que el mismo equipo aparece en dos, y sumarlo duplicaría la
    flota. Por eso al fundir se toma la cantidad mayor y no la suma.
    """
    from .extract import extraer
    from .schema import Borrador, es_desconocido

    detectados = clientes_mencionados(texto)
    varios = len(detectados) > 1
    avisos: list[str] = []
    if varios and not cliente:
        # Sin elegir uno, los equipos de todos acabarían en la misma ficha, que
        # es exactamente el error que hace inservible una base instalada.
        avisos.append(
            "El documento menciona varios clientes ("
            + ", ".join(detectados)
            + "). Elige de cuál capturas ahora; los demás se registran por separado."
        )

    trozos = trozos_de(texto, cliente, varios)
    if not trozos:
        return Lectura(borrador=Borrador(), trozos=0, clientes_detectados=detectados, avisos=avisos)

    parciales = []
    for trozo in trozos:
        try:
            parciales.append(extraer(trozo, motor, fecha=fecha))
        except Exception as exc:  # noqa: BLE001 - un trozo malo no anula el resto
            avisos.append(f"Un fragmento no se pudo interpretar ({type(exc).__name__}).")

    if not parciales:
        return Lectura(borrador=Borrador(), trozos=len(trozos), clientes_detectados=detectados, avisos=avisos)

    fusionado = Borrador()
    for parcial in parciales:
        if es_desconocido(fusionado.customer) and not es_desconocido(parcial.customer):
            fusionado.customer = parcial.customer
        if es_desconocido(fusionado.city) and not es_desconocido(parcial.city):
            fusionado.city = parcial.city
        if es_desconocido(fusionado.country) and not es_desconocido(parcial.country):
            fusionado.country = parcial.country
        for equipo in parcial.items:
            _absorber(fusionado.items, equipo)
        if parcial.notes and parcial.notes not in fusionado.notes:
            fusionado.notes = f"{fusionado.notes} | {parcial.notes}".strip(" |")

    if cliente:
        fusionado.customer = cliente
    fusionado.location_source = "documento"
    return Lectura(
        borrador=fusionado,
        trozos=len(trozos),
        clientes_detectados=detectados,
        avisos=avisos,
    )


def _compatibles(a, b) -> bool:
    """Si dos grupos describen la misma flota.

    Sigue el mismo criterio que usa el almacén al consolidar: marcas o modelos
    conocidos y distintos son flotas distintas; un hueco no contradice a nadie.
    """
    from . import normalize as N
    from .schema import es_desconocido

    if a.modality != b.modality:
        return False
    for campo in ("brand", "model"):
        va, vb = getattr(a, campo), getattr(b, campo)
        if not es_desconocido(va) and not es_desconocido(vb) and N.clave(va) != N.clave(vb):
            return False
    if a.age_years and b.age_years and abs(a.age_years - b.age_years) > 1:
        return False
    return True


def _absorber(items: list, nuevo) -> None:
    """Mete `nuevo` en la lista, fundiéndolo con un gemelo si lo hay."""
    from .schema import es_desconocido

    for existente in items:
        if not _compatibles(existente, nuevo):
            continue
        # Cantidad: la mayor, nunca la suma. Los trozos se solapan y el mismo
        # equipo aparece dos veces; sumarlo inflaria la flota.
        if nuevo.quantity is not None:
            existente.quantity = max(existente.quantity or 0, nuevo.quantity)
        for campo in ("brand", "model"):
            if es_desconocido(getattr(existente, campo)) and not es_desconocido(getattr(nuevo, campo)):
                setattr(existente, campo, getattr(nuevo, campo))
        if existente.age_years is None and nuevo.age_years is not None:
            existente.age_years = nuevo.age_years
        return
    items.append(nuevo)
