#!/usr/bin/env python3
"""Descarga de archivos UV (GRIB2) desde NOMADS/NCEP.

Uso:
    python Descargar_UV.py                              # usa _Configuracion_UV.toml
    python Descargar_UV.py otro.toml                    # usa otro archivo
    python Descargar_UV.py _Configuracion_UV.toml 20261104   # fuerza la fecha
    python Descargar_UV.py --fecha                      # solo muestra la fecha que se usaria

Con fecha = "AUTO": antes de `hora_cambio_utc` (18:30 UTC) se usa la fecha de ayer;
desde esa hora, la de hoy.

Reintentos: cada archivo que falla o llega corrupto se vuelve a pedir hasta `reintentos` veces
(10 por defecto), esperando antes de cada reintento base*k + la espera anterior, con base = `espera`
(10 s) y sin pasar de `espera_max` (60 s): 10, 30, 60, 60, 60, 60, 60, 60, 60 y 60 s (~9 min en total).

Solo usa la libreria estandar de Python 3.11+ (tomllib).
"""
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


def grib_valido(ruta):
    """Un GRIB completo empieza con 'GRIB' y termina con '7777'. Detecta archivos cortados o danados."""
    try:
        with open(ruta, "rb") as f:
            if f.read(4) != b"GRIB":
                return False
            f.seek(-4, 2)
            return f.read(4) == b"7777"
    except OSError:
        return False


def esperas_reintento(reintentos, base, tope=None):
    """Espera (segundos) antes de cada reintento: la de `k` es base*k mas la espera anterior,
    sin pasar de `tope` (si se indica). Con base 10, tope 60 y 10 reintentos:
    10, 30, 60, 60, 60, 60, 60, 60, 60, 60 s."""
    esperas, anterior = [], 0
    for k in range(1, reintentos + 1):
        anterior = base * k + anterior
        if tope:
            anterior = min(anterior, tope)
        esperas.append(anterior)
    return esperas


def descargar(url, destino):
    """UN intento de descarga. Valida el tamano y que sea un GRIB completo.
    Devuelve (nombre, estado) con estado 'ok', 'existente' o 'error: ...'."""
    if destino.exists():
        if destino.stat().st_size > 0 and grib_valido(destino):
            return destino.name, "existente"
        destino.unlink()                      # vacio, cortado o corrupto: se vuelve a bajar
    tmp = destino.with_suffix(destino.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            esperado = r.headers.get("Content-Length")
            with open(tmp, "wb") as f:
                while chunk := r.read(1 << 16):
                    f.write(chunk)
        recibido = tmp.stat().st_size
        if recibido == 0 or (esperado and recibido != int(esperado)):
            raise IOError(f"descarga incompleta ({recibido} de {esperado} bytes)")
        if not grib_valido(tmp):
            raise IOError("archivo corrupto (no es un GRIB completo)")
        tmp.replace(destino)
        return destino.name, "ok"
    except urllib.error.HTTPError as e:
        return destino.name, f"error: HTTP {e.code}"
    except Exception as e:  # red, timeout, descarga cortada, archivo corrupto...
        return destino.name, f"error: {type(e).__name__}: {e}"
    finally:
        if tmp.exists():
            tmp.unlink()


def ronda(tareas, hilos):
    """Ejecuta una ronda de descargas; devuelve (resumen, tareas con error)."""
    resultados, fallidas = {}, []
    with ThreadPoolExecutor(max_workers=hilos) as ex:
        futs = {ex.submit(descargar, u, p): (u, p) for u, p in tareas}
        for i, fut in enumerate(as_completed(futs), 1):
            nombre, estado = fut.result()
            clave = estado.split(" ")[0].rstrip(":")
            resultados[clave] = resultados.get(clave, 0) + 1
            if estado.startswith("error"):
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
    reintentos, espera = d.get("reintentos", 10), d.get("espera", 10)
    espera_max = d.get("espera_max", 60)             # tope de cada espera (s); 0 = sin tope
    hilos = d.get("hilos", 4)

    carpeta.mkdir(parents=True, exist_ok=True)
    url_dir = f"{base}/uvi.{fecha}"
    print(f"Fecha: {fecha} | Origen: {url_dir}/ | Destino: {carpeta}")

    tareas = []
    for prod in productos:
        for h in range(h0, h1 + 1):
            nombre = f"{prod}.{ciclo}.grbf{h:02d}.grib2"  # 01..99, luego 100..120
            tareas.append((f"{url_dir}/{nombre}", carpeta / nombre))

    resumen, fallidas = ronda(tareas, hilos)
    print("Resumen:", resumen)

    # Reintentos: solo los archivos que fallaron (o llegaron corruptos), con espera creciente
    esperas = esperas_reintento(reintentos, espera, espera_max or None)
    for k, pausa in enumerate(esperas, 1):
        if not fallidas:
            break
        print(f"\nReintento {k}/{reintentos}: {len(fallidas)} archivo(s) con error. "
              f"Esperando {pausa:g} s...", flush=True)
        time.sleep(pausa)
        _, fallidas = ronda(fallidas, max(1, min(hilos, 4)))

    faltan = [p.name for _, p in fallidas]
    if faltan:
        print(f"\nERROR: tras {reintentos} reintentos quedaron {len(faltan)} archivos sin descargar: {faltan}")
        sys.exit(1)
    print("\nDescarga completa sin errores.")


if __name__ == "__main__":
    main()
