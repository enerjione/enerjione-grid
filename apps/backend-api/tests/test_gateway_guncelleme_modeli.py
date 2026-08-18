"""Gateway guncelleme modeli: UPDATE cekerken RESTART/START CEKMEZ.

URUN KARARI (bilincli, degistirilmeyecek)
-----------------------------------------
Gateway guncellemesi LATEST-RELEASE modelinde calisir:

    Gateway Guncelle -> `ghcr.io/.../enerjione-grid-dnp3-gateway:latest` PULL
                     -> kayit defterinde latest hangi release ise o
                     -> container o release ile RECREATE edilir
    Restart          -> MEVCUT imajla yeniden baslat (pull YOK, upgrade YOK)
    Start            -> MEVCUT imajla baslat        (pull YOK, upgrade YOK)

Yani `restart != update` ve `start != update`. Bu ayrim sahada kritiktir:
operator "servisi bir yeniden baslatayim" derken FARKINDA OLMADAN yeni bir
gateway surumune gecerse, arizanin sebebi ile cozumu ayni anda degisir ve
teshis imkansizlasir.

NEDEN AYRI BIR TEST DOSYASI
---------------------------
Bu ayrimin dogrulugu IKI KATMANDA birden durur ve ikisi ayri repo/dilde:

  1. backend `gateway_agent_service` -> istek govdesine NE yazar
  2. appliance ajani `e1-gwd.py`      -> o istegi HANGI docker komutuna cevirir

Birinci katman dogru olup ikincisi `docker compose up` cagirirsa
`pull_policy: always` yuzunden restart SESSIZCE upgrade'e doner. Test ikisini
birlikte kilitler.

`pull_policy: always` HAKKINDA
------------------------------
Compose sablonunda `pull_policy: always` VARDIR ve KALMALIDIR. Bu alan
YALNIZCA container OLUSTURAN komutlar tarafindan okunur (`up`, `create`,
`run`). `docker compose restart` ve `docker compose start` MEVCUT container
uzerinde calisir; imaj cozumlemesi yapmaz, dolayisiyla `pull_policy`yi HIC
okumaz. Bu yuzden `pull_policy: always` restart/start'i upgrade'e CEVIRMEZ
ve korunur (install/update yollarinda `:latest` etiketinin gercekten yeni
digest'e tasinmasini garanti eder).

Cihaz yeniden baslatmasi da guvenlidir: container'i Docker DAEMON'i
`restart: unless-stopped` politikasiyla kaldirir. Daemon container'in
KAYITLI imaj ID'siyle calisir; `pull_policy` compose istemcisine ait bir
kavramdir ve container yapilandirmasinda saklanmaz.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.core.config import settings
from app.schemas.gateway_agent import GatewayAgentStatus, LocalGateway
from app.services import gateway_agent_service as ajan
from app.services import gateway_release_service as kayit
from app.services.gateway_compose import DEFAULT_GATEWAY_IMAGE

KOK = Path(__file__).resolve().parents[3]
E1GWD_YOLU = KOK / "infra/appliance/e1-gwd.py"

KOD = "GW-TEST"
KULLANICI = "tester"

#: Guncelleme DISINDAKI hicbir istekte gorunmemesi gereken anahtarlar.
#: `image` -> imaj etiketini degistirir; `params` -> compose'u yeniden uretir.
UPGRADE_ANAHTARLARI = ("image", "params", "pull", "update", "nats_url")


# ---------------------------------------------------------------------------
# Ortak yardimcilar
# ---------------------------------------------------------------------------
@pytest.fixture()
def durum_dizini(tmp_path, monkeypatch):
    """Ajan durum dizinini tmp'ye al ve `availability()` gecsin diye doldur."""
    monkeypatch.setattr(settings, "gateway_state_dir", str(tmp_path))
    (tmp_path / ajan.STATE_FILE).write_text(
        json.dumps({"gateways": [{"code": KOD}], "docker_available": True}),
        encoding="utf-8",
    )
    return tmp_path


def _yazilan_istek(dizin: Path) -> dict:
    """Ajana yazilan `request.json` govdesi."""
    return json.loads((dizin / ajan.REQUEST_FILE).read_text(encoding="utf-8"))


def _e1gwd():
    spec = importlib.util.spec_from_file_location("e1gwd_guncelleme", E1GWD_YOLU)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def ajan_modulu(monkeypatch):
    """`_run`i yakalayan e1-gwd modulu. Donen liste calistirilan komutlar."""
    mod = _e1gwd()
    calistirilan: list[list[str]] = []

    def sahte_run(cmd, timeout=None, **kwargs):
        calistirilan.append(list(cmd))
        return 0, ""

    monkeypatch.setattr(mod, "_run", sahte_run)
    monkeypatch.setattr(mod, "_write_status", lambda *a, **k: None)
    # `_dogrula_hedef` diskteki compose dosyasini ve docker'i sorgular;
    # burada ilgilendigimiz sey HANGI komutun calistigi.
    monkeypatch.setattr(mod, "_dogrula_hedef", lambda code, action: (None, {}))
    return mod, calistirilan


def _compose_alt_komutlari(calistirilan: list[list[str]]) -> list[str]:
    """Calistirilan her komutun compose ALT KOMUTUNU dondur (pull/up/restart...).

    `_compose_cmd()` ciktisi ("docker compose") + ["-p", ad, "-f", yol, ALT...]
    seklinde; alt komut `-f <yol>`dan SONRAKI ilk parcadir.
    """
    out = []
    for cmd in calistirilan:
        if "-f" in cmd:
            i = cmd.index("-f")
            if i + 2 < len(cmd):
                out.append(cmd[i + 2])
    return out


# ---------------------------------------------------------------------------
# A) request_update -> :latest
# ---------------------------------------------------------------------------
def test_A_update_istegi_latest_imaji_tasir(durum_dizini):
    """Guncelleme HER ZAMAN `:latest` ister -- urun karari.

    Sahada bir kez sabit etiket yazilmis kurulumlar (`:1.5.0`) ilk
    guncellemede kendiliginden `:latest`e doner; yoksa "Guncelle" butonu
    o kurulumu bir daha ilerletemez ve ekran kalici "Guncel" der.
    """
    ajan.request_update(KOD, KULLANICI)
    istek = _yazilan_istek(durum_dizini)

    assert istek["action"] == "update"
    assert istek["params"]["image"] == DEFAULT_GATEWAY_IMAGE
    assert istek["params"]["image"].endswith(":latest"), (
        f"guncelleme :latest DISINDA bir etikete sabitlenmis: "
        f"{istek['params']['image']!r}. Bu bilincli urun kararina aykiri."
    )


def test_A2_varsayilan_imaj_beklenen_paket(durum_dizini):
    """Paket adi da sozlesmenin parcasi: yanlis repo sessizce cekilmesin."""
    assert DEFAULT_GATEWAY_IMAGE == (
        "ghcr.io/enerjione/enerjione-grid-dnp3-gateway:latest"
    )


# ---------------------------------------------------------------------------
# B, C) restart / start -> imaj/pull/upgrade parametresi YOK
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "cagri,beklenen_action",
    [
        (ajan.request_restart, "restart"),
        (ajan.request_start, "start"),
        (ajan.request_stop, "stop"),
    ],
    ids=["restart", "start", "stop"],
)
def test_BC_yasam_dongusu_istekleri_upgrade_tasimaz(
    durum_dizini, cagri, beklenen_action
):
    """restart/start/stop istekleri imaj veya pull parametresi TASIMAZ.

    Tasisalardi ajan compose'u yeniden uretir, `:latest` cozulur ve
    "yeniden baslat" sessizce bir surum yukseltmesi olurdu.
    """
    cagri(KOD, KULLANICI)
    istek = _yazilan_istek(durum_dizini)

    assert istek["action"] == beklenen_action
    for anahtar in UPGRADE_ANAHTARLARI:
        assert anahtar not in istek, (
            f"`{beklenen_action}` istegi `{anahtar}` tasiyor. Yasam dongusu "
            f"aksiyonlari MEVCUT imajla calisir; upgrade YALNIZCA `update`tir."
        )
    # Govde tam olarak `_base_request` alanlari olmali -- fazlasi yok.
    assert set(istek) == {"id", "action", "code", "created_at", "requested_by"}


# ---------------------------------------------------------------------------
# Ajan katmani: istek HANGI docker komutuna ceviriliyor
# ---------------------------------------------------------------------------
def test_ajan_restart_yalnizca_compose_restart_calistirir(ajan_modulu):
    """`restart` -> `docker compose restart`. `pull`/`up` YOK.

    `up` cagrilsaydi `pull_policy: always` devreye girer ve restart bir
    upgrade'e donerdi. Test tam olarak bunu yasaklar.
    """
    mod, calistirilan = ajan_modulu
    sonuc = mod._do_restart({"id": "1", "code": KOD}, ["docker", "compose"])

    assert sonuc["ok"] is True
    alt = _compose_alt_komutlari(calistirilan)
    assert alt == ["restart"], f"beklenen ['restart'], calisan {alt}"
    assert "pull" not in alt and "up" not in alt


def test_ajan_start_yalnizca_compose_start_calistirir(ajan_modulu):
    """`start` -> `docker compose start`. `pull`/`up` YOK."""
    mod, calistirilan = ajan_modulu
    sonuc = mod._do_start({"id": "1", "code": KOD}, ["docker", "compose"])

    assert sonuc["ok"] is True
    alt = _compose_alt_komutlari(calistirilan)
    assert alt == ["start"], f"beklenen ['start'], calisan {alt}"
    assert "pull" not in alt and "up" not in alt


def test_ajan_stop_yalnizca_compose_stop_calistirir(ajan_modulu):
    """`stop` -> `docker compose stop` (`down` DEGIL: container silinmemeli)."""
    mod, calistirilan = ajan_modulu
    sonuc = mod._do_stop({"id": "1", "code": KOD}, ["docker", "compose"])

    assert sonuc["ok"] is True
    alt = _compose_alt_komutlari(calistirilan)
    assert alt == ["stop"]
    assert "down" not in alt, "stop container'i SILMEMELI (durum ayrimi kaybolur)"


def test_ajan_update_pull_VE_up_calistirir(ajan_modulu, tmp_path, monkeypatch):
    """`update` -> once `pull`, sonra `up -d`. Upgrade YOLU BUDUR.

    Bu testin negatifleri (restart/start pull etmez) ancak bu pozitif
    varsa anlamlidir: yoksa "hicbir sey pull etmiyor" da testi gecerdi.
    """
    mod, calistirilan = ajan_modulu
    compose_yolu = tmp_path / "docker-compose.yml"
    compose_yolu.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "_compose_path", lambda code: str(compose_yolu))

    sonuc = mod._do_update({"id": "1", "code": KOD}, ["docker", "compose"])

    assert sonuc["ok"] is True
    alt = _compose_alt_komutlari(calistirilan)
    assert alt == ["pull", "up"], f"beklenen ['pull','up'], calisan {alt}"


def test_ajan_yasam_dongusu_aksiyonlari_pull_policy_okumaz():
    """`pull_policy: always` KORUNUR ve restart/start'i etkilemez.

    Bu test sablondaki alanin VARLIGINI kilitler (kaldirilirsa `:latest`
    yeni digest'e tasindiginda `up` eski imaji kullanabilir) ve neden
    guvenli oldugunu belgeler: alani yalnizca container OLUSTURAN komutlar
    okur, `restart`/`start` mevcut container uzerinde calisir.
    """
    govde = E1GWD_YOLU.read_text(encoding="utf-8")
    assert "pull_policy: always" in govde, (
        "`pull_policy: always` sablondan kaldirilmis. Kaldirmadan once "
        "`:latest` etiketinin guncellemede gercekten yeni digest'e tasindigi "
        "dogrulanmali (bkz. bu dosyanin modul basligi)."
    )
    assert "restart: unless-stopped" in govde, (
        "`restart: unless-stopped` yok: cihaz yeniden baslayinca gateway "
        "kalkmaz. (Bu politika DAEMON'a aittir ve mevcut imaj ID'siyle "
        "calisir; pull tetiklemez.)"
    )


# ---------------------------------------------------------------------------
# D, E) running vs latest tespiti
# ---------------------------------------------------------------------------
def _durum(*, yerel: str | None, uzak: str | None) -> GatewayAgentStatus:
    return GatewayAgentStatus(
        available=True,
        gateways=[
            LocalGateway(
                code=KOD,
                tracked_image=DEFAULT_GATEWAY_IMAGE,
                image_digest=yerel,
                remote_digest=uzak,
            )
        ],
    )


@pytest.fixture()
def kayit_defteri(monkeypatch):
    """`lookup`u sabitler; ag yok."""

    def ayarla(digest: str | None, surum: str | None, hata: str | None = None):
        monkeypatch.setattr(
            kayit,
            "lookup",
            lambda ref: (
                kayit.RegistryImage(digest=digest, version=surum, error=hata),
                False,
            ),
        )

    return ayarla


def test_D_eski_surum_calisiyorsa_guncelleme_VAR(kayit_defteri):
    """running 1.11.3 + registry latest 1.11.4 -> update_available TRUE."""
    kayit_defteri("sha256:yeni", "1.11.4")
    durum = kayit.enrich_agent_status(_durum(yerel="sha256:eski", uzak=None))

    gw = durum.gateways[0]
    assert gw.remote_version == "1.11.4"
    assert gw.update_available is True


def test_E_ayni_digest_calisiyorsa_guncelleme_YOK(kayit_defteri):
    """running == latest -> update_available FALSE."""
    kayit_defteri("sha256:ayni", "1.11.4")
    durum = kayit.enrich_agent_status(_durum(yerel="sha256:ayni", uzak=None))

    gw = durum.gateways[0]
    assert gw.remote_version == "1.11.4"
    assert gw.update_available is False


def test_D2_yeni_release_ciktiginda_tekrar_guncelleme_VAR(kayit_defteri):
    """1.11.4 kosarken registry 1.11.5'e gecerse -> yine TRUE.

    Model "onaylanmis hedef" degil, "kayit defterindeki latest" oldugu icin
    yeni her release kendiliginden gorunur; ek bir cerceve GEREKMEZ.
    """
    kayit_defteri("sha256:v1115", "1.11.5")
    durum = kayit.enrich_agent_status(_durum(yerel="sha256:v1114", uzak=None))

    assert durum.gateways[0].update_available is True


def test_yerel_digest_bilinmiyorsa_guncel_DENMEZ(kayit_defteri):
    """Yerel digest yoksa karar UC DURUMLU kalir: None ("bilinmiyor").

    `False` demek, sormadan verilmis bir "guncelsin" iddiasi olurdu.
    """
    kayit_defteri("sha256:yeni", "1.11.4")
    durum = kayit.enrich_agent_status(_durum(yerel=None, uzak=None))

    assert durum.gateways[0].update_available is None
