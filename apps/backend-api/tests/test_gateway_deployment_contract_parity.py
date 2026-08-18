"""Gateway deployment sozlesmesi: UC uretim yolu da ayni sozlesmeyi tasimali.

YASANAN SORUN
-------------
Bir gateway kurulumu Grid'de UC ayri yerden uretiliyor ve ucu de birbirinden
bagimsiz kaymisti:

  1. `gateway_compose._COMPOSE_TEMPLATE`  -> uzak kurulum compose (frontend indirir)
  2. `gateway_compose._ENV_TEMPLATE`      -> Docker disi .env (ayni uctan indirilir)
  3. `infra/appliance/e1-gwd.py`          -> yerel kurulum (appliance ajani)

Olculen kaymalar (v2.98.0 / gateway v1.10.0):

  * .env ciktisinda `DNP3_LIBRARY=dnp3py` -> LEGACY kutuphane. Bu uc
    `GET /gateways/{kod}/docker-compose?format=env` ile UretIMDEN erisilebilir;
    yani operator farkinda olmadan Group 110 desteklemeyen, OpenDNP3
    outstation'lariyla tutarsiz davranan adapter'i kurabiliyordu.
  * compose ciktisinda `CONFIG_REFRESH_SEC` HIC yoktu (kod varsayilani 60),
    .env ciktisinda 30 vardi. Ayni Grid, iki farkli davranis uretiyordu.
  * `DNP3_RESPONSE_TIMEOUT_SEC` / `DNP3_READ_STRATEGY` uretiliyordu; aktif
    yadnp3 adapter'i bunlari YOK SAYAR ve baslangicta uyari basar.
  * `stop_grace_period` yoktu: `docker stop` 10sn'lik varsayilanla in-flight
    bir CROB'un sonucunu komut defterine yazmadan SIGKILL uretebilirdi.

NEDEN "IKI SABLONU ELLE AYNI YAP" YETMEZ
----------------------------------------
Mevcut `test_gateway_agent_compose.py` yalnizca Grid remote ile Grid local'i
BIRBIRIYLE karsilastiriyordu. Iki yanlis sablon birbirine esit olabilir --
nitekim oyleydi: ikisi de legacy knob uretiyor, ikisinde de CONFIG_REFRESH
eksikti ve test yesildi. Bu yuzden karsilastirma artik gateway repo'sunun
UretTIGI sozlesmeye karsi yapilir.

SOZLESME NEREDE
---------------
Sahibi gateway repo'sudur:
    enerjione-grid-dnp3-gateway:docker/gateway-deployment-contract.json
Grid tam kopyasini vendor eder:
    infra/gateway-contract/v<surum>.json
Runtime'da iki repo BIRBIRINE BAGLANMAZ (on-prem/offline calisir); yalnizca
CI karsilastirir.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from app.services import gateway_compose as gc
from app.services.gateway_compose import ComposeRenderInput

KOK = Path(__file__).resolve().parents[3]
# GECERLI sozlesme snapshot'i. Eski surumler (`v1.11.0.json`) tarihsel kanit
# olarak dizinde KALIR; testin ve CI'nin baktigi dosya budur ve gateway
# yayinlandiginda ikisi BIRLIKTE guncellenir.
SOZLESME_YOLU = KOK / "infra/gateway-contract/v1.11.1.json"
E1GWD_YOLU = KOK / "infra/appliance/e1-gwd.py"

TOKEN = "t" * 48


def _sozlesme() -> dict:
    return json.loads(SOZLESME_YOLU.read_text(encoding="utf-8"))


SOZLESME = _sozlesme()
VARSAYILANLAR: dict[str, str] = SOZLESME["environment_defaults"]
YASAKLI: dict[str, str] = SOZLESME["forbidden_environment"]
RUNTIME: dict = SOZLESME["docker_runtime"]


# ---------------------------------------------------------------------------
# Render yardimcilari -- GERCEK uretim fonksiyonlari cagrilir
# ---------------------------------------------------------------------------


def _girdi(**kw) -> ComposeRenderInput:
    temel = dict(
        code="GW-001",
        token=TOKEN,
        name="Saha 1",
        backend_url="http://10.0.0.5/api/v1",
        nats_url="nats://gateway:pw@10.0.0.5:4222",
        host_port=8020,
    )
    temel.update(kw)
    return ComposeRenderInput(**temel)


def _uzak_compose() -> dict:
    return yaml.safe_load(gc.render_compose(_girdi()))


def _uzak_env() -> dict[str, str]:
    govde = gc.render_env(_girdi())
    out: dict[str, str] = {}
    for satir in govde.splitlines():
        satir = satir.strip()
        if not satir or satir.startswith("#") or "=" not in satir:
            continue
        k, v = satir.split("=", 1)
        out[k] = v
    return out


def _e1gwd():
    spec = importlib.util.spec_from_file_location("e1gwd", E1GWD_YOLU)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _yerel_compose() -> dict:
    mod = _e1gwd()
    govde = mod.render_compose(
        "GW-001",
        "Saha 1",
        {
            "token": TOKEN,
            "backend_url": "http://host.docker.internal/api/v1",
            "nats_url": "nats://gateway:pw@host.docker.internal:4222",
            "host_port": 8020,
            "image": "ghcr.io/enerjione/enerjione-grid-dnp3-gateway:latest",
            "app_environment": "production",
            "initiating_port_base": 20100,
            "initiating_port_count": 0,
            "publish_dnp3_quality": False,
        },
    )
    return yaml.safe_load(govde)


def _servis(compose: dict) -> dict:
    return compose["services"]["gateway"]


def _env(compose: dict) -> dict[str, str]:
    return {k: str(v) for k, v in _servis(compose)["environment"].items()}


# Uc uretim yolunun tamami. Her yeni yol buraya eklenmeli.
YOLLAR = ("uzak_compose", "yerel_compose", "uzak_env")


def _yol_env(ad: str) -> dict[str, str]:
    if ad == "uzak_compose":
        return _env(_uzak_compose())
    if ad == "yerel_compose":
        return _env(_yerel_compose())
    if ad == "uzak_env":
        return _uzak_env()
    raise AssertionError(ad)


# ---------------------------------------------------------------------------
# T01 -- sozlesme ve vendor kopyasi
# ---------------------------------------------------------------------------


def test_T01_sozlesme_semasi_gecerli():
    for alan in (
        "contract_version",
        "gateway_release",
        "gateway_source_sha",
        "environment_defaults",
        "required_environment",
        "forbidden_environment",
        "docker_runtime",
        "intentional_mode_differences",
    ):
        assert alan in SOZLESME, f"sozlesmede `{alan}` yok"
    assert isinstance(SOZLESME["contract_version"], int)
    assert VARSAYILANLAR and all(isinstance(v, str) for v in VARSAYILANLAR.values())
    # Cakisma: ayni anahtar hem zorunlu hem yasakli olamaz.
    assert not (set(VARSAYILANLAR) & set(YASAKLI))
    assert not (set(SOZLESME["required_environment"]) & set(YASAKLI))


def test_T01b_her_varsayilanin_gerekcesi_var():
    """Deger degistirmek isteyen bir sonraki kisi NEDEN'i gorebilmeli.

    Gerekcesiz bir sayi, bir sonraki turda 'herhalde keyfi' diye degistirilir --
    bu drift'in ta kendisi.
    """
    gerekce = SOZLESME.get("value_rationale", {})
    kritik = {
        "DNP3_LIBRARY",
        "CONFIG_REFRESH_SEC",
        "DEFAULT_POLL_INTERVAL_SEC",
        "DNP3_EVENT_SCAN_INTERVAL_SEC",
        "DNP3_EVENT_BASELINE_INTERVAL_SEC",
        "TELEMETRY_PUBLISHER",
    }
    eksik = sorted(kritik - set(gerekce))
    assert not eksik, f"su degerlerin gerekcesi yazilmamis: {eksik}"


# ---------------------------------------------------------------------------
# T04 / T05 / T06 -- uc yol da sozlesmeyi tasiyor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("yol", YOLLAR)
@pytest.mark.parametrize("anahtar", sorted(VARSAYILANLAR))
def test_T04_T05_uretim_yollari_sozlesme_degerini_tasiyor(yol: str, anahtar: str):
    env = _yol_env(yol)
    beklenen = VARSAYILANLAR[anahtar]
    assert anahtar in env, (
        f"`{yol}` ciktisinda `{anahtar}` YOK. Sozlesme bunu zorunlu kiliyor; "
        f"eksik olmasi gateway'in kod varsayilanina dusmesi demektir "
        f"(beklenen {beklenen!r})."
    )
    assert env[anahtar] == beklenen, (
        f"`{yol}` ciktisinda `{anahtar}` = {env[anahtar]!r}, sozlesme {beklenen!r} diyor"
    )


@pytest.mark.parametrize("yol", YOLLAR)
def test_T06_T15_yasakli_legacy_anahtarlar_uretilmiyor(yol: str):
    env = _yol_env(yol)
    for anahtar, neden in YASAKLI.items():
        assert anahtar not in env, (
            f"`{yol}` ciktisinda yasakli `{anahtar}` var. {neden}"
        )


@pytest.mark.parametrize("yol", YOLLAR)
def test_T07_dnp3_kutuphanesi_yadnp3(yol: str):
    """P0: indirilebilir .env `dnp3py` uretiyordu ve bu uc uretimden erisilebilir.

    Ayri test cunku bu, sozlesmenin herhangi bir maddesi degil; sessiz bir
    LEGACY ADAPTER kurulumu demekti (Group 110 yok, OpenDNP3 outstation'la
    tutarsiz davranis).
    """
    assert _yol_env(yol).get("DNP3_LIBRARY") == "yadnp3"


@pytest.mark.parametrize("yol", YOLLAR)
def test_T21_T22_install_mode_bildirilmis_degerlerden(yol: str):
    izinli = SOZLESME["intentional_mode_differences"]["INSTALL_MODE"]
    beklenen = {izinli["remote"], izinli["local"]}
    env = _yol_env(yol)
    assert "INSTALL_MODE" in env, f"`{yol}` INSTALL_MODE uretmiyor"
    assert env["INSTALL_MODE"] in beklenen
    if yol == "yerel_compose":
        assert env["INSTALL_MODE"] == izinli["local"], (
            "yerel kurulum `local` olmali: NATS zorunlu, sessiz HTTP yedegi YOK"
        )
    else:
        assert env["INSTALL_MODE"] == izinli["remote"]


# ---------------------------------------------------------------------------
# T14 / T17 / T18 / T19 / T20 -- docker runtime
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("compose_fn", [_uzak_compose, _yerel_compose])
def test_T14_stop_grace_period(compose_fn):
    svc = _servis(compose_fn())
    assert str(svc.get("stop_grace_period")) == RUNTIME["stop_grace_period"], (
        "stop_grace_period sozlesmedeki degeri tasimiyor -- yetersiz pencerede "
        "in-flight CROB sonucu komut defterine yazilamadan SIGKILL gelir"
    )


@pytest.mark.parametrize("compose_fn", [_uzak_compose, _yerel_compose])
def test_T17_state_volume_korunuyor(compose_fn):
    compose = compose_fn()
    svc = _servis(compose)
    hedef = RUNTIME["state_volume_mount"]
    baglar = [str(v) for v in svc.get("volumes", [])]
    assert any(b.endswith(hedef) for b in baglar), (
        f"state volume yok ({hedef}). Kaybolursa komut defteri epoch'u sifirlanir "
        f"ve outbox/instance state gider."
    )
    assert compose.get("volumes"), "adlandirilmis volume tanimi yok (anonim volume recreate'te kaybolur)"


@pytest.mark.parametrize("compose_fn", [_uzak_compose, _yerel_compose])
def test_T18_ulimit_korunuyor(compose_fn):
    nofile = _servis(compose_fn())["ulimits"]["nofile"]
    assert int(nofile["soft"]) == RUNTIME["ulimits"]["nofile"]["soft"]
    assert int(nofile["hard"]) == RUNTIME["ulimits"]["nofile"]["hard"]


@pytest.mark.parametrize("compose_fn", [_uzak_compose, _yerel_compose])
def test_T19_logging_korunuyor(compose_fn):
    log = _servis(compose_fn())["logging"]
    assert log["driver"] == RUNTIME["logging"]["driver"]
    assert str(log["options"]["max-size"]) == RUNTIME["logging"]["options"]["max-size"]
    assert str(log["options"]["max-file"]) == RUNTIME["logging"]["options"]["max-file"]


@pytest.mark.parametrize("compose_fn", [_uzak_compose, _yerel_compose])
def test_restart_ve_healthcheck_korunuyor(compose_fn):
    svc = _servis(compose_fn())
    assert svc["restart"] == RUNTIME["restart"]
    hc = svc["healthcheck"]
    assert hc["interval"] == RUNTIME["healthcheck"]["interval"]
    assert int(hc["retries"]) == RUNTIME["healthcheck"]["retries"]


def test_T20_initiating_portlar_korunuyor():
    """Cihaz gateway'e baglaniyorsa dinlenen portlar host'a acilmali."""
    compose = yaml.safe_load(
        gc.render_compose(_girdi(initiating_port_base=20100, initiating_port_count=2))
    )
    portlar = [str(p) for p in _servis(compose)["ports"]]
    assert any("20100" in p for p in portlar), f"initiating port acilmamis: {portlar}"
    assert any("20101" in p for p in portlar)


# ---------------------------------------------------------------------------
# T16 -- normalize edilmis remote/local esitligi
# ---------------------------------------------------------------------------


def test_T16_remote_local_normalize_edilmis_parity():
    """Bilincli farklar DISINDA iki yol bit bit ayni env uretmeli.

    Bilincli farklar sozlesmede BILDIRILIR; bildirilmemis her fark buradan
    kirmizi doner.
    """
    uzak = _yol_env("uzak_compose")
    yerel = _yol_env("yerel_compose")

    bildirilen = set(SOZLESME["intentional_mode_differences"])
    # Kurulum-ozel (her kurulumda zaten farkli) alanlar
    kurulum_ozel = {
        "GATEWAY_CODE", "GATEWAY_TOKEN", "GATEWAY_NAME", "APP_ENVIRONMENT",
        "BACKEND_API_URL", "NATS_URL", "INSTALL_MODE", "MAX_PARALLEL_DEVICES",
        "GATEWAY_INSECURE_ALLOW_PLAINTEXT", "GATEWAY_PUBLISH_DNP3_QUALITY",
    }
    yoksay = bildirilen | kurulum_ozel

    farklar = {}
    for k in sorted(set(uzak) | set(yerel)):
        if k in yoksay:
            continue
        if uzak.get(k) != yerel.get(k):
            farklar[k] = (uzak.get(k), yerel.get(k))

    assert not farklar, (
        "uzak ve yerel kurulum arasinda BILDIRILMEMIS fark var "
        f"(uzak, yerel): {farklar}. Bilincli ise sozlesmedeki "
        "`intentional_mode_differences` altina yazin."
    )


# ---------------------------------------------------------------------------
# Guvenlik siniri (T25/T26 ile ayni dosyadaki testler korunur)
# ---------------------------------------------------------------------------


def test_e1gwd_allowlist_hala_yerinde():
    """Parity duzeltmesi bahanesiyle ajanin guvenlik siniri gevsememeli."""
    mod = _e1gwd()
    assert isinstance(mod.ALLOWED_PARAM_KEYS, frozenset)
    # Skalerler disinda bir sey kabul edilmemeli: allowlist daralmali/sabit
    # kalmali, genislememeli. YAML/dict/list tasiyan anahtar YOK.
    beklenen = {
        "image", "token", "backend_url", "nats_url", "host_port",
        "app_environment", "initiating_port_base", "initiating_port_count",
        "publish_dnp3_quality",
        # F5 komut duzlemi sirri (2.100.2). Bilincli ekleme: SKALER bir sir,
        # yapi/YAML degil. Ajanin "disardan compose kabul etme" siniri
        # korunuyor; yalnizca bir env degeri daha tasiniyor.
        "command_delivery_token",
    }
    fazla = mod.ALLOWED_PARAM_KEYS - beklenen
    assert not fazla, (
        f"e1-gwd parametre allowlist'i genislemis: {sorted(fazla)}. Ajan "
        "yalnizca skaler kurulum parametresi kabul etmeli; compose YAML'i "
        "DISARDAN gelemez."
    )
    # Bilinmeyen anahtar reddedilmeli (fonksiyon adi degisse de davranis kalir).
    dogrulayici = getattr(mod, "validate_params", None) or getattr(mod, "_validate_params", None)
    assert dogrulayici is not None, "parametre dogrulayici bulunamadi"
    with pytest.raises(Exception):
        dogrulayici({"rastgele_anahtar": "x"})


def test_docker_sock_backende_verilmiyor():
    """Backend'e /var/run/docker.sock verilmesi bu isin kapsaminda DEGIL."""
    compose = (KOK / "docker-compose.yml").read_text(encoding="utf-8")
    bas = compose.index("backend-api:")
    son = compose.index("tag-engine:", bas)
    assert "/var/run/docker.sock" not in compose[bas:son]
