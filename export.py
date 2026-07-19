"""
Excel export for Basir search results.
"""
from io import BytesIO

import openpyxl
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side
)


_HEADER_FILL   = PatternFill("solid", fgColor="1a1a3e")
_HEADER_FONT   = Font(bold=True, color="FFFFFF", name="Calibri", size=12)
_ROW_FILL_ODD  = PatternFill("solid", fgColor="F0F0F8")
_ROW_FILL_EVEN = PatternFill("solid", fgColor="FFFFFF")
_BORDER_SIDE   = Side(border_style="thin", color="C0C0D0")
_CELL_BORDER   = Border(
    left=_BORDER_SIDE, right=_BORDER_SIDE,
    top=_BORDER_SIDE, bottom=_BORDER_SIDE,
)


def generate_excel(results: list[dict]) -> BytesIO:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "نتایج بصیر"
    ws.sheet_view.rightToLeft = True  # RTL

    headers = ["#", "نام کانال / گروه", "محتوای کامل پیام", "تاریخ و زمان",
               "بازدید", "مجموع ری‌اکشن", "ری‌اکشن‌ها", "لینک پیام", "اکانت جستجو"]
    col_widths = [5, 30, 60, 22, 12, 14, 30, 40, 20]

    # Write headers
    for col_idx, (header, width) in enumerate(zip(headers, col_widths), start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _CELL_BORDER
        ws.column_dimensions[cell.column_letter].width = width

    ws.row_dimensions[1].height = 28

    # Write rows
    for row_num, r in enumerate(results, start=2):
        fill = _ROW_FILL_ODD if row_num % 2 == 0 else _ROW_FILL_EVEN
        # format reactions as "emoji count, emoji count, ..."
        reactions_str = ""
        reactions_list = r.get("reactions", [])
        if reactions_list:
            reactions_str = " | ".join(
                f"{rx.get('emoji','')} {rx.get('count','')}" for rx in reactions_list
            )

        values = [
            row_num - 1,
            r.get("channel_name", ""),
            r.get("full_content", "") or r.get("message", ""),
            r.get("datetime_full", "") or r.get("date", ""),
            r.get("views", ""),
            r.get("total_reactions", "") or "",
            reactions_str,
            r.get("message_link", ""),
            r.get("account_name", ""),
        ]
        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=row_num, column=col_idx, value=val)
            cell.fill = fill
            cell.border = _CELL_BORDER
            cell.alignment = Alignment(
                horizontal="right" if col_idx > 1 else "center",
                vertical="center",
                wrap_text=True,
            )
        ws.row_dimensions[row_num].height = 40

    # Freeze top row
    ws.freeze_panes = "A2"

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
