#!/usr/bin/env python3
"""Visor_UV: visor Streamlit de los datos de indice UV generados por _Generar_CSV_UV.py.

Ejecutar (desde cualquier carpeta):
    streamlit run Y0Qa1/_0_UV_index/Visor_UV.py

Requiere: pip install streamlit pandas   (altair viene con streamlit)

Lee, desde Y0Qa1/___Data/___UV/___UV_YYYYMMDD/:
    UV_YYYYMMDD.csv            serie horaria (120 horas de pronostico)
    UV_YYYYMMDD_max_dia.csv    maximos por dia (con nubosidad y cielo despejado)
"""
import hmac
import json
import time
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Visor UV", page_icon="☀️", layout="wide")

RAIZ_DATOS = Path(__file__).resolve().parent.parent / "___Data" / "___UV"
ZONA_DEFECTO = "America/Punta_Arenas"
NOMBRE_TAB_DL = "YoQa1 °.° uv °.° dl"          # nombre de la ultima pestana (ejecutor manual)
API_GITHUB = "https://api.github.com"
ESPERA_ENTRE_EJECUCIONES = 60                  # segundos minimos entre dos disparos desde el visor

# Categorias OMS del indice UV: (nombre, desde, hasta, color)
CATEGORIAS = [
    ("Bajo (0-2)", 0, 3, "#4caf50"),
    ("Moderado (3-5)", 3, 6, "#f5c400"),
    ("Alto (6-7)", 6, 8, "#fb8c00"),
    ("Muy alto (8-10)", 8, 11, "#e53935"),
    ("Extremo (11+)", 11, 20, "#8e24aa"),
]
# Barras (gris claro) y lineas (gris un poco mas oscuro, para que se vea sobre el fondo)
COLOR_BARRA = {"Cielo despejado": "#22c5a5", "Con nubosidad": "#c9ccd1"}   # verde/cyan, gris claro
COLOR_LINEA = {"Cielo despejado": "#22c5a5", "Con nubosidad": "#7f8790"}   # verde/cyan, gris


def categoria(v):
    if v is None or pd.isna(v):
        return "-"
    for nombre, desde, hasta, _ in CATEGORIAS:
        if desde <= v < hasta:
            return nombre.split(" (")[0]
    return "Extremo"


def color_categoria(v):
    if v is None or pd.isna(v):
        return "gray"
    for _, desde, hasta, color in CATEGORIAS:
        if desde <= v < hasta:
            return color
    return CATEGORIAS[-1][3]


def tarjeta_uvi(col, titulo, uvi, fecha_hora):
    """Valor en negrita y con el color de su categoria UV."""
    color = color_categoria(uvi)
    col.markdown(
        f"<div style='line-height:1.25'>"
        f"<div style='font-size:0.95rem;opacity:0.75'>{titulo}</div>"
        f"<div style='color:{color};font-weight:800'>"
        f"<span style='font-size:2.8rem'>{uvi:.1f}</span> "
        f"<span style='font-size:1.15rem'>{categoria(uvi)}</span></div>"
        f"<div style='font-size:0.9rem;opacity:0.75'>{fecha_hora}</div></div>",
        unsafe_allow_html=True)


def nubosidad(atenuacion):
    """(icono, texto) segun cuanto atenua la nubosidad el UV del dia."""
    if atenuacion < 0.20:
        return "🌤️", "escasa nubosidad"
    if atenuacion < 0.50:
        return "⛅", "nubosidad parcial"
    return "☁️", "nublado"


def mostrar_grafico(chart):
    """Compatible con distintas versiones de Streamlit."""
    try:
        st.altair_chart(chart, width="stretch")
    except TypeError:
        st.altair_chart(chart, use_container_width=True)


def boton(col, etiqueta, **kw):
    """Boton de ancho completo, compatible con distintas versiones de Streamlit."""
    try:
        return col.button(etiqueta, width="stretch", **kw)
    except TypeError:
        return col.button(etiqueta, use_container_width=True, **kw)


def mostrar_tabla(df):
    try:
        st.dataframe(df, width="stretch", hide_index=True)
    except TypeError:
        st.dataframe(df, use_container_width=True, hide_index=True)


MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def fecha_corta(d):
    return f"{d.day:02d}-{MESES[d.month - 1]}"


def alternar_serie(clave, otra):
    """Activa/desactiva una serie. Nunca quedan las dos apagadas: si se apaga la ultima
    serie activa, se enciende la otra."""
    if st.session_state[clave]:
        st.session_state[clave] = False
        if not st.session_state[otra]:
            st.session_state[otra] = True
    else:
        st.session_state[clave] = True


def rgba(hex_color, alfa):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alfa})"


def pintar(v):
    if pd.isna(v):
        return "color: gray"
    return f"background-color: {rgba(color_categoria(v), 0.40)}; font-weight: 700"


def etiqueta_ciclo(f, f_actual):
    dias = (pd.to_datetime(f_actual, format="%Y%m%d") - pd.to_datetime(f, format="%Y%m%d")).days
    nombre = {0: "Actual", 1: "Ayer", 2: "Anteayer"}.get(dias, f"Hace {dias} días")
    return f"{nombre} ({f[:4]}-{f[4:6]}-{f[6:]})"


def maximos_por_fecha(raiz, f, lat, lon):
    """Maximos diarios de un ciclo, indexados por FECHA calendario (inicio del bloque de 24 h).
    Devuelve (serie_despejado, serie_nubosidad) o None si el ciclo no tiene archivo de maximos."""
    ruta = raiz / f"___UV_{f}" / f"UV_{f}_max_dia.csv"
    if not ruta.exists():
        return None
    df = leer_csv(str(ruta), ruta.stat().st_mtime)
    df = df[(df["latitud"] == lat) & (df["longitud"] == lon)]
    inicio = pd.to_datetime(f, format="%Y%m%d")
    df = df.assign(fecha=inicio + pd.to_timedelta(df["dia"] - 1, unit="D"))
    desp = df[df["tipo"] == "maximo_cielo_despejado"].set_index("fecha")["uvi_despejado"]
    nub = df[df["tipo"] == "maximo_con_nubosidad"].set_index("fecha")["uvi_nubosidad"]
    return desp, nub


# ----------------------------------------------------------------------------- ejecutor GitHub
def secretos_github():
    """Datos de [github] en .streamlit/secrets.toml (o en los Secrets de Streamlit Cloud)."""
    try:
        return dict(st.secrets.get("github", {}))
    except Exception:          # no hay archivo de secretos
        return {}


def gh(metodo, ruta, token, cuerpo=None):
    """Llamada a la API de GitHub. Devuelve (codigo_http, json). Codigo 0 = error de red."""
    req = urllib.request.Request(
        API_GITHUB + ruta, method=metodo,
        data=json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None,
        headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Y0Qa1-visor-uv",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            datos = json.loads(e.read().decode("utf-8"))
        except Exception:
            datos = {}
        return e.code, datos
    except Exception as e:
        return 0, {"message": f"{type(e).__name__}: {e}"}


def explicar_error(codigo, datos):
    detalle = datos.get("message", "")
    if codigo == 0:
        return f"No se pudo conectar con GitHub ({detalle})."
    if codigo == 401:
        return "Token inválido o vencido."
    if codigo == 403:
        return ("Sin permiso o límite de uso alcanzado. El token necesita 'Actions: Read and write' "
                f"(token fine-grained) o el scope 'workflow' (token clásico). GitHub dice: {detalle}")
    if codigo == 404:
        return ("No se encontró el repositorio o el workflow, o el token no tiene acceso a ese "
                "repositorio. Revisa 'usuario/repositorio' y el nombre del archivo del workflow.")
    if codigo == 422:
        return ("GitHub rechazó la solicitud (¿la rama existe? ¿el workflow tiene "
                f"'workflow_dispatch'?). GitHub dice: {detalle}")
    return f"Error {codigo}: {detalle}"


def fecha_automatica():
    """Fecha que usaria el modo AUTO (misma regla que _Descargar_UV.py) y la hora de cambio UTC."""
    hora = "18:30"
    try:
        ruta = Path(__file__).resolve().parent.parent / "_0_UV_index" / "__Configuracion_UV.toml"
        with open(ruta, "rb") as f:
            hora = tomllib.load(f)["fecha"].get("hora_cambio_utc", hora)
    except Exception:
        pass
    ahora = datetime.now(timezone.utc)
    hh, mm = (int(x) for x in hora.split(":"))
    corte = ahora.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return (ahora if ahora >= corte else ahora - timedelta(days=1)).strftime("%Y%m%d"), hora


ESTADOS = {"queued": "⏳ en cola", "in_progress": "🔄 en curso", "waiting": "⏳ esperando",
           "pending": "⏳ pendiente"}
RESULTADOS = {"success": "✅ exitosa", "failure": "❌ fallida", "cancelled": "⚪ cancelada",
              "skipped": "⚪ omitida", "timed_out": "❌ tiempo agotado"}
EVENTOS = {"schedule": "programada", "workflow_dispatch": "manual", "push": "push"}


def tabla_ejecuciones(runs, zona_txt):
    filas = []
    for r in runs:
        ini = pd.to_datetime(r.get("run_started_at") or r.get("created_at"), utc=True)
        fin = pd.to_datetime(r.get("updated_at"), utc=True)
        hecho = r.get("status") == "completed"
        filas.append({
            "N°": r.get("run_number"),
            "Origen": EVENTOS.get(r.get("event"), r.get("event")),
            "Estado": (RESULTADOS.get(r.get("conclusion"), r.get("conclusion")) if hecho
                       else ESTADOS.get(r.get("status"), r.get("status"))),
            "Inicio": (ini.tz_convert(zona_txt) if zona_txt else ini).strftime("%Y-%m-%d %H:%M"),
            "Duración": f"{int((fin - ini).total_seconds() // 60)} min {int((fin - ini).total_seconds() % 60)} s"
                        if hecho else "—",
            "GitHub": r.get("html_url"),
        })
    return pd.DataFrame(filas)


# ----------------------------------------------------------------------------- datos
def listar_fechas(raiz):
    fechas = []
    for d in sorted(raiz.glob("___UV_*"), reverse=True):
        f = d.name.replace("___UV_", "")
        if (d / f"UV_{f}.csv").exists():
            fechas.append(f)
    return fechas


@st.cache_data(show_spinner="Leyendo CSV...")
def leer_csv(ruta, mtime):
    """mtime solo sirve para invalidar la cache cuando el archivo cambia."""
    df = pd.read_csv(ruta, dtype={"fecha_corrida": str, "ciclo": str, "punto": str})
    df["punto"] = df["punto"].fillna("")
    return df


def etiqueta_punto(r):
    nombre = r["punto"] or "Punto"
    return f"{nombre} ({r['latitud']:.2f}, {r['longitud']:.2f})"


def a_hora(serie_utc, zona):
    """Texto UTC -> datetime sin zona, en hora local (zona) o UTC (zona=None)."""
    t = pd.to_datetime(serie_utc, utc=True)
    if zona is not None:
        t = t.dt.tz_convert(zona)
    return t.dt.tz_localize(None)


def eje_uvi(ymax):
    """Eje Y con una linea horizontal por cada unidad de indice UV."""
    # tickCount/tickMinStep hacen falta: sin ellos Vega dibuja solo una marca cada 2
    return alt.Axis(values=list(range(0, int(ymax) + 1)), tickCount=int(ymax) + 1,
                    tickMinStep=1, grid=True, title="Índice UV")


def bandas(ymax, eje=None):
    b = pd.DataFrame([(n, d, min(h, ymax), c) for n, d, h, c in CATEGORIAS if d < ymax],
                     columns=["categoria", "y0", "y1", "color"])
    return (alt.Chart(b).mark_rect(opacity=0.13)
            .encode(y=alt.Y("y0:Q", scale=alt.Scale(domain=[0, ymax]),
                            axis=eje or alt.Axis(title="Índice UV")),
                    y2="y1:Q",
                    color=alt.Color("categoria:N",
                                    scale=alt.Scale(domain=list(b["categoria"]),
                                                    range=list(b["color"])),
                                    legend=alt.Legend(title="Categoria UV"))))


# ----------------------------------------------------------------------------- barra lateral
with st.sidebar:
    st.header("☀️ Visor UV")
    raiz = Path(st.text_input("Carpeta de datos", str(RAIZ_DATOS)))
    if st.button("Recargar datos"):
        st.cache_data.clear()
        st.rerun()

    fechas = listar_fechas(raiz)
    if not fechas:
        st.error(f"No hay datos en:\n{raiz}\n\nEjecuta primero Y0Qa1.py.")
        st.stop()
    fecha = st.selectbox("Fecha de corrida", fechas,
                         format_func=lambda f: f"{f[:4]}-{f[4:6]}-{f[6:]}")

    modo_hora = st.radio("Mostrar horas en", ["Hora local", "UTC"], horizontal=True)
    zona_txt = ZONA_DEFECTO
    if modo_hora == "Hora local":
        zona_txt = st.text_input("Zona horaria", ZONA_DEFECTO)
        try:
            ZoneInfo(zona_txt)
        except Exception:
            st.warning("Zona horaria no valida; se usa UTC.")
            modo_hora, zona_txt = "UTC", None
    zona = zona_txt if modo_hora == "Hora local" else None
    etiqueta_hora = f"Hora local ({zona})" if zona else "Hora UTC"

carpeta = raiz / f"___UV_{fecha}"
ruta_h = carpeta / f"UV_{fecha}.csv"
ruta_m = carpeta / f"UV_{fecha}_max_dia.csv"

horario = leer_csv(str(ruta_h), ruta_h.stat().st_mtime)
maximos = leer_csv(str(ruta_m), ruta_m.stat().st_mtime) if ruta_m.exists() else None

# ----------------------------------------------------------------------------- punto
puntos = (horario[["punto", "latitud", "longitud"]].drop_duplicates()
          .sort_values(["latitud", "longitud"], ascending=[False, True]).reset_index(drop=True))
puntos["etiqueta"] = puntos.apply(etiqueta_punto, axis=1)
with st.sidebar:
    if len(puntos) > 1:
        sel = st.selectbox(f"Punto ({len(puntos)} disponibles)", puntos["etiqueta"])
    else:
        sel = puntos["etiqueta"].iloc[0]
        st.caption(f"Punto: {sel}")
p = puntos[puntos["etiqueta"] == sel].iloc[0]

def de_este_punto(df):
    return df[(df["latitud"] == p["latitud"]) & (df["longitud"] == p["longitud"])].copy()

h = de_este_punto(horario)
h["hora"] = a_hora(h["fecha_valida_utc"], zona)
h["fecha"] = h["hora"].dt.strftime("%Y-%m-%d")
ciclo = h["ciclo"].iloc[0]

st.title("Índice UV – pronóstico NOAA/NCEP")
st.caption(f"Corrida **{fecha[:4]}-{fecha[4:6]}-{fecha[6:]} {ciclo}** · **{sel}** · "
           f"{len(h)} horas de pronóstico · {etiqueta_hora}")

tab_max, tab_serie, tab_comp, tab_dl = st.tabs(
    ["📈 Máximos por día", "🕐 Serie horaria", "🔀 Comparación", NOMBRE_TAB_DL])

# ----------------------------------------------------------------------------- maximos
with tab_max:
    if maximos is None:
        st.info("No existe el archivo de máximos por día para esta fecha.")
    else:
        m = de_este_punto(maximos)
        m["hora"] = a_hora(m["hora_utc"], zona)
        m["fecha_hora"] = m["hora"].dt.strftime("%Y-%m-%d %H:%M")
        m["tipo_txt"] = m["tipo"].map({"maximo_con_nubosidad": "Con nubosidad",
                                       "maximo_cielo_despejado": "Cielo despejado"})
        m["uvi"] = m.apply(lambda r: r["uvi_nubosidad"] if r["tipo"] == "maximo_con_nubosidad"
                           else r["uvi_despejado"], axis=1)

        dias = [int(x) for x in sorted(m["dia"].unique())]
        # Etiqueta de cada dia = fecha (en la zona mostrada) en que ocurre su maximo de cielo despejado
        etiqueta_dia = {}
        for d_, g in m.groupby("dia"):
            fila = g[g["tipo"] == "maximo_cielo_despejado"]
            fila = fila if not fila.empty else g
            etiqueta_dia[int(d_)] = fecha_corta(fila.iloc[0]["hora"])
        if st.session_state.get("dia_sel") not in dias:
            st.session_state["dia_sel"] = dias[0]
        dia = st.session_state["dia_sel"]

        st.caption("Fecha del pronóstico (cada fecha es un bloque de 24 h)")
        for col, d_ in zip(st.columns(len(dias)), dias):
            boton(col, etiqueta_dia[d_], key=f"btn_dia_{d_}",
                  type="primary" if d_ == dia else "secondary",
                  on_click=st.session_state.update, kwargs={"dia_sel": d_})
        sub = m[m["dia"] == dia]
        n = sub[sub["tipo"] == "maximo_con_nubosidad"]
        d = sub[sub["tipo"] == "maximo_cielo_despejado"]

        c1, c2, c3 = st.columns(3)
        if not n.empty:
            r = n.iloc[0]
            tarjeta_uvi(c1, "Máx. con nubosidad", r["uvi"], r["fecha_hora"])
        if not d.empty:
            r = d.iloc[0]
            tarjeta_uvi(c2, "Máx. cielo despejado", r["uvi"], r["fecha_hora"])
        if not n.empty and not d.empty and d.iloc[0]["uvi"] > 0:
            c3.metric("Atenuación por nubes (máx/máx)",
                      f"{(1 - n.iloc[0]['uvi'] / d.iloc[0]['uvi']) * 100:.0f} %",
                      help="1 - (UVI máx. con nubosidad / UVI máx. cielo despejado) del día.")

        st.subheader("Comparación por día")
        # Icono dentro de cada barra: sol (cielo despejado) o nube segun la atenuacion del dia
        m["dia_txt"] = m["dia"].map(etiqueta_dia)
        desp = m[m["tipo"] == "maximo_cielo_despejado"].set_index("dia")["uvi"]
        atenuacion = m.apply(lambda r: (1 - r["uvi"] / desp.get(r["dia"], float("nan")))
                             if r["tipo"] == "maximo_con_nubosidad" and desp.get(r["dia"], 0) > 0
                             else float("nan"), axis=1)
        m["atenuacion"] = (atenuacion * 100).round(0)
        info = [nubosidad(a) if not pd.isna(a) else ("☀️", "cielo despejado") for a in atenuacion]
        m["icono"] = [i[0] if t == "maximo_con_nubosidad" else "☀️" for i, t in zip(info, m["tipo"])]
        m["condicion"] = [i[1] if t == "maximo_con_nubosidad" else "cielo despejado"
                          for i, t in zip(info, m["tipo"])]
        m["y_icono"] = m["uvi"].clip(lower=1.2) / 2   # centro de la barra (minimo para que se vea)

        ymax = max(12, float(m["uvi"].max()) + 1)
        base = alt.Chart(m)
        x = alt.X("dia_txt:N", title=None, sort=[etiqueta_dia[d_] for d_ in dias],
                  axis=alt.Axis(labelAngle=0))
        barras = (base.mark_bar()
                  .encode(x=x, xOffset="tipo_txt:N",
                          y=alt.Y("uvi:Q", scale=alt.Scale(domain=[0, ymax]), axis=eje_uvi(ymax)),
                          color=alt.Color("tipo_txt:N", title=None,
                                          scale=alt.Scale(domain=list(COLOR_BARRA),
                                                          range=list(COLOR_BARRA.values()))),
                          tooltip=[alt.Tooltip("dia_txt:N", title="Fecha"), "tipo_txt", alt.Tooltip("uvi:Q", format=".1f"),
                                   alt.Tooltip("fecha_hora:N", title=etiqueta_hora),
                                   alt.Tooltip("condicion:N", title="Condición"),
                                   alt.Tooltip("atenuacion:Q", title="Atenuación (%)")]))
        iconos = (base.mark_text(fontSize=28, baseline="middle")
                  .encode(x=x, xOffset="tipo_txt:N", y=alt.Y("y_icono:Q"), text="icono:N"))
        mostrar_grafico(alt.layer(bandas(ymax, eje_uvi(ymax)), barras, iconos)
                        .resolve_scale(color="independent").properties(height=340))
        st.caption("☀️ cielo despejado · 🌤️ escasa nubosidad (atenúa menos de 20 %) · "
                   "⛅ nubosidad parcial (20–50 %) · ☁️ nublado (más de 50 %). "
                   "La atenuación compara el máximo con nubosidad contra el de cielo despejado del día.")

# ----------------------------------------------------------------------------- serie horaria
with tab_serie:
    st.session_state.setdefault("ver_desp", True)
    st.session_state.setdefault("ver_nub", True)
    st.caption("Series visibles (siempre queda al menos una encendida)")
    b1, b2, _ = st.columns([1, 1, 2])
    boton(b1, "☀️ Cielo despejado", key="btn_ver_desp",
          type="primary" if st.session_state["ver_desp"] else "secondary",
          on_click=alternar_serie, args=("ver_desp", "ver_nub"))
    boton(b2, "☁️ Con nubosidad", key="btn_ver_nub",
          type="primary" if st.session_state["ver_nub"] else "secondary",
          on_click=alternar_serie, args=("ver_nub", "ver_desp"))
    series_on = [n for n, k in (("Cielo despejado", "ver_desp"), ("Con nubosidad", "ver_nub"))
                 if st.session_state[k]]

    c1, c2 = st.columns([3, 1])
    dias_f = sorted(h["fecha"].unique())
    elegidos = c1.multiselect("Fechas a mostrar", dias_f, default=dias_f)
    solo_sol = c2.checkbox("Solo horas con sol", value=False)
    s = h[h["fecha"].isin(elegidos)]
    if solo_sol:
        s = s[(s["uvi_despejado"] > 0) | (s["uvi_nubosidad"] > 0)]
    largo = s.melt(id_vars=["hora", "hora_pronostico"],
                   value_vars=["uvi_despejado", "uvi_nubosidad"],
                   var_name="serie", value_name="uvi")
    largo["serie"] = largo["serie"].map({"uvi_despejado": "Cielo despejado",
                                         "uvi_nubosidad": "Con nubosidad"})
    largo = largo[largo["serie"].isin(series_on)]
    if largo.empty:
        st.info("No hay datos para la selección.")
    else:
        ymax = max(12, float(largo["uvi"].max()) + 1)
        lineas = (alt.Chart(largo).mark_line(point=True)
                  .encode(x=alt.X("hora:T", title=etiqueta_hora,
                                  axis=alt.Axis(format="%d-%b %H:%M", labelAngle=-30)),
                          y=alt.Y("uvi:Q", title="Índice UV", scale=alt.Scale(domain=[0, ymax])),
                          color=alt.Color("serie:N", title=None,
                                          scale=alt.Scale(domain=list(COLOR_LINEA),
                                                          range=list(COLOR_LINEA.values()))),
                          tooltip=[alt.Tooltip("hora:T", title=etiqueta_hora, format="%Y-%m-%d %H:%M"),
                                   "serie:N", alt.Tooltip("uvi:Q", format=".1f"),
                                   alt.Tooltip("hora_pronostico:Q", title="Hora de pronóstico")])
                  .properties(height=420))
        mostrar_grafico(alt.layer(bandas(ymax).properties(height=420), lineas)
                        .resolve_scale(color="independent"))
        st.caption("Los colores de fondo son las categorías de la OMS: "
                   "bajo, moderado, alto, muy alto y extremo.")

# ----------------------------------------------------------------------------- comparacion
with tab_comp:
    st.caption("Máximo diario del índice UV en el punto elegido, para el ciclo seleccionado "
               "(Actual) y los 4 ciclos anteriores. Cada columna es la misma fecha en todos los "
               "ciclos; los ciclos más antiguos cubren menos días futuros, por eso les faltan "
               "las últimas fechas (—).")
    ciclos = fechas[fechas.index(fecha):][:5]      # fechas va de la mas reciente a la mas antigua
    datos = {f: maximos_por_fecha(raiz, f, p["latitud"], p["longitud"]) for f in ciclos}
    base = datos.get(fecha)
    if base is None or base[0].empty:
        st.info("El ciclo seleccionado no tiene archivo de máximos por día.")
    else:
        fechas_cols = sorted(base[0].index)
        cols = [f"Día {i} ({fecha_corta(d)})" for i, d in enumerate(fechas_cols, 1)]

        def tabla(i):
            filas = []
            for f in ciclos:
                r = datos[f]
                filas.append([etiqueta_ciclo(f, fecha)] +
                             [r[i].get(d, float("nan")) if r is not None else float("nan")
                              for d in fechas_cols])
            return pd.DataFrame(filas, columns=["Ciclo"] + cols)

        for titulo, i in (("☀️ Cielo despejado", 0), ("☁️ Con nubosidad", 1)):
            st.subheader(titulo)
            sty = tabla(i).style
            sty = (getattr(sty, "map", None) or sty.applymap)(pintar, subset=cols)
            mostrar_tabla(sty.format("{:.1f}", subset=cols, na_rep="—"))
        st.caption("Colores según la categoría del índice UV (verde bajo, amarillo moderado, "
                   "naranja alto, rojo muy alto, violeta extremo).")

# ----------------------------------------------------------------------------- ejecutor manual (GitHub)
with tab_dl:
    st.subheader("Ejecutar el flujo en GitHub")
    st.caption("Lanza a mano el workflow que descarga los GRIB2, genera los CSV/JSON y los sube al "
               "repositorio (el mismo que corre solo todos los días).")
    cfg = secretos_github()

    # --- conexion: desde secretos, o a mano si no hay
    if cfg.get("token") and cfg.get("repo"):
        token, repo = cfg["token"], cfg["repo"]
        workflow, rama = cfg.get("workflow", "Cron_UV.yml"), cfg.get("rama", "main")
        st.caption(f"Repositorio **{repo}** · workflow **{workflow}** · rama **{rama}**")
    else:
        st.info("No hay secretos configurados: escribe los datos aquí (no se guardan; "
                "mira el archivo secrets.toml.example para dejarlos fijos).")
        c1, c2, c3 = st.columns(3)
        repo = c1.text_input("Repositorio (usuario/nombre)", cfg.get("repo", ""))
        workflow = c2.text_input("Archivo del workflow", cfg.get("workflow", "Cron_UV.yml"))
        rama = c3.text_input("Rama", cfg.get("rama", "main"))
        token = st.text_input("Token de GitHub", type="password",
                              help="Token con permiso 'Actions: Read and write' sobre este repositorio.")

    # --- clave opcional que protege el boton (importante si el visor es publico)
    autorizado = True
    if cfg.get("clave"):
        clave = st.text_input("Clave para ejecutar", type="password")
        autorizado = bool(clave) and hmac.compare_digest(clave.encode(), str(cfg["clave"]).encode())
        if clave and not autorizado:
            st.error("Clave incorrecta.")

    # --- fecha
    auto, hora_cambio = fecha_automatica()
    modo = st.radio("Fecha a procesar", ["Automática", "Fecha específica"], horizontal=True)
    if modo == "Automática":
        fecha_sel = ""
        st.caption(f"Se procesará **{auto[:4]}-{auto[4:6]}-{auto[6:]}**: antes de las {hora_cambio} UTC "
                   "toma la fecha de ayer; desde esa hora, la de hoy.")
    else:
        elegida = st.date_input("Fecha", value=datetime.strptime(auto, "%Y%m%d").date())
        fecha_sel = elegida.strftime("%Y%m%d")

    # --- botones
    faltan_datos = not (token and repo and workflow and rama)
    restante = ESPERA_ENTRE_EJECUCIONES - (time.time() - st.session_state.get("dl_ultimo", 0))
    b1, b2, _ = st.columns([1, 1, 2])
    ejecutar = boton(b1, "▶ Ejecutar ahora", key="btn_dl_ejecutar", type="primary",
                     disabled=faltan_datos or not autorizado or restante > 0)
    actualizar = boton(b2, "🔄 Actualizar estado", key="btn_dl_actualizar", disabled=faltan_datos)
    if faltan_datos:
        st.caption("Completa repositorio, workflow, rama y token para habilitar los botones.")
    elif restante > 0:
        st.caption(f"Espera {int(restante)} s antes de ejecutar de nuevo.")

    ruta_runs = f"/repos/{repo}/actions/workflows/{workflow}/runs?per_page=8"

    if ejecutar and autorizado and restante <= 0:
        codigo, datos = gh("GET", ruta_runs, token)
        if codigo != 200:
            st.error(explicar_error(codigo, datos))
        elif any(r["status"] in ("queued", "in_progress", "waiting", "pending")
                 for r in datos.get("workflow_runs", [])):
            st.warning("Ya hay una ejecución en curso o en cola. Espera a que termine "
                       "(usa «Actualizar estado»).")
            st.session_state["dl_runs"] = datos.get("workflow_runs", [])
        else:
            cuerpo = {"ref": rama}
            if fecha_sel:
                cuerpo["inputs"] = {"fecha": fecha_sel}
            codigo, datos = gh("POST", f"/repos/{repo}/actions/workflows/{workflow}/dispatches", token, cuerpo)
            if codigo == 204:
                st.session_state["dl_ultimo"] = time.time()
                st.success("Solicitud enviada a GitHub. La ejecución aparece en unos segundos.")
                with st.spinner("Consultando la ejecución..."):
                    time.sleep(4)
                    codigo, datos = gh("GET", ruta_runs, token)
                if codigo == 200:
                    st.session_state["dl_runs"] = datos.get("workflow_runs", [])
            else:
                st.error(explicar_error(codigo, datos))

    elif actualizar:
        codigo, datos = gh("GET", ruta_runs, token)
        if codigo == 200:
            st.session_state["dl_runs"] = datos.get("workflow_runs", [])
        else:
            st.error(explicar_error(codigo, datos))

    # --- ultimas ejecuciones (solo se consulta al pulsar botones: no en cada recarga)
    st.markdown("**Últimas ejecuciones**")
    runs = st.session_state.get("dl_runs")
    if runs is None:
        st.caption("Pulsa «Actualizar estado» para consultar GitHub.")
    elif not runs:
        st.caption("Este workflow aún no tiene ejecuciones.")
    else:
        tabla = tabla_ejecuciones(runs, zona)
        try:
            st.dataframe(tabla, hide_index=True, width="stretch",
                         column_config={"GitHub": st.column_config.LinkColumn("GitHub", display_text="abrir")})
        except TypeError:
            st.dataframe(tabla, hide_index=True, use_container_width=True)
        st.caption(f"Horas en {etiqueta_hora.lower()}. Cuando termine, el visor mostrará los datos nuevos "
                   "al sincronizar el repositorio; después usa «Recargar datos» en la barra lateral.")
