"""Gateway susarsa cihazlar YESIL kalmamali — ama VERI SUSMASI kopma DEGIL.

YASANAN (2026-08-01, saha test cihazi)
--------------------------------------
Bir cihaz 1 saat 48 dakika tek telemetri gondermedigi halde ONLINE
gorunuyordu. Ilk teshis "cihaz kopmus, backend fark etmiyor" idi ve cihaz
bazli bir veri-yasi bekcisi yazildi.

TESHIS YANLISTI. Cihaz gayet ayaktaydi; yalnizca DEGERLERI DEGISMIYORDU ve
gateway yalnizca degisenleri yayinliyor. Veri yasina bakan bekci onu OFFLINE
isaretleyecekti — duragan bir fiderde YANLIS ALARM.

"Veri gelmiyor" ile "haberlesme yok" AYNI SEY DEGIL. Bu ayrimi yapabilecek
tek katman gateway'dir (DNP3 baglantisi onda):
  * link olurse `comm_lost` yayinlar  -> cihaz zaten OFFLINE olur,
  * link acik ama veri eskiyse bunu AYRICA raporlar.

Yani cihaz seviyesi zaten dogru calisiyor. Kapanmayan tek delik gateway'in
KENDISININ susmasi. Bu testler hem o deligin kapandigini hem de yanlis
tasarima geri donulmedigini dogruluyor.
"""

from __future__ import annotations

import inspect
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.models.enums import CommunicationStatus
from app.services import gateway_staleness_watchdog as wd


def _kod(kaynak: str) -> str:
    kaynak = re.sub(r'""".*?"""', "", kaynak, flags=re.DOTALL)
    return re.sub(r"^\s*#.*$", "", kaynak, flags=re.MULTILINE)


# ---------------------------------------------------------------------------
# Yanlis tasarima donulmedi
# ---------------------------------------------------------------------------

def test_CIHAZ_veri_yasina_BAKMIYOR():
    """Duragan bir fiderde yanlis alarm uretirdi."""
    kod = _kod(inspect.getsource(wd.sweep_once))
    assert "Device.last_update_at" not in kod, (
        "bekci cihazin veri yasina bakiyor — degerleri degismeyen saglikli "
        "bir cihaz OFFLINE isaretlenir (yanlis alarm)"
    )


def test_GATEWAY_son_gorulme_zamanina_bakiyor():
    kod = _kod(inspect.getsource(wd.sweep_once))
    assert "Gateway.last_seen_at" in kod, (
        "bekci gateway'in kendi kalp atisina bakmiyor"
    )


def test_sorgu_BAYATLIK_karsilastirmasi_iceriyor():
    """Sahte DB sorgunun WHERE'ini calistirmaz; kosulun VARLIGINI kaynakta
    dogruluyoruz.

    Ilk yazimda bu test yoktu ve kosulu `is_not(None)` ile degistiren bir
    mutasyon KACTI — o halde TUM gateway'ler bayat sayilirdi. Mutasyon
    testi yakaladi."""
    kod = _kod(inspect.getsource(wd.sweep_once))
    assert "Gateway.last_seen_at < sinir" in kod, (
        "sorguda bayatlik karsilastirmasi yok — tum gateway'ler bayat sayilir"
    )
    assert "timedelta(seconds=stale_after_sec())" in kod, (
        "sinir esikten turetilmiyor"
    )


def test_cihaz_bazli_bekci_MODULU_YOK():
    """Yanlis tasarim silindi; geri gelirse burada durur."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.services.device_staleness_watchdog")


# ---------------------------------------------------------------------------
# Durum secimi
# ---------------------------------------------------------------------------

def test_UNKNOWN_isaretleniyor_OFFLINE_degil():
    """Gateway sustugunda cihazlarin gercekte ne durumda oldugunu BILMIYORUZ.
    'Cevrimdisi' demek veriye dayanmayan bir iddia olurdu."""
    kod = _kod(inspect.getsource(wd.sweep_once))
    assert "CommunicationStatus.UNKNOWN" in kod
    assert "communication_status=CommunicationStatus.OFFLINE" not in kod, (
        "gateway sustugunda cihazlar OFFLINE isaretleniyor — bu, veriye "
        "dayanmayan bir iddia"
    )


def test_yalnizca_ONLINE_olanlar_degistiriliyor():
    """Zaten OFFLINE olan bir cihazi UNKNOWN yapmak bilgi KAYBI olurdu:
    'kopuk oldugunu biliyoruz'dan 'bilmiyoruz'a gerilerdik."""
    kod = _kod(inspect.getsource(wd.sweep_once))
    assert "Device.communication_status == CommunicationStatus.ONLINE" in kod, (
        "bekci OFFLINE cihazlari da UNKNOWN yapiyor — bilgi kaybi"
    )


def test_UNKNOWN_enumda_var():
    assert CommunicationStatus.UNKNOWN.value == "unknown"


# ---------------------------------------------------------------------------
# Esik
# ---------------------------------------------------------------------------

def test_esik_config_poll_periyodunun_USTUNDE(monkeypatch: pytest.MonkeyPatch):
    """Gateway config-poll'u ~30 sn. Esik ona yakinsa tek bir gecikmis tur
    tum sahayi bilinmez gosterirdi."""
    monkeypatch.delenv("GATEWAY_STALE_AFTER_SEC", raising=False)
    assert wd.stale_after_sec() >= 90.0


def test_esik_ALT_SINIRIN_altina_inemiyor(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GATEWAY_STALE_AFTER_SEC", "5")
    assert wd.stale_after_sec() == 90.0


def test_esik_env_ile_YUKSELTILEBILIR(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GATEWAY_STALE_AFTER_SEC", "600")
    assert wd.stale_after_sec() == 600.0


def test_bozuk_env_COKERTMEZ(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GATEWAY_STALE_AFTER_SEC", "abc")
    assert wd.stale_after_sec() == wd.DEFAULT_STALE_AFTER_SEC


# ---------------------------------------------------------------------------
# Davranis
# ---------------------------------------------------------------------------

class _SahteSonuc:
    def __init__(self, rowcount): self.rowcount = rowcount


class _SahteDB:
    def __init__(self, gatewayler, etkilenen=0):
        self.gatewayler = gatewayler
        self.etkilenen = etkilenen
        self.commit_sayisi = 0
        self.update_sayisi = 0

    def scalars(self, _stmt): return self
    def all(self): return self.gatewayler
    def execute(self, _stmt):
        self.update_sayisi += 1
        return _SahteSonuc(self.etkilenen)
    def add(self, _obj): pass
    def flush(self): pass
    def commit(self): self.commit_sayisi += 1


class _SahteGW:
    def __init__(self, code="GW-001", yas_sn=600):
        self.code = code
        self.is_active = True
        self.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=yas_sn)


def test_bayat_gateway_YOKSA_hicbir_sey_yapmiyor():
    db = _SahteDB([])
    assert wd.sweep_once(db) == 0
    assert db.commit_sayisi == 0
    assert db.update_sayisi == 0


def test_bayat_gateway_VARSA_cihazlar_isaretleniyor(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(wd, "record_event", lambda *a, **k: None)
    db = _SahteDB([_SahteGW()], etkilenen=7)
    assert wd.sweep_once(db) == 7
    assert db.commit_sayisi == 1


def test_etkilenen_cihaz_YOKSA_commit_yok(monkeypatch: pytest.MonkeyPatch):
    """Gateway bayat ama tum cihazlari ZATEN OFFLINE — bos commit atmayalim."""
    monkeypatch.setattr(wd, "record_event", lambda *a, **k: None)
    db = _SahteDB([_SahteGW()], etkilenen=0)
    assert wd.sweep_once(db) == 0
    assert db.commit_sayisi == 0


def test_naive_last_seen_COKERTMEZ(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(wd, "record_event", lambda *a, **k: None)
    gw = _SahteGW()
    gw.last_seen_at = gw.last_seen_at.replace(tzinfo=None)
    db = _SahteDB([gw], etkilenen=2)
    assert wd.sweep_once(db) == 2


def test_denetim_kaydi_BIRAKIYOR(monkeypatch: pytest.MonkeyPatch):
    """Gercek bir operasyonel olay: gateway sustu. Nadir oldugu icin gurultu
    yaratmaz — cihazlar UNKNOWN kaldigi surece tekrar isaretlenmez."""
    cagrilar = []
    monkeypatch.setattr(wd, "record_event", lambda *a, **k: cagrilar.append(k))
    db = _SahteDB([_SahteGW()], etkilenen=3)
    wd.sweep_once(db)
    assert cagrilar and cagrilar[0].get("event_type") == "gateway_stale_devices_unknown"


def test_worker_MODUL_DUZEYINDE_kayitli():
    """Kaydedilmemis bir worker hic calismaz.

    AST ile bakiyoruz, metinle DEGIL. Ilk yazimda metin ariyordum ve iki
    ayri zaafi vardi:
      * kontrol `or` ile gevsetilmisti, ikinci dal her zaman dogruydu,
      * kaydi `if False:` icine alan bir mutasyon yine de gecerdi, cunku ad
        metinde duruyordu.
    Mutasyon testi yakaladi. AST'de kosul icine alinmis bir cagri modul
    duzeyinde GORUNMEZ.
    """
    import ast
    import pathlib

    yol = pathlib.Path(inspect.getfile(wd)).parents[1] / "main.py"
    agac = ast.parse(yol.read_text(encoding="utf-8"))

    def _kayit_mi(dugum) -> bool:
        if not isinstance(dugum, ast.Expr) or not isinstance(dugum.value, ast.Call):
            return False
        cagri = dugum.value
        if getattr(cagri.func, "attr", None) != "register" or not cagri.args:
            return False
        ilk = cagri.args[0]
        return isinstance(ilk, ast.Constant) and ilk.value == "gateway_staleness"

    assert any(_kayit_mi(d) for d in agac.body), (
        "gateway_staleness worker'i modul duzeyinde kayitli degil — bir "
        "kosulun icine alinmis olabilir ve hic calismaz"
    )


# ---------------------------------------------------------------------------
# Cihaz bazinda link durumu — veri akisindan BAGIMSIZ tek dogru kaynak
#
# Ariza bekleyen bir gosterge saatlerce sessiz kalabilir: deger degismezse
# gateway hicbir sey yayinlamaz. Telemetri yasina bakan HICBIR kontrol
# "sessiz" ile "olu"yu ayirt edemez; gateway ayirt eder cunku DNP3 baglantisi
# onda.
#
# Aktif yoklama (backend'den cihaza TCP) BILEREK yapilmiyor: outstation
# `CloseExisting` modunda, yeni baglanti gelince mevcut olani kapatiyor —
# yoklama gateway'in oturumunu dusururdu. Sahada 2.172 baglanti kopmasina yol
# acan dongu tam olarak buydu.
# ---------------------------------------------------------------------------

def test_link_durumu_ayristirma():
    import json as _json

    from app.services.gateway_health_service import device_link_states

    ham = _json.dumps({"devices": {"states": {
        "d6": "lost", "d11": "connected", "d3": "recovering", "d9": "uydurma",
    }}})
    sonuc = device_link_states(ham)
    assert sonuc == {"d6": "offline", "d11": "online"}


def test_RECOVERING_disarida_birakiliyor():
    """Gecis durumu. Eslersek cihaz iki katman arasinda gidip gelir."""
    import json as _json

    from app.services.gateway_health_service import device_link_states

    assert device_link_states(_json.dumps({"devices": {"states": {"d1": "recovering"}}})) == {}


def test_BOZUK_govde_cokertmiyor():
    """Veri gateway'den geliyor; bozuk govde cihaz durumlarini bozmamali."""
    from app.services.gateway_health_service import device_link_states

    for ham in (None, "", "{bozuk", "[]", '{"devices": "metin"}', '{"devices":{"states":5}}'):
        assert device_link_states(ham) == {}


def test_gateway_GONDERMIYORSA_hicbir_sey_yapilmiyor():
    """Ileriye donuk uyumluluk: uretici taraf hazir olana kadar sessiz."""
    import json as _json

    from app.services.gateway_health_service import device_link_states

    # Bugun gateway'in gonderdigi bicim — `states` yok.
    ham = _json.dumps({"devices": {"total": 18, "online": 16, "lost": 2}})
    assert device_link_states(ham) == {}


def test_KESIN_bilgi_bayatlik_tahmininden_ONCE_uygulaniyor():
    """Ters sirada olsaydi bayatlik kontrolu, gateway'in az once
    dogruladigi bir cihazi UNKNOWN yapabilirdi."""
    kod = _kod(inspect.getsource(wd.GatewayStalenessWatchdog._run))
    i_link = kod.find("apply_link_states(")
    i_sweep = kod.find("sweep_once(")
    assert i_link != -1 and i_sweep != -1
    assert i_link < i_sweep, "bayatlik tahmini kesin bilgiden ONCE calisiyor"


def test_link_durumu_GATEWAY_ile_eslestiriliyor():
    """Yalnizca kod eslesmesi yapilsaydi, farkli gateway'lerdeki ayni kodlu
    cihazlar birbirinin durumunu ezerdi."""
    kod = _kod(inspect.getsource(wd.apply_link_states))
    assert "Device.gateway_code == satir.gateway_code" in kod


def test_link_durumu_DEGISMEYENI_yazmiyor():
    """Her turda ayni degeri yazmak, 600 cihazda gereksiz UPDATE yagmuru."""
    kod = _kod(inspect.getsource(wd.apply_link_states))
    assert "Device.communication_status != hedef" in kod


# ---------------------------------------------------------------------------
# Sayi bazli guvenli cikarim (2026-08-04): cihaz haritasi basliga sigmiyorsa
# (400+ cihaz) gateway yalnizca SAYILARI gonderir. "Tum cihazlar koptu"
# derken cihazlarin ONLINE kalmasi sahada iki kez yasandi (kuyruk purge/tikali
# iken comm_lost event'leri kayboldu). Bu cikarim telemetri kuyrugundan
# BAGIMSIZ calisir.
# ---------------------------------------------------------------------------

def test_sayi_bazli_cikarim_TUM_kopmada_offline_yapiyor():
    import inspect

    from app.services import gateway_staleness_watchdog as wd

    kod = inspect.getsource(wd.apply_link_states)
    # Harita yokken sayilara bakilir; yalnizca "hepsi koptu" durumunda ve
    # yalnizca ONLINE olanlar OFFLINE'a cekilir.
    assert "devices_lost" in kod and "devices_online" in kod, (
        "sayi bazli cikarim kaldirilmis — kuyruk purge'unda cihazlar ONLINE "
        "takili kalir (sahada yasandi)"
    )
    assert "kayip > 0 and online == 0" in kod, (
        "cikarim yalnizca TUM kopmada uygulanmali — kismi kopmada hangi "
        "cihazin koptugu sayidan bilinemez"
    )


def test_filo_alarmi_VAR_OLMAYAN_kolona_bakmiyor():
    """User.is_active diye bir kolon YOK; onceki surum her turda
    AttributeError firlatiyordu ve filo alarmi hic calismadi."""
    import inspect

    from app.services import gateway_fleet_alarm as fa

    kod = inspect.getsource(fa._hedef_kullanicilar)
    assert "User.is_active" not in kod, (
        "User.is_active referansi geri gelmis — model bu kolonu tasimiyor, "
        "watchdog dongusu her turda patlar"
    )
