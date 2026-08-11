"""ARIZA RAPORU (PDF) — musteriye/arsive giden belge.

NEDEN SUNUCUDA
--------------
Rapor once tarayicinin `window.print()` ciktisiydi. Cikan sey bir BELGE degil,
EKRANIN kagida dokulmus haliydi: kartlar sayfa ortasindan bolunuyor, tarayici
kendi ustbilgisini (tarih + sekme adi) ve altbilgisini (IP adresi) basiyor,
kapali arizada harita bembeyaz cikiyordu. Bir de kagit duzeni ekran duzenine
bagliydi: kullanici pencereyi kucultunce rapor da degisiyordu.

Artik belge burada kuruluyor:
  - Ustbilgi/altbilgi `report_layout.ReportCanvas` ile Olay Raporu ile AYNI
    (solda EnerjiOne, sagda musteri logosu, altta "Sayfa X / Y").
  - Harita figuru `fault_report_map` ile uydu karolari uzerine, KAYITTAKI
    cihazlardan cizilir (ekrandaki canli alarm durumundan degil).
  - Bolumler bos veriyle basilmaz: "—" dolu bir tablo bilgi tasimadigi gibi,
    gercekten bilinen iki degeri de gorunmez kilar.

DIL: rapor TURKCE tek dilde uretilir — Olay Raporu ile ayni tercih. Etiketler
arayuzdeki (tr.json) metinlerin AYNISIDIR; ekranla rapor arasinda terim
farki olmasi sahada "hangisi dogru" sorusuna yol aciyor.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.data.fault_causes import FAULT_CAUSES
from app.models.project_settings import ProjectSettings
from app.schemas.fault import FaultCommentRead, FaultEventRead
from app.services.report_layout import (
    BRAND_ORANGE,
    FOOTER_HEIGHT,
    HEADER_HEIGHT,
    INK,
    MUTED,
    RULE,
    ReportCanvas,
    decode_data_url_image,
    format_report_time,
    report_fonts,
)

SOFT = colors.HexColor("#f8fafc")
CONTENT_W = A4[0] - 24 * mm

#: Durum -> (arayuzdeki etiket, renk). tr.json `faults.status` ile ayni.
STATUS_LABELS: dict[str, tuple[str, str]] = {
    "open": ("Açık", "#ef4444"),
    "assigned": ("Atandı", "#f59e0b"),
    "in_progress": ("Devam Ediyor", "#3b82f6"),
    "resolved": ("Normale Döndü", "#10b981"),
    "closed": ("Kapatıldı", "#64748b"),
}
KIND_LABELS = {"permanent": "Kalıcı", "transient": "Geçici"}
DIRECTION_LABELS = {"green_a": "İleri (Yeşil / A)", "red_b": "Geri (Kırmızı / B)"}
SOURCE_LABELS = {"master": "Master", "sat01": "Satellite 01", "sat02": "Satellite 02"}


@dataclass
class ReportPole:
    """Ariza araligindaki bir direk — sahaya GPS ile gidilecek satir."""

    sequence_no: int
    name: str
    latitude: float
    longitude: float
    #: "start" | "end" | "zone"
    role: str


@dataclass
class ReportRecurrence:
    total: int
    window_days: int
    same_section: int
    last_at: datetime | None


# ---------------------------------------------------------------------------
# Bicimleme
# ---------------------------------------------------------------------------
def _esc(text: object) -> str:
    """Paragraph'a giden SERBEST METNI kacisla.

    reportlab Paragraph icerigi XML gibi ayristirir: bir yorumda gecen
    "R&D" ya da "a<b" belge kurulumunu HATA ile dusuruyordu. Kullanicinin
    ya da cihazin yazdigi her metin buradan gecer.
    """
    return escape(str(text if text is not None else ""))


def _upper_tr(text: str) -> str:
    """Turkce buyuk harf. `str.upper()` 'i' -> 'I' verir: basliklar "ÇIZELGESI",
    "TESPIT", "CIHAZ" diye basiliyordu. Once 'i' -> 'İ' esleniyor."""
    return text.replace("i", "İ").upper()


def _tr_number(value: float, decimals: int = 0) -> str:
    """1234.5 -> '1.234,5' (tr-TR). Arayuzdeki toLocaleString karsiligi."""
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def format_distance(meters: float | None) -> str:
    """1 km altinda '520 m', ustunde '1,24 km' (lineDistance.ts ile ayni)."""
    if meters is None:
        return "—"
    value = abs(meters)
    if value < 1000:
        return f"{_tr_number(round(value))} m"
    return f"{_tr_number(value / 1000, 2)} km"


def format_distance_range(from_m: float | None, to_m: float | None) -> str | None:
    if from_m is None:
        return None
    if to_m is None:
        return format_distance(from_m)
    lo, hi = min(from_m, to_m), max(from_m, to_m)
    if hi - lo < 1:
        return format_distance(lo)
    return f"{format_distance(lo)} – {format_distance(hi)}"


def format_duration(start: datetime, end: datetime) -> str:
    """'1g 2sa 44dk' — arayuzdeki elapsedText ile ayni kurallar."""
    seconds = max(0, int(round((end - start).total_seconds())))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}g {hours}sa {minutes}dk"
    if hours:
        return f"{hours}sa {minutes}dk {seconds}sn"
    if minutes:
        return f"{minutes}dk {seconds}sn"
    return f"{seconds}sn"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _clock(value: datetime | None) -> str:
    if value is None:
        return "—"
    return _aware(value).astimezone().strftime("%H:%M")


def _cause_label(code: str | None) -> str | None:
    if not code:
        return None
    for cause in FAULT_CAUSES:
        if cause["code"] == code:
            return cause["label_tr"]
    return code


# ---------------------------------------------------------------------------
# Belge kurulumu
# ---------------------------------------------------------------------------
class _Styles:
    """Tek yerde tanimli tipografi — bolumler arasi kayma olmasin."""

    def __init__(self) -> None:
        regular, bold = report_fonts()
        self.regular, self.bold = regular, bold
        self.eyebrow = ParagraphStyle(
            "eyebrow", fontName=bold, fontSize=7.5, leading=10, textColor=MUTED
        )
        self.title = ParagraphStyle(
            "docTitle", fontName=bold, fontSize=19, leading=22, textColor=INK
        )
        self.crumb = ParagraphStyle(
            "crumb", fontName=regular, fontSize=8.5, leading=11.5, textColor=MUTED
        )
        self.section = ParagraphStyle(
            "section", fontName=bold, fontSize=10, leading=13, textColor=INK
        )
        self.label = ParagraphStyle(
            "label", fontName=regular, fontSize=7.2, leading=9, textColor=MUTED
        )
        self.value = ParagraphStyle(
            "value", fontName=bold, fontSize=10.5, leading=13, textColor=INK
        )
        self.body = ParagraphStyle(
            "body", fontName=regular, fontSize=8.6, leading=12, textColor=INK
        )
        self.body_muted = ParagraphStyle("bodyMuted", parent=self.body, textColor=MUTED)
        self.cell = ParagraphStyle(
            "cell", fontName=regular, fontSize=8, leading=10.5, textColor=INK, wordWrap="CJK"
        )
        self.cell_bold = ParagraphStyle("cellBold", parent=self.cell, fontName=bold)
        self.cell_right = ParagraphStyle("cellRight", parent=self.cell, alignment=TA_RIGHT)
        self.th = ParagraphStyle(
            "th", parent=self.cell, fontName=bold, fontSize=8, textColor=colors.white
        )
        self.kv_label = ParagraphStyle(
            "kvLabel", fontName=regular, fontSize=8.2, leading=11, textColor=MUTED
        )
        self.kv_value = ParagraphStyle(
            "kvValue", fontName=bold, fontSize=8.6, leading=11, textColor=INK,
            alignment=TA_RIGHT,
        )
        self.caption = ParagraphStyle(
            "caption", fontName=regular, fontSize=7.4, leading=10, textColor=MUTED,
            alignment=TA_LEFT,
        )
        self.pill = ParagraphStyle(
            "pill", fontName=bold, fontSize=11, leading=14, textColor=colors.white,
            alignment=TA_RIGHT,
        )


def _section_head(st: _Styles, title: str, hint: str = "") -> Table:
    """Bolum basligi: solda turuncu dikey cubuk, altta ince kural.

    Baslikta ikon YOK — PDF'te ikon fontu garanti degil ve eksik glif
    kutu olarak basiliyor.
    """
    right = Paragraph(hint, st.caption) if hint else ""
    table = Table(
        [[Paragraph(_upper_tr(title), st.section), right]],
        colWidths=[CONTENT_W * 0.62, CONTENT_W * 0.38],
    )
    table.setStyle(
        TableStyle(
            [
                ("LINEBEFORE", (0, 0), (0, 0), 2.4, BRAND_ORANGE),
                ("LINEBELOW", (0, 0), (-1, -1), 0.6, RULE),
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (0, 0), 6),
                ("LEFTPADDING", (1, 0), (1, 0), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _block(head: Table, first, *rest) -> list:
    """Bolum: basligi ILK icerik parcasindan AYIRMA.

    Baslik sayfanin dibinde yalniz kaldiginda okuyucu sonraki sayfadaki
    tablonun neyin tablosu oldugunu bilemiyor, geri donmek zorunda kaliyor.
    Kalan parcalar serbest akar (uzun tablo sayfa bolebilir — basligi
    `repeatRows` tekrar basar).
    """
    return [Spacer(1, 12), KeepTogether([head, Spacer(1, 6), first]), *rest]


def _stat_strip(st: _Styles, cells: list[tuple[str, str, str | None]]) -> Table:
    """Dortlu olcu seridi — (etiket, deger, deger rengi)."""
    rows: list[list] = []
    for index in range(0, len(cells), 4):
        chunk = cells[index : index + 4]
        while len(chunk) < 4:
            chunk.append(("", "", None))
        row = []
        for label, value, color in chunk:
            style = st.value
            if color:
                style = ParagraphStyle(f"v{color}", parent=st.value, textColor=colors.HexColor(color))
            row.append(
                [Paragraph(_upper_tr(label), st.label), Paragraph(_esc(value) or "—", style)]
                if label
                else ""
            )
        rows.append(row)
    width = CONTENT_W / 4
    table = Table(rows, colWidths=[width] * 4)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("BOX", (0, 0), (-1, -1), 0.6, RULE),
                ("INNERGRID", (0, 0), (-1, -1), 0.6, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def _kv_grid(st: _Styles, pairs: list[tuple[str, str]], columns: int = 2) -> Table:
    """Kunye izgarasi: solda soluk etiket, sagda kalin deger (sag hizali).

    Iki kolon: A4 dikeyde tek kolon sayfayi gereksiz uzatiyor, dort kolon
    degerleri sikistiriyor.
    """
    rows: list[list] = []
    for index in range(0, len(pairs), columns):
        chunk = pairs[index : index + columns]
        row: list = []
        for label, value in chunk:
            row.append(Paragraph(_esc(label), st.kv_label))
            row.append(Paragraph(_esc(value), st.kv_value))
        while len(row) < columns * 2:
            row.append("")
        rows.append(row)
    unit = CONTENT_W / columns
    widths: list[float] = []
    for _ in range(columns):
        widths += [unit * 0.58, unit * 0.42]
    table = Table(rows, colWidths=widths)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]
    for row_index in range(len(rows)):
        for column in range(columns):
            # Etiket-deger cifti bir arada okunsun: her cift kendi noktali
            # cizgisini alir (uzun izgarada satir kaymasini engeller).
            style.append(
                ("LINEBELOW", (column * 2, row_index), (column * 2 + 1, row_index), 0.4, RULE)
            )
            if column:
                style.append(("LEFTPADDING", (column * 2, row_index), (column * 2, row_index), 12))
    table.setStyle(TableStyle(style))
    return table


def _data_table(st: _Styles, headers: list[str], rows: list[list], widths: list[float]) -> Table:
    """Turuncu baslikli, zebra satirli veri tablosu (Olay Raporu ile ayni dil)."""
    data = [[Paragraph(h, st.th) for h in headers]] + rows
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BRAND_ORANGE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
                ("GRID", (0, 0), (-1, -1), 0.25, RULE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _note_box(st: _Styles, title: str, body: str, accent: str = "#e97800") -> Table:
    """Serbest metin kutusu — sol kenarinda renkli cubuk."""
    table = Table(
        [
            [Paragraph(_esc(title), st.label)],
            # Kacislama SONRA satir sonu: once <br/> konsa kacislama onu da
            # metne cevirir ve saha notu tek satira yapisirdi.
            [Paragraph(_esc(body).replace("\n", "<br/>"), st.body)],
        ],
        colWidths=[CONTENT_W],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("LINEBEFORE", (0, 0), (0, -1), 2.4, colors.HexColor(accent)),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (0, 0), 6),
                ("BOTTOMPADDING", (0, 0), (0, 0), 1),
                ("TOPPADDING", (0, 1), (0, 1), 0),
                ("BOTTOMPADDING", (0, 1), (0, 1), 7),
            ]
        )
    )
    return table


# ---------------------------------------------------------------------------
# Rapor
# ---------------------------------------------------------------------------
def build_fault_report_pdf(
    *,
    fault: FaultEventRead,
    comments: Sequence[FaultCommentRead] = (),
    zone_poles: Sequence[ReportPole] = (),
    recurrence: ReportRecurrence | None = None,
    settings_row: ProjectSettings | None = None,
    map_png: bytes | None = None,
    generated_by: str = "",
) -> bytes:
    """Tek arizanin A4 dikey raporu -> PDF bayt dizisi."""
    st = _Styles()
    customer_name = (settings_row.customer_name if settings_row else None) or ""
    project_name = (settings_row.project_name if settings_row else None) or ""
    customer_logo = decode_data_url_image(settings_row.customer_logo if settings_row else None)

    opened = _aware(fault.opened_at)
    end_ref = fault.closed_at or fault.resolved_at
    duration = format_duration(opened, _aware(end_ref) if end_ref else datetime.now(timezone.utc))
    status_label, status_color = STATUS_LABELS.get(fault.status, (fault.status, "#64748b"))

    generated = format_report_time(datetime.now(timezone.utc), with_seconds=True)
    footer_bits = [f"Oluşturma: {generated}", f"Arıza kaydı #{fault.id}"]
    if generated_by:
        footer_bits.append(f"Düzenleyen: {generated_by}")
    ReportCanvas.configure(
        title="Arıza Raporu",
        subtitle=" · ".join([p for p in (project_name, customer_name) if p]),
        footer_left="  ·  ".join(footer_bits),
        customer_logo=customer_logo,
        customer_name=customer_name,
    )

    margin = 12 * mm
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        # Ustbilgi/altbilgi seritleri canvas'ta ciziliyor; govde onlarla
        # cakismasin diye kenar bosluklari serit yuksekligi kadar buyuk.
        topMargin=margin + HEADER_HEIGHT,
        bottomMargin=margin + FOOTER_HEIGHT,
        title=f"EnerjiOne — Arıza Raporu #{fault.id}",
        author="EnerjiOne Grid",
        subject=f"{fault.region_name} / {fault.line_name}",
    )

    story: list = []
    story.append(_title_block(st, fault, status_label, status_color, duration))
    story.append(Spacer(1, 8))
    story.append(_summary_strip(st, fault, status_label, status_color, duration))

    if map_png:
        story.append(Spacer(1, 12))
        story.append(_section_head(st, "Konum", "Arıza bölgesi — uydu görüntüsü"))
        story.append(Spacer(1, 6))
        story.extend(_map_figure(st, map_png, fault))

    spec_pairs = _spec_pairs(fault)
    if spec_pairs:
        story.append(Spacer(1, 12))
        story.append(
            KeepTogether(
                [
                    _section_head(st, "Arıza Künyesi", "Cihazın arıza anında ölçtüğü değerler"),
                    Spacer(1, 5),
                    _kv_grid(st, spec_pairs),
                ]
            )
        )
    if fault.trigger_signals:
        # Sinyal adlari uzun ve teknik; iki kolonlu izgarada sag hizali
        # sarilinca okunmuyordu. Tam genislikte tek satir.
        story.append(_kv_grid(st, [("Tetikleyen sinyaller", ", ".join(fault.trigger_signals))], columns=1))

    story.extend(_devices_section(st, fault))
    story.extend(_alarms_section(st, fault))
    story.extend(_poles_section(st, zone_poles))
    story.extend(_branches_section(st, fault))
    story.extend(_timeline_section(st, fault, recurrence))
    story.extend(_resolution_section(st, fault))
    story.extend(_comments_section(st, comments))
    story.extend(_signature_block(st, fault, generated_by))

    doc.build(story, canvasmaker=ReportCanvas)
    return buffer.getvalue()


def _title_block(
    st: _Styles,
    fault: FaultEventRead,
    status_label: str,
    status_color: str,
    duration: str,
) -> Table:
    crumb = " · ".join(
        part
        for part in (
            fault.region_name or None,
            fault.line_name or None,
            (
                f"Direk #{fault.from_pole_seq} — Direk #{fault.to_pole_seq}"
                if fault.from_pole_seq is not None and fault.to_pole_seq is not None
                else None
            ),
        )
        if part
    )
    left = [
        Paragraph(f"ARIZA KAYDI #{fault.id}", st.eyebrow),
        Paragraph(_esc(fault.line_name or f"Hat #{fault.line_id}"), st.title),
        Paragraph(_esc(crumb), st.crumb),
    ]

    pill = Table([[Paragraph(status_label, st.pill)]])
    pill.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(status_color)),
                ("ROUNDEDCORNERS", [5, 5, 5, 5]),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    pill.hAlign = "RIGHT"
    # Rozetin ALTINA tarih/sure YAZILMAZ: ikisi de hemen asagidaki olcu
    # seridinde duruyor ve tekrar, sayfanin en degerli yerini harciyordu.
    right = [pill]

    table = Table([[left, right]], colWidths=[CONTENT_W * 0.66, CONTENT_W * 0.34])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (0, 0), "TOP"),
                ("VALIGN", (1, 0), (1, 0), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 1.2, INK),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _summary_strip(
    st: _Styles,
    fault: FaultEventRead,
    status_label: str,
    status_color: str,
    duration: str,
) -> Table:
    assignee = fault.assigned_to_full_name or fault.assigned_to_username or "Atanmamış"
    # Sahaya cikan kisiye verilecek TEK sayi: araligin orta noktasi.
    estimate = "—"
    if fault.zone_start_m is not None or fault.zone_end_m is not None:
        a, b = fault.zone_start_m, fault.zone_end_m
        middle = (a + b) / 2 if a is not None and b is not None else (a if a is not None else b)
        estimate = f"~{format_distance(middle)}"

    cells: list[tuple[str, str, str | None]] = [
        ("Durum", status_label, status_color),
        ("Arıza süresi", duration, None),
        ("Atanan kullanıcı", assignee, None),
        ("Tahmini mesafe", estimate, None),
        ("Arıza açıldı", format_report_time(fault.opened_at), None),
        (
            "Normale döndü",
            format_report_time(fault.resolved_at) if fault.resolved_at else "—",
            None,
        ),
        ("Kapatıldı", format_report_time(fault.closed_at) if fault.closed_at else "—", None),
        (
            "Tür / faz",
            " · ".join(
                part
                for part in (
                    KIND_LABELS.get(fault.fault_kind or "", fault.fault_kind or ""),
                    "-".join((fault.phase or "").upper()) or "",
                )
                if part
            )
            or "—",
            None,
        ),
    ]
    return _stat_strip(st, cells)


def _map_figure(st: _Styles, map_png: bytes, fault: FaultEventRead) -> list:
    """Harita + gosterge. Figur genisligi sabit; yukseklik oranindan gelir."""
    reader = Image(io.BytesIO(map_png))
    ratio = reader.imageHeight / reader.imageWidth if reader.imageWidth else 0.55
    figure = Image(io.BytesIO(map_png), width=CONTENT_W, height=CONTENT_W * ratio)

    legend = Paragraph(
        '<font color="#ef4444">■</font> Arıza bölgesi (kesik çizgi)&nbsp;&nbsp;&nbsp;'
        '<font color="#22c55e">■</font> Sağlam kesim&nbsp;&nbsp;&nbsp;'
        '<font color="#dc2626">■</font> Arızayı gören son cihaz&nbsp;&nbsp;&nbsp;'
        '<font color="#10b981">■</font> Görmeyen ilk cihaz&nbsp;&nbsp;&nbsp;'
        '<font color="#64748b">■</font> Diğer direk / cihaz',
        st.caption,
    )
    note = Paragraph(
        "Bölge, arızayı gören son cihaz ile görmeyen ilk cihaz arasındaki kesimdir; "
        "harita arıza kaydındaki cihazlardan çizilmiştir, anlık alarm durumundan değil. "
        "Direk üzerindeki sayılar hat sıra numaralarıdır.",
        st.caption,
    )
    frame = Table([[figure]], colWidths=[CONTENT_W])
    frame.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, RULE),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return [frame, Spacer(1, 5), legend, Spacer(1, 3), note]


def _spec_pairs(fault: FaultEventRead) -> list[tuple[str, str]]:
    """Kunye satirlari — YALNIZCA dolu alanlar (ekrandaki specRows ile ayni)."""
    pairs: list[tuple[str, str]] = []

    def add(label: str, value: str | None) -> None:
        if value:
            pairs.append((label, value))

    add("Tür", KIND_LABELS.get(fault.fault_kind or "", fault.fault_kind or ""))
    add("Etkilenen faz", "-".join((fault.phase or "").upper()) or None)
    add(
        "Yön",
        DIRECTION_LABELS.get(fault.fault_direction or "", fault.fault_direction or ""),
    )
    if fault.fault_current_a is not None:
        add("Arıza akımı", f"{_tr_number(fault.fault_current_a, 1)} A")
    if fault.load_current_before_a is not None:
        add("Öncesi yük akımı", f"{_tr_number(fault.load_current_before_a, 1)} A")
    if fault.conductor_temp_c is not None:
        add("İletken sıcaklığı", f"{_tr_number(fault.conductor_temp_c)} °C")
    if fault.momentary_fault_count is not None:
        add("Geçici arıza sayacı", str(fault.momentary_fault_count))
    if fault.permanent_fault_count is not None:
        add("Kalıcı arıza sayacı", str(fault.permanent_fault_count))
    if fault.measured_at:
        add("Ölçüm saati", _clock(fault.measured_at))
    return pairs


def _devices_section(st: _Styles, fault: FaultEventRead) -> list:
    if not (fault.last_red_device_name or fault.first_green_device_name):
        return []

    def device_cell(role: str, name: str | None, code: str | None, color: str) -> list:
        return [
            Paragraph(_upper_tr(role), st.label),
            Paragraph(
                _esc(name or "Hat ucu (cihaz yok)"),
                ParagraphStyle("dev", parent=st.value, textColor=colors.HexColor(color)),
            ),
            Paragraph(_esc(code or ""), st.caption),
        ]

    row = Table(
        [
            [
                device_cell(
                    "Son arıza algılayan cihaz",
                    fault.last_red_device_name,
                    fault.last_red_device_code,
                    "#dc2626",
                ),
                Paragraph("→", ParagraphStyle("arrow", parent=st.title, textColor=MUTED)),
                device_cell(
                    "İlk arıza algılamayan cihaz",
                    fault.first_green_device_name,
                    fault.first_green_device_code,
                    "#10b981",
                ),
            ]
        ],
        colWidths=[CONTENT_W * 0.44, CONTENT_W * 0.12, CONTENT_W * 0.44],
    )
    row.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#fef2f2")),
                ("BACKGROUND", (2, 0), (2, 0), colors.HexColor("#ecfdf5")),
                ("BOX", (0, 0), (0, 0), 0.6, colors.HexColor("#fecaca")),
                ("BOX", (2, 0), (2, 0), 0.6, colors.HexColor("#a7f3d0")),
                ("ALIGN", (1, 0), (1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )

    pairs: list[tuple[str, str]] = []
    span = format_distance_range(fault.zone_start_m, fault.zone_end_m)
    if span:
        pairs.append(("Hat başından", span))
    if fault.zone_length_m is not None:
        pairs.append(("Belirsizlik aralığı", format_distance(fault.zone_length_m)))

    out: list = [
        Spacer(1, 12),
        _section_head(
            st,
            "Arıza Tespit Eden Cihazlar",
            "Bu iki cihaz arasındaki bölge sahada aranacak kısımdır",
        ),
        Spacer(1, 6),
        row,
    ]
    if pairs:
        out += [Spacer(1, 5), _kv_grid(st, pairs)]
        out.append(
            Paragraph(
                "Mesafeler kuş uçuşu değil, direk koordinatları üzerinden hat boyunca "
                "hesaplanır; kot farkı ve iletken sarkması dâhil değildir.",
                st.caption,
            )
        )
    return [KeepTogether(out)]


def _alarms_section(st: _Styles, fault: FaultEventRead) -> list:
    alarms = fault.trigger_alarms or []
    if not alarms:
        return []
    rows = []
    for alarm in alarms:
        rows.append(
            [
                Paragraph(_esc(alarm.title), st.cell_bold),
                Paragraph(_esc(alarm.device_name or alarm.device_code or "—"), st.cell),
                Paragraph(
                    _esc(SOURCE_LABELS.get(alarm.signal_source or "", alarm.signal_source or "—")),
                    st.cell,
                ),
                Paragraph(format_report_time(alarm.created_at), st.cell),
                Paragraph("Onaylandı" if alarm.acknowledged else "—", st.cell),
            ]
        )
    widths = [CONTENT_W * w for w in (0.34, 0.22, 0.16, 0.18, 0.10)]
    return _block(
        _section_head(st, "Arızayı Açan Alarmlar", "Cihazın ölçtüğü kanıt"),
        _data_table(st, ["Alarm", "Cihaz", "Kaynak", "Zaman", "Onay"], rows, widths),
    )


def _poles_section(st: _Styles, poles: Sequence[ReportPole]) -> list:
    if not poles:
        return []
    role_text = {"start": "Arıza başlangıcı", "end": "Arıza bitişi", "zone": "Aralıkta"}
    rows = []
    for pole in poles:
        rows.append(
            [
                Paragraph(f"#{pole.sequence_no}", st.cell_bold),
                Paragraph(_esc(pole.name), st.cell),
                Paragraph(f"{pole.latitude:.6f}".replace(".", ","), st.cell_right),
                Paragraph(f"{pole.longitude:.6f}".replace(".", ","), st.cell_right),
                Paragraph(role_text.get(pole.role, ""), st.cell),
            ]
        )
    widths = [CONTENT_W * w for w in (0.10, 0.40, 0.16, 0.16, 0.18)]
    return _block(
        _section_head(st, "Arıza Aralığındaki Direkler", "Saha ekibi için koordinatlar"),
        _data_table(st, ["Sıra", "Direk", "Enlem", "Boylam", "Rol"], rows, widths),
    )


def _branches_section(st: _Styles, fault: FaultEventRead) -> list:
    branches = fault.affected_branches or []
    if not branches:
        return []
    rows = []
    for branch in branches:
        pole = branch.branch_pole_name or (
            f"#{branch.branch_pole_seq}" if branch.branch_pole_seq is not None else "—"
        )
        rows.append(
            [
                Paragraph(_esc(branch.line_name), st.cell_bold),
                Paragraph(f"{_esc(pole)} direğinden ayrılıyor", st.cell),
                Paragraph(
                    "Arıza var" if branch.has_own_fault else "Kontrol edin",
                    ParagraphStyle(
                        "branchState",
                        parent=st.cell_bold,
                        textColor=colors.HexColor("#dc2626" if branch.has_own_fault else "#b45309"),
                    ),
                ),
            ]
        )
    widths = [CONTENT_W * w for w in (0.38, 0.42, 0.20)]
    return _block(
        _section_head(
            st,
            "Etkilenen Branşman Kolları",
            "Arıza bölgesindeki dallanma direklerine bağlı kollar",
        ),
        _data_table(st, ["Kol", "Dallanma", "Durum"], rows, widths),
    )


def _timeline_section(
    st: _Styles, fault: FaultEventRead, recurrence: ReportRecurrence | None
) -> list:
    steps = [
        ("Arıza açıldı", fault.opened_at),
        ("Sorumluya atandı", fault.assigned_at),
        ("Normale döndü", fault.resolved_at),
        ("Kapatıldı", fault.closed_at),
    ]
    rows = [
        [Paragraph(label, st.cell_bold), Paragraph(format_report_time(at), st.cell)]
        for label, at in steps
        if at is not None
    ]
    if not rows:
        return []
    out: list = _block(
        _section_head(st, "Zaman Çizelgesi"),
        _data_table(st, ["Adım", "Zaman"], rows, [CONTENT_W * 0.5, CONTENT_W * 0.5]),
    )
    if recurrence and recurrence.total > 0:
        bits = [
            f"Bu hat son {recurrence.window_days} günde {recurrence.total} kez daha arızalandı."
        ]
        if recurrence.same_section:
            bits.append(
                f"{recurrence.same_section} tanesi AYNI kesimde — kök sebep sürüyor olabilir."
            )
        if recurrence.last_at:
            bits.append(f"En son: {format_report_time(recurrence.last_at)}")
        out += [
            Spacer(1, 6),
            _note_box(st, "BU HATTIN GEÇMİŞİ", " ".join(bits), accent="#f59e0b"),
        ]
    return out


def _resolution_section(st: _Styles, fault: FaultEventRead) -> list:
    cause = _cause_label(fault.cause_code)
    suggested = _cause_label(fault.auto_cause_code) if not cause else None
    blocks: list = []
    if cause:
        blocks.append(_kv_grid(st, [("Tespit edilen sebep", cause)], columns=1))
    elif suggested:
        blocks.append(
            _kv_grid(st, [("Sebep (girilmedi — cihaz verisine göre öneri)", suggested)], columns=1)
        )
    if fault.cause_detail:
        blocks += [Spacer(1, 6), _note_box(st, "ÇÖZÜM AYRINTISI", fault.cause_detail)]
    if fault.note:
        blocks += [Spacer(1, 6), _note_box(st, "ÇALIŞMA NOTU", fault.note, accent="#3b82f6")]
    if fault.resolution_note:
        blocks += [
            Spacer(1, 6),
            _note_box(st, "ÇÖZÜM NOTU (KAPANIŞ)", fault.resolution_note, accent="#10b981"),
        ]
    if not blocks:
        return []
    return _block(_section_head(st, "Sebep ve Çözüm"), *blocks)


def _comments_section(st: _Styles, comments: Sequence[FaultCommentRead]) -> list:
    if not comments:
        return []
    # Her yorum KENDI kutusu: tek tabloda ust uste basildiginda iki ayri
    # kisinin notu tek bir metin blogu gibi okunuyordu.
    blocks: list = []
    for index, comment in enumerate(comments):
        if index:
            blocks.append(Spacer(1, 5))
        blocks.append(
            _note_box(
                st,
                f"{comment.author_username} · {format_report_time(comment.created_at)}",
                comment.body,
                accent="#94a3b8",
            )
        )
    return _block(
        _section_head(st, "Saha Raporu / Yorumlar", f"{len(comments)} kayıt"), *blocks
    )


def _signature_block(st: _Styles, fault: FaultEventRead, generated_by: str) -> list:
    """Imza alani — rapor ciktisi arsive/musteriye imzali gider."""
    assignee = fault.assigned_to_full_name or fault.assigned_to_username or ""

    def box(title: str, name: str) -> list:
        return [
            Paragraph(_upper_tr(title), st.label),
            Spacer(1, 2),
            Paragraph(_esc(name) or "&nbsp;", st.body),
            Spacer(1, 16),
            Paragraph("İmza / Tarih", st.caption),
        ]

    table = Table(
        [[box("Sahada çalışan / atanan", assignee), box("Onaylayan", "")]],
        colWidths=[CONTENT_W / 2, CONTENT_W / 2],
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEABOVE", (0, 0), (-1, -1), 0.6, RULE),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("LEFTPADDING", (1, 0), (1, 0), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return [Spacer(1, 16), KeepTogether([table])]
