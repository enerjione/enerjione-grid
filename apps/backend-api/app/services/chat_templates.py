"""WhatsApp / Telegram / SMS mesaj metinleri — TEK KAYNAK.

YASANAN SORUN
-------------
Her kanal kendi metnini gonderim fonksiyonunun icinde uretiyordu ve hepsi
ayni iki satirdan ibaretti:

    Alarm: Asiri akim - esik asildi
    Seviye: CRITICAL

Sahadaki operator icin bu mesaj IS GORMUYOR:
  * HANGI CIHAZ alarm verdi yazmiyor. Tek basina "asiri akim" hangi direge
    gidilecegini soylemiyor.
  * HANGI FAZ oldugu yok. Bir SN2 govdesindeki uc sensor (master/sat01/
    sat02) hattin AYRI fazlarina takilir; ariza fazi mudahalenin ilk
    bilgisidir.
  * Seviye INGILIZCE ("CRITICAL") — arayuzun tamami Turkce.
  * Hat arizasi mesajinda tahmini MESAFE ARALIGI yoktu; oysa ekibi
    yonlendiren asil sayi odur.

Bu modul metinleri tek yerde toplar. Kanal farklari yalnizca BICIMDE:
  * WhatsApp : `*kalin*` (Baileys markdown)
  * Telegram : HTML (`<b>`) — parse_mode=HTML ile gonderiliyor
  * SMS      : sade metin, ~300 karakter butcesi, emoji YOK
                (GSM-7 disi karakter mesaji UCS-2'ye dusurup 70 karaktere
                 kirpar; emoji tek basina SMS maliyetini ucler)
"""

from __future__ import annotations

from datetime import datetime

from app.services import local_time

# Seviye -> (Turkce ad, emoji). Alarm-service ve kurallar bu dort seviyeyi
# uretir; bilinmeyen bir deger gelirse ham hali buyuk harfle gosterilir.
_LEVELS: dict[str, tuple[str, str]] = {
    "critical": ("KRİTİK", "\U0001F534"),   # kirmizi daire
    "error": ("HATA", "\U0001F7E0"),        # turuncu daire
    "high": ("YÜKSEK", "\U0001F7E0"),
    "warning": ("UYARI", "\U0001F7E1"),     # sari daire
    "info": ("BİLGİ", "\U0001F535"),        # mavi daire
}

# Sinyal kaynagi -> faz etiketi. Prefix `signal_key`ten gelir
# (`sat01.current_phase_a` -> `sat01`).
_SOURCES: dict[str, str] = {
    "master": "Master",
    "sat01": "Satellite 01",
    "sat02": "Satellite 02",
}


def level_label(level: str | None) -> str:
    """'critical' -> 'KRITIK'. Bilinmeyen seviye buyuk harfe cevrilir."""
    key = (level or "").strip().lower()
    if key in _LEVELS:
        return _LEVELS[key][0]
    return (level or "ALARM").upper()


def level_emoji(level: str | None) -> str:
    key = (level or "").strip().lower()
    if key in _LEVELS:
        return _LEVELS[key][1]
    return "⚠"  # uyari ucgeni


def source_label(signal_key: str | None) -> str | None:
    """`sat01.voltage_loss` -> 'Satellite 01'. Prefix yoksa None."""
    if not signal_key or "." not in signal_key:
        return None
    prefix = signal_key.split(".", 1)[0].lower()
    return _SOURCES.get(prefix)


def format_distance(meters: float | None) -> str | None:
    """Metre -> Turkce okunusla mesafe. 1 km alti metre, ustu km.

    Ondalik ayraci VIRGUL — mesaj sahadaki ekibe gidiyor, arayuzle ayni
    okunusta olmali (frontend `lineDistance.ts` ile ayni kural).
    """
    if meters is None:
        return None
    if meters < 1000:
        return f"{int(round(meters))} m"
    km = meters / 1000.0
    return f"{km:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".") + " km"


def _fmt_time(dt: datetime | None) -> str:
    """Mesajdaki saat YEREL saattir.

    Ham UTC basiliyordu: 11:00'de olusan alarm mesajda 08:00 gorunuyordu.
    Sistem UTC saklamaya devam eder; donusum yalnizca burada.
    """
    return local_time.fmt_local(dt)


def maps_link(latitude: float | None, longitude: float | None) -> str | None:
    if latitude is None or longitude is None:
        return None
    return f"https://maps.google.com/?q={latitude},{longitude}"


def _esc_html(s: str | None) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Turkce harf -> ASCII karsiligi. SADECE SMS icin.
_ASCII_MAP = str.maketrans(
    {
        "ı": "i", "İ": "I", "ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G",
        "ü": "u", "Ü": "U", "ö": "o", "Ö": "O", "ç": "c", "Ç": "C",
        "–": "-", "—": "-", "’": "'", "“": '"', "”": '"',
    }
)


def to_sms_charset(text: str) -> str:
    """Turkce harfleri ASCII'ye indirger — SADECE SMS metni icin.

    GSM-7 alfabesinde 'ş/ğ/ı/İ' yok. Mesajda bir tanesi bile gecerse
    sagayici tum mesaji UCS-2'ye dusurur ve segment boyu 160 karakterden
    70'e iner: metin ortadan kirpilir, ucret katlanir. WhatsApp / Telegram /
    e-posta tam Turkce kalir; kisitlama yalnizca SMS'e ozgudur.
    """
    return text.translate(_ASCII_MAP)


# ---------------------------------------------------------------- ALARM


def alarm_lines(
    *,
    title: str,
    level: str | None,
    description: str | None,
    device_name: str | None,
    device_code: str | None,
    signal_key: str | None,
    line_name: str | None,
    region_name: str | None,
    occurred_at: datetime | None,
) -> list[tuple[str, str]]:
    """Alarm govdesi — (ETIKET, DEGER) satirlari. Bicimden BAGIMSIZ.

    Satir basina emoji KOYULMAZ. Emoji yalnizca basliktadir (aciliyet
    seviyesi); govdede her satira emoji koymak mesaji suslu ve amator
    gosteriyordu. Vurgu bicimden gelir: etiket kalin, deger ince.
    """
    rows: list[tuple[str, str]] = []

    cihaz = device_name or device_code or None
    if cihaz:
        deger = f"{cihaz} ({device_code})" if device_code and device_name else cihaz
        faz = source_label(signal_key)
        if faz:
            deger = f"{deger} — {faz}"
        rows.append(("Cihaz", deger))

    yer = " / ".join(x for x in (region_name, line_name) if x)
    if yer:
        rows.append(("Konum", yer))

    if description:
        rows.append(("Açıklama", description.strip()))

    rows.append(("Saat", _fmt_time(occurred_at)))
    return rows


def alarm_whatsapp(**kw) -> str:
    """WhatsApp: `*kalin*` yalnizca baslik ve ETIKETLERDE."""
    lvl = kw.get("level")
    parts = [f"{level_emoji(lvl)} *{level_label(lvl)} ALARM*", "", f"*{kw['title']}*", ""]
    parts += [f"*{etiket}:* {deger}" for etiket, deger in alarm_lines(**kw)]
    return "\n".join(parts)


def alarm_telegram_html(**kw) -> str:
    lvl = kw.get("level")
    parts = [
        f"{level_emoji(lvl)} <b>{level_label(lvl)} ALARM</b>",
        "",
        f"<b>{_esc_html(kw['title'])}</b>",
        "",
    ]
    parts += [
        f"<b>{_esc_html(etiket)}:</b> {_esc_html(deger)}"
        for etiket, deger in alarm_lines(**kw)
    ]
    return "\n".join(parts)


def alarm_sms(
    *,
    title: str,
    level: str | None,
    device_name: str | None,
    device_code: str | None,
    signal_key: str | None,
    line_name: str | None,
    region_name: str | None,
    **_ignored,
) -> str:
    """SMS: emoji YOK, tek paragraf, 300 karaktere kirpilir."""
    cihaz = device_name or device_code or "-"
    faz = source_label(signal_key)
    yer = " / ".join(x for x in (region_name, line_name) if x)
    metin = f"{level_label(level)} ALARM: {title}. Cihaz: {cihaz}"
    if faz:
        metin += f" ({faz})"
    if yer:
        metin += f". {yer}"
    return to_sms_charset(metin)[:300]


# ---------------------------------------------------------------- ARIZA


def fault_lines(
    *,
    line_name: str | None,
    region_name: str | None,
    from_pole_seq: int | None,
    to_pole_seq: int | None,
    last_red_name: str | None,
    first_green_name: str | None,
    zone_start_m: float | None,
    zone_end_m: float | None,
    zone_length_m: float | None,
    trigger_titles: list[str] | None,
    latitude: float | None,
    longitude: float | None,
    opened_at: datetime | None,
) -> list[tuple[str, str]]:
    """Hat arizasi govdesi — (ETIKET, DEGER) satirlari (emoji yok)."""
    rows: list[tuple[str, str]] = []

    yer = " / ".join(x for x in (region_name, line_name) if x)
    if yer:
        rows.append(("Bölge / Hat", yer))

    if from_pole_seq is not None and to_pole_seq is not None:
        rows.append((
            "Arıza aralığı",
            f"Direk #{from_pole_seq} – Direk #{to_pole_seq}",
        ))

    # Arizanin hangi iki cihaz arasinda oldugunu ACIKCA yaz: ekip sahada
    # direk numarasindan cok cihaz adiyla yon buluyor.
    if last_red_name:
        arasi = first_green_name or "hat ucu"
        rows.append(("Cihazlar arası", f"{last_red_name} – {arasi}"))

    baslangic = format_distance(zone_start_m)
    bitis = format_distance(zone_end_m)
    if baslangic and bitis:
        deger = f"{baslangic} – {bitis} (hat başından)"
        belirsizlik = format_distance(zone_length_m)
        if belirsizlik:
            deger += f", {belirsizlik}'lik kesim"
        rows.append(("Tahmini mesafe", deger))

    if trigger_titles:
        gorunen = ", ".join(trigger_titles[:3])
        if len(trigger_titles) > 3:
            gorunen += f" (+{len(trigger_titles) - 3})"
        rows.append(("Tetikleyen alarm", gorunen))

    link = maps_link(latitude, longitude)
    if link:
        rows.append(("Konum", link))

    rows.append(("Saat", _fmt_time(opened_at)))
    return rows


def fault_whatsapp(**kw) -> str:
    parts = ["🔴 *HAT ARIZASI*", ""]
    parts += [f"*{etiket}:* {deger}" for etiket, deger in fault_lines(**kw)]
    return "\n".join(parts)


def fault_telegram_html(**kw) -> str:
    parts = ["🔴 <b>HAT ARIZASI</b>", ""]
    parts += [
        f"<b>{_esc_html(etiket)}:</b> {_esc_html(deger)}"
        for etiket, deger in fault_lines(**kw)
    ]
    return "\n".join(parts)


def fault_sms(
    *,
    line_name: str | None,
    region_name: str | None,
    from_pole_seq: int | None,
    to_pole_seq: int | None,
    last_red_name: str | None,
    zone_start_m: float | None,
    zone_end_m: float | None,
    latitude: float | None = None,
    longitude: float | None = None,
    **_ignored,
) -> str:
    """SMS: emoji YOK. Konum linki en sona — kirpilirsa once o gitsin."""
    yer = " / ".join(x for x in (region_name, line_name) if x) or "-"
    metin = f"HAT ARIZASI: {yer}"
    if from_pole_seq is not None and to_pole_seq is not None:
        metin += f". Direk #{from_pole_seq}-#{to_pole_seq}"
    if last_red_name:
        metin += f". Son arizali cihaz: {last_red_name}"
    baslangic = format_distance(zone_start_m)
    bitis = format_distance(zone_end_m)
    if baslangic and bitis:
        metin += f". Tahmini: {baslangic}-{bitis}"
    metin = to_sms_charset(metin)
    # Link EN SONA ve donusumden SONRA eklenir: URL'de karakter degistirmek
    # linki bozar, kirpilma olursa da once linkin gitmesi tercih edilir.
    link = maps_link(latitude, longitude)
    if link and len(metin) + len(link) + 2 <= 300:
        metin += f". {link}"
    return metin[:300]
