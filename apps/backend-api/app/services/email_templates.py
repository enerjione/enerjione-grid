"""HTML e-posta sablonlari (alarm + atama + hat arizasi).

Bu dosya artik HAM HTML yazmaz: duzen `email_components` icindeki
primitiflerden (layout/heading/data_table/metric/button/image_card)
kurulur. Boylece tipografi, renk ve dolgu tek yerden yonetilir — bir
gorsel duzeltme uc sablonda tekrar edilmez. Bkz. o dosyanin bas
kismindaki gerekce ve kurallar.

Uc ana fonksiyon:

  render_alarm_email(...)       -> alarm bildirimi (cihaz, kaynak, hat,
                                   bolge, olcum/esik, severity; istege
                                   bagli inline harita `map_cid`)
  render_assignment_email(...)  -> alarm/ariza atama bildirimi
  render_fault_email(...)       -> hat arizasi (hat, bolge, direk araligi,
                                   koordinat + gomulu OSM statik gorseli +
                                   yol-tarifi linki)

Hepsi `(subject, html_body)` doner. Duz metin (plain-text) govdesi
cagiranin sorumlulugunda — bkz. `notification_dispatch_service`.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.services.email_components import (
    PALETTE,
    badge,
    button,
    data_table,
    esc,
    heading,
    image_card,
    label,
    layout,
    metric,
    note_pill,
    text,
)

#: Urun adi — bildirim/e-posta yuzeylerinde markanin TEK kaynagi.
#:
#: Kurulumun kendi adi (Proje Ayarlari > site_title / project_name /
#: customer_name) doluysa HER ZAMAN o kullanilir; bu sabit yalnizca
#: hicbiri girilmemisken devreye giren yedektir. Once "Horstman Smart
#: Logger" yaziyordu: Horstmann izlenen CIHAZIN ureticisi, bu yazilimin
#: adi degil — musteri "Horstmann'dan mail geldi" saniyordu.
PRODUCT_NAME = "EnerjiOne Grid"


# --------- yardimci (format, etiket) -----------------------------------

def _fmt_number(value: Any) -> str:
    if value is None:
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return esc(value)
    if f.is_integer():
        return f"{int(f)}"
    # En fazla 4 basamak; trailing zero kirp
    s = f"{f:.4f}".rstrip("0").rstrip(".")
    return s or "0"


def _level_label_tr(level: str | None) -> str:
    if not level:
        return "Bilgi"
    k = level.strip().lower()
    if k in ("critical", "high"):
        return "Kritik"
    if k in ("warning", "warn", "medium"):
        return "Uyarı"
    if k in ("info", "low"):
        return "Bilgi"
    if k == "error":
        return "Hata"
    return level.capitalize()


def _level_color(level: str | None) -> str:
    """Severity'e gore vurgu rengi (background)."""
    k = (level or "").strip().lower()
    if k in ("critical", "high"):
        return PALETTE["critical"]
    if k in ("warning", "warn", "medium"):
        return PALETTE["warning"]
    if k == "error":
        return PALETTE["error"]
    return PALETTE["info"]


#: `sat07` gibi uydu kaynaklarini yakalar (SN2'de 2, Pole Master Kit'te 9 uydu).
_SAT_SOURCE_RE = re.compile(r"^sat(\d{1,2})$")


def _source_label(src: str | None) -> str | None:
    """`sat07` -> 'Satellite 07'.

    Uydu numarasi DESENDEN cozulur, sabit sozlukten degil: Pole Master Kit
    dokuz uydu tasiyor ve elle yazilmis bir sozluk yedisini yari-cevrilmis
    birakirdi.
    """
    if not src:
        return None
    k = src.strip().lower()
    if k == "master":
        return "Master"
    eslesme = _SAT_SOURCE_RE.match(k)
    if eslesme:
        return f"Satellite {int(eslesme.group(1)):02d}"
    return src.capitalize()


def _operator_symbol(op: str | None) -> str:
    if not op:
        return ""
    k = op.strip().lower()
    return {
        "gt": ">", "ge": "≥", "gte": "≥",
        "lt": "<", "le": "≤", "lte": "≤",
        "eq": "=", "ne": "≠",
    }.get(k, op)


def _format_timestamp(ts: datetime | str | None) -> str:
    """E-postadaki saat YEREL saattir (bkz. services/local_time.py).

    Ham UTC basiliyordu: 11:00'de olusan alarm postada 08:00 gorunuyordu.
    """
    from app.services.local_time import fmt_local

    if ts is None:
        return ""
    if isinstance(ts, datetime):
        return fmt_local(ts, "%d.%m.%Y %H:%M:%S")
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return esc(ts)
    return fmt_local(parsed, "%d.%m.%Y %H:%M:%S")


def _preheader(*parts: str | None) -> str:
    """Gelen kutusu onizleme satiri — dolu parcalari birlestirir."""
    return " • ".join(p for p in parts if p)


# --------- Alarm email -------------------------------------------------

def render_alarm_email(
    *,
    project_title: str | None,
    rule_name: str,
    description: str | None,
    level: str | None,
    device_name: str | None,
    device_code: str | None,
    signal_source: str | None,
    line_name: str | None,
    region_name: str | None,
    value: float | None,
    value_string: str | None,
    threshold: float | None,
    operator: str | None,
    occurred_at: datetime | str | None,
    alarm_link: str | None = None,
    map_cid: str | None = None,
) -> tuple[str, str]:
    """Alarm bildirimi HTML maili — (subject, html_body) doner.

    `map_cid` verilirse govdeye `cid:` referansli inline harita gorseli
    eklenir (ek olarak iliskilendirmek cagiranin isi). Onceden cagiran bu
    gorseli `</body>` etiketini string ile degistirerek ekliyordu; harita
    kartin ve altbilginin DISINDA, sayfanin en dibinde kaliyordu.
    """
    lvl_label = _level_label_tr(level)
    lvl_color = _level_color(level)
    device_label = device_name or device_code or "—"
    src_label = _source_label(signal_source)
    op_sym = _operator_symbol(operator)
    val_display = value_string if value_string else (
        _fmt_number(value) if value is not None else ""
    )
    thr_display = _fmt_number(threshold) if threshold is not None else ""
    occurred_str = _format_timestamp(occurred_at)

    # Olcum ARTIK tablo satiri degil, vurgu blogu: sahadaki ekip once
    # "kac oldu, esik neydi" bilgisine bakiyor.
    metric_block = ""
    if val_display:
        comparison = None
        if op_sym and thr_display:
            comparison = f"Eşik {esc(op_sym)} {esc(thr_display)}"
        metric_block = metric(
            value=esc(val_display),
            caption="Ölçüm",
            comparison=comparison,
            accent=lvl_color,
        )

    rows: list[tuple[str, str]] = [("Cihaz", esc(device_label))]
    if src_label:
        rows.append(("Kaynak", esc(src_label)))
    if line_name:
        rows.append(("Hat", esc(line_name)))
    if region_name:
        rows.append(("Bölge", esc(region_name)))
    if occurred_str:
        rows.append(("Zaman", esc(occurred_str)))

    map_block = ""
    if map_cid:
        map_block = label("Konum") + image_card(
            f"cid:{map_cid}", "Arıza haritası"
        )

    cta_block = ""
    if alarm_link:
        cta_block = button(alarm_link, "Alarmı Görüntüle", color=lvl_color)

    subject = f"[{lvl_label}] {rule_name} — {device_label}"
    html_body = layout(
        subject=subject,
        preheader_text=_preheader(
            device_label,
            f"{val_display} {op_sym} {thr_display}".strip() if val_display else None,
            occurred_str or None,
        ),
        eyebrow=project_title or PRODUCT_NAME,
        title="Yeni Alarm",
        icon="⚠",
        accent=lvl_color,
        header_badge=badge(lvl_label, lvl_color, on_dark=True),
        blocks=[
            heading(esc(rule_name)),
            text(esc(description)) if description else "",
            metric_block,
            data_table(rows),
            map_block,
            cta_block,
        ],
        footer_note=(
            "Bu e-posta, alarm kuralının \"E-posta gönder\" seçeneği açık "
            "olduğu için gönderildi. Bildirimleri yönetmek için sistem "
            "yöneticinizle iletişime geçin."
        ),
    )
    return subject, html_body


# --------- Atama email ------------------------------------------------

def render_assignment_email(
    *,
    project_title: str | None,
    kind: str,  # "alarm" | "fault"
    recipient_full_name: str,
    title: str,
    description: str | None,
    level: str | None,
    actor_username: str | None,
    link_path: str | None = None,
) -> tuple[str, str]:
    """Alarm/Fault atama bildirimi HTML maili.

    Atanan kisinin sistemde gorebilmesi icin baslik + aciklama + atayan
    kullanici adi. Severity baslik seridindeki rozette gosterilir.
    `link_path` doluysa tiklanabilir BUTON degil bilgi seridi basilir —
    mail icinden dogrudan acilabilir bir adres uretilmiyor."""
    lvl_label = _level_label_tr(level)
    lvl_color = _level_color(level)
    is_fault = kind == "fault"
    eyebrow_text = "Yeni Arıza Ataması" if is_fault else "Yeni Alarm Ataması"
    kind_word = "arıza" if is_fault else "alarm"

    # Sadece "Atayan": tur baslik seridinde ("Yeni Arıza Ataması"),
    # seviye ise rozette yaziyor — tabloda tekrarlamak bilgi katmiyor.
    rows: list[tuple[str, str]] = [
        ("Atayan", esc(actor_username) if actor_username else "—"),
    ]

    subject = f"[{eyebrow_text}] {title}"
    html_body = layout(
        subject=subject,
        preheader_text=_preheader(
            title, f"Atayan: {actor_username}" if actor_username else None, lvl_label
        ),
        eyebrow=project_title or PRODUCT_NAME,
        title=eyebrow_text,
        icon="🛠" if is_fault else "🔔",
        accent=PALETTE["assign"],
        header_badge=badge(lvl_label, lvl_color, on_dark=True) if level else None,
        blocks=[
            text(
                f"Merhaba <strong>{esc(recipient_full_name)}</strong>,",
                bottom=10,
                size=15,
                strong_ink=True,
            ),
            text(f"Sistemde size yeni bir {kind_word} atandı. Detaylar aşağıdadır."),
            heading(esc(title)),
            text(esc(description)) if description else "",
            data_table(rows),
            note_pill(
                f"Kaydın tüm ayrıntısını EnerjiOne Grid arayüzünden "
                f"{kind_word} listesi üzerinden görüntüleyebilirsiniz."
            )
            if link_path
            else "",
        ],
        footer_note=(
            "Bu e-posta size yapılan atama nedeniyle gönderildi. Bildirimleri "
            "kapatmak için kullanıcı tercihlerinizden e-posta seçeneğini "
            "kapatabilirsiniz."
        ),
    )
    return subject, html_body


# --------- Fault email (hat arizasi) ----------------------------------

def _google_maps_static_url(
    latitude: float, longitude: float, *, zoom: int = 15, size: str = "640x320"
) -> str:
    """API key'siz, public statik harita gorseli URL'i.

    Not: Google'in `staticmap` endpoint'i 2018'den beri API anahtarini
    zorunlu tutuyor. Bu sebeple e-posta'da OpenStreetMap
    (staticmap.openstreetmap.de) kullaniyoruz; URL formati ayni mantik.
    """
    return (
        f"https://staticmap.openstreetmap.de/staticmap.php?"
        f"center={latitude},{longitude}&zoom={zoom}&size={size}"
        f"&maptype=mapnik&markers={latitude},{longitude},red-pushpin"
    )


def _google_maps_directions_url(latitude: float, longitude: float) -> str:
    """Google Maps yol tarifi linki (kullanici telefon/web'de aciksa direkt
    yon bulur). API key gerektirmez."""
    return f"https://www.google.com/maps/dir/?api=1&destination={latitude},{longitude}"


def render_fault_email(
    *,
    project_title: str | None,
    line_name: str,
    line_code: str | None,
    region_name: str | None,
    last_red_device_name: str | None,
    last_red_device_code: str | None,
    first_green_device_name: str | None,
    first_green_device_code: str | None,
    from_pole_seq: int | None,
    to_pole_seq: int | None,
    latitude: float | None,
    longitude: float | None,
    opened_at: datetime | str | None,
    fault_link: str | None = None,
    assigned_to: str | None = None,
) -> tuple[str, str]:
    """Hat arizasi HTML maili. Konum verilmise harita gorseli + yol-tarifi
    linki dahil edilir."""
    accent = PALETTE["critical"]
    line_title = esc(line_name)
    if line_code:
        line_title += f" <span style=\"font-weight:500;color:{PALETTE['muted']}\">({esc(line_code)})</span>"

    last_red = last_red_device_name or last_red_device_code or "—"
    first_green = first_green_device_name or first_green_device_code or "—"

    pole_range = ""
    if from_pole_seq is not None and to_pole_seq is not None:
        pole_range = f"#{from_pole_seq} ↔ #{to_pole_seq}"

    opened_str = _format_timestamp(opened_at)

    # Harita bloku (lat/long varsa). Koordinat bozuksa harita atlanir ama
    # mailin kalani gonderilir.
    map_block = ""
    coord_label = ""
    if latitude is not None and longitude is not None:
        try:
            lat_f = float(latitude)
            lon_f = float(longitude)
        except (TypeError, ValueError):
            pass
        else:
            directions_url = _google_maps_directions_url(lat_f, lon_f)
            coord_label = f"{lat_f:.6f}, {lon_f:.6f}"
            map_block = (
                label("Konum")
                + image_card(
                    _google_maps_static_url(lat_f, lon_f),
                    "Arıza konumu",
                    href=directions_url,
                    caption=coord_label,
                )
                + button(
                    directions_url,
                    "🗺  Yol Tarifi Al",
                    color=PALETTE["info"],
                    top=16,
                )
            )

    # Direk araligi arizanin en operasyonel bilgisi ("nereye gidecegim") —
    # tablo satiri yerine vurgu blogunda, alarm mailindeki olcum blogu
    # gibi. Baslik seridindeki rozetle birlikte tutuldugunda ayni deger
    # iki kez yaziliyordu.
    pole_block = ""
    if pole_range:
        pole_block = metric(
            value=esc(pole_range),
            caption="Direk Aralığı",
            comparison=None,
            accent=accent,
        )

    rows: list[tuple[str, str]] = [("Bölge", esc(region_name) if region_name else "—")]
    rows.append(("Son Aktif Cihaz", esc(last_red)))
    rows.append(("İlk Sağlam Cihaz", esc(first_green)))
    if assigned_to:
        rows.append(("Görevli", esc(assigned_to)))
    if opened_str:
        rows.append(("Başlangıç", esc(opened_str)))

    cta_block = ""
    if fault_link:
        cta_block = button(fault_link, "Arıza Detayını Aç", color=accent)

    subject = f"[Hat Arızası] {line_name} — {region_name or 'Bölge'}"
    html_body = layout(
        subject=subject,
        preheader_text=_preheader(
            line_name, region_name, pole_range or None, opened_str or None
        ),
        eyebrow=project_title or PRODUCT_NAME,
        title="Hat Arızası",
        icon="⚡",
        accent=accent,
        blocks=[
            heading(line_title),
            text(
                "Hat üzerinde arıza tespit edildi. Aşağıdaki konum bilgisi ile "
                "sahaya yönlenebilir, harita üstünden yol tarifi alabilirsiniz."
            ),
            pole_block,
            data_table(rows, label_width=150),
            map_block,
            cta_block,
        ],
        footer_note=(
            "Bu e-posta hat arızası bildirimi olarak otomatik oluşturuldu. Yol "
            "tarifi butonu telefonunuzdaki harita uygulamasını açacaktır."
        ),
    )
    return subject, html_body
