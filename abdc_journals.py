#!/usr/bin/env python3
"""ABDC-JQL ExcelからランクC以上のジャーナルを抽出してJSONに保存する。"""

import json
import os
import openpyxl

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXCEL_PATH = os.path.join(SCRIPT_DIR, "..", "ABDC-JQL-2025-v1-260326.xlsx")
OUTPUT_PATH = os.path.join(SCRIPT_DIR, "abdc_c_plus.json")

VALID_RATINGS = {"A*", "A", "B", "C"}

def main():
    wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True)
    ws = wb["2025 JQL"]

    journals = []
    for row in ws.iter_rows(min_row=9, values_only=False):
        title = row[1].value
        rating = row[7].value
        if not title or not rating:
            continue
        rating = str(rating).strip()
        if rating not in VALID_RATINGS:
            continue
        journals.append({
            "title": str(title).strip(),
            "rating": rating,
        })

    wb.close()

    journals.sort(key=lambda j: j["title"].lower())

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(journals, f, ensure_ascii=False, indent=2)

    print(f"Extracted {len(journals)} journals (C and above) → {OUTPUT_PATH}")
    for r in ["A*", "A", "B", "C"]:
        count = sum(1 for j in journals if j["rating"] == r)
        print(f"  {r}: {count}")


if __name__ == "__main__":
    main()
