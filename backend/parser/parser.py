import os
import re
import json
import pdfplumber
import pandas as pd

class UniversalDatasetParser:
    def __init__(self, file_path, use_chandra=False, chandra_method="vllm", chandra_api_base="http://localhost:8000/v1", chandra_api_key="EMPTY"):
        self.file_path = file_path
        self.use_chandra = use_chandra
        self.chandra_method = chandra_method
        self.chandra_api_base = chandra_api_base
        self.chandra_api_key = chandra_api_key

        self.metadata = {}
        self.tables = []
        self.transactions = pd.DataFrame()
        self.raw_text = []
        self.schema = {}
        self.document = {}

    def parse(self):
        extension = os.path.splitext(self.file_path)[1].lower()
        if extension == ".pdf":
            if self.use_chandra:
                self.parse_pdf_chandra()
            else:
                self.parse_pdf()
        elif extension == ".csv":
            self.parse_csv()
        elif extension in [".xls", ".xlsx"]:
            self.parse_excel()
        else:
            raise Exception(f"Unsupported file : {extension}")
        
        self.normalize_transactions()

    def parse_pdf(self):
        print("\nReading PDF...")
        with pdfplumber.open(self.file_path) as pdf:
            self.metadata["pages"] = len(pdf.pages)
            self.metadata["filetype"] = "PDF"
            self.metadata["filename"] = os.path.basename(self.file_path)

            for page_no, page in enumerate(pdf.pages, start=1):
                # Text
                text = page.extract_text()
                if text:
                    self.raw_text.append({
                        "page": page_no,
                        "content": text
                    })

                # Tables
                extracted_tables = page.extract_tables()
                if extracted_tables:
                    for table in extracted_tables:
                        if len(table) < 2:
                            continue
                        header = table[0]
                        rows = table[1:]
                        df = pd.DataFrame(rows, columns=header)
                        self.tables.append(df)

        if self.tables:
            self.transactions = pd.concat(self.tables, ignore_index=True)

    def parse_pdf_chandra(self):
        print(f"\nParsing PDF using Chandra OCR ({self.chandra_method})...")
        try:
            from chandra.input import load_file
            from chandra.model import InferenceManager
            from chandra.model.schema import BatchInputItem
        except ImportError as e:
            raise ImportError(
                "Chandra OCR libraries are not fully installed. Ensure 'chandra-ocr' is installed. Details: " + str(e)
            )

        # Convert PDF pages into PIL Images
        images = load_file(self.file_path, {})
        
        self.metadata["pages"] = len(images)
        self.metadata["filetype"] = "PDF_OCR"
        self.metadata["filename"] = os.path.basename(self.file_path)

        # Set environment variables for Chandra configuration
        os.environ["VLLM_API_BASE"] = self.chandra_api_base
        os.environ["VLLM_API_KEY"] = self.chandra_api_key

        manager = InferenceManager(method=self.chandra_method)
        batch = [BatchInputItem(image=img, prompt_type="ocr_layout") for img in images]

        results = manager.generate(batch, vllm_api_base=self.chandra_api_base)

        for page_no, res in enumerate(results, start=1):
            if res.error:
                print(f"Error parsing page {page_no} with Chandra OCR.")
                continue

            if res.markdown:
                self.raw_text.append({
                    "page": page_no,
                    "content": res.markdown
                })

            if res.html:
                try:
                    import io
                    extracted_dfs = pd.read_html(io.StringIO(res.html))
                    for df in extracted_dfs:
                        if len(df) < 2:
                            continue
                        header = df.iloc[0].fillna("").astype(str).tolist()
                        rows = df.iloc[1:]
                        cleaned_df = pd.DataFrame(rows.values, columns=header)
                        self.tables.append(cleaned_df)
                except Exception as table_err:
                    print(f"Error extracting tables from page {page_no} HTML: {table_err}")

        if self.tables:
            self.transactions = pd.concat(self.tables, ignore_index=True)

    def parse_csv(self):
        print("\nReading CSV...")
        self.transactions = pd.read_csv(self.file_path)
        self.tables.append(self.transactions)
        self.metadata = {
            "filename": os.path.basename(self.file_path),
            "filetype": "CSV",
            "rows": len(self.transactions)
        }

    def parse_excel(self):
        print("\nReading Excel...")
        excel = pd.ExcelFile(self.file_path)
        self.metadata = {
            "filename": os.path.basename(self.file_path),
            "filetype": "EXCEL",
            "sheets": excel.sheet_names
        }

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

    def process_excel_sheet(self, raw_df):
        header_row = self.find_transaction_header(raw_df)
        if header_row is None:
            print("No transaction table found.")
            return

        # Metadata
        metadata_df = raw_df.iloc[:header_row]
        self.metadata.update(self.extract_metadata(metadata_df))

        # Transactions
        headers = raw_df.iloc[header_row].fillna("").astype(str).tolist()
        transaction_df = raw_df.iloc[header_row + 1:].copy()
        transaction_df.columns = headers
        transaction_df = transaction_df.reset_index(drop=True)
        transaction_df = transaction_df.dropna(how="all")
        self.tables.append(transaction_df)

    def finalize_excel(self):
        raw_tables = self.tables.copy()
        self.tables = []
        for raw in raw_tables:
            self.process_excel_sheet(raw)

        if self.tables:
            self.transactions = pd.concat(self.tables, ignore_index=True)

    def find_transaction_header(self, df):
        keywords = {
            "date", "post date", "posting date", "transaction date", "txn date", "value date",
            "description", "remarks", "narration", "particulars", "debit", "withdrawal", "dr",
            "credit", "deposit", "cr", "balance", "amount"
        }

        best_row = None
        best_score = 0
        for idx, row in df.iterrows():
            values = [str(v).strip().lower() for v in row.tolist() if pd.notna(v)]
            score = sum(1 for value in values if value in keywords)
            if score > best_score:
                best_score = score
                best_row = idx

        if best_score >= 2:
            return best_row
        return None

    def extract_metadata(self, metadata_df):
        metadata = {}
        for _, row in metadata_df.iterrows():
            values = [str(v).strip() for v in row.tolist() if pd.notna(v) and str(v).strip() != ""]
            if len(values) >= 2:
                key = values[0].lower().replace(" ", "_")
                metadata[key] = values[1]
        return metadata

    def get_column_aliases(self):
        return {
            "date": ["date", "transaction date", "txn date", "posting date", "value date", "post date", "txn dt", "transaction dt", "post dt", "posting dt"],
            "description": ["description", "remarks", "remark", "narration", "particulars", "details", "txn type"],
            "debit": ["debit", "withdrawal", "withdraw", "dr"],
            "credit": ["credit", "deposit", "cr"],
            "balance": ["balance", "closing balance", "available balance"],
            "amount": ["amount", "transaction amount", "txn amount"],
            "reference": ["reference", "utr", "rrn", "transaction id", "txn id", "ref chq no", "ref txn no", "chq no", "cheque no", "cheque number", "check no"],
            "batch_number": ["batch no", "batch number", "batch", "ctr batch no", "ctr batch number", "batch_no"],
            "account_number": ["account number", "a/c number", "account no"],
            "ifsc": ["ifsc", "ifsc code"]
        }

    def infer_column(self, column_name, series):
        aliases = self.get_column_aliases()
        name = str(column_name).lower().strip()
        name = re.sub(r"_+", " ", name)

        for canonical, words in aliases.items():
            if name in words:
                return canonical, 100

        values = series.dropna().astype(str).head(20).tolist()

        # Date
        date_pattern = r"\s*\d{1,4}[/-]\d{1,2}[/-]\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\s*"
        matches = sum(bool(re.fullmatch(date_pattern, v)) for v in values)
        if matches >= max(3, len(values)//2):
            return "date", 85

        # IFSC
        matches = sum(bool(re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", v)) for v in values)
        if matches >= 3:
            return "ifsc", 90

        # Account Number
        non_account_indicators = ["chq", "cheque", "ref", "branch", "code", "zip", "phone", "mobile", "date", "dt", "batch", "amount", "debit", "credit", "balance"]
        if not any(ind in name for ind in non_account_indicators):
            matches = sum(bool(re.fullmatch(r"\d{9,18}", v)) for v in values)
            if matches >= 3:
                return "account_number", 85

        # Numeric
        numeric = pd.to_numeric(series, errors="coerce")
        if numeric.notna().sum() > len(series) * 0.7:
            if "bal" in name:
                return "balance", 80
            non_amount_indicators = ["branch", "code", "no", "number", "id", "batch", "chq", "cheque", "ref", "phone", "mobile", "zip", "date", "dt"]
            if not any(indicator in name for indicator in non_amount_indicators):
                return "amount", 70

        # Description
        sample = " ".join(values).lower()
        keywords = ["upi", "atm", "neft", "imps", "rtgs", "cash", "transfer"]
        if any(word in sample for word in keywords):
            return "description", 80

        return "UNKNOWN", 0

    def normalize_transactions(self):
        if self.transactions.empty:
            return

        # Deduplicate column names
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

        # Deduplicate renamed canonical columns
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

        if unknown:
            print("\n========== UNKNOWN COLUMNS ==========\n")
            for col, values in unknown.items():
                print(f"{col}")
                print(values)
                print()
        print("\nSchema inference complete.\n")

    def build_document(self):
        transactions = []
        if not self.transactions.empty:
            # Map canonical names to target output names
            mapping = {
                "date": "Date",
                "description": "Narration",
                "debit": "Debit",
                "credit": "Credit",
                "balance": "Balance"
            }
            
            # Match columns to their canonical equivalents
            final_cols = {}
            for col in self.transactions.columns:
                clean_name = col.split("_")[0]
                if clean_name in mapping:
                    target_name = mapping[clean_name]
                    if target_name not in final_cols:
                        final_cols[target_name] = col
            
            # Construct cleaned dataframe
            clean_df = pd.DataFrame()
            for canon_name, target_name in mapping.items():
                source_col = final_cols.get(target_name)
                if source_col and source_col in self.transactions.columns:
                    clean_df[target_name] = self.transactions[source_col]
                else:
                    clean_df[target_name] = ""
            
            # Filter and store only these 5 columns in the transactions list
            transactions = clean_df.fillna("").to_dict(orient="records")

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

    def export_json(self, output_file="parsed_document.json"):
        self.build_document()
        with open(output_file, "w", encoding="utf8") as f:
            json.dump(self.document, f, indent=4, ensure_ascii=False)
        print(f"\nSaved JSON -> {output_file}")

    def inspect(self):
        print("\n========== PARSER SUMMARY ==========\n")
        print(f"File : {self.metadata.get('filename','')}")
        print(f"Type : {self.metadata.get('filetype','')}")
        print(f"Metadata Fields : {len(self.metadata)}")
        print(f"Transaction Rows : {len(self.transactions)}")
        print(f"Columns : {list(self.transactions.columns)}")
        print()

    def get_dataframe(self):
        # Return dataframe with the filtered columns
        mapping = {
            "date": "Date",
            "description": "Narration",
            "debit": "Debit",
            "credit": "Credit",
            "balance": "Balance"
        }
        final_cols = {}
        for col in self.transactions.columns:
            clean_name = col.split("_")[0]
            if clean_name in mapping:
                target_name = mapping[clean_name]
                if target_name not in final_cols:
                    final_cols[target_name] = col
        
        clean_df = pd.DataFrame()
        for canon_name, target_name in mapping.items():
            source_col = final_cols.get(target_name)
            if source_col and source_col in self.transactions.columns:
                clean_df[target_name] = self.transactions[source_col]
            else:
                clean_df[target_name] = ""
        return clean_df

    def get_document(self):
        self.build_document()
        return self.document
