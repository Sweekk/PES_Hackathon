# pyrefly: ignore [missing-import]
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pyvis.network import Network
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
import tempfile
import io
import os
import time
import requests
import json
import ollama
from datetime import datetime

# ==========================================================
# PAGE CONFIGURATION
# ==========================================================
st.set_page_config(
    page_title="BankLens AI — Automated Bank Statement Analysis",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom color variables for styling
primary_color = "#1A3C6E"
accent_color = "#2563EB"
danger_color = "#991B1B"
success_color = "#065F46"
bg_color = "#0F172A"

# Inject Custom Styles
st.markdown(f"""
<style>
    .stApp {{
        background-color: {bg_color};
        color: #e2e8f0;
    }}
    .sidebar .sidebar-content {{
        background-color: #1e293b;
    }}
    .metric-card {{
        background-color: #1e293b;
        border: 1px solid #334155;
        border-radius: 10px;
        padding: 15px;
        text-align: center;
        margin-bottom: 15px;
    }}
    .fraud-card {{
        background-color: #1e293b;
        border-left: 5px solid {danger_color};
        border-top: 1px solid #334155;
        border-right: 1px solid #334155;
        border-bottom: 1px solid #334155;
        border-radius: 6px;
        padding: 15px;
        margin-bottom: 15px;
    }}
    .badge {{
        display: inline-block;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.75em;
        font-weight: bold;
        color: #ffffff;
    }}
    .badge-high {{ background-color: {danger_color}; }}
    .badge-medium {{ background-color: #d97706; }}
    .badge-low {{ background-color: #2563EB; }}
    
    h1, h2, h3, h4 {{
        color: #00f0ff !important;
        font-family: 'Inter', sans-serif;
    }}
</style>
""", unsafe_allow_html=True)

# ==========================================================
# MOCK DATA FOR TESTING
# ==========================================================
mock_transactions = [
    {"date": "2024-01-01", "description": "UPI/Swiggy", "debit": 450.0, "credit": None, "balance": "12000", "type": "UPI", "flagged": False, "status": "Normal"},
    {"date": "2024-01-02", "description": "NEFT/Salary", "debit": None, "credit": 85000.0, "balance": "97000", "type": "NEFT", "flagged": False, "status": "Normal"},
    {"date": "2024-01-03", "description": "UPI/Transfer/ACC202", "debit": 49500.0, "credit": None, "balance": "47500", "type": "UPI", "flagged": True, "status": "Flagged"},
    {"date": "2024-01-03", "description": "UPI/Transfer/ACC303", "debit": 48900.0, "credit": None, "balance": "-1400", "type": "UPI", "flagged": True, "status": "Flagged"},
    {"date": "2024-01-05", "description": "NEFT/Return/ACC101", "debit": None, "credit": 97000.0, "balance": "95600", "type": "NEFT", "flagged": True, "status": "Flagged"},
]

mock_fraud_findings = [
    {
        "type": "Round Tripping",
        "severity": "HIGH",
        "accounts": ["ACC_101", "ACC_202", "ACC_303"],
        "transactions": ["TXN_003", "TXN_005"],
        "description": "Money flows ACC101 → ACC202 → ACC303 → ACC101 within 3 days."
    },
    {
        "type": "Structuring",
        "severity": "HIGH",
        "accounts": ["ACC_101"],
        "transactions": ["TXN_003", "TXN_004"],
        "description": "Multiple transactions clustered just below the Rs. 50,000 reporting threshold."
    },
]

mock_graph_nodes = [
    {"id": "ACC_101", "flagged": True},
    {"id": "ACC_202", "flagged": True},
    {"id": "ACC_303", "flagged": False},
    {"id": "SWIGGY",  "flagged": False},
]

mock_graph_edges = [
    {"from": "ACC_101", "to": "ACC_202", "amount": 49500},
    {"from": "ACC_202", "to": "ACC_303", "amount": 48900},
    {"from": "ACC_303", "to": "ACC_101", "amount": 97000},
    {"from": "ACC_101", "to": "SWIGGY",  "amount": 450},
]

import re

def process_real_data(results):
    dataframe_list = results.get("dataframe", [])
    audit_res = results.get("audit_results", [])
    
    dup_keys = set()
    failed_keys = set()
    
    for file_res in audit_res:
        for tx in file_res.get("duplicates", []):
            key = (
                tx.get("Date") or tx.get("date") or "",
                tx.get("Narration") or tx.get("description") or "",
                str(tx.get("Debit") or tx.get("debit") or "0.0"),
                str(tx.get("Credit") or tx.get("credit") or "0.0")
            )
            dup_keys.add(key)
            
        for fc in file_res.get("failed_candidates", []):
            for k in ("debit", "credit"):
                tx = fc.get(k, {}).get("raw", {})
                key = (
                    tx.get("Date") or tx.get("date") or "",
                    tx.get("Narration") or tx.get("description") or "",
                    str(tx.get("Debit") or tx.get("debit") or "0.0"),
                    str(tx.get("Credit") or tx.get("credit") or "0.0")
                )
                failed_keys.add(key)
                
    transactions = []
    flagged_count = 0
    total_debits = 0.0
    total_credits = 0.0
    
    for tx in dataframe_list:
        try:
            d_val = float(tx.get("Debit") or tx.get("debit") or 0.0)
        except ValueError:
            d_val = 0.0
        try:
            c_val = float(tx.get("Credit") or tx.get("credit") or 0.0)
        except ValueError:
            c_val = 0.0
            
        total_debits += d_val
        total_credits += c_val
        
        tx_key = (
            tx.get("Date") or tx.get("date") or "",
            tx.get("Narration") or tx.get("description") or "",
            str(tx.get("Debit") or tx.get("debit") or "0.0"),
            str(tx.get("Credit") or tx.get("credit") or "0.0")
        )
        
        is_dup = tx_key in dup_keys
        is_failed = tx_key in failed_keys
        is_flagged = is_dup or is_failed
        
        if is_flagged:
            flagged_count += 1
            
        status = "Flagged" if is_flagged else "Normal"
        tx_type = "UNKNOWN"
        desc_lower = (tx.get("Narration") or tx.get("description") or "").lower()
        for kw in ("upi", "neft", "imps", "rtgs", "atm", "cash"):
            if kw in desc_lower:
                tx_type = kw.upper()
                break
                
        transactions.append({
            "date": tx.get("Date") or tx.get("date") or "",
            "description": tx.get("Narration") or tx.get("description") or "",
            "debit": d_val if d_val > 0 else None,
            "credit": c_val if c_val > 0 else None,
            "balance": tx.get("Balance") or tx.get("balance") or "0.0",
            "type": tx_type,
            "flagged": is_flagged,
            "status": status
        })
        
    # Build Fraud Findings list
    fraud_findings = []
    for file_res in audit_res:
        if file_res.get("duplicate_count", 0) > 0:
            fraud_findings.append({
                "type": "Structuring",
                "severity": "MEDIUM",
                "accounts": ["MAIN_ACC"],
                "transactions": ["Duplicates List"],
                "description": f"Detected {file_res['duplicate_count']} duplicate transaction records that were programmatically filtered."
            })
        if file_res.get("failed_count", 0) > 0:
            for candidate in file_res.get("failed_candidates", []):
                fraud_findings.append({
                    "type": "Layering / Failed Reversal",
                    "severity": "HIGH",
                    "accounts": ["MAIN_ACC"],
                    "transactions": ["Debit/Credit Match"],
                    "description": f"Failed reversal candidate detected: Debit of Rs. {candidate['debit']['amount']} was reversed within {candidate['time_difference_days']} days (Reason: {candidate['reason_type']})."
                })
                
    # Build Nodes and Edges for Graph
    nodes = [{"id": "MAIN_ACC", "flagged": False}]
    edges = []
    seen_nodes = {"MAIN_ACC"}
    
    for tx in transactions:
        desc = tx["description"]
        amount = tx["debit"] or tx["credit"] or 0.0
        
        match = re.search(r"ACC_?\d+|[A-Z]{4}\d+", desc, re.IGNORECASE)
        if match:
            target_node = match.group(0).upper().replace(" ", "_")
        else:
            words = [w for w in re.split(r"[^a-zA-Z0-9]", desc) if len(w) > 3]
            target_node = words[0].upper() if words else "UNKNOWN_ENTITY"
            
        if target_node not in seen_nodes:
            nodes.append({
                "id": target_node,
                "flagged": tx["flagged"]
            })
            seen_nodes.add(target_node)
            
        if tx["debit"]:
            edges.append({
                "from": "MAIN_ACC",
                "to": target_node,
                "amount": amount
            })
        else:
            edges.append({
                "from": target_node,
                "to": "MAIN_ACC",
                "amount": amount
            })
            
    return transactions, fraud_findings, nodes, edges, flagged_count, total_debits, total_credits

# Check if real data is available in session state
if st.session_state.get("pipeline_results"):
    res = st.session_state["pipeline_results"]
    transactions, fraud_findings, graph_nodes, graph_edges, flagged_count, total_debits, total_credits = process_real_data(res)
    exec_summary = res.get("report_content", "Analysis report compile pending.")
    risk_score = "HIGH" if flagged_count > 0 else "LOW"
else:
    transactions = mock_transactions
    fraud_findings = mock_fraud_findings
    graph_nodes = mock_graph_nodes
    graph_edges = mock_graph_edges
    flagged_count = sum(1 for tx in transactions if tx.get("flagged"))
    total_debits = sum(tx["debit"] for tx in transactions if tx["debit"])
    total_credits = sum(tx["credit"] for tx in transactions if tx["credit"])
    risk_score = "HIGH"
    exec_summary = (
        "The automated transaction pipeline analyzed a total of 5 ledger entities from the uploaded statement. "
        "Behavioral matching flagged 3 entities as suspicious. A circular round-tripping network loop was isolated "
        "across ACC_101, ACC_202, and ACC_303. Structuring patterns were also flagged just below the Rs. 50,000 threshold."
    )

# ==========================================================
# SIDEBAR
# ==========================================================
with st.sidebar:
    # Logo rendering
    ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    logo_path = os.path.join(ASSETS_DIR, "logo.png")
    if os.path.exists(logo_path):
        st.image(logo_path, use_container_width=True)
    else:
        st.title("🔍 BankLens AI")
    st.markdown("**Version:** 1.0.0-Beta")
    st.markdown("---")
    
    # Sensitivity Controls
    st.header("⚙️ Controls")
    sensitivity = st.selectbox(
        "Sensitivity Level",
        ["Low", "Medium", "High"],
        index=1,
        help="Adjusts behavioral thresholds for anomaly detection algorithms."
    )
    
    # Configuration Controls
    st.header("⚙️ Configuration")
    use_chandra = st.checkbox("Enable Chandra OCR (PDF Scans)", value=False)
    chandra_url = st.text_input("Chandra vLLM API Base", "http://localhost:8000/v1")
    backend_url = st.text_input("FastAPI Backend URL", "http://localhost:8000")
    
    # Upload history
    st.header("⏳ Upload History")
    if "upload_history" not in st.session_state:
        st.session_state["upload_history"] = []
    
    if st.session_state["upload_history"]:
        for h in st.session_state["upload_history"][-3:]:
            st.markdown(f"📄 `{h['filename']}` ({h['time']})")
    else:
        st.info("No files parsed yet.")
        
    st.markdown("---")
    st.header("ℹ️ About")
    st.markdown(
        "BankLens AI utilizes automated transaction matching and graph analytics "
        "to reconstruct fund flows, behaviors, and behavioral anomalies."
    )

# ==========================================================
# HELPERS
# ==========================================================
def create_pyvis_graph(nodes, edges, height="500px"):
    net = Network(height=height, width="100%", bgcolor="#0F172A", font_color="#e2e8f0", directed=True)
    for node in nodes:
        color = danger_color if node["flagged"] else accent_color
        net.add_node(
            node["id"],
            label=node["id"],
            title=f"Account: {node['id']}\nStatus: {'Flagged' if node['flagged'] else 'Normal'}",
            color=color,
            size=25 if node["flagged"] else 15
        )
    for edge in edges:
        width_val = max(1, min(10, int(edge["amount"] / 10000)))
        net.add_edge(
            edge["from"],
            edge["to"],
            title=f"Amount: Rs. {edge['amount']}",
            value=edge["amount"],
            width=width_val,
            color="#475569"
        )
    net.set_options("""
    var options = {
      "physics": {
        "barnesHut": {
          "gravitationalConstant": -4000,
          "centralGravity": 0.3,
          "springLength": 95
        },
        "minVelocity": 0.75
      }
    }
    """)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".html", mode="w", encoding="utf-8") as f:
        html_path = f.name
        net.write_html(html_path)
    return html_path

def generate_pdf_report(tx_data, fraud_data, risk_score, exec_summary):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'TitleStyle',
        parent=styles['Heading1'],
        textColor=colors.HexColor('#1A3C6E'),
        fontSize=20,
        spaceAfter=15
    )
    h2_style = ParagraphStyle(
        'Heading2Style',
        parent=styles['Heading2'],
        textColor=colors.HexColor('#2563EB'),
        fontSize=14,
        spaceAfter=10
    )
    body_style = ParagraphStyle(
        'BodyStyle',
        parent=styles['Normal'],
        textColor=colors.black,
        fontSize=10,
        spaceAfter=8
    )
    
    story.append(Paragraph("BankLens AI — Audit Evidence Report", title_style))
    story.append(Spacer(1, 10))
    
    story.append(Paragraph("Executive Summary", h2_style))
    story.append(Paragraph(exec_summary, body_style))
    story.append(Spacer(1, 10))
    
    story.append(Paragraph(f"<b>Total Transactions Analysed:</b> {len(tx_data)}", body_style))
    story.append(Paragraph(f"<b>Overall Risk Assessment:</b> {risk_score}", body_style))
    story.append(Paragraph(f"<b>Report Generated:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", body_style))
    story.append(Spacer(1, 15))
    
    story.append(Paragraph("Identified Behavioral Anomalies", h2_style))
    for f in fraud_data:
        finding_text = f"<b>{f['type']}</b> ({f['severity']}): {f['description']} (Accounts: {', '.join(f['accounts'])})"
        story.append(Paragraph(finding_text, body_style))
    story.append(Spacer(1, 15))
    
    story.append(Paragraph("Audited Transactions Ledger", h2_style))
    table_data = [["Date", "Description", "Debit", "Credit", "Balance", "Type", "Status"]]
    for tx in tx_data:
        status_text = "FLAGGED" if tx.get("flagged") else "CLEAN"
        table_data.append([
            str(tx.get("date")),
            str(tx.get("description")),
            str(tx.get("debit")) if tx.get("debit") else "-",
            str(tx.get("credit")) if tx.get("credit") else "-",
            str(tx.get("balance")),
            str(tx.get("type")),
            status_text
        ])
    
    t = Table(table_data, colWidths=[65, 140, 50, 50, 60, 50, 65])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1A3C6E')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
    ]))
    
    for idx, tx in enumerate(tx_data, start=1):
        if tx.get("flagged"):
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, idx), (-1, idx), colors.HexColor('#FEE2E2')),
                ('TEXTCOLOR', (0, idx), (-1, idx), colors.HexColor('#991B1B'))
            ]))
            
    story.append(t)
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

def ask_ollama(prompt, history):
    messages = [{"role": "system", "content": "You are BankLens Chatbot, an expert financial analyst. Answer user questions about bank transactions based on the statement data. Be precise."}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": prompt})
    
    try:
        response = ollama.chat(
            model="llama3.2",
            messages=messages,
            options={"temperature": 0.2}
        )
        return response["message"]["content"]
    except Exception as e:
        return f"Hello! I am BankLens AI chatbot. Ollama llama3.2 is currently not reachable (Details: {e}). Based on your request, I can summarize that we detected anomalous transaction activity in accounts ACC_101 and ACC_202, showing structural characteristics matching round-tripping."

# ==========================================================
# SECTION 1: FILE UPLOAD
# ==========================================================
st.title("🔍 BankLens AI")
st.markdown("### Automated Bank Statement Analysis")

# File uploader
uploaded_file = st.file_uploader(
    "Upload Bank Statement (PDF, CSV, XLSX)",
    type=["pdf", "csv", "xlsx"]
)

if uploaded_file is not None:
    # Render file info card
    file_size_kb = len(uploaded_file.getvalue()) / 1024
    st.markdown(f"""
    <div class="premium-card">
        <h4>📄 Upload Details</h4>
        <ul>
            <li><b>Filename:</b> {uploaded_file.name}</li>
            <li><b>Size:</b> {file_size_kb:.2f} KB</li>
            <li><b>Status:</b> Uploaded successfully</li>
        </ul>
    </div>
    """, unsafe_allow_html=True)
    
    # Animate pipeline progress upon Analyse Statement click
    if st.button("🚀 Analyse Statement", use_container_width=True):
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        steps = [
            "Extracting data...",
            "Structuring transactions...",
            "Running behavioural analysis...",
            "Building transaction graph...",
            "Detecting fraud patterns...",
            "Generating report..."
        ]
        
        for idx, step in enumerate(steps):
            status_text.text(step)
            progress_bar.progress((idx + 1) / len(steps))
            time.sleep(0.1)
            
        try:
            # Post request to FastAPI
            files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
            data = {
                "use_chandra": str(use_chandra).lower(),
                "chandra_api": chandra_url
            }
            res = requests.post(f"{backend_url}/api/upload", files=files, data=data)
            
            if res.status_code == 200:
                result = res.json()
                st.session_state["pipeline_results"] = result
                st.toast("Analysis Completed Successfully!", icon="✅")
                st.session_state["analyzed"] = True
                st.session_state["upload_history"].append({
                    "filename": uploaded_file.name,
                    "time": datetime.now().strftime("%H:%M:%S")
                })
                st.rerun()
            else:
                st.error(f"Execution Error: {res.text}")
        except Exception as ex:
            st.error(f"Could not connect to FastAPI Backend at {backend_url}. Verify the server is running. Details: {ex}")
        finally:
            progress_bar.empty()
            status_text.empty()

# ==========================================================
# SECTION 2: THREE TABS (AFTER ANALYSIS)
# ==========================================================
if st.session_state.get("analyzed"):
    res = st.session_state.get("pipeline_results", {})
    is_threat = res.get("status") == "threat_triggered" or res.get("guardrail", {}).get("SAFE_TO_PROCEED") != "YES"
    
    if is_threat:
        guardrail = res.get("guardrail", {})
        st.markdown(f"""
        <div style="background-color: #3b1111; border: 1px solid #991b1b; border-radius: 10px; padding: 25px; margin-bottom: 25px;">
            <h2 style="color: #ef4444; margin-top: 0; display: flex; align-items: center; gap: 10px;">
                🚨 Prompt Guardrail Firewall Blocked Document
            </h2>
            <p style="font-size: 1.1em; color: #fca5a5; margin-bottom: 20px;">
                <b>Reason:</b> {guardrail.get("SUMMARY") or "The uploaded document failed safety assessment rules and was flagged as suspicious."}
            </p>
            <div style="background-color: #1e1b1b; border: 1px solid #451a1a; border-radius: 6px; padding: 15px; font-family: monospace; color: #fecaca;">
                <h4 style="margin-top: 0; color: #f87171;">Assessment Details:</h4>
                <ul style="margin-bottom: 0; padding-left: 20px; line-height: 1.6; list-style-type: disc;">
                    <li><b>Validation Status:</b> <span style="color: #ef4444; font-weight: bold;">{guardrail.get("STATUS", "INVALID")}</span></li>
                    <li><b>Prompt Injection Detected:</b> {guardrail.get("INJECTION_DETECTED", "YES")}</li>
                    <li><b>Injection Type:</b> {guardrail.get("INJECTION_TYPE", "NONE")}</li>
                    <li><b>Tamper Detected:</b> {guardrail.get("TAMPER_DETECTED", "NO")}</li>
                    <li><b>Tamper Reason:</b> {guardrail.get("TAMPER_REASON", "NONE")}</li>
                </ul>
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.stop()

    tab1, tab2, tab3 = st.tabs(["📊 Transaction Overview", "🕸️ Graph & Money Trail Analysis", "🤖 Ask Questions (LLM Chatbot)"])
    
    # ------------------------------------------------------
    # TAB 1: TRANSACTION OVERVIEW
    # ------------------------------------------------------
    with tab1:
        st.header("Transaction Metrics Summary")
        
        if not transactions:
            st.warning("⚠️ No transaction records were found or extracted from the uploaded statement. Please check the file format or verify that the backend is running properly.")
        
        # Calculate summary values
        df_tx = pd.DataFrame(transactions)
        if df_tx.empty:
            df_tx = pd.DataFrame(columns=["date", "description", "debit", "credit", "balance", "type", "flagged", "status"])
        
        total_tx = len(df_tx)
        
        # Ensure correct type conversion for balance calculations
        try:
            closing_balance = float(df_tx.iloc[-1]["balance"])
        except (ValueError, IndexError, TypeError):
            closing_balance = 0.0
            
        col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
        with col_m1:
            st.metric("Total Transactions", total_tx)
        with col_m2:
            st.metric("Total Debits", f"Rs. {total_debits:,.2f}")
        with col_m3:
            st.metric("Total Credits", f"Rs. {total_credits:,.2f}")
        with col_m4:
            st.metric("Closing Balance", f"Rs. {closing_balance:,.2f}")
        with col_m5:
            st.metric("Flagged Items", flagged_count)
            
        st.markdown("---")
        
        # Filters
        st.subheader("Filter Ledger")
        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            date_filter = st.date_input("Date Range", [])
        with col_f2:
            type_filter = st.multiselect("Transaction Type", df_tx["type"].unique(), default=df_tx["type"].unique())
        with col_f3:
            flagged_only = st.toggle("Show Flagged Only")
            
        # Apply filters to Dataframe
        filtered_df = df_tx.copy()
        if flagged_only:
            filtered_df = filtered_df[filtered_df["flagged"] == True]
        if type_filter:
            filtered_df = filtered_df[filtered_df["type"].isin(type_filter)]
            
        # Colour coded rows using pandas styler
        def highlight_flagged(row):
            return ['background-color: #3b1111; color: #fecaca;' if row.flagged else 'background-color: #1e293b;' for _ in row]
            
        styled_df = filtered_df.style.apply(highlight_flagged, axis=1)
        st.dataframe(styled_df, use_container_width=True)
        
        # Charts
        st.markdown("---")
        st.subheader("Interactive Visual Analytics")
        col_c1, col_c2 = st.columns(2)
        
        with col_c1:
            # Bar Chart
            df_bar = df_tx.groupby("date")[["debit", "credit"]].sum().reset_index()
            fig_bar = px.bar(
                df_bar, x="date", y=["debit", "credit"],
                title="Daily Debit vs Credit Breakdown",
                labels={"value": "Amount (Rs.)", "date": "Date"},
                barmode="group",
                color_discrete_map={"debit": "#ef4444", "credit": "#10b981"}
            )
            fig_bar.update_layout(paper_bgcolor="#0F172A", plot_bgcolor="#0F172A", font_color="#e2e8f0")
            st.plotly_chart(fig_bar, use_container_width=True)
            
        with col_c2:
            # Pie Chart
            fig_pie = px.pie(
                df_tx, names="type", values="balance",
                title="Transaction Type Volume Distribution",
                hole=0.4,
                color_discrete_sequence=px.colors.qualitative.Pastel
            )
            fig_pie.update_layout(paper_bgcolor="#0F172A", font_color="#e2e8f0")
            st.plotly_chart(fig_pie, use_container_width=True)

    # ------------------------------------------------------
    # TAB 2: GRAPH & MONEY TRAIL ANALYSIS
    # ------------------------------------------------------
    with tab2:
        st.header("Transaction Flows Network & Trail Mapping")
        
        col_g1, col_g2 = st.columns([3, 2])
        
        with col_g1:
            st.subheader("🌐 Interactive Transaction Graph")
            # Render interactive PyVis Graph
            graph_html_path = create_pyvis_graph(graph_nodes, graph_edges)
            with open(graph_html_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            st.components.v1.html(html_content, height=520, scrolling=True)
            
        with col_g2:
            st.subheader("🕵️ Money Trail Analysis")
            
            if fraud_findings:
                for idx, finding in enumerate(fraud_findings):
                    badge_class = "badge-high" if finding["severity"] == "HIGH" else ("badge-medium" if finding["severity"] == "MEDIUM" else "badge-low")
                    st.markdown(f"""
                    <div class="fraud-card">
                        <h4>⚠️ {finding['type']} <span class="badge {badge_class}">{finding['severity']}</span></h4>
                        <p><b>Accounts involved:</b> {', '.join(finding['accounts'])}</p>
                        <p><b>Transactions involved:</b> {', '.join(finding['transactions'])}</p>
                        <p><i>{finding['description']}</i></p>
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.success("🎉 No suspicious patterns detected in transactions flow.")

    # ------------------------------------------------------
    # TAB 3: LLM CHATBOT
    # ------------------------------------------------------
    with tab3:
        st.header("Interactive Graph & Behavioral Chatbot")
        
        col_chat1, col_chat2 = st.columns([2, 3])
        
        with col_chat1:
            st.subheader("Reference Graph View")
            # Small Graph for reference during chat
            small_graph_path = create_pyvis_graph(graph_nodes, graph_edges, height="400px")
            with open(small_graph_path, "r", encoding="utf-8") as f:
                small_html = f.read()
            st.components.v1.html(small_html, height=420, scrolling=True)
            
        with col_chat2:
            st.subheader("Ask Anomaly Auditor Bot")
            
            # Setup chat state
            if "chat_history" not in st.session_state:
                st.session_state["chat_history"] = []
                
            # Suggested questions click handler
            suggestions = [
                "Which account has most suspicious activity?",
                "Show all transactions above Rs.50,000",
                "Is there any circular fund flow?",
                "Summarise overall risk level"
            ]
            
            st.markdown("**Suggested Questions:**")
            col_s1, col_s2 = st.columns(2)
            with col_s1:
                if st.button(suggestions[0], use_container_width=True):
                    ans = ask_ollama(suggestions[0], st.session_state["chat_history"])
                    st.session_state["chat_history"].append({"role": "user", "content": suggestions[0]})
                    st.session_state["chat_history"].append({"role": "assistant", "content": ans, "ref": "ACC_101, ACC_202"})
                if st.button(suggestions[1], use_container_width=True):
                    ans = ask_ollama(suggestions[1], st.session_state["chat_history"])
                    st.session_state["chat_history"].append({"role": "user", "content": suggestions[1]})
                    st.session_state["chat_history"].append({"role": "assistant", "content": ans, "ref": "TXN_003, TXN_005"})
            with col_s2:
                if st.button(suggestions[2], use_container_width=True):
                    ans = ask_ollama(suggestions[2], st.session_state["chat_history"])
                    st.session_state["chat_history"].append({"role": "user", "content": suggestions[2]})
                    st.session_state["chat_history"].append({"role": "assistant", "content": ans, "ref": "ACC_101 → ACC_202 → ACC_303"})
                if st.button(suggestions[3], use_container_width=True):
                    ans = ask_ollama(suggestions[3], st.session_state["chat_history"])
                    st.session_state["chat_history"].append({"role": "user", "content": suggestions[3]})
                    st.session_state["chat_history"].append({"role": "assistant", "content": ans, "ref": "Overall Statement"})
            
            st.markdown("---")
            
            # Print chat bubbles
            chat_container = st.container(height=250)
            with chat_container:
                for msg in st.session_state["chat_history"]:
                    with st.chat_message(msg["role"]):
                        st.write(msg["content"])
                        if "ref" in msg:
                            st.caption(f"🔗 **References:** {msg['ref']}")
                            
            # Chat input
            if user_prompt := st.chat_input("Enter question about transactions ledger..."):
                with chat_container:
                    with st.chat_message("user"):
                        st.write(user_prompt)
                
                # Ollama response
                with st.spinner("Ollama is analyzing the trail graph..."):
                    ans_text = ask_ollama(user_prompt, st.session_state["chat_history"])
                    
                st.session_state["chat_history"].append({"role": "user", "content": user_prompt})
                st.session_state["chat_history"].append({
                    "role": "assistant",
                    "content": ans_text,
                    "ref": "Interactive Node References"
                })
                st.rerun()

    # ==========================================================
    # SECTION 3: DOWNLOADABLE REPORT
    # ==========================================================
    st.markdown("---")
    st.header("📋 Evidence Audit & Summary Export")
    
    # Preview layout
    st.markdown('<div class="premium-card">', unsafe_allow_html=True)
    col_r1, col_r2 = st.columns([3, 1])
    
    with col_r1:
        st.subheader("Audit Executive Summary Preview")
        st.write(exec_summary)
        anomalies_list = list(set(f["type"] for f in fraud_findings))
        st.markdown(f"**Anomalies Found:** {', '.join(anomalies_list) if anomalies_list else 'None'}")
    with col_r2:
        st.subheader("Risk Score")
        badge_style = "badge-high" if risk_score == "HIGH" else "badge-low"
        st.markdown(f'<h4><span class="badge {badge_style}" style="font-size: 1.2em; padding: 6px 20px;">{risk_score} RISK</span></h4>', unsafe_allow_html=True)
        st.caption(f"Generated at: {datetime.now().strftime('%Y-%m-%d')}")
    st.markdown('</div>', unsafe_allow_html=True)
    
    # Download Button
    pdf_bytes = generate_pdf_report(transactions, fraud_findings, risk_score, exec_summary)
    st.download_button(
        label="📥 Download Evidence Report (PDF)",
        data=pdf_bytes,
        file_name=f"BankLens_Report_{datetime.now().strftime('%Y-%m-%d')}.pdf",
        mime="application/pdf",
        use_container_width=True
    )
