"""Convierte el Excel del hackathon en los ficheros semilla del prototipo.

Uso:  python scripts/import_excel.py "ruta/al/Dummy_Installed_Base_Hackathon.xlsx"
"""
import json
import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def sheet_rows(ws):
    rows = [["" if c is None else str(c).strip() for c in r] for r in ws.iter_rows(values_only=True)]
    header, *body = rows
    return [dict(zip(header, r)) for r in body if any(r)]


def main(xlsx_path: str) -> None:
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    DATA.mkdir(exist_ok=True)

    base = sheet_rows(wb["Dummy Installed Base"])
    (DATA / "seed_installed_base.json").write_text(
        json.dumps(base, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    refs: dict[str, list[str]] = {}
    for row in sheet_rows(wb["Dummy Reference Lists"]):
        refs.setdefault(row["Category"], []).append(row["Dummy Value"])
    (DATA / "reference_lists.json").write_text(
        json.dumps(refs, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    (DATA / "voice_test_prompts.json").write_text(
        json.dumps(sheet_rows(wb["Voice Test Prompts"]), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    (DATA / "agent_question_logic.json").write_text(
        json.dumps(sheet_rows(wb["Agent Question Logic"]), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    print(f"{len(base)} observaciones semilla")
    print({k: len(v) for k, v in refs.items()})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "Downloads" / "Dummy_Installed_Base_Hackathon.xlsx"))
