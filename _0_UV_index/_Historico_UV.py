#!/usr/bin/env python3
"""Archivo maestro historico del indice UV (Excel).

Una HOJA por estacion (SCHR, SCNT, SCCI, ...) y una FILA por fecha de ciclo, con el maximo
diario del indice UV de los dias 0 a 5:

    fecha ciclo despejado | dia 0 ... dia 5 | | fecha ciclo con nubosidad | dia 0 ... dia 5

- "dia 0" es el dia de la fecha de ciclo, "dia 1" el siguiente, etc. Cada dia es un bloque de 24 h
  de pronostico; como el pronostico llega a 120 h, hoy se llenan los dias 0 a 4 (dia 5 queda libre).
- Cada ejecucion escribe (o sobreescribe) la fila de su fecha de ciclo; las demas filas no se tocan.
- El calendario se crea de una vez (por defecto 01-ene-2026 a 31-dic-2027) y, si llega una fecha
  posterior, se extiende solo hasta el 31 de diciembre de ese anio.
- Los colores de las celdas siguen las categorias OMS del indice UV (formato condicional).

Requiere openpyxl (pip install openpyxl).
"""
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

DIAS = 6                               # dia 0 .. dia 5
COL_DESP = 1                           # A: fecha ciclo despejado; B..G: dia 0..5
COL_SEP = 1 + DIAS + 1                 # H: separador
COL_NUB = COL_SEP + 1                  # I: fecha ciclo con nubosidad; J..O: dia 0..5
HOJA_LEYENDA = "Leyenda"
FUENTE = "Arial"
FORMATO_FECHA = "dd mmmm yyyy"         # Excel lo muestra en el idioma del sistema ("01 enero 2026")

# Categorias OMS: (desde, hasta_sin_incluir, color de fondo)
CATEGORIAS = [(0, 3, "A5D6A7"), (3, 6, "FFF59D"), (6, 8, "FFCC80"),
              (8, 11, "EF9A9A"), (11, 100, "CE93D8")]


def _borde():
    lado = Side(style="thin", color="D0D0D0")
    return Border(left=lado, right=lado, top=lado, bottom=lado)


def _escribir_fechas(ws, desde, hasta, fila_ini):
    """Escribe las fechas desde..hasta (ambas columnas de fecha) a partir de fila_ini."""
    borde = _borde()
    d, fila = desde, fila_ini
    while d <= hasta:
        for col in (COL_DESP, COL_NUB):
            c = ws.cell(fila, col, d)
            c.number_format = FORMATO_FECHA
            c.font = Font(name=FUENTE, size=10)
            c.alignment = Alignment(horizontal="left")
            c.border = borde
        for col in list(range(COL_DESP + 1, COL_DESP + 1 + DIAS)) + list(range(COL_NUB + 1, COL_NUB + 1 + DIAS)):
            c = ws.cell(fila, col)
            c.number_format = "0.0"
            c.font = Font(name=FUENTE, size=10)
            c.alignment = Alignment(horizontal="center")
            c.border = borde
        ws.cell(fila, COL_SEP).fill = PatternFill("solid", start_color="404040", end_color="404040")
        d += timedelta(days=1)
        fila += 1


def _formato_condicional(ws, ultima_fila):
    """Colorea cada valor numerico segun su categoria UV (las celdas vacias no se pintan)."""
    ws.conditional_formatting._cf_rules.clear()
    for c0, c1 in ((COL_DESP + 1, COL_DESP + DIAS), (COL_NUB + 1, COL_NUB + DIAS)):
        rango = f"{get_column_letter(c0)}2:{get_column_letter(c1)}{ultima_fila}"
        ref = f"{get_column_letter(c0)}2"
        for desde, hasta, color in CATEGORIAS:
            fill = PatternFill("solid", start_color=color, end_color=color)
            ws.conditional_formatting.add(
                rango, FormulaRule(formula=[f"AND(ISNUMBER({ref}),{ref}>={desde},{ref}<{hasta})"], fill=fill))


def _crear_hoja(wb, nombre, desde, hasta):
    ws = wb.create_sheet(nombre)
    cab = Font(name=FUENTE, size=10, bold=True, color="FFFFFF")
    for col0, titulo, color in ((COL_DESP, "fecha ciclo despejado", "1B8F78"),
                                (COL_NUB, "fecha ciclo con nubosidad", "5F6770")):
        relleno = PatternFill("solid", start_color=color, end_color=color)
        textos = [titulo] + [f"día {i}" for i in range(DIAS)]
        for k, texto in enumerate(textos):
            c = ws.cell(1, col0 + k, texto)
            c.font, c.fill, c.border = cab, relleno, _borde()
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.cell(1, COL_SEP).fill = PatternFill("solid", start_color="404040", end_color="404040")
    for col in (COL_DESP, COL_NUB):
        ws.column_dimensions[get_column_letter(col)].width = 24
    for col in list(range(COL_DESP + 1, COL_DESP + 1 + DIAS)) + list(range(COL_NUB + 1, COL_NUB + 1 + DIAS)):
        ws.column_dimensions[get_column_letter(col)].width = 8
    ws.column_dimensions[get_column_letter(COL_SEP)].width = 2
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "B2"
    _escribir_fechas(ws, desde, hasta, 2)
    _formato_condicional(ws, 1 + (hasta - desde).days + 1)
    return ws


def _hoja_leyenda(wb):
    if HOJA_LEYENDA in wb.sheetnames:
        del wb[HOJA_LEYENDA]
    ws = wb.create_sheet(HOJA_LEYENDA)
    filas = [
        ("Archivo maestro historico - indice UV (pronostico NOAA/NCEP)", True),
        ("", False),
        ("Hojas: una por estacion. Filas: una por fecha de ciclo (la corrida de las 12 UTC de ese dia).", False),
        ("Valores: indice UV MAXIMO del dia = valor del archivo GRIB x 40 (1 UVI = 25 mW/m2), con 1 decimal.", False),
        ("Izquierda: maximo con cielo despejado (archivos uvucs). Derecha: maximo con nubosidad (archivos uv).", False),
        ("dia 0 = el dia de la fecha de ciclo; dia 1 = el siguiente; y asi hasta dia 5.", False),
        ("Cada dia es un bloque de 24 h de pronostico (f01-f24, f25-f48, ...). Como el pronostico llega a 120 h,", False),
        ("se llenan los dias 0 a 4; la columna dia 5 queda libre.", False),
        ("Cada ejecucion escribe solo la fila de su fecha de ciclo (si se repite, la sobreescribe).", False),
        ("Colores: verde bajo (0-2), amarillo moderado (3-5), naranja alto (6-7), rojo muy alto (8-10), violeta extremo (11+).", False),
        ("Fuente: NOAA/NCEP, https://nomads.ncep.noaa.gov/pub/data/nccf/com/uvi/prod/", False),
    ]
    for i, (texto, negrita) in enumerate(filas, 1):
        ws.cell(i, 1, texto).font = Font(name=FUENTE, size=11 if negrita else 10, bold=negrita)
    ws.column_dimensions["A"].width = 120
    return ws


def datos_historico(registros, nombres):
    """Convierte los registros de maximos diarios de _Generar_CSV_UV.py a
    {estacion: {"despejado": [dia0..dia5], "nubosidad": [dia0..dia5]}} (None si falta)."""
    datos = {}
    for r in registros:
        nombre = nombres[r["j"]]
        k = r["dia"] - 1                       # dia 1 del generador = "dia 0" del historico
        if not nombre or not 0 <= k < DIAS:
            continue
        d = datos.setdefault(nombre, {"despejado": [None] * DIAS, "nubosidad": [None] * DIAS})
        valor = r["uvi_desp"] if r["tipo"] == "cielo_despejado" else r["uvi_nub"]
        clave = "despejado" if r["tipo"] == "cielo_despejado" else "nubosidad"
        d[clave][k] = None if valor != valor else round(float(valor), 1)   # NaN -> None
    return datos


def _fila_de(ws, fecha, desde):
    """Fila de `fecha`; si no existe, extiende el calendario hasta el 31-dic de ese anio."""
    primera = ws.cell(2, COL_DESP).value
    primera = primera.date() if isinstance(primera, datetime) else (primera or desde)
    if fecha < primera:
        raise ValueError(f"{fecha} es anterior a la primera fecha del historico ({primera})")
    fila = 2 + (fecha - primera).days
    ultima_fecha = primera + timedelta(days=ws.max_row - 2)
    if fecha > ultima_fecha:
        hasta = date(fecha.year, 12, 31)
        _escribir_fechas(ws, ultima_fecha + timedelta(days=1), hasta, ws.max_row + 1)
        _formato_condicional(ws, ws.max_row)
    return fila


def actualizar_historico(ruta, fecha_ciclo, datos, desde, hasta):
    """Crea el archivo si no existe y escribe la fila de `fecha_ciclo` en la hoja de cada estacion."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if ruta.exists():
        wb = load_workbook(ruta)
    else:
        wb = Workbook()
        wb.remove(wb.active)
    escritas = []
    for nombre, valores in datos.items():
        ws = wb[nombre] if nombre in wb.sheetnames else _crear_hoja(wb, nombre, desde, hasta)
        fila = _fila_de(ws, fecha_ciclo, desde)
        for i in range(DIAS):
            ws.cell(fila, COL_DESP + 1 + i).value = valores["despejado"][i]
            ws.cell(fila, COL_NUB + 1 + i).value = valores["nubosidad"][i]
        escritas.append(nombre)
    _hoja_leyenda(wb)                          # la leyenda siempre queda como ultima hoja
    wb.save(ruta)
    return f"fila {fecha_ciclo.isoformat()} actualizada en {len(escritas)} hoja(s): {', '.join(escritas)}"
