# Base Instalada QVAC — reto Philips

Prototipo local que convierte observaciones de campo en un inventario revisado por cliente y geografía. Streamlit ofrece captura escrita, voz, seguimiento, revisión, historial y consultas. QVAC ejecuta la inferencia en el dispositivo.

## Base preexistente y alcance

Este repositorio evoluciona SAJA_HACKATHON y toma como referencia la lógica de visitas, revisiones y consolidación de `qvac-project-phillips`. No se declara construido desde cero. Los datos semilla proceden del Excel ficticio del reto; son ejemplos, no observaciones obtenidas por inferencia. La interfaz identifica su presencia.

La entrega implementa el prototipo mínimo y varias metas adicionales. OCR de placas y delegación P2P no están implementados. La alternativa técnica elegida es inferencia local con QVAC.

## Iniciar

Requiere Python 3.11 o posterior y el entorno local de QVAC. El entorno usado en las pruebas tiene Python 3.12, Streamlit 1.58 y QVAC 0.19.

```powershell
pip install -r requirements.txt
npm install -g @qvac/sdk@0.19.0
.\run.bat
```

La aplicación inicia QVAC en segundo plano. Se puede escribir durante la carga; la extracción y la grabación se habilitan al quedar listo. Si falla, se muestra el error y **Reintentar**. La primera preparación puede descargar el modelo. Whisper se carga al solicitar la primera transcripción.

`src/config.py` localiza el worker npm en Windows. `QVAC_SDK_DIR` permite indicar otra instalación. El motor crea un ProactorEventLoop para que el worker pueda arrancar desde Streamlit en Windows.

## Recorrido de captura

1. Indicar colaborador y fecha real de visita; dejar la fecha vacía si no se conoce.
2. Escribir una nota o dictarla. La captura exige QVAC disponible: no sustituye silenciosamente un fallo de extracción por reglas.
3. Revisar el borrador. Cantidad o edad vacía significa desconocida; **0 es un valor explícito**.
4. Resolver el cliente ambiguo mediante seguimiento o selección de cliente conocido. La ubicación recuperada del catálogo se identifica como tal y puede corregirse.
5. Responder las preguntas prioritarias. **No lo sé** conserva el desconocido y omite esa pregunta. Las preguntas identifican el grupo y las omisiones usan identificadores estables.
6. Revisar cantidades y grupos: cada grupo representa unidades distintas. El total y sus subconjuntos no deben registrarse como grupos adicionales.
7. Elegir una acción y guardar. Los borradores, respuestas, correcciones, extracción y decisión final se conservan en SQLite.

El ejemplo principal produce dos grupos MR de una unidad cada uno —uno de ocho años y otro de edad desconocida— y un CT de edad desconocida. La marca y el modelo no se inventan.

## Consolidación del inventario

| Acción | Efecto |
|---|---|
| Vincular evidencia | Asocia la visita a un grupo elegido; no añade unidades ni reemplaza datos. Las diferencias quedan registradas. |
| Recuento actual | Reemplaza solo las modalidades descritas del mismo cliente y ubicación. Conserva las cohortes anteriores como histórico. Requiere fecha y cantidades conocidas, y no acepta un recuento anterior a uno vigente más reciente. |
| Completar campos desconocidos | Rellena huecos del grupo elegido si los atributos y cantidades son compatibles. No extiende una edad de una unidad a una flota mayor. |
| Registrar una flota distinta | Añade unidades. Si ya existe inventario para ese cliente, exige indicar que son equipos distintos. |

Cada guardado es una transacción y valida la versión del borrador. Repetir el guardado de la misma visita no duplica el inventario. La coincidencia de cliente incluye nombre, ciudad y país; el selector permite distinguir sedes homónimas.

La vista de Cliente muestra inventario vigente, historial de cohortes y evidencia con texto original. Panorama y las consultas usan solo las cohortes vigentes. Las unidades desconocidas no se suman como si fueran conocidas.

## Confianza, fechas y renovación

La confianza es una heurística de 0 a 100: completitud hasta 40 puntos, estado hasta 25, frescura hasta 20 y diversidad de observadores hasta 15. No es una probabilidad calibrada; nombres distintos no demuestran independencia real.

Las confirmaciones se cuentan desde visitas vinculadas al grupo y compatibles con los atributos conocidos. Una observación contradictoria no aumenta confirmaciones ni refresca la verificación. Completar un atributo parcial tampoco convierte todo el registro en reciente.

La fecha original de visita y la última verificación se mantienen separadas. La edad sigue siendo la reportada, no se incrementa automáticamente. El año de instalación estimado se calcula usando el año de la visita. La falta de fecha genera una alerta.

Umbrales configurables antes de iniciar:

```powershell
$env:QVAC_EDAD_RENOVACION = '10'
$env:QVAC_DIAS_SIN_VERIFICAR = '180'
```

Estos valores son criterios de demostración, no políticas atribuidas a Philips.

## Búsqueda

Ejemplos:

- `Muéstrame solo los de Panamá`
- `Equipos en Brasil`
- `Clientes de México`
- `Hospitales con más de cinco resonadores`
- `Clientes en Brasil con resonadores de más de siete años`
- `Clientes en Brasil con más de cinco resonadores de más de siete años`

Se aceptan Panamá/Panama, Brasil/Brazil y las equivalencias del catálogo. La consulta simple por país se resuelve con reglas locales sin llamar al modelo, muestra el filtro y no devuelve países distintos si no hay coincidencias.

Las condiciones numéricas distinguen unidades de años. La cantidad se calcula agrupando cohortes por cliente, ubicación y modalidad después de aplicar los demás filtros. Si un grupo tiene cantidad desconocida, no se afirma un total completo para esa agrupación.

Se admiten comparaciones como más de, menos de, al menos, hasta y entre. Las consultas complejas emplean QVAC; los filtros se muestran para revisión y se ejecutan en Python, nunca como SQL generado. Países múltiples y exclusiones de país requieren reformular la consulta; el prototipo pide un país por búsqueda.

## Datos y migración

La base predeterminada es `data/observaciones.db`. Puede aislarse una prueba:

```powershell
$env:QVAC_DB_PATH = Join-Path $env:TEMP 'qvac-prueba-philips.db'
.\run.bat
```

Al abrir por primera vez una base con la tabla antigua `observaciones`, se crea `observaciones.db.pre-v2.bak` y se migra una sola vez a tablas `ib_visits`, `ib_revisions`, `ib_equipment` e `ib_evidence`. La tabla anterior permanece intacta. Sus ceros numéricos se interpretan como desconocidos porque esa era la convención anterior.

La migración no adivina qué registros históricos son duplicados: los marca para revisión. No los fusiona ni descarta automáticamente. Recargar la semilla desde la interfaz borra el inventario, visitas y borradores activos de la base seleccionada; usarlo solo para reiniciar una demostración.

## Validación

```powershell
python -B -m unittest discover -s tests -v
python -B scripts/verify_qvac.py
python -B scripts/verify_qvac.py --audio 'C:\ruta\nota.wav'
python -B scripts/test_extraccion.py
```

Las 31 pruebas automatizadas de aceptación e interfaz usan SQLite temporal e inferencia simulada donde corresponde. Cubren país, cantidades y edades, datos desconocidos, identidad ambigua, subconjuntos, estados, historial, duplicación de visitas, reemplazo por modalidad, fechas, confianza y migración con respaldo.

La prueba `verify_qvac.py` usa QVAC real y guarda `reports/qvac-smoke.json`, sin modificar inventario. En la revisión del 9 de septiembre de 2026 pasaron el ejemplo principal, cliente ambiguo, cliente nuevo y consulta combinada. También pasó una transcripción con un WAV sintético de referencia; no equivale a validar el micrófono ni el ruido de un hospital.

La evaluación real de extracción obtuvo cliente y cantidades correctos en 16/16 casos, 5/5 casos sin sitio identificable y 2/2 clientes nuevos. El modelo fue Qwen3 1.7B Q4 y la media de extracción fue 1,51 segundos en ese equipo. Estos resultados corresponden a ese conjunto de prueba, no a precisión general garantizada.

## Verificación sin internet — pendiente de ejecución manual

1. Preparar los modelos de texto y voz con conexión.
2. Detener la aplicación para probar también el arranque desde caché.
3. Desconectar la red manualmente.
4. Iniciar la aplicación y capturar una nota nueva, responder seguimiento y guardar.
5. Probar dictado con el micrófono y consultas sobre la base.
6. Ejecutar el smoke y conservar el reporte junto con evidencia de la desconexión.

La revisión automatizada ejecutó QVAC local real, pero **no desconectó la red del equipo**. El reporte indica `offline_verified: false`; no debe presentarse como certificación offline. ISD debe poder verificar este requisito obligatorio.

## Limitaciones

Prototipo de una estación local: sin autenticación multiusuario, sincronización ni cifrado propio de SQLite. La inferencia pequeña puede equivocarse; la revisión humana sigue siendo necesaria. La detección léxica y el tratamiento de subconjuntos cubren expresiones concretas, no toda construcción posible. Rangos de edad en seguimiento se conservan como nota, sin inventar un punto medio. Fotografías/OCR y P2P quedan fuera de esta entrega.


## Captura unificada

En **Capturar**, una sola barra permite escribir una observación, adjuntar documentos o audios con **+**, grabar con el **micrófono** y enviar. Se requiere Streamlit 1.58 o posterior.

Después del envío se muestra el contenido reunido para revisar y corregir. **Extraer observación** inicia el flujo existente de borrador, seguimiento y confirmación de guardado. Si hay varios clientes, se debe elegir cuál registrar. **Preguntar** mantiene su propia pestaña y no recibe estos envíos.

Los adjuntos admiten los formatos del lector de documentos y WAV, MP3, M4A, OGG, FLAC y AAC. El máximo combinado es 25 MB. La transcripción sigue usando QVAC local y los archivos temporales de audio se eliminan al terminar, también si ocurre un error. Los PDF escaneados siguen requiriendo OCR.

Un envío pendiente se conserva en la sesión mientras carga el motor o se reintenta un error. Puede descartarse para preparar otro. Esta retención en sesión no equivale a un borrador persistido: el borrador se guarda después de extraer.
