"""Rama B del Flujo 3: productividad de la flota (problema 10). No usa Google Cloud.

utilizacion      = horas operando / horas de la ventana
disponibilidad   = horas con lecturas / horas de la ventana (una maquina caida deja de reportar)
indice           = utilizacion x carga media / 100
"""
import os
from collections import defaultdict

from comun import (escanear, fecha_ejecucion, guardar_lote, leer_lecturas, tabla)


def _prom(xs):
    return round(sum(xs) / len(xs), 3) if xs else 0.0


def lambda_handler(event, context):
    fecha = fecha_ejecucion(event)
    lecturas, ventana = leer_lecturas()
    maquinas = {m["machine_id"]: m for m in escanear(tabla("T_MAQUINAS"))}
    h = float(os.environ.get("INTERVALO_MIN", "10")) / 60
    horas_ventana = max(len(ventana), 1) * 24

    c = defaultdict(lambda: {"n": 0, "op": 0, "det": 0, "falla": 0, "carga": 0.0})
    for l in lecturas:
        a = c[l["machine_id"]]
        a["n"] += 1
        est = l.get("estado")
        if est == "operando":
            a["op"] += 1
            a["carga"] += float(l["carga"])
        elif est == "detenida":
            a["det"] += 1
        elif est == "falla":
            a["falla"] += 1

    filas = []
    for mid, m in maquinas.items():
        a = c.get(mid, {"n": 0, "op": 0, "det": 0, "falla": 0, "carga": 0.0})
        util = min(1.0, a["op"] * h / horas_ventana)
        disp = min(1.0, a["n"] * h / horas_ventana)
        carga = a["carga"] / a["op"] if a["op"] else 0.0
        filas.append({"machine_id": mid, "zona": m["zona"], "tipo_maquina": m["tipo"], "modelo": m["modelo"],
                      "horas_operando": round(a["op"] * h, 1), "horas_detenida": round(a["det"] * h, 1),
                      "horas_falla": round(a["falla"] * h, 1), "carga_media": round(carga, 1),
                      "utilizacion": round(util, 3), "disponibilidad": round(disp, 3),
                      "indice_productividad": round(util * carga / 100, 3)})

    rango = f"{ventana[0]}..{ventana[-1]}" if ventana else ""
    items = [{"fecha": fecha, "clave": f"productividad#{f['machine_id']}", "tipo": "productividad",
              "ventana": rango, **f} for f in filas]

    def agrupar(campo, prefijo, etiqueta):
        g = defaultdict(list)
        for f in filas:
            g[f[campo]].append(f)
        out = []
        for k, v in g.items():
            out.append({"fecha": fecha, "clave": f"{prefijo}#{k}", "tipo": prefijo, etiqueta: k,
                        "maquinas": len(v), "ventana": rango,
                        "utilizacion": _prom([x["utilizacion"] for x in v]),
                        "disponibilidad": _prom([x["disponibilidad"] for x in v]),
                        "indice_productividad": _prom([x["indice_productividad"] for x in v])})
        return out

    por_zona = agrupar("zona", "productividad_zona", "zona")
    por_tipo = agrupar("tipo_maquina", "productividad_tipo", "tipo_maquina")
    flota = {"fecha": fecha, "clave": "productividad_flota#total", "tipo": "productividad_flota",
             "maquinas": len(filas), "ventana": rango,
             "utilizacion": _prom([f["utilizacion"] for f in filas]),
             "disponibilidad": _prom([f["disponibilidad"] for f in filas]),
             "indice_productividad": _prom([f["indice_productividad"] for f in filas])}
    guardar_lote(tabla("T_INDICADORES"), items + por_zona + por_tipo + [flota])

    orden = sorted(filas, key=lambda f: f["indice_productividad"])
    cortos = lambda lst: [{k: f[k] for k in ("machine_id", "zona", "utilizacion", "disponibilidad",
                                             "indice_productividad")} for f in lst]
    return {"rama": "productividad", "fecha": fecha,
            "flota": {k: flota[k] for k in ("maquinas", "utilizacion", "disponibilidad", "indice_productividad")},
            "peores": cortos(orden[:3]), "mejores": cortos(orden[-3:][::-1]),
            "por_zona": [{k: z[k] for k in ("zona", "utilizacion", "indice_productividad")} for z in por_zona]}
