import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt

st.set_page_config(
    page_title="Reporte Ejecutivo - Our World in Data",
    layout="wide"
)

st.title("Reporte Ejecutivo con Datos Públicos")
st.caption("Fuente: Our World in Data | Procesamiento: Python + Streamlit")


@st.cache_data
def load_data():
    url = "https://ourworldindata.org/grapher/life-expectancy.csv"
    return pd.read_csv(url)


try:
    data = load_data()

    st.success("Conexión realizada correctamente con el repositorio público.")

    excluded_cols = {"Entity", "Code", "Year"}
    value_columns = [col for col in data.columns if col not in excluded_cols]

    if not value_columns:
        raise ValueError(
            "No se encontró una columna de valores distinta de Entity, Code o Year."
        )

    value_col = value_columns[0]

    countries = sorted(data["Entity"].dropna().unique().tolist())

    default_countries = [
        country
        for country in ["Mexico", "United States", "Spain"]
        if country in countries
    ]

    selected_countries = st.sidebar.multiselect(
        "Selecciona países",
        options=countries,
        default=default_countries
    )

    available_years = sorted(data["Year"].dropna().astype(int).unique().tolist())

    min_year = min(available_years)
    max_year = max(available_years)
    initial_start_year = max(min_year, max_year - 30)

    selected_years = st.sidebar.slider(
        "Rango de años",
        min_value=min_year,
        max_value=max_year,
        value=(initial_start_year, max_year)
    )

    filtered = data[
        data["Entity"].isin(selected_countries)
        & data["Year"].between(selected_years[0], selected_years[1])
    ].copy()

    st.subheader("Indicadores principales")

    if not filtered.empty:
        latest = (
            filtered.sort_values("Year")
            .groupby("Entity", as_index=False)
            .tail(1)
            .copy()
        )

        metric_columns = st.columns(len(latest))

        for col, (_, row) in zip(metric_columns, latest.iterrows()):
            col.metric(
                label=row["Entity"],
                value=f"{row[value_col]:.1f} años"
            )
    else:
        latest = pd.DataFrame(columns=filtered.columns)
        st.info("No existen datos para los filtros seleccionados.")

    st.subheader("Evolución histórica")

    if not filtered.empty:
        fig, ax = plt.subplots(figsize=(10, 5))

        for country in selected_countries:
            country_data = filtered[
                filtered["Entity"] == country
            ].sort_values("Year")

            if not country_data.empty:
                ax.plot(
                    country_data["Year"],
                    country_data[value_col],
                    label=country
                )

        ax.set_xlabel("Año")
        ax.set_ylabel("Esperanza de vida")
        ax.legend()
        ax.grid(True, alpha=0.3)

        st.pyplot(fig)
        plt.close(fig)
    else:
        st.info("No existen datos para los filtros seleccionados.")

    st.subheader("Tabla de datos")
    st.dataframe(filtered, use_container_width=True)

    csv_data = filtered.to_csv(index=False).encode("utf-8")

    st.download_button(
        label="Descargar datos filtrados en CSV",
        data=csv_data,
        file_name="reporte_esperanza_vida.csv",
        mime="text/csv"
    )

    st.subheader("Conclusión automática")

    if not latest.empty:
        max_row = latest.loc[latest[value_col].idxmax()]
        min_row = latest.loc[latest[value_col].idxmin()]

        conclusion = (
            f"En el último año disponible dentro del rango seleccionado, "
            f"{max_row['Entity']} presenta el valor más alto "
            f"({max_row[value_col]:.1f} años), mientras que "
            f"{min_row['Entity']} registra {min_row[value_col]:.1f} años."
        )

        st.write(conclusion)
    else:
        st.info("No existen datos para generar una conclusión.")

except Exception as e:
    st.error("No fue posible cargar o procesar la información.")
    st.exception(e)
