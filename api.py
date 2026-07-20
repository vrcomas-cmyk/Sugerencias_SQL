"""
api.py - API HTTP que reemplaza la UI de Streamlit (app.py).

Misma lógica de negocio, mismo orden de fases que app.py — solo cambia quién
la invoca (degasa-portal en vez de un humano frente a Streamlit). app.py se
deja intacto como respaldo local.

Patrón asíncrono por job: el archivo de facturación puede traer 800k+ filas,
así que el procesamiento corre en un hilo de fondo y el cliente hace polling
del estado en vez de esperar la respuesta HTTP completa.

Auth: no se valida el JWT de Supabase localmente (requeriría el secreto del
proyecto, que no vive aquí) — en su lugar se reenvía el token a
`{SUPABASE_URL}/auth/v1/user` y se confía en que Supabase lo valide. Esto es
exactamente lo que hace cualquier backend que delega auth a Supabase.
"""
import io
import logging
import os
import threading
import time
import uuid
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import httpx
import pandas as pd
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Header, UploadFile
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()  # lee .env si existe (local); en Railway/Render las env vars ya vienen del panel, esto no las pisa.
from fastapi.responses import Response
from pydantic import BaseModel

pd.options.mode.chained_assignment = None
warnings.filterwarnings("ignore", category=UserWarning, message="Parsing dates in.*%d/%m/%Y.*")
warnings.filterwarnings("ignore", category=FutureWarning)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from config import FUENTES_DISPONIBLES
from io_loaders import cargar_facturacion, cargar_hojas_externas, cargar_inventario, cargar_pedidos
from procesadores.facturacion import generar_reporte_consumo, generar_resumen_facturacion
from r2_client import facturacion_object_meta, guardar_facturacion_acumulada, leer_facturacion_acumulada
from reportes.exportador import exportar_a_excel
from reportes.inventario_por_condicion import generar_inventario_por_condicion
from reportes.sin_sugerencias import generar_resumen_sin_sugerencias_optimizado
from reportes.sug_desde_consumo import generar_sugerencias_desde_reporte_consumo
from reportes.todas_sugerencias import generar_todas_sugerencias
from sugerencias.enriquecimiento import enriquecer_sugerencias_con_consumo

# ==============================================================================
# Config vía entorno (ver .env.example)
# ==============================================================================
SUPABASE_URL = os.environ["SUPABASE_URL"]  # ej. https://fiplfsuhsqibzrpvjvbx.supabase.co
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
JOB_TTL_SECONDS = 6 * 3600  # los jobs (y su xlsx en memoria) se limpian después de 6h

app = FastAPI(title="Sugeridor de Materiales — API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ==============================================================================
# Auth: valida el access_token de Supabase reenviándolo a /auth/v1/user
# ==============================================================================
async def require_user(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Falta el header Authorization: Bearer <token>")
    token = authorization.split(" ", 1)[1]
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"Authorization": f"Bearer {token}", "apikey": SUPABASE_ANON_KEY},
        )
    if resp.status_code != 200:
        raise HTTPException(401, "Sesión inválida o expirada")
    return resp.json().get("email", "?")


# ==============================================================================
# Jobs en memoria — suficiente para el puñado de usuarios invitados de hoy.
# Si esto crece a multi-instancia, cambiar por Redis/DB; no antes.
# ==============================================================================
class Job(BaseModel):
    id: str
    status: str = "pendiente"  # pendiente | procesando | listo | error
    fase: str = ""
    progreso: float = 0.0
    error: Optional[str] = None
    filename: Optional[str] = None
    created_at: float


#
# NOTA: los jobs no están aislados por usuario (cualquier usuario autenticado
# que adivine un job_id UUID4 podría consultarlo/descargarlo). Aceptable para
# el puñado de usuarios invitados de hoy; si el equipo crece, agregar
# `owner_email` a Job y comparar contra `require_user` en cada endpoint.
_jobs: dict[str, Job] = {}
_job_files: dict[str, bytes] = {}
_lock = threading.Lock()


def _cleanup_old_jobs() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    with _lock:
        stale = [jid for jid, j in _jobs.items() if j.created_at < cutoff]
        for jid in stale:
            _jobs.pop(jid, None)
            _job_files.pop(jid, None)


def _set(job_id: str, **kw) -> None:
    with _lock:
        job = _jobs[job_id]
        for k, v in kw.items():
            setattr(job, k, v)


# ==============================================================================
# Facturación acumulada en R2 — fusión por ventana de fecha.
#
# El usuario exporta de SAP solo la ventana reciente (mes corriente + ~7 días,
# jueves y lunes normalmente) para no tener que resubir años de historial
# cada vez. La fusión reemplaza en lo acumulado todo lo que caiga dentro del
# rango de fechas del archivo nuevo y agrega esas filas — así el mes corriente
# (o la última semana, al cruzar de mes) siempre queda con el dato más
# reciente, y todo lo anterior a la ventana se conserva intacto.
# ==============================================================================
def _fusionar_facturacion(df_nuevo: pd.DataFrame) -> pd.DataFrame:
    if df_nuevo.empty or "Fecha" not in df_nuevo.columns:
        raise RuntimeError("El archivo de facturación no trae una columna de Fecha reconocible")
    fecha_min = df_nuevo["Fecha"].min()
    if pd.isna(fecha_min):
        raise RuntimeError("No se pudo determinar el rango de fechas del archivo de facturación")

    df_actual = leer_facturacion_acumulada()
    if not df_actual.empty and "Fecha" in df_actual.columns:
        df_actual = df_actual[df_actual["Fecha"] < fecha_min]

    df_merged = pd.concat([df_actual, df_nuevo], ignore_index=True) if not df_actual.empty else df_nuevo
    guardar_facturacion_acumulada(df_merged)
    logger.info(
        "Facturación fusionada: +%d filas nuevas (desde %s), %d filas totales acumuladas",
        len(df_nuevo), fecha_min.date(), len(df_merged),
    )
    return df_merged


# ==============================================================================
# Pipeline — mismo orden de fases que app.py, sin Streamlit.
# ==============================================================================
def _run_pipeline(
    job_id: str,
    pedidos_bytes: bytes,
    inventario_bytes: bytes,
    externas_bytes: bytes,
    facturacion_bytes: Optional[bytes],
    fuentes_activas: list[str],
    gen_todas: bool,
    gen_resumen: bool,
    gen_reporte_consumo: bool,
    gen_resumen_fac: bool,
    gen_sug_consumo: bool,
) -> None:
    try:
        _set(job_id, status="procesando", fase="Cargando archivos", progreso=0.02)
        necesita_facturacion = gen_reporte_consumo or gen_sug_consumo or gen_resumen_fac

        # Facturación: si viene un archivo nuevo, es la ventana incremental
        # (mes corriente + ~7 días) — se fusiona con lo acumulado en R2 y esa
        # fusión (no solo la ventana) es lo que alimenta el pipeline. Si no
        # viene archivo, se usa directo lo acumulado — evita tener que
        # resubir todo el historial cada vez (el pedido original del usuario).
        def _t_facturacion():
            if facturacion_bytes is not None:
                df_nuevo, _ = cargar_facturacion(io.BytesIO(facturacion_bytes))
                return _fusionar_facturacion(df_nuevo), None
            df_acumulado = leer_facturacion_acumulada()
            if df_acumulado.empty:
                raise RuntimeError("No hay facturación acumulada en R2 todavía — sube el archivo de Facturación al menos una vez.")
            return df_acumulado, None

        tareas = {
            "pedidos": lambda: cargar_pedidos(io.BytesIO(pedidos_bytes)),
            "inventario": lambda: cargar_inventario(io.BytesIO(inventario_bytes)),
            "externas": lambda: cargar_hojas_externas(io.BytesIO(externas_bytes)),
            "facturacion": _t_facturacion if necesita_facturacion else (lambda: (None, None)),
        }
        resultados, errores = {}, {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(fn): k for k, fn in tareas.items()}
            for fut in as_completed(futures):
                key = futures[fut]
                try:
                    resultados[key] = fut.result()
                except Exception as e:  # noqa: BLE001
                    errores[key] = str(e)

        for key in ("pedidos", "inventario", "externas"):
            if key in errores:
                raise RuntimeError(f"Error cargando {key}: {errores[key]}")
        if necesita_facturacion and "facturacion" in errores:
            raise RuntimeError(f"Error cargando facturación: {errores['facturacion']}")

        pedidos_df, _ = resultados["pedidos"]
        inventario_df, _ = resultados["inventario"]
        hojas_externas = resultados["externas"]
        df_facturacion_procesado = resultados["facturacion"][0] if necesita_facturacion else None
        df_revision = hojas_externas.get("Revision")

        _set(job_id, progreso=0.1)

        df_reporte_consumo = None
        if gen_reporte_consumo or gen_sug_consumo:
            _set(job_id, fase="Generando Reporte de Consumo", progreso=0.15)
            if df_facturacion_procesado is not None and not df_facturacion_procesado.empty:
                df_reporte_consumo = generar_reporte_consumo(
                    df_facturacion_procesado,
                    reportar_progreso=lambda p, t: _set(job_id, progreso=0.15 + 0.15 * p, fase=f"Reporte de Consumo — {t}"),
                )

        df_resumen_fac = None
        if gen_resumen_fac:
            _set(job_id, fase="Generando Resumen_Fac", progreso=0.32)
            if df_facturacion_procesado is not None and not df_facturacion_procesado.empty:
                df_resumen_fac = generar_resumen_facturacion(
                    df_facturacion_procesado,
                    reportar_progreso=lambda p, t: _set(job_id, progreso=0.32 + 0.08 * p, fase=f"Resumen_Fac — {t}"),
                )

        df_todas_sugerencias = None
        if gen_todas or gen_resumen:
            _set(job_id, fase="Generando Todas las Sugerencias", progreso=0.42)
            df_todas_sugerencias = generar_todas_sugerencias(
                pedidos_df, hojas_externas, fuentes_activas, inventario_df,
                reportar_progreso=lambda p, t: _set(job_id, progreso=0.42 + 0.35 * p, fase=f"Sugerencias — {t}"),
            )

        df_resumen_sin_sugerencias = None
        if gen_resumen:
            _set(job_id, fase="Generando Resumen Sin Sugerencias", progreso=0.78)
            df_resumen_sin_sugerencias = generar_resumen_sin_sugerencias_optimizado(
                df_todas_sugerencias, inventario_df, df_todas_sugerencias, df_facturacion_procesado,
                df_revision=df_revision,
            )

        if gen_resumen and df_todas_sugerencias is not None and not df_todas_sugerencias.empty:
            _set(job_id, fase="Enriqueciendo sugerencias", progreso=0.85)
            df_todas_sugerencias = enriquecer_sugerencias_con_consumo(
                df_todas_sugerencias,
                df_resumen_sin_sugerencias if df_resumen_sin_sugerencias is not None else pd.DataFrame(),
                df_facturacion=df_facturacion_procesado,
                df_reporte_consumo=df_reporte_consumo,
            )

        df_sug_consumo = None
        if gen_sug_consumo and df_reporte_consumo is not None and not df_reporte_consumo.empty:
            _set(job_id, fase="Generando Sug Reporte Consumo", progreso=0.9)
            df_sug_consumo = generar_sugerencias_desde_reporte_consumo(
                df_reporte_consumo, hojas_externas, fuentes_activas, inventario_df,
                df_resumen=df_resumen_sin_sugerencias,
                reportar_progreso=lambda p, t: _set(job_id, progreso=0.9 + 0.05 * p, fase=f"Sug. Consumo — {t}"),
            )

        df_inventario_por_condicion = None
        df_detalle_lotes_cc = None
        df_revision2 = hojas_externas.get("Revision2")
        df_corta_caducidad = hojas_externas.get("Corta caducidad")
        if (df_revision2 is not None and not df_revision2.empty) or (
            df_corta_caducidad is not None and not df_corta_caducidad.empty
        ):
            _set(job_id, fase="Generando Inventario por condicion", progreso=0.95)
            df_inventario_por_condicion, df_detalle_lotes_cc = generar_inventario_por_condicion(
                df_revision2=df_revision2, df_corta_caducidad=df_corta_caducidad, inventario_df=inventario_df,
            )

        _set(job_id, fase="Generando archivo Excel", progreso=0.98)
        excel_bytes = exportar_a_excel(
            df_todas_sugerencias if gen_todas else None,
            df_resumen_sin_sugerencias if gen_resumen else None,
            df_reporte_consumo if gen_reporte_consumo else None,
            df_sug_consumo if gen_sug_consumo else None,
            df_inventario_por_condicion=df_inventario_por_condicion,
            df_detalle_lotes_cc=df_detalle_lotes_cc,
            df_resumen_fac=df_resumen_fac if gen_resumen_fac else None,
        )

        timestamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Reporte_Completo_{timestamp}.xlsx"
        with _lock:
            _job_files[job_id] = excel_bytes
        _set(job_id, status="listo", fase="Completado", progreso=1.0, filename=filename)
        logger.info("Job %s listo: %s (%.0f KB)", job_id, filename, len(excel_bytes) / 1024)
    except Exception as e:  # noqa: BLE001
        logger.error("Job %s falló: %s", job_id, e, exc_info=True)
        _set(job_id, status="error", error=str(e))


# ==============================================================================
# Endpoints
# ==============================================================================
@app.post("/reportes")
async def crear_reporte(
    pedidos: UploadFile = File(...),
    inventario: UploadFile = File(...),
    externas: UploadFile = File(...),
    facturacion: Optional[UploadFile] = File(None),
    fuentes_activas: str = Form(",".join(FUENTES_DISPONIBLES)),  # CSV
    gen_todas: bool = Form(True),
    gen_resumen: bool = Form(True),
    gen_reporte_consumo: bool = Form(False),
    gen_resumen_fac: bool = Form(False),
    gen_sug_consumo: bool = Form(False),
    user_email: str = Depends(require_user),
):
    _cleanup_old_jobs()
    necesita_facturacion = gen_reporte_consumo or gen_sug_consumo or gen_resumen_fac
    if necesita_facturacion and facturacion is None and facturacion_object_meta() is None:
        raise HTTPException(400, "Este reporte requiere facturación — sube el archivo (no hay nada acumulado en R2 todavía)")

    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = Job(id=job_id, created_at=time.time())

    pedidos_bytes = await pedidos.read()
    inventario_bytes = await inventario.read()
    externas_bytes = await externas.read()
    facturacion_bytes = await facturacion.read() if facturacion is not None else None
    fuentes = [f.strip() for f in fuentes_activas.split(",") if f.strip()]

    thread = threading.Thread(
        target=_run_pipeline,
        args=(job_id, pedidos_bytes, inventario_bytes, externas_bytes, facturacion_bytes, fuentes,
              gen_todas, gen_resumen, gen_reporte_consumo, gen_resumen_fac, gen_sug_consumo),
        daemon=True,
    )
    thread.start()
    return {"job_id": job_id}


@app.get("/reportes/{job_id}")
async def estado_reporte(job_id: str, _user: str = Depends(require_user)):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job no encontrado (o expiró)")
    return job


@app.get("/reportes/{job_id}/archivo")
async def descargar_reporte(job_id: str, _user: str = Depends(require_user)):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job no encontrado (o expiró)")
    if job.status != "listo":
        raise HTTPException(409, f"El reporte todavía no está listo (status={job.status})")
    data = _job_files.get(job_id)
    if data is None:
        raise HTTPException(410, "El archivo ya no está disponible, vuelve a generarlo")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{job.filename}"'},
    )


@app.get("/facturacion/estado")
async def estado_facturacion(_user: str = Depends(require_user)):
    """Metadata de lo acumulado en R2 — para que la UI muestre cuándo se
    actualizó por última vez y qué tan pesado está, sin traer el archivo."""
    meta = facturacion_object_meta()
    if meta is None:
        return {"existe": False}
    df = leer_facturacion_acumulada()
    fecha_min = df["Fecha"].min() if "Fecha" in df.columns and not df.empty else None
    fecha_max = df["Fecha"].max() if "Fecha" in df.columns and not df.empty else None
    return {
        "existe": True,
        "filas": len(df),
        "size_kb": meta["size_kb"],
        "actualizado": meta["actualizado"],
        "fecha_min": fecha_min.date().isoformat() if fecha_min is not None and not pd.isna(fecha_min) else None,
        "fecha_max": fecha_max.date().isoformat() if fecha_max is not None and not pd.isna(fecha_max) else None,
    }


@app.post("/facturacion")
async def actualizar_facturacion(archivo: UploadFile = File(...), _user: str = Depends(require_user)):
    """Sube solo la ventana reciente (mes corriente + ~7 días) y la fusiona
    con lo acumulado en R2 — llamar esto solo (sin generar un reporte) cuando
    solo quieras refrescar Facturación, p.ej. para que Comodato la use."""
    data = await archivo.read()
    df_nuevo, _ = cargar_facturacion(io.BytesIO(data))
    df_merged = _fusionar_facturacion(df_nuevo)
    return {"filas_nuevas": len(df_nuevo), "filas_totales": len(df_merged)}


@app.get("/facturacion/parquet")
async def descargar_facturacion_parquet(_user: str = Depends(require_user)):
    """Parquet crudo de lo acumulado — lo consume el módulo Comodato en el
    navegador (DuckDB-wasm), sin pasar por JSON."""
    df = leer_facturacion_acumulada()
    if df.empty:
        raise HTTPException(404, "No hay facturación acumulada todavía")
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return Response(content=buf.getvalue(), media_type="application/octet-stream")


@app.get("/health")
async def health():
    return {"ok": True}
