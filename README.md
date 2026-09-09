# Base Instalada QVAC

Convierte lo que un colaborador de campo observa en un hospital en datos
estructurados y fiables sobre los equipos instalados. La captura es tan simple
como contarlo, y **toda la inferencia corre en el dispositivo** a través de QVAC.

```
"Estoy en Hospital DemoCare Pacific, en Panamá. Tienen dos resonadores
 y un tomógrafo. Uno de los resonadores parece de unos ocho años."
                              ↓
   cliente · ciudad · país · modalidad · cantidad · marca · modelo · antigüedad
   + estado (Confirmado/Reportado/Estimado/Desconocido) + confianza 0-100
   + detección de duplicados + preguntas por lo que falta
```

---

## Declaración de base preexistente

Este proyecto **se construyó desde cero para este hackathon**. No parte de
ningún repositorio, producto ni prototipo anterior del equipo.

Se apoya en las siguientes dependencias de terceros, todas públicas y usadas
tal cual se distribuyen:

| Componente | Origen | Papel |
|---|---|---|
| `tetherto-qvac-sdk` 0.19.0 | Tether (PyPI) | SDK de inferencia on-device |
| `@qvac/sdk` 0.19.0 | Tether (npm) | Worker Bare que ejecuta el SDK |
| Qwen3 1.7B Instruct Q4 | catálogo de modelos de QVAC | extracción, seguimiento y consultas |
| Whisper Base Q8 | catálogo de modelos de QVAC | transcripción de voz |
| Streamlit, pandas, Altair, pydantic, numpy | PyPI | interfaz y datos |

Los datos de partida son el fichero `Dummy_Installed_Base_Hackathon.xlsx`
proporcionado por la organización. Todos los hospitales, marcas y modelos son
ficticios.

---

## Regla técnica: inferencia en el dispositivo

> *La inferencia corre en el dispositivo o entre pares. Nunca en la nube.*

**Cómo se cumple, y cómo verificarlo:**

- Todo el código que ejecuta inferencia está en un único fichero,
  [`src/qvac_engine.py`](src/qvac_engine.py). No hay ningún otro punto del
  proyecto que llame a un modelo.
- Ese fichero solo usa `tetherto.qvac_sdk`, que arranca un worker **Bare local**
  y carga un GGUF desde `~/.qvac/models`.
- **No existe ninguna clave de API** en el repositorio, ni variable de entorno de
  ningún proveedor, ni cliente HTTP hacia un servicio de inferencia.
- La transcripción de voz también es local (Whisper vía QVAC). El audio del
  micrófono nunca sale del equipo.
- **Prueba directa:** arranca la app, carga el modelo, **desconecta el wifi** y
  sigue capturando observaciones. Funciona igual.

Lo único que usa la red es la descarga inicial del fichero del modelo, una vez,
y los mosaicos cartográficos de la pestaña *Mapa*. Esos mosaicos son un recurso
de interfaz, no inferencia, así que no tocan la regla; y sin conexión el mapa
sigue situando los clientes, solo que sobre fondo liso.

```bash
# Comprobar que no hay llamadas a proveedores de inferencia en la nube
grep -rniE "openai|anthropic|api\.|https?://|api_key|bearer" src/ app.py
```

---

## Instalación

Requiere Python ≥ 3.11 y Node.js ≥ 22 (el SDK de Python arranca el worker de QVAC).

```bash
pip install -r requirements.txt
npm install -g @qvac/sdk@0.19.0
python scripts/import_excel.py "ruta/al/Dummy_Installed_Base_Hackathon.xlsx"
streamlit run app.py
```

### Dos detalles de Windows

Ambos ya están resueltos en el código, pero conviene saberlos porque fallan de
forma poco obvia:

1. **El SDK no encuentra el worker.** `tetherto-qvac-sdk` resuelve `npm root -g`
   ejecutando `npm`, que en Windows es `npm.cmd`, así que falla y da
   `WorkerNotFoundError` aunque el paquete esté instalado. `src/config.py`
   localiza el worker por su ruta habitual. Si lo tienes en otro sitio:

   ```bash
   export QVAC_SDK_DIR="/ruta/a/node_modules/@qvac/sdk"
   ```

2. **`NotImplementedError` vacío al iniciar el motor desde Streamlit.** QVAC
   lanza el worker como proceso hijo, y en Windows eso solo funciona sobre
   `ProactorEventLoop`. Tornado — del que depende Streamlit — instala la
   política `WindowsSelectorEventLoopPolicy` al importarse, que no soporta
   subprocesos. Por eso `src/qvac_engine.py` construye su event loop a mano en
   vez de usar `asyncio.new_event_loop()`.

La primera vez que pulses **Iniciar motor QVAC** se descargará el modelo
(~1,1 GB para Qwen3 1.7B). A partir de ahí arranca desde disco.

---

## Cómo funciona

### Extracción híbrida: reglas + LLM

Un modelo que cabe en un portátil sin GPU es bueno interpretando la
**estructura** de una frase y malo con el **detalle literal**. El reparto de
trabajo lo aprovecha:

| | Se encarga de | Por qué |
|---|---|---|
| **Reglas** (`src/normalize.py`, `src/extract.py`) | numerales en español, sinónimos de modalidad, marcas y clientes del catálogo, señales de incertidumbre | Es determinista y no falla. `dos resonadores` → `MR × 2`, siempre. |
| **LLM QVAC** (`src/qvac_engine.py`) | estructura de la frase, atribución de marca/edad a cada grupo, casos ambiguos | Solo él entiende que *"tres resonadores, dos viejos y uno nuevo"* son dos grupos dentro de una flota de tres. |

Y sobre la fusión, el guardarraíl que hace usable un modelo pequeño:

> **Ningún valor sobrevive si no está anclado en el texto original.**
> Si el modelo devuelve la marca `Siemens` y esa palabra no aparece en la nota,
> se descarta y se pregunta. Un campo vacío es recuperable; una marca inventada
> contamina la base y nadie se entera.

Lo mismo con las edades, la ciudad y el país: solo se aceptan si se dijeron. Una
nota que solo decía *"estoy en el hospital"* volvía con ciudad Santander y país
Spain, inventados enteros por el modelo para no dejar el hueco vacío.

**El cliente lleva dos comprobaciones más**, porque es el campo que decide dónde
se archiva todo lo demás:

- **Tiene que ser un nombre.** *"el hospital"*, *"la clínica de aquí al lado"* o
  *"el centro médico"* describen un sitio pero no nombran a nadie. Si al quitar
  el tipo de centro y el relleno no queda ninguna palabra propia, se deja vacío
  y el agente pregunta.
- **El emparejado va por palabras distintivas, no por parecido de cadenas.** Con
  parecido, `"Hospital"` a secas se enganchaba al primer hospital de la lista, y
  `"DemoCare"` —que comparten los trece clientes— también. Ahora hace falta que
  un cliente gane con claridad; si varios empatan, se pregunta cuál en vez de
  adivinar.

Un cliente que **sí** se entiende pero no está en el catálogo se registra como
candidato nuevo, y la interfaz lo marca como tal para que se revise el nombre
antes de guardar.

Las cantidades se concilian entre ambos. Si el texto menciona una modalidad una
sola vez, las reglas mandan. Si la menciona varias veces es ambiguo —
*"dos resonadores... uno de los resonadores parece viejo"* son 2 equipos, no 3 —
y ahí las reglas fijan el rango válido y el LLM elige dentro de él.

### Salida estructurada garantizada

Las llamadas al modelo usan `response_format` con **JSON Schema**, que QVAC
aplica durante el muestreo. La salida es JSON válido con los enums correctos
por construcción: no hay que reparar texto ni reintentar por formato.

### Preguntas por valor, no por orden de columna

El colaborador acaba de salir de un hospital y tiene un minuto. En vez de
repasar el formulario entero, el agente pregunta primero por el dato que más
aporta y que todavía falta (`src/followup.py`):

```
customer 100 · modality 90 · quantity 70 · brand 55 · age 50 · país 30 · modelo 12
```

El peso de marca y antigüedad se multiplica por el tamaño del grupo: saber la
marca de cinco ecógrafos vale más que la de uno, porque la cantidad multiplica
el tamaño de la oportunidad. El modelo del equipo solo se pregunta si ya se
conoce la marca; si no, sobra.

### Confianza explicable (0-100)

Cuatro señales deterministas, todas visibles en la interfaz (`src/confidence.py`):

| Señal | Máx. | Qué mide |
|---|---|---|
| Completitud | 40 | cuántos campos útiles tiene la fila |
| Estado | 25 | Confirmado (25) > Reportado (16) > Estimado (8) |
| Frescura | 20 | decae con la antigüedad del dato |
| Confirmaciones | 15 | observadores distintos que coinciden |

Que dos personas que no hablaron entre sí vean lo mismo es la señal más fuerte
de que el dato es real, así que puntúa aparte.

### Duplicados

Tres personas visitan el mismo hospital y las tres reportan "dos resonadores".
Sin detección, la base dice seis. Antes de guardar, `src/dedup.py` compara
contra lo que ya hay del mismo cliente y ofrece tres salidas:

- **Confirmar** la existente — sube su confianza sin duplicar la cuenta
- **Completar** la existente — vuelca solo los campos que estaban vacíos
- **Guardar aparte** — son flotas distintas

Marcas distintas para la misma modalidad bajan la similitud: un hospital puede
tener perfectamente un CT de dos fabricantes.

### Conflictos

Un duplicado es que dos personas cuenten lo mismo. Un **conflicto** es que
cuenten cosas distintas del mismo equipo, y es otro problema: el duplicado infla
la cuenta, el conflicto la hace dudosa. `src/conflicts.py` los detecta por
cantidad, antigüedad y modelo, y los ordena por gravedad — pesa más el
desacuerdo en cantidad, porque mueve el tamaño de la oportunidad.

Se ignoran los pares del mismo observador: que alguien registre dos veces
números distintos es una corrección suya, no una discrepancia entre fuentes.

Un conflicto no se resuelve solo, así que la pestaña ofrece tres salidas:
quedarse con una de las dos, o marcar que **ambas son correctas** cuando el
desacuerdo tiene explicación (dos flotas parecidas, o equipos añadidos entre
visitas). Sin esa tercera salida el mismo aviso reaparecería siempre y la lista
dejaría de mirarse. La observación descartada queda registrada en la nota de la
que se queda: sin ese rastro, mañana nadie sabría que hubo discrepancia.

### Mapa y Customer 360

El reto pide navegar **Región → País → Ciudad → Cliente → Equipos**, y eso es la
pestaña *Mapa*: filtros encadenados sobre un mapa donde el tamaño del punto es
la cantidad de unidades y el color avisa de flotas envejecidas.

Las coordenadas son una tabla local (`src/geo.py`) a propósito. Geocodificar
contra un servicio externo mandaría los nombres de los clientes fuera del
equipo, que es justo lo que esta aplicación evita.

La ficha de cliente abre con el **panorama por modalidad** — tipo de equipo,
cantidad, edad aproximada, marcas y confianza — y debajo las observaciones
individuales de las que sale cada número. La edad va en **rango** y no en
promedio: una flota comprada en tandas distintas no tiene *una* edad, y
promediarla esconde justo lo interesante, que es que hay equipos viejos.

### Consultas en lenguaje natural

*"clientes en Brasil con resonadores de más de siete años"*

El modelo **no genera SQL ni código**: traduce la pregunta a un filtro con forma
fija (JSON Schema) que se aplica en Python. Una consulta rara devuelve resultados
vacíos en vez de ejecutar algo inesperado, y los números salen siempre de los
datos y no de lo que el modelo recuerde. Los umbrales de edad se corrigen con
reglas, porque *"más de siete"* es `>= 8` y ahí el modelo se equivoca a menudo.

---

## Estructura

```
app.py                    interfaz Streamlit (7 pestañas)
src/
  qvac_engine.py          ← único punto de inferencia de todo el proyecto
  config.py               modelos, rutas, umbrales de negocio
  schema.py               modelo de datos + JSON Schema + etiquetas de pantalla
  normalize.py            numerales, sinónimos, catálogos, fuzzy matching
  extract.py              pipeline híbrido reglas + LLM
  followup.py             siguiente pregunta más valiosa
  confidence.py           puntaje 0-100 y alertas
  dedup.py                detección de duplicados
  conflicts.py            observaciones que se contradicen, y cómo cerrarlas
  insights.py             Customer 360 y analíticas del panel
  geo.py                  jerarquía geográfica y coordenadas del mapa
  nlquery.py              consultas en lenguaje natural
  audio.py                detección de formato de la grabación
  store.py                SQLite
scripts/
  import_excel.py         Excel del reto → datos semilla
  test_extraccion.py      evaluación end-to-end (reglas vs. híbrido)
  test_audio.py           ruta de voz: formato → Whisper → extracción
```

## Pruebas

```bash
python scripts/test_extraccion.py
```

Corre los 10 prompts de voz del Excel más variantes en español, y compara solo
reglas contra el pipeline híbrido con QVAC.

Resultado medido en un portátil Windows sin GPU:

| | Cliente correcto | Equipos correctos | Tiempo |
|---|---|---|---|
| Solo reglas | 16/16 | 14/16 | 0,1 s |
| **Híbrido (QVAC)** | **16/16** | **16/16** | 26 s |

Más dos baterías que cubren el sentido contrario, que es donde más daño hace
equivocarse:

| | Resultado |
|---|---|
| No inventa sitio cuando no se entiende | 5/5 |
| Guarda el cliente nuevo en vez de descartarlo | 2/2 |

**1,45 s por nota**, con el modelo cargado desde disco en 20 s. Los dos casos
que las reglas solas no resuelven son los que necesitan interpretar la frase:
*"muchos ecógrafos, quizás ocho"* (el número no va pegado a la modalidad) y
*"tres resonadores, dos viejos y uno nuevo"* (una flota partida en dos grupos).

## Elección de modelo

Medido en este proyecto, sobre los prompts de prueba:

| Modelo | Latencia | Resultado |
|---|---|---|
| Llama 3.2 1B Q4 | ~2 s | falla los numerales en español |
| **Qwen3 1.7B Q4** | **1–3 s** | **todas las cantidades correctas** |
| Qwen3 4B Q4_K_M | 3,5–10 s | más lento *y* peor: asignó la edad a la modalidad equivocada |

Se puede cambiar sin tocar código:

```bash
export QVAC_LLM_MODEL=QWEN3_4B_INST_Q4_K_M
```

### Dictado por voz

El colaborador graba al salir de la visita y Whisper transcribe **en el
dispositivo**; el audio nunca sale del equipo. Tres cosas que no son evidentes y
que hay que hacer bien para que funcione:

1. **No hay que convertir el audio.** QVAC trae ffmpeg y decodifica wav, ogg,
   mp3, m4a, flac y aac con cualquier frecuencia de muestreo. Un intento de
   remuestrear a 16 kHz con el módulo `wave` de Python rompe la grabación real:
   el navegador produce WAV en coma flotante de 32 bits, que `wave` rechaza con
   `unknown format: 3`. `src/audio.py` solo detecta el formato por los bytes de
   cabecera y le pasa el fichero a QVAC.

2. **Hay que declarar el idioma.** Sin `modelConfig={"language": "es"}`,
   whisper.cpp asume inglés y **traduce**: una nota dictada en español vuelve en
   inglés y los nombres propios se pierden. Se configura con `QVAC_STT_IDIOMA`.

3. **La petición se construye con `model_validate`, no con argumentos.** El SDK
   serializa con `exclude_unset=True`, así que el campo `type` que viene del
   valor por defecto se cae del payload y el worker no sabe qué método invocar.
   El síntoma es un `RuntimeError: expected a response stream` que no apunta a
   nada.

Además se le pasa a whisper un `initial_prompt` con el vocabulario esperado
(modalidades y marcas del catálogo), que es donde más falla un modelo pequeño. Y
si aun así entiende mal una palabra, la transcripción es **editable**: se corrige
y se vuelve a extraer, sin repetir la grabación.

## Limitaciones

- El dictado usa Whisper Base. Va bien en un pasillo tranquilo; con ruido de
  sala conviene subir a `WHISPER_SMALL_Q8_0` (`QVAC_STT_MODEL`), que es más
  lento pero bastante más preciso.
- La captura por foto de placas (OCR) no está implementada. QVAC lo soporta y
  encajaría en `qvac_engine.py` sin tocar el resto.
- El mapa necesita `pydeck` y los mosaicos vienen de internet. Sin conexión los
  clientes siguen situándose, pero sin cartografía de fondo.
- Las coordenadas cubren las capitales de Latinoamérica y las ciudades del
  dataset. Un cliente en una ciudad que no esté en la tabla cae al centro de su
  país, y si tampoco se conoce el país se lista aparte en vez de desaparecer.
- La inferencia delegada por P2P tampoco: la app usa el modo on-device puro,
  que ya cumple la regla del reto.
- Los umbrales de renovación (10 años) y de dato caducado (180 días) están
  fijados en `src/config.py` y deberían venir de la política real de la
  organización.
