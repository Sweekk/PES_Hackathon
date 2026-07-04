import os
import re
import json
import pdfplumber
import pandas as pd

class UniversalDatasetParser:
    # -----------------------------------------------------
    def __init__(self, file_path, use_chandra=False, chandra_method="vllm", chandra_api_base="http://localhost:8000/v1", chandra_api_key="EMPTY"):
        self.file_path = file_path
        self.metadata = {}
        self.tables = []
        self.transactions = pd.DataFrame()
        self.raw_text = []
        self.schema = {}
        self.document = {}
        self.reverse_column_mapping = {}

    # -----------------------------------------------------
    # Main Parser
    # -----------------------------------------------------
    def parse(self):
        extension = os.path.splitext(self.file_path)[1].lower()
        if extension == ".pdf":
            # Reverted to extract text and tables natively without Chandra OCR
            self.parse_pdf()
        elif extension == ".csv":
            self.parse_csv()
        elif extension in [".xls", ".xlsx"]:
            self.parse_excel()
        else:
            raise Exception(f"Unsupported file : {extension}")
        
        self.normalize_transactions()

    # -----------------------------------------------------
    # PDF Table Helper
    # -----------------------------------------------------
    def is_valid_transaction_header(self, headers):
        headers_clean = [str(h).lower().strip() for h in headers if h is not None]
        has_date = any("date" in h or "dt" in h or h == "day" for h in headers_clean)
        has_desc = any("desc" in h or "narr" in h or "particular" in h or "remark" in h or "detail" in h for h in headers_clean)
        has_amount = any(
            any(k in h for k in ["debit", "credit", "amount", "withdrawal", "deposit", "bal", "dr", "cr"])
            for h in headers_clean
        )
        return has_date and has_desc and has_amount

    # -----------------------------------------------------
    # PDF
    # -----------------------------------------------------
    def parse_pdf(self):
        print("\nReading PDF...")
        canonical_headers = None
        canonical_col_count = None
        self.tables = []
        
        with pdfplumber.open(self.file_path) as pdf:
            self.metadata["pages"] = len(pdf.pages)
            self.metadata["filetype"] = "PDF"
            self.metadata["filename"] = os.path.basename(self.file_path)
            
            for page_no, page in enumerate(pdf.pages, start=1):
                # -------- TEXT --------
                text = page.extract_text()
                if text:
                    self.raw_text.append({
                        "page": page_no,
                        "content": text
                    })
                
                # -------- TABLES --------
                extracted_tables = page.extract_tables()
                if extracted_tables:
                    for table in extracted_tables:
                        if len(table) < 1:
                            continue
                            
                        # Search for transaction header in this table
                        header_row_idx = None
                        for idx, row in enumerate(table):
                            if self.is_valid_transaction_header(row):
                                header_row_idx = idx
                                break
                                
                        if header_row_idx is not None:
                            headers = [str(h).strip() for h in table[header_row_idx]]
                            rows = table[header_row_idx + 1:]
                            df = pd.DataFrame(rows, columns=headers)
                            
                            # Update our canonical schema
                            canonical_headers = headers
                            canonical_col_count = len(headers)
                            
                            self.tables.append(df)
                        else:
                            # Continuation page table check
                            if canonical_col_count is not None and len(table[0]) == canonical_col_count:
                                df = pd.DataFrame(table, columns=canonical_headers)
                                self.tables.append(df)
                                
        if self.tables:
            self.transactions = pd.concat(self.tables, ignore_index=True)

    # -----------------------------------------------------
    # CSV
    # -----------------------------------------------------
    def parse_csv(self):
        print("\nReading CSV...")
        self.transactions = pd.read_csv(self.file_path)
        self.tables.append(self.transactions)
        self.metadata = {
            "filename": os.path.basename(self.file_path),
            "filetype": "CSV",
            "rows": len(self.transactions)
        }

    # -----------------------------------------------------
    # EXCEL
    # -----------------------------------------------------
    def parse_excel(self):
        print("\nReading Excel...")
        excel = pd.ExcelFile(self.file_path)
        self.metadata = {
            "filename": os.path.basename(self.file_path),
            "filetype": "EXCEL",
            "sheets": excel.sheet_names
        }

        # Read every sheet as RAW
        self.tables = []
        for sheet in excel.sheet_names:
            raw = pd.read_excel(
                self.file_path,
                sheet_name=sheet,
                header=None,
                dtype=str
            )
            self.tables.append(raw)
        
        self.finalize_excel()

    # -----------------------------------------------------
    def get_tables(self):
        return self.tables

    # -----------------------------------------------------
    def get_metadata(self):
        return self.metadata

    # -----------------------------------------------------
    def get_transactions(self):
        return self.transactions

    # -----------------------------------------------------
    def get_text(self):
        return self.raw_text

    # -----------------------------------------------------
    def get_document(self):
        self.build_document()
        return self.document
    
    # -----------------------------------------------------
    # Find Transaction Header
    # -----------------------------------------------------
    def find_transaction_header(self, df):
        """
        Finds the row where the actual transaction table starts.
        """
        keywords = {
            "date", "post date", "posting date", "transaction date", "txn date", "value date", "tran date",
            "description", "narration", "remarks", "remark", "particulars", "particular", "debit", "withdrawal", "dr",
            "credit", "deposit", "cr", "balance", "amount"
        }

        best_row = None
        best_score = 0

        for idx, row in df.iterrows():
            values = [str(v).strip().lower() for v in row.tolist() if pd.notna(v)]
            score = 0
            for val in values:
                for kw in keywords:
                    if len(kw) <= 2:
                        # Exact match for short keywords like dr, cr
                        if kw == val or f" {kw} " in f" {val} " or val.startswith(f"{kw} ") or val.endswith(f" {kw}"):
                            score += 1
                            break
                    else:
                        # Substring match for longer keywords
                        if kw in val:
                            score += 1
                            break
                            
            if score > best_score:
                best_score = score
                best_row = idx

        # Need at least TWO matching keywords
        if best_score >= 2:
            return best_row

        return None

    # -----------------------------------------------------
    # Extract Metadata
    # -----------------------------------------------------
    def extract_metadata(self, metadata_df):
        metadata = {}
        for _, row in metadata_df.iterrows():
            values = [str(v).strip() for v in row.tolist() if pd.notna(v) and str(v).strip() != ""]
            if len(values) >= 2:
                key = values[0].lower().replace(" ", "_")
                metadata[key] = values[1]
        return metadata

    # -----------------------------------------------------
    # Process Raw Excel Sheet
    # -----------------------------------------------------
    def process_excel_sheet(self, raw_df):
        header_row = self.find_transaction_header(raw_df)
        if header_row is None:
            print("No transaction table found.")
            return

        # ---------------- Metadata ----------------
        metadata_df = raw_df.iloc[:header_row]
        self.metadata.update(self.extract_metadata(metadata_df))

        # ---------------- Transactions ----------------
        headers = raw_df.iloc[header_row].fillna("").astype(str).tolist()
        transaction_df = raw_df.iloc[header_row + 1:].copy()
        transaction_df.columns = headers
        transaction_df = transaction_df.reset_index(drop=True)

        # Remove empty rows
        transaction_df = transaction_df.dropna(how="all")
        self.tables.append(transaction_df)

    # -----------------------------------------------------
    # Finalize Excel
    # -----------------------------------------------------
    def finalize_excel(self):
        raw_tables = self.tables.copy()
        self.tables = []
        for raw in raw_tables:
            self.process_excel_sheet(raw)

        if self.tables:
            self.transactions = pd.concat(self.tables, ignore_index=True)

    # -----------------------------------------------------
    # Canonical Column Dictionary
    # -----------------------------------------------------
    def get_column_aliases(self):
        return {
            "date": [
                "date", "transaction date", "txn date", "posting date", "value date", "post date",
                "txn dt", "transaction dt", "post dt", "posting dt", "tran date", "val date", 
            ],
            "description": [
                "description", "remarks", "remark", "narration", "particulars", "particular", "details", "txn type",
                "tran particular", "tran particulars", "transaction particulars", "tran rmks", "tran remarks"
            ],
            "debit": [
                "debit", "withdrawal", "withdraw", "dr", "debit amount", "withdrawal amount", "dr amt"
            ],
            "credit": [
                "credit", "deposit", "cr", "credit amount", "deposit amount", "cr amt"
            ],
            "balance": [
                "balance", "closing balance", "available balance", "balance amount"
            ],
            "amount": [
                "amount", "transaction amount", "txn amount"
            ],
            "reference": [
                "reference", "utr", "rrn", "transaction id", "txn id", "ref chq no", "ref txn no",
                "chq no", "cheque no", "cheque number", "check no"
            ],
            "batch_number": [
                "batch no", "batch number", "batch", "ctr batch no", "ctr batch number"
            ],
            "account_number": [
                "account number", "a/c number", "account no"
            ],
            "ifsc": [
                "ifsc", "ifsc code"
            ]
        }

    # -----------------------------------------------------
    # Infer Single Column
    # -----------------------------------------------------
    def infer_column(self, column_name, series):
        aliases = self.get_column_aliases()
        name = str(column_name).lower().strip()
        name = re.sub(r"_+", " ", name)

        # ---------------- Alias Matching ----------------
        for canonical, words in aliases.items():
            if name in words:
                return canonical, 100

        # ---------------- Sample Values ----------------
        values = series.dropna().astype(str).head(20).tolist()

        # ---------------- Date ----------------
        date_pattern = r"\s*\d{1,4}[/-]\d{1,2}[/-]\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\s*"
        matches = sum(bool(re.fullmatch(date_pattern, v)) for v in values)
        if matches >= max(3, len(values)//2):
            return "date", 85

        # ---------------- IFSC ----------------
        matches = sum(bool(re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", v)) for v in values)
        if matches >= 3:
            return "ifsc", 90

        # ---------------- Account Number ----------------
        non_account_indicators = ["chq", "cheque", "ref", "branch", "code", "zip", "phone", "mobile", "date", "dt", "batch", "amount", "debit", "credit", "balance"]
        if not any(ind in name for ind in non_account_indicators):
            matches = sum(bool(re.fullmatch(r"\d{9,18}", v)) for v in values)
            if matches >= 3:
                return "account_number", 85

        # ---------------- Numeric ----------------
        numeric = pd.to_numeric(series, errors="coerce")
        if numeric.notna().sum() > len(series) * 0.7:
            if "bal" in name:
                return "balance", 80
            non_amount_indicators = ["branch", "code", "no", "number", "id", "batch", "chq", "cheque", "ref", "phone", "mobile", "zip", "date", "dt"]
            if not any(indicator in name for indicator in non_amount_indicators):
                return "amount", 70

        # ---------------- Description ----------------
        sample = " ".join(values).lower()
        keywords = ["upi", "atm", "neft", "imps", "rtgs", "cash", "transfer"]
        if any(word in sample for word in keywords):
            return "description", 80

        return "UNKNOWN", 0

    # -----------------------------------------------------
    # Normalize Whole DataFrame
    # -----------------------------------------------------
    def normalize_transactions(self):
        if self.transactions.empty:
            return

        # Deduplicate column names to handle empty or duplicate headers
        cols = []
        count = {}
        for col in self.transactions.columns:
            col_str = str(col).strip()
            if not col_str or col_str.lower() in ["nan", "nat"]:
                col_str = "EMPTY"
            if col_str in count:
                count[col_str] += 1
                cols.append(f"{col_str}_{count[col_str]}")
            else:
                count[col_str] = 0
                cols.append(col_str)
        self.transactions.columns = cols

        print("\n========== SCHEMA INFERENCE ==========\n")
        rename = {}
        unknown = {}
        self.schema = {}

        for column in self.transactions.columns:
            canonical, confidence = self.infer_column(column, self.transactions[column])
            print(f"{column:25} -> {canonical:20}({confidence}%)")

            self.schema[column] = {
                "canonical": canonical,
                "confidence": confidence
            }

            if canonical != "UNKNOWN":
                rename[column] = canonical
            else:
                unknown[column] = self.transactions[column].head(5).tolist()

        # Deduplicate renamed canonical columns to avoid duplicate column names
        rename_dedup = {}
        canonical_count = {}
        for original, canonical in rename.items():
            if canonical in canonical_count:
                canonical_count[canonical] += 1
                rename_dedup[original] = f"{canonical}_{canonical_count[canonical]}"
            else:
                canonical_count[canonical] = 0
                rename_dedup[original] = canonical

        self.transactions.rename(columns=rename_dedup, inplace=True)
        self.reverse_column_mapping = {v: k for k, v in rename_dedup.items()}

        if unknown:
            print("\n========== UNKNOWN COLUMNS ==========\n")
            for col, values in unknown.items():
                print(f"{col}")
                print(values)
                print()
        print("\nSchema inference complete.\n")

    # -----------------------------------------------------
    # Clean and Filter Parsed Dataframe Rows
    # -----------------------------------------------------
    def clean_parsed_dataframe(self, df):
        if df.empty:
            return df
        
        df = df.copy()
        
        # Ensure all columns exist
        for col in ["Date", "Narration", "Debit", "Credit", "Balance"]:
            if col not in df.columns:
                df[col] = ""
                
        # Clean text values
        for col in ["Date", "Narration", "Debit", "Credit", "Balance"]:
            df[col] = df[col].astype(str).str.strip()
            
        df["Date"] = df["Date"].replace(["nan", "NaN", "None", "NaT"], "")
        df["Narration"] = df["Narration"].replace(["nan", "NaN", "None"], "")
        df["Debit"] = df["Debit"].replace(["nan", "NaN", "None", "0.0", "0", "0.00"], "")
        df["Credit"] = df["Credit"].replace(["nan", "NaN", "None", "0.0", "0", "0.00"], "")
        df["Balance"] = df["Balance"].replace(["nan", "NaN", "None"], "")
        
        # Drop rows that are headers
        header_keywords = {"date", "tran date", "trans date", "transaction date", "value date", "post date", "posting date", "dt", "txn date", "posting dt", "value dt"}
        is_header = df["Date"].str.lower().isin(header_keywords) | df["Narration"].str.lower().isin(header_keywords)
        df = df[~is_header].reset_index(drop=True)
        
        if df.empty:
            return df
            
        merged_rows = []
        for idx, row in df.iterrows():
            # Check if it is a continuation row:
            is_empty_date = row["Date"] == ""
            is_empty_amount = row["Debit"] == "" and row["Credit"] == ""
            
            # If it's a summary/metadata row, skip it
            summary_keywords = ["total", "brought forward", "carried forward", "b/f", "c/f", "page total", "sub total", "grand total", "bal b/f", "bal c/f", "balance b/f", "balance c/f", "opening balance", "opening bal", "closing balance", "closing bal"]
            is_summary = any(kw in row["Narration"].lower() for kw in summary_keywords)
            
            if is_summary:
                continue
                
            if is_empty_date and is_empty_amount and row["Narration"] != "":
                # Append narration to previous row if exists
                if merged_rows:
                    merged_rows[-1]["Narration"] = (merged_rows[-1]["Narration"] + " " + row["Narration"]).strip()
                continue
            elif is_empty_date and is_empty_amount and row["Narration"] == "":
                # Completely empty row, skip it
                continue
                
            # Normal row, append to list
            merged_rows.append(row.to_dict())
            
        return pd.DataFrame(merged_rows)

    # -----------------------------------------------------
    # Build Final Document
    # -----------------------------------------------------
    def build_document(self):
        transactions = []
        if not self.transactions.empty:
            # Map canonical names to target output names to ensure downstream compatibility
            mapping = {
                "date": "Date",
                "description": "Narration",
                "debit": "Debit",
                "credit": "Credit",
                "balance": "Balance"
            }
            
            # Match columns to their canonical equivalents using alias-priority scoring
            final_cols = {}
            aliases = self.get_column_aliases()
            
            for canon_name, target_name in mapping.items():
                matching_cols = []
                for col in self.transactions.columns:
                    if col == canon_name or col.startswith(f"{canon_name}_"):
                        matching_cols.append(col)
                        
                if not matching_cols:
                    continue
                    
                # Score each matching column based on original name priority in aliases list
                best_col = None
                best_score = 999
                for col in matching_cols:
                    orig_name = self.reverse_column_mapping.get(col, "")
                    orig_clean = str(orig_name).lower().strip().replace("_", " ")
                    alias_list = aliases.get(canon_name, [])
                    try:
                        score = alias_list.index(orig_clean)
                    except ValueError:
                        score = 999
                        
                    if score < best_score:
                        best_score = score
                        best_col = col
                        
                if best_col is None:
                    best_col = matching_cols[0]
                final_cols[target_name] = best_col
            
            # Construct cleaned dataframe and filter/clean NaN values
            clean_df = pd.DataFrame()
            for canon_name, target_name in mapping.items():
                source_col = final_cols.get(target_name)
                if source_col and source_col in self.transactions.columns:
                    series = self.transactions[source_col]
                    if canon_name in ["debit", "credit", "balance"]:
                        series = series.fillna("0.0").replace("nan", "0.0").replace("NaN", "0.0")
                    else:
                        series = series.fillna("").replace("nan", "").replace("NaN", "")
                    clean_df[target_name] = series
                else:
                    clean_df[target_name] = ""
            
            # Clean and filter dataframe rows
            clean_df = self.clean_parsed_dataframe(clean_df)
            
            # Filter and store only these 5 columns in the transactions list
            transactions = clean_df.to_dict(orient="records")

        self.document = {
            "source": {
                "filename": self.metadata.get("filename", ""),
                "filetype": self.metadata.get("filetype", "")
            },
            "metadata": self.metadata,
            "schema": self.schema,
            "transactions": transactions,
            "raw_text": self.raw_text
        }

    # -----------------------------------------------------
    # Export JSON
    # -----------------------------------------------------
    def export_json(self, output_file="parsed_document.json"):
        self.build_document()
        with open(output_file, "w", encoding="utf8") as f:
            json.dump(self.document, f, indent=4, ensure_ascii=False)
        print(f"\nSaved JSON -> {output_file}")

    # -----------------------------------------------------
    def inspect(self):
        print("\n========== PARSER SUMMARY ==========\n")
        print(f"File : {self.metadata.get('filename','')}")
        print(f"Type : {self.metadata.get('filetype','')}")
        print(f"Metadata Fields : {len(self.metadata)}")
        print(f"Transaction Rows : {len(self.transactions)}")
        print(f"Columns : {list(self.transactions.columns)}")
        print()

    # -----------------------------------------------------
    def get_dataframe(self):
        # Return dataframe with the mapped columns to prevent breaking structurer/app
        mapping = {
            "date": "Date",
            "description": "Narration",
            "debit": "Debit",
            "credit": "Credit",
            "balance": "Balance"
        }
        
        # Match columns to their canonical equivalents using alias-priority scoring
        final_cols = {}
        aliases = self.get_column_aliases()
        
        for canon_name, target_name in mapping.items():
            matching_cols = []
            for col in self.transactions.columns:
                if col == canon_name or col.startswith(f"{canon_name}_"):
                    matching_cols.append(col)
                    
            if not matching_cols:
                continue
                
            # Score each matching column based on original name priority in aliases list
            best_col = None
            best_score = 999
            for col in matching_cols:
                orig_name = self.reverse_column_mapping.get(col, "")
                orig_clean = str(orig_name).lower().strip().replace("_", " ")
                alias_list = aliases.get(canon_name, [])
                try:
                    score = alias_list.index(orig_clean)
                except ValueError:
                    score = 999
                    
                if score < best_score:
                    best_score = score
                    best_col = col
                    
            if best_col is None:
                best_col = matching_cols[0]
            final_cols[target_name] = best_col
        
        clean_df = pd.DataFrame()
        for canon_name, target_name in mapping.items():
            source_col = final_cols.get(target_name)
            if source_col and source_col in self.transactions.columns:
                series = self.transactions[source_col]
                if canon_name in ["debit", "credit", "balance"]:
                    series = series.fillna("0.0").replace("nan", "0.0").replace("NaN", "0.0")
                else:
                    series = series.fillna("").replace("nan", "").replace("NaN", "")
                clean_df[target_name] = series
            else:
                clean_df[target_name] = ""
        
        # Clean and filter dataframe rows
        clean_df = self.clean_parsed_dataframe(clean_df)
        return clean_df
