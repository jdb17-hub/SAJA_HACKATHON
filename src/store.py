"""Repositorio SQLite. Una fila por grupo de equipos, con las mismas columnas
que la hoja `Dummy Installed Base`, para que el export sea directo."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .config import DATA_DIR, DB_PATH
from .schema import Observacion

ESQUEMA_SQL = """
CREATE TABLE IF NOT EXISTS observaciones (
    observation_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    country          TEXT NOT NULL DEFAULT 'Unknown',
    city             TEXT NOT NULL DEFAULT 'Unknown',
    customer         TEXT NOT NULL DEFAULT 'Unknown',
    observer         TEXT NOT NULL DEFAULT 'Unknown',
    visit_date       TEXT NOT NULL DEFAULT '',
    modality         TEXT NOT NULL DEFAULT 'Unknown',
    quantity         INTEGER NOT NULL DEFAULT 0,
    brand            TEXT NOT NULL DEFAULT 'Unknown',
    model            TEXT NOT NULL DEFAULT 'Unknown',
    age_years        INTEGER NOT NULL DEFAULT 0,
    install_year     INTEGER,
    confidence       TEXT NOT NULL DEFAULT 'Low',
    confidence_score INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'Unknown',
    source           TEXT NOT NULL DEFAULT 'Text',
    raw_input        TEXT NOT NULL DEFAULT '',
    notes            TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_cliente ON observaciones(customer);
CREATE INDEX IF NOT EXISTS idx_pais ON observaciones(country);
CREATE INDEX IF NOT EXISTS idx_modalidad ON observaciones(modality);
"""

COLUMNAS = [
    "country", "city", "customer", "observer", "visit_date", "modality",
    "quantity", "brand", "model", "age_years", "install_year", "confidence",
    "confidence_score", "status", "source", "raw_input", "notes", "created_at",
]


def conectar() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(ESQUEMA_SQL)
    return con


def guardar(obs: Observacion) -> int:
    obs.created_at = obs.created_at or datetime.now().isoformat(timespec="seconds")
    obs.visit_date = obs.visit_date or date.today().isoformat()
    if obs.age_years > 0 and obs.install_year is None:
        obs.install_year = date.today().year - obs.age_years
    con = conectar()
    with con:
        cur = con.execute(
            f"INSERT INTO observaciones ({','.join(COLUMNAS)}) "
            f"VALUES ({','.join('?' * len(COLUMNAS))})",
            [getattr(obs, c) for c in COLUMNAS],
        )
    con.close()
    return int(cur.lastrowid)


def actualizar(observation_id: int, **campos) -> None:
    if not campos:
        return
    asignaciones = ", ".join(f"{k} = ?" for k in campos)
    con = conectar()
    with con:
        con.execute(
            f"UPDATE observaciones SET {asignaciones} WHERE observation_id = ?",
            [*campos.values(), observation_id],
        )
    con.close()


def borrar(observation_id: int) -> None:
    con = conectar()
    with con:
        con.execute("DELETE FROM observaciones WHERE observation_id = ?", (observation_id,))
    con.close()


def todas() -> list[dict]:
    con = conectar()
    filas = [dict(r) for r in con.execute("SELECT * FROM observaciones ORDER BY observation_id")]
    con.close()
    return filas


def por_cliente(customer: str) -> list[dict]:
    con = conectar()
    filas = [
        dict(r)
        for r in con.execute(
            "SELECT * FROM observaciones WHERE customer = ? ORDER BY visit_date DESC", (customer,)
        )
    ]
    con.close()
    return filas


def clientes() -> list[str]:
    con = conectar()
    nombres = [r[0] for r in con.execute("SELECT DISTINCT customer FROM observaciones ORDER BY customer")]
    con.close()
    return nombres


def contar() -> int:
    con = conectar()
    n = con.execute("SELECT COUNT(*) FROM observaciones").fetchone()[0]
    con.close()
    return int(n)


def vaciar() -> None:
    con = conectar()
    with con:
        con.execute("DELETE FROM observaciones")
        con.execute("DELETE FROM sqlite_sequence WHERE name = 'observaciones'")
    con.close()


# --- semilla ----------------------------------------------------------------

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


def sembrar(forzar: bool = False) -> int:
    """Carga las 20 observaciones de referencia del Excel.

    Sin ellas la app arranca vacia y no se puede ensenar la vista agregada;
    con ellas, la primera captura del usuario ya cae sobre una base real.
    """
    ruta = DATA_DIR / "seed_installed_base.json"
    if not ruta.exists():
        return 0
    if contar() > 0 and not forzar:
        return 0
    if forzar:
        vaciar()

    filas = json.loads(ruta.read_text(encoding="utf-8"))
    insertadas = 0
    for fila in filas:
        datos = {destino: fila.get(origen, "") for origen, destino in _MAPEO_SEMILLA.items()}
        obs = Observacion(
            **{
                **datos,
                "quantity": _int(datos.get("quantity")),
                "age_years": _int(datos.get("age_years")),
                "install_year": _int(datos.get("install_year")) or None,
                "source": "Seed",
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        obs.notes = " | ".join(
            p for p in [fila.get("Notes", ""), _seguimiento(fila)] if p
        )
        guardar(obs)
        insertadas += 1

    recalcular_confianza()
    return insertadas


def _seguimiento(fila: dict) -> str:
    pregunta = (fila.get("Agent Follow-up Question") or "").strip()
    respuesta = (fila.get("Follow-up Answer") or "").strip()
    return f"Seguimiento: {pregunta} -> {respuesta}" if pregunta and respuesta else ""


def _int(valor) -> int:
    try:
        return int(float(str(valor).strip()))
    except (TypeError, ValueError):
        return 0


def recalcular_confianza() -> None:
    """Recalcula el puntaje de todas las filas.

    Se hace en bloque porque el puntaje depende de las demas observaciones
    (confirmaciones independientes), asi que guardar una fila nueva puede subir
    la confianza de otra ya existente.
    """
    from .confidence import calcular_confianza

    filas = todas()
    con = conectar()
    with con:
        for fila in filas:
            puntaje, etiqueta = calcular_confianza(fila, filas)
            con.execute(
                "UPDATE observaciones SET confidence_score = ?, confidence = ? WHERE observation_id = ?",
                (puntaje, etiqueta, fila["observation_id"]),
            )
    con.close()


def exportar_csv(ruta: Path) -> Path:
    import csv

    filas = todas()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", newline="", encoding="utf-8-sig") as f:
        escritor = csv.DictWriter(f, fieldnames=["observation_id", *COLUMNAS])
        escritor.writeheader()
        escritor.writerows(filas)
    return ruta
