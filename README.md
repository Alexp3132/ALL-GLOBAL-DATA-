# Control Fiscal y Auditoría — Streamlit

Aplicación didáctica orientada a un perfil de **Administración y Auditoría**. Integra facturación, gastos, movimientos bancarios y retenciones de ISR para construir un tablero ejecutivo de control fiscal, conciliación y alertas.

## Archivos de entrada

La aplicación espera cuatro archivos Excel con la estructura de las bases utilizadas en el curso:

1. `Base_1_Facturas_Emitidas.xlsx`
   - `folio`, `fecha`, `cliente`, `concepto`, `subtotal`, `iva`, `total`
2. `Base_2_Gastos_Facturas_Recibidas.xlsx`
   - `folio_gasto`, `fecha`, `proveedor`, `concepto`, `subtotal`, `iva`, `total`, `comprobante`
3. `Base_3_Movimientos_Bancarios.xlsx`
   - `fecha`, `monto`, `tipo`, `referencia`
4. `Base_4_Retenciones_ISR.xlsx`
   - `fecha`, `referencia`, `concepto`, `isr_retenido`

Puedes cargar los cuatro archivos desde la barra lateral. Alternativamente, si conservan los nombres anteriores y se encuentran en la misma carpeta que `app.py`, la aplicación los carga automáticamente.

## Funcionalidad principal

- Periodo dinámico: cualquier mes o rango de fechas.
- Conciliación prioritaria por folio/referencia.
- Coincidencia alternativa por monto + fecha + concepto.
- Tolerancias configurables; valores iniciales: ±$10 y ±5 días.
- Manejo de cobros/pagos parciales y acumulaciones simples.
- Clasificación de gastos en deducibles y no deducibles con base en el campo `comprobante`.
- IVA trasladado, IVA acreditable e IVA neto.
- ISR retenido y alertas de retenciones sin vínculo documental identificable.
- Alertas de facturas no cobradas, gastos sin pago, movimientos sin soporte, duplicados, campos vacíos, fechas fuera del periodo, IVA inconsistente e importes atípicos.
- Índice General de Control Fiscal y Auditoría.
- Gráfica prioritaria de flujo diario.
- Conclusiones y recomendaciones automáticas.
- Reporte Excel descargable con hojas: Resumen, Facturas, Gastos, Bancos, ISR, Conciliación y Alertas.

## Índice General de Control Fiscal y Auditoría

El indicador utiliza una ponderación didáctica:

- Conciliación: 30%
- Cumplimiento fiscal: 25%
- Integridad de datos: 20%
- Soporte documental: 15%
- Incidencias: 10%

Interpretación:

- 90–100: Control satisfactorio
- 80–89: Control aceptable con observaciones
- 70–79: Requiere seguimiento
- Menos de 70: Riesgo elevado / revisión prioritaria

## Instalación

Requiere Python 3.10 o superior.

```bash
python -m venv .venv
```

En Windows:

```bash
.venv\Scripts\activate
```

Instala las dependencias:

```bash
pip install -r requirements.txt
```

## Ejecución

Desde la carpeta del proyecto:

```bash
streamlit run app.py
```

Streamlit abrirá la aplicación en el navegador.

## Flujo de uso recomendado

1. Ejecuta `streamlit run app.py`.
2. Carga las cuatro bases desde el panel lateral o coloca los archivos con sus nombres originales junto a `app.py`.
3. Selecciona el rango de fechas.
4. Ajusta las tolerancias de conciliación si es necesario.
5. Utiliza los filtros por cliente, proveedor, clasificación y estatus.
6. Revisa el Índice General de Control y la gráfica de flujo diario.
7. Revisa las pestañas de conciliación y alertas.
8. Consulta las conclusiones y recomendaciones.
9. Descarga el reporte Excel.

## Criterio de conciliación

La aplicación no inventa relaciones documentales. Primero busca coincidencia exacta por folio o referencia. Si no existe, intenta una asociación probable con monto y fecha dentro de las tolerancias configuradas, complementada por una similitud simple del concepto/referencia.

Cuando una retención de ISR no contiene un identificador común con las facturas emitidas, se marca como **retención sin folio de factura identificable**. Para una auditoría formal se recomienda incorporar a la fuente un UUID, folio CFDI o identificador común.

## Consideraciones

Este proyecto es deliberadamente didáctico. Las reglas pueden ampliarse para incluir UUID de CFDI, RFC, cuentas contables, centros de costo, pólizas, complementos de pago o reglas fiscales específicas.

Las coincidencias automáticas y las conclusiones generadas son herramientas de apoyo administrativo y de auditoría; deben validarse contra la documentación fuente antes de una determinación fiscal formal.
