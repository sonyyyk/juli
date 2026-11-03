import pandas as pd
import streamlit as st

from app.agent.core import AgentCore
from app.core.database import ensure_schema
from app.monitoring.history_reader import count_snapshots, load_recent_snapshots


DASHBOARD_SNAPSHOT_LIMIT = 2000
SNAPSHOT_COLUMNS = ["timestamp", "cpu_usage", "memory_usage", "disk_usage"]

ensure_schema()


def load_snapshots() -> pd.DataFrame:
    """Load the latest snapshots from SQLite in chronological order."""
    rows = load_recent_snapshots(limit=DASHBOARD_SNAPSHOT_LIMIT)

    return pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)


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
        total = count_snapshots()
        st.write(
            f"**Snapshots collected:** {total} "
            f"(chart: latest {min(total, DASHBOARD_SNAPSHOT_LIMIT)})"
        )

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
