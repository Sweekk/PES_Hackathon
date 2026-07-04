import os
import glob
import sys
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# Ensure backend package can be imported
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.main import run_pipeline

app = FastAPI(
    title="BankLens AI API",
    description="Backend API for BankLens bank statement parsing, guardrail checks, and audit investigation",
    version="1.0.0"
)

# Enable CORS for Streamlit frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INPUT_DIR = os.path.join(PROJECT_ROOT, "data", "input")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "output")

os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    use_chandra: bool = Form(False),
    chandra_api: str = Form("http://localhost:8000/v1")
):
    # Save file locally
    file_path = os.path.join(INPUT_DIR, file.filename)
    try:
        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")

    # Run the pipeline
    result = run_pipeline(file_path, use_chandra=use_chandra, chandra_api_base=chandra_api)
    if result is None:
        raise HTTPException(status_code=500, detail="Pipeline processing failed.")

    return result

@app.get("/api/files")
def list_files():
    json_files = glob.glob(os.path.join(OUTPUT_DIR, "*.json"))
    statements = []
    for fpath in json_files:
        if fpath.endswith("_structured.json"):
            continue
        statements.append({
            "filename": os.path.basename(fpath),
            "size_bytes": os.path.getsize(fpath)
        })
    return {"statements": statements}

@app.get("/api/report")
def get_report():
    report_path = os.path.join(OUTPUT_DIR, "analysis_report.md")
    if not os.path.exists(report_path):
        return {"report": "No audit report has been generated yet."}
    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()
    return {"report": content}

@app.get("/api/transactions/{filename}")
def get_transactions(filename: str):
    fpath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(fpath):
        raise HTTPException(status_code=404, detail="Parsed file not found.")
    
    import json
    with open(fpath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data
