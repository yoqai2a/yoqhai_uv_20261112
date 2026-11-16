#!/usr/bin/env python3
"""Limpieza de carpetas diarias: conserva solo los ultimos N dias (15 por defecto).

Uso:
    python _Limpiar_UV.py                              # usa __Configuracion_UV.toml
    python _Limpiar_UV.py otro.toml 20261104           # otra configuracion / fecha de referencia

- Solo toca carpetas llamadas ___UV_YYYYMMDD dentro de ___Data/___UV/ (donde se guarda cada dia).
- La referencia es la fecha mas reciente entre la del proceso y las carpetas existentes; se
  conservan esa fecha y las N-1 anteriores. Todo lo anterior se borra.
- El archivo maestro Historico_UV.xlsx (y cualquier otro archivo) no se toca.
- `dias_a_conservar = 0` en [limpieza] desactiva el borrado.
"""
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from _Descargar_UV import CONFIG_POR_DEFECTO, carpeta_destino, fecha_desde_config, leer_config

PATRON = re.compile(r"^___UV_(\d{8})$")


def carpetas_diarias(raiz):
    """[(fecha, ruta)] de las carpetas ___UV_YYYYMMDD de `raiz`, ordenadas por fecha."""
    res = []
    for d in raiz.iterdir():
        m = PATRON.match(d.name)
        if d.is_dir() and m:
            try:
                res.append((datetime.strptime(m.group(1), "%Y%m%d").date(), d))
            except ValueError:
                pass
    return sorted(res)


def limpiar(raiz, fecha, dias):
    """Borra las carpetas con mas de `dias` dias respecto de la referencia. Devuelve las borradas."""
    if dias <= 0 or not raiz.exists():
        return []
    carpetas = carpetas_diarias(raiz)
    referencia = max([fecha] + [f for f, _ in carpetas])
    limite = referencia - timedelta(days=dias - 1)
    borradas = []
    for f, ruta in carpetas:
        if f < limite:
            shutil.rmtree(ruta)
            borradas.append(ruta.name)
    return borradas


def main():
    ruta_cfg = sys.argv[1] if len(sys.argv) > 1 else CONFIG_POR_DEFECTO
    cfg = leer_config(ruta_cfg)
    fecha_txt = fecha_desde_config(cfg, sys.argv[2] if len(sys.argv) > 2 else None)
    dias = cfg.get("limpieza", {}).get("dias_a_conservar", 15)
    raiz = carpeta_destino(cfg, ruta_cfg, fecha_txt).parent      # ___Data/___UV
    borradas = limpiar(raiz, datetime.strptime(fecha_txt, "%Y%m%d").date(), dias)
    if dias <= 0:
        print("Limpieza desactivada (dias_a_conservar = 0).")
    elif borradas:
        print(f"Limpieza: se borraron {len(borradas)} carpeta(s) con mas de {dias} dias: {', '.join(borradas)}")
    else:
        print(f"Limpieza: nada que borrar (se conservan {dias} dias).")


if __name__ == "__main__":
    main()
