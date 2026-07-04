import json
import pandas as pd

class Structurer:
    def __init__(self):
        self.clean_df = pd.DataFrame()
        self.structured = []

    def structure(self, raw_df: pd.DataFrame):
        """
        Converts unorganized text columns into standardized ledger schemas.
        """
        if raw_df.empty:
            return

        processed_data = []
        for index, row in raw_df.iterrows():
            record = {
                "date": str(row.get("Date", "UNKNOWN")),
                "description": str(row.get("Narration", "NO_NARRATION")),
                "debit": str(row.get("Debit", "0.0")),
                "credit": str(row.get("Credit", "0.0")),
                "balance": str(row.get("Balance", "0.0"))
            }
            processed_data.append(record)

        self.structured = processed_data
        self.clean_df = pd.DataFrame(processed_data)

    def to_json(self, output_file: str):
        """Saves clean normalized array into a system JSON state file."""
        if self.structured:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(self.structured, f, indent=4, ensure_ascii=False)
            print(f"💾 Local cache checkpoint saved: {output_file}")

    def to_ollama_string(self, max_rows: int = 20) -> str:
        """Serializes historical data slices into string lines for context processing."""
        subset = self.structured[:max_rows]
        return json.dumps(subset, indent=2)

    def to_dataframe(self) -> pd.DataFrame:
        return self.clean_df
