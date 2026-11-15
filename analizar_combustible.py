"""Rama A del Flujo 3: detecta consumo de combustible anormal (problema 9).

Lee el historial en S3, resume el consumo por maquina y le pide a Google Cloud Run
(POST /combustible) que detecte las anomalias. Guarda el resultado en t_indicadores.
"""
import os
from collections import defaultdict

from comun import (escanear, fecha_ejecucion, guardar_lote, leer_lecturas,
                   llamar_cloud_run, tabla)


def lambda_handler(event, context):
    fecha = fecha_ejecucion(event)
    lecturas, ventana = leer_lecturas()
    maquinas = {m["machine_id"]: m for m in escanear(tabla("T_MAQUINAS"))}

    acum = defaultdict(lambda: {"n": 0, "comb": 0.0, "carga": 0.0, "rpm": 0.0})
    for l in lecturas:
        if l.get("estado") != "operando":
            continue
        a = acum[l["machine_id"]]
        a["n"] += 1
        a["comb"] += float(l["combustible"])
        a["carga"] += float(l["carga"])
        a["rpm"] += float(l["rpm"])

    horas_por_lectura = float(os.environ.get("INTERVALO_MIN", "10")) / 60
    resumen = []
    for mid, a in acum.items():
        m = maquinas.get(mid)
        if not m or a["n"] == 0:
            continue
        resumen.append({
            "machine_id": mid, "modelo": m["modelo"], "zona": m["zona"],
            "consumo_medio": round(a["comb"] / a["n"], 3),                       # litros por hora
            "consumo_por_carga": round(a["comb"] / max(a["carga"], 1e-6), 4),
            "rpm_medio": round(a["rpm"] / a["n"], 1),
            "horas_operando": round(a["n"] * horas_por_lectura, 1),
        })
    if not resumen:
        return {"rama": "combustible", "fecha": fecha, "maquinas_analizadas": 0, "anomalias": []}

    resp = llamar_cloud_run("/combustible", {"maquinas": resumen})
    por_id = {r["machine_id"]: r for r in resumen}
    rango = f"{ventana[0]}..{ventana[-1]}" if ventana else ""

    items, anomalias = [], []
    for r in resp["resultados"]:
        base = por_id.get(r["machine_id"], {})
        item = {"fecha": fecha, "clave": f"combustible#{r['machine_id']}", "tipo": "combustible",
                "ventana": rango, **base, **r}
        items.append(item)
        if r.get("anomalia"):
            anomalias.append({k: item.get(k) for k in
                              ("machine_id", "modelo", "zona", "consumo_medio", "exceso_pct",
                               "z_consumo", "puntaje_anomalia")})
    guardar_lote(tabla("T_INDICADORES"), items)
    anomalias.sort(key=lambda x: x["z_consumo"], reverse=True)
    return {"rama": "combustible", "fecha": fecha, "maquinas_analizadas": len(items),
            "total_anomalias": len(anomalias), "anomalias": anomalias[:10]}
