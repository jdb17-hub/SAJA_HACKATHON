# Cambios y mejoras del proyecto — Alicia 1

Fecha: 9 de septiembre de 2026.
Rama de trabajo: `Mejora-de-leer-documentos`.

## Alcance

Dos bloques. El primero incorpora a esta rama el trabajo que se había quedado
en una carpeta local sin subir: mapa geográfico, vista de conflictos, panorama
por modalidad y dos analíticas del reto. El segundo añade la captura desde
documentos y simplifica el guardado.

En los ficheros compartidos se conservó siempre la versión de esta rama. Los
tests existentes se mantienen en verde.

---

## 1. Integración del trabajo pendiente

Commit `fa00a76`. Ficheros nuevos: `src/geo.py`, `src/insights.py`,
`src/conflicts.py`. Dependencia nueva: `pydeck`.

| Área | Comportamiento nuevo |
|---|---|
| Mapa | Pestaña con navegación Región → País → Ciudad → Cliente → Equipos, como pide el documento del reto. El tamaño del punto es la cantidad de unidades; el color señala flotas en ventana de renovación. |
| Coordenadas | Tabla local en `src/geo.py`. No se geocodifica contra ningún servicio: enviar nombres de clientes fuera del equipo contradice la premisa del proyecto, y además dejaría de funcionar sin conexión. |
| Cliente | La ficha abre con un panorama por modalidad —tipo de equipo, cantidad, edad, marcas y confianza— y debajo quedan los grupos individuales. |
| Edad | Se presenta en rango y no en promedio: `Mixta (3–9 años)`. Una flota comprada en tandas distintas no tiene una sola edad, y promediarla esconde que hay equipos viejos. |
| Analíticas | Se añaden las dos que faltaban de las ocho que enumera el reto: clientes con información incompleta y sitios actualizados recientemente. |
| Conflictos | Pestaña que lee los grupos cuya evidencia el almacén marcó como incompatible y explica en qué difieren. |
| Motor | Diagrama del recorrido Capturar → Entender → Estructurar → Validar → Guardar → Visualizar → Generar valor, con el fichero que hace cada etapa. |
| Guardado | El aviso indica cuánto tardó la captura, de la nota al dato guardado. |

`geo.py` e `insights.py` funcionan sin modificaciones sobre el almacén nuevo.
`conflicts.py` se reescribió: la versión anterior comparaba filas planas y
resolvía editando o borrando, y este almacén es de solo añadir y ya registra el
veredicto de compatibilidad en cada evidencia.

### Mosaicos del mapa

La cartografía de fondo se descarga de internet. Es un recurso de interfaz y no
inferencia, así que no afecta a la regla del reto. Sin conexión los clientes se
siguen situando, sobre fondo liso.

---

## 2. Captura desde documentos

Fichero nuevo: `src/documents.py`. Dependencia nueva: `pypdf`.

Tercera pestaña de entrada, junto a Escribir y Dictar. Formatos admitidos: PDF,
Word `.docx`, Excel, CSV y texto plano.

Leer el fichero es análisis de formato, no inferencia: se abre y se extrae el
texto que ya contiene. A partir de ahí sigue el mismo camino que una nota
escrita a mano, de modo que no aparece una segunda forma de equivocarse.

| Área | Comportamiento |
|---|---|
| Formatos | PDF con `pypdf`; `.docx` leyendo su XML interno, sin dependencias añadidas; Excel y CSV convertidos a líneas `Columna: valor`, que el extractor interpreta igual que una frase. |
| Documentos largos | Se trocean por párrafos con solape y los resultados se funden. Al fundir se toma la cantidad mayor y nunca la suma: el solape repite equipos y sumarlos duplicaría la flota. |
| Varios clientes | Un inventario puede listar varios hospitales. Se detectan todos, se avisa y se elige de cuál se captura; el filtrado va por líneas para que ninguno arrastre filas de otro. |
| Antigüedad en hojas | Cuando la columna se llama «Antigüedad» y el valor es un número, se le añade la unidad al convertir. El extractor solo acepta una edad nombrada en años, y en una hoja la unidad está en la cabecera. Un valor de cuatro cifras se trata como año de instalación, no como antigüedad. |
| Corrección | El texto extraído se muestra editable antes de extraer, y sigue siéndolo en la pantalla de revisión con un botón para reprocesar. |
| Sin OCR | No se lee texto de imágenes ni de PDF escaneados. Un escaneo es una imagen y requeriría un modelo de visión. Cuando un PDF no trae texto seleccionable se indica explícitamente; si es mixto, se leen las páginas con texto y se avisa de cuántas se saltaron. |

---

## 3. Guardado sin elegir acción

Antes de guardar había que elegir entre cuatro acciones sobre el inventario y,
para dos de ellas, el grupo de destino de cada modalidad. La opción inicial era
«Vincular evidencia», que exige destino para todos los grupos, de modo que el
botón de guardar permanecía desactivado hasta completarlos.

Se añadió el modo `auto` a `store.consolidar`. La decisión pasa a ser por grupo
y no por visita, porque una misma visita puede traer una modalidad ya conocida
y otra nueva:

- Modalidad ya registrada en ese cliente: la visita entra como evidencia sobre
  su grupo. Si lo reportado no coincide, queda anotado como conflicto y aparece
  en la pestaña correspondiente.
- Modalidad nueva: se crea el grupo.

Guardar es un solo botón. Antes de pulsarlo se avisa de lo que no cuadra con lo
registrado: corregir un dictado equivocado en ese momento cuesta un segundo,
descubrirlo después en la lista de conflictos cuesta una llamada al hospital.

El modo `auto` es aditivo. Los cuatro modos anteriores siguen disponibles en el
almacén y sus pruebas no cambian.

---

## 4. Correcciones

### Sinónimos de modalidad

«Ecógrafo» y «ultrasonido» son la misma modalidad, pero el modelo los devolvía
como dos grupos distintos, y en esa misma nota añadía un CT que nadie había
mencionado.

- Se descartan las modalidades que no aparecen en el texto. El guardarraíl de
  anclaje cubría marca, modelo, edad y ubicación, pero no la modalidad. Un
  equipo inventado es peor que uno que falta, porque nadie lo cuestiona al leer
  la ficha.
- Se unen los grupos de la misma modalidad que no se distinguen en nada. Si
  difieren en marca, modelo o edad se mantienen separados, porque ahí la
  separación significa algo: es la que distingue «dos viejos y uno nuevo».

Resultado: `tres ecógrafos y dos ultrasonidos` produce un grupo de cinco.
`3 radiografías de 5 años y 10 rayos X de 10 años` mantiene dos grupos.

### Conflictos sin detalle

La pestaña detectaba el conflicto pero mostraba cero versiones: leía las
revisiones de tipo `consolidar`, que solo guardan la acción. Ahora lee las de
tipo `extracción`, que contienen el borrador de cada visita, y muestra qué
reportó cada persona y en qué difiere.

### Interfaz

- Se retiró el subtítulo `Borrador #N guardado. Extracción: …`, que era
  diagnóstico interno.
- Se retiró el selector «Seleccionar cliente conocido» y la nota sobre la
  ubicación inicial. El campo Cliente ya permite escribir el nombre, y debajo se
  sigue indicando si es un cliente conocido o nuevo.
- Se sustituyeron los emojis de la leyenda del mapa por texto, según la
  convención adoptada en la rama.

---

## Comprobaciones

| Prueba | Resultado |
|---|---|
| `pytest tests/` | 31/31 |
| `scripts/test_extraccion.py` | 17/17 cliente y equipos; 5/5 no inventa sitio; 2/2 cliente nuevo |
| `scripts/test_documentos.py --qvac` | 19/19 |
| `scripts/test_audio.py` | correcto |

`scripts/test_documentos.py` es nuevo y genera sus propios ficheros de prueba.
Cubre los cinco formatos, el PDF escaneado, el troceado de documentos largos y
la separación de clientes en un inventario.

En `tests/test_streamlit.py` se actualizó `test_evidence_ui_does_not_add_units`:
manejaba el desplegable de destino que ya no existe. Las comprobaciones —que una
segunda visita sume evidencia y no unidades— se conservan sin cambios.

## Dependencias añadidas

    pydeck>=0.9
    pypdf>=5.0

## Pendiente

- Captura desde fotografías de placas mediante OCR. QVAC lo admite y encajaría
  en `documents.py` sin alterar el resto del recorrido.
- Resolución de conflictos desde su propia pestaña. Hoy se cierran registrando
  una visita nueva desde Capturar.
