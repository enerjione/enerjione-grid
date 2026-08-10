"""WhatsApp / Telegram / SMS mesajlari SAHADA IS GORMELI.

YASANAN SORUN
-------------
Gonderilen mesaj iki satirdi:

    Alarm: Asiri akim - esik asildi
    Seviye: CRITICAL

Bu mesajla sahaya cikilamaz:
  * HANGI CIHAZ alarm verdi yazmiyordu.
  * HANGI FAZ oldugu yoktu — master/sat01/sat02 hattin AYRI fazlarina
    takilir, ariza fazi mudahalenin ilk bilgisidir.
  * Seviye INGILIZCE'ydi ("CRITICAL") — arayuzun tamami Turkce.
  * Hat arizasi mesajinda tahmini MESAFE ARALIGI yoktu; ekibi yonlendiren
    asil sayi odur.

Bu dosya o dort bilginin mesajdan CIKARILAMAMASINI kilitler.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.services import chat_templates as ct

ALARM = {
    "title": "Aşırı akım",
    "level": "critical",
    "description": "Eşik aşıldı",
    "device_name": "Direk-12",
    "device_code": "DEV-1",
    "signal_key": "sat01.current_phase_a",
    "line_name": "HAT-1",
    "region_name": "Batman",
    "occurred_at": datetime(2026, 8, 10, 9, 3, tzinfo=timezone.utc),
}

ARIZA = {
    "line_name": "HAT-1",
    "region_name": "Batman",
    "from_pole_seq": 3,
    "to_pole_seq": 4,
    "last_red_name": "Direk-12",
    "first_green_name": None,
    "zone_start_m": 9920.0,
    "zone_end_m": 14430.0,
    "zone_length_m": 4510.0,
    "trigger_titles": ["Aşırı akım (Satellite 01)"],
    "latitude": 37.88,
    "longitude": 41.13,
    "opened_at": datetime(2026, 8, 10, 9, 3, tzinfo=timezone.utc),
}


# ------------------------------------------------------------------ seviye


def test_seviye_TURKCE():
    assert ct.level_label("critical") == "KRİTİK"
    assert ct.level_label("warning") == "UYARI"
    assert ct.level_label("info") == "BİLGİ"


def test_seviye_emojisi_seviyeye_gore_DEGISIR():
    """Tek bakista aciliyet: kirmizi mi sari mi."""
    esler = {ct.level_emoji(x) for x in ("critical", "warning", "info")}
    assert len(esler) == 3, "seviyeler ayni emojiyi paylasiyor"


def test_bilinmeyen_seviye_PATLAMAZ():
    assert ct.level_label(None)
    assert ct.level_label("beklenmedik") == "BEKLENMEDIK"


# ------------------------------------------------------------------- alarm


def test_alarm_mesajinda_CIHAZ_ve_FAZ_var():
    for metin in (ct.alarm_whatsapp(**ALARM), ct.alarm_telegram_html(**ALARM)):
        assert "Direk-12" in metin, "cihaz adi yok — sahada nereye gidilecegi bilinmiyor"
        assert "DEV-1" in metin
        assert "Satellite 01" in metin, "faz bilgisi yok"


def test_alarm_mesajinda_seviye_INGILIZCE_degil():
    metin = ct.alarm_whatsapp(**ALARM)
    assert "KRİTİK" in metin
    assert "CRITICAL" not in metin.upper()


def test_alarm_mesajinda_HAT_ve_BOLGE_var():
    metin = ct.alarm_whatsapp(**ALARM)
    assert "Batman" in metin and "HAT-1" in metin


def test_telegram_HTML_kacisi_yapiliyor():
    """parse_mode=HTML: kacisi yapilmayan '<' mesaji tamamen dusurur."""
    metin = ct.alarm_telegram_html(**{**ALARM, "title": "A < B & C"})
    assert "&lt;" in metin and "&amp;" in metin
    assert "A < B" not in metin


def test_SMS_te_emoji_ve_TURKCE_harf_YOK():
    """GSM-7 disi tek bir karakter mesaji UCS-2'ye dusurur: segment 160
    yerine 70 karakter olur, metin ortadan kirpilir ve ucret katlanir."""
    for metin in (ct.alarm_sms(**ALARM), ct.fault_sms(**ARIZA)):
        assert all(ord(ch) < 128 for ch in metin), f"SMS'te GSM-7 disi karakter: {metin!r}"
        assert len(metin) <= 300
    metin = ct.alarm_sms(**ALARM)
    assert "Direk-12" in metin and "KRITIK" in metin
    assert "Asiri akim" in metin, "Turkce harfler ASCII'ye indirgenmemis"


def test_WhatsApp_ve_Telegram_TAM_TURKCE_kalir():
    """Karakter kisiti SMS'e ozgu; sohbet kanallarinda metin bozulmamali."""
    for metin in (ct.alarm_whatsapp(**ALARM), ct.fault_whatsapp(**ARIZA)):
        assert "Aşırı akım" in metin or "Arıza" in metin, metin
    assert "KRİTİK" in ct.alarm_whatsapp(**ALARM)


def test_SMS_te_konum_linki_BOZULMAZ():
    """ASCII indirgemesi URL'e dokunmamali."""
    metin = ct.fault_sms(**ARIZA)
    assert "https://maps.google.com/?q=37.88,41.13" in metin


# ------------------------------------------------------------------- ariza


def test_ariza_mesajinda_MESAFE_ARALIGI_var():
    """Ekibi yonlendiren asil sayi bu — eskiden mesajda hic yoktu."""
    metin = ct.fault_whatsapp(**ARIZA)
    assert "9,92 km" in metin, metin
    assert "14,43 km" in metin, metin
    assert "4,51 km" in metin, "belirsizlik araligi yok"


def test_ariza_mesajinda_BOLGE_HAT_DIREK_ve_CIHAZ_var():
    metin = ct.fault_whatsapp(**ARIZA)
    assert "Batman" in metin and "HAT-1" in metin
    assert "#3" in metin and "#4" in metin
    assert "Direk-12" in metin
    # Yesil cihaz yoksa arizanin hat ucuna kadar surdugu SOYLENMELI.
    assert "hat ucu" in metin


def test_ariza_mesajinda_TETIKLEYEN_ALARM_var():
    metin = ct.fault_whatsapp(**ARIZA)
    assert "Aşırı akım" in metin, "arizayi acan alarm mesajda yok"


def test_ariza_mesajinda_KONUM_linki_var():
    metin = ct.fault_whatsapp(**ARIZA)
    assert "maps.google.com" in metin and "37.88" in metin


def test_ariza_SMS_i_300_karakteri_ASMAZ():
    metin = ct.fault_sms(**ARIZA)
    assert len(metin) <= 300
    assert all(ord(ch) < 0x2000 for ch in metin)


def test_mesafe_bicimi_TURKCE_ondalik():
    assert ct.format_distance(9920.0) == "9,92 km"
    assert ct.format_distance(450.0) == "450 m"
    assert ct.format_distance(None) is None
    # Binlik ayraci nokta olmali: 12.345,67 km
    assert ct.format_distance(12_345_670.0) == "12.345,67 km"


def test_mesafe_yoksa_satir_HIC_cikmaz():
    """Koordinatsiz topolojide 'None - None' yazmamali."""
    metin = ct.fault_whatsapp(**{**ARIZA, "zone_start_m": None, "zone_end_m": None})
    assert "None" not in metin
    assert "Tahmini mesafe" not in metin


# ------------------------------------------------------------------ bicim


def test_emoji_SADECE_baslikta():
    """Her satira emoji koymak mesaji suslu ve amator gosteriyordu.
    Aciliyet basliktaki tek emojiyle verilir; govde sade kalir."""
    metin = ct.alarm_whatsapp(**ALARM)
    satirlar = [s for s in metin.split("\n") if s.strip()]
    assert satirlar[0].startswith(ct.level_emoji("critical"))
    for satir in satirlar[1:]:
        assert all(ord(ch) < 0x2600 for ch in satir), f"govde satirinda emoji var: {satir!r}"


def test_govde_ETIKET_kalin_DEGER_ince():
    """WhatsApp'ta `*...*` kalin. Etiket vurgulu, deger duz olmali."""
    metin = ct.alarm_whatsapp(**ALARM)
    assert "*Cihaz:* Direk-12" in metin, metin
    assert "*Konum:* Batman / HAT-1" in metin
    # Deger kalin OLMAMALI: "*Cihaz:* *Direk-12*" gibi bir sey cikmasin.
    assert "*Cihaz:* *" not in metin


def test_ariza_mesajinda_da_ayni_bicim():
    metin = ct.fault_whatsapp(**ARIZA)
    satirlar = [s for s in metin.split("\n") if s.strip()]
    assert satirlar[0].startswith("\U0001F534")
    for satir in satirlar[1:]:
        assert satir.startswith("*") and ":*" in satir, f"etiket bicimi bozuk: {satir!r}"
        assert all(ord(ch) < 0x2600 for ch in satir), f"govde satirinda emoji var: {satir!r}"


# ------------------------------------------------------------------- saat


def test_saat_YEREL_saat_dilimindedir():
    """UTC 08:00'de olusan alarm mesajda 11:00 gorunmeli (TR = UTC+3).
    Ham UTC basiliyordu; saha ekibi duvar saatine bakiyor."""
    utc = datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)
    metin = ct.alarm_whatsapp(**{**ALARM, "occurred_at": utc})
    assert "11:00" in metin, metin
    assert "08:00" not in metin


def test_naive_zaman_UTC_kabul_edilir():
    """DB'den tz bilgisi dusmus olabilir; yerel varsaymak degeri 3 saat kaydirirdi."""
    from app.services.local_time import to_local

    naive = datetime(2026, 8, 10, 8, 0)
    assert to_local(naive).hour == 11
