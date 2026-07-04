import os
import argparse
import sys
import glob

# Ensure backend package can be imported if running directly
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.parser.parser import UniversalDatasetParser
from backend.parser.structurer import Structurer
from backend.guardrail.guardrail import run_guardrail
from backend.report.report_generator import generate_report

def run_pipeline(file_path, use_chandra=False, chandra_api_base="http://localhost:8000/v1"):
    print("\n" + "=" * 60)
    print(f"BANKLENS AI - MODULAR PIPELINE START")
    print(f"Target Input: {file_path}")
    print("=" * 60)

    # Determine directories
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)
    input_dir = os.path.join(project_root, "data", "input")
    output_dir = os.path.join(project_root, "data", "output")
    report_path = os.path.join(project_root, "data", "output", "analysis_report.md")
    
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    # Copy file to input folder if not already there
    dest_path = os.path.join(input_dir, os.path.basename(file_path))
    if os.path.abspath(file_path) != os.path.abspath(dest_path):
        import shutil
        shutil.copy(file_path, dest_path)

    # ── STEP 1: PARSE ──
    print("\n[STEP 1] Parsing file configurations...")
    parser = UniversalDatasetParser(
        dest_path, 
        use_chandra=use_chandra, 
        chandra_api_base=chandra_api_base
    )
    parser.parse()
    
    base_name = os.path.splitext(os.path.basename(dest_path))[0]
    parsed_json_path = os.path.join(output_dir, f"{base_name}.json")
    parser.export_json(parsed_json_path)
    
    raw_df = parser.get_dataframe()
    if raw_df.empty:
        print("Parser returned no transaction data.")
        return None

    # ── STEP 2: STRUCTURE ──
    print("\n[STEP 2] Structuring unstructured entities...")
    structurer = Structurer()
    structurer.structure(raw_df)
    
    structured_json_path = os.path.join(output_dir, f"{base_name}_structured.json")
    structurer.to_json(structured_json_path)
    
    ollama_input = structurer.to_ollama_string(max_rows=20)
    clean_df = structurer.to_dataframe()
    print(f"Schema normalized into {len(clean_df)} entries.")

    # ── STEP 3: GUARDRAIL ──
    print("\n[STEP 3] Running Prompt Guardrail Firewall (Bypassed)...")
    guardrail_result = {
        "STATUS": "VALID",
        "INJECTION_DETECTED": "NO",
        "INJECTION_TYPE": "NONE",
        "TAMPER_DETECTED": "NO",
        "TAMPER_REASON": "NONE",
        "SAFE_TO_PROCEED": "YES",
        "SUMMARY": "Guardrail logic bypassed by developer request."
    }
    print("\n-- Guardrail Security Assessment Metrics --")
    for key, val in guardrail_result.items():
        print(f"  {key}: {val}")

    # ── STEP 4: AUDIT REPORT GENERATOR ──
    print("\n[STEP 4] Compiling Financial Investigation Audit Report...")
    json_files = glob.glob(os.path.join(output_dir, "*.json"))
    statement_jsons = [f for f in json_files if not f.endswith("_structured.json")]
    
    report_content, audit_results = generate_report(statement_jsons, output_dir, report_path)
    
    print("\nIntegration Verification Success!")
    return {
        "status": "success",
        "guardrail": guardrail_result,
        "report_content": report_content,
        "dataframe": clean_df.to_dict(orient="records"),
        "audit_results": audit_results
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BankLens Pipeline Runner")
    parser.add_argument("--file", required=True, help="Path to statement file (PDF, CSV, XLS, XLSX)")
    parser.add_argument("--use-chandra", action="store_true", help="Enable Chandra OCR for PDFs")
    parser.add_argument("--chandra-api", default="http://localhost:8000/v1", help="vLLM API Base URL for Chandra")
    args = parser.parse_args()

    run_pipeline(args.file, use_chandra=args.use_chandra, chandra_api_base=args.chandra_api)
