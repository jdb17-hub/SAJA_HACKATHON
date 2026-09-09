"""Observaciones que se contradicen.

Un duplicado es que dos personas cuenten lo mismo; un conflicto es que cuenten
cosas distintas del mismo equipo. El documento del reto pide detectar ambos, y
son problemas diferentes: el duplicado infla la cuenta, el conflicto la hace
dudosa. Y un conflicto no se resuelve solo, hace falta que alguien decida.

Mientras un conflicto sigue abierto, lo honesto es enseñarlo: una base que
presenta un número discutido como si fuera firme es peor que una que admite la
duda.
"""
from __future__ import annotations

from datetime import date

from . import normalize as N
from . import store
from .confidence import dias_desde
from .schema import es_desconocido

# Diferencia de años a partir de la cual dos observaciones de la misma flota
# dejan de ser compatibles. Por debajo cabe en el margen de "parece de unos...".
TOLERANCIA_EDAD = 3


def _misma_flota(a: dict, b: dict) -> bool:
    """Si las dos filas hablan del mismo grupo de equipos.

    Marcas distintas y conocidas significan flotas distintas: un hospital puede
    tener perfectamente dos tomógrafos de fabricantes diferentes, y tratarlo
    como conflicto llenaría la pantalla de falsos positivos.
    """
    if N.clave(a.get("customer", "")) != N.clave(b.get("customer", "")):
        return False
    if a.get("modality") != b.get("modality"):
        return False
    marca_a, marca_b = a.get("brand", ""), b.get("brand", "")
    if not es_desconocido(marca_a) and not es_desconocido(marca_b):
        return N.clave(marca_a) == N.clave(marca_b)
    return True


def _desacuerdos(a: dict, b: dict) -> list[str]:
    motivos = []
    cant_a, cant_b = int(a.get("quantity") or 0), int(b.get("quantity") or 0)
    if cant_a and cant_b and cant_a != cant_b:
        motivos.append(f"cantidad: {cant_a} frente a {cant_b}")

    edad_a, edad_b = int(a.get("age_years") or 0), int(b.get("age_years") or 0)
    if edad_a and edad_b and abs(edad_a - edad_b) > TOLERANCIA_EDAD:
        motivos.append(f"antigüedad: {edad_a} años frente a {edad_b}")

    marca_a, marca_b = a.get("brand", ""), b.get("brand", "")
    modelo_a, modelo_b = a.get("model", ""), b.get("model", "")
    if (
        not es_desconocido(marca_a) and not es_desconocido(marca_b)
        and N.clave(marca_a) == N.clave(marca_b)
        and not es_desconocido(modelo_a) and not es_desconocido(modelo_b)
        and N.clave(modelo_a) != N.clave(modelo_b)
    ):
        motivos.append(f"modelo: {modelo_a} frente a {modelo_b}")
    return motivos


def detectar(filas: list[dict]) -> list[dict]:
    """Pares de observaciones que se contradicen, del más grave al menos.

    Se ignoran los pares del mismo observador: que alguien registre dos veces
    números distintos es una corrección suya, no una discrepancia entre fuentes.
    """
    conflictos = []
    for i, a in enumerate(filas):
        for b in filas[i + 1 :]:
            if not _misma_flota(a, b):
                continue
            observador_a = (a.get("observer") or "").strip().lower()
            observador_b = (b.get("observer") or "").strip().lower()
            if observador_a and observador_a == observador_b:
                continue
            motivos = _desacuerdos(a, b)
            if not motivos:
                continue
            conflictos.append(
                {
                    "a": a,
                    "b": b,
                    "motivos": motivos,
                    "gravedad": _gravedad(a, b, motivos),
                    "clave": f"{a['observation_id']}-{b['observation_id']}",
                }
            )
    return sorted(conflictos, key=lambda c: -c["gravedad"])


def _gravedad(a: dict, b: dict, motivos: list[str]) -> int:
    """Cuánto importa este conflicto.

    Pesa más el desacuerdo en cantidad (mueve el tamaño de la oportunidad) y
    cuantos más equipos haya en juego, más urge resolverlo.
    """
    puntos = 0
    for motivo in motivos:
        if motivo.startswith("cantidad"):
            puntos += 40
        elif motivo.startswith("antigüedad"):
            puntos += 25
        else:
            puntos += 10
    puntos += min(int(a.get("quantity") or 0), int(b.get("quantity") or 0)) * 2
    return puntos


def esta_resuelto(fila: dict) -> bool:
    return MARCA_RESUELTO in (fila.get("notes") or "")


MARCA_RESUELTO = "[conflicto revisado]"


def abiertos(filas: list[dict]) -> list[dict]:
    """Conflictos que nadie ha revisado todavía."""
    return [
        c for c in detectar(filas)
        if not (esta_resuelto(c["a"]) and esta_resuelto(c["b"]))
    ]


# --- resolución -------------------------------------------------------------


def resolver_quedandose_con(ganadora_id: int, descartada_id: int, observador: str) -> None:
    """Se acepta una de las dos versiones y se descarta la otra.

    La observación que se va queda registrada en la nota de la que se queda: sin
    ese rastro, mañana nadie sabría que hubo una discrepancia ni qué se decidió.
    """
    filas = {f["observation_id"]: f for f in store.todas()}
    ganadora, descartada = filas.get(ganadora_id), filas.get(descartada_id)
    if not ganadora or not descartada:
        return

    rastro = (
        f"{MARCA_RESUELTO} {observador} el {date.today().isoformat()}: se descarta la "
        f"observación #{descartada_id} de {descartada.get('observer')} "
        f"({descartada.get('quantity')} × {descartada.get('modality')}, "
        f"{descartada.get('brand')})"
    )
    nota = (ganadora.get("notes") or "").strip()
    store.actualizar(
        ganadora_id,
        notes=f"{nota} | {rastro}".strip(" |"),
        status="Confirmed",
        visit_date=date.today().isoformat(),
    )
    store.borrar(descartada_id)
    store.recalcular_confianza()


def marcar_revisado(ids: list[int], observador: str, comentario: str = "") -> None:
    """Se deja constancia de que alguien lo miró y decidió no cambiar nada.

    Hace falta porque hay conflictos legítimos: dos flotas parecidas, o una
    ampliación entre visitas. Sin esta salida, el mismo aviso reaparecería para
    siempre y la lista dejaría de mirarse.
    """
    filas = {f["observation_id"]: f for f in store.todas()}
    marca = f"{MARCA_RESUELTO} {observador} el {date.today().isoformat()}"
    if comentario.strip():
        marca += f": {comentario.strip()}"
    for observation_id in ids:
        fila = filas.get(observation_id)
        if not fila:
            continue
        nota = (fila.get("notes") or "").strip()
        store.actualizar(observation_id, notes=f"{nota} | {marca}".strip(" |"))


def resumen(fila: dict) -> str:
    """Una línea que describe la observación, para poder compararlas de un vistazo."""
    partes = [f"{fila.get('quantity')} × {fila.get('modality')}"]
    if not es_desconocido(fila.get("brand")):
        partes.append(str(fila.get("brand")))
    if int(fila.get("age_years") or 0) > 0:
        partes.append(f"{fila.get('age_years')} años")
    dias = dias_desde(fila)
    origen = f"{fila.get('observer')} · {fila.get('visit_date')}"
    if dias is not None:
        origen += f" (hace {dias} días)"
    return " · ".join(partes) + f" — {origen}"
