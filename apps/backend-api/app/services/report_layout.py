"""Rapor sablonu — PDF ciktilarinin ORTAK ustbilgi/altbilgi duzeni ve
ORTAK GOVDE PARCALARI (bolum basligi, kunye izgarasi, veri tablosu...).

Kural: her sayfada SOLDA EnerjiOne logosu, SAGDA musteri logosu; altbilgide
solda olusturma zamani, sagda "Sayfa X / Y". Toplam sayfa sayisi ancak
belge kurulduktan sonra bilindiginden iki gecisli bir canvas kullanilir
(NumberedCanvas): birinci gecis sayfalari biriktirir, ikincisinde altbilgi
gercek toplamla yazilir.

GOVDE PARCALARI NEDEN BURADA: bu parcalar (turuncu cubuklu bolum basligi,
zebra satirli tablo, sag hizali kunye izgarasi) Ariza Raporu icin yazilmisti
ve orada PRIVATE duruyordu. Ikinci bir belge turu (Cihaz Durum Raporu)
eklenirken iki secenek vardi: 250 satiri kopyalamak ya da buraya tasimak.
Kopya, iki raporun zamanla FARKLI gorunmesi demekti — musteriye giden iki
belgenin ayni kurumdan ciktigi bakisla anlasilmaz olurdu. Ustbilgi zaten
burada ortaklasmisti; govde de ayni yere ait.

TURKCE KARAKTER: reportlab'in gomulu Helvetica'si WinAnsi (cp1252) kodlar
ve `ğ ş ı İ Ğ Ş` karakterlerini ICERMEZ — bu fontla basilan rapor "Guvenlik
duvari acildi" yerine bozuk kutu gosterir. Bu yuzden sistemde bulunan bir
TrueType font (Linux: DejaVu, Windows: Arial/Tahoma) kayit edilir; hicbiri
yoksa Helvetica'ya duser (rapor uretilir, yalnizca birkac karakter bozulur —
export'un tamamen basarisiz olmasindansa iyidir).
"""

from __future__ import annotations

import base64
import binascii
import io
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

# Marka rengi (arayuzdeki turuncu ile ayni).
BRAND_ORANGE = colors.HexColor("#e97800")
INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#64748b")
RULE = colors.HexColor("#e2e8f0")
SOFT = colors.HexColor("#f8fafc")

#: A4 DIKEY govde genisligi (iki yanda 12 mm kenar bosluk). Tablo genislikleri
#: bunun ORANI olarak verilir; sayfa boyutu degisirse tek yerden degisir.
CONTENT_WIDTH = A4[0] - 24 * mm

HEADER_HEIGHT = 20 * mm
FOOTER_HEIGHT = 12 * mm
LOGO_MAX_H = 11 * mm
LOGO_MAX_W = 46 * mm

_OUR_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "enerjione-logo.png"

# Turkce destekli TTF adaylari (once Linux/production, sonra Windows/dev).
_FONT_CANDIDATES: list[tuple[str, str, str]] = [
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "DejaVuSans",
    ),
    (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "LiberationSans",
    ),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf", "ArialUni"),
    (r"C:\Windows\Fonts\tahoma.ttf", r"C:\Windows\Fonts\tahomabd.ttf", "TahomaUni"),
]

_font_cache: tuple[str, str] | None = None


def report_fonts() -> tuple[str, str]:
    """(normal, bold) font adlari. Ilk cagrida kaydeder, sonra onbellekten."""
    global _font_cache
    if _font_cache is not None:
        return _font_cache
    for regular_path, bold_path, name in _FONT_CANDIDATES:
        if not Path(regular_path).exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, regular_path))
            bold_name = name
            if Path(bold_path).exists():
                bold_name = f"{name}-Bold"
                pdfmetrics.registerFont(TTFont(bold_name, bold_path))
            _font_cache = (name, bold_name)
            return _font_cache
        except Exception:
            # Bozuk/okunamayan font — sonraki adaya gec.
            continue
    _font_cache = ("Helvetica", "Helvetica-Bold")
    return _font_cache


def report_font_files() -> tuple[str, str] | None:
    """(normal, bold) TTF DOSYA YOLU. Hicbir aday yoksa None.

    `report_fonts()` reportlab'e kayitli font ADINI dondurur; harita figuru
    (Pillow) ise dosya yolu ister. Aday listesi tek yerde dursun diye ayni
    listeden okunur — iki liste zamanla ayrisir ve rapor ile harita farkli
    fontla cikardi.
    """
    for regular_path, bold_path, _name in _FONT_CANDIDATES:
        if Path(regular_path).exists():
            bold = bold_path if Path(bold_path).exists() else regular_path
            return regular_path, bold
    return None


def decode_data_url_image(data_url: str | None) -> ImageReader | None:
    """`data:image/png;base64,...` -> ImageReader. Bozuksa None (rapor yine cikar).

    SVG desteklenmez (reportlab raster bekler) — musteri SVG yuklediyse
    logo sessizce atlanir, metin basligi zaten var.
    """
    if not data_url or not isinstance(data_url, str):
        return None
    if not data_url.startswith("data:image/"):
        return None
    header, _, payload = data_url.partition(",")
    if not payload or "svg" in header.lower():
        return None
    try:
        raw = base64.b64decode(payload, validate=False)
        return ImageReader(io.BytesIO(raw))
    except (binascii.Error, ValueError, OSError):
        return None


def _draw_logo(
    canvas: pdfcanvas.Canvas,
    image: ImageReader,
    *,
    right_x: float | None = None,
    left_x: float | None = None,
    center_y: float,
) -> None:
    """Logoyu en-boy oranini KORUYARAK ciz (ezilmis logo amatorce durur)."""
    try:
        width, height = image.getSize()
    except Exception:
        return
    if not width or not height:
        return
    scale = min(LOGO_MAX_W / width, LOGO_MAX_H / height)
    draw_w, draw_h = width * scale, height * scale
    x = left_x if left_x is not None else (right_x or 0) - draw_w
    canvas.drawImage(
        image,
        x,
        center_y - draw_h / 2,
        width=draw_w,
        height=draw_h,
        mask="auto",
        preserveAspectRatio=True,
    )


def format_report_time(value: datetime | None, *, with_seconds: bool = False) -> str:
    """Okunabilir yerel tarih: 06.08.2026 14:36 (ISO 'T' formati DEGIL).

    Kayitlar UTC saklanir; rapor sahada okunacagi icin yerel saate cevrilir.
    """
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone()
    pattern = "%d.%m.%Y %H:%M:%S" if with_seconds else "%d.%m.%Y %H:%M"
    return local.strftime(pattern)


class ReportCanvas(pdfcanvas.Canvas):
    """Iki gecisli canvas — altbilgide "Sayfa X / Y" yazabilmek icin.

    Ustbilgi/altbilgi icerigi `configure()` ile sinif duzeyinde verilir;
    SimpleDocTemplate canvasmaker'a parametre gecirmeye izin vermiyor.
    """

    _title_text = ""
    _subtitle_text = ""
    _footer_left = ""
    _our_logo: ImageReader | None = None
    _customer_logo: ImageReader | None = None
    _customer_name = ""

    @classmethod
    def configure(
        cls,
        *,
        title: str,
        subtitle: str,
        footer_left: str,
        customer_logo: ImageReader | None,
        customer_name: str,
    ) -> None:
        cls._title_text = title
        cls._subtitle_text = subtitle
        cls._footer_left = footer_left
        cls._customer_logo = customer_logo
        cls._customer_name = customer_name
        cls._our_logo = None
        if _OUR_LOGO_PATH.exists():
            try:
                cls._our_logo = ImageReader(str(_OUR_LOGO_PATH))
            except Exception:
                cls._our_logo = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_states: list[dict] = []

    def showPage(self) -> None:  # noqa: N802 (reportlab API)
        self._saved_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved_states)
        for state in self._saved_states:
            self.__dict__.update(state)
            self._draw_header()
            self._draw_footer(total)
            super().showPage()
        super().save()

    # ---- cizim ----------------------------------------------------------
    def _draw_header(self) -> None:
        page_w, page_h = self._pagesize
        margin = 12 * mm
        band_y = page_h - margin - HEADER_HEIGHT
        center_y = band_y + HEADER_HEIGHT / 2
        regular, bold = report_fonts()

        if self._our_logo is not None:
            _draw_logo(self, self._our_logo, left_x=margin, center_y=center_y)
        if self._customer_logo is not None:
            _draw_logo(self, self._customer_logo, right_x=page_w - margin, center_y=center_y)
        elif self._customer_name:
            # Logo yoksa musteri ADI sagda metin olarak dursun.
            self.setFont(bold, 10)
            self.setFillColor(INK)
            self.drawRightString(page_w - margin, center_y - 3, self._customer_name)

        # Ortada baslik — iki logonun arasinda kalir.
        self.setFont(bold, 13)
        self.setFillColor(INK)
        self.drawCentredString(page_w / 2, center_y + 1, self._title_text)
        if self._subtitle_text:
            self.setFont(regular, 8)
            self.setFillColor(MUTED)
            self.drawCentredString(page_w / 2, center_y - 9, self._subtitle_text)

        self.setStrokeColor(BRAND_ORANGE)
        self.setLineWidth(1.2)
        self.line(margin, band_y - 2, page_w - margin, band_y - 2)

    def _draw_footer(self, total_pages: int) -> None:
        page_w = self._pagesize[0]
        margin = 12 * mm
        regular, _ = report_fonts()
        y = margin + 4

        self.setStrokeColor(RULE)
        self.setLineWidth(0.5)
        self.line(margin, y + 9, page_w - margin, y + 9)

        self.setFont(regular, 7.5)
        self.setFillColor(MUTED)
        self.drawString(margin, y, self._footer_left)
        self.drawRightString(
            page_w - margin, y, f"Sayfa {self._pageNumber} / {total_pages}"
        )


# ===========================================================================
# ORTAK GOVDE PARCALARI
#
# Asagidakiler Ariza Raporu icin yazildi, Cihaz Durum Raporu eklenince
# ortaklastirildi. Ikisi de AYNI belgeden cikmis gibi gorunmeli; gerekce
# modul docstring'inde.
# ===========================================================================


def esc(text: object) -> str:
    """Paragraph'a giden SERBEST METNI kacisla.

    reportlab Paragraph icerigini XML gibi ayristirir: bir yorumda gecen
    "R&D" ya da "a<b" belge kurulumunu HATA ile dusuruyordu; daha kotusu
    `<yenilendi>` gibi bir ifade BILINMEYEN ETIKET sayilip SESSIZCE
    atiliyor, notun bir kelimesi eksik basiliyordu. Kullanicinin ya da
    cihazin yazdigi her metin buradan gecer.
    """
    return escape(str(text if text is not None else ""))


def upper_tr(text: str) -> str:
    """Turkce buyuk harf. `str.upper()` 'i' -> 'I' verir: basliklar "CIZELGESI",
    "TESPIT", "CIHAZ" diye basiliyordu. Once 'i' -> 'I' esleniyor."""
    return text.replace("i", "İ").upper()


def tr_number(value: float, decimals: int = 0) -> str:
    """1234.5 -> '1.234,5' (tr-TR). Arayuzdeki toLocaleString karsiligi."""
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


class ReportStyles:
    """Tek yerde tanimli tipografi — bolumler ve BELGELER arasi kayma olmasin."""

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
        self.cell_center = ParagraphStyle("cellCenter", parent=self.cell, alignment=TA_CENTER)
        self.cell_muted = ParagraphStyle("cellMuted", parent=self.cell, textColor=MUTED)
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
            "pill", fontName=bold, fontSize=10.5, leading=13, textColor=colors.white,
            alignment=TA_CENTER,
        )


def section_head(
    st: ReportStyles, title: str, hint: str = "", *, width: float = CONTENT_WIDTH
) -> Table:
    """Bolum basligi: solda turuncu dikey cubuk, altta ince kural.

    Baslikta ikon YOK — PDF'te ikon fontu garanti degil ve eksik glif
    kutu olarak basiliyor.
    """
    right = Paragraph(hint, st.caption) if hint else ""
    table = Table(
        [[Paragraph(upper_tr(title), st.section), right]],
        colWidths=[width * 0.62, width * 0.38],
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


def block(head: Table, first, *rest) -> list:
    """Bolum: basligi ILK icerik parcasindan AYIRMA.

    Baslik sayfanin dibinde yalniz kaldiginda okuyucu sonraki sayfadaki
    tablonun neyin tablosu oldugunu bilemiyor, geri donmek zorunda kaliyor.
    Kalan parcalar serbest akar (uzun tablo sayfa bolebilir — basligi
    `repeatRows` tekrar basar).
    """
    return [Spacer(1, 12), KeepTogether([head, Spacer(1, 6), first]), *rest]


def stat_strip(
    st: ReportStyles,
    cells: list[tuple[str, str, str | None]],
    *,
    width: float = CONTENT_WIDTH,
) -> Table:
    """Dortlu olcu seridi — (etiket, deger, deger rengi)."""
    rows: list[list] = []
    for index in range(0, len(cells), 4):
        chunk = list(cells[index : index + 4])
        while len(chunk) < 4:
            chunk.append(("", "", None))
        row = []
        for label, value, color in chunk:
            style = st.value
            if color:
                style = ParagraphStyle(
                    f"v{color}", parent=st.value, textColor=colors.HexColor(color)
                )
            row.append(
                [Paragraph(upper_tr(label), st.label), Paragraph(esc(value) or "—", style)]
                if label
                else ""
            )
        rows.append(row)
    cell_w = width / 4
    table = Table(rows, colWidths=[cell_w] * 4)
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


def kv_grid(
    st: ReportStyles,
    pairs: list[tuple[str, str]],
    columns: int = 2,
    *,
    width: float = CONTENT_WIDTH,
) -> Table:
    """Kunye izgarasi: solda soluk etiket, sagda kalin deger (sag hizali).

    Iki kolon: A4 dikeyde tek kolon sayfayi gereksiz uzatiyor, dort kolon
    degerleri sikistiriyor.
    """
    rows: list[list] = []
    for index in range(0, len(pairs), columns):
        chunk = pairs[index : index + columns]
        row: list = []
        for label, value in chunk:
            row.append(Paragraph(esc(label), st.kv_label))
            row.append(Paragraph(esc(value), st.kv_value))
        while len(row) < columns * 2:
            row.append("")
        rows.append(row)
    unit = width / columns
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


def data_table(
    st: ReportStyles, headers: list[str], rows: list[list], widths: list[float]
) -> Table:
    """Turuncu baslikli, zebra satirli veri tablosu (tum raporlarda ayni dil)."""
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


def note_box(
    st: ReportStyles,
    title: str,
    body: str,
    accent: str = "#e97800",
    *,
    width: float = CONTENT_WIDTH,
) -> Table:
    """Serbest metin kutusu — sol kenarinda renkli cubuk."""
    table = Table(
        [
            [Paragraph(esc(title), st.label)],
            # Kacislama SONRA satir sonu: once <br/> konsa kacislama onu da
            # metne cevirir ve saha notu tek satira yapisirdi.
            [Paragraph(esc(body).replace("\n", "<br/>"), st.body)],
        ],
        colWidths=[width],
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


def status_pill(st: ReportStyles, text: str, color: str) -> Table:
    """Metin kadar genis, yuvarlak kosen durum rozeti.

    `colWidths` verilmezse tablo icinde bulundugu hucrenin TAMAMINA yayilir:
    "Acik" gibi iki heceli bir durum icin sayfanin sag ucunu kaplayan kocaman
    bir blok. Genislik yazinin kendisinden olculur.
    """
    pill_w = pdfmetrics.stringWidth(text, st.bold, 10.5) + 22
    table = Table([[Paragraph(esc(text), st.pill)]], colWidths=[pill_w])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(color)),
                ("ROUNDEDCORNERS", [5, 5, 5, 5]),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    table.hAlign = "RIGHT"
    return table
