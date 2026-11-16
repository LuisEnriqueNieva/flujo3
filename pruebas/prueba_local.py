"""Prueba local del Flujo 3 SIN gastar nada: simula AWS con moto y Cloud Run con el cliente de pruebas de Flask.

    pip install boto3 moto flask scikit-learn numpy
    cd datos && python generar_datos.py config.json salida && cd ..
    python pruebas/prueba_local.py
"""
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [RAIZ, os.path.join(RAIZ, "cloudrun"), os.path.join(RAIZ, "datos")]

os.environ.update({
    "AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "x",
    "BUCKET_LECTURAS": "arquifusion-g13-lecturas-test",
    "T_MAQUINAS": "t_maquinas", "T_MANTENIMIENTO": "t_mantenimiento", "T_RESULTADOS": "t_resultados",
    "T_REPUESTOS": "t_repuestos", "T_PEDIDOS_REPUESTO": "t_pedidos_repuesto",
    "T_INDICADORES": "t_indicadores", "T_PLAN": "t_plan",
    "PARAM_GCP_URL": "/arquifusion/gcp/url", "PARAM_GCP_KEY": "/arquifusion/gcp/api-key",
    "VENTANA_DIAS": "7", "INTERVALO_MIN": "10", "HORIZONTE_DIAS": "14", "USO_DIARIO_HORAS": "8",
    "API_KEY": "clave-test"})

import boto3
from moto import mock_aws

DEFINICIONES = {
    "t_maquinas": [("machine_id", "HASH")], "t_mantenimiento": [("machine_id", "HASH"), ("registro_id", "RANGE")],
    "t_resultados": [("machine_id", "HASH"), ("timestamp", "RANGE")],
    "t_repuestos": [("zona", "HASH"), ("repuesto_id", "RANGE")],
    "t_pedidos_repuesto": [("pedido_id", "HASH")],
    "t_indicadores": [("fecha", "HASH"), ("clave", "RANGE")], "t_plan": [("fecha", "HASH"), ("clave", "RANGE")]}


@mock_aws
def main():
    ddb = boto3.resource("dynamodb")
    for nombre, claves in DEFINICIONES.items():
        attrs = {k for k, _ in claves}
        extra = {}
        if nombre == "t_pedidos_repuesto":      # igual que en el Flujo 2: indice por estado y fecha
            attrs |= {"estado", "fecha"}
            extra["GlobalSecondaryIndexes"] = [{"IndexName": "EstadoFechaIndex", "Projection": {"ProjectionType": "ALL"},
                                                "KeySchema": [{"AttributeName": "estado", "KeyType": "HASH"},
                                                              {"AttributeName": "fecha", "KeyType": "RANGE"}]}]
        ddb.create_table(TableName=nombre, BillingMode="PAY_PER_REQUEST",
                         AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"} for k in sorted(attrs)],
                         KeySchema=[{"AttributeName": k, "KeyType": t} for k, t in claves], **extra)
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket=os.environ["BUCKET_LECTURAS"])
    ssm = boto3.client("ssm")
    ssm.put_parameter(Name="/arquifusion/gcp/url", Value="https://ejemplo.run.app", Type="String")
    ssm.put_parameter(Name="/arquifusion/gcp/api-key", Value="clave-test", Type="SecureString")

    # SNS con tres colas suscritas con el mismo filtro por rol que usan los correos reales
    sns, sqs = boto3.client("sns"), boto3.client("sqs")
    tema = sns.create_topic(Name="TemaReportes")["TopicArn"]
    os.environ["TOPIC_REPORTES"] = tema
    colas = {}
    for rol in ("supervisor", "logistica", "gerencia"):
        url = sqs.create_queue(QueueName=f"cola-{rol}")["QueueUrl"]
        arn = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        sns.subscribe(TopicArn=tema, Protocol="sqs", Endpoint=arn,
                      Attributes={"FilterPolicy": json.dumps({"rol": [rol]}), "RawMessageDelivery": "true"})
        colas[rol] = url

    import cargar_aws
    carpeta = os.path.join(RAIZ, "datos", "salida")
    print("== Carga de datos ==")
    cargar_aws.cargar_tablas(ddb, carpeta)
    cargar_aws.cargar_lecturas(s3, os.environ["BUCKET_LECTURAS"], carpeta)
    # pedido pendiente (lo que escribiria el Flujo 2)
    # Un pedido en el formato exacto del Flujo 2 (sin `componente`; estado en mayusculas)
    ddb.Table("t_pedidos_repuesto").put_item(Item={"pedido_id": "PED-1", "repuesto_id": "REP-002", "cantidad": 2,
                                                  "estado": "PENDIENTE", "fecha": "2026-11-14", "zona": "Lima Norte"})
    ddb.Table("t_pedidos_repuesto").put_item(Item={"pedido_id": "PED-0", "repuesto_id": "REP-002", "cantidad": 5,
                                                  "estado": "ATENDIDO", "fecha": "2026-11-01", "zona": "Lima Norte"})

    # Cloud Run simulado: las Lambdas llaman al servicio Flask en memoria
    import comun
    import main as cloudrun
    cliente = cloudrun.app.test_client()

    def falso_cloud_run(ruta, payload, timeout=50):
        r = cliente.post(ruta, json=payload, headers={"X-API-Key": comun.parametro("/arquifusion/gcp/api-key")})
        assert r.status_code == 200, (ruta, r.status_code, r.get_json())
        return r.get_json()
    comun.llamar_cloud_run = falso_cloud_run
    assert comun.parametro("/arquifusion/gcp/url", False) == "https://ejemplo.run.app"

    import analizar_combustible, calcular_productividad, planificar_mantenimiento, consolidar_reportes
    import consultar_indicadores, consultar_plan
    evento = {"fecha": "2026-11-14"}

    print("\n== Rama A: combustible ==")
    a = analizar_combustible.lambda_handler(evento, None)
    print(json.dumps(a, ensure_ascii=False)[:600])
    assert a["total_anomalias"] == 4, a["total_anomalias"]

    print("\n== Rama B: productividad ==")
    b = calcular_productividad.lambda_handler(evento, None)
    print(json.dumps({"flota": b["flota"], "peores": b["peores"][:2]}, ensure_ascii=False))
    assert 0 < b["flota"]["utilizacion"] <= 1

    print("\n== Rama C: planificacion y repuestos ==")
    c = planificar_mantenimiento.lambda_handler(evento, None)
    print(json.dumps(c, ensure_ascii=False))
    assert c["mantenimiento"]["urgentes"] + c["mantenimiento"]["programar"] > 0
    bomba = next(r for r in c["reposicion"] if r["componente"] == "bomba_hidraulica")
    assert bomba["pendientes"] == 2, bomba           # cuenta el PENDIENTE y NO el ATENDIDO

    print("\n== Consolidar (SNS) ==")
    out = consolidar_reportes.lambda_handler([a, b, c], None)
    print(out)
    for rol, url in colas.items():
        msgs = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10).get("Messages", [])
        assert len(msgs) == 1, (rol, len(msgs))          # el filtro por rol entrega 1 mensaje a cada rol
        if rol == "supervisor":
            print("\n--- correo del supervisor ---\n" + msgs[0]["Body"])
        if rol == "logistica":
            print("\n--- correo de logistica ---\n" + msgs[0]["Body"])

    print("\n== API de consulta ==")
    r1 = consultar_indicadores.lambda_handler({"queryStringParameters": {"tipo": "productividad_flota"}}, None)
    r2 = consultar_plan.lambda_handler({"queryStringParameters": {"tipo": "repuesto"}}, None)
    print(r1["statusCode"], json.loads(r1["body"])["total"], "| plan repuestos:", r2["statusCode"], json.loads(r2["body"])["total"])
    assert r1["statusCode"] == 200 and r2["statusCode"] == 200
    print("\nTODO OK")


main()
