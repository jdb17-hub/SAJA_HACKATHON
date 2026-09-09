"""Mapeo del Excel histórico para importar la semilla."""
_MAPEO_SEMILLA = {
    "Country": "country",
    "City": "city",
    "Customer / Hospital": "customer",
    "Observer": "observer",
    "Visit Date": "visit_date",
    "Modality": "modality",
    "Quantity": "quantity",
    "Dummy Brand": "brand",
    "Dummy Model": "model",
    "Approx. Age (Years)": "age_years",
    "Estimated Installation Year": "install_year",
    "Confidence": "confidence",
    "Status": "status",
    "Source": "source",
    "Voice Input Example": "raw_input",
    "Notes": "notes",
}


def _seguimiento(fila: dict) -> str:
    pregunta = (fila.get("Agent Follow-up Question") or "").strip()
    respuesta = (fila.get("Follow-up Answer") or "").strip()
    return f"Seguimiento: {pregunta} -> {respuesta}" if pregunta and respuesta else ""


def _int(valor) -> int:
    try:
        return int(float(str(valor).strip()))
    except (TypeError, ValueError):
        return 0


