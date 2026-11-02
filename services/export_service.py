import io
from datetime import datetime
from django.http import HttpResponse
from django.utils import timezone
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


def export_to_excel_response(filename_prefix: str, sheet_title: str, headers: list, rows: list, title: str = None) -> HttpResponse:
    """
    Generates a beautifully styled .xlsx spreadsheet HTTP download response.
    Includes branded header row, alternating fill colors, auto column widths, and proper cell alignments.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title[:31]  # Excel sheet title max 31 chars
    ws.views.sheetView[0].showGridLines = True

    # Styling definitions
    brand_header_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")  # Dark slate navy
    brand_header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

    title_font = Font(name="Calibri", size=14, bold=True, color="0F172A")
    meta_font = Font(name="Calibri", size=9, italic=True, color="64748B")

    thin_border_side = Side(style="thin", color="CBD5E1")
    cell_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)

    zebra_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

    current_row = 1

    # Optional top title banner
    if title:
        ws.cell(row=current_row, column=1, value=title).font = title_font
        current_row += 1
        gen_time = timezone.now().strftime("%d-%b-%Y %I:%M %p")
        ws.cell(row=current_row, column=1, value=f"Generated from LokalLoop Admin Console on {gen_time} | Total Records: {len(rows)}").font = meta_font
        current_row += 2  # Blank row separation

    # Header Row
    header_row_idx = current_row
    for col_idx, header_text in enumerate(headers, 1):
        cell = ws.cell(row=header_row_idx, column=col_idx, value=header_text)
        cell.font = brand_header_font
        cell.fill = brand_header_fill
        cell.alignment = Alignment(horizontal="center" if "Status" in header_text or "ID" in header_text or "Date" in header_text else "left", vertical="center", wrap_text=True)
        cell.border = cell_border
    ws.row_dimensions[header_row_idx].height = 26

    # Data Rows
    current_row += 1
    for row_idx, row_data in enumerate(rows, start=current_row):
        is_even = (row_idx % 2 == 0)
        for col_idx, value in enumerate(row_data, 1):
            # Neutralize CSV / Excel Formula Injection (CWE-1236)
            if isinstance(value, str) and value:
                stripped = value.lstrip()
                if stripped.startswith(('=', '+', '-', '@')):
                    value = f"'{value}"
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.font = Font(name="Calibri", size=10)
            cell.border = cell_border
            if is_even:
                cell.fill = zebra_fill

            # Alignment logic based on value type or header
            header_name = headers[col_idx - 1] if col_idx - 1 < len(headers) else ""
            if any(term in header_name.lower() for term in ["amount", "balance", "points", "credits", "count", "rating"]):
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif any(term in header_name.lower() for term in ["status", "date", "id", "phone", "pin", "gst"]):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

        ws.row_dimensions[row_idx].height = 20

    # Auto-adjust column widths with minimum and padding
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.row < header_row_idx:
                continue  # Skip title banner row from width calculation
            val = str(cell.value or "")
            if len(val) > max_len:
                max_len = len(val)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    # Save to in-memory buffer
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    # Prepare HTTP response
    timestamp_str = timezone.localtime(timezone.now()).strftime("%Y%m%d_%H%M")
    filename = f"{filename_prefix}_{timestamp_str}.xlsx"
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
