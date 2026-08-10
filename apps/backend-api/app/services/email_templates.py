"""HTML email sablonu uretimi (alarm + hat arizasi).

Tek dosya — basit string templating. Jinja2 zorunlulugu eklemeden, sade ve
guvenli (hicbir kullanici girdisi raw HTML icine `escape` yapilmadan
girmez). Sablonlar koyu/acik temayi tetikleyen e-posta cliene gore otomatik
calismaz; tablodaki sabit renkler kullanilir.

Iki ana fonksiyon:

  render_alarm_email(...)  -> alarm bildirimi (cihaz, kaynak, hat, bolge,
                              deger, esik, severity)
  render_fault_email(...)  -> hat arizasi (hat adi, bolge, koordinatlar +
                              gomulu Google Maps statik gorseli +
                              yol-tarifi linki)

Tum HTML inline CSS ile yazilir — modern e-posta clientlari (Gmail,
Outlook 365) <head><style> ile cogu kez calisir ama Outlook desktop
inline CSS bekler.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

#: Urun adi — bildirim/e-posta yuzeylerinde markanin TEK kaynagi.
#:
#: Kurulumun kendi adi (Proje Ayarlari > site_title / project_name /
#: customer_name) doluysa HER ZAMAN o kullanilir; bu sabit yalnizca
#: hicbiri girilmemisken devreye giren yedektir. Once "Horstman Smart
#: Logger" yaziyordu: Horstmann izlenen CIHAZIN ureticisi, bu yazilimin
#: adi degil — musteri "Horstmann'dan mail geldi" saniyordu.
PRODUCT_NAME = "EnerjiOne Grid"


# --------- yardimci (escape, format) -----------------------------------

def _esc(value: Any) -> str:
    """Kullanici girdisini HTML-safe yap. None -> '—'."""
    if value is None:
        return "—"
    return html.escape(str(value), quote=True)


def _fmt_number(value: Any) -> str:
    if value is None:
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return _esc(value)
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
        return "#dc2626"
    if k in ("warning", "warn", "medium"):
        return "#d97706"
    if k == "error":
        return "#b91c1c"
    return "#2563eb"


def _source_label(src: str | None) -> str | None:
    if not src:
        return None
    k = src.strip().lower()
    if k == "master":
        return "Master"
    if k == "sat01":
        return "Satellite 01"
    if k == "sat02":
        return "Satellite 02"
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
    if ts is None:
        return ""
    if isinstance(ts, datetime):
        return ts.strftime("%d.%m.%Y %H:%M:%S")
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).strftime("%d.%m.%Y %H:%M:%S")
    except Exception:  # noqa: BLE001
        return _esc(ts)


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
) -> tuple[str, str]:
    """Alarm bildirimi HTML maili — (subject, html_body) doner."""
    lvl_label = _level_label_tr(level)
    lvl_color = _level_color(level)
    title_safe = _esc(project_title or PRODUCT_NAME)
    rule_safe = _esc(rule_name)
    desc_safe = _esc(description) if description else ""
    device_label = device_name or device_code or "—"
    src_label = _source_label(signal_source)
    op_sym = _operator_symbol(operator)
    val_display = value_string if value_string else (_fmt_number(value) if value is not None else "")
    thr_display = _fmt_number(threshold) if threshold is not None else ""

    # Tablo satirlari — sadece dolu olanlari ekle
    rows: list[tuple[str, str]] = []
    rows.append(("Cihaz", _esc(device_label)))
    if src_label:
        rows.append(("Kaynak", _esc(src_label)))
    if line_name:
        rows.append(("Hat", _esc(line_name)))
    if region_name:
        rows.append(("Bölge", _esc(region_name)))
    if val_display:
        if op_sym and thr_display:
            rows.append((
                "Ölçüm",
                f'<strong>{_esc(val_display)}</strong>'
                f' <span style="color:#64748b">{_esc(op_sym)}</span> '
                f'<span style="color:#64748b">{_esc(thr_display)} (eşik)</span>',
            ))
        else:
            rows.append(("Ölçüm", f'<strong>{_esc(val_display)}</strong>'))
    occurred_str = _format_timestamp(occurred_at)
    if occurred_str:
        rows.append(("Zaman", _esc(occurred_str)))

    rows_html = "".join(
        f'<tr>'
        f'<td style="padding:10px 14px;color:#64748b;font-size:12px;'
        f'text-transform:uppercase;letter-spacing:0.04em;font-weight:700;'
        f'width:120px;border-bottom:1px solid #f1f5f9;">{_esc(label)}</td>'
        f'<td style="padding:10px 14px;color:#0f172a;font-size:14px;'
        f'border-bottom:1px solid #f1f5f9;">{value}</td>'
        f'</tr>'
        for label, value in rows
    )

    desc_block = (
        f'<p style="margin:0 0 16px;color:#475569;font-size:14px;line-height:1.5;">'
        f'{desc_safe}</p>'
    ) if desc_safe else ""

    cta_block = ""
    if alarm_link:
        cta_block = (
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:20px 0 0;"><tr><td>'
            f'<a href="{_esc(alarm_link)}" '
            f'style="display:inline-block;padding:10px 22px;background:{lvl_color};'
            f'color:#ffffff;text-decoration:none;border-radius:8px;font-weight:600;'
            f'font-size:14px;">Alarmı Görüntüle</a>'
            f'</td></tr></table>'
        )

    subject = f"[{lvl_label}] {rule_name} — {device_label}"
    html_body = f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(subject)}</title>
</head>
<body style="margin:0;padding:24px 12px;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0f172a;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center"
         style="max-width:600px;width:100%;background:#ffffff;border-radius:14px;
                box-shadow:0 4px 14px rgba(15,23,42,0.06);overflow:hidden;">
    <tr>
      <td style="background:{lvl_color};padding:18px 24px;color:#ffffff;">
        <div style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase;
                    font-weight:700;opacity:0.85;">{title_safe}</div>
        <div style="font-size:20px;font-weight:700;margin-top:4px;">
          ⚠ Yeni Alarm — {_esc(lvl_label)}
        </div>
      </td>
    </tr>
    <tr>
      <td style="padding:24px;">
        <h2 style="margin:0 0 8px;font-size:18px;color:#0f172a;line-height:1.3;">
          {rule_safe}
        </h2>
        {desc_block}
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
               style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;
                      border-collapse:separate;border-spacing:0;">
          {rows_html}
        </table>
        {cta_block}
      </td>
    </tr>
    <tr>
      <td style="padding:14px 24px;background:#f8fafc;color:#94a3b8;font-size:11px;
                 line-height:1.5;border-top:1px solid #e2e8f0;">
        Bu e-posta, alarm kuralinin "E-posta gonder" secenegi acik oldugu icin
        gonderildi. Bildirimleri yonetmek icin sistem yoneticinizle iletisime
        gecin.
      </td>
    </tr>
  </table>
</body>
</html>"""
    return subject, html_body


# --------- Fault email (hat arizasi) ----------------------------------

def _google_maps_static_url(
    latitude: float, longitude: float, *, zoom: int = 15, size: str = "640x320"
) -> str:
    """API key'siz, public Google Maps statik gorsel URL'i.

    Not: Google'in `staticmap` endpoint'i 2018'den beri API anahtarini
    zorunlu tutuyor. Bu sebeple e-posta'da OpenStreetMap (staticmap.openstreetmap.de)
    kullaniyoruz; URL formati ayni mantik.
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
    kullanici adi. Severity kucuk rozetle gosterilir; CTA butonu (varsa
    link_path) "Detayi Goruntule" yazar."""
    lvl_label = _level_label_tr(level)
    lvl_color = _level_color(level)
    title_safe = _esc(project_title or PRODUCT_NAME)
    name_safe = _esc(recipient_full_name)
    rule_safe = _esc(title)
    desc_safe = _esc(description) if description else ""
    actor_safe = _esc(actor_username) if actor_username else "-"
    eyebrow = "Yeni Arıza Ataması" if kind == "fault" else "Yeni Alarm Ataması"
    icon_emoji = "🛠" if kind == "fault" else "🔔"

    rows: list[tuple[str, str]] = [
        ("Atayan", actor_safe),
    ]
    if level:
        rows.append((
            "Seviye",
            f'<span style="display:inline-block;padding:2px 10px;border-radius:6px;'
            f'background:{lvl_color};color:#ffffff;font-size:11px;font-weight:700;'
            f'letter-spacing:0.04em;">{_esc(lvl_label)}</span>',
        ))
    rows_html = "".join(
        f'<tr>'
        f'<td style="padding:10px 14px;color:#64748b;font-size:12px;'
        f'text-transform:uppercase;letter-spacing:0.04em;font-weight:700;'
        f'width:120px;border-bottom:1px solid #f1f5f9;">{_esc(label)}</td>'
        f'<td style="padding:10px 14px;color:#0f172a;font-size:14px;'
        f'border-bottom:1px solid #f1f5f9;">{value}</td>'
        f'</tr>'
        for label, value in rows
    )

    desc_block = (
        f'<p style="margin:0 0 16px;color:#475569;font-size:14px;line-height:1.5;">'
        f'{desc_safe}</p>'
    ) if desc_safe else ""

    cta_block = ""
    if link_path:
        cta_block = (
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:20px 0 0;"><tr><td>'
            f'<span style="display:inline-block;padding:10px 22px;background:#4338ca;'
            f'color:#ffffff;border-radius:8px;font-weight:600;font-size:14px;">'
            f'Sistemden detayını görüntüleyebilirsiniz</span>'
            f'</td></tr></table>'
        )

    subject = f"[{eyebrow}] {title}"
    html_body = f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(subject)}</title>
</head>
<body style="margin:0;padding:24px 12px;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0f172a;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center"
         style="max-width:600px;width:100%;background:#ffffff;border-radius:14px;
                box-shadow:0 4px 14px rgba(15,23,42,0.06);overflow:hidden;">
    <tr>
      <td style="background:#4338ca;padding:18px 24px;color:#ffffff;">
        <div style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase;
                    font-weight:700;opacity:0.85;">{title_safe}</div>
        <div style="font-size:20px;font-weight:700;margin-top:4px;">
          {icon_emoji} {_esc(eyebrow)}
        </div>
      </td>
    </tr>
    <tr>
      <td style="padding:24px;">
        <p style="margin:0 0 14px;color:#0f172a;font-size:15px;">
          Merhaba <strong>{name_safe}</strong>,
        </p>
        <p style="margin:0 0 16px;color:#475569;font-size:14px;line-height:1.5;">
          Sistemde size yeni bir {"arıza" if kind == "fault" else "alarm"}
          atandı. Detaylar aşağıdadır.
        </p>
        <h2 style="margin:0 0 8px;font-size:18px;color:#0f172a;line-height:1.3;">
          {rule_safe}
        </h2>
        {desc_block}
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
               style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;
                      border-collapse:separate;border-spacing:0;">
          {rows_html}
        </table>
        {cta_block}
      </td>
    </tr>
    <tr>
      <td style="padding:14px 24px;background:#f8fafc;color:#94a3b8;font-size:11px;
                 line-height:1.5;border-top:1px solid #e2e8f0;">
        Bu e-posta size yapılan atama nedeniyle gönderildi. Bildirimleri
        kapatmak için kullanıcı tercihlerinizden e-posta seçeneğini
        kapatabilirsiniz.
      </td>
    </tr>
  </table>
</body>
</html>"""
    return subject, html_body


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
    """Hat arizasi HTML maili. Konum verilmise harita gorseli + yol-tarifi linki
    dahil edilir."""
    title_safe = _esc(project_title or PRODUCT_NAME)
    line_safe = _esc(line_name)
    line_code_safe = f" ({_esc(line_code)})" if line_code else ""
    region_safe = _esc(region_name) if region_name else "—"

    last_red = last_red_device_name or last_red_device_code or "—"
    first_green = first_green_device_name or first_green_device_code or "—"

    pole_range = "—"
    if from_pole_seq is not None and to_pole_seq is not None:
        pole_range = f"#{from_pole_seq} ↔ #{to_pole_seq}"

    opened_str = _format_timestamp(opened_at)

    # Map blok (lat/long varsa)
    map_block = ""
    if latitude is not None and longitude is not None:
        try:
            lat_f = float(latitude)
            lon_f = float(longitude)
            static_url = _google_maps_static_url(lat_f, lon_f)
            directions_url = _google_maps_directions_url(lat_f, lon_f)
            coord_label = f"{lat_f:.6f}, {lon_f:.6f}"
            map_block = f"""
        <div style="margin:24px 0 12px;">
          <h3 style="margin:0 0 10px;font-size:14px;color:#475569;
                     text-transform:uppercase;letter-spacing:0.05em;font-weight:700;">
            Konum
          </h3>
          <a href="{_esc(directions_url)}" target="_blank"
             style="display:block;border-radius:10px;overflow:hidden;
                    border:1px solid #e2e8f0;text-decoration:none;">
            <img src="{_esc(static_url)}" alt="Arıza Konumu"
                 style="display:block;width:100%;height:auto;max-width:600px;border:0;">
          </a>
          <div style="margin-top:8px;font-size:12px;color:#64748b;
                      font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;">
            {_esc(coord_label)}
          </div>
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"
                 style="margin:14px 0 0;">
            <tr><td>
              <a href="{_esc(directions_url)}" target="_blank"
                 style="display:inline-block;padding:10px 22px;background:#2563eb;
                        color:#ffffff;text-decoration:none;border-radius:8px;
                        font-weight:600;font-size:14px;">
                🗺  Yol Tarifi Al
              </a>
            </td></tr>
          </table>
        </div>
"""
        except (TypeError, ValueError):
            map_block = ""

    # Detay tablo
    rows: list[tuple[str, str]] = []
    rows.append(("Hat", _esc(line_name) + line_code_safe))
    rows.append(("Bölge", region_safe))
    rows.append(("Direk Aralığı", _esc(pole_range)))
    rows.append(("Son Aktif Cihaz", _esc(last_red)))
    rows.append(("İlk Sağlam Cihaz", _esc(first_green)))
    if assigned_to:
        rows.append(("Görevli", _esc(assigned_to)))
    if opened_str:
        rows.append(("Başlangıç", _esc(opened_str)))

    rows_html = "".join(
        f'<tr>'
        f'<td style="padding:10px 14px;color:#64748b;font-size:12px;'
        f'text-transform:uppercase;letter-spacing:0.04em;font-weight:700;'
        f'width:160px;border-bottom:1px solid #f1f5f9;">{_esc(label)}</td>'
        f'<td style="padding:10px 14px;color:#0f172a;font-size:14px;'
        f'border-bottom:1px solid #f1f5f9;">{value}</td>'
        f'</tr>'
        for label, value in rows
    )

    cta_block = ""
    if fault_link:
        cta_block = (
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:20px 0 0;"><tr><td>'
            f'<a href="{_esc(fault_link)}" '
            f'style="display:inline-block;padding:10px 22px;background:#dc2626;'
            f'color:#ffffff;text-decoration:none;border-radius:8px;font-weight:600;'
            f'font-size:14px;">Arıza Detayını Aç</a>'
            f'</td></tr></table>'
        )

    subject = f"[Hat Arızası] {line_name} — {region_name or 'Bölge'}"
    html_body = f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(subject)}</title>
</head>
<body style="margin:0;padding:24px 12px;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0f172a;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center"
         style="max-width:600px;width:100%;background:#ffffff;border-radius:14px;
                box-shadow:0 4px 14px rgba(15,23,42,0.06);overflow:hidden;">
    <tr>
      <td style="background:#dc2626;padding:18px 24px;color:#ffffff;">
        <div style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase;
                    font-weight:700;opacity:0.85;">{title_safe}</div>
        <div style="font-size:20px;font-weight:700;margin-top:4px;">
          ⚡ Hat Arızası
        </div>
      </td>
    </tr>
    <tr>
      <td style="padding:24px;">
        <h2 style="margin:0 0 16px;font-size:18px;color:#0f172a;line-height:1.3;">
          {line_safe}{line_code_safe}
        </h2>
        <p style="margin:0 0 16px;color:#475569;font-size:14px;line-height:1.5;">
          Hat üzerinde arıza tespit edildi. Aşağıdaki konum bilgisi ile
          sahaya yönlenebilir, harita üstünden yol tarifi alabilirsiniz.
        </p>
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
               style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;
                      border-collapse:separate;border-spacing:0;">
          {rows_html}
        </table>
        {map_block}
        {cta_block}
      </td>
    </tr>
    <tr>
      <td style="padding:14px 24px;background:#f8fafc;color:#94a3b8;font-size:11px;
                 line-height:1.5;border-top:1px solid #e2e8f0;">
        Bu e-posta hat arızası bildirimi olarak otomatik oluşturuldu. Yol tarifi
        butonu telefonunuzdaki harita uygulamasını açacaktır.
      </td>
    </tr>
  </table>
</body>
</html>"""
    return subject, html_body
