import os
import glob
import json
import re
import urllib.request
from datetime import datetime
from collections import deque

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

def parse_amount(value):
    if value in ("", None):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    clean_value = str(value).strip()
    if not clean_value or clean_value.lower() in ("nan", "none", "null"):
        return 0.0

    is_negative = False
    if clean_value.startswith("(") and clean_value.endswith(")"):
        is_negative = True
        clean_value = clean_value[1:-1]

    clean_value = re.sub(r"(?i)\b(rs\.?|inr|dr|cr)\b", "", clean_value)
    clean_value = clean_value.replace(",", "").strip()
    clean_value = re.sub(r"[^0-9.\-]", "", clean_value)

    try:
        amount = float(clean_value)
    except ValueError:
        return 0.0

    if is_negative:
        amount = -abs(amount)
    return abs(amount)

def get_first_value(tx, keys, default=""):
    for key in keys:
        value = tx.get(key)
        if value not in ("", None):
            return value
    return default

def normalize_tx_info(tx, index):
    debit_val = parse_amount(get_first_value(tx, ("Debit", "debit", "DEBIT"), 0.0))
    credit_val = parse_amount(get_first_value(tx, ("Credit", "credit", "CREDIT"), 0.0))
    balance_val = parse_amount(get_first_value(tx, ("Balance", "balance", "BALANCE"), 0.0))
    tx_date_str = get_first_value(tx, ("Date", "date", "TXN DT", "Txn Date", "Post Date"), "")
    tx_desc = get_first_value(tx, ("Narration", "description", "Description", "NARRATION"), "")
    ref_no = get_first_value(tx, ("ChequeNo/Reference No", "REF TXN NO", "REF CHQ NO", "reference"), "")

    return {
        "index": index,
        "date": tx_date_str,
        "desc": tx_desc,
        "from": tx.get("from_account", ""),
        "to": tx.get("to_account", ""),
        "ref_no": ref_no,
        "debit": debit_val,
        "credit": credit_val,
        "balance": balance_val,
        "raw": tx
    }

def analyze_money_trails(cleaned_txs):
    """
    Tracks how received credits are consumed by later debits.

    Credits enter a FIFO queue. Every debit is allocated to the oldest unspent
    credit buckets first, so multiple credits are traced without double-counting.
    A credit is fully traced when debits assigned to it equal the credit amount,
    which is the point where the credited amount has been spent and the ledger
    has effectively returned to the pre-credit balance for that inflow.
    """
    credit_queue = deque()
    trails = []

    for index, tx in enumerate(cleaned_txs):
        tx_info = normalize_tx_info(tx, index)

        if tx_info["credit"] > 0:
            credit_amount = tx_info["credit"]
            balance_after_credit = tx_info["balance"]
            previous_balance = balance_after_credit - credit_amount
            trail = {
                "credit_id": f"CREDIT_{len(trails) + 1:03d}",
                "credit_index": index,
                "credit_date": tx_info["date"],
                "credit_description": tx_info["desc"],
                "credit_reference": tx_info["ref_no"],
                "credit_amount": round(credit_amount, 2),
                "balance_before_credit": round(previous_balance, 2),
                "balance_after_credit": round(balance_after_credit, 2),
                "amount_tracked": 0.0,
                "remaining_unspent": round(credit_amount, 2),
                "status": "Open",
                "debit_transactions": []
            }
            trails.append(trail)
            credit_queue.append({
                "trail_index": len(trails) - 1,
                "remaining": credit_amount
            })
            continue

        if tx_info["debit"] <= 0:
            continue

        debit_remaining = tx_info["debit"]
        while debit_remaining > 0 and credit_queue:
            bucket = credit_queue[0]
            allocation = min(debit_remaining, bucket["remaining"])
            trail = trails[bucket["trail_index"]]

            trail["debit_transactions"].append({
                "debit_index": index,
                "date": tx_info["date"],
                "description": tx_info["desc"],
                "reference": tx_info["ref_no"],
                "debit_amount": round(tx_info["debit"], 2),
                "allocated_amount": round(allocation, 2),
                "balance_after_debit": round(tx_info["balance"], 2),
                "to_account": tx_info["to"],
                "raw": tx_info["raw"]
            })

            bucket["remaining"] -= allocation
            debit_remaining -= allocation
            trail["amount_tracked"] = round(trail["amount_tracked"] + allocation, 2)
            trail["remaining_unspent"] = round(max(0.0, bucket["remaining"]), 2)

            if bucket["remaining"] <= 0.005:
                trail["status"] = "Fully Spent"
                trail["remaining_unspent"] = 0.0
                credit_queue.popleft()
            else:
                credit_queue[0] = bucket

    return trails

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
            get_first_value(tx, ("Date", "date", "TXN DT", "Txn Date"), ""),
            parse_amount(get_first_value(tx, ("Debit", "debit", "DEBIT"), 0.0)),
            parse_amount(get_first_value(tx, ("Credit", "credit", "CREDIT"), 0.0)),
            parse_amount(get_first_value(tx, ("Balance", "balance", "BALANCE"), 0.0)),
            get_first_value(tx, ("Narration", "description", "Description", "NARRATION"), ""),
            tx.get("from_account", ""),
            tx.get("to_account", ""),
            get_first_value(tx, ("ChequeNo/Reference No", "REF TXN NO", "REF CHQ NO", "reference"), "")
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
        tx_info_norm = normalize_tx_info(tx, len(debits) + len(credits))
        debit_val = tx_info_norm["debit"]
        credit_val = tx_info_norm["credit"]
        tx_date_str = tx_info_norm["date"]
        tx_desc = tx_info_norm["desc"]
        
        tx_info = {
            "date": tx_date_str,
            "desc": tx_desc,
            "from": tx_info_norm["from"],
            "to": tx_info_norm["to"],
            "ref_no": tx_info_norm["ref_no"],
            "balance": tx_info_norm["balance"],
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

    money_trails = analyze_money_trails(cleaned_txs)

    return {
        "filename": filename,
        "total_before": len(transactions),
        "total_after": len(cleaned_txs),
        "duplicate_count": len(duplicates),
        "duplicates": duplicates[:10],
        "failed_count": len(failed_candidates),
        "failed_candidates": failed_candidates,
        "money_trails": money_trails
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
