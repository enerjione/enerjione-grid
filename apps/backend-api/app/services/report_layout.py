"""Rapor sablonu — PDF ciktilarinin ORTAK ustbilgi/altbilgi duzeni.

Kural: her sayfada SOLDA EnerjiOne logosu, SAGDA musteri logosu; altbilgide
solda olusturma zamani, sagda "Sayfa X / Y". Toplam sayfa sayisi ancak
belge kurulduktan sonra bilindiginden iki gecisli bir canvas kullanilir
(NumberedCanvas): birinci gecis sayfalari biriktirir, ikincisinde altbilgi
gercek toplamla yazilir.

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

from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas

# Marka rengi (arayuzdeki turuncu ile ayni).
BRAND_ORANGE = colors.HexColor("#e97800")
INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#64748b")
RULE = colors.HexColor("#e2e8f0")

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
