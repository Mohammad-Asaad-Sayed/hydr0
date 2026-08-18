import streamlit as st
import os
import time
from datetime import datetime, timezone

from hydramem import HydraMemory

st.set_page_config(page_title="HydraMem Demo", page_icon="🧠", layout="wide")

st.markdown("""
<style>
    .state-answerable { color:#10b981; font-weight:bold; font-size:1.1em; }
    .state-conflicting { color:#ef4444; font-weight:bold; font-size:1.1em; }
    .state-no-evidence { color:#6b7280; font-weight:bold; font-size:1.1em; }
    .revision-chain { background:linear-gradient(90deg,#3b82f6,#8b5cf6);
        padding:12px; border-radius:8px; color:white; margin:8px 0; font-weight:600; }
    .session-log { background:#f9fafb; border-left:4px solid #10b981;
        padding:10px; margin:6px 0; border-radius:4px; font-size:0.9em; }
</style>
""", unsafe_allow_html=True)

# ---- session state ----
if "memory" not in st.session_state:
    st.session_state.memory = None
if "ingest_log" not in st.session_state:
    st.session_state.ingest_log = []   # local record of what the user added this session

st.title("🧠 HydraMem: Graph-Native Temporal Memory")
st.markdown("**Deterministic agent memory with explicit revision chains and correct abstention**")

# ============================ SIDEBAR ============================
with st.sidebar:
    st.header("⚙️ Configuration")
    # Read from Streamlit secrets (cloud) or env var (local), fallback to manual input
    _default_key = ""
    try:
        _default_key = st.secrets.get("HYDRA_DB_API_KEY", "")
    except Exception:
        _default_key = os.getenv("HYDRA_DB_API_KEY", "")
    
    api_key = st.text_input("HydraDB API Key", type="password",
                            value=_default_key,
                            help="Pre-filled on deployed demo. Enter your own key for local use.")
    database_name = st.text_input("Database Name", value="hydramem_streamlit_v3")

    if st.button("🚀 Initialize Memory", use_container_width=True):
        if not api_key:
            st.error("Please enter your API key")
        else:
            with st.spinner("Initializing..."):
                try:
                    st.session_state.memory = HydraMemory(database=database_name, api_key=api_key)
                    st.success("✅ Memory initialized!")
                except Exception as e:
                    st.error(f"Failed to initialize: {e}")

    st.divider()
    # ONE user drives both ingestion and queries -> no subject mismatch
    active_user = st.text_input("👤 Active User ID", value="alice",
                                help="All memories you add and all queries run under this subject.")

if st.session_state.memory is None:
    st.info("👈 Enter your API key and click **Initialize Memory** to start.")
    st.stop()

# ============================ MAIN ============================
col_add, col_query = st.columns([1, 1])

# ---------------- LEFT: ADD MEMORY ----------------
with col_add:
    st.header("📝 Add Memory")

    tab_demo, tab_custom = st.tabs(["🎬 Demo Scenario", "✍️ Custom Input"])

    with tab_demo:
        st.caption("Loads a 3-session story (NYC → London revision + preference) under the Active User.")
        if st.button("Load Demo Scenario", use_container_width=True):
            demo_sessions = [
                {"session_id": f"{active_user}_s1", "user_id": active_user,
                 "timestamp": datetime(2026, 8, 1, tzinfo=timezone.utc),
                 "messages": [{"role": "user", "content": "Hey, I'm based in New York these days."}]},
                {"session_id": f"{active_user}_s2", "user_id": active_user,
                 "timestamp": datetime(2026, 8, 5, tzinfo=timezone.utc),
                 "messages": [{"role": "user", "content": "I really prefer dark mode for all my apps."}]},
                {"session_id": f"{active_user}_s3", "user_id": active_user,
                 "timestamp": datetime(2026, 8, 10, tzinfo=timezone.utc),
                 "messages": [{"role": "user", "content": "Quick update: I relocated to London for work."}]},
            ]
            with st.spinner("Ingesting & building graph..."):
                ids = st.session_state.memory.add_sessions(demo_sessions)
            st.session_state.ingest_log.append(
                f"🎬 Demo scenario for '{active_user}' ({len(ids)} facts)")
            st.success(f"✅ Ingested {len(ids)} facts with SUPERSEDES edges!")

    with tab_custom:
        st.caption("Type anything. HydraMem extracts facts, links revisions, and stores them in the graph.")
        cust_session = st.text_input("Session ID", value=f"sess_{int(time.time())}")
        cust_text = st.text_area(
            "Conversation turn (user message)",
            placeholder="e.g. I just moved to Paris and my salary is $150k",
            height=90,
        )
        if st.button("➕ Add This Memory", use_container_width=True) and cust_text.strip():
            with st.spinner("Extracting & ingesting..."):
                ids = st.session_state.memory.add(
                    messages=[{"role": "user", "content": cust_text.strip()}],
                    user_id=active_user,
                    session_id=cust_session,
                    timestamp=datetime.now(timezone.utc),
                )
            if ids:
                st.session_state.ingest_log.append(
                    f"✍️ '{cust_text.strip()[:60]}...' → {len(ids)} fact(s)")
                st.success(f"✅ Added {len(ids)} fact(s) for '{active_user}'.")
            else:
                st.warning("⚠️ No facts extracted — try more explicit phrasing "
                           "(e.g. 'I live in...', 'My job is...', 'I prefer...').")

    # Ingest log
    if st.session_state.ingest_log:
        st.subheader("🗂️ Added This Session")
        for entry in st.session_state.ingest_log:
            st.markdown(f'<div class="session-log">{entry}</div>', unsafe_allow_html=True)

# ---------------- RIGHT: QUERY MEMORY ----------------
with col_query:
    st.header("🔍 Query Memory")
    st.caption(f"Resolving against subject **{active_user}**.")

    query_options = {
        "Where does the user live?": "Temporal revision",
        "What does the user prefer?": "Static fact isolation",
        "What is the user's favorite pet?": "Correct abstention",
        "Custom query...": "Ask anything",
    }
    selected = st.selectbox("Select a query", list(query_options.keys()),
        format_func=lambda x: f"{x} — {query_options[x]}" if x != "Custom query..." else x)

    if selected == "Custom query...":
        query = st.text_input("Enter your question", placeholder="Where does the user live?")
    else:
        query = selected
        st.info(f"**Scenario:** {query_options[selected]}")

    if st.button("🎯 Query", type="primary", use_container_width=True, disabled=not query):
        with st.spinner("Querying with graph context..."):
            result = st.session_state.memory.search(query, user_id=active_user)

            state = result["state"]
            css = {"ANSWERABLE": "state-answerable",
                   "CONFLICTING": "state-conflicting"}.get(state, "state-no-evidence")
            st.markdown(f'<p class="{css}">{state}</p>', unsafe_allow_html=True)

            if result["answer"]:
                st.markdown(f"### Answer: **{result['answer']}**")
            else:
                st.markdown("### Answer: *No answer available*")

            st.markdown(f"**Reason:** {result['reason']}")
            st.caption(f"Candidates retrieved: {result['retrieved_candidates']}"
                       f" · Target: `{result['target']}`")

            if result.get("revision_chain"):
                st.subheader("🔗 Revision Chain")
                chain_html = '<div class="revision-chain">'
                chain_html += " → ".join(s["value"] for s in result["revision_chain"])
                chain_html += "</div>"
                st.markdown(chain_html, unsafe_allow_html=True)

            if result.get("multi_hop"):
                st.info("🌐 Multi-hop query — resolved by walking graph edges across entities.")

# ============================ FOOTER ============================
st.divider()
c1, c2, c3 = st.columns(3)
c1.markdown("#### 🎯 Deterministic Truth\nWalks explicit `SUPERSEDES` edges. No hallucination.")
c2.markdown("#### 🚫 Correct Abstention\nReturns `NO_EVIDENCE` instead of inventing answers.")
c3.markdown("#### 🌐 Multi-Hop Reasoning\nTraverses relationships across entities.")

st.markdown("---")
st.markdown(
    "<div style='text-align:center;color:#6b7280;'>Built with "
    "<a href='https://hydradb.com'>HydraDB</a> for "
    "<a href='https://hackhydra.hydradb.com'>Hack Hydra 2026</a> · Track 3</div>",
    unsafe_allow_html=True,
)
