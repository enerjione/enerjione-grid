"""Gateway'i konsoldan durdur/baslat/yeniden baslat — backend sozlesmesi.

BU DOSYANIN KILITLEDIGI SEYLER
------------------------------
1. YETKI: durdurma INSTALLER'a ozel. `require_role` HIYERARSIK DEGIL, tam
   eslesme yapar; operator/engineer/ops_manager kazara veri akisini
   kesememeli. Test rol kontrolunu METIN ARAYARAK degil, ucun kendi
   bagimliligini her rolle CAGIRARAK dogrular.
2. DENETIM: her istek olay kaydina duser. Sahadaki "veri neden gelmiyor"
   sorusunun cevabi orada olmali; durdurma kaydi WARNING seviyesinde ki
   olay listesinde kaybolmasin.
3. PROTOKOL: backend ajana bir KOMUT gondermez, yalnizca sabit bir eylem
   ADI + gateway kodu yazar. Istek govdesinde compose/komut alani olmamali.
4. KURULU DEGILSE: bu uclar yalnizca BU cihazdaki container'i yonetir;
   baska makinedeki gateway icin 409 doner.
"""

from __future__ import annotations

import inspect
import json

import pytest
from fastapi import HTTPException

from app.api import gateways
from app.models.enums import UserRole
from app.services import gateway_agent_service as gas


# ---------------------------------------------------------------------------
# Sahte cevre
# ---------------------------------------------------------------------------

class SahteGateway:
    def __init__(self, code: str = "GW-7", name: str = "Saha A"):
        self.code = code
        self.name = name


class SahteKullanici:
    def __init__(self, rol: UserRole, username: str = "kurulumcu"):
        self.role = rol
        self.username = username


class SahteDb:
    def __init__(self, gateway):
        self._gateway = gateway
        self.commits = 0

    def scalar(self, _stmt):
        return self._gateway

    def commit(self):
        self.commits += 1


@pytest.fixture
def cevre(monkeypatch):
    """Ajan kurulu ve gateway bu cihazda; istekler yakalanir."""
    yakalanan: dict = {}
    olaylar: list[dict] = []

    def _istek_yaz(body: dict) -> str:
        yakalanan.clear()
        yakalanan.update(body)
        return str(body["id"])

    monkeypatch.setattr(gas, "_write_request", _istek_yaz)
    monkeypatch.setattr(gas, "is_installed_locally", lambda code: True)
    monkeypatch.setattr(
        gateways.gateway_agent_service, "is_installed_locally", lambda code: True
    )
    monkeypatch.setattr(
        gateways, "record_event", lambda db, **kw: olaylar.append(kw)
    )

    class Cevre:
        istek = yakalanan
        events = olaylar

    return Cevre


UCLAR = {
    "stop": gateways.stop_gateway_locally,
    "start": gateways.start_gateway_locally,
    "restart": gateways.restart_gateway_locally,
}


# ---------------------------------------------------------------------------
# YETKI
# ---------------------------------------------------------------------------

def _rol_kapisi(uc):
    """Ucun `current_user` bagimliligini (require_role kapanisini) dondurur."""
    varsayilan = inspect.signature(uc).parameters["current_user"].default
    return varsayilan.dependency


@pytest.mark.parametrize("ad", sorted(UCLAR))
def test_yalnizca_INSTALLER_gecebiliyor(ad):
    """Operator kazara veri akisini kesmemeli — ve engineer de kesmemeli:
    `require_role` hiyerarsik degildir, tam eslesme arar."""
    kapi = _rol_kapisi(UCLAR[ad])
    kurulumcu = SahteKullanici(UserRole.INSTALLER)
    assert kapi(kurulumcu) is kurulumcu

    for rol in (UserRole.OPERATOR, UserRole.ENGINEER, UserRole.OPS_MANAGER):
        with pytest.raises(HTTPException) as hata:
            kapi(SahteKullanici(rol))
        assert hata.value.status_code == 403


# ---------------------------------------------------------------------------
# PROTOKOL: sabit eylem adi, komut YOK
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ad", sorted(UCLAR))
def test_ajana_yalnizca_EYLEM_ADI_gidiyor(cevre, ad):
    db = SahteDb(SahteGateway())
    UCLAR[ad]("GW-7", SahteKullanici(UserRole.INSTALLER), db)

    istek = cevre.istek
    assert istek["action"] == ad
    assert istek["code"] == "GW-7"
    assert istek["requested_by"] == "kurulumcu"
    # Ajan root kosuyor: calistirilabilir hicbir sey gondermiyoruz.
    assert set(istek) == {"id", "action", "code", "created_at", "requested_by"}


def test_durdurma_istegi_JSON_serilestirilebilir(cevre):
    """Istek diske yazilacak; serilestirilemeyen bir alan sessizce kaybolmasin."""
    db = SahteDb(SahteGateway())
    gateways.stop_gateway_locally("GW-7", SahteKullanici(UserRole.INSTALLER), db)
    assert json.loads(json.dumps(cevre.istek))["action"] == "stop"


# ---------------------------------------------------------------------------
# DENETIM
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ad,event_type",
    [
        ("stop", "gateway_local_stop_requested"),
        ("start", "gateway_local_start_requested"),
        ("restart", "gateway_local_restart_requested"),
    ],
)
def test_her_islem_OLAY_KAYDINA_dusuyor(cevre, ad, event_type):
    db = SahteDb(SahteGateway())
    UCLAR[ad]("GW-7", SahteKullanici(UserRole.INSTALLER), db)

    assert len(cevre.events) == 1
    olay = cevre.events[0]
    assert olay["event_type"] == event_type
    assert olay["category"] == "gateway"
    assert olay["actor_username"] == "kurulumcu"
    assert olay["metadata"]["gateway_code"] == "GW-7"
    # Istek kimligi kaydedilmeli: ajanin sonucu ile eslestirilebilsin.
    assert olay["metadata"]["request_id"]
    assert db.commits == 1, "olay kaydi commit edilmedi"


def test_durdurma_UYARI_seviyesinde(cevre):
    """Veri akisini kesen bir islem 'info' yiginin icinde kaybolmamali."""
    db = SahteDb(SahteGateway())
    gateways.stop_gateway_locally("GW-7", SahteKullanici(UserRole.INSTALLER), db)
    assert cevre.events[0]["severity"] == "warning"


def test_olay_mesaji_SONUCU_soyluyor(cevre):
    db = SahteDb(SahteGateway())
    gateways.stop_gateway_locally("GW-7", SahteKullanici(UserRole.INSTALLER), db)
    mesaj = cevre.events[0]["message"]
    assert "Saha A" in mesaj and "GW-7" in mesaj
    assert "veri akisi" in mesaj.lower()


# ---------------------------------------------------------------------------
# HATA YOLLARI
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ad", sorted(UCLAR))
def test_bilinmeyen_gateway_404(cevre, ad):
    db = SahteDb(None)
    with pytest.raises(HTTPException) as hata:
        UCLAR[ad]("YOK", SahteKullanici(UserRole.INSTALLER), db)
    assert hata.value.status_code == 404
    assert cevre.events == []


@pytest.mark.parametrize("ad", sorted(UCLAR))
def test_bu_cihazda_kurulu_degilse_409(cevre, ad, monkeypatch):
    """Gateway baska makinede kosuyor olabilir; oradaki container'a buradan
    dokunulamaz. Sessizce 'basarili' demek yanlis guven yaratirdi."""
    monkeypatch.setattr(
        gateways.gateway_agent_service, "is_installed_locally", lambda code: False
    )
    db = SahteDb(SahteGateway())
    with pytest.raises(HTTPException) as hata:
        UCLAR[ad]("GW-7", SahteKullanici(UserRole.INSTALLER), db)
    assert hata.value.status_code == 409
    assert cevre.events == []


def test_ajan_kabul_etmezse_olay_YAZILMIYOR(cevre, monkeypatch):
    """Onceki istek hala uygulaniyorsa (409) denetim kaydi olusmamali —
    yapilmamis bir durdurma kayitta gorunmesin."""
    def _patla(code, actor):
        raise gas.GatewayAgentError("request_pending")

    monkeypatch.setattr(gas, "request_stop", _patla)
    db = SahteDb(SahteGateway())
    with pytest.raises(HTTPException) as hata:
        gateways.stop_gateway_locally("GW-7", SahteKullanici(UserRole.INSTALLER), db)
    assert hata.value.status_code == 409
    assert cevre.events == []
    assert db.commits == 0


# ---------------------------------------------------------------------------
# SERVIS: istek dosyasi gercekten yaziliyor mu
# ---------------------------------------------------------------------------

def test_istek_dosyasi_diske_yaziliyor(tmp_path, monkeypatch):
    monkeypatch.setattr(gas.settings, "gateway_state_dir", str(tmp_path))
    (tmp_path / gas.STATE_FILE).write_text("{}", encoding="utf-8")

    request_id = gas.request_stop("GW-7", "kurulumcu")

    govde = json.loads((tmp_path / gas.REQUEST_FILE).read_text(encoding="utf-8"))
    assert govde["id"] == request_id
    assert govde["action"] == "stop"
    assert govde["code"] == "GW-7"


def test_ust_uste_istek_REDDEDILIYOR(tmp_path, monkeypatch):
    """Onceki istek uygulanmadan ikincisi gelirse hangisinin gecerli oldugu
    belirsizlesir."""
    monkeypatch.setattr(gas.settings, "gateway_state_dir", str(tmp_path))
    (tmp_path / gas.STATE_FILE).write_text("{}", encoding="utf-8")

    gas.request_stop("GW-7", "kurulumcu")
    with pytest.raises(gas.GatewayAgentError):
        gas.request_start("GW-7", "kurulumcu")


# ---------------------------------------------------------------------------
# SEMA / SOZLESME SENKRONU
# ---------------------------------------------------------------------------

def test_ajan_bu_eylemleri_taniyor():
    """Backend'in gonderdigi ad ajanin kumesinde YOKSA istek diske yazilir ve
    sessizce reddedilir — iki taraf birlikte degismeli."""
    import importlib.util
    from pathlib import Path

    yol = Path(__file__).resolve().parents[3] / "infra" / "appliance" / "e1-gwd.py"
    spec = importlib.util.spec_from_file_location("e1_gwd_backend_ut", yol)
    assert spec is not None and spec.loader is not None
    ajan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ajan)

    for eylem in ("stop", "start", "restart"):
        assert eylem in ajan.ALLOWED_ACTIONS


@pytest.mark.parametrize("dil", ["tr", "en"])
def test_ceviriler_TAM(dil: str):
    from pathlib import Path

    fe = Path(__file__).resolve().parents[3] / "apps" / "frontend-web"
    d = json.loads(
        (fe / "src/shared/i18n/resources" / f"{dil}.json").read_text(encoding="utf-8")
    )

    canli = d["engineering"]["gatewayLive"]
    for anahtar in ("online", "stopped", "unreachable", "unknown"):
        assert canli.get(anahtar), f"{dil}: gatewayLive.{anahtar} yok"
    # Durduruldu ile ulasilamiyor AYNI metni tasimamali.
    assert canli["stopped"] != canli["unreachable"]

    yasam = d["engineering"]["gateways"]["lifecycle"]
    for anahtar in ("start", "stop", "restart", "stopConfirm", "restartConfirm"):
        assert yasam.get(anahtar), f"{dil}: lifecycle.{anahtar} yok"
    for asama in ("gonderiliyor", "uygulaniyor", "stop", "start", "restart",
                  "bitti", "zaman_asimi"):
        assert yasam["progress"].get(asama), f"{dil}: progress.{asama} yok"

    olaylar = d["events"]["message"]
    for anahtar in (
        "gateway_local_stop_requested",
        "gateway_local_start_requested",
        "gateway_local_restart_requested",
    ):
        assert olaylar.get(anahtar), f"{dil}: events.message.{anahtar} yok"


def test_durdurma_onayi_SONUCU_yaziyor():
    """Operator ne olacagini bilerek onaylamali: 'veri akisi duracak'."""
    from pathlib import Path

    fe = Path(__file__).resolve().parents[3] / "apps" / "frontend-web"
    tr = json.loads(
        (fe / "src/shared/i18n/resources/tr.json").read_text(encoding="utf-8")
    )
    en = json.loads(
        (fe / "src/shared/i18n/resources/en.json").read_text(encoding="utf-8")
    )
    # Buyuk/kucuk harf donusumu yapilmiyor: Turkce noktali I bu karsilastirmayi
    # sessizce bozar. Metin zaten vurgu icin buyuk harfle yaziliyor.
    tr_metin = tr["engineering"]["gateways"]["lifecycle"]["stopConfirm"]
    en_metin = en["engineering"]["gateways"]["lifecycle"]["stopConfirm"]
    assert "VERİ AKIŞI DURACAK" in tr_metin, "onay metni sonucu soylemiyor"
    assert "DATA FLOW WILL STOP" in en_metin
