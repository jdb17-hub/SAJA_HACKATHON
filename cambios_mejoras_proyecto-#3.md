# Cambios y mejoras del proyecto — #3

Fecha: 9 de septiembre de 2026.

## Alcance

Se mejoró la presentación de la interfaz Streamlit y se implementó el inicio automático de QVAC. Los cambios están en el repositorio local, sin commits.

## 1. Interfaz sin emojis

- Se retiraron los emojis de las pestañas, los encabezados y los avisos, así como el icono de hospital configurado para la página.
- La conversación muestra etiquetas de texto **Colaborador** y **Agente** en lugar de los avatares predeterminados del chat.
- Se conservaron las secciones Capturar, Cliente, Panorama, Preguntar y Motor.

## 2. Ortografía y redacción

- Se corrigieron tildes y signos de interrogación en formularios y preguntas de seguimiento.
- Se revisaron textos de transcripción, resúmenes, alertas, edades, fechas y encabezados de tablas.
- Ejemplos: **País**, **Antigüedad**, **Edad (años)**, **Última visita**, **No lo sé** y **¿Cuántos tomógrafos viste?**.
- Se sustituyeron expresiones como «Dataset completo» por «Base de datos completa».

Los cambios de presentación no corrigen ni migran los textos ya almacenados en SQLite.

## 3. Inicio automático de QVAC

### Problema anterior

El usuario debía pulsar **Iniciar motor QVAC** antes de utilizar el modelo. Además, una captura escrita podía ejecutarse solo con reglas mientras el motor no estuviera disponible.

### Comportamiento implementado

1. Al abrir la aplicación, se inicia la carga de QVAC en un hilo de segundo plano.
2. La barra lateral muestra **Preparando captura… Puedes escribir mientras carga.**
3. La entrada de texto permanece disponible. La extracción, los ejemplos de captura y la grabación se deshabilitan hasta que el motor esté listo.
4. Al finalizar la carga, aparece **Captura lista** y se habilitan los controles.
5. Si el inicio falla, se muestra una explicación, el detalle del error y el botón **Reintentar**.

El motor comparte una única carga entre interacciones. Un bloqueo evita iniciar simultáneamente varios hilos de carga. Después de un error, el inicio solo se vuelve a intentar mediante la acción explícita del usuario.

La interfaz consulta el estado cada segundo mediante un fragmento de Streamlit y actualiza la aplicación al cambiar de estado. La entrada escrita se conserva durante la transición de carga a disponibilidad.

### Límites del cambio

- El inicio automático no elimina el tiempo de carga ni la descarga inicial del modelo.
- Whisper conserva su carga bajo demanda al utilizar la transcripción; **Captura lista** indica que el modelo de lenguaje está preparado, no que Whisper ya esté cargado.
- Se bloquea la captura mientras el motor no esté listo. El respaldo con reglas ante errores internos de extracción sigue existiendo en el pipeline.
- Las consultas conservan su comportamiento previo, incluido su respaldo con reglas cuando el motor no está disponible.

## Archivos modificados

| Archivo | Cambio |
|---|---|
| `app.py` | Presentación, ortografía, inicio automático, estado de carga, reintento y disponibilidad de controles. |
| `src/qvac_engine.py` | Inicio en segundo plano con protección contra cargas simultáneas. |
| `src/followup.py` | Ortografía y gramática de preguntas y resúmenes. |
| `src/confidence.py` | Ortografía de alertas visibles. |
| `src/dedup.py` | Ortografía de mensajes de duplicados. |
| `src/nlquery.py` | Ortografía de filtros y respuestas visibles. |

## Verificaciones realizadas

- Comprobación de sintaxis de los archivos editados durante la revisión de textos.
- Comprobación de formato con `git diff --check`.
- Pruebas con el inicio del motor simulado: carga no bloqueante, una sola carga simultánea, captura de fallos y reintento explícito.
- Pruebas con Streamlit AppTest y almacenamiento simulado: controles deshabilitados durante la carga, habilitación al estar listo, conservación del texto y aparición de **Reintentar** ante un error.

Estas verificaciones no equivalen a una prueba de inferencia real. Queda pendiente comprobar el arranque del modelo, la descarga cuando corresponda y el micrófono en el equipo del usuario.

## Pruebas manuales sugeridas

Desde la carpeta del repositorio, reiniciar Streamlit:

```powershell
.\run.bat
```

| Caso | Pasos | Resultado esperado |
|---|---|---|
| Inicio automático | Abrir la aplicación sin pulsar botones de inicio. | Aparece el estado de preparación y luego **Captura lista**, si el modelo carga correctamente. |
| Escritura durante la carga | Escribir una nota mientras QVAC se prepara. | El texto sigue visible al terminar la carga. |
| Controles durante la carga | Revisar Extraer datos, los ejemplos y Dictar. | Permanecen deshabilitados hasta que el motor esté listo. |
| Captura escrita | Introducir «Estoy en Hospital DemoCare Pacific, en Panamá. Tienen dos resonadores y un tomógrafo» y extraer. | Se abre un borrador editable. Comprobar MR=2 y CT=1 como revisión funcional. |
| Seguimiento | Responder una pregunta y omitir otra con **No lo sé**. | Preguntas con ortografía correcta y conversación con etiquetas de texto. |
| Dictado | Una vez lista la captura, grabar una nota y transcribir. | Se obtiene una transcripción editable; el primer uso puede esperar la carga de Whisper. |
| Navegación | Recorrer todas las pestañas. | La navegación conserva su funcionamiento y no muestra los emojis retirados. |
| Reintento | Si ocurre un fallo de inicio, revisar el detalle y pulsar **Reintentar** después de resolver su causa. | Comienza un nuevo intento y se actualiza el estado. |

Para revisar solo la interfaz, descartar el borrador antes de guardarlo. Guardar observaciones sí modifica la base SQLite.

## Mejoras funcionales pendientes

Las siguientes propuestas del análisis inicial no están implementadas en esta entrega:

- Resolución segura de nombres ambiguos de hospitales.
- Separación entre visitas, evidencia e inventario vigente.
- Corrección de confirmaciones que aumentan la confianza pese a información contradictoria.
- Separación correcta de condiciones de cantidad y edad en consultas.
- Fecha real de visita, borradores persistentes e historial de revisiones.

## Impacto

El usuario ya no necesita conocer ni ejecutar el inicio técnico del motor. La interfaz comunica cuándo puede capturar y permite aprovechar la espera para escribir. La limpieza visual y las correcciones de texto mejoran la presentación; la calidad de consolidación del inventario mantiene las limitaciones funcionales señaladas anteriormente.
