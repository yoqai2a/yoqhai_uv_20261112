#!/usr/bin/env python3
"""Genera un CSV con los datos extraidos de los GRIB2 de UV (NOMADS/NCEP).

Uso:
    python Generar_CSV_UV.py                                  # usa _Configuracion_UV.toml
    python Generar_CSV_UV.py otro.toml                        # otro archivo de config
    python Generar_CSV_UV.py _Configuracion_UV.toml 20261104  # fuerza la fecha
    python Generar_CSV_UV.py --inspeccionar archivo.grib2     # lista los mensajes de un GRIB

Requiere: Python 3.11+, numpy y pygrib  (pip install numpy pygrib)

Columnas del CSV
    fecha_corrida, ciclo, hora_pronostico, dia_pronostico, fecha_valida_utc, punto,
    latitud, longitud,
    uv_bruto            valor sin procesar leido del archivo uv (con nubosidad)
    uvucs_bruto         valor sin procesar leido del archivo uvucs (cielo despejado)
    uvi_despejado       indice UV cielo despejado  = uvucs_bruto * factor_uvi (1 decimal)
    uvi_nubosidad       indice UV con nubosidad    = uv_bruto * factor_uvi (1 decimal)
    transmision         uv / uvucs (3 decimales; vacio si el UVI despejado <= umbral)
    max_uvucs           maximo del indice UV despejado del punto en su bloque de 24 h

Maximos por dia (UV_{FECHA}_max_dia.csv y UV_{FECHA}_max_dia.json):
    por cada dia y punto, dos registros:
    1) con_nubosidad    -> hora en que uv (con nubosidad) es maximo
    2) cielo_despejado  -> hora en que uvucs (despejado) es maximo
    Se indica fecha/hora local y UTC. "dia" = bloque de 24 h de pronostico (dia1 = f01-f24, ...)
    o dia calendario local, segun `agrupar_por`. El JSON es un diccionario anidado:
    {fecha_corrida: {"punto (lat, lon)": {max_con_nubosidad: {dia1: {...}}, max_cielo_despejado: {...}}}}
"""
import csv
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pygrib

from _Descargar_UV import CONFIG_POR_DEFECTO, carpeta_destino, fecha_desde_config  # misma logica de fecha (AUTO + hora de cambio UTC)


def leer_config(ruta):
    try:
        with open(ruta, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        sys.exit(f"No se pudo leer la configuracion {ruta}: {e}")


def abrir_mensaje(grbs):
    """Mensaje con el indice UV. Si el archivo tiene uno solo, lo toma directo."""
    if grbs.messages == 1:
        return grbs.message(1)
    for m in grbs:
        if "uv" in f"{m.name} {m.shortName}".lower():
            return m
    return grbs.message(1)


def leer_valores(ruta, con_grilla=False):
    """Valores 2D (NaN donde falta dato) o None si el archivo no existe / esta vacio.

    Solo el PRIMER archivo necesita lats/lons (con_grilla=True): calcularlas cuesta casi
    tanto como leer los valores y la grilla es la misma en todos los archivos.
    """
    ruta = Path(ruta)
    if not ruta.exists() or ruta.stat().st_size == 0:
        return None
    grbs = pygrib.open(str(ruta))
    try:
        msg = abrir_mensaje(grbs)
        vals = np.ma.filled(msg.values.astype("float64"), np.nan)
        if not con_grilla:
            return vals
        lats, lons = msg.latlons()
    finally:
        grbs.close()
    lons = ((lons + 180.0) % 360.0) - 180.0
    return vals, lats, lons


def vecinos_1d(eje, x):
    """Indices (i, j) y peso w para interpolar x entre eje[i] y eje[j]."""
    i = int(np.argmin(np.abs(eje - x)))
    if abs(eje[i] - x) > 1.5 * abs(eje[1] - eje[0]):
        sys.exit(f"El valor {x} esta fuera de la grilla.")
    j = i + 1 if i + 1 < len(eje) and (eje[i] - x) * (eje[i + 1] - x) <= 0 else i - 1
    if j < 0 or j >= len(eje) or eje[j] == eje[i]:
        return i, i, 0.0
    return i, j, float((x - eje[i]) / (eje[j] - eje[i]))


def preparar(e, lats, lons):
    """Devuelve (spec, lat_pts, lon_pts, descripcion).

    spec es un diccionario simple (se puede enviar a otros procesos) que `aplicar`
    usa para sacar los puntos pedidos de cada grilla 2D.
    modo "punto": un solo punto (interpolacion bilineal o vecino mas cercano).
    modo "zona" : todos los puntos de la grilla dentro de la caja lat/lon.
    """
    if e.get("modo", "zona") == "punto":
        lat0, lon0 = e["punto_lat"], e["punto_lon"]
        i, i2, wi = vecinos_1d(lats[:, 0], lat0)
        j, j2, wj = vecinos_1d(lons[0, :], lon0)
        if e.get("metodo", "bilineal") == "vecino":
            ii, jj = (i2 if wi > 0.5 else i), (j2 if wj > 0.5 else j)
            desc = f"vecino mas cercano: lat {lats[ii, jj]:.2f}, lon {lons[ii, jj]:.2f}"
            spec = {"tipo": "indices", "flat": np.array([ii * lats.shape[1] + jj])}
            return spec, np.array([lat0]), np.array([lon0]), desc
        desc = (f"bilineal entre lat {lats[i, 0]:.2f}/{lats[i2, 0]:.2f} "
                f"y lon {lons[0, j]:.2f}/{lons[0, j2]:.2f}")
        spec = {"tipo": "bilineal", "nodos": (i, j, i2, j2), "pesos": (wi, wj)}
        return spec, np.array([lat0]), np.array([lon0]), desc

    mask = ((lats >= e["lat_min"]) & (lats <= e["lat_max"]) &
            (lons >= e["lon_min"]) & (lons <= e["lon_max"]))
    if not mask.any():
        sys.exit("La zona configurada no contiene puntos de la grilla.")
    spec = {"tipo": "indices", "flat": np.flatnonzero(mask)}
    return spec, lats[mask], lons[mask], f"zona con {int(mask.sum())} puntos"


def aplicar(spec, v):
    """Saca de la grilla 2D `v` los valores de los puntos definidos en `spec`."""
    if spec["tipo"] == "bilineal":
        i, j, i2, j2 = spec["nodos"]
        wi, wj = spec["pesos"]
        return np.array([(1 - wi) * (1 - wj) * v[i, j] + (1 - wi) * wj * v[i, j2] +
                         wi * (1 - wj) * v[i2, j] + wi * wj * v[i2, j2]])
    return v.ravel()[spec["flat"]]


def tarea(args):
    """Trabajo de un proceso: lee un archivo y devuelve solo los puntos pedidos."""
    prod, h, ruta, spec, forma = args
    try:
        vals = leer_valores(ruta)
    except Exception as exc:  # archivo danado, etc.
        return prod, h, None, f"{type(exc).__name__}: {exc}"
    if vals is None:
        return prod, h, None, None
    if vals.shape != forma:
        return prod, h, None, f"otra grilla {vals.shape}"
    return prod, h, aplicar(spec, vals), None


def num(v, dec):
    """Numero redondeado para JSON (None si es NaN)."""
    return None if v is None or np.isnan(v) else round(float(v), dec)


def calcular_max_dia(horas, inicio, tz, agrupar, uv, uvucs, factor, umbral):
    """Para cada grupo (dia) y punto, busca la hora del maximo con nubosidad (uv)
    y la del maximo de cielo despejado (uvucs). Devuelve una lista de registros."""
    zona = ZoneInfo(tz)
    locales = [(inicio + timedelta(hours=h)).replace(tzinfo=timezone.utc).astimezone(zona)
               for h in horas]
    if agrupar == "dia_local":
        dias = sorted({t.date() for t in locales})
        grupos = [[i for i, t in enumerate(locales) if t.date() == d] for d in dias]
    else:  # "bloque_24h": dia 1 = f01-f24, dia 2 = f25-f48, ...
        grupos = [list(range(i, min(i + 24, len(horas)))) for i in range(0, len(horas), 24)]

    registros = []
    for n, idx in enumerate(grupos, 1):
        for j in range(uv.shape[1]):
            for tipo, serie in (("con_nubosidad", uv), ("cielo_despejado", uvucs)):
                col = serie[idx, j]
                if np.isnan(col).all():
                    continue
                k = idx[int(np.nanargmax(col))]
                a, b = uv[k, j], uvucs[k, j]
                trans = (a / b if (not np.isnan(a) and not np.isnan(b)
                                   and b * factor > umbral) else np.nan)
                registros.append(dict(
                    dia=n, tipo=tipo, j=j, n_horas=len(idx), hora_pron=horas[k],
                    local=locales[k], utc=inicio + timedelta(hours=horas[k]),
                    uv=a, uvucs=b, uvi_nub=a_uvi(a, factor), uvi_desp=a_uvi(b, factor),
                    trans=trans))
    return registros


def escribir_max_dia(ruta_csv, ruta_json, sep, fecha, nombre, registros, lat_pts, lon_pts):
    """Escribe el CSV de maximos por dia (sin columna ciclo) y el mismo contenido
    como diccionario anidado en JSON."""
    with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=sep, lineterminator="\n")
        w.writerow(["fecha_corrida", "dia", "tipo", "fecha_local", "hora_local", "hora_utc",
                    "hora_pronostico", "punto", "latitud", "longitud", "uv_bruto",
                    "uvucs_bruto", "uvi_despejado", "uvi_nubosidad", "transmision",
                    "n_horas_del_dia"])
        for r in registros:
            j = r["j"]
            w.writerow([fecha, r["dia"], "maximo_" + r["tipo"],
                        r["local"].strftime("%Y-%m-%d"), r["local"].strftime("%H:%M"),
                        r["utc"].strftime("%Y-%m-%d %H:%M"), r["hora_pron"], nombre,
                        fmt(lat_pts[j], 2), fmt(lon_pts[j], 2), fmt(r["uv"], 4),
                        fmt(r["uvucs"], 4), fmt(r["uvi_desp"], 1), fmt(r["uvi_nub"], 1),
                        fmt(r["trans"], 3), r["n_horas"]])

    # {fecha_corrida: {"punto (lat, lon)": {max_con_nubosidad: {dia1: {...}}, max_cielo_despejado: {...}}}}
    puntos = {}
    for r in registros:
        j = r["j"]
        clave = f"{nombre or 'punto'} ({lat_pts[j]:.2f}, {lon_pts[j]:.2f})"
        p = puntos.setdefault(clave, {
            "punto": nombre, "latitud": num(lat_pts[j], 2), "longitud": num(lon_pts[j], 2),
            "max_con_nubosidad": {}, "max_cielo_despejado": {}})
        base = {"fecha_local": r["local"].strftime("%Y-%m-%d"),
                "hora_local": r["local"].strftime("%H:%M"),
                "hora_utc": r["utc"].strftime("%Y-%m-%d %H:%M")}
        if r["tipo"] == "con_nubosidad":
            base.update(uvi_con_nubosidad=num(r["uvi_nub"], 1), uv_bruto=num(r["uv"], 4))
            p["max_con_nubosidad"][f"dia{r['dia']}"] = base
        else:
            base.update(uvi_despejado=num(r["uvi_desp"], 1), uvucs_bruto=num(r["uvucs"], 4))
            p["max_cielo_despejado"][f"dia{r['dia']}"] = base
    with open(ruta_json, "w", encoding="utf-8") as f:
        json.dump({fecha: puntos}, f, ensure_ascii=False, indent=2)
    print(f"Max por dia: {ruta_csv} ({len(registros)} filas) y {ruta_json}")


def inspeccionar(ruta):
    grbs = pygrib.open(ruta)
    for m in grbs:
        v = np.ma.filled(m.values.astype("float64"), np.nan)
        lats, lons = m.latlons()
        print(f"#{m.messagenumber}: name='{m.name}' shortName='{m.shortName}' "
              f"units='{m.units}' nivel={m.typeOfLevel}/{m.level} "
              f"forma={v.shape} min={np.nanmin(v):.3f} max={np.nanmax(v):.3f} "
              f"lat=[{lats.min():.2f},{lats.max():.2f}] lon=[{lons.min():.2f},{lons.max():.2f}] "
              f"valida={getattr(m, 'validDate', '?')}")
    grbs.close()


def a_uvi(v, factor):
    """Convierte el valor GRIB a indice UV (nunca negativo; conserva NaN)."""
    return v if np.isnan(v) else max(v * factor, 0.0)


def fmt(v, dec):
    return "" if v is None or np.isnan(v) else f"{v:.{dec}f}"


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--inspeccionar":
        return inspeccionar(sys.argv[2])

    ruta_cfg = sys.argv[1] if len(sys.argv) > 1 else CONFIG_POR_DEFECTO
    cfg = leer_config(ruta_cfg)
    fecha = fecha_desde_config(cfg, sys.argv[2] if len(sys.argv) > 2 else None)

    d, e, c = cfg["descarga"], cfg["extraccion"], cfg.get("csv", {})
    ciclo = d.get("ciclo", "t12z")
    h0, h1 = d.get("hora_inicio", 1), d.get("hora_fin", 120)
    carpeta = carpeta_destino(cfg, ruta_cfg, fecha)
    solo_sol = e.get("solo_con_sol", True)
    umbral = e.get("umbral_transmision", 0.1)      # en unidades de indice UV
    factor = e.get("factor_uvi", 40.0)             # valor GRIB -> indice UV
    salida = carpeta / c.get("archivo", "UV_{FECHA}.csv").replace("{FECHA}", fecha)
    sep = c.get("separador", ",")
    nombre = e.get("punto_nombre", "") if e.get("modo", "zona") == "punto" else ""

    inicio = datetime.strptime(fecha + ciclo[1:3], "%Y%m%d%H")  # corrida, UTC
    horas = list(range(h0, h1 + 1))

    # 1) Primer archivo disponible (lectura normal): define la grilla y los puntos a extraer
    t0 = time.perf_counter()
    rutas = [(prod, h, carpeta / f"{prod}.{ciclo}.grbf{h:02d}.grib2")
             for h in horas for prod in ("uv", "uvucs")]
    spec = lat_pts = lon_pts = forma = uv = uvucs = None
    pendientes = []
    for prod, h, ruta in rutas:
        if spec is None:
            r = leer_valores(ruta, con_grilla=True)
            if r is None:
                continue
            vals, lats, lons = r
            forma = vals.shape
            spec, lat_pts, lon_pts, desc = preparar(e, lats, lons)
            uv = np.full((len(horas), len(lat_pts)), np.nan)
            uvucs = np.full((len(horas), len(lat_pts)), np.nan)
            (uv if prod == "uv" else uvucs)[h - h0, :] = aplicar(spec, vals)
            print(f"Grilla {forma}; extraccion: {desc}")
        else:
            pendientes.append((prod, h, str(ruta), spec, forma))
    if spec is None:
        sys.exit(f"No se encontraron archivos GRIB2 en {carpeta}")

    # 2) Resto de archivos, en paralelo (cada proceso decodifica archivos distintos)
    n_proc = e.get("procesos", 0) or min(os.cpu_count() or 1, 8)
    if n_proc > 1 and len(pendientes) > 4:
        with ProcessPoolExecutor(max_workers=n_proc) as ex:
            resultados = ex.map(tarea, pendientes, chunksize=max(1, len(pendientes) // (n_proc * 4)))
            resultados = list(resultados)
    else:
        n_proc = 1
        resultados = [tarea(t) for t in pendientes]
    for prod, h, vec, err in resultados:
        if err:
            print(f"AVISO: {prod} f{h:02d}: {err}; se omite")
        elif vec is not None:
            (uv if prod == "uv" else uvucs)[h - h0, :] = vec
    print(f"Lectura de {len(rutas)} archivos: {time.perf_counter() - t0:.1f} s ({n_proc} procesos)")

    faltan = [h for i, h in enumerate(horas) if np.isnan(uv[i]).all() or np.isnan(uvucs[i]).all()]
    if faltan:
        print(f"AVISO: horas con datos incompletos: {faltan}")

    # max_uvucs: maximo por punto en cada bloque de 24 h de pronostico
    max_uvucs = np.full_like(uvucs, np.nan)
    for ini in range(0, len(horas), 24):
        bloque = uvucs[ini:ini + 24]
        if not np.isnan(bloque).all():
            with np.errstate(all="ignore"):
                max_uvucs[ini:ini + 24] = np.nanmax(bloque, axis=0)

    filas = 0
    with open(salida, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=sep, lineterminator="\n")
        w.writerow(["fecha_corrida", "ciclo", "hora_pronostico", "dia_pronostico",
                    "fecha_valida_utc", "punto", "latitud", "longitud", "uv_bruto", "uvucs_bruto",
                    "uvi_despejado", "uvi_nubosidad", "transmision", "max_uvucs"])
        for i, h in enumerate(horas):
            valida = (inicio + timedelta(hours=h)).strftime("%Y-%m-%d %H:%M")
            dia = (h - 1) // 24 + 1
            for j in range(uv.shape[1]):
                a, b = uv[i, j], uvucs[i, j]
                if np.isnan(a) and np.isnan(b):
                    continue
                if solo_sol and (np.nan_to_num(a) <= 0 and np.nan_to_num(b) <= 0):
                    continue
                trans = a / b if (not np.isnan(a) and not np.isnan(b) and b * factor > umbral) else np.nan
                w.writerow([fecha, ciclo, h, dia, valida, nombre,
                            fmt(lat_pts[j], 2), fmt(lon_pts[j], 2),
                            fmt(a, 4), fmt(b, 4), fmt(a_uvi(b, factor), 1), fmt(a_uvi(a, factor), 1),
                            fmt(trans, 3), fmt(a_uvi(max_uvucs[i, j], factor), 1)])
                filas += 1
    print(f"CSV generado: {salida} ({filas} filas)")

    reemplazar = lambda t: t.replace("{FECHA}", fecha)
    salida_max = carpeta / reemplazar(c.get("archivo_max_dia", "UV_{FECHA}_max_dia.csv"))
    salida_json = carpeta / reemplazar(c.get("archivo_max_dia_json", "UV_{FECHA}_max_dia.json"))
    registros = calcular_max_dia(horas, inicio, c.get("zona_horaria", "America/Punta_Arenas"),
                                 c.get("agrupar_por", "bloque_24h"), uv, uvucs, factor, umbral)
    escribir_max_dia(salida_max, salida_json, sep, fecha, nombre, registros, lat_pts, lon_pts)


if __name__ == "__main__":
    main()
