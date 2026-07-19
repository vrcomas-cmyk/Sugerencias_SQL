-- =============================================================================
--  COMODATOS PROTEC — RENTABILIDAD Y SEGUIMIENTO DE CLIENTES  (SCRIPT COMPLETO)
--  Dialecto: DuckDB. Incluye:
--    · Fix YBFD/ZBRE (YBFD=+"Cantidad" siempre; ZBRE=-"Cantidad de pedido")
--    · v_crecimiento / v_crecimiento_cliente (tendencia mensual)
--    · v_seguimiento_360 con columnas de crecimiento
--    · Factura sin comodato (base por unión de llaves + bandera)
--    · Fecha robusta (COALESCE "Creado el3"/"Creado el") -> ZBRE ya no se pierden
--    · Fecha de facturación robusta (acepta TIMESTAMP_NS/DATE o texto)
--    · Zona/Ejecutivo/Grupo de cliente (tabla ejecutivos_zona) en v_seguimiento_360
--  Orden de ejecución correcto (cada vista usa solo lo definido arriba).
-- =============================================================================

-- #############################################################################
-- ## CAPA 1 — STAGING                                                         ##
-- #############################################################################
-- mm_ybfd_zbre -> stg_comodatos   (captura AMBAS cantidades: entrega y devolución)
CREATE OR REPLACE VIEW stg_comodatos AS
SELECT
    TRIM(REGEXP_REPLACE(CAST("Solicitante" AS VARCHAR), '\.0$', ''))  AS cliente,
    "Nombre11"                                                         AS razon_social,
    TRIM(REGEXP_REPLACE(CAST("Material"    AS VARCHAR), '\.0$', ''))  AS codigo_detalle,
    "Denominación"                                                     AS texto_material,
    TRIM(CAST("Clase doc.ventas" AS VARCHAR))                          AS tipo_movimiento,   -- YBFD / ZBRE
    COALESCE(TRY_CAST(REPLACE(TRIM(CAST("Cantidad" AS VARCHAR)), ',', '') AS NUMERIC), 0)
                                                                       AS cantidad,          -- ENTREGA (YBFD)
    COALESCE(TRY_CAST(REPLACE(TRIM(CAST("Cantidad de pedido" AS VARCHAR)), ',', '') AS NUMERIC), 0)
                                                                       AS cantidad_pedido,   -- DEVOLUCIÓN (ZBRE)
    -- Fecha: las ENTREGAS (YBFD) traen "Creado el3"; las DEVOLUCIONES (ZBRE)
    -- a veces solo traen "Creado el". Se toma el primero disponible para no
    -- perder las ZBRE en el filtro de fecha (causa del faltante en carritos).
    COALESCE(
        TRY_STRPTIME(CAST("Creado el3" AS VARCHAR), '%d/%m/%Y'),
        TRY_STRPTIME(CAST("Creado el3" AS VARCHAR), '%Y-%m-%d'),
        TRY_STRPTIME(CAST("Creado el3" AS VARCHAR), '%Y-%m-%d %H:%M:%S'),
        TRY_STRPTIME(CAST("Creado el3" AS VARCHAR), '%m/%d/%Y'),
        TRY_STRPTIME(CAST("Creado el"  AS VARCHAR), '%d/%m/%Y'),
        TRY_STRPTIME(CAST("Creado el"  AS VARCHAR), '%Y-%m-%d'),
        TRY_STRPTIME(CAST("Creado el"  AS VARCHAR), '%Y-%m-%d %H:%M:%S'),
        TRY_STRPTIME(CAST("Creado el"  AS VARCHAR), '%m/%d/%Y'),
        TRY_CAST("Creado el3" AS TIMESTAMP),
        TRY_CAST("Creado el"  AS TIMESTAMP)
    )                                                                  AS fecha_entrega,
    NULLIF(TRIM(CAST("Motivo de rechazo" AS VARCHAR)), '')             AS motivo_rechazo
FROM mm_ybfd_zbre
WHERE "Solicitante" IS NOT NULL
  AND "Material"    IS NOT NULL;

-- facturacion -> stg_facturacion
CREATE OR REPLACE VIEW stg_facturacion AS
SELECT
    TRIM(REGEXP_REPLACE(CAST("Solicitante" AS VARCHAR), '\.0$', ''))  AS cliente,
    "Razón Social"                                                     AS razon_social,
    TRIM(REGEXP_REPLACE(CAST("Material"    AS VARCHAR), '\.0$', ''))  AS codigo_detalle,
    "Texto Material"                                                   AS texto_material,
    -- Fecha robusta: acepta TIMESTAMP/DATE (p.ej. TIMESTAMP_NS) o texto en varios formatos
    CAST(COALESCE(
        TRY_CAST("Fecha" AS DATE),
        TRY_CAST("Fecha" AS TIMESTAMP),
        TRY_STRPTIME(CAST("Fecha" AS VARCHAR), '%d/%m/%Y'),
        TRY_STRPTIME(CAST("Fecha" AS VARCHAR), '%Y-%m-%d'),
        TRY_STRPTIME(CAST("Fecha" AS VARCHAR), '%Y-%m-%d %H:%M:%S'),
        TRY_STRPTIME(CAST("Fecha" AS VARCHAR), '%m/%d/%Y')
    ) AS DATE)                                                         AS fecha,
    TRY_CAST(REGEXP_REPLACE(CAST("Gpo. Vdor."  AS VARCHAR), '\.0$', '') AS BIGINT) AS gpo_vdor,    -- zona (Gpo. Vdor.)
    TRY_CAST(REGEXP_REPLACE(CAST("Grp. Cliente" AS VARCHAR), '\.0$', '') AS BIGINT) AS grp_cliente,  -- grupo de cliente (numérico)
    CAST("Factura" AS VARCHAR)                                         AS factura,
    COALESCE(TRY_CAST(REPLACE(TRIM(CAST("Cantidad" AS VARCHAR)), ',', '') AS NUMERIC), 0) AS cantidad,
    COALESCE(TRY_CAST(REPLACE(TRIM(CAST("Importe"  AS VARCHAR)), ',', '') AS NUMERIC), 0) AS importe
FROM facturacion
WHERE "Solicitante" IS NOT NULL
  AND "Material"    IS NOT NULL;

-- c_materiales -> stg_materiales
CREATE OR REPLACE VIEW stg_materiales AS
SELECT
    TRIM(REGEXP_REPLACE(CAST(codigo_detalle AS VARCHAR), '\.0$', '')) AS codigo_detalle,
    codigo_grupo,
    codigo_subgrupo,
    CAST("Costo" AS NUMERIC)                                           AS costo,
    activo
FROM c_materiales
WHERE codigo_detalle IS NOT NULL;

-- #############################################################################
-- ## CAPA 2 — CONFIGURACIÓN                                                   ##
-- #############################################################################
CREATE OR REPLACE VIEW cfg_equipos AS
SELECT * FROM (
VALUES
  ('1331070', '65652-928 CARRO PARA 2 CANISTERS', 'Carro (complementario)'),
  ('1331080', '65652-586 CARRO PARA CANISTERS',   'Carro (complementario)'),
  ('1331100', '65652-611 CANISTER 1000 CC PI',    'Canister (facturación directa)'),
  ('1331120', '65652-616 CANISTER 1500 CC PI',    'Canister (facturación directa)'),
  ('1331140', '65652-631 CANISTER 3000 CC PI',    'Canister (facturación directa)')
) t (codigo_equipo, descripcion_equipo, tipo);

CREATE OR REPLACE VIEW cfg_relacion_comodatos AS
SELECT * FROM (
VALUES
  ('1331100', '65652-611 CANISTER 1000 CC PI', '1330130', '65651-910M BOLSA DESECHABLE 1000 CC C/5'),
  ('1331100', '65652-611 CANISTER 1000 CC PI', '1330310', '65651910PG BOLSA DESECH C/GEL 1000 C/5'),
  ('1331120', '65652-616 CANISTER 1500 CC PI', '1330150', '65651-920M BOLSA DESECHABLE 1500 CC C/5'),
  ('1331120', '65652-616 CANISTER 1500 CC PI', '1330350', '65651920PG BOLSA DESECH C/GEL 1500 C/5'),
  ('1331140', '65652-631 CANISTER 3000 CC PI', '1330170', '65651-930M BOLSA DESECHABLE 3000 CC C/5'),
  ('1331140', '65652-631 CANISTER 3000 CC PI', '1330380', '65651930PG BOLSA DESECH C/GEL 3000 C/5')
) t (codigo_equipo, descripcion_equipo, codigo_consumible, descripcion_consumible);

-- #############################################################################
-- ## CAPA 3 — NÚCLEO                                                          ##
-- #############################################################################
-- Movimientos netos: YBFD = +"Cantidad" (siempre) | ZBRE = -"Cantidad de pedido"
CREATE OR REPLACE VIEW comodato_movimientos AS
SELECT
    cliente,
    razon_social,
    codigo_detalle AS codigo_equipo,
    fecha_entrega,
    DATE_TRUNC('month', fecha_entrega) AS periodo_entrega,
    tipo_movimiento,
    CASE
        WHEN tipo_movimiento = 'YBFD' THEN  COALESCE(cantidad, 0)
        WHEN tipo_movimiento = 'ZBRE' THEN -COALESCE(cantidad_pedido, 0)
        ELSE 0
    END AS cantidad_neta
FROM stg_comodatos
WHERE codigo_detalle IN ('1331070','1331080','1331100','1331120','1331140')
  AND tipo_movimiento IN ('YBFD','ZBRE')
  AND fecha_entrega IS NOT NULL;
  -- (sin filtro motivo_rechazo: las entregas YBFD cuentan aunque tengan motivo)

CREATE OR REPLACE VIEW comodato_resumen AS
SELECT
    m.cliente,
    MAX(m.razon_social)                                        AS razon_social,
    m.codigo_equipo,
    COUNT(*) FILTER (WHERE m.cantidad_neta > 0)                AS veces_entregado,
    COUNT(*) FILTER (WHERE m.cantidad_neta < 0)                AS veces_retirado,
    SUM(CASE WHEN m.cantidad_neta > 0 THEN m.cantidad_neta ELSE 0 END) AS piezas_entregadas,
    SUM(m.cantidad_neta)                                       AS piezas_vigentes,
    MIN(m.fecha_entrega) FILTER (WHERE m.cantidad_neta > 0)    AS primera_entrega,
    MAX(m.fecha_entrega) FILTER (WHERE m.cantidad_neta > 0)    AS ultima_entrega,
    CAST(
        DATE_DIFF('day',
            MIN(m.fecha_entrega) FILTER (WHERE m.cantidad_neta > 0),
            MAX(m.fecha_entrega) FILTER (WHERE m.cantidad_neta > 0))
        / NULLIF(COUNT(*) FILTER (WHERE m.cantidad_neta > 0) - 1, 0)
    AS INTEGER) AS dias_entre_entregas,
    MAX(mat.costo)                                             AS costo_unitario_equipo,
    ROUND(SUM(m.cantidad_neta) * MAX(mat.costo), 2)            AS inversion_comodato
FROM comodato_movimientos m
LEFT JOIN stg_materiales mat ON m.codigo_equipo = mat.codigo_detalle
GROUP BY m.cliente, m.codigo_equipo;

CREATE OR REPLACE VIEW facturacion_asociada AS
SELECT
    f.cliente, f.razon_social, r.codigo_equipo, r.descripcion_equipo,
    f.codigo_detalle AS codigo_consumible, r.descripcion_consumible,
    f.factura, f.fecha, DATE_TRUNC('month', f.fecha) AS periodo,
    f.cantidad, f.importe, m.costo,
    ROUND(f.cantidad * m.costo, 2)               AS costo_total,
    ROUND(f.importe - (f.cantidad * m.costo), 2) AS margen
FROM stg_facturacion f
INNER JOIN cfg_relacion_comodatos r ON f.codigo_detalle = r.codigo_consumible
LEFT  JOIN stg_materiales m         ON f.codigo_detalle = m.codigo_detalle;

CREATE OR REPLACE VIEW facturacion_periodos AS
WITH meses AS (SELECT DISTINCT cliente, codigo_equipo, periodo FROM facturacion_asociada),
ranked AS (
    SELECT cliente, codigo_equipo, periodo,
           ROW_NUMBER() OVER (PARTITION BY cliente, codigo_equipo ORDER BY periodo DESC) AS rn
    FROM meses)
SELECT cliente, codigo_equipo,
       MAX(CASE WHEN rn = 1 THEN periodo END) AS ultima_factura_mes,
       MAX(CASE WHEN rn = 2 THEN periodo END) AS penultima_factura_mes
FROM ranked GROUP BY cliente, codigo_equipo;

CREATE OR REPLACE VIEW facturacion_resumen AS
SELECT
    fa.cliente, MAX(fa.razon_social) AS razon_social, fa.codigo_equipo,
    ROUND(SUM(fa.cantidad), 2)                            AS bolsas_facturadas,
    ROUND(SUM(fa.importe),  2)                            AS importe_vendido,
    ROUND(SUM(fa.costo_total), 2)                         AS costo_bolsas,
    ROUND(SUM(fa.margen),   2)                            AS margen_total,
    COUNT(*)                                              AS num_lineas_factura,
    COUNT(DISTINCT fa.factura)                            AS num_facturas,
    COUNT(DISTINCT fa.periodo)                            AS meses_con_venta,
    MIN(fa.fecha)                                         AS primera_factura,
    MAX(fa.fecha)                                         AS ultima_factura,
    CAST(DATE_DIFF('day', MIN(fa.fecha), MAX(fa.fecha)) / NULLIF(COUNT(DISTINCT fa.factura) - 1, 0) AS INTEGER) AS dias_entre_facturas,
    ROUND(SUM(fa.importe) / NULLIF(COUNT(DISTINCT fa.periodo), 0), 2) AS ticket_prom_mensual,
    ROUND(SUM(fa.importe) / NULLIF(COUNT(DISTINCT fa.factura), 0), 2) AS importe_prom_factura,
    ROUND(SUM(fa.importe) FILTER (WHERE fa.fecha >  CURRENT_DATE - 90), 2) AS importe_90d,
    ROUND(SUM(fa.importe) FILTER (WHERE fa.fecha <= CURRENT_DATE - 90 AND fa.fecha > CURRENT_DATE - 180), 2) AS importe_90d_prev,
    ROUND(SUM(fa.importe) FILTER (WHERE fa.periodo = DATE_TRUNC('month', CURRENT_DATE)), 2) AS importe_mes_actual
FROM facturacion_asociada fa
GROUP BY fa.cliente, fa.codigo_equipo;

-- #############################################################################
-- ## CAPA 4 — DIRECCIÓN                                                       ##
-- #############################################################################
CREATE OR REPLACE VIEW v_direccion_detalle AS
SELECT
    cr.cliente, cr.razon_social, cr.codigo_equipo, e.descripcion_equipo, e.tipo,
    mat.codigo_grupo AS grupo, mat.codigo_subgrupo AS subgrupo,
    cr.veces_entregado, cr.veces_retirado, cr.piezas_entregadas, cr.piezas_vigentes,
    cr.primera_entrega, cr.ultima_entrega, cr.dias_entre_entregas,
    cr.costo_unitario_equipo, cr.inversion_comodato,
    COALESCE(fr.bolsas_facturadas, 0) AS bolsas_facturadas,
    COALESCE(fr.importe_vendido,   0) AS importe_vendido,
    COALESCE(fr.costo_bolsas,      0) AS costo_bolsas,
    COALESCE(fr.margen_total,      0) AS margen,
    fr.num_facturas, fr.num_lineas_factura, fr.dias_entre_facturas,
    COALESCE(fr.meses_con_venta, 0) AS meses_con_venta,
    fr.primera_factura, fp.penultima_factura_mes, fp.ultima_factura_mes, fr.ultima_factura,
    fr.ticket_prom_mensual, fr.importe_prom_factura,
    ROUND(COALESCE(fr.bolsas_facturadas,0) / NULLIF(cr.piezas_vigentes,0), 2) AS bolsas_por_equipo,
    ROUND(COALESCE(fr.bolsas_facturadas,0) / NULLIF(cr.piezas_vigentes,0) / NULLIF(fr.meses_con_venta,0), 2) AS bolsas_equipo_mes,
    ROUND(COALESCE(fr.margen_total,0) - cr.inversion_comodato, 2)             AS ganancia_neta,
    ROUND(COALESCE(fr.margen_total,0) / NULLIF(cr.inversion_comodato,0), 2)   AS roi,
    ROUND(COALESCE(fr.importe_vendido,0) / NULLIF(cr.inversion_comodato,0),2) AS facturacion_x_inversion,
    ROUND(100.0 * COALESCE(fr.margen_total,0) / NULLIF(fr.importe_vendido,0), 1) AS pct_margen,
    DATE_DIFF('day', cr.ultima_entrega, CURRENT_DATE)  AS dias_desde_ultima_entrega,
    DATE_DIFF('day', fr.ultima_factura, CURRENT_DATE)  AS dias_desde_ultima_factura,
    DATE_DIFF('day', cr.primera_entrega, CURRENT_DATE) AS dias_relacion,
    ROUND(DATE_DIFF('day', cr.primera_entrega, CURRENT_DATE) / 30.0, 1) AS meses_relacion,
    CASE WHEN e.tipo LIKE 'Canister%' AND COALESCE(fr.bolsas_facturadas,0) = 0
         THEN 'Ocioso (sin consumo)' ELSE '' END AS bandera_equipo,
    CASE
        WHEN e.tipo LIKE 'Carro%'                          THEN 'Complementario'
        WHEN fr.ultima_factura IS NULL                     THEN 'Sin venta'
        WHEN DATE_DIFF('day', fr.ultima_factura, CURRENT_DATE) <= 45  THEN 'Activo'
        WHEN DATE_DIFF('day', fr.ultima_factura, CURRENT_DATE) <= 90  THEN 'En seguimiento'
        WHEN DATE_DIFF('day', fr.ultima_factura, CURRENT_DATE) <= 150 THEN 'En riesgo'
        ELSE 'Inactivo'
    END AS status_actividad,
    CASE
        WHEN e.tipo LIKE 'Carro%'                          THEN 'Complementario'
        WHEN COALESCE(fr.margen_total,0) <= 0              THEN 'No rentable'
        WHEN fr.margen_total / NULLIF(cr.inversion_comodato,0) >= 5 THEN 'AAA'
        WHEN fr.margen_total / NULLIF(cr.inversion_comodato,0) >= 2 THEN 'Bueno'
        WHEN fr.margen_total / NULLIF(cr.inversion_comodato,0) >= 1 THEN 'Riesgo'
        ELSE 'No rentable'
    END AS clasificacion
FROM comodato_resumen cr
LEFT JOIN cfg_equipos e            ON cr.codigo_equipo = e.codigo_equipo
LEFT JOIN stg_materiales mat       ON cr.codigo_equipo = mat.codigo_detalle
LEFT JOIN facturacion_resumen fr   ON cr.cliente = fr.cliente AND cr.codigo_equipo = fr.codigo_equipo
LEFT JOIN facturacion_periodos fp  ON cr.cliente = fp.cliente AND cr.codigo_equipo = fp.codigo_equipo;

CREATE OR REPLACE VIEW v_direccion_por_equipo AS
SELECT
    codigo_equipo, descripcion_equipo, tipo,
    COUNT(DISTINCT cliente) AS num_clientes,
    SUM(piezas_entregadas)  AS piezas_entregadas,
    SUM(inversion_comodato) AS inversion_comodato,
    SUM(bolsas_facturadas)  AS bolsas_facturadas,
    SUM(importe_vendido)    AS importe_vendido,
    SUM(margen)             AS margen,
    ROUND(SUM(margen) / NULLIF(SUM(inversion_comodato),0), 2)      AS roi,
    ROUND(100.0 * SUM(margen) / NULLIF(SUM(importe_vendido),0), 1) AS pct_margen
FROM v_direccion_detalle
GROUP BY codigo_equipo, descripcion_equipo, tipo
ORDER BY importe_vendido DESC;

-- v_bolsas_resumen: ranking por BOLSA (consumible) — qué bolsa factura/margina más
CREATE OR REPLACE VIEW v_bolsas_resumen AS
SELECT
    fa.codigo_consumible,
    fa.descripcion_consumible,
    fa.codigo_equipo,
    fa.descripcion_equipo,
    COUNT(DISTINCT fa.cliente)                              AS num_clientes,
    ROUND(SUM(fa.cantidad), 2)                              AS bolsas_facturadas,
    ROUND(SUM(fa.importe),  2)                              AS importe_vendido,
    ROUND(SUM(fa.costo_total), 2)                           AS costo_total,
    ROUND(SUM(fa.margen),   2)                              AS margen_total,
    ROUND(100.0 * SUM(fa.margen) / NULLIF(SUM(fa.importe),0), 1) AS pct_margen,
    ROUND(SUM(fa.importe) / NULLIF(SUM(fa.cantidad),0), 2)  AS precio_prom_bolsa
FROM facturacion_asociada fa
GROUP BY fa.codigo_consumible, fa.descripcion_consumible, fa.codigo_equipo, fa.descripcion_equipo
ORDER BY importe_vendido DESC;

CREATE OR REPLACE VIEW v_direccion_tendencia AS
WITH ent AS (
    SELECT cliente, codigo_equipo, periodo_entrega AS periodo, SUM(cantidad_neta) AS piezas_netas_mes
    FROM comodato_movimientos GROUP BY cliente, codigo_equipo, periodo_entrega),
ven AS (
    SELECT cliente, codigo_equipo, periodo,
           SUM(cantidad) AS bolsas_mes, SUM(importe) AS importe_mes, SUM(margen) AS margen_mes
    FROM facturacion_asociada GROUP BY cliente, codigo_equipo, periodo)
SELECT
    COALESCE(ent.cliente, ven.cliente)             AS cliente,
    COALESCE(ent.codigo_equipo, ven.codigo_equipo) AS codigo_equipo,
    e.descripcion_equipo,
    COALESCE(ent.periodo, ven.periodo)             AS periodo,
    COALESCE(ent.piezas_netas_mes, 0)              AS piezas_netas_mes,
    COALESCE(ven.bolsas_mes, 0)                    AS bolsas_mes,
    COALESCE(ven.importe_mes, 0)                   AS importe_mes,
    COALESCE(ven.margen_mes, 0)                    AS margen_mes
FROM ent
FULL OUTER JOIN ven ON ent.cliente = ven.cliente AND ent.codigo_equipo = ven.codigo_equipo AND ent.periodo = ven.periodo
LEFT JOIN cfg_equipos e ON COALESCE(ent.codigo_equipo, ven.codigo_equipo) = e.codigo_equipo
ORDER BY cliente, codigo_equipo, periodo;

-- #############################################################################
-- ## CAPA 4.5 — CRECIMIENTO (tendencia mensual; usa v_direccion_tendencia)    ##
-- #############################################################################
CREATE OR REPLACE VIEW v_crecimiento AS
WITH idx AS (
    SELECT cliente, codigo_equipo, descripcion_equipo, periodo, importe_mes, bolsas_mes, piezas_netas_mes,
           DATE_DIFF('month', MIN(periodo) OVER (PARTITION BY cliente, codigo_equipo), periodo) AS m_idx,
           MAX(periodo) OVER (PARTITION BY cliente, codigo_equipo) AS ult_periodo
    FROM v_direccion_tendencia),
vent AS (
    SELECT cliente, codigo_equipo, descripcion_equipo,
        COUNT(*) FILTER (WHERE importe_mes <> 0) AS meses_con_venta,
        MIN(periodo) AS primer_periodo, MAX(periodo) AS ultimo_periodo,
        SUM(importe_mes)      FILTER (WHERE periodo >  ult_periodo - INTERVAL 3 MONTH)                                             AS fact_u3,
        SUM(importe_mes)      FILTER (WHERE periodo <= ult_periodo - INTERVAL 3 MONTH AND periodo > ult_periodo - INTERVAL 6 MONTH) AS fact_p3,
        SUM(bolsas_mes)       FILTER (WHERE periodo >  ult_periodo - INTERVAL 3 MONTH)                                             AS bolsas_u3,
        SUM(bolsas_mes)       FILTER (WHERE periodo <= ult_periodo - INTERVAL 3 MONTH AND periodo > ult_periodo - INTERVAL 6 MONTH) AS bolsas_p3,
        SUM(piezas_netas_mes) FILTER (WHERE periodo >  ult_periodo - INTERVAL 3 MONTH)                                             AS comod_u3,
        SUM(piezas_netas_mes) FILTER (WHERE periodo <= ult_periodo - INTERVAL 3 MONTH AND periodo > ult_periodo - INTERVAL 6 MONTH) AS comod_p3,
        REGR_SLOPE(importe_mes, m_idx)      AS pendiente_fact,
        REGR_SLOPE(piezas_netas_mes, m_idx) AS pendiente_comod
    FROM idx GROUP BY cliente, codigo_equipo, descripcion_equipo),
calc AS (
    SELECT v.*,
        ROUND(100.0*(fact_u3-fact_p3)/NULLIF(fact_p3,0),1)     AS crec_fact_pct,
        ROUND(100.0*(bolsas_u3-bolsas_p3)/NULLIF(bolsas_p3,0),1) AS crec_bolsas_pct,
        ROUND(100.0*(comod_u3-comod_p3)/NULLIF(comod_p3,0),1)  AS crec_comod_pct
    FROM vent v)
SELECT
    cliente, codigo_equipo, descripcion_equipo, meses_con_venta, primer_periodo, ultimo_periodo,
    ROUND(fact_u3,2) AS fact_ult_3m, ROUND(fact_p3,2) AS fact_prev_3m, crec_fact_pct,
    ROUND(bolsas_u3,0) AS bolsas_ult_3m, ROUND(bolsas_p3,0) AS bolsas_prev_3m, crec_bolsas_pct,
    comod_u3 AS comod_ult_3m, comod_p3 AS comod_prev_3m, crec_comod_pct,
    ROUND(pendiente_fact,2) AS pendiente_fact, ROUND(pendiente_comod,3) AS pendiente_comod,
    CASE
        WHEN fact_p3 IS NULL OR fact_p3=0 THEN CASE WHEN fact_u3>0 THEN 'Nuevo / Reactivando' ELSE 'Sin datos' END
        WHEN crec_fact_pct >= 30 AND pendiente_fact > 0 THEN 'Acelerando'
        WHEN crec_fact_pct >= 10 THEN 'Creciendo'
        WHEN crec_fact_pct <= -30 THEN 'Cayendo fuerte'
        WHEN crec_fact_pct <= -10 THEN 'Desacelerando'
        ELSE 'Estable'
    END AS nivel_crec_fact,
    CASE
        WHEN comod_p3 IS NULL OR comod_p3=0 THEN CASE WHEN comod_u3>0 THEN 'Nuevas entregas' ELSE 'Sin nuevas entregas' END
        WHEN crec_comod_pct >= 20 THEN 'Aumentando parque'
        WHEN crec_comod_pct <= -20 THEN 'Retirando parque'
        ELSE 'Parque estable'
    END AS nivel_crec_comod,
    CASE
        WHEN COALESCE(comod_u3,0)=0 AND COALESCE(comod_p3,0)=0 THEN 'Sin movimiento de equipo'
        WHEN COALESCE(crec_comod_pct,0) >= 20 AND COALESCE(crec_fact_pct,0) < 10 THEN 'Equipo crece > factura (vigilar ROI)'
        WHEN COALESCE(crec_fact_pct,0) >= COALESCE(crec_comod_pct,0) THEN 'Factura crece ≥ equipo (eficiente)'
        ELSE 'Equilibrado'
    END AS balance_crec
FROM calc;

CREATE OR REPLACE VIEW v_crecimiento_cliente AS
WITH idx AS (
    SELECT cliente, periodo, importe_mes, piezas_netas_mes,
           DATE_DIFF('month', MIN(periodo) OVER (PARTITION BY cliente), periodo) AS m_idx,
           MAX(periodo) OVER (PARTITION BY cliente) AS ult_periodo
    FROM (SELECT cliente, periodo, SUM(importe_mes) AS importe_mes, SUM(piezas_netas_mes) AS piezas_netas_mes
          FROM v_direccion_tendencia GROUP BY cliente, periodo)),
vent AS (
    SELECT cliente,
        SUM(importe_mes)      FILTER (WHERE periodo >  ult_periodo - INTERVAL 3 MONTH)                                             AS fact_u3,
        SUM(importe_mes)      FILTER (WHERE periodo <= ult_periodo - INTERVAL 3 MONTH AND periodo > ult_periodo - INTERVAL 6 MONTH) AS fact_p3,
        SUM(piezas_netas_mes) FILTER (WHERE periodo >  ult_periodo - INTERVAL 3 MONTH)                                             AS comod_u3,
        SUM(piezas_netas_mes) FILTER (WHERE periodo <= ult_periodo - INTERVAL 3 MONTH AND periodo > ult_periodo - INTERVAL 6 MONTH) AS comod_p3,
        REGR_SLOPE(importe_mes, m_idx) AS pendiente_fact
    FROM idx GROUP BY cliente)
SELECT cliente,
    ROUND(fact_u3,2) AS fact_ult_3m, ROUND(fact_p3,2) AS fact_prev_3m,
    ROUND(100.0*(fact_u3-fact_p3)/NULLIF(fact_p3,0),1) AS crec_fact_pct,
    ROUND(100.0*(comod_u3-comod_p3)/NULLIF(comod_p3,0),1) AS crec_comod_pct,
    ROUND(pendiente_fact,2) AS pendiente_fact,
    CASE
        WHEN fact_p3 IS NULL OR fact_p3=0 THEN CASE WHEN fact_u3>0 THEN 'Nuevo / Reactivando' ELSE 'Sin datos' END
        WHEN 100.0*(fact_u3-fact_p3)/NULLIF(fact_p3,0) >= 30 AND pendiente_fact>0 THEN 'Acelerando'
        WHEN 100.0*(fact_u3-fact_p3)/NULLIF(fact_p3,0) >= 10 THEN 'Creciendo'
        WHEN 100.0*(fact_u3-fact_p3)/NULLIF(fact_p3,0) <= -30 THEN 'Cayendo fuerte'
        WHEN 100.0*(fact_u3-fact_p3)/NULLIF(fact_p3,0) <= -10 THEN 'Desacelerando'
        ELSE 'Estable'
    END AS nivel_crec_fact
FROM vent;

-- #############################################################################
-- ## CAPA 5 — EJECUTIVO                                                       ##
-- #############################################################################
CREATE OR REPLACE VIEW v_ejecutivo_cliente AS
WITH base AS (
    SELECT
        cr.cliente, MAX(cr.razon_social) AS razon_social,
        SUM(cr.piezas_vigentes) AS equipos_vigentes,
        COUNT(*) FILTER (WHERE e.tipo LIKE 'Canister%') AS modelos_canister,
        SUM(cr.inversion_comodato) AS inversion_total,
        MIN(cr.primera_entrega) AS primera_entrega, MAX(cr.ultima_entrega) AS ultima_entrega,
        SUM(COALESCE(fr.bolsas_facturadas,0)) AS bolsas_total,
        SUM(COALESCE(fr.importe_vendido,0)) AS importe_vendido,
        SUM(COALESCE(fr.margen_total,0)) AS margen,
        SUM(COALESCE(fr.num_facturas,0)) AS num_facturas,
        MAX(fr.ultima_factura) AS ultima_factura,
        SUM(COALESCE(fr.importe_90d,0)) AS importe_90d,
        SUM(COALESCE(fr.importe_90d_prev,0)) AS importe_90d_prev
    FROM comodato_resumen cr
    LEFT JOIN cfg_equipos e          ON cr.codigo_equipo = e.codigo_equipo
    LEFT JOIN facturacion_resumen fr ON cr.cliente = fr.cliente AND cr.codigo_equipo = fr.codigo_equipo
    GROUP BY cr.cliente),
calc AS (
    SELECT b.*,
        ROUND(b.margen - b.inversion_total, 2) AS ganancia_neta,
        ROUND(b.margen / NULLIF(b.inversion_total,0), 2) AS roi,
        ROUND(100.0 * b.margen / NULLIF(b.importe_vendido,0), 1) AS pct_margen,
        DATE_DIFF('day', b.ultima_factura, CURRENT_DATE) AS dias_sin_comprar,
        ROUND(DATE_DIFF('day', b.primera_entrega, CURRENT_DATE) / 30.0, 1) AS meses_relacion,
        CASE
            WHEN b.ultima_factura IS NULL THEN 'Sin compra'
            WHEN DATE_DIFF('day', b.ultima_factura, CURRENT_DATE) <= 45  THEN 'Activo'
            WHEN DATE_DIFF('day', b.ultima_factura, CURRENT_DATE) <= 90  THEN 'En seguimiento'
            WHEN DATE_DIFF('day', b.ultima_factura, CURRENT_DATE) <= 150 THEN 'En riesgo'
            ELSE 'Inactivo'
        END AS status_actividad,
        CASE
            WHEN b.importe_90d = 0 AND b.importe_90d_prev = 0 THEN 'Sin actividad reciente'
            WHEN b.importe_90d_prev = 0 AND b.importe_90d > 0 THEN 'Reactivando'
            WHEN b.importe_90d >= b.importe_90d_prev * 1.10   THEN 'Creciendo'
            WHEN b.importe_90d <= b.importe_90d_prev * 0.70   THEN 'Cayendo'
            ELSE 'Estable'
        END AS tendencia
    FROM base b)
SELECT
    cliente, razon_social, equipos_vigentes, modelos_canister, inversion_total,
    bolsas_total, importe_vendido, margen, ganancia_neta, roi, pct_margen,
    num_facturas, ultima_factura, dias_sin_comprar, meses_relacion, tendencia, status_actividad,
    CASE
        WHEN bolsas_total = 0 THEN 'Sin retorno aún'
        WHEN roi >= 1         THEN 'Rentable'
        WHEN margen > 0       THEN 'Margen positivo (no cubre inversión)'
        ELSE 'No rentable'
    END AS es_rentable,
    CASE
        WHEN status_actividad = 'Sin compra'                 THEN 'Por activar'
        WHEN status_actividad IN ('Activo','En seguimiento') THEN 'N.A. (vigente)'
        WHEN ganancia_neta > 0                               THEN 'Sí - alta prioridad'
        WHEN margen > 0                                      THEN 'Sí - media'
        ELSE 'Baja'
    END AS recuperable,
    CASE
        WHEN bolsas_total = 0                                     THEN 'Sin compra'
        WHEN status_actividad = 'Activo'  AND roi >= 5            THEN 'Estrella'
        WHEN status_actividad = 'Activo'  AND roi >= 1            THEN 'Sano'
        WHEN status_actividad = 'Activo'                          THEN 'Activo bajo margen'
        WHEN status_actividad = 'En seguimiento'                  THEN 'Vigilar'
        WHEN status_actividad = 'En riesgo'                       THEN 'En riesgo'
        WHEN status_actividad = 'Inactivo' AND ganancia_neta > 0  THEN 'Recuperar (rentable)'
        ELSE 'Inactivo'
    END AS diagnostico,
    CASE
        WHEN bolsas_total = 0 THEN 'Equipo colocado sin consumo: activar primer pedido de bolsas.'
        WHEN status_actividad = 'Activo'  AND roi >= 5 THEN 'Cuenta clave: asegurar abasto y proponer más equipos.'
        WHEN status_actividad = 'Activo'  AND roi >= 1 THEN 'Sano: mantener seguimiento normal.'
        WHEN status_actividad = 'Activo' THEN 'Activo pero bajo margen: revisar precio/mezcla.'
        WHEN status_actividad = 'En seguimiento' THEN 'Consumo desacelerando: confirmar próximo pedido.'
        WHEN status_actividad = 'En riesgo' THEN 'Contactar: lleva ' || CAST(dias_sin_comprar AS VARCHAR) || ' días sin comprar.'
        WHEN status_actividad = 'Inactivo' AND ganancia_neta > 0 THEN 'Reactivar: ya es rentable y dejó de comprar.'
        ELSE 'Evaluar retiro de equipo o plan de reactivación.'
    END AS accion_sugerida,
    CASE
        WHEN status_actividad = 'En riesgo' THEN 1
        WHEN status_actividad = 'Inactivo' AND ganancia_neta > 0 THEN 1
        WHEN status_actividad = 'En seguimiento' THEN 2
        WHEN bolsas_total = 0 THEN 3
        WHEN status_actividad = 'Activo' THEN 4
        ELSE 5
    END AS prioridad
FROM calc
ORDER BY prioridad ASC, importe_vendido DESC;

CREATE OR REPLACE VIEW v_ejecutivo_kpi AS
SELECT
    COUNT(*) AS clientes,
    COUNT(*) FILTER (WHERE bolsas_total = 0) AS clientes_sin_compra,
    COUNT(*) FILTER (WHERE status_actividad = 'En riesgo') AS clientes_en_riesgo,
    COUNT(*) FILTER (WHERE status_actividad = 'Inactivo')  AS clientes_inactivos,
    COUNT(*) FILTER (WHERE es_rentable = 'Rentable') AS clientes_rentables,
    SUM(inversion_total) AS inversion_total,
    SUM(importe_vendido) AS importe_vendido,
    SUM(margen) AS margen,
    ROUND(SUM(margen) / NULLIF(SUM(inversion_total),0), 2) AS roi,
    ROUND(100.0 * SUM(margen) / NULLIF(SUM(importe_vendido),0),1) AS pct_margen
FROM v_ejecutivo_cliente;

-- #############################################################################
-- ## CAPA 6 — VISTA 360 (con columnas de crecimiento)                         ##
-- #############################################################################
-- =============================================================================
--  CATÁLOGOS Zona / Ejecutivo / Grupo de cliente  (tabla fuente: ejecutivos_zona)
-- =============================================================================
-- stg_ejecutivos: normaliza la tabla ejecutivos_zona
CREATE OR REPLACE VIEW stg_ejecutivos AS
SELECT
    TRY_CAST(REGEXP_REPLACE(CAST("Zona"    AS VARCHAR), '\.0$', '') AS BIGINT) AS zona_num,
    TRIM(CAST("Ejecutivo" AS VARCHAR))                                          AS ejecutivo,
    TRY_CAST(REGEXP_REPLACE(CAST("Gpo Cte" AS VARCHAR), '\.0$', '') AS BIGINT) AS gpo_cte,
    TRIM(CAST("Grupo Cliente" AS VARCHAR))                                      AS grupo_cliente
FROM ejecutivos_zona
WHERE "Zona" IS NOT NULL;

-- cat_zona: cada zona -> su ejecutivo (único) + zona en formato "000"
CREATE OR REPLACE VIEW cat_zona AS
SELECT
    zona_num,
    LPAD(CAST(zona_num AS VARCHAR), 3, '0') AS zona_fmt,
    MAX(ejecutivo)                          AS ejecutivo
FROM stg_ejecutivos
WHERE zona_num IS NOT NULL
GROUP BY zona_num;

-- cat_grupo: Gpo Cte -> Grupo Cliente (texto). Si un código tiene varios textos, gana el más frecuente.
CREATE OR REPLACE VIEW cat_grupo AS
WITH cnt AS (
    SELECT gpo_cte, grupo_cliente, COUNT(*) AS n
    FROM stg_ejecutivos WHERE gpo_cte IS NOT NULL
    GROUP BY gpo_cte, grupo_cliente
), rk AS (
    SELECT gpo_cte, grupo_cliente,
           ROW_NUMBER() OVER (PARTITION BY gpo_cte ORDER BY n DESC, grupo_cliente) AS rn
    FROM cnt
)
SELECT gpo_cte, grupo_cliente FROM rk WHERE rn = 1;

-- cliente_atributos: por cliente, zona y grupo predominantes (moda por # de facturas, desempata la más reciente)
CREATE OR REPLACE VIEW cliente_atributos AS
WITH
zc   AS (SELECT cliente, gpo_vdor, COUNT(*) n, MAX(fecha) fmax
         FROM stg_facturacion WHERE gpo_vdor IS NOT NULL GROUP BY cliente, gpo_vdor),
zpk  AS (SELECT cliente, gpo_vdor AS zona_num,
                ROW_NUMBER() OVER (PARTITION BY cliente ORDER BY n DESC, fmax DESC) rn FROM zc),
gc   AS (SELECT cliente, grp_cliente, COUNT(*) n, MAX(fecha) fmax
         FROM stg_facturacion WHERE grp_cliente IS NOT NULL GROUP BY cliente, grp_cliente),
gpk  AS (SELECT cliente, grp_cliente AS gpo_cte,
                ROW_NUMBER() OVER (PARTITION BY cliente ORDER BY n DESC, fmax DESC) rn FROM gc)
SELECT
    COALESCE(z.cliente, g.cliente)        AS cliente,
    z.zona_num,
    cz.zona_fmt,
    cz.ejecutivo,
    g.gpo_cte,
    cg.grupo_cliente
FROM      (SELECT * FROM zpk WHERE rn=1) z
FULL JOIN (SELECT * FROM gpk WHERE rn=1) g ON z.cliente = g.cliente
LEFT JOIN cat_zona  cz ON z.zona_num = cz.zona_num
LEFT JOIN cat_grupo cg ON g.gpo_cte  = cg.gpo_cte;

CREATE OR REPLACE VIEW v_seguimiento_360 AS
WITH
claves AS (
    SELECT cliente, codigo_equipo FROM comodato_resumen
    UNION
    SELECT cliente, codigo_equipo FROM facturacion_resumen
),
b AS (
    SELECT
        k.cliente,
        COALESCE(cr.razon_social, fr.razon_social)        AS razon_social,
        k.codigo_equipo                                   AS material_comodato,
        e.descripcion_equipo                              AS descripcion_comodato,
        e.tipo,
        COALESCE(cr.piezas_vigentes, 0)                   AS cantidad_comodato,
        cr.dias_entre_entregas                            AS renueva_comodato_dias,
        COALESCE(cr.inversion_comodato, 0)                AS inversion_comodato,
        COALESCE(fr.bolsas_facturadas, 0)                 AS bolsas_facturadas,
        COALESCE(fr.importe_vendido,   0)                 AS facturacion_total,
        COALESCE(fr.margen_total,      0)                 AS margen_total,
        fr.ultima_factura, fr.ticket_prom_mensual, fr.meses_con_venta,
        COALESCE(fr.importe_mes_actual, 0)                AS importe_mes_actual,
        COALESCE(fr.importe_90d, 0)                       AS importe_90d,
        COALESCE(fr.importe_90d_prev, 0)                  AS importe_90d_prev,
        cr.ultima_entrega, cr.primera_entrega,
        (cr.cliente IS NULL)                              AS sin_comodato
    FROM claves k
    LEFT JOIN comodato_resumen   cr ON k.cliente = cr.cliente AND k.codigo_equipo = cr.codigo_equipo
    LEFT JOIN facturacion_resumen fr ON k.cliente = fr.cliente AND k.codigo_equipo = fr.codigo_equipo
    LEFT JOIN cfg_equipos        e  ON k.codigo_equipo = e.codigo_equipo
),
fact_mensual AS (
    SELECT fa.cliente, fa.codigo_equipo, fa.periodo,
        SUM(fa.cantidad) AS bolsas_mes, SUM(fa.importe) AS importe_mes,
        ROW_NUMBER() OVER (PARTITION BY fa.cliente, fa.codigo_equipo ORDER BY fa.periodo DESC) AS rn
    FROM facturacion_asociada fa GROUP BY fa.cliente, fa.codigo_equipo, fa.periodo),
fact_last AS (
    SELECT cliente, codigo_equipo,
        MAX(CASE WHEN rn=1 THEN STRFTIME(periodo,'%m/%Y') END) AS ult_mes_fact,
        MAX(CASE WHEN rn=1 THEN bolsas_mes END) AS ult_mes_fact_cant,
        MAX(CASE WHEN rn=1 THEN importe_mes END) AS ult_mes_fact_imp,
        MAX(CASE WHEN rn=2 THEN STRFTIME(periodo,'%m/%Y') END) AS pen_mes_fact,
        MAX(CASE WHEN rn=2 THEN bolsas_mes END) AS pen_mes_fact_cant,
        MAX(CASE WHEN rn=2 THEN importe_mes END) AS pen_mes_fact_imp
    FROM fact_mensual GROUP BY cliente, codigo_equipo),
comod_mensual AS (
    SELECT cm.cliente, cm.codigo_equipo, cm.periodo_entrega AS periodo,
        SUM(cm.cantidad_neta) AS piezas_netas_mes,
        ROW_NUMBER() OVER (PARTITION BY cm.cliente, cm.codigo_equipo ORDER BY cm.periodo_entrega DESC) AS rn
    FROM comodato_movimientos cm WHERE cm.cantidad_neta > 0
    GROUP BY cm.cliente, cm.codigo_equipo, cm.periodo_entrega),
comod_last AS (
    SELECT cliente, codigo_equipo,
        MAX(CASE WHEN rn=1 THEN STRFTIME(periodo,'%m/%Y') END) AS ult_mes_comod,
        MAX(CASE WHEN rn=1 THEN piezas_netas_mes END) AS ult_mes_comod_cant,
        MAX(CASE WHEN rn=2 THEN STRFTIME(periodo,'%m/%Y') END) AS pen_mes_comod,
        MAX(CASE WHEN rn=2 THEN piezas_netas_mes END) AS pen_mes_comod_cant
    FROM comod_mensual GROUP BY cliente, codigo_equipo),
frecuencias AS (
    SELECT b.cliente, b.material_comodato,
        CASE WHEN b.meses_con_venta <= 1 THEN NULL
             ELSE ROUND(DATE_DIFF('month', MIN(fa.fecha), MAX(fa.fecha)) * 1.0 / (b.meses_con_venta - 1), 1) END AS meses_entre_facturas,
        ROUND(b.renueva_comodato_dias / 30.44, 1) AS meses_entre_comodato
    FROM b LEFT JOIN facturacion_asociada fa ON b.cliente = fa.cliente AND b.material_comodato = fa.codigo_equipo
    GROUP BY b.cliente, b.material_comodato, b.meses_con_venta, b.renueva_comodato_dias),
c AS (
    SELECT b.*,
        ROUND(b.bolsas_facturadas / NULLIF(b.cantidad_comodato,0), 2) AS bolsas_por_canister,
        ROUND(b.margen_total      / NULLIF(b.cantidad_comodato,0), 2) AS margen_por_canister,
        ROUND(b.margen_total      / NULLIF(b.meses_con_venta,0),   2) AS margen_prom_mensual,
        ROUND(b.margen_total      / NULLIF(b.inversion_comodato,0),2) AS roi,
        ROUND(b.margen_total - b.inversion_comodato, 2)              AS ganancia_neta,
        DATE_DIFF('day', b.ultima_factura, CURRENT_DATE)             AS dias_sin_compra,
        CASE
            WHEN b.tipo LIKE 'Carro%'                       THEN 'Complementario'
            WHEN b.ultima_factura IS NULL OR b.facturacion_total = 0 THEN 'Sin compra'
            WHEN DATE_DIFF('day', b.ultima_factura, CURRENT_DATE) <= 45  THEN 'Activo'
            WHEN DATE_DIFF('day', b.ultima_factura, CURRENT_DATE) <= 90  THEN 'En seguimiento'
            WHEN DATE_DIFF('day', b.ultima_factura, CURRENT_DATE) <= 150 THEN 'En riesgo'
            ELSE 'Inactivo'
        END AS status_actividad,
        CASE
            WHEN b.tipo LIKE 'Carro%'                            THEN 'N.A.'
            WHEN b.importe_90d = 0 AND b.importe_90d_prev = 0    THEN 'Sin actividad reciente'
            WHEN b.importe_90d_prev = 0 AND b.importe_90d > 0    THEN 'Reactivando'
            WHEN b.importe_90d >= b.importe_90d_prev * 1.10      THEN 'Creciendo'
            WHEN b.importe_90d <= b.importe_90d_prev * 0.70      THEN 'Cayendo'
            ELSE 'Estable'
        END AS tendencia
    FROM b)
SELECT
    c.cliente, c.razon_social, c.material_comodato, c.descripcion_comodato,
    c.cantidad_comodato, c.inversion_comodato, c.bolsas_facturadas, c.facturacion_total,
    c.margen_total, c.bolsas_por_canister, c.margen_por_canister, c.ultima_factura,
    c.ticket_prom_mensual, c.margen_prom_mensual, c.roi, c.dias_sin_compra,
    c.tendencia, c.status_actividad,
    CASE WHEN c.sin_comodato THEN 'Sí' ELSE 'No' END                   AS "Sin comodato asignado",
    CASE
        WHEN c.tipo LIKE 'Carro%'                          THEN 'Complementario'
        WHEN c.sin_comodato AND c.facturacion_total > 0    THEN 'Factura sin comodato'
        WHEN c.bolsas_facturadas = 0                       THEN 'Sin retorno aún'
        WHEN c.roi >= 1                                    THEN 'Rentable'
        WHEN c.margen_total > 0                            THEN 'Margen positivo (no cubre inversión)'
        ELSE 'No rentable'
    END AS rentable,
    CASE
        WHEN c.tipo LIKE 'Carro%'                              THEN 'N.A.'
        WHEN c.sin_comodato                                    THEN 'N.A. (sin equipo)'
        WHEN c.status_actividad = 'Sin compra'                 THEN 'Por activar'
        WHEN c.status_actividad IN ('Activo','En seguimiento') THEN 'N.A. (vigente)'
        WHEN c.ganancia_neta > 0                               THEN 'Sí - alta prioridad'
        WHEN c.margen_total > 0                                THEN 'Sí - media'
        ELSE 'Baja'
    END AS recuperable,
    CASE
        WHEN c.tipo LIKE 'Carro%' THEN 'Equipo habilitador (sin facturación directa).'
        WHEN c.sin_comodato AND c.facturacion_total > 0 THEN 'Compra bolsas SIN canister en comodato: colocar equipo o validar atribución del consumible.'
        WHEN c.bolsas_facturadas = 0 THEN 'Canister sin consumo: activar primer pedido de bolsas.'
        WHEN c.status_actividad = 'Activo' AND c.roi >= 5 THEN 'Canister clave: asegurar abasto.'
        WHEN c.status_actividad = 'Activo' AND c.roi >= 1 THEN 'Sano: seguimiento normal.'
        WHEN c.status_actividad = 'Activo' THEN 'Activo bajo margen: revisar precio/mezcla.'
        WHEN c.status_actividad = 'En seguimiento' THEN 'Consumo desacelerando: confirmar próximo pedido.'
        WHEN c.status_actividad = 'En riesgo' THEN 'Contactar: ' || CAST(c.dias_sin_compra AS VARCHAR) || ' días sin comprar.'
        WHEN c.status_actividad = 'Inactivo' AND c.ganancia_neta > 0 THEN 'Reactivar: ya es rentable y dejó de comprar.'
        ELSE 'Evaluar retiro de equipo o reactivación.'
    END AS accion_sugerida,
    fl.ult_mes_fact      AS "Ultimo mes de facturación",
    fl.ult_mes_fact_cant AS "Cantidad ultimo mes de facturación",
    fl.ult_mes_fact_imp  AS "Importe facturación ultimo mes",
    fl.pen_mes_fact      AS "Penultimo mes de facturación",
    fl.pen_mes_fact_cant AS "Cantidad penultimo mes de facturación",
    fl.pen_mes_fact_imp  AS "Importe facturación Penultimo mes",
    COALESCE(fr.meses_entre_facturas, 0) AS "Cada cuantos meses se le factura",
    cl.ult_mes_comod      AS "Ultima fecha de entrega de comodato",
    cl.ult_mes_comod_cant AS "Cantidad ultima comodato",
    cl.pen_mes_comod      AS "Penultima fecha de entrega de comodato",
    cl.pen_mes_comod_cant AS "Cantidad penultima comodato",
    c.renueva_comodato_dias AS "Cada cuantos días se le entrega comodato",
    fr.meses_entre_comodato AS "Cada cuantos meses se le entrega comodato",
    cg.crec_fact_pct   AS "Crecimiento facturación %",
    cg.nivel_crec_fact AS "Nivel crecimiento facturación",
    cg.crec_comod_pct  AS "Crecimiento entrega comodato %",
    cg.nivel_crec_comod AS "Nivel crecimiento comodato",
    cg.balance_crec    AS "Balance factura vs equipo",
    cg.pendiente_fact  AS "Pendiente facturación (tendencia)",
    ca.zona_fmt       AS "Zona",
    ca.ejecutivo      AS "Ejecutivo",
    ca.grupo_cliente  AS "Grupo de cliente",
    c.importe_mes_actual
FROM c
LEFT JOIN fact_last fl   ON c.cliente = fl.cliente AND c.material_comodato = fl.codigo_equipo
LEFT JOIN comod_last cl  ON c.cliente = cl.cliente AND c.material_comodato = cl.codigo_equipo
LEFT JOIN frecuencias fr ON c.cliente = fr.cliente AND c.material_comodato = fr.material_comodato
LEFT JOIN v_crecimiento cg ON c.cliente = cg.cliente AND c.material_comodato = cg.codigo_equipo
LEFT JOIN cliente_atributos ca ON c.cliente = ca.cliente
-- Excluir "fantasmas": facturó sin comodato y devolvió todo (neto 0). No hay equipo ni material.
WHERE NOT (c.sin_comodato AND c.facturacion_total = 0)
ORDER BY c.facturacion_total DESC;