"""Cierre del Flujo 3: arma un reporte por rol y lo publica en SNS (TemaReportes).

Recibe la salida del estado Parallel: una lista con el resultado de las tres ramas.
Cada mensaje lleva el atributo `rol`; las suscripciones filtran por ese atributo.
"""
import os

import boto3

from comun import guardar_lote, tabla


def _lineas(titulo, filas):
    return [titulo] + (filas if filas else ["  (sin novedades)"]) + [""]


def construir(ramas, fecha):
    comb = ramas.get("combustible", {})
    prod = ramas.get("productividad", {})
    plan = ramas.get("planificacion", {})
    flota = prod.get("flota", {})
    anom = comb.get("anomalias", [])
    repo = [r for r in plan.get("reposicion", []) if r["comprar"] > 0]
    mant = plan.get("mantenimiento", {})

    f_anom = [f"  - {a['machine_id']} ({a['modelo']}, {a['zona']}): consumo {a['consumo_medio']} L/h, "
              f"+{a['exceso_pct']}% sobre su modelo" for a in anom]
    f_peor = [f"  - {p['machine_id']} ({p['zona']}): utilizacion {p['utilizacion']:.0%}, "
              f"indice {p['indice_productividad']}" for p in prod.get("peores", [])]
    f_repo = [f"  - {r['componente']}: demanda prevista {r['demanda_prevista']}, stock {r['stock']}, "
              f"pedidos pendientes {r['pendientes']} -> COMPRAR {r['comprar']}" for r in repo]

    supervisor = (["Reporte operativo del " + fecha, ""]
                  + _lineas("Combustible: maquinas con consumo anormal", f_anom)
                  + _lineas("Productividad: maquinas con menor indice", f_peor)
                  + [f"Mantenimiento: {mant.get('urgentes', 0)} urgentes, {mant.get('programar', 0)} por programar "
                     f"(urgentes: {', '.join(mant.get('lista_urgentes', [])) or 'ninguna'})"])
    logistica = (["Reposicion de repuestos al " + fecha, ""]
                 + _lineas(f"Piezas a comprar (horizonte {plan.get('horizonte_dias', '?')} dias)", f_repo))
    gerencia = [f"Resumen de flota {fecha}", "",
                f"Utilizacion de la flota: {flota.get('utilizacion', 0):.0%}",
                f"Disponibilidad: {flota.get('disponibilidad', 0):.0%}",
                f"Maquinas con consumo anormal: {comb.get('total_anomalias', 0)} de {comb.get('maquinas_analizadas', 0)}",
                f"Mantenimientos urgentes: {mant.get('urgentes', 0)}; por programar: {mant.get('programar', 0)}",
                f"Componentes a reponer: {len(repo)}"]
    return {"supervisor": "\n".join(supervisor), "logistica": "\n".join(logistica), "gerencia": "\n".join(gerencia)}


def lambda_handler(event, context):
    lista = event if isinstance(event, list) else [event]
    ramas = {r["rama"]: r for r in lista if isinstance(r, dict) and "rama" in r}
    fecha = next((r["fecha"] for r in ramas.values() if "fecha" in r), "sin-fecha")

    sns = boto3.client("sns")
    mensajes = construir(ramas, fecha)
    for rol, texto in mensajes.items():
        sns.publish(TopicArn=os.environ["TOPIC_REPORTES"],
                    Subject=f"Flujo 3 - reporte diario para {rol} ({fecha})",
                    Message=texto,
                    MessageAttributes={"rol": {"DataType": "String", "StringValue": rol}})

    # puntero a la ultima corrida: las APIs de consulta lo usan cuando no reciben ?fecha=
    for var in ("T_INDICADORES", "T_PLAN"):
        guardar_lote(tabla(var), [{"fecha": "_ultima", "clave": "corrida", "fecha_ref": fecha}])
    return {"publicados": list(mensajes.keys()), "fecha": fecha}
