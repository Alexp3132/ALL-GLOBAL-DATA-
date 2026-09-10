import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt

# ---------------------------------------------------------------------------
# A. Configuración inicial
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Reporte Ejecutivo - Our World in Data",
    layout="wide",
)

st.title("Reporte Ejecutivo con Datos Públicos")
st.caption("Fuente: Our World in Data | Procesamiento: Python + Streamlit")

# ---------------------------------------------------------------------------
# B. Fuente de datos
# ---------------------------------------------------------------------------
CSV_URL = "https://ourworldindata.org/grapher/life-expectancy.csv"


@st.cache_data
def load_data(url: str) -> pd.DataFrame:
    """Descarga el CSV público de Our World in Data y lo devuelve como DataFrame."""
    return pd.read_csv(url)


# ---------------------------------------------------------------------------
# C. Manejo de errores + carga y procesamiento principal
# ---------------------------------------------------------------------------
try:
    df = load_data(CSV_URL)
    st.success("Conexión realizada correctamente con el repositorio público.")

    # -----------------------------------------------------------------------
    # D. Identificación automática de la variable de valores
    # -----------------------------------------------------------------------
    base_cols = {"Entity", "Code", "Year"}
    value_candidates = [c for c in df.columns if c not in base_cols]
    if not value_candidates:
        raise ValueError("No se encontró una columna numérica de valores en el dataset.")
    value_col = value_candidates[0]

    # -----------------------------------------------------------------------
    # E. Selector de países
    # -----------------------------------------------------------------------
    countries = sorted(df["Entity"].dropna().unique().tolist())

    default_countries = [c for c in ["Mexico", "United States", "Spain"] if c in countries]

    selected_countries = st.sidebar.multiselect(
        "Selecciona países",
        options=countries,
        default=default_countries,
    )

    # -----------------------------------------------------------------------
    # F. Selector de periodo
    # -----------------------------------------------------------------------
    years = df["Year"].dropna().astype(int)
    min_year = int(years.min())
    max_year = int(years.max())

    default_start = max(min_year, max_year - 30)

    selected_range = st.sidebar.slider(
        "Rango de años",
        min_value=min_year,
        max_value=max_year,
        value=(default_start, max_year),
    )
    start_year, end_year = selected_range

    # -----------------------------------------------------------------------
    # G. Filtrado
    # -----------------------------------------------------------------------
    filtered = df[
        (df["Entity"].isin(selected_countries))
        & (df["Year"] >= start_year)
        & (df["Year"] <= end_year)
    ].copy()

    if filtered.empty:
        st.warning("No existen datos para los filtros seleccionados.")
    else:
        # -------------------------------------------------------------------
        # H. Indicadores principales
        # -------------------------------------------------------------------
        st.subheader("Indicadores principales")

        latest = (
            filtered.sort_values("Year")
            .groupby("Entity", as_index=False)
            .last()
        )

        n_cols = max(1, len(latest))
        cols = st.columns(n_cols)
        for col, (_, row) in zip(cols, latest.iterrows()):
            col.metric(
                label=row["Entity"],
                value=f"{row[value_col]:.1f} años",
            )

        # -------------------------------------------------------------------
        # I. Evolución histórica
        # -------------------------------------------------------------------
        st.subheader("Evolución histórica")

        fig, ax = plt.subplots(figsize=(10, 5))
        for country in selected_countries:
            data_country = filtered[filtered["Entity"] == country].sort_values("Year")
            if not data_country.empty:
                ax.plot(
                    data_country["Year"],
                    data_country[value_col],
                    label=country,
                )

        ax.set_xlabel("Año")
        ax.set_ylabel("Esperanza de vida")
        ax.legend()
        ax.grid(True, alpha=0.3)
        st.pyplot(fig)

        # -------------------------------------------------------------------
        # J. Tabla de datos
        # -------------------------------------------------------------------
        st.subheader("Tabla de datos")
        st.dataframe(filtered, use_container_width=True)

        # -------------------------------------------------------------------
        # K. Descarga
        # -------------------------------------------------------------------
        csv_data = filtered.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Descargar datos filtrados en CSV",
            data=csv_data,
            file_name="reporte_esperanza_vida.csv",
            mime="text/csv",
        )

        # -------------------------------------------------------------------
        # L. Conclusión automática
        # -------------------------------------------------------------------
        st.subheader("Conclusión automática")

        if not latest.empty:
            idx_max = latest[value_col].idxmax()
            idx_min = latest[value_col].idxmin()

            top_country = latest.loc[idx_max, "Entity"]
            top_value = latest.loc[idx_max, value_col]
            bottom_country = latest.loc[idx_min, "Entity"]
            bottom_value = latest.loc[idx_min, value_col]

            st.write(
                f"En el último año disponible dentro del rango seleccionado, "
                f"**{top_country}** presenta el valor más alto "
                f"({top_value:.1f} años), mientras que "
                f"**{bottom_country}** registra {bottom_value:.1f} años."
            )

except Exception as e:
    st.error("No fue posible cargar o procesar la información.")
    st.exception(e)