"""Generador de datos simulados. Todo sale de config.json (nada fijo en el codigo).

Uso:  python generar_datos.py [config.json] [carpeta_salida]
Unidad de "combustible": consumo en litros por hora.
Reutilizable: las funciones muestrear_lectura / crear_maquinas las puede importar
la Lambda Simulador para generar lecturas en vivo con la misma logica.
"""
import csv, json, math, os, random, sys
from datetime import datetime, timedelta

SENSORES = ["temperatura", "vibracion", "presion", "combustible", "carga", "velocidad", "rpm"]


def cargar_config(ruta):
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def crear_maquinas(cfg, rng):
    """Reparte modelos, zonas y decide cuales fallaron, cuales estan en riesgo y cuales gastan de mas."""
    modelos = cfg["modelos"]
    pesos = [m["cantidad_relativa"] for m in modelos]
    n = cfg["n_maquinas"]
    total_horas = cfg["dias_historial"] * 24
    idx = list(range(n))
    rng.shuffle(idx)
    n_falla = round(n * cfg["proporcion_con_falla"])
    n_riesgo = round(n * cfg["proporcion_en_riesgo"])
    ids_falla = set(idx[:n_falla])
    ids_riesgo = set(idx[n_falla:n_falla + n_riesgo])
    ids_comb = set(rng.sample(range(n), round(n * cfg["combustible_anomalo"]["proporcion"])))
    maquinas = []
    for i in range(n):
        m = rng.choices(modelos, weights=pesos)[0]
        z = rng.choice(cfg["zonas"])
        falla_h, causa = None, None
        if i in ids_falla:
            # la falla ocurre despues de que haya una ventana de degradacion completa
            minimo = min(cfg["horas_degradacion"], total_horas * 0.6)
            falla_h = rng.uniform(minimo, total_horas)
            causa = rng.choice(cfg["causas_falla"])
        elif i in ids_riesgo:
            # esta degradandose ahora: la falla ocurriria poco despues del fin del historial
            falla_h = total_horas + rng.uniform(6, cfg["horas_degradacion"] * 0.8)
            causa = rng.choice(cfg["causas_falla"])
        maquinas.append({
            "machine_id": f"MAQ-{i + 1:03d}",
            "tipo": m["tipo"],
            "modelo": m["modelo"],
            "zona": z["nombre"],
            "lat": z["lat"] + rng.uniform(-0.01, 0.01),
            "lon": z["lon"] + rng.uniform(-0.01, 0.01),
            "horas_uso": round(rng.uniform(*cfg["horas_uso_inicial"]), 1),
            "intervalo_mant_horas": m["intervalo_mant_horas"],
            "estado": "OPERATIVA",
            "repuesto_requerido": next((r["repuesto_id"] for r in cfg["repuestos"] if causa and r["componente"] == causa["componente"]),
                                       rng.choice(cfg["repuestos"])["repuesto_id"]),
            "operador_email": "operador@example.com",
            "_falla_en_horas": falla_h,   # internos: no se envian como datos de la maquina
            "_causa": causa,
            "_factor_combustible": rng.uniform(*cfg["combustible_anomalo"]["factor"]) if i in ids_comb else 1.0,
            "_combustible_desde_h": rng.uniform(0, total_horas * 0.5) if i in ids_comb else 0.0,
        })
    return maquinas


def muestrear_lectura(maquina, cfg, rng, progreso=0.0, estado="operando"):
    """Una lectura. progreso en [0,1]: 0 = sana, 1 = momento de la falla."""
    modelo = next(m for m in cfg["modelos"] if m["modelo"] == maquina["modelo"])
    lectura = {}
    for s in SENSORES:
        p = modelo["sensores"][s]
        # la degradacion crece de forma suave (cuadratica) hacia la falla
        valor = rng.gauss(p["media"], p["desv"]) + p["delta_falla"] * (progreso ** 2)
        lectura[s] = valor
    if estado == "detenida":
        lectura["carga"] = 0
        lectura["velocidad"] = 0
        lectura["rpm"] = cfg["rpm_ralenti"]
    lectura["combustible"] = min(max(lectura["combustible"], 0), 100)
    lectura["carga"] = min(max(lectura["carga"], 0), 100)
    lectura["velocidad"] = max(lectura["velocidad"], 0)
    return {k: round(v, 2) for k, v in lectura.items()}


def generar_historial(cfg, maquinas, rng):
    inicio = datetime.fromisoformat(cfg["inicio"])
    paso = cfg["intervalo_min"] / 60
    total_horas = cfg["dias_historial"] * 24
    deg = cfg["horas_degradacion"]
    filas = []
    for m in maquinas:
        falla_h = m["_falla_en_horas"]
        k, t = 0, 0.0
        horas = m["horas_uso"]
        n_pasos = round(total_horas / paso)
        while k < n_pasos:
            t = k * paso
            if falla_h is not None and t > falla_h:
                break
            estado = "operando" if rng.random() < cfg["prob_estado"]["operando"] else "detenida"
            progreso, etiqueta = 0.0, 0
            if falla_h is not None:
                progreso = min(max((t - (falla_h - deg)) / deg, 0.0), 1.0)
                etiqueta = 1 if (falla_h - t) <= cfg["horas_etiqueta_falla"] else 0
            if falla_h is not None and falla_h <= total_horas and falla_h - t < paso:
                estado = "falla"
            lec = muestrear_lectura(m, cfg, rng, progreso, "operando" if estado == "falla" else estado)
            if estado == "detenida":
                lec["combustible"] = round(lec["combustible"] * cfg["combustible_ralenti_factor"], 2)
            elif t >= m["_combustible_desde_h"]:
                lec["combustible"] = round(lec["combustible"] * m["_factor_combustible"], 2)
            m["_progreso_final"] = progreso
            if estado == "operando":
                horas += paso
            ts = inicio + timedelta(hours=t)
            filas.append({
                "machine_id": m["machine_id"], "timestamp": ts.isoformat(),
                "lat": round(m["lat"] + rng.gauss(0, cfg["ruido_ubicacion_grados"]), 6),
                "lon": round(m["lon"] + rng.gauss(0, cfg["ruido_ubicacion_grados"]), 6),
                **lec, "horas_uso": round(horas, 1), "estado": estado,
                "falla_en_24h": etiqueta,
            })
            k += 1
        m["horas_uso_final"] = round(horas, 1)
    return filas


def generar_mantenimiento(cfg, maquinas, rng):
    inicio = datetime.fromisoformat(cfg["inicio"])
    registros = []
    for m in maquinas:
        modelo = next(x for x in cfg["modelos"] if x["modelo"] == m["modelo"])
        inter = modelo["intervalo_mant_horas"]
        ultimo = math.floor(m["horas_uso"] / inter) * inter
        # ultimos 3 preventivos antes del inicio del historial
        for k in range(3):
            h = ultimo - k * inter
            if h <= 0:
                break
            dias_atras = int((m["horas_uso"] - h) / 8) + 1   # supone ~8 h de uso por dia
            registros.append({
                "machine_id": m["machine_id"],
                "fecha": (inicio - timedelta(days=dias_atras)).date().isoformat(),
                "registro_id": f"{(inicio - timedelta(days=dias_atras)).date().isoformat()}#preventivo#{h}",
                "tipo": "preventivo", "horas_al_momento": h, "falla": False, "causa": "", "componente": ""})
        # preventivos que ocurren dentro del historial (la maquina cruza un multiplo del intervalo)
        h0, h1 = m["horas_uso"], m.get("horas_uso_final", m["horas_uso"])
        for k in range(math.floor(h0 / inter) + 1, math.floor(h1 / inter) + 1):
            frac = (k * inter - h0) / max(h1 - h0, 1e-6)
            f_p = (inicio + timedelta(hours=frac * cfg["dias_historial"] * 24)).date().isoformat()
            registros.append({
                "machine_id": m["machine_id"], "registro_id": f"{f_p}#preventivo#{k * inter}",
                "fecha": f_p, "tipo": "preventivo", "horas_al_momento": k * inter,
                "falla": False, "causa": "", "componente": ""})
        if m["_falla_en_horas"] is not None and m["_falla_en_horas"] <= cfg["dias_historial"] * 24:
            causa = m["_causa"]
            fecha_f = (inicio + timedelta(hours=m["_falla_en_horas"])).date().isoformat()
            registros.append({
                "machine_id": m["machine_id"], "registro_id": f"{fecha_f}#correctivo#falla",
                "fecha": fecha_f,
                "tipo": "correctivo", "horas_al_momento": m.get("horas_uso_final", m["horas_uso"]),
                "falla": True, "causa": causa["causa"], "componente": causa["componente"]})
    return registros


def generar_resultados(cfg, maquinas, rng):
    """Resultados de IA de 'demo' (lo que escribiria el Flujo 1) segun el estado final de cada maquina."""
    fin = datetime.fromisoformat(cfg["inicio"]) + timedelta(hours=cfg["dias_historial"] * 24)
    u = cfg["umbrales_clasificacion"]
    filas = []
    for m in maquinas:
        prog = m.get("_progreso_final", 0.0)
        prob = round(min(1.0, 0.03 + 0.97 * prog + rng.uniform(0, 0.03)), 3)
        clas = "urgente" if prob >= u["urgente"] else "preventivo" if prob >= u["preventivo"] else "normal"
        filas.append({
            "machine_id": m["machine_id"], "timestamp": fin.isoformat(),
            "prob_falla": prob, "puntaje_anomalia": round(min(1.0, prob * 0.9 + 0.05), 3),
            "clasificacion": clas,
            "variable_principal": m["_causa"]["variable"] if (m["_causa"] and prob >= u["preventivo"]) else "ninguna"})
    return filas


def generar_tecnicos(cfg, rng):
    """Tecnicos en el esquema del Flujo 2: PK zona + tecnico_id (cada zona queda con tecnicos)."""
    t = cfg["tecnicos"]
    zonas = cfg["zonas"]
    return [{
        "zona": zonas[i % len(zonas)]["nombre"],
        "tecnico_id": f"TEC-{i + 1:03d}",
        "nombre": f"Tecnico {i + 1:03d}",
        "email": f"tecnico{i + 1:03d}@example.com",
        "especialidad": rng.choice(t["especialidades"]),
        "disponibilidad": "DISPONIBLE" if rng.random() < t["prob_disponible"] else "OCUPADO",
    } for i in range(t["cantidad"])]


def generar_repuestos(cfg, rng):
    """Repuestos en el esquema del Flujo 2: PK zona + repuesto_id (se agrega `componente` para el Flujo 3)."""
    filas = []
    for r in cfg["repuestos"]:
        for z in cfg["zonas"]:
            filas.append({"zona": z["nombre"], "repuesto_id": r["repuesto_id"], "nombre": r["nombre"],
                          "componente": r["componente"], "stock": rng.randint(*r["stock"])})
    return filas


def main():
    ruta_cfg = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    salida = sys.argv[2] if len(sys.argv) > 2 else "salida"
    cfg = cargar_config(ruta_cfg)
    rng = random.Random(cfg["seed"])
    os.makedirs(salida, exist_ok=True)

    maquinas = crear_maquinas(cfg, rng)
    lecturas = generar_historial(cfg, maquinas, rng)
    mant = generar_mantenimiento(cfg, maquinas, rng)
    tecnicos = generar_tecnicos(cfg, rng)
    repuestos = generar_repuestos(cfg, rng)

    resultados = generar_resultados(cfg, maquinas, rng)
    pub = []
    for m in maquinas:
        d = {k: v for k, v in m.items() if not k.startswith("_") and k != "horas_uso_final"}
        d["horas_uso"] = m.get("horas_uso_final", m["horas_uso"])   # horas al cierre del historial
        pub.append(d)
    for nombre, datos in [("maquinas", pub), ("mantenimiento", mant), ("resultados", resultados),
                          ("tecnicos", tecnicos), ("repuestos", repuestos)]:
        with open(os.path.join(salida, f"{nombre}.json"), "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
    with open(os.path.join(salida, "lecturas.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(lecturas[0].keys()))
        w.writeheader()
        w.writerows(lecturas)

    n_f = sum(1 for m in maquinas if m["_falla_en_horas"] is not None and m["_falla_en_horas"] <= cfg["dias_historial"] * 24)
    n_r = sum(1 for m in maquinas if m["_falla_en_horas"] is not None) - n_f
    n_c = sum(1 for m in maquinas if m["_factor_combustible"] > 1)
    pos = sum(l["falla_en_24h"] for l in lecturas)
    print(f"maquinas={len(pub)} (con falla={n_f}, en riesgo={n_r}, combustible anomalo={n_c}) lecturas={len(lecturas)} "
          f"etiquetadas_falla={pos} mantenimientos={len(mant)} "
          f"tecnicos={len(tecnicos)} repuestos={len(repuestos)}")


if __name__ == "__main__":
    main()
