"""Disk Guardian — esik modeli, asla-silme sozlesmesi ve gorunurluk.

BU DOSYA NEYI KILITLER
----------------------
Disk guard'in degeri "bir seyler siliyor olmasi" degil, NEYI SILMEDIGIDIR.
Buradaki testlerin cogu bir davranisin VAR oldugunu degil, YOK oldugunu
kanitlar — cunku sahayi karartacak olan sey yanlis silmedir.

DUZELTILEN TERS ONCELIK (D ajani bulgusu)
-----------------------------------------
Onceki sirada KRITIK seviyede once veritabani pencereleri (dedup defteri,
outbox, canli telemetri) kisaltiliyor; internetten yeniden inebilen harita
karo onbellegi ise ancak ACIL seviyede temizleniyordu. Yani sistem 70 MB'lik
yeniden uretilebilir bir onbellegi TUTARKEN idempotency defterini budamaya
basliyordu. `test_DG03_*` ve `test_DG04_*` yeni sirayi kilitler.
"""

from __future__ import annotations

import os
import time

import pytest

from app.core.config import settings
from app.services import disk_guard as dg

GB = 1024**3


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------


def _durum(free_gb: float, total_gb: float = 456.0, inode_pct: float | None = 3.0):
    """Gercek dosya sistemi olmadan bir DiskStatus kurar."""
    free = int(free_gb * GB)
    total = int(total_gb * GB)
    rezerv = dg.reserve_for(total)
    return dg.DiskStatus(
        path="/test",
        total_bytes=total,
        free_bytes=free,
        reserve_bytes=rezerv,
        level=dg.classify(free, rezerv, total_bytes=total, inode_percent=inode_pct),
        inode_percent=inode_pct,
    )


@pytest.fixture()
def temizlik_kaydi(monkeypatch):
    """Temizlik adimlarini CAGIRMADAN kaydeder.

    Gercek silme yapilmaz; olculen sey hangi adimin hangi seviyede
    tetiklendigidir.
    """
    kayit: list[str] = []

    def _sahte(ad: str):
        def _f(*a, **kw):  # noqa: ANN002, ANN003
            kayit.append(ad)
            return [f"{ad}: sahte"]
        return _f

    monkeypatch.setattr(dg, "_temizle_ftp_bayat_gecici", _sahte("ftp_temp"))
    monkeypatch.setattr(dg, "_temizle_harita_onbellegi", _sahte("map_cache"))
    monkeypatch.setattr(dg, "_relieve_aggressive", _sahte("db_windows"))
    monkeypatch.setattr(dg, "_relieve_emergency", _sahte("backup_trim"))
    monkeypatch.setattr(dg, "_record", lambda s: None)
    return kayit


def _tick_ile(monkeypatch, durum) -> list[str]:
    monkeypatch.setattr(dg, "evaluate", lambda path=None: durum)
    monkeypatch.setattr(settings, "disk_guard_enabled", True, raising=False)
    dg.tick()
    return durum.actions


# ===========================================================================
# DG01-DG04 — SEVIYE / TEMIZLIK SIRASI
# ===========================================================================


def test_DG01_normal_diskte_hicbir_temizlik_yok(monkeypatch, temizlik_kaydi):
    """456 GB'de 404 GB bos — saha baslangic durumu. Hicbir sey yapilmamali."""
    durum = _durum(free_gb=404.0)
    assert durum.level == dg.LEVEL_OK
    _tick_ile(monkeypatch, durum)
    assert temizlik_kaydi == []


def test_DG02_uyari_seviyesinde_YIKICI_temizlik_yok(monkeypatch, temizlik_kaydi):
    """UYARI = gorunurluk. Tek bir bayt bile silinmemeli."""
    durum = _durum(free_gb=60.0)  # %86,8 dolu -> uyari
    assert durum.level == dg.LEVEL_WARN
    _tick_ile(monkeypatch, durum)
    assert temizlik_kaydi == [], "uyari seviyesinde temizlik tetiklendi"


def test_DG03_kritik_seviyede_YALNIZ_izinli_katmanlar(monkeypatch, temizlik_kaydi):
    """KRITIK: bayat gecici + harita onbellegi + kisa DB pencereleri.

    Yedek budama bu seviyede DEVREYE GIRMEZ — yedekler son savunma hatti.
    """
    durum = _durum(free_gb=30.0)  # %93,4 dolu -> kritik
    assert durum.level == dg.LEVEL_CRITICAL
    _tick_ile(monkeypatch, durum)
    assert temizlik_kaydi == ["ftp_temp", "map_cache", "db_windows"]
    assert "backup_trim" not in temizlik_kaydi


def test_DG04_acil_seviyede_GUVENLI_SIRA(monkeypatch, temizlik_kaydi):
    """ACIL: once yeniden uretilebilirler, EN SON yedek budama.

    Sira TERS OLSAYDI sistem, internetten yeniden inebilen bir onbellegi
    tutarken geri donus noktalarini azaltmaya baslardi.
    """
    durum = _durum(free_gb=5.0)
    assert durum.level == dg.LEVEL_EMERGENCY
    _tick_ile(monkeypatch, durum)
    assert temizlik_kaydi == ["ftp_temp", "map_cache", "db_windows", "backup_trim"]
    assert temizlik_kaydi.index("map_cache") < temizlik_kaydi.index("db_windows"), (
        "yeniden uretilebilir onbellek, veritabani penceresinden SONRA "
        "temizleniyor — duzeltilen ters oncelik geri gelmis"
    )


# ===========================================================================
# DG05-DG11 — ASLA SILME SOZLESMESI
# ===========================================================================

#: Disk baskisi ile ASLA silinmemesi gereken tablolar/kavramlar.
_TIER0 = (
    "users", "devices", "gateways", "device_commands", "command",
    "project_settings", "api_keys", "alarm_rules", "signals",
    "responsibility_areas", "grid_topology", "alembic_version",
)
_TIER1 = ("alarm_events", "alarm_comments", "fault_events", "fault_comments",
          "system_events", "notifications")


def _guard_kaynagi() -> str:
    import inspect

    from app.services import storage_snapshot

    return inspect.getsource(dg) + inspect.getsource(storage_snapshot)


@pytest.mark.parametrize("tablo", _TIER0)
def test_DG05_tier0_disk_baskisiyla_SILINMEZ(tablo: str):
    """Yapilandirma / komut defteri / denetim disk baskisiyla purge edilemez.

    Guard kaynaginda bu tablolara giden bir silme yolu OLMAMALI.
    """
    kaynak = _guard_kaynagi()
    for kalip in (f"delete({tablo}", f"DELETE FROM {tablo}", f"purge_{tablo}"):
        assert kalip not in kaynak, f"disk guard TIER0 tablosuna dokunuyor: {tablo}"


@pytest.mark.parametrize("tablo", _TIER1)
def test_DG06_alarm_ariza_olay_gecmisi_SILINMEZ(tablo: str):
    kaynak = _guard_kaynagi()
    for kalip in (f"delete({tablo}", f"DELETE FROM {tablo}", f"purge_{tablo}"):
        assert kalip not in kaynak, f"disk guard TIER1 gecmisine dokunuyor: {tablo}"


def test_DG07_aktif_FTP_config_dosyasi_SILINMEZ(tmp_path, monkeypatch):
    """FTP-T0 dosyalari (cihazin indirmeyi bekledigi) korunmali.

    Bunlari silmek sahadaki cihazi yapilandirmasiz birakir; disk baskisi
    boyle bir bedeli haklilastirmaz.
    """
    monkeypatch.setenv("FTP_ROOT", str(tmp_path))
    eski = time.time() - 30 * 24 * 3600  # cok eski — yas kriteri kurtarmasin

    aktif = []
    for ad in ("SN20_Configuration.csv", "SN20_DNP3_settings.bin", "SN20_Firmware.utf"):
        p = tmp_path / ad
        p.write_bytes(b"x" * 100)
        os.utime(p, (eski, eski))
        aktif.append(p)

    dg._temizle_ftp_bayat_gecici()

    for p in aktif:
        assert p.exists(), f"AKTIF FTP dosyasi silindi: {p.name}"


def test_DG08_yayinlanmamis_outbox_ASLA_silinmez():
    """`published=False` outbox satiri kalici veri kaybidir.

    Guard'in kullandigi purge, sorgusunda `published.is_(True)` sartini
    TASIMAK ZORUNDA.
    """
    import inspect

    from app.services import telemetry_retention

    kaynak = inspect.getsource(telemetry_retention.RetentionWorker.purge_outbox_events)
    assert "OutboxEvent.published.is_(True)" in kaynak, (
        "outbox purge yayinlanmamis satirlari da kapsayabilir"
    )


#: UYGULAMA SEVIYESI DEDUP PENCERESI — dondurulmus sozlesme.
#: Broker seviyesi `Nats-Msg-Id` yalnizca 120 saniye kapsar; 2 saatlik
#: uygulama penceresi onun UZERINE gelen bagimsiz katmandir.
NORMAL_APPLICATION_DEDUP_WINDOW_HOURS = 2


def test_DG09_disk_guard_dedup_penceresine_DOKUNMAZ():
    """`processed_messages` correctness durumudur; guard onu kisaltamaz.

    KALDIRILAN DAVRANIS: guard baski altinda pencereyi 1 saate cekiyordu.
    Bu, sistemin DOGRULUGUNU DISK DOLULUGUNA baglar — ayni message_id 90
    dakika sonra yeniden yayinlandiginda saglikli diskte dedup edilir,
    kritik diskte DUPLICATE uretilirdi.
    """
    import ast
    import inspect

    agac = ast.parse(inspect.getsource(dg))
    for dugum in ast.walk(agac):
        if not isinstance(dugum, ast.Call):
            continue
        ad = getattr(dugum.func, "attr", None) or getattr(dugum.func, "id", None)
        assert ad != "purge_processed_messages", (
            "disk guard idempotency defterini temizliyor — pencere disk "
            "durumuna bagli hale gelir ve duplicate telemetri uretilebilir"
        )

    kaynak = inspect.getsource(dg)
    assert "_AGGRESSIVE_PROCESSED_HOURS" not in kaynak, (
        "baski altinda kisaltilmis dedup penceresi sabiti geri gelmis"
    )

    # Yapilandirilmis deger dondurulmus sozlesmeyle ayni olmali.
    assert (
        settings.processed_messages_retention_hours
        == NORMAL_APPLICATION_DEDUP_WINDOW_HOURS
    )

    # Alt sinir kilidi (env'e 0 yazan operatore karsi) yerinde kalmali.
    from app.services import telemetry_retention as tr

    assert "REDELIVERY_WINDOW_SEC" in inspect.getsource(
        tr.RetentionWorker.purge_processed_messages
    )


def test_DG10_aktif_gateway_state_SILINMEZ():
    """Gateway state (komut defteri + outbox SQLite) guard tarafindan
    silinmez. Oksuz volume tespiti bile bu turda YALNIZCA gozlemdir."""
    kaynak = _guard_kaynagi()
    for kalip in ("gw-", "gateway_state", "command_ledger", "e1-gw-"):
        assert kalip not in kaynak, (
            f"disk guard gateway state'e dokunuyor: {kalip}"
        )


def test_DG11_docker_imaj_prune_yolu_YOK():
    """Guard hicbir Docker islemi yapmaz.

    Backend konteyneri `docker.sock` mount ETMIYOR; ustelik aktif/rollback
    imajlarini silmek sahayi geri donulemez birakirdi. Kaynakta docker
    cagrisi bulunmamali.
    """
    import ast
    import inspect

    from app.services import storage_snapshot

    # YORUMLARA DEGIL KODA BAK: modul aciklamalari "docker volume" gibi
    # ifadeler icerir ve icermeli. Olculen sey CALISTIRILABILIR yol:
    # alt surec baslatma yetenegi ve prune cagrisi.
    for modul in (dg, storage_snapshot):
        agac = ast.parse(inspect.getsource(modul))

        ice_aktarilan: set[str] = set()
        for dugum in ast.walk(agac):
            if isinstance(dugum, ast.Import):
                ice_aktarilan.update(a.name.split(".")[0] for a in dugum.names)
            elif isinstance(dugum, ast.ImportFrom) and dugum.module:
                ice_aktarilan.add(dugum.module.split(".")[0])

        assert "subprocess" not in ice_aktarilan, (
            f"{modul.__name__} alt surec baslatabiliyor — guard'in Docker'a "
            "(ya da baska bir kabuga) uzanan bir yolu olmamali"
        )

        # Kod icindeki string sabitlerinde docker/prune komutu aranir;
        # docstring'ler haric tutulur.
        docstringler = {
            ast.get_docstring(d)
            for d in ast.walk(agac)
            if isinstance(d, (ast.Module, ast.FunctionDef, ast.ClassDef))
        }
        for dugum in ast.walk(agac):
            if not isinstance(dugum, ast.Constant) or not isinstance(dugum.value, str):
                continue
            if dugum.value in docstringler:
                continue
            dusuk = dugum.value.lower()
            assert "prune" not in dusuk, f"{modul.__name__} prune cagrisi iceriyor"
            assert not dusuk.startswith("docker "), (
                f"{modul.__name__} docker komutu iceriyor"
            )


# ===========================================================================
# DG12-DG13 — IZIN VERILEN TEMIZLIK
# ===========================================================================


def test_DG12_harita_onbellegi_temizlenebilir(monkeypatch):
    """Yeniden uretilebilir: internetten tekrar iner, veri kaybi yok."""
    # GERCEK modul yamalanir. `sys.modules` uzerinden sahte modul koymak
    # kirilgandir: modul baska bir test tarafindan zaten import edilmisse
    # `from app.services import map_tile_service` paket ozniteliginden
    # cozulur ve sahte modulu HIC gormez (tek basina gecip tum pakette
    # dusen bir test bu yuzden olusmustu).
    from app.services import map_tile_service

    cagrildi = {"clear": False}
    monkeypatch.setattr(map_tile_service, "cache_size_bytes", lambda: 70 * 1024 * 1024)
    monkeypatch.setattr(
        map_tile_service, "clear_cache", lambda: cagrildi.__setitem__("clear", True)
    )
    sonuc = dg._temizle_harita_onbellegi()
    assert cagrildi["clear"] is True
    assert sonuc and "harita_onbellegi_temizlendi" in sonuc[0]


def test_DG13_bayat_FTP_gecici_dosyasi_temizlenir(tmp_path, monkeypatch):
    """FTP-T3: yarim transfer artiklari. YENI olanlara dokunulmaz."""
    monkeypatch.setenv("FTP_ROOT", str(tmp_path))

    bayat = tmp_path / "SN20_Configuration.csv.tmp"
    bayat.write_bytes(b"y" * 50)
    eski = time.time() - (settings.disk_guard_ftp_temp_stale_hours + 2) * 3600
    os.utime(bayat, (eski, eski))

    taze = tmp_path / ".tmp_SN21_Configuration.csv"
    taze.write_bytes(b"z" * 50)  # simdi yazildi — yazim SURUYOR olabilir

    dg._temizle_ftp_bayat_gecici()

    assert not bayat.exists(), "bayat gecici dosya silinmedi"
    assert taze.exists(), "SUREN bir yazimin gecici dosyasi silindi"


# ===========================================================================
# DG14-DG15, DG18 — GORUNURLUK
# ===========================================================================


def test_DG14_jetstream_depolama_metrikleri_gorunur(monkeypatch):
    """JetStream host disk kullanimi gorunur olmali — YALNIZCA gozlem."""
    from app.services import storage_snapshot as ss

    sahte = {
        "storage": 79 * 1024**2,
        "reserved_storage": 38 * GB,
        "account_details": [{"stream_detail": [
            {"name": "TELEMETRY_RAW", "state": {"bytes": 17 * 1024**2},
             "config": {"max_bytes": 24 * GB}},
        ]}],
    }
    monkeypatch.setattr(ss, "_ANLIK", {"at": 0.0, "data": None})

    class _Yanit:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): import json; return json.dumps(sahte).encode()

    monkeypatch.setattr(ss.urllib.request, "urlopen", lambda *a, **k: _Yanit())
    monkeypatch.setattr(ss.json, "load", lambda f: sahte)

    d = ss.jetstream_durumu()
    assert d["measured"] is True
    assert d["store_bytes"] == 79 * 1024**2
    assert d["configured_cap_bytes"] == 24 * GB
    assert d["streams"][0]["percent_of_cap"] is not None


def test_DG15_rabbitmq_disk_esigi_URUN_FARKINDA():
    """RabbitMQ'nun kendi disk alarmi Disk Guardian ACIL'inden ONCE calmali.

    Varsayilan 50 MB, 456 GB'lik diskte %99,99 dolulukta alarm demektir —
    Disk Guardian ACIL esiginden (%95) cok sonra, yani koruma olarak olu.
    """
    import re
    from pathlib import Path

    kok = Path(__file__).resolve().parents[3]
    conf = kok / "infra" / "rabbitmq" / "rabbitmq.conf"
    assert conf.is_file(), "rabbitmq.conf yok — urun-farkinda esik verilmemis"

    m = re.search(r"^disk_free_limit\.absolute\s*=\s*(\d+)\s*GB\s*$",
                  conf.read_text(encoding="utf-8"), re.M)
    assert m, "disk_free_limit.absolute GB cinsinden tanimlanmamis"
    limit_gb = int(m.group(1))

    assert limit_gb > settings.disk_guard_emergency_free_gb, (
        f"RabbitMQ alarmi ({limit_gb} GB) Disk Guardian ACIL esiginden "
        f"({settings.disk_guard_emergency_free_gb} GB) SONRA caliyor"
    )
    assert limit_gb <= settings.disk_guard_warning_free_gb, (
        "RabbitMQ alarmi uyari esiginden once caliyor — gereksiz erken blok"
    )


def test_DG15b_rabbitmq_metrikleri_snapshotta_var():
    import inspect

    from app.services import storage_snapshot as ss

    kaynak = inspect.getsource(ss)
    for alan in ("disk_free_bytes", "disk_free_limit_bytes", "disk_alarm"):
        assert alan in kaynak, f"RabbitMQ metrigi sunulmuyor: {alan}"


def test_DG18_inode_baskisi_gorunur_ve_seviye_YUKSELTIR():
    """Disk %7 dolu olsa bile inode tukenmesi seviyeyi yukseltmeli.

    Yalnizca bayta bakan bir guard bu tukenmeyi HIC gormez ve "yer var"
    derken yazma ENOSPC alir.
    """
    total = int(456 * GB)
    free = int(404 * GB)  # bayt ekseninde tamamen saglikli
    rezerv = dg.reserve_for(total)

    assert dg.classify(free, rezerv, total_bytes=total, inode_percent=3.0) == dg.LEVEL_OK
    assert dg.classify(free, rezerv, total_bytes=total, inode_percent=85.0) == dg.LEVEL_WARN
    assert dg.classify(free, rezerv, total_bytes=total, inode_percent=92.0) == dg.LEVEL_CRITICAL
    assert dg.classify(free, rezerv, total_bytes=total, inode_percent=97.0) == dg.LEVEL_EMERGENCY

    # Ve olculen deger disari sunulmali.
    assert "inode_percent" in _durum(free_gb=404.0).to_dict()


# ===========================================================================
# DG16 — OLAY HIZI
# ===========================================================================


def test_DG16_ayni_seviyede_olay_bastirilir_gecis_bastirilmaz(monkeypatch):
    """5 dakikalik tick, ayni seviyede gunde 288 olay yazmamali.

    Ama seviye DEGISTIGINDE olay HER ZAMAN yazilmali — bastirma bir gecisi
    gizlerse operatorun tek uyarisi kaybolur.
    """
    monkeypatch.setattr(settings, "disk_guard_event_cooldown_sec", 3600, raising=False)
    dg._son_olay["level"] = None
    dg._son_olay["at"] = 0.0

    assert dg._olay_yazilmali(dg.LEVEL_WARN, now=1000.0) is True
    dg._olay_isaretle(dg.LEVEL_WARN, now=1000.0)

    # Ayni seviye, cooldown dolmadan -> bastirilir
    assert dg._olay_yazilmali(dg.LEVEL_WARN, now=1300.0) is False
    # Seviye degisti -> HER ZAMAN yazilir
    assert dg._olay_yazilmali(dg.LEVEL_CRITICAL, now=1300.0) is True
    # Ayni seviye, cooldown doldu -> yazilir
    assert dg._olay_yazilmali(dg.LEVEL_WARN, now=1000.0 + 3601) is True


# ===========================================================================
# DG17 — YEDEK RETENTION SINIRI
# ===========================================================================


def test_DG17_backup_retention_sessizce_0_ya_da_sinirsiz_OLAMAZ():
    """`retention_count=0` "sinirsiz yedek" demek olurdu; disk dolar.

    API girdiyi 1'in altina indiremez ve `apply_retention` 0/negatif degeri
    NO-OP sayar (yani "hepsini sil" gibi davranmaz).
    """
    import inspect

    from app.api import backups as backups_api
    from app.services import backup_service

    api_kaynak = inspect.getsource(backups_api.update_schedule)
    assert "max(1, int(payload.retention_count))" in api_kaynak, (
        "API retention_count icin alt sinir uygulamiyor"
    )

    ret_kaynak = inspect.getsource(backup_service.apply_retention)
    assert "if retention_count <= 0:" in ret_kaynak and "return 0" in ret_kaynak, (
        "apply_retention 0/negatif degeri guvenli sekilde ele almiyor"
    )

    # Ve en yeni BASARILI yedek her kosulda korunur.
    purge_kaynak = inspect.getsource(backup_service._purge_failed_and_manual)
    assert "protected_id" in purge_kaynak


# ===========================================================================
# DG19 — DB TEMIZLIK YUZEYI TAM OLARAK BU (envanter kilidi)
# ===========================================================================

#: Disk guard'in dokunmasina IZIN VERILEN purge cagrilari. Liste bilincli
#: olarak KAPALI: yeni bir tablo temizlige eklenirse bu test kirmizi olur ve
#: karar gozden gecirilmek zorunda kalir.
#:
#: Her birinin neden guvenli oldugu:
#:   purge_telemetry          — her (cihaz, sinyal) icin EN YENI satir
#:                              `ROW_NUMBER() ... rn > 1` ile muaf; canli
#:                              ekran bosalmaz.
#:   purge_outbox_events      — YALNIZCA `published=True`; teslim edilmemis
#:                              olay ve dead-letter kaniti korunur.
#:   apply_retention          — yedek DOSYALARI + kayitlari; en yeni BASARILI
#:                              yedek her kosulda korunur.
_IZINLI_PURGE = {
    "purge_telemetry",
    "purge_outbox_events",
    "apply_retention",
}

#: Disk baskisiyla ASLA temizlenmemesi gereken, DG05/DG06'da adi gecmeyen
#: tablolar. Historian musterinin analiz verisidir; saklama otoritesi
#: TimescaleDB politikasidir, disk guard degil.
_GUARD_DISI_TABLOLAR = (
    "telemetry_latest",
    "telemetry_history",
    "telemetry_history_1m",
    "telemetry_history_1h",
    "unknown_device_telemetry",
)


def test_DG19_db_temizlik_yuzeyi_TAM_OLARAK_izinli_kume():
    """Guard'in cagirdigi purge kumesi genisletilemez.

    Envanterin sessizce buyumesi, bu incelemede cikarilan "hangi tabloya
    dokunuluyor" cevabini gecersiz kilardi.
    """
    import ast
    import inspect

    agac = ast.parse(inspect.getsource(dg))
    bulunan = set()
    for dugum in ast.walk(agac):
        if not isinstance(dugum, ast.Call):
            continue
        ad = getattr(dugum.func, "attr", None) or getattr(dugum.func, "id", None)
        if ad and ad.startswith(("purge_", "apply_retention")):
            bulunan.add(ad)

    fazla = bulunan - _IZINLI_PURGE
    assert not fazla, (
        f"disk guard temizlik yuzeyi genisledi: {sorted(fazla)} — "
        "her yeni tablo icin veri kaybi riski ayrica degerlendirilmeli"
    )


@pytest.mark.parametrize("tablo", _GUARD_DISI_TABLOLAR)
def test_DG19b_historian_ve_karantina_guard_disinda(tablo: str):
    """Historian saklama otoritesi TimescaleDB politikasidir.

    Guard bu tablolara ne `DELETE` ne `drop_chunks` uygulamali; acil
    seviyede bile karar operatorun/urunun olmali.
    """
    import inspect

    kaynak = inspect.getsource(dg)

    # OLCULEN SEY YIKICI YOL, TABLO ADININ GECMESI DEGIL.
    #
    # `tick()` acil seviyede operatore "historian retention'i bir URUN
    # karari olarak gozden gecirilmeli" diye yaziyor ve o mesajda tablo adi
    # GECMELI — bu tam da guard'in otomatik silmedigini soyleyen cumle.
    # Yasaklanan sey o tabloya giden DELETE / drop_chunks cagrisidir.
    for kalip in (
        f"DELETE FROM {tablo}",
        f"delete({tablo}",
        f"drop_chunks('{tablo}'",
        f'drop_chunks("{tablo}"',
    ):
        assert kalip not in kaynak, (
            f"disk guard {tablo} uzerinde yikici islem yapiyor: {kalip} — "
            "historian/karantina saklama otoritesi guard DEGIL"
        )
    assert "drop_chunks" not in kaynak, (
        "disk guard drop_chunks cagiriyor — saklama otoritesi TimescaleDB "
        "politikasi olmali"
    )


# ===========================================================================
# DG20 — DEDUP PENCERESI HER SEVIYEDE AYNI
# ===========================================================================


@pytest.mark.parametrize(
    "seviye,free_gb",
    [
        ("NORMAL", 404.0),
        ("WARNING", 60.0),
        ("CRITICAL", 30.0),
        ("EMERGENCY", 5.0),
    ],
)
def test_DG20_processed_messages_penceresi_seviyeden_BAGIMSIZ(
    monkeypatch, seviye: str, free_gb: float
):
    """Dort seviyede de efektif dedup penceresi AYNI (2 saat) olmali.

    Guard'in temizlik hattini gercekten kostururuz; `RetentionWorker`in
    `purge_processed_messages` metodu cagrilirsa hangi pencereyle
    cagrildigini yakalariz. Beklenen: HIC CAGRILMAMASI.
    """
    from app.services import telemetry_retention as tr

    cagrilar: list[object] = []

    def _yakala(self, *, retention_hours=None):  # noqa: ANN001, ANN202
        cagrilar.append(retention_hours)
        return 0

    monkeypatch.setattr(tr.RetentionWorker, "purge_processed_messages", _yakala)
    monkeypatch.setattr(tr.RetentionWorker, "purge_telemetry",
                        lambda self, **kw: 0)
    monkeypatch.setattr(tr.RetentionWorker, "purge_outbox_events",
                        lambda self, **kw: 0)
    monkeypatch.setattr(dg, "_temizle_ftp_bayat_gecici", lambda: [])
    monkeypatch.setattr(dg, "_temizle_harita_onbellegi", lambda: [])
    monkeypatch.setattr(dg, "_relieve_emergency", lambda: [])
    monkeypatch.setattr(dg, "_record", lambda s: None)

    durum = _durum(free_gb=free_gb)
    _tick_ile(monkeypatch, durum)

    assert cagrilar == [], (
        f"{seviye} seviyesinde disk guard dedup penceresine dokundu "
        f"(cagri: {cagrilar}) — pencere disk durumuna baglanamaz"
    )

    # Efektif pencere her seviyede yapilandirilmis deger olarak kalir.
    assert (
        settings.processed_messages_retention_hours
        == NORMAL_APPLICATION_DEDUP_WINDOW_HOURS
    ), f"{seviye}: efektif dedup penceresi 2 saatten farkli"


def test_DG20b_J9_gecikmeli_duplicate_sozlesmesi():
    """J9 paritesi: 90 dakika gecikmeli duplicate disk seviyesinden BAGIMSIZ
    olarak dedup penceresi ICINDE kalmali.

    JetStream dedup zinciri (D ajani, dondurulmus):
        30 sn duplicate  -> Nats-Msg-Id (120 sn broker penceresi)
        5 dk duplicate   -> processed_messages (uygulama penceresi)
        3 saat duplicate -> duplicate KABUL EDILEN sinir

    90 dakika, broker penceresinin (2 dk) COK disinda ama uygulama
    penceresinin (2 saat) ICINDE. Yani bu senaryoda dedup'i saglayan TEK
    katman `processed_messages`tir. Guard onu 1 saate cekseydi bu senaryo
    kritik diskte DUPLICATE uretirdi.

    Bu test gercek NATS calistirmaz — sozlesme aritmetigini kilitler.
    """
    gecikme_dk = 90
    pencere_dk = NORMAL_APPLICATION_DEDUP_WINDOW_HOURS * 60

    assert gecikme_dk < pencere_dk, (
        "90 dakikalik duplicate uygulama dedup penceresinin disinda kaliyor"
    )

    # Broker penceresi tek basina YETMEZ — bu yuzden uygulama katmani sart.
    broker_dk = 120 / 60
    assert gecikme_dk > broker_dk, (
        "senaryo broker penceresiyle zaten cozuluyorsa J9 paritesi anlamsiz"
    )

    # Ve guard bu pencereyi hicbir seviyede kisaltamaz (bkz. DG09/DG20).
    import inspect

    assert "purge_processed_messages" not in inspect.getsource(dg)
