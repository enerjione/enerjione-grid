"""DNP3 SAAT SENKRONIZASYONU — Horstmann icin `nonlan`.

YASANAN HATA
------------
Grid, DNP3_TIME_SYNC degerini UC RENDER NOKTASINDA sabit `"lan"` yaziyordu.
Gateway 1.15.1 lab olcumu (yadnp3 3.2.1.1, gercek outstation):

    lan     -> FC=24 RECORD_CURRENT_TIME  -> WRITE G50V3
    nonlan  -> FC=23 DELAY_MEASUREMENT    -> WRITE G50V1

Horstmann SN 2.0 / Pole Master profili **FC=23 ve G50V1'i ILAN EDER**;
FC=24 ve G50V3'u **ETMEZ**. Yani `lan` seciliyken gateway, cihazin ilan
ETMEDIGI bir nesneyi yaziyordu: NEED_TIME asserted olsa BILE senkronizasyon
basarisiz oluyor ve saat yanlis kaliyordu. Sahada bir cihazin RTC'si **2066**
yilindaydi ve bu Grid'de HIC gorunmuyordu.

Gerekce (vendor edilmis): `docs/gateway-contract/horstmann-time-sync-1.15.1.md`

NEDEN MODEL BAZLI OTOMATIK SECIM YOK
------------------------------------
Ayar GATEWAY basinadir; bir gateway'e farkli modelde cihazlar baglanabilir.
Cihaz adina ya da sinyal profiline bakip prosedur secmek bir STRING
HEURISTIC'i olurdu ve profil adi bir gun degistiginde gateway SESSIZCE
yanlis procedure gecerdi. Secim ACIK, allowlist'li bir alandir.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from app.services.gateway_compose import (
    TIME_SYNC_DEGERLERI,
    TIME_SYNC_VARSAYILAN,
    ComposeRenderError,
    ComposeRenderInput,
    render_compose,
    render_env,
)

KOK = pathlib.Path(__file__).resolve().parents[3]

TEMEL = dict(
    code="GW-1",
    token="t" * 24,
    name="Saha 1",
    backend_url="https://grid.example.com/api/v1",
    nats_url="nats://nats:4222",
)


def _env_satirlari(metin: str) -> dict[str, str]:
    """Compose ya da .env ciktisindan `DNP3_TIME_SYNC` degerini cikar."""
    out: dict[str, str] = {}
    for satir in metin.splitlines():
        kirpik = satir.strip()
        if kirpik.startswith("#") or "DNP3_TIME_SYNC" not in kirpik:
            continue
        if ":" in kirpik and "=" not in kirpik.split(":", 1)[0]:
            ad, _, deger = kirpik.partition(":")
        else:
            ad, _, deger = kirpik.partition("=")
        out[ad.strip()] = deger.strip().strip('"')
    return out


def _appliance():
    spec = importlib.util.spec_from_file_location(
        "e1gwd", KOK / "infra/appliance/e1-gwd.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ===========================================================================
# A / B — Horstmann render: her iki renderer da `nonlan`
# ===========================================================================


def test_A_backend_compose_NONLAN():
    c = render_compose(ComposeRenderInput(**TEMEL))
    assert _env_satirlari(c)["DNP3_TIME_SYNC"] == "nonlan"


def test_A2_backend_env_NONLAN():
    e = render_env(ComposeRenderInput(**TEMEL))
    assert _env_satirlari(e)["DNP3_TIME_SYNC"] == "nonlan"


def test_B_appliance_compose_NONLAN():
    m = _appliance()
    assert m.TIME_SYNC_VARSAYILAN == "nonlan"
    assert m._time_sync_dogrula(None) == "nonlan"


def test_B2_iki_renderer_AYNI_allowlist_ve_varsayilan():
    """Ayrisirsa ayni gateway iki farkli prosedurle kurulabilirdi."""
    m = _appliance()
    assert tuple(m.TIME_SYNC_DEGERLERI) == tuple(TIME_SYNC_DEGERLERI)
    assert m.TIME_SYNC_VARSAYILAN == TIME_SYNC_VARSAYILAN


# ===========================================================================
# C — update / recreate: deger KAYBOLMUYOR
# ===========================================================================


def test_C_tekrar_render_NONLAN_kalir():
    """Compose her guncellemede YENIDEN uretilir; deger her seferinde ayni."""
    for _ in range(3):
        c = render_compose(ComposeRenderInput(**TEMEL))
        assert _env_satirlari(c)["DNP3_TIME_SYNC"] == "nonlan"


def test_C2_deger_SABIT_METIN_DEGIL_parametre():
    """Sablonda duz `lan` kalmamali; aksi halde parametre bosa duser."""
    kaynaklar = [
        KOK / "apps/backend-api/app/services/gateway_compose.py",
        KOK / "infra/appliance/e1-gwd.py",
    ]
    for yol in kaynaklar:
        metin = yol.read_text(encoding="utf-8")
        assert 'DNP3_TIME_SYNC: "lan"' not in metin, f"{yol.name}: sabit lan duruyor"
        assert "DNP3_TIME_SYNC=lan" not in metin, f"{yol.name}: sabit lan duruyor (.env)"
        assert "{{DNP3_TIME_SYNC}}" in metin, f"{yol.name}: yer tutucu yok"


# ===========================================================================
# D / E — acik secim: geriye uyumluluk ve `none`
# ===========================================================================


@pytest.mark.parametrize("deger", ["lan", "nonlan", "none"])
def test_D_E_acik_secim_AYNEN_render_edilir(deger: str):
    """Horstmann olmayan bir outstation `lan` (ya da `none`) secebilmeli."""
    c = render_compose(ComposeRenderInput(**TEMEL, dnp3_time_sync=deger))
    e = render_env(ComposeRenderInput(**TEMEL, dnp3_time_sync=deger))
    assert _env_satirlari(c)["DNP3_TIME_SYNC"] == deger
    assert _env_satirlari(e)["DNP3_TIME_SYNC"] == deger


# ===========================================================================
# F — FAIL CLOSED
# ===========================================================================


@pytest.mark.parametrize(
    "kotu", ["nonlann", "LAN", "Nonlan", "off", "disabled", "", " lan", "lan ", "true"]
)
def test_F_gecersiz_deger_REDDEDILIR(kotu: str):
    """Sessizce `lan`a DUSME YOK.

    1.15.0'a kadar gateway taninmayan HER degeri sessizce `lan` sayiyordu ve
    duzeltmek istedigimiz sey tam da buydu. 1.15.1 gateway'i gecersiz degerde
    ACILMIYOR — Grid'in gecersiz deger uretmesi, sahada acilmayan bir
    konteynere donusurdu.
    """
    with pytest.raises(ComposeRenderError):
        render_compose(ComposeRenderInput(**TEMEL, dnp3_time_sync=kotu))
    with pytest.raises(ComposeRenderError):
        render_env(ComposeRenderInput(**TEMEL, dnp3_time_sync=kotu))


def test_F2_appliance_de_FAIL_CLOSED():
    m = _appliance()
    for kotu in ("nonlann", "LAN", "off", ""):
        with pytest.raises(ValueError):
            m._time_sync_dogrula(kotu)


def test_F3_takma_adlar_URETILMEZ():
    """`off`/`disabled` gateway tarafinda `none`a normalize edilir; Grid
    onlari URETMEZ, yalnizca kanonik uc degeri yazar."""
    assert set(TIME_SYNC_DEGERLERI) == {"lan", "nonlan", "none"}


# ===========================================================================
# Gateway kaydi
# ===========================================================================


def test_gateway_kolonu_VARSAYILAN_nonlan():
    from app.models.gateway import Gateway

    kol = Gateway.__table__.columns["dnp3_time_sync"]
    assert kol.nullable is False
    assert kol.server_default.arg == "nonlan", (
        "mevcut kayitlar `lan`da kalirsa duzeltilmek istenen hatayi yasamaya "
        "devam ederler"
    )


def test_MIGRATION_ile_MODEL_ayni_varsayilani_soyler():
    """Ayrisirsa temiz kurulum ile yukseltilen kurulum FARKLI davranirdi."""
    spec = importlib.util.spec_from_file_location(
        "m77",
        KOK
        / "apps/backend-api/alembic_migrations/versions"
        / "2026_08_21_0003-0077_gateway_dnp3_time_sync.py",
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    from app.models.gateway import Gateway

    assert m.VARSAYILAN == Gateway.__table__.columns["dnp3_time_sync"].server_default.arg
    assert m.VARSAYILAN == TIME_SYNC_VARSAYILAN
