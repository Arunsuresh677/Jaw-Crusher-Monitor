"""
reports.py — PDF and CSV report generation
Crusher Monitor — Kannan Blue Metals

Generates downloadable shift reports from DB data using ReportLab.
Called by GET /api/reports/shift-pdf and GET /api/reports/shift-csv
"""

import csv
import io
import logging
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

log = logging.getLogger(__name__)


# ── Time helpers ─────────────────────────────────────────────────────────

def _to_12h(dt_str: str) -> str:
    """Convert 'YYYY-MM-DD HH:MM:SS' or 'HH:MM:SS' to 12-hour format."""
    if not dt_str or dt_str in ("—", "None", "null"):
        return "—"
    try:
        s = dt_str[:19]
        fmt = "%Y-%m-%d %H:%M:%S" if len(s) == 19 and "-" in s else "%H:%M:%S"
        dt = datetime.strptime(s, fmt)
        return dt.strftime("%I:%M:%S %p").lstrip("0") or "12:00:00 AM"
    except Exception:
        return dt_str


def _hms_to_secs(hms: str) -> int:
    """Convert HH:MM:SS duration string to total seconds."""
    try:
        h, m, s = hms.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except Exception:
        return 0


def _secs_to_hms(secs: int) -> str:
    """Convert total seconds to HH:MM:SS duration string."""
    secs = max(0, int(secs))
    return f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"


def _calc_elapsed(start_str: str, end_str: str) -> str:
    """Calculate End − Start and return as HH:MM:SS."""
    try:
        fmt = "%Y-%m-%d %H:%M:%S"
        start = datetime.strptime(start_str[:19], fmt)
        end   = datetime.strptime(end_str[:19],   fmt)
        return _secs_to_hms(int((end - start).total_seconds()))
    except Exception:
        return "—"

# ── Brand colours ────────────────────────────────────────────────────────
AMBER  = colors.HexColor("#F5A623")
NAVY   = colors.HexColor("#1A2B5E")
GREEN  = colors.HexColor("#1DB97A")
RED    = colors.HexColor("#E03A3A")
ORANGE = colors.HexColor("#F07C20")
GREY   = colors.HexColor("#8892B0")
LIGHT  = colors.HexColor("#F0F2FF")
WHITE  = colors.white
BLACK  = colors.HexColor("#0D1320")


# ── Style helpers ────────────────────────────────────────────────────────
def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", fontName="Helvetica-Bold", fontSize=22,
            leading=26,                # explicit line height (was auto = 20 * 1.2 = 24)
            textColor=NAVY, spaceAfter=0, alignment=TA_LEFT,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", fontName="Helvetica", fontSize=9.5,
            leading=14,                # explicit line height; bigger than auto to avoid overlap
            textColor=GREY, spaceAfter=0, alignment=TA_LEFT,
        ),
        "section": ParagraphStyle(
            "section", fontName="Helvetica-Bold", fontSize=11,
            textColor=NAVY, spaceBefore=14, spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "small", fontName="Helvetica", fontSize=8,
            textColor=GREY, alignment=TA_CENTER,
        ),
        "meta": ParagraphStyle(
            "meta", fontName="Helvetica", fontSize=9,
            textColor=BLACK, spaceAfter=3,
        ),
        "meta_bold": ParagraphStyle(
            "meta_bold", fontName="Helvetica-Bold", fontSize=9,
            textColor=BLACK, spaceAfter=3,
        ),
    }


def _oee_color(val: float) -> colors.HexColor:
    if val >= 85: return GREEN
    if val >= 65: return ORANGE
    return RED


def _availability_color(val: float) -> colors.HexColor:
    return _oee_color(val)


# ── PDF builder ──────────────────────────────────────────────────────────

def build_shift_pdf(shift_rows: list[dict], live_state: dict) -> bytes:
    """
    Build a PDF from shift_rows (from DB) and live_state (from crusher_logic).
    Returns raw PDF bytes suitable for a StreamingResponse.

    Layout:
      1. Header (company + plant + generation timestamp)
      2. Daily Summary — Total run time / Stuck time / No-feed time / Model session
      3. Shift History table (from DB)
      4. Footer
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        title="Crusher Shift Report — Kannan Blue Metals",
        author="Crusher Monitor v6.3",
    )
    S = _styles()
    generated = datetime.now().strftime("%d %b %Y  %H:%M")
    story = []

    # ── Header — three independent flowables for bulletproof spacing ─
    # 1. Title paragraph
    # 2. Spacer
    # 3. Subtitle paragraph
    # 4. Spacer  ← clear vertical gap
    # 5. Divider line as a Table with a coloured background (1.5pt high)
    # 6. Spacer
    story.append(Paragraph("Kannan Blue Metals", S["title"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"Jaw Crusher Monitor — Shift Report  ·  "
        f"Chennimalai, Erode  ·  Generated {generated}",
        S["subtitle"],
    ))
    story.append(Spacer(1, 14))                # << clear gap before divider

    divider = Table([[""]], colWidths=[doc.width], rowHeights=[1.5])
    divider.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), AMBER),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(divider)
    story.append(Spacer(1, 16))                # << clear gap after divider

    # ── Daily Summary (the four metrics that matter) ──────────────
    story.append(Paragraph("Daily Summary", S["section"]))

    t_stk  = live_state.get("timer_stuck",   "00:00:00")
    t_nfd  = live_state.get("timer_no_feed", "00:00:00")
    avail  = live_state.get("availability_pct", 0)

    # ── Production Start Time (first NORMAL detection) ────────────────────
    first_run_str   = live_state.get("first_run_at")
    prod_start_disp = _to_12h(first_run_str) if first_run_str else "Not yet started"

    # ── End Time (last AI frame — if recent, show "Running") ──────────────
    last_act_str  = live_state.get("last_active", "—")
    end_time_disp = _to_12h(last_act_str)
    try:
        last_dt = datetime.strptime(last_act_str[:19], "%Y-%m-%d %H:%M:%S")
        if (datetime.now() - last_dt).total_seconds() < 60:
            end_time_disp = f"Running  ({_to_12h(last_act_str)})"
    except Exception:
        pass

    # ── Total Run Time = End − Production Start ───────────────────────────
    if first_run_str and last_act_str and last_act_str != "—":
        total_run_hms = _calc_elapsed(first_run_str, last_act_str)
    else:
        total_run_hms = "—"

    # ── Total Production Time = Total Run − (Stuck + No Feed) ─────────────
    run_secs  = _hms_to_secs(total_run_hms) if total_run_hms != "—" else 0
    prod_secs = max(0, run_secs - _hms_to_secs(t_stk) - _hms_to_secs(t_nfd))
    total_prod_hms = _secs_to_hms(prod_secs) if run_secs > 0 else "—"

    # ── Model Active Window ───────────────────────────────────────────────
    sess_start_str  = live_state.get("session_start", "—")
    model_window    = f"{_to_12h(sess_start_str)}  →  {end_time_disp}"

    # Row layout (header = row 0):
    # 1  Production Start Time  — blue
    # 2  End Time               — grey
    # 3  Total Run Time         — green
    # 4  Stone Stuck Time       — red
    # 5  No Raw Material Time   — orange
    # 6  Total Production Time  — green bold
    # 7  Model Active Window    — neutral
    # 8  Availability           — dynamic colour
    summary_data = [
        ["Metric",                      "Value"],
        ["Production Start Time",       prod_start_disp],
        ["End Time",                    end_time_disp],
        ["Total Run Time",              total_run_hms],
        ["Stone Stuck Time",            t_stk],
        ["No Raw Material Time",        t_nfd],
        ["Total Production Time",       total_prod_hms],
        ["Model Active Window (today)", model_window],
        ["Availability",                f"{avail:.1f} %"],
    ]

    col_w = [80*mm, 80*mm]
    summary_table = Table(summary_data, colWidths=col_w)
    summary_table.setStyle(TableStyle([
        # Header row
        ("BACKGROUND",    (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR",     (0, 0), (-1, 0), WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 10),
        ("ALIGN",         (0, 0), (-1, 0), "LEFT"),
        # Body base styles
        ("BACKGROUND",    (0, 1), (0, -1), LIGHT),
        ("FONTNAME",      (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",      (1, 1), (1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 1), (-1, -1), 10),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#D0D4E8")),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [WHITE, colors.HexColor("#F7F8FC")]),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("TOPPADDING",    (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        # Row 1 — Production Start Time — blue
        ("TEXTCOLOR",     (1, 1), (1, 1), colors.HexColor("#1A6FD4")),
        ("FONTNAME",      (1, 1), (1, 1), "Helvetica-Bold"),
        # Row 2 — End Time — grey
        ("TEXTCOLOR",     (1, 2), (1, 2), GREY),
        ("FONTNAME",      (1, 2), (1, 2), "Helvetica"),
        # Row 3 — Total Run Time — green
        ("TEXTCOLOR",     (1, 3), (1, 3), GREEN),
        ("FONTNAME",      (1, 3), (1, 3), "Helvetica-Bold"),
        # Row 4 — Stone Stuck — red
        ("TEXTCOLOR",     (1, 4), (1, 4), RED),
        ("FONTNAME",      (1, 4), (1, 4), "Helvetica-Bold"),
        # Row 5 — No Raw Material — orange
        ("TEXTCOLOR",     (1, 5), (1, 5), ORANGE),
        ("FONTNAME",      (1, 5), (1, 5), "Helvetica-Bold"),
        # Row 6 — Total Production Time — green bold
        ("TEXTCOLOR",     (1, 6), (1, 6), GREEN),
        ("FONTNAME",      (1, 6), (1, 6), "Helvetica-Bold"),
        ("BACKGROUND",    (0, 6), (-1, 6), colors.HexColor("#EAF7F1")),
        # Row 8 — Availability — dynamic colour
        ("TEXTCOLOR",     (1, 8), (1, 8), _availability_color(avail)),
        ("FONTNAME",      (1, 8), (1, 8), "Helvetica-Bold"),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 14))

    # ── Shift history table ───────────────────────────────────────
    story.append(Paragraph(
        f"Shift History  ·  last {len(shift_rows)} shifts",
        S["section"],
    ))

    if not shift_rows:
        story.append(Paragraph("No shift records found in the database.", S["meta"]))
    else:
        headers = [
            "Date / Time", "Shift", "Runtime",
            "Stuck", "No Feed", "Avail %", "Tonnes", "Alerts",
        ]
        col_w2 = [36*mm, 16*mm, 22*mm, 18*mm, 18*mm, 18*mm, 18*mm, 14*mm]
        table_data = [headers]

        for row in shift_rows:
            avail_val = float(row.get("availability_pct", 0))
            table_data.append([
                row.get("timestamp", "—")[:16],
                row.get("shift_type", "—").title(),
                row.get("total_runtime", "—"),
                row.get("total_stuck", "—"),
                row.get("total_no_feed", "—"),
                f"{avail_val:.1f}%",
                f"{float(row.get('tonnage_actual', 0)):.1f}",
                str(row.get("total_alerts", 0)),
            ])

        history_table = Table(table_data, colWidths=col_w2, repeatRows=1)

        # Build per-cell styles for availability column (col 5)
        cell_styles = [
            ("BACKGROUND",    (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR",     (0, 0), (-1, 0), WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, 0), 8),
            ("ALIGN",         (0, 0), (-1, 0), "CENTER"),
            ("FONTSIZE",      (0, 1), (-1, -1), 7.5),
            ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#D0D4E8")),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [WHITE, colors.HexColor("#F7F8FC")]),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN",         (1, 0), (-1, -1), "CENTER"),
        ]
        # Colour availability cells per value
        for i, row in enumerate(shift_rows, start=1):
            a = float(row.get("availability_pct", 0))
            cell_styles.append(("TEXTCOLOR", (5, i), (5, i), _availability_color(a)))
            cell_styles.append(("FONTNAME",  (5, i), (5, i), "Helvetica-Bold"))

        history_table.setStyle(TableStyle(cell_styles))
        story.append(history_table)

    story.append(Spacer(1, 14))

    # ── Footer — same three-flowable pattern ──────────────────────
    story.append(Spacer(1, 14))
    footer_divider = Table([[""]], colWidths=[doc.width], rowHeights=[0.8])
    footer_divider.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), GREY),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(footer_divider)
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        f"Crusher Monitor v6.3  ·  Kannan Blue Metals, Chennimalai, Erode  ·  "
        f"Report generated {generated}",
        S["small"],
    ))

    doc.build(story)
    return buf.getvalue()


# ── CSV builder ──────────────────────────────────────────────────────────

def build_shift_csv(shift_rows: list[dict], live_state: dict | None = None) -> str:
    """
    Build a CSV string with:
      - Daily Summary header block (today's run / stuck / no-feed / model window)
      - Blank line
      - Shift history rows from the DB

    Returns UTF-8 string suitable for a Response / download.
    """
    buf = io.StringIO()
    buf.write("# Kannan Blue Metals — Crusher Shift Report\n")
    buf.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    buf.write("#\n")

    # ── Daily Summary block ────────────────────────────────────
    if live_state:
        sess_start = live_state.get("session_start", "—")
        last_act   = live_state.get("last_active",   "—")
        first_run = live_state.get("first_run_at")
        last_act  = live_state.get("last_active", "—")
        t_stk_csv = live_state.get("timer_stuck",   "00:00:00")
        t_nfd_csv = live_state.get("timer_no_feed", "00:00:00")

        # Total Run Time = End - Start
        total_run_csv = _calc_elapsed(first_run, last_act) if first_run else "—"

        # Total Production Time = Total Run - Stuck - No Feed
        r = _hms_to_secs(total_run_csv) if total_run_csv != "—" else 0
        prod_csv = _secs_to_hms(max(0, r - _hms_to_secs(t_stk_csv) - _hms_to_secs(t_nfd_csv))) if r > 0 else "—"

        buf.write("# Daily Summary\n")
        buf.write(f"metric,value\n")
        buf.write(f"Production Start Time,{_to_12h(first_run) if first_run else 'Not yet started'}\n")
        buf.write(f"End Time,{_to_12h(last_act)}\n")
        buf.write(f"Total Run Time,{total_run_csv}\n")
        buf.write(f"Stone Stuck Time,{t_stk_csv}\n")
        buf.write(f"No Raw Material Time,{t_nfd_csv}\n")
        buf.write(f"Total Production Time,{prod_csv}\n")
        buf.write(f"Model Session Start,{_to_12h(live_state.get('session_start', '—'))}\n")
        buf.write(f"Availability %,{live_state.get('availability_pct', 0)}\n")
        buf.write(f"Tonnage (t),{live_state.get('tonnage_actual', 0)}\n")
        buf.write("\n")

    # ── Shift history ──────────────────────────────────────────
    buf.write("# Shift History\n")
    fieldnames = [
        "timestamp", "shift_type", "shift_start",
        "total_runtime", "total_stuck", "total_no_feed",
        "availability_pct", "tonnage_actual", "total_alerts",
    ]
    writer = csv.DictWriter(
        buf, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    for row in shift_rows:
        writer.writerow(row)
    return buf.getvalue()
