"""Write a jobs DataFrame to CSV + formatted XLSX (ported from the original script)."""
from __future__ import annotations

import csv
import re
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo

COLUMN_WIDTHS = {
    "title": 34, "company": 28, "description": 60,
    "job_url": 52, "job_url_direct": 52, "location": 24,
}
# openpyxl raises IllegalCharacterError on these, and scraped descriptions do contain them.
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_EXCEL_CELL_LIMIT = 32000


def _clean_for_excel(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in df.columns:
        # pandas 3 uses a dedicated string dtype (not `object`), so test for both
        if df[col].dtype == object or pd.api.types.is_string_dtype(df[col]):
            df[col] = df[col].map(
                lambda v: _ILLEGAL.sub("", v)[:_EXCEL_CELL_LIMIT] if isinstance(v, str) else v
            )
    return df


def write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, quoting=csv.QUOTE_NONNUMERIC, escapechar="\\", index=False)


def write_xlsx(df: pd.DataFrame, path: Path) -> None:
    _clean_for_excel(df).to_excel(path, index=False, engine="openpyxl")
    wb = load_workbook(path)
    ws = wb.active
    ws.title = "Job Results"
    ws.freeze_panes = "A2"

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in ws[1]:
        cell.fill, cell.font = header_fill, header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for column_cells in ws.columns:
        name = column_cells[0].value
        ws.column_dimensions[column_cells[0].column_letter].width = COLUMN_WIDTHS.get(name, 16)
        for cell in column_cells[1:]:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if name in {"job_url", "job_url_direct"} and cell.value:
                cell.hyperlink = cell.value
                cell.font = Font(color="0563C1", underline="single")

    for row in ws.iter_rows(min_row=2):
        ws.row_dimensions[row[0].row].height = 48

    if ws.max_row > 1 and ws.max_column > 0:  # Excel tables need at least one data row
        table = Table(displayName="JobResults", ref=ws.dimensions)
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2", showRowStripes=True,
            showFirstColumn=False, showLastColumn=False, showColumnStripes=False,
        )
        ws.add_table(table)
    wb.save(path)
