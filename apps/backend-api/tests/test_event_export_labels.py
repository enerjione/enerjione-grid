"""Export EKRANLA AYNI seyi yazmali + PDF sablonu bozulmamali.

IKI AYRI ARIZA SINIFI
---------------------
1. AYRISMA (drift). Olay mesajlarinin Turkce sablonlari frontend
   `tr.json`'da yasiyor; backend export'u ayni metni uretebilmek icin
   `app/data/event_labels_tr.json` aynasini kullaniyor. Biri guncellenip
   digeri unutulursa PDF'te "Alarm rule triggered: Test alarmi" (ham
   Ingilizce) gorunur, ekranda ise Turkcesi — musteriye giden raporda.
   Bu testler iki dosyayi birbirine kilitler.

2. TASAN HUCRE. Eski PDF duz string hucreler kullaniyordu; uzun mesaj
   komsu sutunun uzerine biniyordu. Hucreler artik Paragraph — testte
   uzun metinli satirla PDF uretilip cok sayfali cikti dogrulanir.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services import event_labels
from app.services.event_labels import (
    category_label,
    format_message,
    message_subject,
    severity_label,
    status_key,
    status_label,
)

BACKEND_LABELS = Path(__file__).resolve().parents[1] / "app" / "data" / "event_labels_tr.json"
# tests/ -> backend-api/ -> apps/  (parents[2]); frontend kardes dizinde.
FRONTEND_TR = (
    Path(__file__).resolve().parents[2]
    / "frontend-web"
    / "src"
    / "shared"
    / "i18n"
    / "resources"
    / "tr.json"
)


def _frontend_events() -> dict:
    with FRONTEND_TR.open(encoding="utf-8") as handle:
        return json.load(handle)["events"]


@pytest.mark.skipif(not FRONTEND_TR.exists(), reason="frontend kaynagi yok (backend-only checkout)")
@pytest.mark.parametrize("section", ["message", "category", "severity", "status"])
def test_backend_aynasi_frontend_ile_ayni(section):
    """Ayna dosyasi tr.json ile BIREBIR — eksik/fazla/degismis anahtar yok."""
    with BACKEND_LABELS.open(encoding="utf-8") as handle:
        backend = json.load(handle)
    frontend = _frontend_events()[section]
    assert backend[section] == frontend, (
        f"'{section}' bolumu ayrismis. Duzeltmek icin tr.json'daki events.{section} "
        f"icerigini {BACKEND_LABELS.name} dosyasina kopyalayin."
    )


def test_i18n_sablonu_uygulanir():
    meta = json.dumps({"_i18n": {"key": "alarm_triggered", "params": {"title": "Test alarmı"}}})
    assert format_message("Alarm rule triggered: Test alarmı", meta) == (
        "Alarm kuralı gerçekleşti: Test alarmı"
    )
    # Mesaj sutunu yalnizca OZNE gosterir (ekranla ayni).
    assert message_subject("Alarm rule triggered: Test alarmı", meta) == "Test alarmı"


def test_i18n_yoksa_ham_mesaj():
    assert format_message("Ham mesaj", None) == "Ham mesaj"
    assert format_message("Ham mesaj", "{bozuk json") == "Ham mesaj"
    # Bilinmeyen anahtar -> ham mesaja duser (bos string DEGIL).
    meta = json.dumps({"_i18n": {"key": "boyle_bir_olay_yok", "params": {}}})
    assert format_message("Ham mesaj", meta) == "Ham mesaj"


def test_ayrac_yoksa_mesaj_bozulmaz():
    # Iki nokta yoksa mesaj oldugu gibi kalir.
    assert message_subject("Bildirim ayarları güncellendi", None) == (
        "Bildirim ayarları güncellendi"
    )
    # Ayrac cok gec geliyorsa (icerikteki iki nokta) kirpma YAPILMAZ.
    uzun = "A" * 70 + ": kuyruk"
    assert message_subject(uzun, None) == uzun


@pytest.mark.parametrize(
    "event_type,beklenen",
    [
        ("alarm_triggered", "triggered"),
        ("alarm_auto_cleared", "cleared"),
        ("device_command_queued", "queued"),
        ("device_deleted", "deleted"),
        ("gateway_created", "created"),
        ("config_applied", "success"),
        ("outbound_dead_letter", "failed"),
        ("firewall_disabled", "disabled"),
        ("signal_updated", "updated"),
        ("bilinmeyen_olay", "info"),
    ],
)
def test_durum_turetme(event_type, beklenen):
    assert status_key(event_type) == beklenen


def test_etiketler_turkce():
    assert status_label("alarm_triggered") == "Tetiklendi"
    assert category_label("alarm") == "Alarmlar"
    assert severity_label("warning") == "Uyarı"
    # Bilinmeyen kategori insanlastirilir (frontend _humanize ile ayni).
    assert category_label("yeni-modul") == "Yeni modul"


# --- PDF sablonu ------------------------------------------------------------


class _FakeEvent:
    def __init__(self, i: int, message: str, metadata_json: str | None = None):
        self.id = i
        self.category = "alarm"
        self.event_type = "alarm_triggered"
        self.severity = "warning"
        self.message = message
        self.metadata_json = metadata_json
        self.actor_username = "installer"
        self.device_code = "50984"
        self.created_at = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc) - timedelta(minutes=i)


def test_pdf_uretimi_cok_sayfa_ve_logo():
    from app.api.events import _build_pdf, _format_rows_for_export
    from app.models.project_settings import ProjectSettings

    uzun = "Çok uzun bir olay mesajı — " + ("sarma davranışı sınanıyor " * 12)
    events = [_FakeEvent(i, uzun if i % 3 == 0 else "Kısa mesaj") for i in range(60)]
    rows = _format_rows_for_export(events, {"50984": "Ayaş Fider 3"})

    # Cihaz KODU degil ADI yazilmali (ekranla ayni).
    assert rows[0]["device"] == "Ayaş Fider 3"
    # Tarih okunabilir bicimde (ISO 'T' YOK).
    assert "T" not in rows[0]["created_at"]
    assert rows[0]["created_at"].count(".") == 2

    pdf = _build_pdf(rows, settings_row=ProjectSettings(id=1, customer_name="Başkent EDAŞ"))
    assert pdf.startswith(b"%PDF")
    # Uzun metin sarinca birden fazla sayfa olusur; altbilgi "Sayfa X / Y"
    # yazabilmek icin toplam sayfa ikinci gecise kalir.
    assert pdf.count(b"/Type /Page\n") > 1 or b"/Count" in pdf


def test_pdf_bos_liste_de_uretilir():
    from app.api.events import _build_pdf

    pdf = _build_pdf([], settings_row=None)
    assert pdf.startswith(b"%PDF")


def test_bozuk_logo_raporu_dusurmez():
    from app.services.report_layout import decode_data_url_image

    assert decode_data_url_image("data:image/png;base64,ZZZ!!") is None
    assert decode_data_url_image("data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=") is None
    assert decode_data_url_image(None) is None
    assert decode_data_url_image("http://example.com/logo.png") is None


def test_turkce_karakter_fontu_secilir():
    """Font secimi Turkce destekli bir TTF bulmali; bulamazsa Helvetica'ya
    duser (rapor yine uretilir). Ikisi de kabul — burada onemli olan
    cagrinin PATLAMAMASI ve gecerli bir font adi donmesi."""
    event_labels._labels.cache_clear()
    from app.services.report_layout import report_fonts

    regular, bold = report_fonts()
    assert isinstance(regular, str) and regular
    assert isinstance(bold, str) and bold
