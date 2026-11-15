"""Servicio de IA del Flujo 3 (Google Cloud Run).

POST /combustible  -> detecta maquinas con consumo anormal (IsolationForest + z-score robusto)
POST /demanda      -> estima la demanda de repuestos (tasa historica de fallas + riesgo esperado)
GET  /salud        -> prueba de vida (sin clave)

Se protege con una clave en el header  X-API-Key  (variable de entorno API_KEY).
"""
import math
import os
from collections import defaultdict

import numpy as np
from flask import Flask, jsonify, request
from sklearn.ensemble import IsolationForest

app = Flask(__name__)

UMBRAL_Z = float(os.environ.get("UMBRAL_Z", "3.0"))          # regla estadistica estandar (3 desviaciones robustas)
UMBRAL_PUNTAJE = float(os.environ.get("UMBRAL_PUNTAJE", "0.5"))  # >0.5 = sospechoso en IsolationForest
MIN_PARA_IF = 8                                              # con menos maquinas no se entrena el bosque


@app.before_request
def verificar_clave():
    if request.path == "/salud":
        return None
    esperada = os.environ.get("API_KEY")
    if not esperada:
        return jsonify({"error": "API_KEY no configurada en el servicio"}), 503
    if request.headers.get("X-API-Key") != esperada:
        return jsonify({"error": "clave invalida"}), 401
    return None


@app.get("/salud")
def salud():
    return jsonify({"estado": "ok"})


def z_robusto(valores, grupos):
    """z-score con mediana y MAD, calculado dentro de cada grupo (modelo de maquina)."""
    valores = np.asarray(valores, dtype=float)
    z = np.zeros(len(valores))
    med_g = np.zeros(len(valores))
    por_grupo = defaultdict(list)
    for i, g in enumerate(grupos):
        por_grupo[g].append(i)
    for g, idx in por_grupo.items():
        v = valores[idx]
        med = float(np.median(v))
        mad = float(np.median(np.abs(v - med)))
        escala = max(1.4826 * mad, 0.05 * abs(med), 1e-9)   # piso para no dividir por ~0
        z[idx] = (v - med) / escala
        med_g[idx] = med
    return z, med_g


@app.post("/combustible")
def combustible():
    maquinas = (request.get_json(silent=True) or {}).get("maquinas", [])
    if not maquinas:
        return jsonify({"error": "falta la lista 'maquinas'"}), 400
    grupos = [m["modelo"] for m in maquinas]
    z_cons, mediana = z_robusto([m["consumo_medio"] for m in maquinas], grupos)
    z_carga, _ = z_robusto([m["consumo_por_carga"] for m in maquinas], grupos)
    z_rpm, _ = z_robusto([m["rpm_medio"] for m in maquinas], grupos)

    if len(maquinas) >= MIN_PARA_IF:
        X = np.column_stack([z_cons, z_carga, z_rpm])
        bosque = IsolationForest(n_estimators=200, contamination="auto", random_state=0).fit(X)
        puntaje = -bosque.score_samples(X)          # mas alto = mas anomalo (convencion del paper: >0.5 sospechoso)
        usa_if = True
    else:
        puntaje = np.clip(z_cons / 6.0, 0, 1)       # respaldo simple con pocas maquinas
        usa_if = False

    resultados = []
    for i, m in enumerate(maquinas):
        z = float(z_cons[i])
        anomalia = bool(z >= UMBRAL_Z and puntaje[i] >= (UMBRAL_PUNTAJE if usa_if else 0.0))
        exceso = (m["consumo_medio"] / mediana[i] - 1) * 100 if mediana[i] else 0.0
        resultados.append({
            "machine_id": m["machine_id"],
            "z_consumo": round(z, 2),
            "exceso_pct": round(exceso, 1),
            "puntaje_anomalia": round(float(puntaje[i]), 3),
            "anomalia": anomalia,
            "causa_probable": (f"consumo {exceso:.0f}% sobre la mediana de su modelo"
                               if anomalia else "dentro de lo normal"),
        })
    return jsonify({"modelo": "IsolationForest + z-score robusto" if usa_if else "z-score robusto",
                    "umbral_z": UMBRAL_Z, "resultados": resultados})


@app.post("/demanda")
def demanda():
    d = request.get_json(silent=True) or {}
    horizonte = float(d.get("horizonte_dias", 14))
    dias_hist = max(float(d.get("dias_historial", 7)), 1.0)
    fallas = defaultdict(int)
    for f in d.get("fallas", []):
        fallas[f["componente"]] += 1
    riesgo = defaultdict(float)
    for r in d.get("riesgos", []):
        riesgo[r["componente_probable"]] += float(r["prob_falla"])
    comps = sorted(set(d.get("componentes", [])) | set(fallas) | set(riesgo))

    salida = []
    for c in comps:
        tasa = fallas[c] / dias_hist                  # fallas por dia segun el historico (proceso de Poisson)
        esperado_hist = tasa * horizonte
        total = esperado_hist + riesgo[c]             # + fallas que el modelo ya ve venir (suma de probabilidades)
        salida.append({"componente": c, "tasa_historica_dia": round(tasa, 4),
                       "esperado_historico": round(esperado_hist, 2),
                       "riesgo_esperado": round(riesgo[c], 2),
                       "demanda_prevista": round(total, 2)})
    return jsonify({"horizonte_dias": horizonte, "metodo": "tasa historica (Poisson) + riesgo esperado",
                    "componentes": salida})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
