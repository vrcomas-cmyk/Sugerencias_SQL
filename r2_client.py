"""
r2_client.py - Cliente S3-compatible para Cloudflare R2.

Mismas credenciales que ya usa la Edge Function `r2-presign` de Supabase
(mismo bucket `degasa-portal`), pero aquí se usan directo desde el backend
Python porque la fusión incremental de Facturación (leer lo acumulado,
recortar la ventana, escribir de vuelta) es más simple server-side que via
URLs prefirmadas para un merge que igual necesita traer el archivo completo
a memoria.
"""
import io
import os

import boto3
import pandas as pd
from botocore.exceptions import ClientError

R2_ENDPOINT = os.environ["R2_ENDPOINT"]
R2_ACCESS_KEY_ID = os.environ["R2_ACCESS_KEY_ID"]
R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
R2_BUCKET = os.environ["R2_BUCKET"]

_client = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    region_name="auto",
)

# Facturación es un dato de la empresa, no personal — una sola llave
# compartida entre los usuarios invitados, no prefijada por usuario como los
# xlsx de la Edge Function r2-presign.
FACTURACION_KEY = "facturacion/acumulada.parquet"


def leer_facturacion_acumulada() -> pd.DataFrame:
    """Lee lo acumulado en R2. Si no existe todavía, regresa un DataFrame vacío."""
    try:
        obj = _client.get_object(Bucket=R2_BUCKET, Key=FACTURACION_KEY)
        return pd.read_parquet(io.BytesIO(obj["Body"].read()))
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return pd.DataFrame()
        raise


def guardar_facturacion_acumulada(df: pd.DataFrame) -> None:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    buf.seek(0)
    _client.put_object(Bucket=R2_BUCKET, Key=FACTURACION_KEY, Body=buf.getvalue())


def facturacion_object_meta() -> dict | None:
    try:
        head = _client.head_object(Bucket=R2_BUCKET, Key=FACTURACION_KEY)
        return {"size_kb": round(head["ContentLength"] / 1024), "actualizado": head["LastModified"].isoformat()}
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise
