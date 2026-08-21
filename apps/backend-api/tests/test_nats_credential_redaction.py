"""NATS PAROLASI UYGULAMA LOGLARINA DUSMEZ.

YASANAN RISK
------------
Baglanti URL'i kimlik tasiyor:

    nats://backend:SuperSecretPassword@nats:4222

ve su satirlarda DUZ METIN olarak log'a dusuyordu:

    logger.info("jetstream_bus_ready url=%s", self._url)
    logger.warning("jetstream_bus_start_failed url=%s ...", self._url)
    logger.info("tag-engine-running url=%s ...", NATS_URL)
    logger.info("iec104_consumer_running ... url=%s", s.nats_url)
    logger.info("modbus_consumer_running ... url=%s", s.nats_url)
    print(f"alarm-service-running ... url={NATS_URL}")

Log'lar teshis icin destege gonderiliyor, `docker logs` ile ekrana basiliyor
ve saha cihazinin diskinde duruyor: parola tek satirla uc ayri yere kopyalanmis
oluyordu.

BU DOSYANIN KILITLEDIGI UC SEY
------------------------------
1. Maskeleme fonksiyonunun kendisi (birim davranis + fail-safe).
2. GERCEK log cagri noktasi: yalnizca yardimci fonksiyonun dondurdugu degeri
   test etmek yetmez — cagri yerinde ham degiskeni gecmek hala mumkundur.
   Bu yuzden gercek `start()` yolu surulur ve YAKALANAN LOG metninde sirrin
   OLMADIGI dogrulanir.
3. Kopyalar arasi PARITE: servisler ayri imajlar, ortak Python paketi yok;
   helper kopyalandi. Kopyalar ayrisirsa bir serviste maskeleme sessizce
   zayiflar.
"""

from __future__ import annotations

import logging
import pathlib

import pytest

from app.core.redaction import MASK, redact_url_credentials

SIR = "DO_NOT_LEAK_12345"
KOK = pathlib.Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# 1. Birim davranis
# ---------------------------------------------------------------------------


def test_kullanici_adi_parola_maskelenir():
    assert (
        redact_url_credentials("nats://backend:secret@nats:4222")
        == f"nats://backend:{MASK}@nats:4222"
    )


def test_credential_YOKSA_url_bozulmaz():
    """Anonim URL anlamsiz sekilde degistirilmemeli — teshiste adres lazim."""
    for url in ("nats://nats:4222", "nats://localhost:4222", "nats://10.0.0.5:4222"):
        assert redact_url_credentials(url) == url


def test_KOLONSUZ_userinfo_da_maskelenir():
    """`nats://TOKEN@host` NATS'ta TOKEN kimlik dogrulamasidir.

    "Kullanici adi gorunur kalabilir" kuralini duz uygulamak burada tokeni
    log'a basardi — tek parca olan sey kullanici adi degil, sirrin kendisi.
    """
    cikti = redact_url_credentials(f"nats://{SIR}@nats:4222")
    assert SIR not in cikti
    assert cikti == f"nats://{MASK}@nats:4222"


def test_parolada_ozel_karakter():
    # Parolada "@" olabilir; ayirici SON "@" olmali.
    cikti = redact_url_credentials("nats://u:p@ss@word@nats:4222")
    assert "p@ss@word" not in cikti
    assert cikti.startswith(f"nats://u:{MASK}@")


@pytest.mark.parametrize(
    "anahtar", ["password", "passwd", "token", "secret", "api_key", "apikey"]
)
def test_sorgu_dizesindeki_sirlar_maskelenir(anahtar):
    cikti = redact_url_credentials(f"nats://host:4222?{anahtar}={SIR}")
    assert SIR not in cikti
    assert f"{anahtar}={MASK}" in cikti


def test_sorgudaki_zararsiz_parametre_korunur():
    cikti = redact_url_credentials("nats://host:4222?name=grid&token=x")
    assert "name=grid" in cikti
    assert f"token={MASK}" in cikti


def test_VIRGULLU_sunucu_listesinde_HEPSI_maskelenir():
    """NATS birden fazla sunucuyu tek dizede kabul eder.

    Yalnizca ilkini maskelemek ikinci sunucunun parolasini log'a birakirdi.
    """
    cikti = redact_url_credentials(
        f"nats://a:{SIR}@h1:4222,nats://b:{SIR}@h2:4222"
    )
    assert SIR not in cikti
    assert cikti.count(MASK) == 2


@pytest.mark.parametrize(
    "girdi",
    ["", "   ", None, "duz-metin", "://bozuk", "nats://", 12345, object()],
)
def test_BOZUK_girdi_ISTISNA_uretmez(girdi):
    """Log yolunda atilan istisna, teshis satirini ikinci bir ariza yapardi."""
    sonuc = redact_url_credentials(girdi)
    assert isinstance(sonuc, str)


def test_COZULEMEYEN_girdi_HAM_donmez():
    """Ayristiramadigimiz metin "parola yok" demek DEGILDIR.

    Supheli girdiyi oldugu gibi log'a basmak tam da onlemeye calistigimiz sey.
    """
    assert SIR not in redact_url_credentials(f"bu bir url degil {SIR}")


# ---------------------------------------------------------------------------
# 2. GERCEK log cagri noktasi (jetstream_bus)
# ---------------------------------------------------------------------------


def _bus(url: str):
    """Gercek `JetStreamBus` — uretimdeki kurulumun ayni imzasi.

    Sema/subject adlari testin konusu degil; onemli olan `url` alaninin
    HAM kalmasi ve log yolunun maskelemesi.
    """
    from app.services import jetstream_bus as jb

    return jb, jb.JetStreamBus(
        url=url,
        stream_raw="RAW",
        stream_normalized="NORM",
        stream_dlq="DLQ",
        subject_raw_pattern="e1.telemetry.raw.>",
        subject_normalized_pattern="e1.telemetry.norm.>",
        subject_dlq_pattern="e1.telemetry.dlq.>",
        max_age_days_raw=1,
        max_age_days_normalized=1,
        max_age_days_dlq=1,
        connect_timeout_sec=1,
    )


def test_start_failed_log_satirinda_SIR_YOK(caplog, monkeypatch):
    """Gercek `start()` yolu surulur; yakalanan log'da parola olmamali."""
    from app.services import jetstream_bus as jb

    url = f"nats://backend:{SIR}@nats:4222"
    monkeypatch.setattr(jb, "NATS_AVAILABLE", True, raising=False)
    _jb, otobus = _bus(url)

    async def _patla():
        raise RuntimeError("baglanti kurulamadi")

    monkeypatch.setattr(otobus, "_connect_and_setup", _patla, raising=False)

    with caplog.at_level(logging.WARNING):
        sonuc = otobus.start()

    metin = caplog.text
    assert sonuc is False
    assert "jetstream_bus_start_failed" in metin, "gercek cagri noktasi kosmadi"
    assert SIR not in metin, "PAROLA LOG'A DUSTU"
    assert MASK in metin, "maskeli gosterim yok"


def test_ready_log_satirinda_SIR_YOK(caplog, monkeypatch):
    """Basari yolundaki `jetstream_bus_ready` satiri da maskeli olmali."""
    from app.services import jetstream_bus as jb

    url = f"nats://backend:{SIR}@nats:4222"
    monkeypatch.setattr(jb, "NATS_AVAILABLE", True, raising=False)
    _jb, otobus = _bus(url)

    async def _tamam():
        return None

    monkeypatch.setattr(otobus, "_connect_and_setup", _tamam, raising=False)

    with caplog.at_level(logging.INFO):
        sonuc = otobus.start()
    try:
        metin = caplog.text
        assert sonuc is True
        assert "jetstream_bus_ready" in metin, "gercek cagri noktasi kosmadi"
        assert SIR not in metin, "PAROLA LOG'A DUSTU"
        assert MASK in metin
    finally:
        otobus.stop()


def test_CALISMA_ZAMANI_url_degismedi():
    """Maskeleme YALNIZCA gosterim icin: istemciye giden deger ham kalmali.

    Maskelenmis URL'yi baglanti icin kullanmak, parolasi "***" olan bir
    sunucuya baglanmayi denemek demektir.
    """
    url = f"nats://backend:{SIR}@nats:4222"
    _jb, otobus = _bus(url)
    assert otobus._url == url

    kaynak = (
        KOK / "apps/backend-api/app/services/jetstream_bus.py"
    ).read_text(encoding="utf-8")
    assert "servers=[self._url]" in kaynak, (
        "baglanti maskelenmis URL'yi kullaniyor olabilir"
    )


# ---------------------------------------------------------------------------
# 3. Kopyalar arasi parite
# ---------------------------------------------------------------------------

KOPYALAR = [
    "apps/tag-engine/tag_engine/redaction.py",
    "apps/alarm-service/alarm_service/redaction.py",
    "apps/iec104-outbound/iec104_outbound/redaction.py",
    "apps/modbus-outbound/modbus_outbound/redaction.py",
]


def _govde(yol: str) -> str:
    """Dosyanin docstring SONRASI kismi — kopyalarda yalnizca ust not farkli."""
    metin = (KOK / yol).read_text(encoding="utf-8")
    return metin.split('"""', 2)[2]


@pytest.mark.parametrize("yol", KOPYALAR)
def test_kopya_kanonikle_AYNI(yol):
    """Kopyalar ayrisirsa bir serviste maskeleme sessizce zayiflar."""
    assert _govde(yol) == _govde("apps/backend-api/app/core/redaction.py"), (
        f"{yol} kanonik surumden ayrismis — "
        "apps/backend-api/app/core/redaction.py'den kopyalayin"
    )


# ---------------------------------------------------------------------------
# 4. Repo taramasi: ham NATS URL'i log argumaninda kalmasin
# ---------------------------------------------------------------------------

#: (dosya, log cagrisinda GECMESI YASAK ham ifade)
HAM_KULLANIMLAR = [
    ("apps/backend-api/app/services/jetstream_bus.py", "self._url,"),
    # Modul duzeyindeki fabrika da log basiyor (`jetstream_bus_started`,
    # `jetstream_bus_unavailable`) ve ilk taramada GOZDEN KACMISTI: yalnizca
    # sinif icindeki `self._url` aranmisti.
    ("apps/backend-api/app/services/jetstream_bus.py", "settings.nats_url,"),
    ("apps/tag-engine/tag_engine/main.py", "NATS_URL,"),
    ("apps/iec104-outbound/iec104_outbound/consumer.py", "s.nats_url,"),
    ("apps/modbus-outbound/modbus_outbound/consumer.py", "s.nats_url,"),
]


@pytest.mark.parametrize("yol,ham", HAM_KULLANIMLAR)
def test_log_argumaninda_HAM_url_kalmadi(yol, ham):
    """`logger.*(...)` govdesinde ham URL degiskeni gecmemeli.

    Yalnizca `servers=[...]` gibi BAGLANTI kullanimlari serbest; onlar
    calisma zamani degeridir ve ham kalmak ZORUNDA.
    """
    metin = (KOK / yol).read_text(encoding="utf-8")
    for i, satir in enumerate(metin.splitlines(), start=1):
        if satir.strip() != ham.strip():
            continue
        # Bu satirdan onceki 8 satirda bir log cagrisi var mi?
        onceki = "\n".join(metin.splitlines()[max(0, i - 9) : i])
        assert not any(
            im in onceki for im in ("logger.info", "logger.warning", "logger.error", "print(")
        ), f"{yol}:{i} ham URL bir log cagrisina veriliyor"


def test_alarm_service_print_maskeli():
    """`print` de uygulama log'udur: konteyner stdout'u toplaniyor."""
    metin = (KOK / "apps/alarm-service/alarm_service/main.py").read_text(encoding="utf-8")
    for satir in metin.splitlines():
        if "print(" in satir and "url=" in satir:
            assert "redact_url_credentials" in satir, f"maskesiz print: {satir.strip()}"
