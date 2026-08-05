"""Gateway host ajani (e1-gwd) compose uretimi + parametre allowlist'i.

Iki sey guvence altina aliniyor:

1) ESLESME (parity): ajanin urettigi compose ile backend'in "baska cihaza kur"
   akisinda kullaniciya indirdigi compose AYNI olmali. Iki sablon iki ayri
   dosyada yasiyor (biri host'ta root ile calisan stdlib-only bir script,
   digeri backend servisi) — ayrisirlarsa ayni gateway iki farkli sekilde
   kurulur ve bu sessizce olur. Bu test onlari birbirine baglar.

2) ALLOWLIST: ajan artik compose GOVDESI kabul etmiyor; yalnizca dogrulanmis
   skaler parametrelerden kendi sablonunu render ediyor. Onceki tasarim regex
   kara listesiyle gelen YAML'i suzuyordu ve asagidaki testlerde gorulen
   varyantlarla asilabiliyordu (uzun-form bind, named-volume driver_opts,
   security_opt unconfined, build.context, `privileged: yes`).
   Bu testler kara listeye DONULMESINI engeller.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.services.gateway_compose import ComposeRenderInput, render_compose

# --- e1-gwd.py'yi modul olarak yukle (tire iceriyor, normal import olmaz) ----
_AGENT_PATH = (
    Path(__file__).resolve().parents[3] / "infra" / "appliance" / "e1-gwd.py"
)


def _load_agent():
    spec = importlib.util.spec_from_file_location("e1_gwd", _AGENT_PATH)
    assert spec and spec.loader, f"e1-gwd.py yuklenemedi: {_AGENT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def agent():
    if not _AGENT_PATH.is_file():
        pytest.skip(f"e1-gwd.py bulunamadi: {_AGENT_PATH}")
    return _load_agent()


def _params(**over):
    base = {
        "image": "ghcr.io/enerjione/enerjione-grid-dnp3-gateway:latest",
        "token": "abcdefghijklmnop0123456789",
        "backend_url": "http://host.docker.internal/api/v1",
        "nats_url": "nats://gateway:secretpass@10.0.0.5:4222",
        "host_port": 8020,
        "app_environment": "production",
        "initiating_port_base": 20100,
        "initiating_port_count": 0,
    }
    base.update(over)
    return base


def _backend_compose(code="GW-1", name="Saha 1", **over):
    p = _params(**over)
    return render_compose(
        ComposeRenderInput(
            code=code,
            token=p["token"],
            name=name,
            backend_url=p["backend_url"],
            nats_url=p["nats_url"],
            host_port=p["host_port"],
            image=p["image"],
            app_environment=p["app_environment"],
            initiating_port_base=p["initiating_port_base"],
            initiating_port_count=p["initiating_port_count"],
        )
    )


# --- 1) Parity --------------------------------------------------------------
@pytest.mark.parametrize(
    "over",
    [
        {},
        {"initiating_port_count": 50},
        {"initiating_port_count": 1, "initiating_port_base": 21100},
        {"host_port": 9999},
        {"app_environment": "staging"},
        {"backend_url": "https://grid.example.com/api/v1/"},  # trailing slash
        {"image": "ghcr.io/x/y@sha256:" + "a" * 64},
    ],
    ids=[
        "default", "50-initiating", "1-initiating", "custom-health-port",
        "staging", "trailing-slash-url", "digest-image",
    ],
)
def test_agent_and_backend_render_identical_compose(agent, over):
    """Ajan sablonu backend sablonundan AYRISMAMIS olmali."""
    code, name = "GW-1", "Saha 1"
    params = agent._validate_params(_params(**over))
    from_agent = agent.render_compose(code, name, params)
    from_backend = _backend_compose(code=code, name=name, **over)
    assert from_agent == from_backend, (
        "e1-gwd.py COMPOSE_TEMPLATE ile gateway_compose.py _COMPOSE_TEMPLATE "
        "ayrismis. Iki sablonu da guncelleyin."
    )


def test_rendered_compose_contains_expected_values(agent):
    params = agent._validate_params(_params(initiating_port_count=3))
    body = agent.render_compose("GW-A", "Hat Basi", params)
    assert 'GATEWAY_CODE: "GW-A"' in body
    assert 'GATEWAY_NAME: "Hat Basi"' in body
    assert "container_name: e1-gw-gw-a" in body
    assert 'NATS_URL: "nats://gateway:secretpass@10.0.0.5:4222"' in body
    # initiating blok: host 20100-20102 -> container 20100-20102
    assert '"20100-20102:20100-20102"' in body
    # count=0 iken blok hic olmamali
    body0 = agent.render_compose("GW-A", "x", agent._validate_params(_params()))
    assert "20100-" not in body0


def test_compose_project_adi_container_ile_hizali(agent):
    """`name:` alani container/volume adlandirmasiyla AYNI prefix'te olmali.

    Eski sablon `name: e1-gateway-*` uretiyordu ama agent `-p e1-gw-*` ile
    kuruyor (container'lar o projeye kayitli). `-p`'siz calistirilan her
    `docker compose up -d` "container name already in use" veriyordu ve
    guncelleme yapilamiyordu (sahada yasandi)."""
    params = agent._validate_params(_params())
    body = agent.render_compose("GW-A", "x", params)
    assert "name: e1-gw-gw-a\n" in body
    assert "e1-gateway-" not in body
    # Ajanin compose'a verdigi -p degeriyle dosyadaki name ayni olmali.
    assert agent._project_name("GW-A") == "e1-gw-gw-a"


def test_cikti_production_temiz(agent):
    """Uretilen dosya musteriye gider: cok satirli gerekce yorumu, olcum
    sonucu, tarih anlatisi OLMAMALI. En fazla 2 satirlik baslik + tek
    satirlik bolum basliklari. Gerekceler kaynak kodda ve docs/APPLIANCE.md
    bolum 8'de yasar."""
    params = agent._validate_params(_params())
    body = agent.render_compose("GW-A", "Saha", params)
    satirlar = body.splitlines()
    # Ilk iki satir kimlik basligi; sonrasinda ardisik yorum satiri yok
    # (cok satirli anlatinin imzasi ardisik # satirlaridir).
    govde = satirlar[2:]
    for onceki, simdiki in zip(govde, govde[1:]):
        ikisi_de_yorum = onceki.strip().startswith("#") and simdiki.strip().startswith("#")
        assert not ikisi_de_yorum, f"cok satirli yorum blogu: {onceki!r} / {simdiki!r}"
    # Olcum/tarih/anlati kaliplari geri gelmesin.
    for yasak in ("2026-", "olcum", "sahada", "eskiden", "fd acikti", "v2.2"):
        assert yasak not in body.lower(), f"gelistirme notu sizmis: {yasak!r}"


def test_plaintext_bayragi_kosullu(agent):
    """GATEWAY_INSECURE_ALLOW_PLAINTEXT guvenlik opt-out'u: https backend'de
    "false", http backend'de "true" uretilmeli — sabit "true" her dosyada
    acik geliyordu."""
    p_http = agent._validate_params(_params())
    assert p_http["backend_url"].startswith("http://") or "://" not in p_http["backend_url"]
    body_http = agent.render_compose("GW-A", "x", p_http)
    assert 'GATEWAY_INSECURE_ALLOW_PLAINTEXT: "true"' in body_http

    p_https = agent._validate_params(_params(backend_url="https://scada.example.com/api/v1"))
    body_https = agent.render_compose("GW-A", "x", p_https)
    assert 'GATEWAY_INSECURE_ALLOW_PLAINTEXT: "false"' in body_https

    # Backend renderer ayni mantigi kullanmali (parity zaten sablonu esitler;
    # bu, HESAPLANAN degerin esitligini kilitler).
    from app.services.gateway_compose import _insecure_allow_plaintext

    assert _insecure_allow_plaintext("https://scada.example.com") == "false"
    assert _insecure_allow_plaintext("http://10.0.0.5:8000") == "true"


def test_env_sirasi_mantiksal(agent):
    """kimlik -> ortam -> backend -> telemetri -> saglik/polling -> DNP3 ->
    log. Rastgele sira karsilastirmayi zorlastiriyordu."""
    params = agent._validate_params(_params())
    body = agent.render_compose("GW-A", "x", params)
    sira = [
        "GATEWAY_CODE:", "APP_ENVIRONMENT:", "BACKEND_API_URL:",
        "NATS_URL:", "WORKER_HEALTH_HOST:", "DNP3_LOCAL_ADDRESS:", "LOG_LEVEL:",
    ]
    konumlar = [body.index(anahtar) for anahtar in sira]
    assert konumlar == sorted(konumlar), f"env sirasi bozuk: {sira} -> {konumlar}"


def test_compose_ulimits_nofile_var(agent):
    """fd tavani sablonda OLMALI — elle eklenen ulimits her render'da
    siliniyordu. Her DNP3 cihazi bir TCP soketi tutar; Docker varsayilani
    1024 soft limit, 500 cihaz hedefinde baglanti flap'iyle birlikte
    yetersiz. Limit dolunca hata "cihaz kopuk" gibi gorunur."""
    params = agent._validate_params(_params())
    body = agent.render_compose("GW-A", "x", params)
    assert "ulimits:" in body
    assert "nofile:" in body
    assert "soft: 65536" in body
    assert "hard: 65536" in body


# --- 2) compose govdesi reddi ----------------------------------------------
def test_agent_rejects_compose_body(agent):
    """En onemli test: serbest metin compose ARTIK kabul edilmiyor."""
    with pytest.raises(ValueError, match="compose govdesi kabul etmiyor"):
        agent._validate({
            "action": "install", "code": "GW-1", "name": "x",
            "compose": "services:\n  evil:\n    image: alpine\n",
        })


def test_agent_requires_params_for_install(agent):
    with pytest.raises(ValueError, match="params"):
        agent._validate({"action": "install", "code": "GW-1", "name": "x"})


# --- 3) Eski kara listeyi ASAN varyantlar artik yapisal olarak imkansiz ----
# Bu degerler parametre olarak gelse bile regex'ler reddeder; compose'a
# gomulemezler. Testler "kara listeye geri donulmesin" diye duruyor.
@pytest.mark.parametrize(
    "field,value",
    [
        # YAML skalerinden kacis denemeleri
        ("image", 'alpine"\n    privileged: true\n    x: "'),
        ("image", "alpine\n    privileged: true"),
        ("token", 'tok"\n    privileged: true\n    y: "abcdefghijklmnop'),
        ("backend_url", 'http://x"\n    privileged: true\n    z: "'),
        ("nats_url", 'nats://x"\n    pid: host\n    q: "'),
        # Sablon enterpolasyonu / komut denemeleri
        ("image", "alpine:${EVIL}"),
        ("image", "alpine:`id`"),
        ("backend_url", "http://x/$(id)"),
        # Yol/protokol kacisi
        ("backend_url", "file:///etc/passwd"),
        ("backend_url", "javascript:alert(1)"),
        ("nats_url", "tcp://x:4222"),
        ("image", "/etc/passwd"),
        ("image", "ALPINE:LATEST"),  # buyuk harf docker'da gecersiz
        # Bos / cok kisa
        ("token", "short"),
        ("image", ""),
        ("nats_url", ""),
    ],
)
def test_params_reject_dangerous_values(agent, field, value):
    with pytest.raises(ValueError):
        agent._validate_params(_params(**{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("host_port", 0),
        ("host_port", 70000),
        ("host_port", "8020"),          # metin degil tam sayi olmali
        ("host_port", True),            # bool int alt sinifi — reddedilmeli
        ("initiating_port_base", 80),   # <1024
        ("initiating_port_count", -1),
        ("initiating_port_count", 5000),
        ("app_environment", "prod"),    # tam eslesme sart
        ("app_environment", "production; rm -rf /"),
    ],
)
def test_params_reject_out_of_range(agent, field, value):
    with pytest.raises(ValueError):
        agent._validate_params(_params(**{field: value}))


def test_params_reject_unknown_key(agent):
    """Bilinmeyen anahtar sessizce yutulmamali — dogrulanmadan sablona
    girebilecek bir alan eklenmesini engeller."""
    with pytest.raises(ValueError, match="bilinmeyen parametre"):
        agent._validate_params(_params(privileged=True))


def test_params_reject_non_dict(agent):
    for bad in (None, "compose", 42, ["a"]):
        with pytest.raises(ValueError):
            agent._validate_params(bad)


def test_gateway_name_with_quotes_rejected(agent):
    """Ad UI'dan gelen serbest metin — tirnak ile YAML skalerinden cikilmasin."""
    with pytest.raises(ValueError, match="gateway adi"):
        agent._validate({
            "action": "install", "code": "GW-1",
            "name": 'x"\n    privileged: true\n    k: "',
            "params": _params(),
        })


def test_gateway_code_regex_blocks_path_traversal(agent):
    for bad in ("../etc", "a/b", "", "-lead", 'a"b', "a b"):
        with pytest.raises(ValueError, match="gecersiz gateway kodu"):
            agent._validate({
                "action": "install", "code": bad, "name": "x", "params": _params(),
            })


def test_valid_request_passes(agent):
    clean = agent._validate({
        "action": "install", "code": "GW-1", "name": "Saha 1", "params": _params(),
    })
    assert clean["action"] == "install"
    assert clean["code"] == "GW-1"
    assert clean["params"]["host_port"] == 8020
    # Turkce karakterli ad kabul edilmeli (tirnak/kontrol karakteri yok)
    ok = agent._validate({
        "action": "install", "code": "GW-2", "name": "Şanlıurfa Hattı",
        "params": _params(),
    })
    assert ok["name"] == "Şanlıurfa Hattı"


def test_rendered_output_passes_self_check(agent):
    """Uretilen compose kendi saglik kontrolunden gecmeli (sablon temiz)."""
    import re as _re

    params = agent._validate_params(_params(initiating_port_count=10))
    body = agent.render_compose("GW-1", "Saha", params)
    for pattern, label in agent.FORBIDDEN_PATTERNS:
        assert not _re.search(pattern, body), f"sablon tehlikeli alan iceriyor: {label}"


def test_remove_and_restart_need_no_params(agent):
    for action in ("remove", "restart"):
        clean = agent._validate({"action": action, "code": "GW-1"})
        assert clean["action"] == action
        assert "params" not in clean


def test_unknown_action_rejected(agent):
    for bad in ("exec", "", "INSTALL", "install; rm -rf /"):
        with pytest.raises(ValueError, match="desteklenmeyen aksiyon"):
            agent._validate({"action": bad, "code": "GW-1"})
