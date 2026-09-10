"""Prueba de la ruta de documentos: fichero -> texto -> borrador.

Cubre lo que puede salir mal al leer un informe de visita:
  1. Que cada formato se abra y devuelva el texto que trae dentro.
  2. Que un PDF escaneado dé un error claro en vez de una página en blanco.
  3. Que un documento largo se trocee sin partir la relación número-equipo.
  4. Que un inventario con varios hospitales no mezcle sus equipos.

Los ficheros de prueba se generan aquí mismo, así que no hace falta traer nada.

    python scripts/test_documentos.py            # solo lectura y troceado
    python scripts/test_documentos.py --qvac     # además, extracción con QVAC
"""
from __future__ import annotations

import csv
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import documents  # noqa: E402
from src.documents import DocumentoNoSoportado, analizar, leer, trocear  # noqa: E402

INFORME = """Informe de visita técnica
Cliente: Hospital DemoCare Pacific
Ciudad: Panama City, Panamá
Técnico: Field User 04

Durante la revisión anual se recorrieron las áreas de imagen y urgencias.
El servicio de radiología cuenta con dos resonadores NovaMed, modelo NM-MR 700,
instalados hace aproximadamente siete años.

También se verificó un tomógrafo Aurelia Health de unos cinco años.

El área de urgencias dispone de cuatro ecógrafos HelixCare de unos dos años."""


def _docx(lineas: list[str]) -> bytes:
    """Un .docx mínimo pero válido, para no depender de Word ni de librerías."""
    def esc(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    cuerpo = "".join(
        f"<w:p><w:r><w:t xml:space='preserve'>{esc(l)}</w:t></w:r></w:p>" for l in lineas
    )
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{cuerpo}</w:body></w:document>"
    )
    tipos = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", tipos)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
    return buffer.getvalue()


def _xlsx() -> bytes:
    """Un inventario con dos hospitales, que es el caso peligroso."""
    import openpyxl

    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.title = "Inventario"
    hoja.append(["Cliente", "Ciudad", "Pais", "Modalidad", "Cantidad", "Marca", "Antiguedad"])
    for fila in [
        ["Clinica DemoCare Light", "Campinas", "Brazil", "CT", 2, "Orion Imaging", 11],
        ["Clinica DemoCare Light", "Campinas", "Brazil", "Ultrasound", 5, "HelixCare", 4],
        ["Hospital DemoCare Park", "Buenos Aires", "Argentina", "MR", 1, "HelixCare", 10],
        ["Hospital DemoCare Park", "Buenos Aires", "Argentina", "CT", 3, "Zenith MedTech", 7],
    ]:
        hoja.append(fila)
    buffer = io.BytesIO()
    libro.save(buffer)
    return buffer.getvalue()


def _csv() -> bytes:
    buffer = io.StringIO()
    escritor = csv.writer(buffer)
    escritor.writerow(["Cliente", "Modalidad", "Cantidad", "Marca", "Antiguedad"])
    escritor.writerow(["Instituto DemoCare Lima", "CT", 2, "Orion Imaging", 12])
    return buffer.getvalue().encode("utf-8")


def _pdfs() -> dict[str, bytes]:
    """PDF con texto y PDF "escaneado". Se salta si no hay con qué generarlos."""
    try:
        from fpdf import FPDF
    except ImportError:
        return {}

    con_texto = FPDF()
    con_texto.add_page()
    con_texto.set_font("Helvetica", size=11)
    for linea in INFORME.split("\n"):
        # El generador de PDF de prueba solo maneja latin-1.
        con_texto.multi_cell(0, 6, linea.encode("latin-1", "replace").decode("latin-1") or " ")

    vacio = FPDF()
    vacio.add_page()
    return {
        "informe.pdf": _bytes_pdf(con_texto),
        "escaneado.pdf": _bytes_pdf(vacio),
    }


def _bytes_pdf(pdf) -> bytes:
    """PyFPDF 1.x devuelve texto latin-1; fpdf2 devuelve bytes. Sirven los dos."""
    try:
        salida = pdf.output(dest="S")
    except TypeError:
        salida = pdf.output()
    return salida.encode("latin-1") if isinstance(salida, str) else bytes(salida)


def probar_lectura() -> tuple[int, int]:
    print("=" * 78)
    print("1. LECTURA DE FORMATOS (sin modelo)")
    print("=" * 78)
    ficheros: dict[str, bytes] = {
        "informe.txt": INFORME.encode("utf-8"),
        "acta.docx": _docx(INFORME.split("\n")),
        "inventario.xlsx": _xlsx(),
        "inventario.csv": _csv(),
        **_pdfs(),
    }

    aciertos = total = 0
    for nombre, datos in sorted(ficheros.items()):
        total += 1
        esperado_error = nombre == "escaneado.pdf"
        try:
            doc = leer(nombre, datos)
            if esperado_error:
                print(f"  [FAIL] {nombre:20} debería haber avisado de que no trae texto")
                continue
            ok = doc.palabras > 5
            aciertos += ok
            print(
                f"  [{'OK ' if ok else 'FAIL'}] {nombre:20} {doc.palabras:4} palabras · "
                f"{len(trocear(doc.texto))} trozo(s)"
            )
        except DocumentoNoSoportado as exc:
            aciertos += esperado_error
            marca = "OK " if esperado_error else "FAIL"
            print(f"  [{marca}] {nombre:20} {exc}")

    if "informe.pdf" not in ficheros:
        print("  (sin fpdf instalado: los PDF de prueba no se generaron)")

    # Un formato que no se sabe leer tiene que decirlo, no reventar.
    total += 1
    try:
        leer("informe.doc", b"x" * 100)
        print("  [FAIL] un .doc antiguo debería dar un error explicado")
    except DocumentoNoSoportado as exc:
        aciertos += 1
        print(f"  [OK ] informe.doc         {exc}")
    return aciertos, total


def probar_troceado() -> tuple[int, int]:
    print()
    print("=" * 78)
    print("2. TROCEADO DE DOCUMENTOS LARGOS")
    print("=" * 78)
    largo = "\n\n".join([INFORME] * 12)
    trozos = trocear(largo)
    palabras = [len(t.split()) for t in trozos]

    pruebas = [
        (len(trozos) > 1, f"un documento de {len(largo.split())} palabras se parte ({len(trozos)} trozos)"),
        (max(palabras) <= documents.PALABRAS_POR_TROZO * 1.6, f"ningún trozo se dispara (máx {max(palabras)})"),
        (len(trocear(INFORME)) == 1, "un informe corto se queda en un solo trozo"),
        (trocear("") == [], "un texto vacío no produce trozos"),
    ]
    for ok, descripcion in pruebas:
        print(f"  [{'OK ' if ok else 'FAIL'}] {descripcion}")
    return sum(1 for ok, _ in pruebas if ok), len(pruebas)


def probar_separacion_clientes() -> tuple[int, int]:
    print()
    print("=" * 78)
    print("3. INVENTARIO CON VARIOS CLIENTES (sin modelo)")
    print("=" * 78)
    doc = leer("inventario.xlsx", _xlsx())
    detectados = documents.clientes_mencionados(doc.texto)

    esperados = ["Clinica DemoCare Light", "Hospital DemoCare Park"]
    pruebas = [(sorted(detectados) == esperados, f"detecta los dos clientes: {detectados}")]

    # Cada cliente se queda solo con sus filas; mezclarlas es el peor fallo aquí.
    for cliente, modalidades in [
        ("Clinica DemoCare Light", {"CT", "Ultrasound"}),
        ("Hospital DemoCare Park", {"CT", "MR"}),
    ]:
        trozos = documents.trozos_de(doc.texto, cliente, varios=True)
        texto = " ".join(trozos)
        otros = [c for c in esperados if c != cliente]
        limpio = all(otro.split()[-1] not in texto for otro in otros)
        pruebas.append((limpio, f"«{cliente}» no arrastra filas de {otros[0]}"))

    for ok, descripcion in pruebas:
        print(f"  [{'OK ' if ok else 'FAIL'}] {descripcion}")
    return sum(1 for ok, _ in pruebas if ok), len(pruebas)


def probar_extraccion_qvac() -> tuple[int, int]:
    print()
    print("=" * 78)
    print("4. EXTRACCIÓN CON QVAC (en el dispositivo)")
    print("=" * 78)
    from src.qvac_engine import obtener_motor

    motor = obtener_motor()
    if not motor.iniciar().listo:
        print(f"  QVAC no disponible: {motor.estado.error}")
        return 0, 1

    aciertos = total = 0
    doc = leer("acta.docx", _docx(INFORME.split("\n")))
    lectura = analizar(doc.texto, motor)
    borrador = lectura.borrador
    equipos = {e.modality.value: e.quantity for e in borrador.items}

    for ok, descripcion in [
        (borrador.customer == "Hospital DemoCare Pacific", f"cliente: {borrador.customer}"),
        (equipos.get("MR") == 2, f"MR x{equipos.get('MR')}"),
        (equipos.get("CT") == 1, f"CT x{equipos.get('CT')}"),
        (equipos.get("Ultrasound") == 4, f"Ecógrafos x{equipos.get('Ultrasound')}"),
    ]:
        total += 1
        aciertos += ok
        print(f"  [{'OK ' if ok else 'FAIL'}] informe Word — {descripcion}")

    # El inventario filtrado por cliente no debe traer equipos del otro.
    inventario = leer("inventario.xlsx", _xlsx())
    lectura = analizar(inventario.texto, motor, cliente="Hospital DemoCare Park")
    modalidades = {e.modality.value for e in lectura.borrador.items}
    total += 1
    ok = modalidades == {"MR", "CT"} and lectura.borrador.customer == "Hospital DemoCare Park"
    aciertos += ok
    print(
        f"  [{'OK ' if ok else 'FAIL'}] inventario filtrado — "
        f"{lectura.borrador.customer}: {sorted(modalidades)}"
    )

    motor.cerrar()
    return aciertos, total


def main() -> None:
    aciertos = total = 0
    for funcion in (probar_lectura, probar_troceado, probar_separacion_clientes):
        a, t = funcion()
        aciertos, total = aciertos + a, total + t

    if "--qvac" in sys.argv:
        a, t = probar_extraccion_qvac()
        aciertos, total = aciertos + a, total + t
    else:
        print("\n(pasa --qvac para probar además la extracción con el modelo)")

    print(f"\nRESULTADO: {aciertos}/{total}")
    sys.exit(0 if aciertos == total else 1)


if __name__ == "__main__":
    main()
