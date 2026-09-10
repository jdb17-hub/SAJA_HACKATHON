"""Prueba de extraccion end-to-end contra QVAC, sin interfaz.

Corre los 10 prompts de voz del Excel mas variantes en espanol y compara con la
salida esperada. Sirve para medir el efecto de cambiar de modelo o de prompt.

    python scripts/test_extraccion.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.extract import extraer, extraer_solo_reglas  # noqa: E402
from src.qvac_engine import obtener_motor  # noqa: E402

# (nota, cliente esperado, [(modalidad, cantidad), ...])
CASOS: list[tuple[str, str, list[tuple[str, int]]]] = [
    # --- los 10 prompts de la hoja "Voice Test Prompts"
    ("I am at Hospital DemoCare Pacific in Panama. They have two MR systems and one CT.",
     "Hospital DemoCare Pacific", [("MR", 2), ("CT", 1)]),
    ("At Hospital DemoCare Horizon I saw three MR systems. Two seem old and one looks much newer.",
     "Hospital DemoCare Horizon", [("MR", 3)]),
    ("Clinica DemoCare Light has two CT scanners, both Orion Imaging, around eleven years old.",
     "Clinica DemoCare Light", [("CT", 2)]),
    ("Centro Medico DemoCare Valley has one MR and two CTs. I do not know the brands.",
     "Centro Medico DemoCare Valley", [("MR", 1), ("CT", 2)]),
    ("Hospital DemoCare North has about six ultrasound units, mostly new.",
     "Hospital DemoCare North", [("Ultrasound", 6)]),
    ("Clinica DemoCare Andes has one very old CT and two MR systems from the same manufacturer.",
     "Clinica DemoCare Andes", [("CT", 1), ("MR", 2)]),
    ("Hospital DemoCare Park has one MR, maybe ten years old, plus three CT scanners.",
     "Hospital DemoCare Park", [("MR", 1), ("CT", 3)]),
    ("Clinica DemoCare Central has many ultrasound systems, maybe eight, all Aurelia Health.",
     "Clinica DemoCare Central", [("Ultrasound", 8)]),
    ("Instituto DemoCare Lima has two old CTs and one recently installed MR.",
     "Instituto DemoCare Lima", [("CT", 2), ("MR", 1)]),
    ("Hospital DemoCare Metro North has two MR systems. I know the brand is Aurelia Health but not the model.",
     "Hospital DemoCare Metro North", [("MR", 2)]),
    # --- las mismas situaciones dichas en espanol
    ("Estoy en Hospital DemoCare Pacific, en Panama. Tienen dos resonadores y un tomografo. "
     "Uno de los resonadores parece de unos ocho anos.",
     "Hospital DemoCare Pacific", [("MR", 2), ("CT", 1)]),
    ("En Hospital DemoCare Horizon vi tres resonadores. Dos se ven viejos y uno mucho mas nuevo.",
     "Hospital DemoCare Horizon", [("MR", 3)]),
    ("Clinica DemoCare Light tiene dos tomografos Orion Imaging de unos once anos.",
     "Clinica DemoCare Light", [("CT", 2)]),
    ("Centro Medico DemoCare Valley tiene un MR y dos CTs. No se las marcas.",
     "Centro Medico DemoCare Valley", [("MR", 1), ("CT", 2)]),
    ("Hospital DemoCare Park tiene un resonador de unos diez anos y tres tomografos Zenith MedTech.",
     "Hospital DemoCare Park", [("MR", 1), ("CT", 3)]),
    ("Clinica DemoCare Central tiene muchos ecografos, quizas ocho, todos Aurelia Health.",
     "Clinica DemoCare Central", [("Ultrasound", 8)]),
    # "Ecografo" y "ultrasonido" son la misma modalidad: cinco equipos, no dos
    # flotas. Y de aqui salia ademas un CT que nadie menciono.
    ("Hospital DemoCare Pines tiene tres ecografos y dos ultrasonidos.",
     "Hospital DemoCare Pines", [("Ultrasound", 5)]),
]


# Notas donde el sitio no se identifica. Lo correcto es dejarlo vacio y
# preguntar: inventar un cliente o una ciudad ensucia la base con filas que
# nadie podra reconciliar despues, y eso es peor que un hueco.
CASOS_SIN_LUGAR: list[tuple[str, str]] = [
    ("Estoy en el hospital, tienen dos resonadores y un tomografo.", "tipo de centro, sin nombre"),
    ("Estoy en la clinica de aqui al lado, vi tres ecografos.", "descripcion, no nombre"),
    ("Tienen dos resonadores y un tomografo.", "no se menciona ningun sitio"),
    ("Estoy en DemoCare, tienen dos resonadores.", "ambiguo: lo comparten 13 clientes"),
    ("Vi dos tomografos en el centro medico.", "generico"),
]

# Clientes que no estan en el catalogo. Estos SI se guardan: el reto pide crear
# un candidato nuevo cuando el nombre no se reconoce.
CASOS_CLIENTE_NUEVO: list[tuple[str, str]] = [
    ("Estoy en el Hospital Santo Tomas en Panama, tienen dos resonadores.", "Hospital Santo Tomas"),
    ("En la Clinica San Fernando vi tres ecografos.", "Clinica San Fernando"),
]


def evaluar_sin_lugar(extractor) -> tuple[int, int]:
    """Que no se invente ni cliente, ni ciudad, ni pais."""
    print()
    print("=" * 78)
    print("SITIO NO IDENTIFICABLE (no debe inventar nada)")
    print("=" * 78)
    aciertos = 0
    for nota, motivo in CASOS_SIN_LUGAR:
        b = extractor(nota)
        limpio = b.customer == "Unknown" and b.city == "Unknown" and b.country == "Unknown"
        aciertos += limpio
        print(f"[{'OK ' if limpio else 'FAIL'}] {nota[:56]}...  ({motivo})")
        if not limpio:
            print(f"        invento: cliente={b.customer!r} ciudad={b.city!r} pais={b.country!r}")
    return aciertos, len(CASOS_SIN_LUGAR)


def evaluar_cliente_nuevo(extractor) -> tuple[int, int]:
    """Un cliente que no esta en el catalogo se registra igual, no se descarta."""
    print()
    print("=" * 78)
    print("CLIENTE NUEVO (debe guardarse)")
    print("=" * 78)
    aciertos = 0
    for nota, esperado in CASOS_CLIENTE_NUEVO:
        b = extractor(nota)
        ok = b.customer == esperado
        aciertos += ok
        print(f"[{'OK ' if ok else 'FAIL'}] {nota[:56]}...")
        if not ok:
            print(f"        cliente: {b.customer!r} != {esperado!r}")
    return aciertos, len(CASOS_CLIENTE_NUEVO)


def totales(borrador) -> dict[str, int]:
    """Unidades por modalidad, sumando los grupos que el agente haya partido."""
    suma: dict[str, int] = {}
    for eq in borrador.items:
        suma[eq.modality.value] = suma.get(eq.modality.value, 0) + (eq.quantity or 0)
    return suma


def evaluar(nombre: str, extractor) -> tuple[int, int, float]:
    aciertos_cliente = aciertos_equipos = 0
    inicio = time.time()
    print(f"\n{'=' * 78}\n{nombre}\n{'=' * 78}")
    for nota, cliente_esperado, equipos_esperados in CASOS:
        borrador = extractor(nota)
        obtenido = totales(borrador)
        esperado = dict(equipos_esperados)

        ok_cliente = borrador.customer == cliente_esperado
        ok_equipos = obtenido == esperado
        aciertos_cliente += ok_cliente
        aciertos_equipos += ok_equipos

        marca = "OK " if (ok_cliente and ok_equipos) else "FAIL"
        print(f"[{marca}] {nota[:62]}...")
        if not ok_cliente:
            print(f"        cliente: {borrador.customer!r} != {cliente_esperado!r}")
        if not ok_equipos:
            print(f"        equipos: {obtenido} != {esperado}")
        extras = [
            f"{e.modality.value}:{e.brand}" for e in borrador.items if e.brand != "Unknown"
        ]
        if extras:
            print(f"        marcas detectadas: {', '.join(extras)}")
    return aciertos_cliente, aciertos_equipos, time.time() - inicio


def main() -> None:
    total = len(CASOS)

    c_reglas, e_reglas, t_reglas = evaluar("SOLO REGLAS (sin modelo)", extraer_solo_reglas)

    motor = obtener_motor()
    print("\nCargando QVAC...")
    estado = motor.iniciar()
    if not estado.listo:
        print(f"QVAC no disponible: {estado.error}")
        sys.exit(1)
    print(f"Modelo {estado.llm_model} cargado en {estado.segundos_carga:.1f}s")

    def hibrido(nota: str):
        return extraer(nota, motor, estricto=True)

    c_hib, e_hib, t_hib = evaluar("HIBRIDO (reglas + QVAC en el dispositivo)", hibrido)
    sl_ok, sl_total = evaluar_sin_lugar(hibrido)
    cn_ok, cn_total = evaluar_cliente_nuevo(hibrido)

    print(f"\n{'=' * 78}\nRESUMEN ({total} casos)\n{'=' * 78}")
    print(f"{'':<28}{'cliente':>10}{'equipos':>10}{'tiempo':>12}")
    print(f"{'solo reglas':<28}{c_reglas:>7}/{total}{e_reglas:>7}/{total}{t_reglas:>10.1f}s")
    print(f"{'hibrido (QVAC)':<28}{c_hib:>7}/{total}{e_hib:>7}/{total}{t_hib:>10.1f}s")
    print(f"{'no inventa sitio':<28}{sl_ok:>7}/{sl_total}")
    print(f"{'guarda cliente nuevo':<28}{cn_ok:>7}/{cn_total}")
    print(f"\nLatencia media por nota: {t_hib / total:.2f}s · {motor.estado.inferencias} inferencias")
    motor.cerrar()
    if c_hib != total or e_hib != total or sl_ok != sl_total or cn_ok != cn_total:
        sys.exit(1)


if __name__ == "__main__":
    main()
