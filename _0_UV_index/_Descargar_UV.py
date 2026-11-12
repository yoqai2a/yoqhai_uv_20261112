#!/usr/bin/env python3
"""Descarga de archivos UV (GRIB2) desde NOMADS/NCEP.

Uso:
    python Descargar_UV.py                              # usa _Configuracion_UV.toml
    python Descargar_UV.py otro.toml                    # usa otro archivo
    python Descargar_UV.py _Configuracion_UV.toml 20261104   # fuerza la fecha
    python Descargar_UV.py --fecha                      # solo muestra la fecha que se usaria

Con fecha = "AUTO": antes de `hora_cambio_utc` (18:30 UTC) se usa la fecha de ayer;
desde esa hora, la de hoy.

Solo usa la libreria estandar de Python 3.11+ (tomllib).
"""
import random
import sys
import time
import tomllib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path


def leer_config(ruta):
    try:
        with open(ruta, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        sys.exit(f"No se pudo leer la configuracion {ruta}: {e}")


def resolver_fecha(valor, hora_cambio="18:30", ahora=None):
    """Devuelve la fecha YYYYMMDD a descargar.

    "AUTO": antes de `hora_cambio` (UTC) usa la fecha de AYER; desde esa hora, la de HOY.
    Cualquier otra cosa debe ser una fecha YYYYMMDD explicita.
    """
    valor = str(valor).strip().upper()
    if valor == "AUTO":
        ahora = ahora or datetime.now(timezone.utc)
        hh, mm = (int(x) for x in hora_cambio.split(":"))
        corte = ahora.replace(hour=hh, minute=mm, second=0, microsecond=0)
        dia = ahora if ahora >= corte else ahora - timedelta(days=1)
        return dia.strftime("%Y%m%d")
    datetime.strptime(valor, "%Y%m%d")  # valida formato
    return valor


ERRORES_REINTENTABLES = {403, 408, 429, 500, 502, 503, 504}


def descargar(url, destino, reintentos, espera):
    """Descarga con reintentos, espera exponencial y validacion de tamano."""
    if destino.exists() and destino.stat().st_size > 0:
        return destino.name, "existente"
    tmp = destino.with_suffix(destino.suffix + ".part")
    err = ""
    for intento in range(1, reintentos + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                esperado = r.headers.get("Content-Length")
                with open(tmp, "wb") as f:
                    while chunk := r.read(1 << 16):
                        f.write(chunk)
            recibido = tmp.stat().st_size
            if recibido == 0 or (esperado and recibido != int(esperado)):
                raise IOError(f"descarga incompleta ({recibido} de {esperado} bytes)")
            tmp.replace(destino)
            return destino.name, "ok"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return destino.name, "no disponible (404)"
            err = f"HTTP {e.code}"
            if e.code not in ERRORES_REINTENTABLES:
                break
        except Exception as e:  # red, timeout, descarga cortada, etc.
            err = f"{type(e).__name__}: {e}"
        finally:
            if tmp.exists():
                tmp.unlink()
        if intento < reintentos:
            time.sleep(espera * 2 ** (intento - 1) + random.uniform(0, 1))
    return destino.name, f"error: {err}"


def ronda(tareas, hilos, reintentos, espera):
    """Ejecuta una ronda de descargas; devuelve (resultados, tareas con error)."""
    resultados, fallidas = {}, []
    with ThreadPoolExecutor(max_workers=hilos) as ex:
        futs = {ex.submit(descargar, u, p, reintentos, espera): (u, p) for u, p in tareas}
        for i, fut in enumerate(as_completed(futs), 1):
            nombre, estado = fut.result()
            clave = estado.split(" ")[0].rstrip(":")
            resultados[clave] = resultados.get(clave, 0) + 1
            if estado.startswith(("error", "no disponible")):
                fallidas.append(futs[fut])
            print(f"[{i}/{len(tareas)}] {nombre}: {estado}", flush=True)
    return resultados, fallidas


def fecha_desde_config(cfg, forzada=None):
    f = cfg.get("fecha", {})
    return resolver_fecha(forzada or f.get("fecha", "AUTO"), f.get("hora_cambio_utc", "18:30"))


CONFIG_POR_DEFECTO = str(Path(__file__).resolve().parent / "__Configuracion_UV.toml")


def carpeta_destino(cfg, ruta_cfg, fecha):
    """Carpeta de descarga. Si `destino` es relativo, se mide desde la carpeta del
    archivo de configuracion (no desde donde se ejecute el programa)."""
    destino = cfg["descarga"].get("destino", "../___Data/___UV/___UV_{FECHA}")
    return (Path(ruta_cfg).resolve().parent / destino.replace("{FECHA}", fecha)).resolve()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--fecha":   # solo imprime la fecha resuelta
        ruta = sys.argv[2] if len(sys.argv) > 2 else CONFIG_POR_DEFECTO
        print(fecha_desde_config(leer_config(ruta)))
        return

    ruta_cfg = sys.argv[1] if len(sys.argv) > 1 else CONFIG_POR_DEFECTO
    cfg = leer_config(ruta_cfg)

    fecha = fecha_desde_config(cfg, sys.argv[2] if len(sys.argv) > 2 else None)
    d = cfg["descarga"]
    ciclo = d.get("ciclo", "t12z")
    productos = d.get("productos", ["uv", "uvucs"])
    h0, h1 = d.get("hora_inicio", 1), d.get("hora_fin", 120)
    base = d["url_base"].rstrip("/")
    carpeta = carpeta_destino(cfg, ruta_cfg, fecha)
    reintentos, espera = d.get("reintentos", 3), d.get("espera", 5)
    hilos = d.get("hilos", 4)
    rondas_extra = d.get("rondas_extra", 3)

    carpeta.mkdir(parents=True, exist_ok=True)
    url_dir = f"{base}/uvi.{fecha}"
    print(f"Fecha: {fecha} | Origen: {url_dir}/ | Destino: {carpeta}")

    tareas = []
    for prod in productos:
        for h in range(h0, h1 + 1):
            nombre = f"{prod}.{ciclo}.grbf{h:02d}.grib2"  # 01..99, luego 100..120
            tareas.append((f"{url_dir}/{nombre}", carpeta / nombre))

    resumen, fallidas = ronda(tareas, hilos, reintentos, espera)
    print("Resumen:", resumen)

    # Rondas extra: solo los archivos que fallaron, con menos hilos y mas pausa
    for n in range(1, rondas_extra + 1):
        if not fallidas:
            break
        pausa = 30 * n
        print(f"\nRonda extra {n}/{rondas_extra}: {len(fallidas)} archivos con error. "
              f"Esperando {pausa} s...", flush=True)
        time.sleep(pausa)
        _, fallidas = ronda(fallidas, max(1, min(hilos, 4)), reintentos, espera)

    faltan = [p.name for _, p in fallidas]
    if faltan:
        print(f"\nERROR: quedaron {len(faltan)} archivos sin descargar: {faltan}")
        sys.exit(1)
    print("\nDescarga completa sin errores.")


if __name__ == "__main__":
    main()
