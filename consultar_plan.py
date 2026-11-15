"""GET /plan?fecha=AAAA-MM-DD&tipo=mant|repuesto  (plan de mantenimiento y reposicion de repuestos)"""
from boto3.dynamodb.conditions import Key

from comun import consultar, respuesta_api, tabla


def lambda_handler(event, context):
    q = event.get("queryStringParameters") or {}
    t = tabla("T_PLAN")
    fecha = q.get("fecha")
    if not fecha:
        p = t.get_item(Key={"fecha": "_ultima", "clave": "corrida"}).get("Item")
        if not p:
            return respuesta_api(404, {"mensaje": "Todavia no hay corridas del Flujo 3"})
        fecha = p["fecha_ref"]
    cond = Key("fecha").eq(fecha)
    if q.get("tipo"):
        cond = cond & Key("clave").begins_with(q["tipo"] + "#")
    items = consultar(t, KeyConditionExpression=cond)
    return respuesta_api(200, {"fecha": fecha, "total": len(items), "items": items})
