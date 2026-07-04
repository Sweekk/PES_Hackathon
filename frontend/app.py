import streamlit as st
import pandas as pd
import requests
import json
import os

# Page Configuration for Premium UI/UX
st.set_page_config(
    page_title="BankLens AI — Premium Statement Audit & Guardrail Platform",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Dark Mode custom styling using custom CSS
st.markdown("""
<style>
    .reportview-container {
        background: #0f111a;
        color: #e3e6ed;
    }
    .sidebar .sidebar-content {
        background: #171a26;
    }
    h1, h2, h3 {
        color: #00ffd0 !important;
        font-family: 'Inter', sans-serif;
    }
    .stAlert {
        border-radius: 10px;
    }
    /* Custom Neon Borders and Cards */
    .premium-card {
        background-color: #1c2030;
        border: 1px solid #2e354f;
        padding: 20px;
        border-radius: 12px;
        box-shadow: 0 4px 20px rgba(0, 255, 208, 0.05);
        margin-bottom: 20px;
    }
    .status-badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 20px;
        font-weight: bold;
        font-size: 0.85em;
    }
    .status-safe {
        background-color: rgba(0, 255, 128, 0.15);
        color: #00ff80;
        border: 1px solid #00ff80;
    }
    .status-threat {
        background-color: rgba(255, 64, 64, 0.15);
        color: #ff4040;
        border: 1px solid #ff4040;
    }
</style>
""", unsafe_allow_html=True)

# App Setup and Logo
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
logo_path = os.path.join(ASSETS_DIR, "logo.png")

with st.sidebar:
    if os.path.exists(logo_path):
        st.image(logo_path, use_column_width=True)
    else:
        st.title("BankLens AI")
    st.markdown("---")
    
    st.header("⚙️ Configuration")
    use_chandra = st.checkbox("Enable Chandra OCR (PDF Scans)", value=False)
    chandra_url = st.text_input("Chandra vLLM API Base", "http://localhost:8000/v1")
    backend_url = st.text_input("FastAPI Backend URL", "http://localhost:8000")
    
    st.markdown("---")
    st.markdown("🧬 **Powered by Antigravity & Ollama**")

st.title("🔍 BankLens AI")
st.markdown("### Modular Bank Statement Parsing, Threat Guardrails & Financial Audits")

# Upload Area
st.markdown('<div class="premium-card">', unsafe_allow_html=True)
uploaded_file = st.file_uploader(
    "Upload Bank Statement (PDF, CSV, XLS, XLSX)",
    type=["pdf", "csv", "xls", "xlsx"]
)
st.markdown('</div>', unsafe_allow_html=True)

if uploaded_file is not None:
    if st.button("🚀 Process Statement Document", use_container_width=True):
        with st.spinner("Processing through Modular pipeline..."):
            try:
                # Prepare payload
                files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
                data = {
                    "use_chandra": str(use_chandra).lower(),
                    "chandra_api": chandra_url
                }
                
                # Post request to FastAPI
                res = requests.post(f"{backend_url}/api/upload", files=files, data=data)
                
                if res.status_code == 200:
                    result = res.json()
                    st.success("🎉 Pipeline executed successfully!")
                    
                    st.session_state["pipeline_results"] = result
                else:
                    st.error(f"Execution Error: {res.text}")
            except Exception as ex:
                st.error(f"Could not connect to FastAPI Backend at {backend_url}. Verify the server is running. Details: {ex}")

# Render results if present in session state
if "pipeline_results" in st.session_state:
    results = st.session_state["pipeline_results"]
    
    # ── Pipeline Status Tabs ──
    tab1, tab2, tab3 = st.tabs(["📊 Transaction Ledger", "🛡️ Guardrail Firewall", "📄 Audit Report"])
    
    with tab1:
        if results.get("status") == "success":
            st.subheader("Transaction Ledger")
            df = pd.DataFrame(results.get("dataframe", []))
            if not df.empty:
                st.dataframe(df, use_container_width=True)
                
                # Show file stats in card
                audit_res = results.get("audit_results", [{}])
                first_res = audit_res[0] if audit_res else {}
                st.markdown(f"""
                <div class="premium-card">
                    <h4>Document Properties</h4>
                    <ul>
                        <li><b>File</b>: {first_res.get("filename", "unknown")}</li>
                        <li><b>Total Raw Elements</b>: {first_res.get("total_before", 0)}</li>
                        <li><b>Total Unique Rows</b>: {first_res.get("total_after", 0)}</li>
                    </ul>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.info("No transaction ledger rows parsed.")
        else:
            st.error("Pipeline stopped before ledger extraction due to security constraints.")
            
    with tab2:
        st.subheader("Ollama Prompt Guardrail Firewall")
        guardrail = results.get("guardrail", {})
        
        is_safe = guardrail.get("SAFE_TO_PROCEED") == "YES"
        badge_class = "status-safe" if is_safe else "status-threat"
        badge_text = "PASSED (SAFE)" if is_safe else "BLOCKED (THREAT DETECTED)"
        
        st.markdown(f"""
        <div class="premium-card">
            <h4>Security Status: <span class="status-badge {badge_class}">{badge_text}</span></h4>
            <br>
            <ul>
                <li><b>Validation Status</b>: {guardrail.get("STATUS")}</li>
                <li><b>Prompt Injection Detected</b>: {guardrail.get("INJECTION_DETECTED")}</li>
                <li><b>Injection Type</b>: {guardrail.get("INJECTION_TYPE")}</li>
                <li><b>Tamper Detected</b>: {guardrail.get("TAMPER_DETECTED")}</li>
                <li><b>Tamper Reason</b>: {guardrail.get("TAMPER_REASON")}</li>
                <li><b>Summary</b>: {guardrail.get("SUMMARY")}</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)
        
    with tab3:
        st.subheader("Financial Investigation Audit Report")
        report_content = results.get("report_content", "No report compiled.")
        if "Error contacting Ollama" in report_content:
            st.warning("⚠️ The audit report could not compile because the local Ollama LLM was unreachable. The programmatic analysis results are still saved.")
        st.markdown(report_content)
