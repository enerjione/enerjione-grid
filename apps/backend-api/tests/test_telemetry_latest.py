"""`telemetry_latest` — canli deger okumasi pencere boyutundan bagimsiz olmali.

YASANAN SORUN (T1)
------------------
`/signals/live` son degerleri `telemetry` uzerinde `DISTINCT ON` ile
cikariyordu. Koddaki yorum "index-only scan, <50 ms" diyordu ve 200 cihazda
dogruydu; 600 cihazda DEGIL:

  * `DISTINCT ON` PostgreSQL'de skip-scan DEGILDIR. 30 dakikalik pencerenin
    TAMAMI (~2,16M satir) okunup 12.000 satira indirgeniyordu.
  * Anasayfa bu ucu `device_codes` VERMEDEN cagiriyor; WS koptugunda periyot
    30 sn'den 5 sn'ye dusuyor, yani yuk 6 KATLANIYOR.
  * Istek basina ~311 MB bellek tepesi, container tavani 2 GB. Uc-bes
    esizamanli anasayfa backend-api'yi OOM'a goturuyordu.

Artik her (cihaz, sinyal) icin TEK satir var ve okuma PK uzerinden gidiyor.

EN KRITIK DAVRANIS: BAYAT DEGER YENIYI EZMEMELI
-----------------------------------------------
NATS en-az-bir-kez teslim eder; ayni mesaj yeniden dagitilabilir ve sira
DISINDA gelebilir. Kosulsuz `DO UPDATE` bayat bir okumayi "son deger"
yapardi — ve bu tablo canli ekranin + WS yayininin kaynagi oldugu icin sonuc
dogrudan operatore yansir: deger geri sicrar, ariza gecisi yanlis gorunur.

Upsert semantigi ayrica GERCEK PostgreSQL uzerinde ucdan uca dogrulandi
(backfill en yeniyi alir, bayat ezmez, ayni damga gecer, cihaz silininde
CASCADE, downgrade tabloyu dusurur).
"""

from __future__ import annotations

import ast
import inspect

import pytest


def test_model_bilesik_PK():
    """Cihaz basina sinyal basina TEK satir — tekrar eden satir olmamali."""
    from app.models.telemetry_latest import TelemetryLatest

    pk = {c.name for c in TelemetryLatest.__table__.primary_key.columns}
    assert pk == {"device_id", "signal_key"}, f"birincil anahtar {pk}"


def test_kolonlar_telemetry_ile_AYNI_genislikte():
    """Ayrisirsa upsert `DataError` ile patlar ve batch karantinaya girer."""
    from app.models.telemetry import Telemetry
    from app.models.telemetry_latest import TelemetryLatest

    for ad in ("signal_key", "quality"):
        assert (
            TelemetryLatest.__table__.columns[ad].type.length
            == Telemetry.__table__.columns[ad].type.length
        ), f"{ad} genisligi telemetry ile ayni degil"


def test_cihaz_silininde_CASCADE():
    """Cihaz silinince son deger satirlari da gitmeli; aksi halde yetim kalir."""
    from app.models.telemetry_latest import TelemetryLatest

    fk = next(iter(TelemetryLatest.__table__.columns["device_id"].foreign_keys))
    assert fk.ondelete == "CASCADE"


def test_signals_live_ARTIK_telemetry_taramiyor():
    """Okuma yolu `telemetry_latest`'e gecmeli.

    AST ile bakiliyor: metin aramasi bu dosyanin ya da signals.py'nin kendi
    aciklamalarina takilirdi (bu tuzaga bu depoda birkac kez dusuldu).
    """
    from app.api import signals

    fn = next(
        d
        for d in ast.walk(ast.parse(inspect.getsource(signals)))
        if isinstance(d, ast.FunctionDef) and d.name == "list_live_values"
    )
    kullanilan = {
        n.value.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
    }
    assert "TelemetryLatest" in kullanilan, "canli deger okumasi yeni tabloya gecmemis"
    assert "Telemetry" not in kullanilan, (
        "hala `telemetry` taraniyor — 30 dakikalik pencerenin tamami okunur"
    )
    # DISTINCT ON kalmamali (skip-scan degil, tum pencereyi okur)
    assert not any(
        isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "distinct"
        for n in ast.walk(fn)
    ), "DISTINCT ON hala kullaniliyor"


def test_consumer_upsert_BAYAT_degeri_reddediyor():
    """`WHERE EXCLUDED.source_timestamp >= ...` kosulu SART.

    Kosul dusesse bayat bir redelivery son degeri geri sicratir; canli ekran
    ve WS yayini bunu dogrudan operatore gosterir. Upsert artik ORM degil
    sabit SQL (`_CANLI_SQL`, toplu yazim) — kosul o metinde aranir.
    """
    from app.services import telemetry_consumer as tc

    assert "ON CONFLICT (device_id, signal_key) DO UPDATE" in tc._CANLI_SQL, (
        "telemetry_latest icin upsert yok"
    )
    assert (
        "WHERE EXCLUDED.source_timestamp >= telemetry_latest.source_timestamp"
        in tc._CANLI_SQL
    ), "upsert KOSULSUZ — bayat bir okuma son degeri ezer"


def test_batch_ici_tekillestirme_VAR():
    """Ayni batch'te ayni cift iki kez gelirse tek INSERT bunu kaldiramaz.

    PostgreSQL: "ON CONFLICT DO UPDATE command cannot affect row a second
    time". Sozluk ile tekillestirme olmazsa TUM BATCH patlar ve saglam
    olcumler de redeliver dongusune girer. Tekillestirme artik yazim
    fonksiyonunda (`_tek_gecis_yaz`): dilimlere bolunmus yeniden denemede
    de dogru kalsin diye TEK yerde.
    """
    from app.services import telemetry_consumer as tc

    fn_src = inspect.getsource(tc._tek_gecis_yaz)
    agac = ast.parse(fn_src.lstrip())

    # `canli_satirlar` bir dict olarak baslatilmali (liste degil)
    atamalar = [
        n
        for n in ast.walk(agac)
        if isinstance(n, ast.AnnAssign)
        and getattr(n.target, "id", None) == "canli_satirlar"
    ]
    assert atamalar, "canli_satirlar tanimlanmamis"
    assert isinstance(atamalar[0].value, ast.Dict), (
        "canli_satirlar liste — ayni batch'te tekrar eden cift tum INSERT'i patlatir"
    )
    # En yenisi kalmali: source_timestamp (indeks 5) karsilastirmasi.
    assert "s.canli[5] >=" in fn_src, (
        "tekillestirme en yeni satiri secmiyor — bayat deger kalabilir"
    )


@pytest.mark.parametrize("alan", ["value", "value_string", "quality", "source_timestamp",
                                  "timestamp_quality", "device_event_at", "updated_at"])
def test_upsert_TUM_alanlari_guncelliyor(alan: str):
    """Eksik bir alan "yari guncellenmis" satir birakir: yeni deger ama eski
    kalite gibi — teshiste en yaniltici hal. Upsert sabit SQL'de
    (`_CANLI_SQL`); her alan `alan = EXCLUDED.alan` olarak gecmeli."""
    from app.services import telemetry_consumer as tc

    assert f"{alan} = EXCLUDED.{alan}" in tc._CANLI_SQL, (
        f"upsert `{alan}` alanini guncellemiyor"
    )
