import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from app.agent.core import AgentCore


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

st.divider()
st.subheader("JULI Assistant")
st.caption("Local Ollama model · Monitoring evidence · Read-only diagnostics")

if "juli_agent" not in st.session_state:
    st.session_state.juli_agent = AgentCore()
if "juli_chat_messages" not in st.session_state:
    st.session_state.juli_chat_messages = []

for chat_message in st.session_state.juli_chat_messages:
    with st.chat_message(chat_message["role"]):
        st.markdown(chat_message["content"])

if prompt := st.chat_input("Ask JULI about the system or its monitoring evidence"):
    st.session_state.juli_chat_messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    result = st.session_state.juli_agent.handle_message(prompt)
    if result["status"] == "success":
        assistant_text = str(result["response"])
        st.session_state.juli_chat_messages.append({
            "role": "assistant",
            "content": assistant_text,
        })
        with st.chat_message("assistant"):
            st.markdown(assistant_text)
            if result.get("monitoring_issues"):
                st.caption("Some Monitoring data is incomplete; JULI was given the available quality issues.")
    else:
        error = result.get("error") or {}
        assistant_text = (
            f"Local assistant error ({error.get('code', 'UNKNOWN')}): "
            f"{error.get('message', 'No response was available.')}"
        )
        st.session_state.juli_chat_messages.append({
            "role": "assistant",
            "content": assistant_text,
        })
        with st.chat_message("assistant"):
            st.error(assistant_text)
