import io
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.event import SystemEventRead
from app.services.system_event_service import list_system_events

router = APIRouter(prefix="/events", tags=["events"])


# Export icin desteklenen formatlar. csv + json frontend tarafinda da
# uretilebilir ama endpoint backend-side fallback olarak duruyor (curl/script
# entegrasyonlari icin). xlsx + pdf sadece backend uzerinden.
_EXPORT_FORMATS = ("csv", "json", "xlsx", "pdf")


@router.get("", response_model=list[SystemEventRead])
def list_events(
    category: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    actor_username: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return list_system_events(db, category=category, severity=severity, actor_username=actor_username)


def _format_rows_for_export(events) -> list[dict]:
    """Tum format'lar icin ortak satir yapisi. ISO timestamp + flat fields."""
    rows = []
    for ev in events:
        meta_summary = ""
        if ev.metadata_json:
            try:
                parsed = json.loads(ev.metadata_json)
                if isinstance(parsed, dict):
                    meta_summary = ", ".join(f"{k}={v}" for k, v in parsed.items())
                else:
                    meta_summary = str(parsed)
            except (json.JSONDecodeError, TypeError):
                meta_summary = (ev.metadata_json or "")[:200]
        rows.append({
            "id": ev.id,
            "created_at": ev.created_at.isoformat() if ev.created_at else "",
            "severity": ev.severity or "",
            "category": ev.category or "",
            "event_type": ev.event_type or "",
            "message": ev.message or "",
            "actor": ev.actor_username or "",
            "device": ev.device_code or "",
            "metadata": meta_summary,
        })
    return rows


def _build_csv(rows: list[dict]) -> bytes:
    """RFC 4180 uyumlu CSV. UTF-8 BOM ile basliyor — Excel Turkce karakteri
    bozmadan acsin."""
    import csv

    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    csv_bytes = ("﻿" + buf.getvalue()).encode("utf-8")
    return csv_bytes


def _build_xlsx(rows: list[dict]) -> bytes:
    """openpyxl ile .xlsx — hucre tipleri korunur, baslik kalin, donduruldu."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Events"

    if not rows:
        ws.append(["No events to export"])
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()

    headers = list(rows[0].keys())
    ws.append(headers)
    # Header satiri: kalin + soluk turuncu arka plan (marka uyumu)
    header_font = Font(bold=True, color="FFFFFFFF")
    header_fill = PatternFill(start_color="FFFF8C00", end_color="FFFF8C00", fill_type="solid")
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="left", vertical="center")

    for row in rows:
        ws.append([row.get(h, "") for h in headers])

    # Sutun genisliklerini icerige gore otomatik ayarla
    col_widths = {h: max(len(h), 12) for h in headers}
    for row in rows:
        for h in headers:
            v = str(row.get(h, ""))
            col_widths[h] = min(60, max(col_widths[h], len(v)))
    for i, h in enumerate(headers, start=1):
        from openpyxl.utils import get_column_letter

        ws.column_dimensions[get_column_letter(i)].width = col_widths[h] + 2

    # Ilk satiri dondur ki scroll yaparken baslik kalsin
    ws.freeze_panes = "A2"

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _build_pdf(rows: list[dict]) -> bytes:
    """reportlab ile A4 landscape PDF. Baslik + uretim zamani + tablo +
    sayfa numarasi. Buyuk export'larda tek tabloyu sayfalara boler."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate,
            Table,
            TableStyle,
            Paragraph,
            Spacer,
        )
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="reportlab kutuphanesi yuklu degil; PDF export devre disi. "
            "Sistem yoneticisine 'pip install reportlab' calistirmasini soyleyin.",
        )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title="EnerjiOne Event Report",
    )
    styles = getSampleStyleSheet()
    story = []

    # Baslik
    story.append(Paragraph("<b>EnerjiOne — Event Report</b>", styles["Title"]))
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    story.append(Paragraph(f"Generated: {generated} · Total events: {len(rows)}", styles["Normal"]))
    story.append(Spacer(1, 8))

    # Tablo
    # PDF sutun seti CSV/Excel'den daha dar (sayfa genisligi sinirli) —
    # metadata kolonunu cikartiyoruz, message'i kisaltarak veriyoruz.
    visible_keys = ["created_at", "severity", "category", "event_type", "message", "actor", "device"]
    headers = ["Time", "Severity", "Category", "Type", "Message", "User", "Device"]
    data = [headers]
    for r in rows:
        msg = str(r.get("message", ""))
        if len(msg) > 80:
            msg = msg[:77] + "..."
        data.append([
            str(r.get("created_at", ""))[:19],  # YYYY-MM-DD HH:MM:SS
            str(r.get("severity", "")),
            str(r.get("category", "")),
            str(r.get("event_type", "")),
            msg,
            str(r.get("actor", "")),
            str(r.get("device", "")),
        ])

    if len(data) > 1:
        tbl = Table(data, repeatRows=1, colWidths=[
            38 * mm, 18 * mm, 28 * mm, 32 * mm, 90 * mm, 22 * mm, 22 * mm,
        ])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ff8c00")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#fafafa"), colors.white]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(tbl)
    else:
        story.append(Paragraph("<i>No events match the current filters.</i>", styles["Normal"]))

    doc.build(story)
    return buf.getvalue()


@router.get("/export")
def export_events(
    fmt: Literal["csv", "json", "xlsx", "pdf"] = Query("csv", description="Export format"),
    category: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    actor_username: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Filtreli event listesini CSV/JSON/XLSX/PDF olarak indir.

    Frontend Events sayfasinda Export modal'i bu endpoint'i cagiriyor.
    Filtre query param'lari list_events ile birebir ayni — UI'da uygulanan
    kategori/severity/actor filtreleri ayni davranisi gosterir.
    """
    if fmt not in _EXPORT_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported format: {fmt!r}. Allowed: {', '.join(_EXPORT_FORMATS)}",
        )

    events = list_system_events(
        db, category=category, severity=severity, actor_username=actor_username
    )
    rows = _format_rows_for_export(events)
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename_base = f"events-{now}"

    if fmt == "csv":
        content = _build_csv(rows)
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename_base}.csv"'},
        )
    if fmt == "json":
        content = json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")
        return Response(
            content=content,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename_base}.json"'},
        )
    if fmt == "xlsx":
        content = _build_xlsx(rows)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename_base}.xlsx"'},
        )
    # pdf
    content = _build_pdf(rows)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename_base}.pdf"'},
    )
