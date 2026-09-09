# Cambios y mejoras del proyecto — #4

Fecha: 9 de septiembre de 2026.
Rama de trabajo: `feat-mejoras_proyecto-#4`.
Los cambios se dejan locales, sin commits.

## Resultado

Se incorporó persistencia de visitas y borradores, revisiones, inventario vigente y evidencia vinculada. La captura y las vistas existentes de Streamlit se conservaron, junto con las mejoras visuales y el inicio automático de QVAC.

## Cambios principales

| Área | Comportamiento nuevo |
|---|---|
| Cliente | Un nombre ambiguo como Hospital DemoCare queda sin resolver. Se puede seleccionar explícitamente un cliente y su ubicación. Las sedes con el mismo nombre se distinguen por ciudad y país. |
| Extracción | El ejemplo del reto conserva MR=2 y CT=1; solo una unidad MR tiene ocho años. El modo interactivo exige QVAC y muestra el fallo sin sustituir la inferencia por reglas. |
| Desconocidos | Cantidad y edad vacías son `null`; cero significa un valor explícito. |
| Estados | Se conservan Confirmado, Reportado, Estimado y Desconocido por grupo. Se evita extender la incertidumbre de otra modalidad en cláusulas simples. |
| Borradores | Se guardan al extraer y al revisar. Retomar borrador recupera texto, datos, conversación, fecha y preguntas omitidas. |
| Seguimiento | Cada pregunta identifica el grupo; sus claves son estables aunque se quite otro grupo. Los rangos de edad se conservan como nota. |
| Guardado | Acciones explícitas: nueva flota, recuento actual, vincular evidencia o completar campos desconocidos. |
| Duplicación | Una visita ya consolidada no puede guardarse dos veces. Vincular evidencia no suma unidades. |
| Historial | Se conserva el texto original, la respuesta estructurada, revisiones y decisiones; los grupos reemplazados permanecen en el histórico. |
| Confianza | Usa evidencia vinculada y compatible. Una discrepancia no aumenta confirmaciones ni refresca la verificación. |
| Fechas | Fecha real de visita, última verificación y fecha de observación de edad se distinguen. El año estimado usa la fecha de visita. |
| Alertas | Los registros sin fecha también requieren revisión. Renovación y plazo de verificación son configurables. |
| Agregación | Cliente, Panorama, exportación y consultas leen el inventario vigente. Las cantidades desconocidas no se incluyen en las unidades conocidas. |
| Migración | Copia de respaldo antes de migrar la tabla antigua; migración idempotente y conservación de la tabla original. Los posibles duplicados históricos se revisan manualmente. |

## Búsqueda por país y condiciones numéricas

Solicitudes admitidas:

- `Muéstrame solo los de Panamá`.
- `Equipos en Brasil`.
- `Clientes de México`.
- `Panama` o `Brazil`.
- `Hospitales con más de cinco resonadores`.
- `Clientes en Brasil con resonadores de más de siete años`.
- `Clientes en Brasil con más de cinco resonadores de más de siete años`.

Las equivalencias del catálogo se normalizan también en los registros: Panamá/Panama y Brasil/Brazil producen el mismo filtro. La consulta simple por país no necesita llamar al modelo. El filtro siempre se muestra; un país sin resultados no devuelve registros de otros países.

Las cantidades y edades se interpretan según sus unidades. Para cantidades se suman las cohortes coincidentes por cliente, ubicación y modalidad. Las consultas actuales permiten un país por búsqueda; múltiples países o exclusiones requieren reformulación.

## Verificación realizada

- **31 pruebas automatizadas aprobadas**, incluyendo lógica, migración y recorridos de Streamlit con SQLite temporal.
- Pruebas de interfaz: guardar desconocidos, retomar borrador, vincular evidencia sin aumentar unidades, ausencia de resultados por país y conservación del texto durante la carga.
- **QVAC real: 16/16** casos con cliente y cantidades correctos, **5/5** casos de sitio no identificable y **2/2** clientes nuevos.
- Smoke real: ejemplo principal, cliente ambiguo, cliente nuevo, consulta combinada y transcripción de un WAV sintético de referencia.
- `git diff --check` sin errores de formato.

El reporte del smoke está en `reports/qvac-smoke.json`. Las pruebas automatizadas de persistencia y Streamlit no modifican la base habitual del usuario.

## Cómo probar

Para probar en una base separada:

```powershell
$env:QVAC_DB_PATH = Join-Path $env:TEMP 'qvac-prueba-philips.db'
.\run.bat
```

1. Capturar el ejemplo principal y comprobar total de tres unidades.
2. Revisar la fecha, los estados y los atributos desconocidos.
3. Guardar como nueva flota o recuento según los datos existentes.
4. Capturar otra visita que describa el mismo grupo y elegir Vincular evidencia.
5. Comprobar que las cantidades no aumentan y la visita aparece en el historial.
6. Repetir con una cantidad distinta; comprobar que queda señalada y no aumenta confirmaciones.
7. Capturar sin marca o edad, cerrar la sesión y retomar el borrador.
8. Buscar por país; comparar resultados con Cliente y Panorama.

Para usar otra vez la base predeterminada, abrir otra terminal sin `QVAC_DB_PATH` o quitar únicamente esa variable de entorno de la sesión.

```powershell
python -B -m unittest discover -s tests -v
python -B scripts/verify_qvac.py
python -B scripts/test_extraccion.py
```

## Pendiente de validación manual

**No se desconectó la red del equipo durante estas pruebas.** Debe ejecutarse el recorrido con internet desactivado y los modelos previamente preparados para demostrar el requisito obligatorio de ISD. El reporte mantiene `offline_verified: false`.

La transcripción de prueba no valida el micrófono físico ni el ruido hospitalario. OCR y P2P no están implementados; son ampliaciones y no son necesarios para la vía de inferencia local del mínimo.

La extracción sigue siendo un prototipo con reglas conservadoras: expresiones complejas pueden requerir corrección manual. La confianza es una heurística y la diversidad de nombres de observadores no acredita independencia real.
