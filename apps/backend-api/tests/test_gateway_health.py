"""Gateway saglik heartbeat'i — ayristirma ve dayaniklilik testleri.

EN ONEMLI KURAL: `/gateways/{code}/pending` SCADA KOMUT KANALIDIR.
Saglik basligindaki hicbir sey — bozuk base64, gecersiz JSON, devasa
baslik, yanlis tip — o kanali dusurmemelidir. Aksi halde saglik raporlamak
icin eklenen bir ozellik, kesici acma/kapama komutlarinin iletilmesini
engeller. Asagidaki testlerin cogu tam olarak bunu kilitler.
"""

from __future__ import annotations

import base64
import json

from app.services import gateway_health_service as ghs


def _kodla(obj) -> str:
    ham = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(ham).decode("ascii").rstrip("=")


GECERLI = {
    "status": "degraded",
    "issues": ["outbox_near_capacity", "some_devices_comm_lost"],
    "outbox_pending": 1234,
    "outbox_dead_letter": 0,
    "devices": {"total": 300, "online": 287, "recovering": 3, "lost": 10},
    "uptime_sec": 86400,
    "version": "0.5.0",
}


# ------------------------------------------------------------- ayristirma


def test_gecerli_baslik_cozulur():
    out = ghs.parse_health_header(_kodla(GECERLI))
    assert out is not None
    assert out["status"] == "degraded"
    assert out["devices"]["lost"] == 10


def test_padding_atlanmis_base64_cozulur():
    """Gateway base64 padding'ini atlayabilir — kabul etmeliyiz."""
    kodlu = _kodla({"status": "ok"})
    assert "=" not in kodlu
    assert ghs.parse_health_header(kodlu) is not None


# --------------------------------------------- KOMUT KANALI DUSMEMELI


def test_bos_baslik_none_doner():
    assert ghs.parse_health_header(None) is None
    assert ghs.parse_health_header("") is None


def test_bozuk_base64_patlamaz():
    assert ghs.parse_health_header("bu-base64-degil!!!") is None


def test_gecersiz_json_patlamaz():
    kotu = base64.urlsafe_b64encode(b"{bozuk json").decode("ascii")
    assert ghs.parse_health_header(kotu) is None


def test_json_ama_dict_degilse_reddedilir():
    """Dizi/sayi gelirse sonraki adimlar `.get()` cagirip patlardi."""
    dizi = base64.urlsafe_b64encode(b'["a","b"]').decode("ascii")
    assert ghs.parse_health_header(dizi) is None


def test_devasa_baslik_HIC_ayristirilmaz():
    """Boyut siniri iki yonlu koruma.

    (1) Kotu niyetli/hatali gateway backend'i mesgul edemesin.
    (2) nginx varsayilan baslik tavani ~8 KB; ona yaklasan bir baslik
        istegin TAMAMINI reddettirir — yani komutlar da gitmez. Sinir
        gercek govdenin (~300 bayt) cok uzerinde ama tavanin cok altinda.
    """
    devasa = "A" * (ghs._MAX_HEADER_BYTES + 1)
    assert ghs.parse_health_header(devasa) is None


def test_utf8_olmayan_bayt_patlamaz():
    kotu = base64.urlsafe_b64encode(b"\xff\xfe\xfd").decode("ascii")
    assert ghs.parse_health_header(kotu) is None


# ----------------------------------------------------- tip zorlamasi


def test_yanlis_tipler_satiri_dusurmez(monkeypatch):
    """Gateway yanlis tip gonderirse alan None olsun, kayit YINE yazilsin.

    Tek bir hatali alan yuzunden tum saglik raporunu atmak, kor noktayi
    kapatmak icin eklenen ozelligi ise yaramaz kilar.
    """
    payload = {
        "status": "ok",
        "outbox_pending": "bu-sayi-degil",
        "devices": "sozluk-degil",
        "uptime_sec": 12.7,
        "version": 123,
    }
    out = ghs.parse_health_header(_kodla(payload))
    assert out is not None
    assert ghs._as_int(out["outbox_pending"]) is None
    assert ghs._as_int(out["uptime_sec"]) == 12
    assert ghs._as_str(out["version"], 40) == "123"


def test_bool_int_olarak_sayilmaz():
    """Python'da bool bir int'tir; `True` -> 1 yazmak sessiz veri bozulmasi."""
    assert ghs._as_int(True) is None
    assert ghs._as_int(False) is None


# ------------------------------------------------------- yazma sikligi


def test_cok_sik_gelen_tekrar_DB_YE_YAZILMAZ(monkeypatch):
    """1 Hz'de yazmak gateway basina gunde 86.400 UPDATE demekti.

    Bu uc zaten sicak (her cagrida gateways.last_seen_at UPDATE'i var);
    saglik yazimini da her saniye eklemek yazma buyumesini ikiye katlardi.
    """
    yazimlar = []

    class _SahteDb:
        def execute(self, stmt):
            yazimlar.append(stmt)

    ghs._last_write.clear()
    db = _SahteDb()

    assert ghs.record_health(db, "GW-TEST", GECERLI) is True
    assert len(yazimlar) == 1
    # Hemen ardindan gelen ikinci rapor suzulmeli
    assert ghs.record_health(db, "GW-TEST", GECERLI) is False
    assert len(yazimlar) == 1, "cok sik gelen tekrar DB'ye yazildi"


def test_db_hatasi_komut_kanalini_dusurmez():
    """DB yazimi patlasa bile record_health istisna SIZDIRMAMALI."""

    class _PatlayanDb:
        def execute(self, stmt):
            raise RuntimeError("DB gitti")

    ghs._last_write.clear()
    assert ghs.record_health(_PatlayanDb(), "GW-PATLAK", GECERLI) is False


def test_farkli_gatewayler_birbirini_engellemez():
    yazimlar = []

    class _SahteDb:
        def execute(self, stmt):
            yazimlar.append(stmt)

    ghs._last_write.clear()
    db = _SahteDb()
    assert ghs.record_health(db, "GW-A", GECERLI) is True
    assert ghs.record_health(db, "GW-B", GECERLI) is True
    assert len(yazimlar) == 2


def test_pending_saglik_yazimini_KENDI_sessioninda_yapar():
    """Sahada yasandi (GW-001): saglik INSERT'i request session'inda
    patlayinca istisna yakalansa da paylasilan transaction "aborted"
    kaliyor, /pending'in kendi commit'i (komut durumu + last_seen) 500
    veriyor ve gateway basligi 10 dakika birakiyordu. Saglik yazimi KENDI
    session'inda olmali ki hicbir hatasi komut kanalina dokunamasin."""
    import inspect
    import re

    from app.api import gateways

    src = inspect.getsource(gateways.get_gateway_pending)
    src = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    src = re.sub(r"^\s*#.*$", "", src, flags=re.MULTILINE)
    i = src.find("record_health(")
    assert i != -1, "saglik kaydi /pending'den kaldirilmis"
    assert "SessionLocal()" in src[:i], (
        "saglik yazimi request session'ini kullaniyor — hata transaction'i "
        "zehirler ve komut kanali 500 verir"
    )
    assert "record_health(db," not in src, (
        "record_health request db'si ile cagriliyor"
    )
