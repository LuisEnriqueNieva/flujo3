"""Funciones compartidas por las Lambdas del Flujo 3 (optimizacion operativa)."""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

import boto3

_clientes = {}
_cache_parametros = {}


def _cliente(nombre):
    if nombre not in _clientes:
        _clientes[nombre] = boto3.client(nombre)
    return _clientes[nombre]


def tabla(var_env):
    """Devuelve la tabla DynamoDB cuyo nombre esta en la variable de entorno var_env."""
    if "ddb" not in _clientes:
        _clientes["ddb"] = boto3.resource("dynamodb")
    return _clientes["ddb"].Table(os.environ[var_env])


# ---------- fechas ----------
def hoy():
    return datetime.now(timezone.utc).date().isoformat()


def fecha_ejecucion(event):
    """La fecha de la corrida. Se puede forzar con {"fecha": "AAAA-MM-DD"} al iniciar la ejecucion."""
    if isinstance(event, dict) and event.get("fecha"):
        return event["fecha"]
    return hoy()


# ---------- DynamoDB ----------
def a_dynamo(obj):
    """Convierte floats a Decimal (DynamoDB no acepta float)."""
    return json.loads(json.dumps(obj, default=str), parse_float=Decimal)


def desde_dynamo(obj):
    if isinstance(obj, list):
        return [desde_dynamo(x) for x in obj]
    if isinstance(obj, dict):
        return {k: desde_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    return obj


def escanear(t, **kw):
    items, resp = [], t.scan(**kw)
    items.extend(resp["Items"])
    while "LastEvaluatedKey" in resp:
        resp = t.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **kw)
        items.extend(resp["Items"])
    return desde_dynamo(items)


def consultar(t, **kw):
    items, resp = [], t.query(**kw)
    items.extend(resp["Items"])
    while "LastEvaluatedKey" in resp:
        resp = t.query(ExclusiveStartKey=resp["LastEvaluatedKey"], **kw)
        items.extend(resp["Items"])
    return desde_dynamo(items)


def guardar_lote(t, items):
    with t.batch_writer(overwrite_by_pkeys=None) as lote:
        for it in items:
            lote.put_item(Item=a_dynamo(it))


# ---------- S3: historial de lecturas ----------
def _parsear(texto):
    texto = texto.strip()
    if not texto:
        return []
    if texto.startswith("["):
        return json.loads(texto)
    return [json.loads(linea) for linea in texto.splitlines() if linea.strip()]


def leer_lecturas(dias=None):
    """Lee las lecturas de los ultimos `dias` dias que haya en S3.

    Formato esperado: s3://BUCKET/lecturas/AAAA-MM-DD/<archivo>.jsonl  (una lectura JSON por linea).
    Devuelve (lista_de_lecturas, lista_de_fechas_leidas).
    """
    bucket = os.environ["BUCKET_LECTURAS"]
    dias = int(dias or os.environ.get("VENTANA_DIAS", "7"))
    s3 = _cliente("s3")
    pag = s3.get_paginator("list_objects_v2")
    fechas = []
    for p in pag.paginate(Bucket=bucket, Prefix="lecturas/", Delimiter="/"):
        for cp in p.get("CommonPrefixes", []):
            f = cp["Prefix"].split("/")[1]
            if len(f) == 10 and f[4] == "-" and f[7] == "-":
                fechas.append(f)
    fechas = sorted(fechas)[-dias:]
    lecturas = []
    for f in fechas:
        for p in pag.paginate(Bucket=bucket, Prefix=f"lecturas/{f}/"):
            for o in p.get("Contents", []):
                cuerpo = s3.get_object(Bucket=bucket, Key=o["Key"])["Body"].read().decode("utf-8")
                lecturas.extend(_parsear(cuerpo))
    return lecturas, fechas


# ---------- Parameter Store + llamada a Google Cloud Run ----------
def parametro(nombre, descifrar=True):
    if nombre not in _cache_parametros:
        resp = _cliente("ssm").get_parameter(Name=nombre, WithDecryption=descifrar)
        _cache_parametros[nombre] = resp["Parameter"]["Value"]
    return _cache_parametros[nombre]


def llamar_cloud_run(ruta, payload, timeout=50):
    """POST HTTPS al servicio de Cloud Run. URL y clave salen de Parameter Store (no van en el codigo)."""
    url = parametro(os.environ["PARAM_GCP_URL"], False).rstrip("/") + ruta
    clave = parametro(os.environ["PARAM_GCP_KEY"], True)
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": clave})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Cloud Run {ruta} respondio {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}")


# ---------- API Gateway ----------
def respuesta_api(codigo, cuerpo):
    return {
        "statusCode": codigo,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(cuerpo, default=str, ensure_ascii=False),
    }
