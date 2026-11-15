"""Rama C del Flujo 3: planificacion con historico (problema 4) y repuestos (problema 8).

1. Plan de mantenimiento: horas desde el ultimo preventivo vs el intervalo del modelo + riesgo de falla (Flujo 1).
2. Demanda de repuestos: Google Cloud Run (POST /demanda) estima cuantas piezas se necesitaran.
3. Reposicion: demanda prevista - stock - pedidos pendientes (t_pedidos_repuesto, que escribe el Flujo 2).
"""
import json
import math
import os
from collections import defaultdict
from datetime import date, timedelta

from comun import escanear, fecha_ejecucion, guardar_lote, llamar_cloud_run, tabla

MAPA_DEFECTO = '{"temperatura": "motor", "vibracion": "rodamiento", "presion": "bomba_hidraulica"}'


def lambda_handler(event, context):
    fecha = fecha_ejecucion(event)
    hoy = date.fromisoformat(fecha)
    horizonte = int(os.environ.get("HORIZONTE_DIAS", "14"))
    uso_diario = float(os.environ.get("USO_DIARIO_HORAS", "8"))
    u_urg = float(os.environ.get("UMBRAL_RIESGO_URGENTE", "0.7"))
    u_prev = float(os.environ.get("UMBRAL_RIESGO_PREVENTIVO", "0.35"))
    mapa = json.loads(os.environ.get("MAPA_VARIABLE_COMPONENTE", MAPA_DEFECTO))

    maquinas = escanear(tabla("T_MAQUINAS"))
    mant = escanear(tabla("T_MANTENIMIENTO"))
    resultados = escanear(tabla("T_RESULTADOS"))
    repuestos = escanear(tabla("T_REPUESTOS"))
    pedidos = escanear(tabla("T_PEDIDOS_REPUESTO"))

    # ---- datos de apoyo ----
    ultimo_prev = defaultdict(float)
    for r in mant:
        if r.get("tipo") == "preventivo":
            ultimo_prev[r["machine_id"]] = max(ultimo_prev[r["machine_id"]], float(r["horas_al_momento"]))
    riesgo = {}
    for r in resultados:                                  # el resultado mas reciente de cada maquina
        if r["machine_id"] not in riesgo or r["timestamp"] > riesgo[r["machine_id"]]["timestamp"]:
            riesgo[r["machine_id"]] = r

    # ---- 1. plan de mantenimiento ----
    plan, urgentes, programar = [], [], []
    for m in maquinas:
        mid = m["machine_id"]
        restante = float(m["intervalo_mant_horas"]) - (float(m["horas_uso"]) - ultimo_prev.get(mid, 0.0))
        dias = restante / uso_diario
        prob = float(riesgo.get(mid, {}).get("prob_falla", 0))
        motivos = []
        if restante <= 0 or prob >= u_urg:
            prioridad, sugerida = "urgente", hoy
        elif dias <= horizonte or prob >= u_prev:
            prioridad = "programar"
            sugerida = hoy + timedelta(days=max(0, math.floor(dias)))
        else:
            continue
        if restante <= 0:
            motivos.append("intervalo de mantenimiento vencido")
        elif dias <= horizonte:
            motivos.append(f"le tocan mantenimiento en ~{max(0, math.floor(dias))} dias")
        if prob >= u_prev:
            motivos.append(f"riesgo de falla {prob:.0%}")
        plan.append({"fecha": fecha, "clave": f"mant#{mid}", "tipo": "mantenimiento", "machine_id": mid,
                     "modelo": m["modelo"], "zona": m["zona"], "prioridad": prioridad,
                     "horas_restantes": round(restante, 1), "dias_estimados": round(dias, 1),
                     "fecha_sugerida": sugerida.isoformat(), "prob_falla": round(prob, 3),
                     "motivo": "; ".join(motivos)})
        (urgentes if prioridad == "urgente" else programar).append(mid)

    # ---- 2. demanda de repuestos (Google Cloud Run) ----
    fechas = [r["fecha"] for r in mant if r.get("fecha")]
    if fechas:
        dias_hist = max(7, (date.fromisoformat(max(fechas)) - date.fromisoformat(min(fechas))).days + 1)
    else:
        dias_hist = 7
    componentes = sorted({r["componente"] for r in repuestos})
    fallas = [{"componente": r["componente"], "fecha": r["fecha"]}
              for r in mant if r.get("falla") and r.get("componente")]
    riesgos = []
    for mid, r in riesgo.items():
        comp = mapa.get(r.get("variable_principal"))
        if comp:
            riesgos.append({"machine_id": mid, "prob_falla": float(r["prob_falla"]), "componente_probable": comp})
    demanda = llamar_cloud_run("/demanda", {"horizonte_dias": horizonte, "dias_historial": dias_hist,
                                            "componentes": componentes, "fallas": fallas, "riesgos": riesgos})
    por_comp = {d["componente"]: d for d in demanda["componentes"]}

    # ---- 3. reposicion ----
    stock = defaultdict(int)
    rep_a_comp = {}
    for r in repuestos:
        stock[r["componente"]] += int(r["stock"])
        rep_a_comp[r["repuesto_id"]] = r["componente"]
    pendientes = defaultdict(int)
    for p in pedidos:
        if p.get("estado") == "pendiente":
            comp = p.get("componente") or rep_a_comp.get(p.get("repuesto_id"))
            if comp:
                pendientes[comp] += int(p.get("cantidad", 1))
    reposicion = []
    for comp in componentes:
        dem = float(por_comp.get(comp, {}).get("demanda_prevista", 0))
        comprar = max(0, math.ceil(dem - stock[comp] - pendientes[comp]))
        fila = {"componente": comp, "demanda_prevista": round(dem, 2), "stock": stock[comp],
                "pendientes": pendientes[comp], "comprar": comprar}
        reposicion.append(fila)
        plan.append({"fecha": fecha, "clave": f"repuesto#{comp}", "tipo": "repuesto",
                     "prioridad": "comprar" if comprar > 0 else "suficiente", **fila})

    guardar_lote(tabla("T_PLAN"), plan)
    return {"rama": "planificacion", "fecha": fecha, "horizonte_dias": horizonte,
            "mantenimiento": {"urgentes": len(urgentes), "programar": len(programar),
                              "lista_urgentes": urgentes[:10]},
            "reposicion": reposicion}
