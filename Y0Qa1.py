#!/usr/bin/env python3
"""Iniciador del proyecto Y0Qa1: ejecuta el modulo de indice UV (_0_UV_index/Main_UV.py).

Estructura:
    Y0Qa1/
    ├── Y0Qa1.py                      <- este archivo
    ├── ___Data/___UV/___UV_YYYYMMDD/ <- GRIB2 descargados y CSV/JSON generados
    └── _0_UV_index/
        ├── Main_UV.py
        ├── Descargar_UV.py
        ├── Generar_CSV_UV.py
        └── _Configuracion_UV.toml

Uso (desde cualquier carpeta):
    python Y0Qa1.py                # fecha segun la hora UTC (AUTO, cambio 18:30 UTC)
    python Y0Qa1.py 20261104       # fecha fija
"""
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
CARPETA_UV = RAIZ / "_0_UV_index"


def main():
    cmd = [sys.executable, str(CARPETA_UV / "Main_UV.py"),
           str(CARPETA_UV / "__Configuracion_UV.toml")]
    if len(sys.argv) > 1:
        cmd.append(sys.argv[1])  # fecha YYYYMMDD opcional
    print(f"Y0Qa1: iniciando {CARPETA_UV.name}/Main_UV.py", flush=True)
    sys.exit(subprocess.run(cmd, cwd=RAIZ).returncode)


if __name__ == "__main__":
    main()
