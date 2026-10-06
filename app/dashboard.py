import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st


DATABASE_PATH = Path("data/juli.db")


def load_snapshots() -> pd.DataFrame:
    """Load system snapshots from SQLite."""
    connection = sqlite3.connect(DATABASE_PATH)

    query = """
        SELECT
            timestamp,
            cpu_usage,
            memory_usage,
            disk_usage
        FROM system_snapshots
        ORDER BY timestamp
    """

    data = pd.read_sql_query(query, connection)
    connection.close()

    return data


st.set_page_config(
    page_title="JULI Dashboard",
    page_icon="🌼",
    layout="wide",
)

st.title("🌼 JULI")
st.subheader("System Dashboard")


@st.fragment(run_every="5s")
def dashboard():
    data = load_snapshots()

    if data.empty:
        st.warning("No system snapshots available.")
        return

    latest = data.iloc[-1]

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("CPU", f"{latest['cpu_usage']:.1f}%")

    with col2:
        st.metric("RAM", f"{latest['memory_usage']:.1f}%")

    with col3:
        st.metric("Disk", f"{latest['disk_usage']:.1f}%")

    st.divider()

    st.subheader("System Usage")

    chart_data = data.set_index("timestamp")[
        ["cpu_usage", "memory_usage", "disk_usage"]
    ]

    chart_data.columns = ["CPU", "RAM", "Disk"]

    st.line_chart(chart_data)

    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        st.write(f"**Snapshots collected:** {len(data)}")

    with col2:
        st.write(f"**Last update:** {latest['timestamp']}")


dashboard()