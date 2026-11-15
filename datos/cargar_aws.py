"""Carga los datos simulados en AWS (S3 + DynamoDB) para probar el Flujo 3.

Uso:
    python generar_datos.py config.json salida
    python cargar_aws.py            (usa las credenciales de `aws configure`, region us-east-1)
    python cargar_aws.py --dir salida --region us-east-1 --bucket mi-bucket
"""
import argparse
import csv
import json
import os
from collections import defaultdict
from decimal import Decimal

import boto3

TABLAS = {"maquinas": "t_maquinas", "mantenimiento": "t_mantenimiento",
          "resultados": "t_resultados", "repuestos": "t_repuestos"}


def a_dynamo(obj):
    return json.loads(json.dumps(obj), parse_float=Decimal)


def cargar_tablas(ddb, carpeta):
    for archivo, nombre in TABLAS.items():
        with open(os.path.join(carpeta, f"{archivo}.json"), encoding="utf-8") as f:
            filas = json.load(f)
        with ddb.Table(nombre).batch_writer() as lote:
            for fila in filas:
                lote.put_item(Item=a_dynamo(fila))
        print(f"  {nombre}: {len(filas)} registros")


def _numero(v):
    try:
        f = float(v)
        return int(f) if f.is_integer() and "." not in v else f
    except ValueError:
        return v


def cargar_lecturas(s3, bucket, carpeta):
    por_dia = defaultdict(list)
    with open(os.path.join(carpeta, "lecturas.csv"), encoding="utf-8") as f:
        for fila in csv.DictReader(f):
            reg = {k: (v if k in ("machine_id", "timestamp", "estado") else _numero(v)) for k, v in fila.items()}
            por_dia[fila["timestamp"][:10]].append(json.dumps(reg))
    for dia, lineas in sorted(por_dia.items()):
        s3.put_object(Bucket=bucket, Key=f"lecturas/{dia}/lecturas.jsonl",
                      Body=("\n".join(lineas) + "\n").encode("utf-8"))
        print(f"  s3://{bucket}/lecturas/{dia}/lecturas.jsonl ({len(lineas)} lecturas)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default="salida")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--bucket")
    a = p.parse_args()
    sesion = boto3.Session(region_name=a.region)
    cuenta = sesion.client("sts").get_caller_identity()["Account"]
    bucket = a.bucket or f"arquifusion-g13-lecturas-{cuenta}"
    print(f"Cuenta {cuenta} | bucket {bucket}")
    print("Tablas:")
    cargar_tablas(sesion.resource("dynamodb"), a.dir)
    print("Lecturas:")
    cargar_lecturas(sesion.client("s3"), bucket, a.dir)
    print("Listo.")


if __name__ == "__main__":
    main()
