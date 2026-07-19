"""
reportes/exportador_html.py - v3

Genera un único archivo HTML interactivo a partir de uno o más DataFrames
del sugeridor de DEGASA. Los KPIs y formato condicional usan lógica
específica del negocio (no detección genérica).

Hojas reconocidas con lógica específica:
  - Todas las Sugerencias
  - Resumen Sin Sugerencias
  - Reporte de Consumo
  - Inventario por condicion
  - Detalle Lotes Corta Caducidad

Hojas no reconocidas → fallback genérico (KPI total filas + tabla).

API pública:
  exportar_a_html(hojas, titulo, config_hojas=None) -> bytes
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime
from typing import Iterable

import numpy as np
import pandas as pd


# ============================================================================
# UTILIDADES BÁSICAS
# ============================================================================
def _df_a_records(df: pd.DataFrame) -> list[list]:
    """Convierte un DataFrame a lista de listas JSON-safe."""
    if df is None or df.empty:
        return []
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d").where(df[col].notna(), None)
    df = df.replace({np.nan: None, pd.NaT: None, np.inf: None, -np.inf: None})
    out = []
    for fila in df.itertuples(index=False, name=None):
        out.append([_v(v) for v in fila])
    return out


def _v(v):
    if v is None:
        return None
    if isinstance(v, float):
        if pd.isna(v) or v in (np.inf, -np.inf):
            return None
        if v != int(v):
            return round(v, 4)
        return v
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return None if pd.isna(f) else f
    if isinstance(v, (pd.Timestamp, datetime)):
        try:
            return v.strftime("%Y-%m-%d")
        except Exception:
            return str(v)
    if isinstance(v, str):
        return v
    return str(v)


def _tipo(df: pd.DataFrame, col: str) -> str:
    s = df[col]
    if pd.api.types.is_numeric_dtype(s):
        return "num"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "date"
    if s.dtype == object:
        nn = s.dropna().astype(str).head(20)
        if len(nn) > 0:
            if nn.str.match(r"^\d{4}-\d{2}-\d{2}").mean() > 0.7:
                return "date"
            if nn.str.match(r"^\d{4}-\d{2}$").mean() > 0.7:
                return "yearmonth"
    return "text"


def _meses_desde(yearmonth_str: str, hoy: datetime) -> int | None:
    """Convierte 'YYYY-MM' a número de meses desde hoy. None si no parsea."""
    if not yearmonth_str or not isinstance(yearmonth_str, str):
        return None
    m = re.match(r"^(\d{4})-(\d{1,2})$", yearmonth_str.strip())
    if not m:
        return None
    try:
        y, mo = int(m.group(1)), int(m.group(2))
        return (hoy.year - y) * 12 + (hoy.month - mo)
    except Exception:
        return None


def _fmt_money(v: float | None) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if abs(v) >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"${v/1_000:.1f}K"
    return f"${v:,.0f}"


def _fmt_int(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{int(v):,}"


# ============================================================================
# LÓGICA ESPECÍFICA POR HOJA
# ============================================================================
def _analizar_todas_sugerencias(df: pd.DataFrame, hoy: datetime) -> dict:
    """KPIs ejecutivos para 'Todas las Sugerencias':
       - Valor pendiente SIN FUENTE: Σ(Cantidad pendiente × Precio) donde Fuente está vacía.
       - Pedidos abiertos: count distinct(Pedido).
       - Material con más pendientes: top 1 por Σ Cantidad pendiente.
    Devuelve dict con kpis y graficas.
    """
    kpis = []
    graficas = []
    notas = []

    col_cant = "Cantidad pendiente" if "Cantidad pendiente" in df.columns else None
    col_precio = "Precio" if "Precio" in df.columns else None
    col_fuente = "Fuente" if "Fuente" in df.columns else None
    col_pedido = "Pedido" if "Pedido" in df.columns else None
    col_material = "Material" if "Material" in df.columns else None

    # KPI 1: Total de líneas
    kpis.append({"label": "Total líneas", "valor": int(len(df)), "tipo": "int", "color": "primary"})

    # KPI 2: Valor pendiente SIN FUENTE (regla del negocio)
    if col_cant and col_precio and col_fuente:
        mask_sin_fuente = df[col_fuente].isna() | (df[col_fuente].astype(str).str.strip() == "")
        df_sin = df[mask_sin_fuente]
        valor = (pd.to_numeric(df_sin[col_cant], errors="coerce") *
                 pd.to_numeric(df_sin[col_precio], errors="coerce")).sum()
        kpis.append({
            "label": "Valor pendiente (sin fuente)",
            "valor": float(valor) if pd.notna(valor) else 0,
            "tipo": "money",
            "color": "warn",
            "hint": f"{int(mask_sin_fuente.sum())} líneas sin fuente",
        })
        # KPI 2b: cantidad de líneas sin fuente
        kpis.append({
            "label": "Líneas sin fuente",
            "valor": int(mask_sin_fuente.sum()),
            "tipo": "int",
            "color": "warn",
        })

    # KPI 3: Pedidos abiertos (únicos)
    if col_pedido:
        unicos = df[col_pedido].dropna().nunique()
        kpis.append({"label": "Pedidos abiertos", "valor": int(unicos), "tipo": "int", "color": "info"})

    # KPI 4: Cantidad pendiente total
    if col_cant:
        total_cant = pd.to_numeric(df[col_cant], errors="coerce").sum()
        kpis.append({
            "label": "Σ Cantidad pendiente",
            "valor": int(total_cant) if pd.notna(total_cant) else 0,
            "tipo": "int",
            "color": "primary",
        })

    # Gráfica: Top 10 materiales con más pendientes
    if col_material and col_cant:
        top = df.groupby(col_material, dropna=False)[col_cant].sum() \
                .sort_values(ascending=False).head(10)
        if len(top) > 0:
            graficas.append({
                "titulo": "Top 10 materiales con más pendientes",
                "subtitulo": f"Material #1: {top.index[0]} ({_fmt_int(top.iloc[0])} uds)",
                "labels": [str(x) for x in top.index],
                "values": [float(v) for v in top.values],
                "y_tipo": "int",
                "color": "primary",
            })

    # Gráfica: Pendiente por Fuente (incluye "Sin fuente")
    if col_fuente and col_cant:
        df_g = df.copy()
        df_g[col_fuente] = df_g[col_fuente].fillna("⚠ Sin fuente").replace("", "⚠ Sin fuente")
        agr = df_g.groupby(col_fuente)[col_cant].sum().sort_values(ascending=False)
        graficas.append({
            "titulo": "Cantidad pendiente por Fuente",
            "subtitulo": "Las 'sin fuente' son las que inflan el valor pendiente",
            "labels": [str(x) for x in agr.index],
            "values": [float(v) for v in agr.values],
            "y_tipo": "int",
            "color": "primary",
        })

    return {"kpis": kpis, "graficas": graficas, "formato_filas": None}


def _analizar_resumen_sin_sugerencias(df: pd.DataFrame, hoy: datetime) -> dict:
    """KPIs y formato condicional para 'Resumen Sin Sugerencias':
       - Reglas de coloreado de filas:
         * VERDE: Disponible 1031-1030 > 0 (hay material disponible)
         * AMARILLO: Ultimo_Mes_Consumo > 6 meses Y suma de Inv 1030/1031/1032/1060 > 0
    """
    kpis = []
    graficas = []
    cols_inv = [c for c in ["Inv 1030", "Inv 1031", "Inv 1032", "Inv 1060"] if c in df.columns]
    col_disp = "Disponible 1031-1030" if "Disponible 1031-1030" in df.columns else None
    col_ult = "Ultimo_Mes_Consumo" if "Ultimo_Mes_Consumo" in df.columns else None

    kpis.append({"label": "Total filas", "valor": int(len(df)), "tipo": "int", "color": "primary"})

    # Marcar las reglas de fila
    info_filas = []  # lista de "clase" por fila índice
    n_verde = n_amarillo = 0
    for idx, fila in df.iterrows():
        clase = ""
        # Regla VERDE
        if col_disp is not None:
            v_disp = pd.to_numeric(pd.Series([fila[col_disp]]), errors="coerce").iloc[0]
            if pd.notna(v_disp) and v_disp > 0:
                clase = "ok"
                n_verde += 1
        # Regla AMARILLA (solo si no es verde ya)
        if clase == "" and col_ult and cols_inv:
            meses = _meses_desde(fila[col_ult], hoy) if isinstance(fila[col_ult], str) else None
            if meses is not None and meses > 6:
                suma_inv = 0
                for ci in cols_inv:
                    val = pd.to_numeric(pd.Series([fila[ci]]), errors="coerce").iloc[0]
                    if pd.notna(val):
                        suma_inv += val
                if suma_inv > 0:
                    clase = "warn"
                    n_amarillo += 1
        info_filas.append(clase)

    kpis.append({
        "label": "✓ Con disponible 1031-1030",
        "valor": n_verde, "tipo": "int", "color": "success",
        "hint": "Tienen material disponible",
    })
    kpis.append({
        "label": "⚠ Sin consumo >6 meses con stock",
        "valor": n_amarillo, "tipo": "int", "color": "warn",
        "hint": "Stock en 1030/31/32/60 pero sin consumo reciente",
    })

    # Gráfica de antigüedad de último consumo (distribución)
    if col_ult:
        buckets = {"≤3m": 0, "3-6m": 0, "6-12m": 0, "12-24m": 0, ">24m": 0, "Sin dato": 0}
        for v in df[col_ult]:
            m = _meses_desde(v, hoy) if isinstance(v, str) else None
            if m is None:
                buckets["Sin dato"] += 1
            elif m <= 3:
                buckets["≤3m"] += 1
            elif m <= 6:
                buckets["3-6m"] += 1
            elif m <= 12:
                buckets["6-12m"] += 1
            elif m <= 24:
                buckets["12-24m"] += 1
            else:
                buckets[">24m"] += 1
        graficas.append({
            "titulo": "Antigüedad del último consumo",
            "subtitulo": "Cuántos materiales por rango de meses sin facturar",
            "labels": list(buckets.keys()),
            "values": [float(v) for v in buckets.values()],
            "y_tipo": "int",
            "color": "warn",
        })

    return {"kpis": kpis, "graficas": graficas, "formato_filas": info_filas}


def _analizar_reporte_consumo(df: pd.DataFrame, hoy: datetime) -> dict:
    """Reporte de Consumo:
       - Fila ROJA: 'Ultimo mes facturacion' > 12 meses.
       - Fila AMARILLA: > 6 meses (y <= 12).
    """
    kpis = []
    graficas = []
    col_ult = "Ultimo mes facturacion" if "Ultimo mes facturacion" in df.columns else None

    kpis.append({"label": "Total filas", "valor": int(len(df)), "tipo": "int", "color": "primary"})

    info_filas = []
    n_rojo = n_amarillo = 0
    if col_ult:
        for v in df[col_ult]:
            clase = ""
            m = _meses_desde(v, hoy) if isinstance(v, str) else None
            if m is not None:
                if m > 12:
                    clase = "error"
                    n_rojo += 1
                elif m > 6:
                    clase = "warn"
                    n_amarillo += 1
            info_filas.append(clase)
    else:
        info_filas = [""] * len(df)

    kpis.append({"label": "✗ Sin facturar >12 meses", "valor": n_rojo, "tipo": "int", "color": "error"})
    kpis.append({"label": "⚠ Sin facturar 6–12 meses", "valor": n_amarillo, "tipo": "int", "color": "warn"})

    if col_ult and "Material" in df.columns:
        # Distribución por bucket
        buckets = {"≤3m": 0, "3-6m": 0, "6-12m": 0, ">12m": 0, "Sin dato": 0}
        for v in df[col_ult]:
            m = _meses_desde(v, hoy) if isinstance(v, str) else None
            if m is None: buckets["Sin dato"] += 1
            elif m <= 3: buckets["≤3m"] += 1
            elif m <= 6: buckets["3-6m"] += 1
            elif m <= 12: buckets["6-12m"] += 1
            else: buckets[">12m"] += 1
        graficas.append({
            "titulo": "Antigüedad de la última facturación",
            "subtitulo": f"{n_rojo + n_amarillo} materiales con más de 6 meses sin facturar",
            "labels": list(buckets.keys()),
            "values": [float(v) for v in buckets.values()],
            "y_tipo": "int",
            "color": "warn",
        })

    return {"kpis": kpis, "graficas": graficas, "formato_filas": info_filas}


def _analizar_inventario_condicion(df: pd.DataFrame, hoy: datetime) -> dict:
    """Inventario por condicion:
       - KPIs: Σ Suma inventario, Σ Costo inventario.
       - Gráfica: Top materiales con más $ inventario pendiente.
    """
    kpis = []
    graficas = []
    col_suma = "Suma inventario" if "Suma inventario" in df.columns else None
    col_costo = "Costo inventario" if "Costo inventario" in df.columns else None
    col_cond = "Condicion" if "Condicion" in df.columns else None

    kpis.append({"label": "Total filas", "valor": int(len(df)), "tipo": "int", "color": "primary"})

    if col_suma:
        total_suma = pd.to_numeric(df[col_suma], errors="coerce").sum()
        kpis.append({
            "label": "Σ Suma inventario",
            "valor": int(total_suma) if pd.notna(total_suma) else 0,
            "tipo": "int", "color": "info",
        })
    if col_costo:
        total_costo = pd.to_numeric(df[col_costo], errors="coerce").sum()
        kpis.append({
            "label": "Σ Costo inventario",
            "valor": float(total_costo) if pd.notna(total_costo) else 0,
            "tipo": "money", "color": "warn",
        })

    # Top materiales con más $ inventario
    if col_costo and "Material" in df.columns:
        top = df.groupby("Material", dropna=False)[col_costo].sum() \
                .sort_values(ascending=False).head(15)
        if len(top) > 0:
            top_mat = top.index[0]
            top_val = top.iloc[0]
            graficas.append({
                "titulo": "Top 15 materiales por $ inventario pendiente",
                "subtitulo": f"Material #1: {top_mat} con {_fmt_money(top_val)}",
                "labels": [str(x) for x in top.index],
                "values": [float(v) for v in top.values],
                "y_tipo": "money",
                "color": "warn",
            })

    # Costo por Condicion
    if col_cond and col_costo:
        agr = df.groupby(col_cond, dropna=False)[col_costo].sum().sort_values(ascending=False)
        graficas.append({
            "titulo": "Costo inventario por Condición",
            "subtitulo": "Distribución del costo retenido por estado del inventario",
            "labels": [str(x) for x in agr.index],
            "values": [float(v) for v in agr.values],
            "y_tipo": "money",
            "color": "info",
        })

    return {"kpis": kpis, "graficas": graficas, "formato_filas": None}


def _analizar_lotes(df: pd.DataFrame, hoy: datetime) -> dict:
    """Detalle Lotes Corta Caducidad:
       - Ordena la tabla por Fecha_Caducidad ASC (más urgente primero).
       - Formato de celda en Fecha_Caducidad con escala roja → naranja → amarillo → blanco.
    """
    kpis = []
    graficas = []
    col_fecha = "Fecha_Caducidad" if "Fecha_Caducidad" in df.columns else None

    kpis.append({"label": "Total lotes", "valor": int(len(df)), "tipo": "int", "color": "primary"})

    if col_fecha:
        fechas = pd.to_datetime(df[col_fecha], errors="coerce")
        dias = (fechas - pd.Timestamp(hoy)).dt.days
        n_30 = int((dias < 30).sum())
        n_90 = int(((dias >= 30) & (dias < 90)).sum())
        n_180 = int(((dias >= 90) & (dias < 180)).sum())
        kpis.append({"label": "✗ Caducan <30 días", "valor": n_30, "tipo": "int", "color": "error"})
        kpis.append({"label": "⚠ Caducan <90 días", "valor": n_90, "tipo": "int", "color": "warn"})
        kpis.append({"label": "ℹ Caducan <180 días", "valor": n_180, "tipo": "int", "color": "info"})

    # Devolver el DataFrame ORDENADO por fecha ascendente
    if col_fecha:
        df_ord = df.sort_values(by=col_fecha, ascending=True, na_position="last").reset_index(drop=True)
    else:
        df_ord = df

    return {
        "kpis": kpis,
        "graficas": graficas,
        "formato_filas": None,
        "df_ordenado": df_ord,
    }


def _analizar_generico(df: pd.DataFrame, hoy: datetime) -> dict:
    """Fallback para hojas no reconocidas: solo el conteo de filas."""
    return {
        "kpis": [{"label": "Total filas", "valor": int(len(df)), "tipo": "int", "color": "primary"}],
        "graficas": [],
        "formato_filas": None,
    }


# Mapeo nombre de hoja → función analizadora
_ANALIZADORES = {
    "Todas las Sugerencias": _analizar_todas_sugerencias,
    "Resumen Sin Sugerencias": _analizar_resumen_sin_sugerencias,
    "Reporte de Consumo": _analizar_reporte_consumo,
    "Inventario por condicion": _analizar_inventario_condicion,
    "Detalle Lotes Corta Caducidad": _analizar_lotes,
}


# ============================================================================
# DETECCIÓN DE FORMATO DE CELDA (no de fila)
# ============================================================================
def _columnas_formato_celda(df: pd.DataFrame, tipos: list[str], nombre_hoja: str) -> dict:
    """Identifica columnas que reciben formato condicional de CELDA.

    - fecha_caducidad: indices de columnas tipo fecha con palabra 'caducidad'/'vencim'/'expir'.
    - status: indices de columnas categóricas tipo Status/Condicion/Estado.
    - meses_atras: indices de columnas tipo YYYY-MM con palabra 'ultimo'/'mes' (Ultimo_Mes_Consumo, Ultimo mes facturacion).
    - inventario_centro: indices de columnas tipo 'Inv 1030', 'Inv 1031', etc. (resaltar si > 0).
    - disponible_centro: indices de columnas tipo 'Disponible NNNN-NNNN' (resaltar si > 0).
    """
    formato = {
        "fecha_caducidad": [],
        "status": [],
        "meses_atras": [],
        "inventario_centro": [],
        "disponible_centro": [],
    }
    for i, col in enumerate(df.columns):
        col_l = str(col).lower()
        if tipos[i] == "date" and any(k in col_l for k in ["caducidad", "vencim", "expir"]):
            formato["fecha_caducidad"].append(i)
        if tipos[i] == "text" and any(k in col_l for k in ["status", "condicion", "estado"]):
            formato["status"].append(i)
        if tipos[i] == "yearmonth" and any(k in col_l for k in ["ultimo", "mes "]) and any(k in col_l for k in ["consumo", "facturac", "mes"]):
            formato["meses_atras"].append(i)
        if re.match(r"^inv\s+\d+", col_l):
            formato["inventario_centro"].append(i)
        if re.match(r"^disponible\s+\d+", col_l):
            formato["disponible_centro"].append(i)
    return formato


# ============================================================================
# API PÚBLICA
# ============================================================================
def exportar_a_html(
    hojas: list[tuple[str, pd.DataFrame]] | None = None,
    titulo: str = "Reporte Sugeridor de Materiales",
    config_hojas: dict | None = None,
    # --- Retrocompatibilidad con la API vieja ---
    df_todas_sugerencias: pd.DataFrame | None = None,
    df_resumen_sin_sugerencias: pd.DataFrame | None = None,
    df_reporte_consumo: pd.DataFrame | None = None,
    df_sug_consumo: pd.DataFrame | None = None,
    df_inventario_por_condicion: pd.DataFrame | None = None,
    df_detalle_lotes_cc: pd.DataFrame | None = None,
) -> bytes:
    """Genera un archivo HTML interactivo autónomo."""
    if hojas is None:
        hojas = [
            ("Todas las Sugerencias", df_todas_sugerencias),
            ("Resumen Sin Sugerencias", df_resumen_sin_sugerencias),
            ("Reporte de Consumo", df_reporte_consumo),
            ("Sug Reporte Consumo", df_sug_consumo),
            ("Inventario por condicion", df_inventario_por_condicion),
            ("Detalle Lotes Corta Caducidad", df_detalle_lotes_cc),
        ]
    config_hojas = config_hojas or {}
    hoy = datetime.now()

    hojas_payload = []
    for nombre, df in hojas:
        if df is None or df.empty:
            continue

        analizador = _ANALIZADORES.get(nombre, _analizar_generico)
        analisis = analizador(df, hoy)

        # Algunas hojas devuelven un df ordenado
        df_final = analisis.get("df_ordenado", df)

        columnas = [str(c) for c in df_final.columns]
        tipos = [_tipo(df_final, c) for c in df_final.columns]
        registros = _df_a_records(df_final)
        formato_celda = _columnas_formato_celda(df_final, tipos, nombre)

        cfg = config_hojas.get(nombre, {})
        mostrar_kpis = cfg.get("kpis", True)
        mostrar_graficas = cfg.get("graficas", True)

        hojas_payload.append({
            "nombre": nombre,
            "columnas": columnas,
            "tipos": tipos,
            "filas": registros,
            "total": len(registros),
            "kpis": analisis["kpis"] if mostrar_kpis else [],
            "graficas": analisis["graficas"] if mostrar_graficas else [],
            "formato_celda": formato_celda,
            "formato_filas": analisis.get("formato_filas"),
        })

    fecha = hoy.strftime("%Y-%m-%d %H:%M")
    payload = {"titulo": titulo, "fecha": fecha, "hojas": hojas_payload}

    json_str = json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/")

    doc = (_PLANTILLA_HTML
           .replace("__TITULO__", html.escape(titulo))
           .replace("__FECHA__", html.escape(fecha))
           .replace("__DATA_JSON__", json_str))
    return doc.encode("utf-8")


# ============================================================================
# PLANTILLA HTML (CSS + JS embebidos)
# ============================================================================
_PLANTILLA_HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITULO__</title>
<style>
:root {
  --bg: #f4f6fa;
  --panel: #ffffff;
  --border: #e3e6ee;
  --border-strong: #cbd0dc;
  --text: #1a202c;
  --text-soft: #4a5568;
  --muted: #718096;
  --primary: #2563eb;
  --primary-soft: #dbeafe;
  --primary-dark: #1d4ed8;
  --success: #15803d;
  --success-soft: #dcfce7;
  --warn: #b45309;
  --warn-soft: #fef3c7;
  --warn-bg: #fffbeb;
  --error: #b91c1c;
  --error-soft: #fee2e2;
  --error-bg: #fef2f2;
  --info: #0e7490;
  --info-soft: #cffafe;
  --row-alt: #f9fafc;
  --row-hover: #eef2ff;
  --shadow: 0 1px 3px rgba(15,23,42,.06), 0 1px 2px rgba(15,23,42,.04);
  --shadow-lg: 0 4px 12px rgba(15,23,42,.08);
}
html[data-theme="dark"] {
  --bg: #0b1220;
  --panel: #1a2236;
  --border: #2a3550;
  --border-strong: #3a4866;
  --text: #e2e8f0;
  --text-soft: #cbd5e1;
  --muted: #94a3b8;
  --primary: #60a5fa;
  --primary-soft: #1e3a8a;
  --primary-dark: #3b82f6;
  --success: #4ade80;
  --success-soft: #064e3b;
  --warn: #fbbf24;
  --warn-soft: #78350f;
  --warn-bg: #2a1d0b;
  --error: #f87171;
  --error-soft: #7f1d1d;
  --error-bg: #2a0f0f;
  --info: #22d3ee;
  --info-soft: #164e63;
  --row-alt: #1f2942;
  --row-hover: #2a3756;
  --shadow: 0 1px 3px rgba(0,0,0,.4);
  --shadow-lg: 0 4px 12px rgba(0,0,0,.5);
}

* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  background: var(--bg); color: var(--text);
  font-size: 14px; line-height: 1.5;
}
header {
  background: var(--panel);
  border-bottom: 1px solid var(--border);
  padding: 18px 28px;
  display: flex; justify-content: space-between; align-items: center;
  flex-wrap: wrap; gap: 12px;
  box-shadow: var(--shadow);
}
header h1 {
  margin: 0; font-size: 19px; font-weight: 700;
  display: flex; align-items: center; gap: 10px;
}
header .meta { color: var(--muted); font-size: 12px; margin-top: 2px; }
.header-actions { display: flex; gap: 8px; align-items: center; }
button, .btn {
  background: var(--panel); color: var(--text);
  border: 1px solid var(--border-strong); border-radius: 6px;
  padding: 7px 14px; font-size: 13px; cursor: pointer;
  transition: all .15s;
  font-family: inherit; font-weight: 500;
}
button:hover, .btn:hover { background: var(--primary-soft); border-color: var(--primary); color: var(--primary-dark); }

main { padding: 20px 28px 40px; max-width: 1600px; margin: 0 auto; }

/* ============ TABS ============ */
.tabs {
  display: flex; gap: 2px;
  margin-bottom: 24px;
  border-bottom: 2px solid var(--border);
  overflow-x: auto;
}
.tab {
  padding: 11px 18px; cursor: pointer;
  border: none; background: transparent; color: var(--muted);
  font-size: 13px; font-weight: 500; white-space: nowrap;
  border-bottom: 3px solid transparent; margin-bottom: -2px;
  font-family: inherit;
  transition: color .15s;
}
.tab:hover { color: var(--text); }
.tab.active {
  color: var(--primary); border-bottom-color: var(--primary);
  font-weight: 600;
}
.tab .badge {
  display: inline-block; background: var(--border); color: var(--muted);
  border-radius: 10px; padding: 1px 8px; font-size: 11px;
  margin-left: 6px; font-weight: 500;
}
.tab.active .badge { background: var(--primary-soft); color: var(--primary-dark); }

/* ============ KPI EJECUTIVOS ============ */
.kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 14px; margin-bottom: 22px;
}
.kpi {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 18px;
  box-shadow: var(--shadow);
  position: relative;
  overflow: hidden;
  transition: transform .15s, box-shadow .15s;
}
.kpi:hover { transform: translateY(-1px); box-shadow: var(--shadow-lg); }
.kpi::before {
  content: ""; position: absolute; left: 0; top: 0; bottom: 0;
  width: 4px;
  background: var(--primary);
}
.kpi.kpi-warn::before    { background: var(--warn); }
.kpi.kpi-error::before   { background: var(--error); }
.kpi.kpi-success::before { background: var(--success); }
.kpi.kpi-info::before    { background: var(--info); }
.kpi .label {
  font-size: 11px; color: var(--muted);
  text-transform: uppercase; letter-spacing: .06em;
  font-weight: 600; margin-bottom: 6px;
}
.kpi .value {
  font-size: 26px; font-weight: 700; color: var(--text);
  font-variant-numeric: tabular-nums; line-height: 1.15;
}
.kpi.kpi-warn    .value { color: var(--warn); }
.kpi.kpi-error   .value { color: var(--error); }
.kpi.kpi-success .value { color: var(--success); }
.kpi.kpi-info    .value { color: var(--info); }
.kpi .hint {
  font-size: 11px; color: var(--muted); margin-top: 4px;
}

/* ============ GRÁFICAS ============ */
.graficas {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(440px, 1fr));
  gap: 16px; margin-bottom: 22px;
}
.grafica {
  background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
  padding: 16px 18px; box-shadow: var(--shadow);
}
.grafica h3 {
  margin: 0 0 2px; font-size: 14px; font-weight: 600;
  color: var(--text);
}
.grafica .subtitulo {
  color: var(--muted); font-size: 12px; margin-bottom: 12px;
}
.grafica-cuerpo { width: 100%; min-height: 60px; }

.seccion {
  font-size: 11px; color: var(--muted);
  text-transform: uppercase; letter-spacing: .08em;
  margin: 22px 0 10px; font-weight: 700;
  display: flex; align-items: center; gap: 10px;
}
.seccion::after {
  content: ""; flex: 1; height: 1px; background: var(--border);
}

/* ============ TOOLBAR ============ */
.toolbar {
  display: flex; gap: 10px; margin-bottom: 12px;
  flex-wrap: wrap; align-items: center;
}
.toolbar input[type="search"] {
  flex: 1 1 280px; min-width: 220px;
  padding: 8px 14px; border: 1px solid var(--border-strong); border-radius: 8px;
  background: var(--panel); color: var(--text); font-size: 13px;
  font-family: inherit;
}
.toolbar input[type="search"]:focus {
  outline: none; border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-soft);
}
.toolbar .info { color: var(--muted); font-size: 12px; }

/* ============ TABLA ============ */
.table-wrap {
  background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
  overflow: auto; max-height: 62vh; box-shadow: var(--shadow);
}
table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
thead { position: sticky; top: 0; background: var(--panel); z-index: 2; }
th, td {
  padding: 8px 12px; border-bottom: 1px solid var(--border);
  text-align: left; white-space: nowrap;
}
th {
  font-weight: 600; color: var(--text-soft); cursor: pointer;
  user-select: none; background: var(--panel);
  border-bottom: 2px solid var(--border-strong);
  font-size: 12px;
}
th:hover { color: var(--primary); }
th .sort-ind { display: inline-block; width: 12px; opacity: .7; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:nth-child(even) { background: var(--row-alt); }
tbody tr:hover { background: var(--row-hover); }
.col-filter {
  width: 100%; padding: 4px 8px; font-size: 11px;
  border: 1px solid var(--border); border-radius: 5px;
  background: var(--panel); color: var(--text);
  font-family: inherit;
}
.col-filter:focus { outline: none; border-color: var(--primary); }

/* ============ FORMATO CONDICIONAL ============ */
/* Filas completas */
tr.fila-ok      { background: var(--success-soft) !important; }
tr.fila-ok:hover { background: var(--success-soft) !important; filter: brightness(0.96); }
tr.fila-warn    { background: var(--warn-bg) !important; }
tr.fila-warn:hover { background: var(--warn-bg) !important; filter: brightness(0.96); }
tr.fila-error   { background: var(--error-bg) !important; }
tr.fila-error:hover { background: var(--error-bg) !important; filter: brightness(0.96); }

/* Celdas */
.cf-critica { background: var(--error-soft); color: var(--error); font-weight: 600; }
.cf-alerta  { background: var(--warn-soft); color: var(--warn); font-weight: 500; }
.cf-aviso   { background: var(--info-soft); color: var(--info); }
.cf-pos     { color: var(--success); font-weight: 600; }
.cf-neg     { color: var(--muted); }

/* Pill de status */
.pill {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 2px 9px; border-radius: 10px;
  font-weight: 500; font-size: 11.5px;
  white-space: nowrap;
}
.pill-ok    { background: var(--success-soft); color: var(--success); }
.pill-warn  { background: var(--warn-soft); color: var(--warn); }
.pill-error { background: var(--error-soft); color: var(--error); }
.pill-other { background: var(--border); color: var(--muted); }

/* ============ PAGINACIÓN ============ */
.pagination {
  display: flex; gap: 6px; justify-content: center; align-items: center;
  padding: 14px 0 4px;
}
.pagination button { padding: 5px 11px; font-size: 12px; }
.pagination .page-info { color: var(--muted); font-size: 12px; margin: 0 12px; }

.empty {
  padding: 40px; text-align: center; color: var(--muted);
  background: var(--panel); border: 1px dashed var(--border); border-radius: 8px;
}

@media (max-width: 720px) {
  header { padding: 14px 16px; }
  main { padding: 14px 16px 24px; }
  .kpi .value { font-size: 20px; }
  .graficas { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<header>
  <div>
    <h1>📊 __TITULO__</h1>
    <div class="meta">Generado: __FECHA__</div>
  </div>
  <div class="header-actions">
    <button id="btn-csv" title="Exporta la vista filtrada de la hoja actual a CSV">⬇ Exportar CSV</button>
    <button id="btn-theme" title="Alternar tema claro / oscuro">🌓 Tema</button>
  </div>
</header>

<main>
  <nav class="tabs" id="tabs" role="tablist"></nav>
  <section id="hoja"></section>
</main>

<script>
const DATA = __DATA_JSON__;
const PAGE_SIZE = 100;
const HOY = new Date();
HOY.setHours(0,0,0,0);

const state = {
  hojaIdx: 0,
  busqueda: "",
  filtros: {},
  orden: {},
  pagina: {},
};

// ─── Helpers de formato ─────────────────────────────────────────────────────
function fmtNum(v) {
  if (v === null || v === undefined || v === "") return "";
  if (typeof v !== "number") return v;
  if (Number.isInteger(v)) return v.toLocaleString("es-MX");
  return v.toLocaleString("es-MX", { maximumFractionDigits: 4 });
}
function fmtKPI(tipo, v) {
  if (v === null || v === undefined) return "—";
  if (tipo === "money") {
    const a = Math.abs(v);
    if (a >= 1e6) return "$" + (v/1e6).toFixed(2) + "M";
    if (a >= 1e3) return "$" + (v/1e3).toFixed(1) + "K";
    return "$" + v.toLocaleString("es-MX", { maximumFractionDigits: 0 });
  }
  if (tipo === "int") return Math.round(v).toLocaleString("es-MX");
  if (typeof v === "number") {
    if (Number.isInteger(v)) return v.toLocaleString("es-MX");
    return v.toLocaleString("es-MX", { maximumFractionDigits: 2 });
  }
  return String(v);
}
function escapeHTML(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}
function compararValores(a, b, tipo) {
  if (a === null || a === undefined) return 1;
  if (b === null || b === undefined) return -1;
  if (tipo === "num") return (Number(a) || 0) - (Number(b) || 0);
  if (tipo === "date" || tipo === "yearmonth") return String(a).localeCompare(String(b));
  return String(a).localeCompare(String(b), "es", { numeric: true });
}

// ─── Formato condicional de celdas ──────────────────────────────────────────
function diasHasta(fechaStr) {
  if (!fechaStr) return null;
  const f = new Date(fechaStr);
  if (isNaN(f.getTime())) return null;
  return Math.floor((f - HOY) / (86400000));
}
function mesesDesde(yearmonthStr) {
  if (!yearmonthStr || typeof yearmonthStr !== "string") return null;
  const m = yearmonthStr.trim().match(/^(\d{4})-(\d{1,2})$/);
  if (!m) return null;
  const y = parseInt(m[1]), mo = parseInt(m[2]);
  return (HOY.getFullYear() - y) * 12 + (HOY.getMonth() + 1 - mo);
}
function claseFechaCaducidad(fechaStr) {
  const d = diasHasta(fechaStr);
  if (d === null) return "";
  if (d < 30) return "cf-critica";
  if (d < 90) return "cf-alerta";
  if (d < 180) return "cf-aviso";
  return "";
}
function claseMesesAtras(yearmonthStr) {
  const m = mesesDesde(yearmonthStr);
  if (m === null) return "";
  if (m > 12) return "cf-critica";
  if (m > 6) return "cf-alerta";
  return "";
}
function statusInfo(valor) {
  if (valor === null || valor === undefined || valor === "") return null;
  const v = String(valor).toLowerCase();
  if (/(disponible|^ok$|aprob|activo|buen|sin\s+observ)/.test(v))
    return { cls: "pill-ok", icon: "✓" };
  if (/(critic|bloqu|urgen|caducad|venc)/.test(v))
    return { cls: "pill-error", icon: "✗" };
  if (/(revis|pendient|alert|aviso|corta|lento|sin\s+movim)/.test(v))
    return { cls: "pill-warn", icon: "⚠" };
  return { cls: "pill-other", icon: "•" };
}

// ─── Renderizador de barras (SVG inline) ────────────────────────────────────
function renderBarChart(container, cfg) {
  const { labels, values, y_tipo, color } = cfg;
  const maxVal = Math.max(...values, 1);
  const barH = 22;
  const gap = 6;
  const labelW = Math.min(220, Math.max(120, container.clientWidth * 0.32));
  const padR = 100;
  const padT = 6; const padB = 6;
  const w = container.clientWidth || 600;
  const innerW = Math.max(60, w - labelW - padR);
  const totalH = (barH + gap) * labels.length + padT + padB;

  // Color segun semantica
  const root = getComputedStyle(document.documentElement);
  const colorMap = {
    primary: root.getPropertyValue("--primary").trim(),
    warn: root.getPropertyValue("--warn").trim(),
    error: root.getPropertyValue("--error").trim(),
    success: root.getPropertyValue("--success").trim(),
    info: root.getPropertyValue("--info").trim(),
  };
  const colMuted = root.getPropertyValue("--muted").trim();
  const colText  = root.getPropertyValue("--text").trim();

  let svg = `<svg viewBox="0 0 ${w} ${totalH}" xmlns="http://www.w3.org/2000/svg"
    style="display:block;width:100%;height:${totalH}px;font-family:inherit;font-size:12px">`;

  labels.forEach((lbl, i) => {
    const y = padT + i * (barH + gap);
    const val = values[i];
    const bw = innerW * (val / maxVal);
    const lblCorto = lbl.length > 30 ? lbl.slice(0, 28) + "…" : lbl;
    // Color especial: si la barra es "⚠ Sin fuente" o "✗", resalta en warn/error
    let barColor = colorMap[color || "primary"] || colorMap.primary;
    if (/⚠|sin fuente|>12m|>24m|critic/i.test(lbl)) barColor = colorMap.error;
    else if (/12-24m|6-12m/i.test(lbl)) barColor = colorMap.warn;

    svg += `<text x="${labelW - 8}" y="${y + barH/2 + 4}" text-anchor="end" fill="${colText}">${escapeHTML(lblCorto)}</text>`;
    svg += `<rect x="${labelW}" y="${y}" width="${bw}" height="${barH}" rx="4" fill="${barColor}" opacity="0.9">
      <title>${escapeHTML(lbl)}: ${fmtKPI(y_tipo, val)}</title>
    </rect>`;
    svg += `<text x="${labelW + bw + 6}" y="${y + barH/2 + 4}" fill="${colMuted}">${fmtKPI(y_tipo, val)}</text>`;
  });

  svg += `</svg>`;
  container.innerHTML = svg;
}

// ─── Tabs ───────────────────────────────────────────────────────────────────
function renderTabs() {
  const cont = document.getElementById("tabs");
  if (!DATA.hojas.length) { cont.innerHTML = ""; return; }
  cont.innerHTML = DATA.hojas.map((h, i) => `
    <button class="tab ${i === state.hojaIdx ? "active" : ""}" data-idx="${i}">
      ${escapeHTML(h.nombre)}
      <span class="badge">${h.total.toLocaleString("es-MX")}</span>
    </button>
  `).join("");
  cont.querySelectorAll(".tab").forEach(t => {
    t.addEventListener("click", () => {
      state.hojaIdx = parseInt(t.dataset.idx);
      state.busqueda = "";
      renderTabs();
      renderHoja();
    });
  });
}

// ─── Filtrar/ordenar (preserva el índice original para colorear filas) ─────
function obtenerFilas(hoja) {
  const hojaIdx = state.hojaIdx;
  const busq = state.busqueda.trim().toLowerCase();
  const filtros = state.filtros[hojaIdx] || {};
  const orden = state.orden[hojaIdx];

  // Trabajamos sobre pares [fila, idxOriginal] para no perder la clase de fila
  let parejas = hoja.filas.map((fila, idx) => [fila, idx]);

  if (busq) {
    parejas = parejas.filter(([fila]) => fila.some(v =>
      v !== null && v !== undefined && String(v).toLowerCase().includes(busq)
    ));
  }
  const cfs = Object.entries(filtros).filter(([_, t]) => t && t.trim());
  if (cfs.length) {
    parejas = parejas.filter(([fila]) => cfs.every(([ci, txt]) => {
      const v = fila[parseInt(ci)];
      return v !== null && v !== undefined &&
        String(v).toLowerCase().includes(txt.toLowerCase());
    }));
  }
  if (orden) {
    const tipo = hoja.tipos[orden.col];
    const factor = orden.asc ? 1 : -1;
    parejas = [...parejas].sort(([a], [b]) =>
      factor * compararValores(a[orden.col], b[orden.col], tipo)
    );
  }
  return parejas;
}

// ─── Render principal de la hoja ────────────────────────────────────────────
function renderHoja() {
  const cont = document.getElementById("hoja");
  if (!DATA.hojas.length) { cont.innerHTML = `<div class="empty">No hay hojas para mostrar.</div>`; return; }
  const hoja = DATA.hojas[state.hojaIdx];

  let html = "";
  if (hoja.kpis && hoja.kpis.length) {
    html += `<div class="seccion">📌 Indicadores ejecutivos</div>`;
    html += `<div class="kpis">` + hoja.kpis.map(k => `
      <div class="kpi kpi-${k.color || 'primary'}">
        <div class="label">${escapeHTML(k.label)}</div>
        <div class="value">${fmtKPI(k.tipo, k.valor)}</div>
        ${k.hint ? `<div class="hint">${escapeHTML(k.hint)}</div>` : ""}
      </div>
    `).join("") + `</div>`;
  }

  if (hoja.graficas && hoja.graficas.length) {
    html += `<div class="seccion">📈 Visualizaciones</div>`;
    html += `<div class="graficas">` + hoja.graficas.map((g, i) => `
      <div class="grafica">
        <h3>${escapeHTML(g.titulo)}</h3>
        ${g.subtitulo ? `<div class="subtitulo">${escapeHTML(g.subtitulo)}</div>` : ""}
        <div class="grafica-cuerpo" id="graf-${state.hojaIdx}-${i}"></div>
      </div>
    `).join("") + `</div>`;
  }

  html += `<div class="seccion">📋 Detalle</div>`;
  html += renderTabla(hoja);

  cont.innerHTML = html;

  if (hoja.graficas) {
    hoja.graficas.forEach((g, i) => {
      const el = document.getElementById(`graf-${state.hojaIdx}-${i}`);
      if (el) renderBarChart(el, g);
    });
  }
  bindEventosTabla(hoja);
}

function renderTabla(hoja) {
  const parejas = obtenerFilas(hoja);
  const pagina = state.pagina[state.hojaIdx] || 1;
  const totalPaginas = Math.max(1, Math.ceil(parejas.length / PAGE_SIZE));
  const paginaSegura = Math.min(pagina, totalPaginas);
  state.pagina[state.hojaIdx] = paginaSegura;
  const ini = (paginaSegura - 1) * PAGE_SIZE;
  const parejasPag = parejas.slice(ini, ini + PAGE_SIZE);

  const orden = state.orden[state.hojaIdx] || {};
  const filtros = state.filtros[state.hojaIdx] || {};
  const fmt = hoja.formato_celda || {};
  const idxFechasCad = new Set(fmt.fecha_caducidad || []);
  const idxStatus = new Set(fmt.status || []);
  const idxMesesAtras = new Set(fmt.meses_atras || []);
  const idxInvCentro = new Set(fmt.inventario_centro || []);
  const idxDispCentro = new Set(fmt.disponible_centro || []);
  const filasClases = hoja.formato_filas || [];

  const ths = hoja.columnas.map((c, i) => {
    const ind = orden.col === i ? (orden.asc ? "▲" : "▼") : "";
    return `<th class="${hoja.tipos[i] === "num" ? "num" : ""}" data-col="${i}">
      ${escapeHTML(c)} <span class="sort-ind">${ind}</span>
    </th>`;
  }).join("");

  const thFiltros = hoja.columnas.map((c, i) => `
    <th class="${hoja.tipos[i] === "num" ? "num" : ""}">
      <input class="col-filter" data-col="${i}"
        value="${escapeHTML(filtros[i] || "")}" placeholder="filtrar…" />
    </th>
  `).join("");

  const trs = parejasPag.map(([fila, idxOrig]) => {
    const claseFila = filasClases[idxOrig] || "";
    const tds = fila.map((v, i) => {
      const tipo = hoja.tipos[i];
      const cls = [tipo === "num" ? "num" : ""];
      let contenido;

      // Status → pill
      if (idxStatus.has(i) && v) {
        const info = statusInfo(v);
        if (info) {
          contenido = `<span class="pill ${info.cls}">${info.icon} ${escapeHTML(v)}</span>`;
        } else {
          contenido = escapeHTML(v);
        }
      } else if (idxFechasCad.has(i) && v) {
        const cf = claseFechaCaducidad(v);
        if (cf) cls.push(cf);
        contenido = escapeHTML(v);
      } else if (idxMesesAtras.has(i) && v) {
        const cf = claseMesesAtras(v);
        if (cf) cls.push(cf);
        contenido = escapeHTML(v);
      } else if (idxInvCentro.has(i) || idxDispCentro.has(i)) {
        // Resaltar cuando > 0
        const num = Number(v);
        if (!isNaN(num) && num > 0) cls.push("cf-pos");
        else if (num === 0) cls.push("cf-neg");
        contenido = (tipo === "num" && typeof v === "number") ? fmtNum(v) : escapeHTML(v);
      } else if (tipo === "num" && typeof v === "number") {
        contenido = fmtNum(v);
      } else {
        contenido = escapeHTML(v);
      }
      return `<td class="${cls.filter(Boolean).join(" ")}">${contenido}</td>`;
    }).join("");
    const trCls = claseFila ? `fila-${claseFila}` : "";
    return `<tr class="${trCls}">${tds}</tr>`;
  }).join("");

  return `
    <div class="toolbar">
      <input type="search" id="busqueda"
        placeholder="🔍 Buscar en todas las columnas…"
        value="${escapeHTML(state.busqueda)}" />
      <span class="info">${parejas.length.toLocaleString("es-MX")} de ${hoja.total.toLocaleString("es-MX")} filas</span>
      <button id="btn-limpiar">Limpiar filtros</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>${ths}</tr>
          <tr>${thFiltros}</tr>
        </thead>
        <tbody>
          ${trs || `<tr><td colspan="${hoja.columnas.length}" class="empty" style="border:none;">Sin resultados</td></tr>`}
        </tbody>
      </table>
    </div>
    <div class="pagination">
      <button id="pag-p" ${paginaSegura === 1 ? "disabled" : ""}>« Primera</button>
      <button id="pag-a" ${paginaSegura === 1 ? "disabled" : ""}>‹ Anterior</button>
      <span class="page-info">Página ${paginaSegura.toLocaleString("es-MX")} de ${totalPaginas.toLocaleString("es-MX")}</span>
      <button id="pag-s" ${paginaSegura === totalPaginas ? "disabled" : ""}>Siguiente ›</button>
      <button id="pag-u" ${paginaSegura === totalPaginas ? "disabled" : ""}>Última »</button>
    </div>
  `;
}

function bindEventosTabla(hoja) {
  const cont = document.getElementById("hoja");

  document.getElementById("busqueda").addEventListener("input", e => {
    state.busqueda = e.target.value;
    state.pagina[state.hojaIdx] = 1;
    rerenderTabla(hoja);
    setTimeout(() => {
      const el = document.getElementById("busqueda");
      if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
    }, 0);
  });

  document.getElementById("btn-limpiar").addEventListener("click", () => {
    state.busqueda = "";
    state.filtros[state.hojaIdx] = {};
    state.orden[state.hojaIdx] = null;
    state.pagina[state.hojaIdx] = 1;
    rerenderTabla(hoja);
  });

  cont.querySelectorAll("thead th[data-col]").forEach(th => {
    th.addEventListener("click", () => {
      const col = parseInt(th.dataset.col);
      const actual = state.orden[state.hojaIdx];
      state.orden[state.hojaIdx] = (actual && actual.col === col)
        ? { col, asc: !actual.asc } : { col, asc: true };
      rerenderTabla(hoja);
    });
  });

  cont.querySelectorAll(".col-filter").forEach(inp => {
    inp.addEventListener("input", e => {
      const col = parseInt(e.target.dataset.col);
      if (!state.filtros[state.hojaIdx]) state.filtros[state.hojaIdx] = {};
      state.filtros[state.hojaIdx][col] = e.target.value;
      state.pagina[state.hojaIdx] = 1;
      rerenderTabla(hoja);
      setTimeout(() => {
        const sel = cont.querySelector(`.col-filter[data-col="${col}"]`);
        if (sel) { sel.focus(); sel.setSelectionRange(sel.value.length, sel.value.length); }
      }, 0);
    });
  });

  const parejas = obtenerFilas(hoja);
  const totalPaginas = Math.max(1, Math.ceil(parejas.length / PAGE_SIZE));
  const paginaSegura = state.pagina[state.hojaIdx] || 1;
  const dp = document.getElementById("pag-p");
  const da = document.getElementById("pag-a");
  const ds = document.getElementById("pag-s");
  const du = document.getElementById("pag-u");
  if (dp) dp.addEventListener("click", () => { state.pagina[state.hojaIdx] = 1; rerenderTabla(hoja); });
  if (da) da.addEventListener("click", () => { state.pagina[state.hojaIdx] = Math.max(1, paginaSegura - 1); rerenderTabla(hoja); });
  if (ds) ds.addEventListener("click", () => { state.pagina[state.hojaIdx] = Math.min(totalPaginas, paginaSegura + 1); rerenderTabla(hoja); });
  if (du) du.addEventListener("click", () => { state.pagina[state.hojaIdx] = totalPaginas; rerenderTabla(hoja); });
}

function rerenderTabla(hoja) {
  const cont = document.getElementById("hoja");
  const secs = cont.querySelectorAll(".seccion");
  const secDatos = secs[secs.length - 1];
  if (!secDatos) { renderHoja(); return; }
  let n = secDatos.nextSibling;
  while (n) { const nn = n.nextSibling; n.remove(); n = nn; }
  const div = document.createElement("div");
  div.innerHTML = renderTabla(hoja);
  while (div.firstChild) cont.appendChild(div.firstChild);
  bindEventosTabla(hoja);
}

// ─── Exportar CSV ───────────────────────────────────────────────────────────
function exportarCSV() {
  if (!DATA.hojas.length) return;
  const hoja = DATA.hojas[state.hojaIdx];
  const parejas = obtenerFilas(hoja);
  const esc = v => {
    if (v === null || v === undefined) return "";
    const s = String(v);
    return /[",\n\r;]/.test(s) ? `"${s.replace(/"/g,'""')}"` : s;
  };
  const lineas = [hoja.columnas.map(esc).join(",")];
  parejas.forEach(([fila]) => lineas.push(fila.map(esc).join(",")));
  const csv = "\uFEFF" + lineas.join("\r\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${hoja.nombre.replace(/[^\w\-]+/g,"_")}_filtrado.csv`;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function alternarTema() {
  const a = document.documentElement.getAttribute("data-theme") || "light";
  const n = a === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", n);
  try { localStorage.setItem("sugeridor-tema", n); } catch(e) {}
  renderHoja();
}

(function init() {
  try {
    const g = localStorage.getItem("sugeridor-tema");
    if (g) document.documentElement.setAttribute("data-theme", g);
  } catch(e) {}
  document.getElementById("btn-theme").addEventListener("click", alternarTema);
  document.getElementById("btn-csv").addEventListener("click", exportarCSV);
  renderTabs();
  renderHoja();
})();
</script>
</body>
</html>
"""
