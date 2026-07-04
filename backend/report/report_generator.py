import os
import glob
import json
import re
import urllib.request
from datetime import datetime

def call_llama(prompt, model_name="llama3.1:8b"):
    url = "http://localhost:11434/api/generate"
    data = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1
        }
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as response:
            res = json.loads(response.read().decode("utf-8"))
            return res.get("response", "")
    except Exception as e:
        return f"Error contacting Ollama: {e}"

def parse_date(date_str):
    if not date_str or not isinstance(date_str, str):
        return None
    clean_date = date_str.strip()[:10]
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(clean_date, fmt)
        except Exception:
            pass
    return None

def analyze_statement_file(fpath, cleaned_dir):
    filename = os.path.basename(fpath)
    with open(fpath, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    if isinstance(data, dict) and "transactions" in data:
        transactions = data["transactions"]
    elif isinstance(data, list):
        transactions = data
    else:
        transactions = []
        
    print(f"Analyzing {filename} ({len(transactions)} transactions)...")
    
    # 1. Programmatic Duplicate Detection
    seen = set()
    duplicates = []
    cleaned_txs = []
    
    for tx in transactions:
        # Standardize transaction keys (checking both capitalized and lowercase names)
        tx_key = (
            tx.get("Date") or tx.get("date") or tx.get("TXN DT") or tx.get("Txn Date") or "",
            tx.get("Debit") or tx.get("debit") or tx.get("DEBIT") or 0.0,
            tx.get("Credit") or tx.get("credit") or tx.get("CREDIT") or 0.0,
            tx.get("Balance") or tx.get("balance") or tx.get("BALANCE") or 0.0,
            tx.get("Narration") or tx.get("description") or tx.get("Description") or tx.get("NARRATION") or "",
            tx.get("from_account") or "",
            tx.get("to_account") or "",
            tx.get("ChequeNo/Reference No") or tx.get("REF TXN NO") or tx.get("REF CHQ NO") or tx.get("reference") or ""
        )
        if tx_key in seen:
            duplicates.append(tx)
        else:
            seen.add(tx_key)
            cleaned_txs.append(tx)
            
    # Write cleaned transactions
    os.makedirs(cleaned_dir, exist_ok=True)
    cleaned_fpath = os.path.join(cleaned_dir, filename)
    with open(cleaned_fpath, 'w', encoding='utf-8') as f_out:
        json.dump(cleaned_txs, f_out, indent=2, ensure_ascii=False)
        
    # 2. Programmatic Failed/Reversal Candidates Detection
    debits = []
    credits = []
    for tx in cleaned_txs:
        # Check debit value
        debit_val = 0.0
        for k in ("Debit", "debit", "DEBIT", "Debit"):
            if k in tx and tx[k] not in ("", None):
                try:
                    debit_val = float(tx[k])
                except ValueError:
                    pass
        
        # Check credit value
        credit_val = 0.0
        for k in ("Credit", "credit", "CREDIT", "Credit"):
            if k in tx and tx[k] not in ("", None):
                try:
                    credit_val = float(tx[k])
                except ValueError:
                    pass
                    
        tx_date_str = tx.get("Date") or tx.get("date") or tx.get("TXN DT") or tx.get("Txn Date") or tx.get("Post Date") or ""
        tx_desc = tx.get("Narration") or tx.get("description") or tx.get("Description") or tx.get("NARRATION") or ""
        
        tx_info = {
            "date": tx_date_str,
            "desc": tx_desc,
            "from": tx.get("from_account", ""),
            "to": tx.get("to_account", ""),
            "ref_no": tx.get("ChequeNo/Reference No") or tx.get("REF TXN NO") or tx.get("REF CHQ NO") or tx.get("reference") or "",
            "balance": tx.get("Balance") or tx.get("balance") or tx.get("BALANCE") or 0.0,
            "raw": tx
        }
        
        if debit_val > 0:
            tx_info["amount"] = debit_val
            debits.append(tx_info)
        elif credit_val > 0:
            tx_info["amount"] = credit_val
            credits.append(tx_info)

    # Match debits and credits to find failed/reversal candidate pairs
    failed_candidates = []
    matched_credits = set()
    
    # Group credits by amount
    credits_by_amount = {}
    for idx, c in enumerate(credits):
        amt_key = round(c["amount"], 2)
        if amt_key not in credits_by_amount:
            credits_by_amount[amt_key] = []
        credits_by_amount[amt_key].append((idx, c))
        
    for d in debits:
        d_date = parse_date(d["date"])
        if not d_date:
            continue
            
        amt_key = round(d["amount"], 2)
        if amt_key not in credits_by_amount:
            continue
            
        for idx, c in credits_by_amount[amt_key]:
            if idx in matched_credits:
                continue
                
            c_date = parse_date(c["date"])
            if not c_date:
                continue
                
            delta = (c_date - d_date).days
            if 0 <= delta <= 5:
                accounts_reversed = (d["from"] == c["to"] and d["to"] == c["from"] and d["from"] != "")
                has_reversal_keywords = any(kw in c["desc"].lower() for kw in ("reversal", "failed", "refund", "returned", "rev", "rtn", "bounce", "reject"))
                
                if accounts_reversed or has_reversal_keywords:
                    failed_candidates.append({
                        "debit": d,
                        "credit": c,
                        "time_difference_days": delta,
                        "reason_type": "Account Loop" if accounts_reversed else "Keyword Match"
                    })
                    matched_credits.add(idx)
                    break

    return {
        "filename": filename,
        "total_before": len(transactions),
        "total_after": len(cleaned_txs),
        "duplicate_count": len(duplicates),
        "duplicates": duplicates[:10],
        "failed_count": len(failed_candidates),
        "failed_candidates": failed_candidates
    }

def generate_report(json_files, cleaned_dir, report_path, model_name="llama3.1:8b"):
    all_results = []
    total_duplicates = 0
    total_failed = 0
    
    for fpath in json_files:
        res = analyze_statement_file(fpath, cleaned_dir)
        all_results.append(res)
        total_duplicates += res["duplicate_count"]
        total_failed += res["failed_count"]
        
    llm_context_data = []
    global_failed_samples = []
    
    for res in all_results:
        if res["duplicate_count"] > 0 or res["failed_count"] > 0:
            summary = {
                "file": res["filename"],
                "duplicates_found": res["duplicate_count"],
                "failed_pairs_found": res["failed_count"]
            }
            llm_context_data.append(summary)
            
            for candidate in res["failed_candidates"]:
                global_failed_samples.append({
                    "file": res["filename"],
                    "debit_date": candidate["debit"]["date"],
                    "debit_desc": candidate["debit"]["desc"],
                    "credit_date": candidate["credit"]["date"],
                    "credit_desc": candidate["credit"]["desc"],
                    "amount": candidate["debit"]["amount"],
                    "from_acc": candidate["debit"]["from"],
                    "to_acc": candidate["debit"]["to"]
                })
            
    prompt = f"""You are Antigravity, an AI financial investigator. Your task is to review the results of a programmatic bank statement audit and compile a professional investigation report.
 
Below is the summary of duplicates and candidate failed transactions detected across the secondary bank statements dataset.
 
Dataset Summary:
- Total Statements Checked: {len(json_files)}
- Total Duplicate Transactions Removed: {total_duplicates}
- Total Failed/Reversed Transaction Pairs Detected: {total_failed}
 
Detected File-by-File Statistics:
{json.dumps(llm_context_data, indent=2)}

Representative Failed Transactions Samples:
{json.dumps(global_failed_samples[:10], indent=2)}

Please write a comprehensive, professional audit report in Markdown format. The report should contain:
1. **Title**: "AUDIT REPORT: SECONDARY BANK STATEMENT ANALYSIS"
2. **Executive Summary**: High-level overview of the findings, including statistics on total files processed, duplicates removed, and failed transaction rate.
3. **Failed Transaction Investigation**:
   - Analyze the sample transactions.
   - Explain how and why these transactions were marked as failed (e.g. debit followed by reversal credit/IMPS return).
   - Point out specific patterns or counterparty accounts where failures are recurring.
4. **Data Integrity Note**: Explain the value of removing duplicates for the transaction graph builder and downstream fraud/failed-transaction detection models.
5. **Conclusion & Recommendations**: Clear action items.

Make sure to format the report professionally with tables and markdown headers. Be concise and thorough.
"""

    print(f"\nCalling local Llama model ({model_name}) via Ollama to generate final audit report...")
    report_content = call_llama(prompt, model_name=model_name)
    
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w', encoding='utf-8') as f_rep:
        f_rep.write(report_content)
        
    print(f"\nFinal Report successfully generated and saved to: {report_path}")
    return report_content, all_results
