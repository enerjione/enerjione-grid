"""E-posta bilesenleri — react-email bilesen sozlugunun Python karsiligi.

NEDEN BU DOSYA VAR: mail HTML'i her sablonda elle yazilinca (padding, renk,
font yiginini her f-string'de tekrarlamak) sablonlar birbirinden kayiyor ve
tek bir gorsel duzeltme uc ayri yerde tekrar edilmek zorunda kaliyordu.
react-email'in yaptigi is ozunde "tablo tabanli primitifler + tek token
seti"; burada ayni sozlugu Node zinciri getirmeden kuruyoruz. Mailler saha
cihazindaki Python worker'inda RUNTIME'da render edildigi icin ikinci bir
dil / build adimi istemiyoruz.

Kullanim (bkz. `email_templates.py`):

    layout(
        subject="[Kritik] ...",
        preheader_text="Kutu onizlemesinde gorunecek tek satir",
        eyebrow="Proje adi",
        title="Yeni Alarm",
        accent=PALETTE["critical"],
        blocks=[heading("..."), text("..."), data_table(rows), button(...)],
        footer_note="...",
    )

Tum primitifler HTML parcasi (`str`) dondurur. Kullanici girdisini
`esc()` ile kacislamak CAGIRANIN sorumlulugudur; primitifler aldigi
degeri raw HTML olarak gomer (bir satirin degeri kasten `<strong>` gibi
isaretleme icerebiliyor).

Uc kural bozulmasin:
  1. Duzen TABLO ile kurulur — Outlook (Word motoru) flex/grid bilmez.
  2. Gorsel stil INLINE yazilir; `<head><style>` yalnizca mobil ve koyu
     tema DUZELTMESI icin var (Gmail web `<style>` destekler ama bazi
     istemciler siler — inline olan her kosulda ayakta kalir).
  3. Renk/olcu degerleri PALETTE ve olcu sabitlerinden gelir, elle
     yazilmaz.
"""

from __future__ import annotations

import html
from typing import Any, Iterable

#: Tek renk kaynagi. Tailwind slate + severity tonlari (mevcut mailin
#: paleti korunuyor, sablonlarin ortak sozlugu haline getiriliyor).
PALETTE: dict[str, str] = {
    "bg": "#f1f5f9",
    "surface": "#ffffff",
    "ink": "#0f172a",
    "body": "#475569",
    "muted": "#64748b",
    "faint": "#94a3b8",
    "line": "#e2e8f0",
    "line_soft": "#eef2f6",
    "panel": "#f8fafc",
    # severity / amac
    "critical": "#dc2626",
    "error": "#b91c1c",
    "warning": "#d97706",
    "info": "#2563eb",
    "assign": "#4338ca",
}

# Kisayollar — f-string icinde PALETTE["x"] yazmak Python 3.11'de ic-ice
# ayni tirnak sorununa dusuyor (PEP 701 3.12'de geldi).
_BG = PALETTE["bg"]
_SURFACE = PALETTE["surface"]
_INK = PALETTE["ink"]
_BODY = PALETTE["body"]
_MUTED = PALETTE["muted"]
_FAINT = PALETTE["faint"]
_LINE = PALETTE["line"]
_LINE_SOFT = PALETTE["line_soft"]
_PANEL = PALETTE["panel"]

FONT_STACK = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
)
MONO_STACK = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"

#: Kart genisligi — 600px e-posta dunyasinin fiili standardi (Outlook
#: okuma bolmesi bunun altinda yatay kaydirma yapmaz).
CARD_WIDTH = 600

#: Govde dolgusu. Mobilde `<style>` icindeki `.e1-pad` bunu kucultur.
PAD_X = 26
PAD_Y = 24

#: `<head>` stili — SADECE inline CSS'in yapamadigi iki is:
#: (1) 480px alti yerlesim, (2) koyu tema. Inline stil selector'dan
#: guclu oldugu icin buradaki kurallar `!important` ile yazilmak
#: ZORUNDA; aksi halde hicbir etkisi olmaz.
_HEAD_STYLE = """
    body { margin:0; padding:0; width:100% !important; -webkit-text-size-adjust:100%; }
    table { border-collapse:collapse; }
    img { border:0; outline:none; text-decoration:none; line-height:100%; -ms-interpolation-mode:bicubic; }
    a { color:inherit; }
    @media only screen and (max-width:480px) {
      .e1-gutter { padding:0 !important; }
      .e1-card { border-radius:0 !important; }
      .e1-pad { padding-left:18px !important; padding-right:18px !important; }
      .e1-title { font-size:18px !important; }
      .e1-metric-value { font-size:26px !important; }
      /* Etiket/deger sutunlari mobilde alt alta gecer: 132px'lik etiket
         sutunu 320px ekranda degeri iki-uc satira kiriyordu. */
      .e1-cell-label { display:block !important; width:auto !important;
                       padding:12px 16px 2px !important; border-bottom:0 !important; }
      .e1-cell-value { display:block !important; width:auto !important;
                       padding:0 16px 12px !important; }
      /* Buton mobilde tam genislik. `a` uzerine width:100% VERILMEZ:
         content-box + 28px yatay padding ile kutu %100'u asar ve buton
         karttan tasar. Blok seviyesindeki `a` kapsayicisini zaten
         doldurur. */
      .e1-btn, .e1-btn td { width:100% !important; }
      .e1-btn a { display:block !important; text-align:center !important; }
    }
    @media (prefers-color-scheme: dark) {
      .e1-bg { background:#0b1220 !important; }
      .e1-card { background:#131c2e !important; }
      .e1-ink, .e1-ink a { color:#e8edf5 !important; }
      .e1-body, .e1-body a { color:#b8c4d6 !important; }
      .e1-muted { color:#93a2b8 !important; }
      .e1-panel { background:#0f1829 !important; border-color:#28344a !important; }
      .e1-divider { border-color:#28344a !important; }
      .e1-foot { background:#0f1829 !important; border-color:#28344a !important; }
    }
"""

_UPPER_LABEL = (
    "font-size:11px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase"
)


# --------- kacis / yardimci --------------------------------------------

def esc(value: Any) -> str:
    """Kullanici girdisini HTML-safe yap. None -> em-dash."""
    if value is None:
        return "—"
    return html.escape(str(value), quote=True)


# --------- metin primitifleri -----------------------------------------

def preheader(content: str) -> str:
    """Gelen kutusu onizleme satiri.

    Bu blok olmadan istemci onizlemeye govdenin ilk metnini koyar — yani
    mailin altbilgisindeki "Bu e-posta ... gonderildi" cumlesi ya da ham
    baslik. Ilk satirin ne yazacagini burada BIZ soyluyoruz. Sondaki
    zero-width karakter zinciri, kisa onizlemenin arkasina govde
    metninin sizmasini engeller (klasik e-posta hilesi).
    """
    filler = "&#847;&zwnj;&nbsp;" * 32
    return (
        '<div style="display:none;font-size:1px;line-height:1px;max-height:0;'
        'max-width:0;opacity:0;overflow:hidden;mso-hide:all;">'
        f"{esc(content)}{filler}</div>"
    )


def heading(content: str, *, size: int = 18, top: int = 0, bottom: int = 10) -> str:
    """Kart icindeki ana baslik. `content` RAW HTML."""
    return (
        f'<h2 class="e1-ink" style="margin:{top}px 0 {bottom}px;font-size:{size}px;'
        f"font-weight:700;font-family:{FONT_STACK};color:{_INK};line-height:1.35;"
        f'">{content}</h2>'
    )


def text(
    content: str,
    *,
    top: int = 0,
    bottom: int = 18,
    size: int = 14,
    strong_ink: bool = False,
) -> str:
    """Paragraf. `strong_ink` koyu ana metin (hitap satiri) icin."""
    cls = "e1-ink" if strong_ink else "e1-body"
    color = _INK if strong_ink else _BODY
    return (
        f'<p class="{cls}" style="margin:{top}px 0 {bottom}px;font-size:{size}px;'
        f'font-family:{FONT_STACK};color:{color};line-height:1.6;">{content}</p>'
    )


def label(content: str, *, top: int = 26, bottom: int = 10) -> str:
    """Blok ustu kucuk buyuk-harf baslik ("KONUM" gibi)."""
    return (
        f'<div class="e1-muted" style="margin:{top}px 0 {bottom}px;{_UPPER_LABEL};'
        f'letter-spacing:0.08em;font-family:{FONT_STACK};color:{_MUTED};">'
        f"{esc(content)}</div>"
    )


def badge(content: str, color: str, *, on_dark: bool = False) -> str:
    """Kucuk rozet. `on_dark` renkli baslik seridi uzerinde kullanilir —
    orada dolgu rengi degil yariseffaf beyaz zemin isini goruyor."""
    if on_dark:
        return (
            '<span style="display:inline-block;padding:4px 11px;border-radius:999px;'
            "background:rgba(255,255,255,0.18);border:1px solid rgba(255,255,255,0.35);"
            f"color:#ffffff;font-family:{FONT_STACK};{_UPPER_LABEL};"
            f'white-space:nowrap;">{esc(content)}</span>'
        )
    return (
        '<span style="display:inline-block;padding:3px 11px;border-radius:999px;'
        f"background:{color};color:#ffffff;font-family:{FONT_STACK};font-size:11px;"
        f'font-weight:700;letter-spacing:0.05em;white-space:nowrap;">'
        f"{esc(content)}</span>"
    )


# --------- yerlesim primitifleri --------------------------------------

def data_table(rows: Iterable[tuple[str, str]], *, label_width: int = 132) -> str:
    """Etiket/deger tablosu. `rows` degerleri RAW HTML olarak gomulur.

    Son satirin alt cizgisi kaldirilir — panel kenarligiyla ust uste
    binen iki cizgi kalin bir kirik hat gibi gorunuyordu.
    """
    items = [r for r in rows if r]
    if not items:
        return ""
    cells: list[str] = []
    for index, (row_label, row_value) in enumerate(items):
        border = (
            ""
            if index == len(items) - 1
            else f"border-bottom:1px solid {_LINE_SOFT};"
        )
        cells.append(
            "<tr>"
            f'<td class="e1-cell-label e1-muted e1-divider" style="padding:11px 16px;'
            f"width:{label_width}px;vertical-align:top;{_UPPER_LABEL};"
            f'font-family:{FONT_STACK};color:{_MUTED};{border}">{esc(row_label)}</td>'
            f'<td class="e1-cell-value e1-ink e1-divider" style="padding:11px 16px;'
            f"vertical-align:top;font-size:14px;line-height:1.5;"
            f'font-family:{FONT_STACK};color:{_INK};{border}">{row_value}</td>'
            "</tr>"
        )
    body = "".join(cells)
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="100%" class="e1-panel" style="width:100%;background:{_PANEL};'
        f"border:1px solid {_LINE};border-radius:12px;border-collapse:separate;"
        f'border-spacing:0;">{body}</table>'
    )


def metric(
    *,
    value: str,
    caption: str | None = None,
    comparison: str | None = None,
    accent: str,
) -> str:
    """Olcum vurgu blogu — alarmin SAYISI mailin en gorunur ogesi olmali.

    Onceden deger etiket/deger tablosunun icinde bir satirdi; telefonda
    ilk bakista "esik neydi, deger neydi" okunmuyordu.
    """
    caption_html = ""
    if caption:
        caption_html = (
            f'<div class="e1-muted" style="margin:0 0 6px;{_UPPER_LABEL};'
            f'letter-spacing:0.08em;color:{_MUTED};">{esc(caption)}</div>'
        )
    comparison_html = ""
    if comparison:
        comparison_html = (
            f'<div class="e1-muted" style="margin:7px 0 0;font-size:13px;'
            f'color:{_MUTED};font-family:{MONO_STACK};">{comparison}</div>'
        )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="100%" style="width:100%;margin:0 0 18px;font-family:{FONT_STACK};">'
        f'<tr><td class="e1-panel" style="padding:16px 18px;background:{_PANEL};'
        f"border:1px solid {_LINE};border-left:4px solid {accent};"
        f'border-radius:12px;">'
        f"{caption_html}"
        f'<div class="e1-metric-value e1-ink" style="font-size:30px;font-weight:700;'
        f'line-height:1.15;color:{_INK};letter-spacing:-0.01em;">{value}</div>'
        f"{comparison_html}"
        "</td></tr></table>"
    )


def button(href: str, content: str, *, color: str, top: int = 24) -> str:
    """CTA butonu.

    Dolgu `<a>` yerine `<td bgcolor>` uzerinde: Outlook masaustu `<a>`
    padding'ini yok sayar ve buton "duz link" gibi cikar.
    """
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'class="e1-btn" style="margin:{top}px 0 0;">'
        f'<tr><td align="center" bgcolor="{color}" style="border-radius:10px;">'
        f'<a href="{esc(href)}" target="_blank" style="display:inline-block;'
        f"padding:13px 28px;font-family:{FONT_STACK};font-size:14px;font-weight:600;"
        f'color:#ffffff;text-decoration:none;border-radius:10px;">{esc(content)}</a>'
        "</td></tr></table>"
    )


def note_pill(content: str, *, top: int = 24) -> str:
    """Link OLMAYAN bilgilendirme seridi (atama mailindeki gibi).

    Buton gibi gorunup tiklanmayan bir oge kullaniciyi yaniltiyordu;
    bu yuzden kasten soluk zemin + kesikli kenarlik.
    """
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="100%" style="width:100%;margin:{top}px 0 0;">'
        f'<tr><td class="e1-panel" style="padding:13px 16px;background:{_PANEL};'
        f"border:1px dashed {_LINE};border-radius:10px;font-family:{FONT_STACK};"
        f'font-size:13px;color:{_MUTED};line-height:1.55;">{content}</td>'
        "</tr></table>"
    )


def image_card(
    src: str, alt: str, *, href: str | None = None, caption: str | None = None
) -> str:
    """Kenarlikli gorsel (harita). `href` verilirse gorsel tiklanabilir.

    `img` uzerindeki font/renk ozellikleri ALT METNI bicimlendirir:
    Outlook ve Gmail gorselleri VARSAYILAN OLARAK engeller, o durumda
    kutuda kirik-gorsel ikonu + bicimsiz sistem yazisi kaliyordu. Bu
    haliyle "Arıza konumu" soluk bir aciklama gibi okunur.
    """
    img = (
        f'<img src="{esc(src)}" alt="{esc(alt)}" width="{CARD_WIDTH}" '
        "style=\"display:block;width:100%;max-width:100%;height:auto;border:0;"
        f'font-family:{FONT_STACK};font-size:12px;color:{_MUTED};">'
    )
    inner = img
    if href:
        inner = (
            f'<a href="{esc(href)}" target="_blank" '
            f'style="display:block;text-decoration:none;">{img}</a>'
        )
    caption_html = ""
    if caption:
        caption_html = (
            f'<div class="e1-muted" style="margin:8px 0 0;font-size:12px;'
            f'color:{_MUTED};font-family:{MONO_STACK};">{esc(caption)}</div>'
        )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'width="100%" style="width:100%;margin:0;">'
        f'<tr><td class="e1-divider" style="border:1px solid {_LINE};'
        f'border-radius:12px;overflow:hidden;line-height:0;">{inner}</td></tr>'
        f'<tr><td style="padding:0;">{caption_html}</td></tr></table>'
    )


def divider(*, top: int = 22, bottom: int = 22) -> str:
    return (
        f'<div class="e1-divider" style="margin:{top}px 0 {bottom}px;font-size:0;'
        f'line-height:0;border-top:1px solid {_LINE};">&nbsp;</div>'
    )


# --------- belge iskeleti ---------------------------------------------

def layout(
    *,
    subject: str,
    preheader_text: str,
    eyebrow: str,
    title: str,
    accent: str,
    blocks: Iterable[str],
    footer_note: str,
    icon: str | None = None,
    header_badge: str | None = None,
) -> str:
    """Tam HTML dokumani.

    `blocks` sirayla kartin govdesine dizilir (bos parcalar atlanir).
    `header_badge` renkli serit uzerinde sag ustte durur.
    """
    body_html = "".join(b for b in blocks if b)
    icon_html = f"{icon} " if icon else ""
    badge_cell = ""
    if header_badge:
        badge_cell = (
            '<td align="right" style="vertical-align:top;padding-left:10px;">'
            f"{header_badge}</td>"
        )

    return f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html lang="tr" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{esc(subject)}</title>
<style>{_HEAD_STYLE}</style>
<!--[if mso]>
<style>body,table,td,a,h2,p,div {{ font-family:'Segoe UI',Arial,sans-serif !important; }}</style>
<![endif]-->
</head>
<body class="e1-bg" style="margin:0;padding:0;background:{_BG};font-family:{FONT_STACK};color:{_INK};">
{preheader(preheader_text)}
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
       class="e1-bg" style="width:100%;background:{_BG};">
  <tr>
    <td class="e1-gutter" align="center" style="padding:28px 12px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="{CARD_WIDTH}"
             class="e1-card"
             style="width:100%;max-width:{CARD_WIDTH}px;background:{_SURFACE};
                    border-radius:16px;overflow:hidden;
                    box-shadow:0 6px 20px rgba(15,23,42,0.08);">
        <tr>
          <td class="e1-pad" style="background:{accent};padding:20px {PAD_X}px;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                   style="width:100%;">
              <tr>
                <td style="vertical-align:top;">
                  <div style="font-family:{FONT_STACK};font-size:11px;letter-spacing:0.1em;
                              text-transform:uppercase;font-weight:700;color:#ffffff;
                              opacity:0.82;">{esc(eyebrow)}</div>
                </td>
                {badge_cell}
              </tr>
            </table>
            <div class="e1-title" style="font-family:{FONT_STACK};font-size:21px;font-weight:700;
                        color:#ffffff;margin-top:6px;line-height:1.3;">
              {icon_html}{esc(title)}
            </div>
          </td>
        </tr>
        <tr>
          <td class="e1-pad" style="padding:{PAD_Y}px {PAD_X}px;">
            {body_html}
          </td>
        </tr>
        <tr>
          <td class="e1-pad e1-foot e1-muted"
              style="padding:16px {PAD_X}px;background:{_PANEL};
                     border-top:1px solid {_LINE};font-family:{FONT_STACK};
                     font-size:11px;line-height:1.6;color:{_FAINT};">
            {esc(footer_note)}
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""
