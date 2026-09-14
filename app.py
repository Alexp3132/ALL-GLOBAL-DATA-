from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import math
import re
import unicodedata

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


# ==============================================================
# CONFIGURACIÓN GENERAL
# ============================================================== 
st.set_page_config(
    page_title="Control Fiscal y Auditoría",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

COLORS = {
    "navy": "#0B2E4F",
    "blue": "#1F5A94",
    "green": "#2E7D32",
    "amber": "#D99000",
    "red": "#B3261E",
    "gray": "#F3F5F7",
    "text": "#1F2937",
}

DEFAULT_FILES = {
    "facturas": "Base_1_Facturas_Emitidas.xlsx",
    "gastos": "Base_2_Gastos_Facturas_Recibidas.xlsx",
    "bancos": "Base_3_Movimientos_Bancarios.xlsx",
    "isr": "Base_4_Retenciones_ISR.xlsx",
}

EXPECTED_COLUMNS = {
    "facturas": ["folio", "fecha", "cliente", "concepto", "subtotal", "iva", "total"],
    "gastos": ["folio_gasto", "fecha", "proveedor", "concepto", "subtotal", "iva", "total", "comprobante"],
    "bancos": ["fecha", "monto", "tipo", "referencia"],
    "isr": ["fecha", "referencia", "concepto", "isr_retenido"],
}

INCOME_WORDS = {"deposito", "ingreso", "cobro", "abono", "transferencia recibida"}
EXPENSE_WORDS = {"egreso", "cargo", "pago", "retiro", "transferencia enviada"}


# ==============================================================
# FUNCIONES DE APOYO
# ============================================================== 
def normalize_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().lower()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text)


def money(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "$0.00"
    return f"${float(value):,.2f}"


def pct(value) -> str:
    return f"{float(value):,.1f}%"


def safe_numeric_sum(df: pd.DataFrame, column: str) -> float:
    if df is None or column not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[column], errors="coerce").fillna(0.0).sum())


def safe_column(df: pd.DataFrame, column: str, default=None) -> pd.Series:
    if isinstance(df, pd.DataFrame) and column in df.columns:
        return df[column]
    index = df.index if isinstance(df, pd.DataFrame) else pd.RangeIndex(0)
    return pd.Series(default, index=index)


def parse_date_column(series: pd.Series) -> pd.Series:
    """Convierte fechas de Excel, datetime o texto en pandas datetime."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce")

    numeric = pd.to_numeric(series, errors="coerce")
    # Los seriales de Excel modernos suelen estar por encima de 20,000.
    mask_excel = numeric.notna() & numeric.between(20000, 80000)
    result = pd.to_datetime(series, errors="coerce", dayfirst=True)
    if mask_excel.any():
        result.loc[mask_excel] = pd.Timestamp("1899-12-30") + pd.to_timedelta(numeric.loc[mask_excel], unit="D")
    return result


def clean_dataframe(df: pd.DataFrame, key: str) -> pd.DataFrame:
    df = df.copy()
    df.columns = [normalize_text(c).replace(" ", "_") for c in df.columns]

    missing = [c for c in EXPECTED_COLUMNS[key] if c not in df.columns]
    if missing:
        raise ValueError(f"La base '{key}' no contiene las columnas requeridas: {', '.join(missing)}")

    df = df[EXPECTED_COLUMNS[key]].copy()
    if "fecha" in df.columns:
        df["fecha"] = parse_date_column(df["fecha"])

    for col in ["subtotal", "iva", "total", "monto", "isr_retenido"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ["folio", "folio_gasto", "cliente", "proveedor", "concepto", "comprobante", "tipo", "referencia"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()

    return df


@st.cache_data(show_spinner=False)
def read_excel_bytes(file_bytes: bytes, key: str) -> pd.DataFrame:
    df = pd.read_excel(BytesIO(file_bytes))
    return clean_dataframe(df, key)


def load_local_file(path: Path, key: str) -> pd.DataFrame:
    return clean_dataframe(pd.read_excel(path), key)


def get_data(uploaded, default_name: str, key: str) -> tuple[pd.DataFrame | None, str]:
    if uploaded is not None:
        return read_excel_bytes(uploaded.getvalue(), key), f"Archivo cargado: {uploaded.name}"

    local = Path(default_name)
    if local.exists():
        return load_local_file(local, key), f"Archivo local: {local.name}"

    return None, "Pendiente de carga"


def classify_bank_direction(tipo: str, monto: float) -> str:
    t = normalize_text(tipo)
    if t in INCOME_WORDS or any(word in t for word in ["deposit", "ingres", "cobro", "abono"]):
        return "Ingreso"
    if t in EXPENSE_WORDS or any(word in t for word in ["egres", "cargo", "pago", "retiro"]):
        return "Egreso"
    # Fallback didáctico: signo del monto.
    return "Ingreso" if (pd.notna(monto) and monto >= 0) else "Egreso"


def text_similarity(a: str, b: str) -> float:
    """Similitud simple Jaccard de palabras; evita dependencias complejas."""
    sa = set(normalize_text(a).split())
    sb = set(normalize_text(b).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


@dataclass
class MatchResult:
    bank_indexes: list[int]
    paid_amount: float
    status: str
    method: str
    difference: float
    day_difference: float | None


def match_document_to_bank(
    doc_ref: str,
    doc_date: pd.Timestamp,
    doc_amount: float,
    doc_concept: str,
    bank: pd.DataFrame,
    candidate_mask: pd.Series,
    used: set[int],
    amount_tolerance: float,
    day_tolerance: int,
) -> MatchResult:
    """
    Conciliación didáctica:
    1) referencia exacta;
    2) monto + fecha + concepto;
    3) pagos parciales dentro de la ventana de fecha.

    No asigna un mismo movimiento bancario a dos documentos.
    """
    if pd.isna(doc_amount):
        return MatchResult([], 0.0, "Revisar", "Importe inválido", np.nan, None)

    candidates = bank[candidate_mask & ~bank.index.isin(used)].copy()
    if candidates.empty:
        return MatchResult([], 0.0, "Sin coincidencia", "Sin movimientos disponibles", doc_amount, None)

    candidates["dias"] = (candidates["fecha"] - doc_date).dt.days.abs()
    candidates["dif_importe"] = (candidates["monto_abs"] - doc_amount).abs()
    candidates["similitud"] = candidates.apply(
        lambda r: max(text_similarity(doc_concept, r.get("referencia", "")), text_similarity(doc_ref, r.get("referencia", ""))),
        axis=1,
    )

    # 1. Coincidencia exacta por referencia, aceptando uno o varios movimientos.
    exact = candidates[candidates["referencia_norm"] == normalize_text(doc_ref)].sort_values(["dias", "dif_importe"])
    if not exact.empty:
        picked = []
        acc = 0.0
        for idx, row in exact.iterrows():
            picked.append(idx)
            acc += row["monto_abs"]
            if abs(acc - doc_amount) <= amount_tolerance or acc >= doc_amount:
                break
        diff = acc - doc_amount
        max_days = float(exact.loc[picked, "dias"].max()) if picked else None
        if abs(diff) <= amount_tolerance:
            status = "Conciliado" if abs(diff) < 0.01 else "Conciliado con tolerancia"
        elif acc < doc_amount:
            status = "Parcial"
        else:
            status = "Revisar"
        return MatchResult(picked, acc, status, "Folio/referencia", diff, max_days)

    # 2. Coincidencia probable por monto + fecha + concepto/referencia.
    probable = candidates[(candidates["dias"] <= day_tolerance) & (candidates["dif_importe"] <= amount_tolerance)].copy()
    if not probable.empty:
        probable["score"] = (
            (1 - probable["dif_importe"] / max(amount_tolerance, 0.01)) * 0.55
            + (1 - probable["dias"] / max(day_tolerance, 1)) * 0.35
            + probable["similitud"] * 0.10
        )
        best_idx = probable["score"].idxmax()
        row = probable.loc[best_idx]
        diff = row["monto_abs"] - doc_amount
        status = "Conciliado" if abs(diff) < 0.01 else "Conciliado con tolerancia"
        return MatchResult([best_idx], float(row["monto_abs"]), status, "Monto + fecha + concepto", float(diff), float(row["dias"]))

    # 3. Posible pago parcial: movimientos cercanos en fecha, acumulados sin exceder demasiado.
    partial = candidates[candidates["dias"] <= day_tolerance].sort_values(["dias", "monto_abs"])
    picked = []
    acc = 0.0
    for idx, row in partial.iterrows():
        if row["monto_abs"] <= doc_amount + amount_tolerance:
            picked.append(idx)
            acc += row["monto_abs"]
            if abs(acc - doc_amount) <= amount_tolerance:
                break
            if acc > doc_amount + amount_tolerance:
                picked.pop()
                acc -= row["monto_abs"]
                break
    if picked and acc > 0:
        diff = acc - doc_amount
        max_days = float(partial.loc[picked, "dias"].max())
        if abs(diff) <= amount_tolerance:
            return MatchResult(picked, acc, "Conciliado con tolerancia", "Acumulación por fecha/monto", diff, max_days)
        if acc < doc_amount:
            return MatchResult(picked, acc, "Parcial", "Pago parcial probable", diff, max_days)

    return MatchResult([], 0.0, "Sin coincidencia", "Sin coincidencia probable", -doc_amount, None)


def reconcile_documents(
    documents: pd.DataFrame,
    bank: pd.DataFrame,
    ref_col: str,
    party_col: str,
    direction: str,
    amount_tolerance: float,
    day_tolerance: int,
) -> tuple[pd.DataFrame, set[int]]:
    used: set[int] = set()
    out = []
    duplicate_refs = set(documents.loc[documents[ref_col].duplicated(keep=False), ref_col].astype(str))

    direction_mask = bank["direccion"] == direction
    for _, doc in documents.sort_values("fecha").iterrows():
        ref = str(doc[ref_col])
        if ref in duplicate_refs:
            out.append({
                ref_col: ref,
                "fecha": doc["fecha"],
                party_col: doc[party_col],
                "concepto": doc["concepto"],
                "importe_documento": doc["total"],
                "importe_bancario": 0.0,
                "diferencia": -doc["total"],
                "dias_diferencia": np.nan,
                "estatus": "Duplicado",
                "metodo": "Referencia duplicada en base",
                "movimientos_bancarios": "",
            })
            continue

        match = match_document_to_bank(
            ref, doc["fecha"], doc["total"], doc["concepto"], bank,
            direction_mask, used, amount_tolerance, day_tolerance,
        )
        used.update(match.bank_indexes)
        refs = ", ".join(bank.loc[match.bank_indexes, "referencia"].astype(str).tolist()) if match.bank_indexes else ""
        out.append({
            ref_col: ref,
            "fecha": doc["fecha"],
            party_col: doc[party_col],
            "concepto": doc["concepto"],
            "importe_documento": doc["total"],
            "importe_bancario": match.paid_amount,
            "diferencia": match.difference,
            "dias_diferencia": match.day_difference,
            "estatus": match.status,
            "metodo": match.method,
            "movimientos_bancarios": refs,
        })
    return pd.DataFrame(out), used


def detect_outliers(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    if s.dropna().shape[0] < 4:
        return pd.Series(False, index=series.index)
    q1, q3 = s.quantile([0.25, 0.75])
    iqr = q3 - q1
    if iqr == 0:
        return pd.Series(False, index=series.index)
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return (s < lower) | (s > upper)


def build_alerts(
    facturas: pd.DataFrame,
    gastos: pd.DataFrame,
    bancos: pd.DataFrame,
    isr: pd.DataFrame,
    conc_fact: pd.DataFrame,
    conc_gas: pd.DataFrame,
    used_income: set[int],
    used_expense: set[int],
    selected_start: pd.Timestamp,
    selected_end: pd.Timestamp,
) -> pd.DataFrame:
    alerts = []

    def add(level, category, reference, detail, amount=np.nan):
        alerts.append({"nivel": level, "categoria": category, "referencia": reference, "detalle": detail, "importe": amount})

    for _, r in conc_fact[conc_fact["estatus"].isin(["Sin coincidencia", "Parcial", "Duplicado", "Revisar"])].iterrows():
        add("Alta" if r["estatus"] in ["Duplicado", "Revisar"] else "Media", "Facturación/Cobranza", r["folio"], f"Factura con estatus: {r['estatus']}", r["importe_documento"])

    for _, r in conc_gas[conc_gas["estatus"].isin(["Sin coincidencia", "Parcial", "Duplicado", "Revisar"])].iterrows():
        add("Media", "Gastos/Pagos", r["folio_gasto"], f"Gasto con estatus: {r['estatus']}", r["importe_documento"])

    unused = bancos.index[~bancos.index.isin(used_income | used_expense)]
    for idx in unused:
        r = bancos.loc[idx]
        add("Media", "Bancos", r["referencia"], "Movimiento bancario sin documento asociado", r["monto_abs"])

    # Campos vacíos.
    for name, df, id_col in [
        ("Facturas", facturas, "folio"), ("Gastos", gastos, "folio_gasto"), ("Bancos", bancos, "referencia"), ("ISR", isr, "referencia")
    ]:
        for idx, row in df.iterrows():
            if row.isna().any() or any(str(row.get(c, "")).strip() == "" for c in df.select_dtypes(include="object").columns):
                add("Baja", "Integridad de datos", str(row.get(id_col, idx)), f"{name}: registro con campo vacío o nulo")

    # Fechas fuera del periodo seleccionado: se revisan contra las bases completas.
    for name, df, id_col in [
        ("Facturas", facturas, "folio"), ("Gastos", gastos, "folio_gasto"), ("Bancos", bancos, "referencia"), ("ISR", isr, "referencia")
    ]:
        outside = df[(df["fecha"] < selected_start) | (df["fecha"] > selected_end)]
        for _, r in outside.head(100).iterrows():
            add("Baja", "Periodo", str(r.get(id_col, "")), f"{name}: fecha fuera del periodo analizado")

    # IVA inconsistente: total debe ser subtotal + IVA y la tasa esperada es 16% salvo redondeos.
    for name, df, id_col in [("Factura", facturas, "folio"), ("Gasto", gastos, "folio_gasto")]:
        iva_error = (df["subtotal"] + df["iva"] - df["total"]).abs() > 0.02
        tasa_error = (df["iva"] - df["subtotal"] * 0.16).abs() > 0.10
        for _, r in df[iva_error | tasa_error].iterrows():
            add("Alta", "IVA", r[id_col], f"{name}: IVA o total inconsistente con tasa esperada de 16%", r["total"])

    # Comprobante y deducibilidad.
    for _, r in gastos[gastos["clasificacion"] == "No deducible"].iterrows():
        add("Media", "Deducibilidad", r["folio_gasto"], "Gasto clasificado como no deducible por falta de comprobante", r["total"])

    # Importes atípicos.
    for name, df, col, id_col in [
        ("Factura", facturas, "total", "folio"), ("Gasto", gastos, "total", "folio_gasto"), ("Banco", bancos, "monto_abs", "referencia")
    ]:
        mask = detect_outliers(df[col])
        for _, r in df[mask].iterrows():
            add("Baja", "Importe atípico", str(r[id_col]), f"{name}: importe fuera del rango intercuartílico esperado", r[col])

    # ISR: con la estructura real actual, sólo se vincula si la referencia coincide con un folio.
    invoice_refs = set(facturas["folio"].map(normalize_text))
    isr["vinculado_factura"] = isr["referencia"].map(normalize_text).isin(invoice_refs)
    for _, r in isr[~isr["vinculado_factura"]].iterrows():
        add("Media", "ISR", r["referencia"], "Retención ISR sin folio de factura identificable; requiere soporte documental", r["isr_retenido"])

    if not alerts:
        return pd.DataFrame(columns=["nivel", "categoria", "referencia", "detalle", "importe"])
    return pd.DataFrame(alerts).drop_duplicates()


def calculate_control_index(
    conc_fact: pd.DataFrame,
    conc_gas: pd.DataFrame,
    alerts: pd.DataFrame,
    facturas: pd.DataFrame,
    gastos: pd.DataFrame,
    bancos: pd.DataFrame,
    isr: pd.DataFrame,
) -> tuple[float, dict]:
    total_docs = max(len(conc_fact) + len(conc_gas), 1)
    ok_docs = conc_fact["estatus"].isin(["Conciliado", "Conciliado con tolerancia"]).sum() + conc_gas["estatus"].isin(["Conciliado", "Conciliado con tolerancia"]).sum()
    reconciliation = ok_docs / total_docs * 100

    tax_alerts = len(alerts[alerts["categoria"].isin(["IVA", "ISR", "Deducibilidad"])])
    tax_base = max(len(facturas) + len(gastos) + len(isr), 1)
    fiscal = max(0, 100 - tax_alerts / tax_base * 100)

    integrity_alerts = len(alerts[alerts["categoria"].isin(["Integridad de datos", "Importe atípico", "Periodo"])])
    integrity_base = max(len(facturas) + len(gastos) + len(bancos) + len(isr), 1)
    integrity = max(0, 100 - integrity_alerts / integrity_base * 100)

    support = gastos["clasificacion"].eq("Deducible").mean() * 100 if len(gastos) else 100

    high = len(alerts[alerts["nivel"] == "Alta"])
    medium = len(alerts[alerts["nivel"] == "Media"])
    incident_score = max(0, 100 - (high * 3 + medium * 1.5) / max(total_docs, 1) * 100)

    components = {
        "Conciliación": reconciliation,
        "Cumplimiento fiscal": fiscal,
        "Integridad de datos": integrity,
        "Soporte documental": support,
        "Incidencias": incident_score,
    }
    total = reconciliation * 0.30 + fiscal * 0.25 + integrity * 0.20 + support * 0.15 + incident_score * 0.10
    return float(np.clip(total, 0, 100)), components


def risk_label(score: float) -> tuple[str, str]:
    if score >= 90:
        return "Control satisfactorio", COLORS["green"]
    if score >= 80:
        return "Control aceptable con observaciones", COLORS["blue"]
    if score >= 70:
        return "Requiere seguimiento", COLORS["amber"]
    return "Riesgo elevado / revisión prioritaria", COLORS["red"]


def build_conclusions(score: float, components: dict, alerts: pd.DataFrame, metrics: dict) -> list[str]:
    label, _ = risk_label(score)
    conclusions = [f"El Índice General de Control Fiscal y Auditoría es {score:.1f}%, clasificado como '{label}'."]
    conclusions.append(f"La cobranza del periodo equivale al {metrics['pct_cobranza']:.1f}% del total facturado analizado.")
    conclusions.append(f"El IVA neto estimado del periodo es {money(metrics['iva_neto'])} y el ISR retenido registrado asciende a {money(metrics['isr'])}.")

    if alerts.empty:
        conclusions.append("No se identificaron alertas relevantes bajo las reglas configuradas.")
        return conclusions

    counts = alerts.groupby(["nivel", "categoria"]).size().sort_values(ascending=False)
    top = counts.head(3)
    for (level, category), count in top.items():
        conclusions.append(f"Se detectaron {count} observaciones de nivel {level.lower()} en la categoría {category}.")

    weakest = min(components, key=components.get)
    conclusions.append(f"La dimensión con menor calificación es '{weakest}' ({components[weakest]:.1f}%), por lo que debe priorizarse en el seguimiento de auditoría.")
    return conclusions


def recommendations(alerts: pd.DataFrame, components: dict) -> list[str]:
    recs = []
    categories = set(alerts["categoria"]) if not alerts.empty else set()
    if "Facturación/Cobranza" in categories:
        recs.append("Documentar y dar seguimiento a facturas no cobradas, parciales o con diferencias antes del cierre fiscal del periodo.")
    if "Gastos/Pagos" in categories or "Deducibilidad" in categories:
        recs.append("Regularizar comprobantes y evidencia de pago de gastos para sostener su deducibilidad y trazabilidad.")
    if "Bancos" in categories:
        recs.append("Investigar movimientos bancarios sin soporte y agregar referencias normalizadas para reducir conciliaciones manuales.")
    if "IVA" in categories:
        recs.append("Revisar operaciones con diferencias de IVA y validar que subtotal, tasa y total correspondan al CFDI fuente.")
    if "ISR" in categories:
        recs.append("Incorporar en la base de ISR un folio de factura/CFDI o identificador común que permita una vinculación documental verificable.")
    if "Integridad de datos" in categories:
        recs.append("Completar campos obligatorios y establecer validaciones previas a la carga para evitar registros incompletos.")
    if not recs:
        recs.append("Mantener las validaciones actuales y conservar evidencia de conciliación para el expediente de auditoría.")
    return recs


def to_excel_bytes(sheets: dict[str, pd.DataFrame], summary: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        summary.to_excel(writer, sheet_name="Resumen", index=False)
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name[:31], index=False)

        workbook = writer.book
        header = workbook.add_format({"bold": True, "font_color": "white", "bg_color": COLORS["navy"], "border": 0})
        money_fmt = workbook.add_format({"num_format": '$#,##0.00;[Red]($#,##0.00);-'})
        date_fmt = workbook.add_format({"num_format": "dd/mm/yyyy"})
        for sheet_name, worksheet in writer.sheets.items():
            worksheet.freeze_panes(1, 0)
            worksheet.hide_gridlines(2)
            df = summary if sheet_name == "Resumen" else sheets[sheet_name]
            for col_idx, col_name in enumerate(df.columns):
                worksheet.write(0, col_idx, col_name, header)
                width = min(max(len(str(col_name)) + 2, 12), 34)
                worksheet.set_column(col_idx, col_idx, width)
                if any(k in normalize_text(col_name) for k in ["importe", "monto", "total", "iva", "isr", "subtotal", "diferencia"]):
                    worksheet.set_column(col_idx, col_idx, max(width, 16), money_fmt)
                if "fecha" in normalize_text(col_name):
                    worksheet.set_column(col_idx, col_idx, max(width, 13), date_fmt)
    return output.getvalue()


# ==============================================================
# ESTILOS
# ==============================================================
st.markdown(
    f"""
    <style>
      :root {{ color-scheme: light; }}
      .stApp {{ background-color:#F4F7FA !important; color:#172033 !important; }}
      [data-testid="stAppViewContainer"] {{ background-color:#F4F7FA !important; }}
      [data-testid="stSidebar"] {{ background-color:#EAF0F5 !important; }}
      [data-testid="stSidebar"] * {{ color:#172033; }}
      .main-title {{ color:{COLORS['navy']}; font-size:2.1rem; font-weight:800; margin-bottom:.2rem; }}
      .subtitle {{ color:#536273; margin-bottom:1.2rem; font-weight:500; }}
      .audit-box {{ background:#FFFFFF; border-left:5px solid {COLORS['blue']}; padding:16px; border-radius:10px; }}
      .risk-box {{ padding:20px; border-radius:10px; color:#FFFFFF !important; font-weight:700; text-align:center; }}
      div[data-testid="stMetric"] {{ background:#FFFFFF !important; border:1px solid #D8E0E8 !important; padding:14px 16px !important; border-radius:10px !important; }}
      div[data-testid="stMetric"] [data-testid="stMetricLabel"],
      div[data-testid="stMetric"] [data-testid="stMetricLabel"] p {{ color:#526173 !important; opacity:1 !important; -webkit-text-fill-color:#526173 !important; font-weight:650 !important; }}
      div[data-testid="stMetric"] [data-testid="stMetricValue"],
      div[data-testid="stMetric"] [data-testid="stMetricValue"] *,
      div[data-testid="stMetric"] [data-testid="stMetricValue"] > div {{ color:#0B2E4F !important; opacity:1 !important; -webkit-text-fill-color:#0B2E4F !important; font-weight:800 !important; }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="main-title">Control Fiscal y Auditoría</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Modelo administrativo y de auditoría · Conciliación, fiscalización, alertas y conclusiones ejecutivas</div>', unsafe_allow_html=True)


# ==============================================================
# CARGA Y PARÁMETROS
# ============================================================== 
with st.sidebar:
    st.header("1. Bases de datos")
    f_fact = st.file_uploader("Facturas emitidas", type=["xlsx"], key="f_fact")
    f_gas = st.file_uploader("Gastos / facturas recibidas", type=["xlsx"], key="f_gas")
    f_ban = st.file_uploader("Movimientos bancarios", type=["xlsx"], key="f_ban")
    f_isr = st.file_uploader("Retenciones ISR", type=["xlsx"], key="f_isr")

    st.header("2. Parámetros de conciliación")
    amount_tolerance = st.number_input("Tolerancia de monto ($)", min_value=0.0, value=10.0, step=1.0)
    day_tolerance = st.number_input("Tolerancia de fecha (días)", min_value=0, value=5, step=1)

try:
    facturas, status_fact = get_data(f_fact, DEFAULT_FILES["facturas"], "facturas")
    gastos, status_gas = get_data(f_gas, DEFAULT_FILES["gastos"], "gastos")
    bancos, status_ban = get_data(f_ban, DEFAULT_FILES["bancos"], "bancos")
    isr, status_isr = get_data(f_isr, DEFAULT_FILES["isr"], "isr")
except Exception as exc:
    st.error(f"No fue posible leer las bases: {exc}")
    st.stop()

with st.sidebar:
    for label, status in [("Facturas", status_fact), ("Gastos", status_gas), ("Bancos", status_ban), ("ISR", status_isr)]:
        st.caption(f"{label}: {status}")

if any(x is None for x in [facturas, gastos, bancos, isr]):
    st.info(
        "Carga los cuatro archivos Excel. También puedes colocar los archivos con sus nombres originales en la misma carpeta que app.py para que se lean automáticamente."
    )
    st.stop()

# Preparaciones de negocio.
gastos["clasificacion"] = np.where(gastos["comprobante"].map(normalize_text).isin(["si", "sí", "yes", "1", "true"]), "Deducible", "No deducible")
bancos["monto_abs"] = bancos["monto"].abs()
bancos["direccion"] = bancos.apply(lambda r: classify_bank_direction(r["tipo"], r["monto"]), axis=1)
bancos["referencia_norm"] = bancos["referencia"].map(normalize_text)

all_dates = pd.concat([facturas["fecha"], gastos["fecha"], bancos["fecha"], isr["fecha"]], ignore_index=True).dropna()
if all_dates.empty:
    st.error("No se encontraron fechas válidas en las bases.")
    st.stop()

min_date, max_date = all_dates.min().date(), all_dates.max().date()
with st.sidebar:
    st.header("3. Periodo y filtros")
    date_range = st.date_input("Rango de fechas", value=(min_date, max_date), min_value=min_date, max_value=max_date)
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = end_date = date_range

start_ts, end_ts = pd.Timestamp(start_date), pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)

def period(df):
    return df[df["fecha"].between(start_ts, end_ts)].copy()

fact_p, gas_p, ban_p, isr_p = map(period, [facturas, gastos, bancos, isr])

with st.sidebar:
    clients = ["Todos"] + sorted([x for x in fact_p["cliente"].dropna().unique() if str(x).strip()])
    suppliers = ["Todos"] + sorted([x for x in gas_p["proveedor"].dropna().unique() if str(x).strip()])
    client_filter = st.selectbox("Cliente", clients)
    supplier_filter = st.selectbox("Proveedor", suppliers)
    class_filter = st.selectbox("Clasificación del gasto", ["Todos", "Deducible", "No deducible"])

if client_filter != "Todos":
    fact_p = fact_p[fact_p["cliente"] == client_filter]
if supplier_filter != "Todos":
    gas_p = gas_p[gas_p["proveedor"] == supplier_filter]
if class_filter != "Todos":
    gas_p = gas_p[gas_p["clasificacion"] == class_filter]

# Conciliación.
conc_fact, used_income = reconcile_documents(fact_p, ban_p, "folio", "cliente", "Ingreso", amount_tolerance, int(day_tolerance))
conc_gas, used_expense = reconcile_documents(gas_p, ban_p, "folio_gasto", "proveedor", "Egreso", amount_tolerance, int(day_tolerance))

# El filtro de estatus se aplica después de conciliar.
with st.sidebar:
    status_filter = st.selectbox(
        "Estatus de conciliación",
        ["Todos", "Conciliado", "Conciliado con tolerancia", "Parcial", "Sin coincidencia", "Duplicado", "Revisar"],
    )

# Alertas (las bases completas se usan para señalar fechas fuera de periodo; las conciliaciones corresponden al filtro actual).
alerts = build_alerts(facturas.copy(), gastos.copy(), bancos.copy(), isr.copy(), conc_fact, conc_gas, used_income, used_expense, start_ts, end_ts)

# Métricas.
facturado = safe_numeric_sum(fact_p, "total")
cobrado = safe_numeric_sum(conc_fact, "importe_bancario")
pct_cobranza = (cobrado / facturado * 100) if facturado else 0

gasto_deducible = gas_p.loc[gas_p["clasificacion"] == "Deducible", "total"].sum()
gasto_no_deducible = gas_p.loc[gas_p["clasificacion"] == "No deducible", "total"].sum()
iva_trasladado = safe_numeric_sum(fact_p, "iva")
iva_acreditable = gas_p.loc[gas_p["clasificacion"] == "Deducible", "iva"].sum()
iva_neto = iva_trasladado - iva_acreditable
isr_total = safe_numeric_sum(isr_p, "isr_retenido")

bank_income = ban_p.loc[ban_p["direccion"] == "Ingreso", "monto_abs"].sum()
bank_expense = ban_p.loc[ban_p["direccion"] == "Egreso", "monto_abs"].sum()
flujo_neto = bank_income - bank_expense
pending = (~conc_fact["estatus"].isin(["Conciliado", "Conciliado con tolerancia"])).sum() + (~conc_gas["estatus"].isin(["Conciliado", "Conciliado con tolerancia"])).sum()

score, components = calculate_control_index(conc_fact, conc_gas, alerts, fact_p, gas_p, ban_p, isr_p)
risk, risk_color = risk_label(score)
metrics = {
    "facturado": facturado, "cobrado": cobrado, "pct_cobranza": pct_cobranza,
    "gasto_deducible": gasto_deducible, "gasto_no_deducible": gasto_no_deducible,
    "flujo_neto": flujo_neto, "iva_trasladado": iva_trasladado, "iva_acreditable": iva_acreditable,
    "iva_neto": iva_neto, "isr": isr_total, "pending": pending,
}

# ==============================================================
# DASHBOARD
# ============================================================== 
st.subheader("Resumen ejecutivo")
row1 = st.columns(6)
for col, label, value in zip(
    row1,
    ["Facturado", "Cobrado", "% cobranza", "Gasto deducible", "Gasto no deducible", "Flujo neto"],
    [money(facturado), money(cobrado), pct(pct_cobranza), money(gasto_deducible), money(gasto_no_deducible), money(flujo_neto)],
):
    col.metric(label, value)

row2 = st.columns(5)
for col, label, value in zip(
    row2,
    ["IVA trasladado", "IVA acreditable", "IVA neto", "ISR retenido", "Pendientes"],
    [money(iva_trasladado), money(iva_acreditable), money(iva_neto), money(isr_total), f"{pending:,}"],
):
    col.metric(label, value)

idx_col, gauge_col = st.columns([1, 2])
with idx_col:
    st.markdown(f'<div class="risk-box" style="background:{risk_color};"><div style="font-size:2.5rem">{score:.1f}%</div><div>Índice General de Control</div><div style="font-size:.95rem;margin-top:6px">{risk}</div></div>', unsafe_allow_html=True)
    st.caption("Ponderación: Conciliación 30% · Cumplimiento fiscal 25% · Integridad 20% · Soporte documental 15% · Incidencias 10%.")
with gauge_col:
    comp_df = pd.DataFrame({"Dimensión": components.keys(), "Calificación": components.values()})
    fig_comp = px.bar(comp_df, x="Calificación", y="Dimensión", orientation="h", range_x=[0, 100], text_auto=".1f")
    fig_comp.update_layout(title="Componentes del índice", height=310, margin=dict(l=10, r=10, t=45, b=10), showlegend=False)
    st.plotly_chart(fig_comp, use_container_width=True)

# Flujo diario prioritario.
st.subheader("Flujo diario")
daily_source = ban_p.copy()

if not daily_source.empty and "fecha" in daily_source.columns:
    daily_source = daily_source[daily_source["fecha"].notna()].copy()
    daily_source["fecha_dia"] = daily_source["fecha"].dt.normalize()
    direccion = safe_column(daily_source, "direccion", "")
    monto_abs = pd.to_numeric(safe_column(daily_source, "monto_abs", 0.0), errors="coerce").fillna(0.0)
    daily_source["ingreso"] = np.where(direccion.eq("Ingreso"), monto_abs, 0.0)
    daily_source["egreso"] = np.where(direccion.eq("Egreso"), monto_abs, 0.0)
    daily = (
        daily_source.groupby("fecha_dia", as_index=False)
        .agg(ingresos=("ingreso", "sum"), egresos=("egreso", "sum"))
        .rename(columns={"fecha_dia": "fecha"})
        .sort_values("fecha")
    )
else:
    daily = pd.DataFrame(columns=["fecha", "ingresos", "egresos"])

for required_col in ["fecha", "ingresos", "egresos"]:
    if required_col not in daily.columns:
        daily[required_col] = pd.NaT if required_col == "fecha" else 0.0

daily["ingresos"] = pd.to_numeric(daily["ingresos"], errors="coerce").fillna(0.0)
daily["egresos"] = pd.to_numeric(daily["egresos"], errors="coerce").fillna(0.0)
daily["flujo_neto"] = daily["ingresos"] - daily["egresos"]

if not daily.empty:
    fig_flow = go.Figure()
    fig_flow.add_bar(x=daily["fecha"], y=daily["ingresos"], name="Ingresos")
    fig_flow.add_bar(x=daily["fecha"], y=-daily["egresos"], name="Egresos")
    fig_flow.add_scatter(x=daily["fecha"], y=daily["flujo_neto"], name="Flujo neto", mode="lines+markers")
    fig_flow.update_layout(barmode="relative", height=430, yaxis_title="Importe ($)", xaxis_title="Fecha",
                           legend_title="Serie", paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
                           font=dict(color="#172033"))
    st.plotly_chart(fig_flow, use_container_width=True)
else:
    st.info("No existen movimientos bancarios para el periodo seleccionado.")

# Gráficas secundarias.
c1, c2 = st.columns(2)
with c1:
    fig_fc = px.bar(pd.DataFrame({"Concepto": ["Facturado", "Cobrado"], "Importe": [facturado, cobrado]}), x="Concepto", y="Importe", text_auto=".2s", title="Facturado vs. cobrado")
    st.plotly_chart(fig_fc, use_container_width=True)
with c2:
    fig_g = px.pie(pd.DataFrame({"Clasificación": ["Deducible", "No deducible"], "Importe": [gasto_deducible, gasto_no_deducible]}), names="Clasificación", values="Importe", title="Gastos por deducibilidad")
    st.plotly_chart(fig_g, use_container_width=True)

c3, c4 = st.columns(2)
with c3:
    fig_iva = px.bar(pd.DataFrame({"IVA": ["Trasladado", "Acreditable", "Neto"], "Importe": [iva_trasladado, iva_acreditable, iva_neto]}), x="IVA", y="Importe", title="IVA del periodo")
    st.plotly_chart(fig_iva, use_container_width=True)
with c4:
    status_counts = pd.concat([conc_fact[["estatus"]], conc_gas[["estatus"]]], ignore_index=True)["estatus"].value_counts().reset_index()
    status_counts.columns = ["Estatus", "Operaciones"]
    fig_status = px.bar(status_counts, x="Estatus", y="Operaciones", title="Estatus de conciliación")
    st.plotly_chart(fig_status, use_container_width=True)

# Detalle.
st.subheader("Detalle y trazabilidad")
tab1, tab2, tab3, tab4, tab5 = st.tabs(["Conciliación facturas", "Conciliación gastos", "Movimientos bancarios", "ISR", "Alertas"])

def apply_status(df):
    return df if status_filter == "Todos" else df[df["estatus"] == status_filter]

with tab1:
    st.dataframe(apply_status(conc_fact), use_container_width=True, hide_index=True)
with tab2:
    st.dataframe(apply_status(conc_gas), use_container_width=True, hide_index=True)
with tab3:
    st.dataframe(ban_p, use_container_width=True, hide_index=True)
with tab4:
    isr_show = isr_p.copy()
    invoice_refs = set(fact_p["folio"].map(normalize_text))
    isr_show["vinculado_factura"] = isr_show["referencia"].map(normalize_text).isin(invoice_refs)
    st.dataframe(isr_show, use_container_width=True, hide_index=True)
    st.caption("Nota de auditoría: si la base de ISR no contiene el folio de factura/CFDI, la aplicación no inventa una relación; lo marca para revisión documental.")
with tab5:
    st.dataframe(alerts, use_container_width=True, hide_index=True)

# Conclusiones y recomendaciones.
st.subheader("Conclusiones y recomendaciones de auditoría")
conclusions = build_conclusions(score, components, alerts, metrics)
recs = recommendations(alerts, components)

left, right = st.columns(2)
with left:
    st.markdown("**Conclusiones**")
    for item in conclusions:
        st.markdown(f"- {item}")
with right:
    st.markdown("**Recomendaciones**")
    for item in recs:
        st.markdown(f"- {item}")

# Descarga Excel.
summary_df = pd.DataFrame([
    ["Periodo", f"{start_date} a {end_date}"],
    ["Ingresos facturados", facturado],
    ["Ingresos cobrados", cobrado],
    ["% cobranza", pct_cobranza / 100],
    ["Gastos deducibles", gasto_deducible],
    ["Gastos no deducibles", gasto_no_deducible],
    ["Flujo neto", flujo_neto],
    ["IVA trasladado", iva_trasladado],
    ["IVA acreditable", iva_acreditable],
    ["IVA neto", iva_neto],
    ["ISR retenido", isr_total],
    ["Partidas pendientes", pending],
    ["Índice General de Control", score / 100],
    ["Clasificación", risk],
], columns=["Indicador", "Valor"])

export_sheets = {
    "Facturas": fact_p,
    "Gastos": gas_p,
    "Bancos": ban_p,
    "ISR": isr_p,
    "Conciliación": pd.concat([
        conc_fact.assign(tipo_documento="Factura"),
        conc_gas.rename(columns={"folio_gasto": "folio", "proveedor": "cliente"}).assign(tipo_documento="Gasto")
    ], ignore_index=True, sort=False),
    "Alertas": alerts,
}

excel_bytes = to_excel_bytes(export_sheets, summary_df)
st.download_button(
    "Descargar reporte Excel de auditoría",
    data=excel_bytes,
    file_name=f"Reporte_Control_Fiscal_Auditoria_{start_date}_{end_date}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
)

st.caption("Modelo didáctico. Las coincidencias probables y alertas deben revisarse con evidencia documental antes de utilizarlas como conclusión formal de auditoría o determinación fiscal.")
