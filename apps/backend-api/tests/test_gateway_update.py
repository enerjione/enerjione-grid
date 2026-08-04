"""Gateway guncelleme: surum tespiti, buton, bildirim.

SURUM NASIL TESPIT EDILIYOR
---------------------------
Gateway imaji `:latest` etiketine sabit — karsilastirilacak surum numarasi
YOK. Bu yuzden DIGEST karsilastirmasi kullaniliyor: etiketin isaret ettigi
manifest digest'i degistiyse yeni imaj yayinlanmis demektir.

TUZAK — CIHAZDA DOGRULANDI
`docker manifest inspect -v` cok mimarili bir imajda ALT PLATFORM
digest'lerini dondurur (linux/amd64 + attestation). Onu yerel `RepoDigests`
ile karsilastirmak FARKLI SEVIYELERI karsilastirmak olur ve HER ZAMAN
farkli cikar — sistem surekli "guncelleme var" der, tum kullanicilara bos
bildirim yagar ve bildirim merkezi kullanilamaz hale gelirdi.

Dogrusu `docker buildx imagetools inspect --format {{.Manifest.Digest}}`:
ust seviye liste digest'ini verir ve `RepoDigests` ile BIREBIR eslesir
(saha cihazinda dogrulandi: ikisi de sha256:1d05c3a4...).

UC DURUM
`update_available` bool DEGIL, uc durumlu: `None` = BILINMIYOR (kayit
defterine ulasilamadi). `False` ile ayni saymak, sormadan "guncel" demek
olurdu.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

AJAN = Path(__file__).resolve().parents[3] / "infra" / "appliance" / "e1-gwd.py"
FE = Path(__file__).resolve().parents[3] / "apps" / "frontend-web"


def _kod(kaynak: str) -> str:
    kaynak = re.sub(r'""".*?"""', "", kaynak, flags=re.DOTALL)
    return re.sub(r"^\s*#.*$", "", kaynak, flags=re.MULTILINE)


def _ajan() -> str:
    return _kod(AJAN.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Ajan: digest tespiti
# ---------------------------------------------------------------------------

def test_uzak_digest_BUILDX_ile_aliniyor():
    kod = _ajan()
    assert "buildx" in kod and "imagetools" in kod
    assert "{{.Manifest.Digest}}" in kod, "ust seviye liste digest'i istenmiyor"


def test_manifest_inspect_KULLANILMIYOR():
    """Alt platform digest'i dondurur; yerel RepoDigests ile karsilastirmak
    HER ZAMAN farkli cikar ve surekli yanlis 'guncelleme var' uretir."""
    kod = _ajan()
    assert '"manifest"' not in kod, (
        "manifest inspect kullaniliyor — farkli seviyede digest dondurur"
    )


def test_yerel_digest_REPODIGESTS_ten_aliniyor():
    assert "RepoDigests" in _ajan()


def test_uzak_digest_ONBELLEKLENIYOR():
    """Her rapor turunda kayit defterine gitmenin karsiligi yok; yeni imaj
    dakikalar icinde degil gunler icinde cikiyor."""
    kod = _ajan()
    assert "_REMOTE_DIGEST_TTL_SEC" in kod
    assert "_remote_digest_cache" in kod


def test_BASARISIZ_sorgu_da_onbellekleniyor():
    """Ag yoksa her turda zaman asimi beklemeyelim."""
    kod = _ajan()
    i = kod.index("def _remote_digest(")
    govde = kod[i:kod.index("def _container_info(", i)]
    assert "_remote_digest_cache[image] = (deger, simdi)" in govde


def test_update_available_UC_DURUMLU():
    """None = bilinmiyor. False ile ayni saymak, sormadan 'guncel' demek."""
    assert "None if (not yerel or not uzak) else (yerel != uzak)" in _ajan(), (
        "update_available uc durumlu degil — kayit defterine ulasilamadiginda "
        "'guncel' gosterilir"
    )


# ---------------------------------------------------------------------------
# Ajan: update eylemi
# ---------------------------------------------------------------------------

def test_update_eylemi_TANINIYOR():
    assert '"install", "remove", "restart", "update"' in _ajan()


def _update_govdesi() -> str:
    kod = _ajan()
    i = kod.index("def _do_update(")
    return kod[i:kod.index("def cmd_apply(", i)]


def test_update_ONCE_pull_sonra_up():
    govde = _update_govdesi()
    assert govde.index('"pull"') < govde.index('"up"'), (
        "pull'dan once up yapiliyor — eski imajla baslar"
    )


def test_pull_BASARISIZSA_containera_dokunulmuyor():
    """Yarim guncelleme yerine calisan eski surumde kalmak dogru."""
    govde = _update_govdesi()
    assert govde.index('"stage": "pull"') < govde.index('"up"'), (
        "pull hatasinda up yine de calisiyor"
    )


def test_update_sonrasi_ONBELLEK_dusuruluyor():
    """Aksi halde guncelleme sonrasi rapor 15 dakika ESKI sonucu gosterirdi."""
    assert "_remote_digest_cache.clear()" in _update_govdesi()


def test_ajan_dispatchinde_update_var():
    assert 'elif req["action"] == "update":' in _ajan()


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

def test_sema_uc_durumlu_alan_iceriyor():
    from app.schemas.gateway_agent import LocalGateway

    g = LocalGateway(code="GW-001")
    assert g.update_available is None, "varsayilan 'bilinmiyor' olmali"
    assert "image_digest" in LocalGateway.model_fields
    assert "remote_digest" in LocalGateway.model_fields


def test_update_istegi_servisi_var():
    from app.services import gateway_agent_service

    assert hasattr(gateway_agent_service, "request_update")
    assert '"update"' in _kod(inspect.getsource(gateway_agent_service.request_update))


def test_uc_nokta_INSTALLER_a_ozel_ve_denetimli():
    """Panelden tetiklenen her yeniden baslatma sahada gorunur bir kesinti."""
    from app.api import gateways

    ham = inspect.getsource(gateways.update_gateway_locally)
    assert "UserRole.INSTALLER" in ham
    assert "record_event(" in _kod(ham), "guncelleme istegi denetim kaydi birakmiyor"


def test_kurulu_DEGILSE_reddediliyor():
    from app.api import gateways

    assert "is_installed_locally" in _kod(inspect.getsource(gateways.update_gateway_locally))


# ---------------------------------------------------------------------------
# NATS-direkt standart: guncelleme compose'u guncel NATS URL'i ile tazeler.
# Telemetrinin standart rotasi gateway -> NATS JetStream; HTTP ingest yedek
# yoldur. NATS oncesi kurulan gateway'ler guncellemede NATS'a gecmeli, HTTP
# fallback'inde takili kalmamali (her olcum outbox uzerinden backend'e yuk
# bindirir). Ajan tarafinin davranis testleri:
#   infra/appliance/tests/test_update_nats_standart.py
# ---------------------------------------------------------------------------

def test_ajan_update_params_taniyor():
    kod = _ajan()
    assert "UPDATE_PARAM_KEYS" in kod, "ajan update'te params kabul etmiyor"
    assert "_params_from_compose" in kod, (
        "ajan mevcut compose degerlerini geri kazanmiyor — update kurulum "
        "seceneklerini sessizce varsayilana dondururdu"
    )


def test_update_istegi_nats_url_tasiyor(monkeypatch):
    from app.services import gateway_agent_service as s

    yakalanan: dict = {}

    def _yakala(body: dict) -> str:
        yakalanan.update(body)
        return body["id"]

    monkeypatch.setattr(s, "_write_request", _yakala)
    s.request_update("GW-001", "installer", nats_url="nats://gateway:pw@h:4222")
    assert yakalanan["params"] == {"nats_url": "nats://gateway:pw@h:4222"}


def test_update_istegi_nats_url_yoksa_params_gondermiyor(monkeypatch):
    """Eski davranis korunur: URL turetilemediyse (parola bos) compose'a
    dokunulmaz, yalnizca imaj cekilir."""
    from app.services import gateway_agent_service as s

    yakalanan: dict = {}

    def _yakala(body: dict) -> str:
        yakalanan.update(body)
        return body["id"]

    monkeypatch.setattr(s, "_write_request", _yakala)
    s.request_update("GW-001", "installer")
    assert "params" not in yakalanan
    yakalanan.clear()
    s.request_update("GW-001", "installer", nats_url="   ")
    assert "params" not in yakalanan


def test_endpoint_standart_nats_url_turetiyor():
    from app.api import gateways

    ham = _kod(inspect.getsource(gateways.update_gateway_locally))
    assert "derive_nats_url" in ham, (
        "local-update standart NATS URL'i gondermiyor — eski kurulumlar "
        "HTTP fallback'inde kalir"
    )
    # Anonim URL NATS deny-all'a takilir; parola bos ise (dev/test) calisan
    # gateway'i bozmak yerine compose korunmali.
    assert "nats_gateway_password.strip()" in ham


def test_http_ingest_fallback_uyariyor():
    """HTTP'den telemetri basan gateway gorunmez kalmamali — standart disi
    calistiginin tek isareti bu uyari."""
    from app.services import ingest_service

    assert "_warn_http_fallback" in _kod(inspect.getsource(ingest_service.ingest_gateway_batch))
    assert "_warn_http_fallback" in _kod(inspect.getsource(ingest_service.ingest_gateway_legacy))


def test_http_fallback_uyarisi_rate_limitli(caplog):
    """Gateway saniyede birkac batch basar; her batch'te WARNING log'u
    dosyayi doldururdu."""
    import logging

    from app.services import ingest_service as i

    with caplog.at_level(logging.WARNING, logger="app.services.ingest_service"):
        i._warn_http_fallback("GW-TEST-RL", 10)
        i._warn_http_fallback("GW-TEST-RL", 10)
    kayitlar = [
        r for r in caplog.records
        if "gateway_http_fallback_ingest" in r.getMessage() and "GW-TEST-RL" in r.getMessage()
    ]
    assert len(kayitlar) == 1


# ---------------------------------------------------------------------------
# Bildirim
# ---------------------------------------------------------------------------

def test_bildirim_YAYIN_olarak_gidiyor():
    from app.services import gateway_update_notifier as n

    assert "recipient_username=None" in _kod(inspect.getsource(n.check_once)), (
        "bildirim tek kullaniciya gidiyor"
    )


def test_BILINMIYOR_durumu_bildirim_uretmiyor():
    """Yalnizca KESIN 'yeni surum var' bilgisi bildirime donusmeli."""
    from app.services import gateway_update_notifier as n

    assert "gw.update_available is not True" in _kod(inspect.getsource(n.check_once)), (
        "None (bilinmiyor) durumu da bildirim uretiyor"
    )


def test_bildirim_BIR_KEZ_gonderiliyor():
    """Kontrol periyodik; her turda bildirim gondermek bildirim merkezini
    kullanilamaz hale getirirdi."""
    from app.services import gateway_update_notifier as n

    assert "_already_notified(" in _kod(inspect.getsource(n.check_once))


def test_isaret_SUREC_DISINDA_tutuluyor():
    """Surec ici bir degisken restart'ta sifirlanir ve backend her yeniden
    baslatildiginda ayni bildirim tekrar giderdi."""
    from app.services import gateway_update_notifier as n

    assert "SystemEvent" in _kod(inspect.getsource(n._already_notified))


def test_isaret_DIGEST_bazinda():
    """Gateway bazinda olsaydi bir sonraki surum icin bildirim HIC gitmezdi."""
    from app.services import gateway_update_notifier as n

    assert "digest" in _kod(inspect.getsource(n._already_notified))


def test_worker_MODUL_DUZEYINDE_kayitli():
    import ast

    from app.services import gateway_update_notifier as n

    yol = Path(inspect.getfile(n)).parents[1] / "main.py"
    agac = ast.parse(yol.read_text(encoding="utf-8"))

    def _kayit_mi(d) -> bool:
        if not isinstance(d, ast.Expr) or not isinstance(d.value, ast.Call):
            return False
        c = d.value
        if getattr(c.func, "attr", None) != "register" or not c.args:
            return False
        return isinstance(c.args[0], ast.Constant) and c.args[0].value == "gateway_update_notifier"

    assert any(_kayit_mi(d) for d in agac.body), "notifier kayitli degil"


# ---------------------------------------------------------------------------
# Arayuz
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dil", ["tr", "en"])
def test_ceviriler_TAM(dil: str):
    import json

    d = json.loads(
        (FE / "src/shared/i18n/resources" / f"{dil}.json").read_text(encoding="utf-8")
    )
    form = d["engineering"]["gateways"]["editForm"]
    for anahtar in (
        "updateAvailable", "updateNow", "upToDate",
        "versionUnknown", "versionUnknownHint", "updateConfirm",
    ):
        assert form.get(anahtar), f"{dil}: {anahtar} yok"


def _modal() -> str:
    ham = (FE / "src/features/gateways/GatewayEditModal.tsx").read_text(encoding="utf-8")
    return re.sub(r"^\s*//.*$", "", ham, flags=re.MULTILINE)


def test_arayuz_KESINTIYI_soruyor():
    """Gateway yeniden baslarken telemetri durur; operator bilmeden
    tetiklememeli."""
    kod = _modal()
    i = kod.index("const handleLocalUpdate")
    govde = kod[i:i + 800]
    assert "window.confirm(" in govde, "guncelleme onay sormadan tetikleniyor"
    assert govde.index("window.confirm(") < govde.index("updateGatewayLocally("), (
        "onay cagridan SONRA soruluyor"
    )


def test_arayuz_BILINMIYOR_durumunu_ayirt_ediyor():
    kod = _modal()
    assert "update_available === true" in kod
    assert "update_available === false" in kod, (
        "arayuz uc durumu ayirt etmiyor — bilinmiyor iken 'guncel' gosterir"
    )
