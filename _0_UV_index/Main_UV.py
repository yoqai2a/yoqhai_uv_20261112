#!/usr/bin/env python3
"""Programa principal: descarga los GRIB2 y, si todo salio bien, genera los CSV/JSON.

Uso:
    python Main_UV.py                               # usa _Configuracion_UV.toml
    python Main_UV.py otro.toml                     # otro archivo de configuracion
    python Main_UV.py _Configuracion_UV.toml 20261104   # fuerza la fecha

Pasos:
    1) Descargar_UV.py     -> descarga los archivos (termina con error si falta alguno)
    2) Generar_CSV_UV.py   -> solo se ejecuta si el paso 1 termino sin errores

La fecha se resuelve una sola vez aqui y se entrega a ambos pasos, para que usen
exactamente la misma aunque la ejecucion cruce la hora de cambio (18:30 UTC).
"""
import subprocess
import sys
import time
from pathlib import Path

from _Descargar_UV import CONFIG_POR_DEFECTO, fecha_desde_config, leer_config

PASOS = ("_Descargar_UV.py", "_Generar_CSV_UV.py")


def main():
    ruta_cfg = sys.argv[1] if len(sys.argv) > 1 else CONFIG_POR_DEFECTO
    forzada = sys.argv[2] if len(sys.argv) > 2 else None
    fecha = fecha_desde_config(leer_config(ruta_cfg), forzada)
    carpeta = Path(__file__).resolve().parent

    print(f"=== Main_UV: fecha {fecha} | configuracion {ruta_cfg} ===", flush=True)
    for n, script in enumerate(PASOS, 1):
        print(f"\n--- Paso {n}/{len(PASOS)}: {script} ---", flush=True)
        t0 = time.time()
        rc = subprocess.run([sys.executable, str(carpeta / script), ruta_cfg, fecha]).returncode
        if rc != 0:
            print(f"\nERROR: {script} termino con codigo {rc}. "
                  f"{'No se ejecutan los pasos siguientes.' if n < len(PASOS) else ''}")
            sys.exit(rc)
        print(f"--- {script} OK ({time.time() - t0:.0f} s) ---", flush=True)

    print(f"\n=== Main_UV finalizado correctamente ({fecha}) ===")


if __name__ == "__main__":
    main()
