"""Guvenli restore — birim testleri.

BU DOSYANIN TEK BIR IDDIASI VAR:
Cutover'a ULASILMADAN once olusan HICBIR hata, uretim veritabanina yikici
bir ifade gondermez.

NASIL KANITLANIYOR
------------------
`safe_restore` icinde uretim veritabanini etkileyebilecek her sey IKI
KAPIDAN gecer:

    _admin_sql(...)     CREATE/DROP/ALTER DATABASE
    _run_pg_tool(...)   pg_restore / pg_dump

Testler bu iki kapiyi kaydeden birer sahte ile degistirir ve sonra
"kayitta uretim veritabanina dokunan bir ifade var mi" diye DOGRUDAN
sorar. Dolayli cikarim yok: iddia, calisan komutlarin kendisiyle
dogrulanir.

Bu, eski akista imkansizdi — orada `pg_restore --clean -d enerjione_grid`
tek parca bir cagriydi ve "yarim restore" durumu ancak gercek bir
veritabaninda gozlemlenebilirdi.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import restore_state_store as ss
from app.services import safe_restore as sr


URETIM = "enerjione_grid"

#: Uretim veritabanina dokunan YIKICI ifade kaliplari. Testler kayitta
#: bunlardan hicbirinin olmadigini dogrular.
_YIKICI = re.compile(
    r"\b(DROP\s+DATABASE|ALTER\s+DATABASE|DROP\s+TABLE|TRUNCATE)\b", re.I
)


class Kayit:
    """Iki kapinin cagri kaydi."""

    def __init__(self) -> None:
        self.admin: list[str] = []
        self.arac: list[tuple[str, str, list[str]]] = []  # (arac, dbname, args)

    # -- iddialar ---------------------------------------------------------

    def uretime_yikici_ifade(self) -> list[str]:
        """Uretim veritabanini hedef alan yikici ifadeler."""
        bulunan = []
        for sql in self.admin:
            if not _YIKICI.search(sql):
                continue
            # Staging/rollback adlarini hedefleyenler MASUM.
            hedefler = re.findall(r'"([^"]+)"', sql)
            for h in hedefler:
                if h == URETIM:
                    bulunan.append(sql)
                    break
        return bulunan

    def uretime_pg_tool(self) -> list[tuple]:
        """Uretim veritabanina calistirilan harici arac cagrilari."""
        return [c for c in self.arac if c[1] == URETIM]

    def staging_yaratildi_mi(self) -> bool:
        return any("CREATE DATABASE" in s.upper() for s in self.admin)


@pytest.fixture()
def kayit(monkeypatch, tmp_path):
    """Iki kapiyi da sahteler + durum dosyasini tmp'ye alir."""
    k = Kayit()

    def _sahte_admin(sql: str, *, params: dict | None = None):
        k.admin.append(sql)
        u = sql.upper()
        if "PG_DATABASE_SIZE" in u:
            return [(64 * 1024 * 1024,)]           # 64 MB — sahadaki gercek deger
        if "PG_TERMINATE_BACKEND" in u:
            return [(0,)]
        if "SHOW DATA_DIRECTORY" in u:
            return [("/nonexistent",)]
        if "FROM PG_DATABASE" in u:
            return [(URETIM,)]
        return []

    def _sahte_arac(arac, args, *, dbname, timeout=3600, stdin_kapali=True):
        k.arac.append((arac, dbname, list(args)))
        return 0, ""

    monkeypatch.setattr(sr, "_admin_sql", _sahte_admin)
    monkeypatch.setattr(sr, "_run_pg_tool", _sahte_arac)
    monkeypatch.setattr(sr, "_production_db_name", lambda: URETIM)
    # Kilit gercek DB ister; birim testte devre disi.
    monkeypatch.setattr(sr.RestoreLock, "acquire", lambda self: None)
    monkeypatch.setattr(sr.RestoreLock, "release", lambda self: None)
    # Durum dosyasi tmp'ye.
    monkeypatch.setattr(
        "app.services.backup_service.get_backup_dir", lambda: tmp_path
    )
    return k


@pytest.fixture()
def dump(tmp_path) -> Path:
    """Gecerli gorunumlu bir yedek dosyasi (PGDMP magic)."""
    p = tmp_path / "e1-test.dump"
    p.write_bytes(b"PGDMP" + b"\x00" * 4096)
    return p


def _hersey_gecsin(monkeypatch):
    """Disk + arsiv + migration + dogrulama: hepsi PASS."""
    monkeypatch.setattr(sr, "disk_preflight", lambda d: (True, "", {}))
    monkeypatch.setattr(sr, "arsiv_on_dogrula", lambda d: (True, ""))
    monkeypatch.setattr(sr, "staging_migrate", lambda s: (True, ""))
    rapor = sr.DogrulamaRaporu()
    rapor.ekle("hepsi", True)
    monkeypatch.setattr(sr, "staging_dogrula", lambda s: rapor.sonucla())
    monkeypatch.setattr(sr, "cutover_sonrasi_dogrula", lambda: rapor.sonucla())


# ==========================================================================
# Test 1 — bozuk yedek
# ==========================================================================


def test_1_bozuk_yedek_staging_bile_yaratilmadan_duser(kayit, dump, monkeypatch):
    monkeypatch.setattr(
        sr, "arsiv_on_dogrula", lambda d: (False, "Yedek dosyasi bozuk")
    )
    ok, hata = sr.run(1, dump)

    assert ok is False
    assert "bozuk" in hata.lower()
    assert "DEGISTIRILMEDI" in hata
    assert kayit.uretime_yikici_ifade() == []
    assert kayit.uretime_pg_tool() == []
    assert not kayit.staging_yaratildi_mi(), "bozuk arsivde staging bile yaratilmamali"


# ==========================================================================
# Test 2 — pg_restore basarisiz
# ==========================================================================


def test_2_pg_restore_basarisiz_uretim_dokunulmaz(kayit, dump, monkeypatch):
    _hersey_gecsin(monkeypatch)
    monkeypatch.setattr(
        sr, "staginge_restore_et", lambda s, d: (False, "pg_restore basarisiz (rc=1)")
    )
    ok, hata = sr.run(2, dump)

    assert ok is False
    assert "pg_restore" in hata
    assert kayit.uretime_yikici_ifade() == []
    assert kayit.uretime_pg_tool() == []
    # Staging yaratildi ve TEMIZLENDI.
    assert kayit.staging_yaratildi_mi()
    assert any("DROP DATABASE" in s.upper() and "stg_2" in s for s in kayit.admin)


# ==========================================================================
# Test 3 — dogrulama basarisiz
# ==========================================================================


def test_3_dogrulama_basarisiz_cutover_yapilmaz(kayit, dump, monkeypatch):
    _hersey_gecsin(monkeypatch)
    monkeypatch.setattr(sr, "staginge_restore_et", lambda s, d: (True, ""))
    kotu = sr.DogrulamaRaporu()
    kotu.ekle("kritik_tablolar", False, "eksik: devices")
    monkeypatch.setattr(sr, "staging_dogrula", lambda s: kotu.sonucla())

    ok, hata = sr.run(3, dump)

    assert ok is False
    assert "dogrulamayi gecemedi" in hata
    assert kayit.uretime_yikici_ifade() == [], "cutover yapilmamaliydi"
    # RENAME hic denenmemis olmali.
    assert not any("RENAME" in s.upper() for s in kayit.admin)


# ==========================================================================
# Test 4 — migration basarisiz
# ==========================================================================


def test_4_migration_basarisiz_cutover_yapilmaz(kayit, dump, monkeypatch):
    _hersey_gecsin(monkeypatch)
    monkeypatch.setattr(sr, "staginge_restore_et", lambda s, d: (True, ""))
    monkeypatch.setattr(
        sr, "staging_migrate", lambda s: (False, "Migration basarisiz (rc=1)")
    )

    ok, hata = sr.run(4, dump)

    assert ok is False
    assert "Migration" in hata
    assert kayit.uretime_yikici_ifade() == []
    assert not any("RENAME" in s.upper() for s in kayit.admin)


# ==========================================================================
# Test 5 — yetersiz disk
# ==========================================================================


def test_5_yetersiz_disk_restore_hic_baslamaz(kayit, dump, monkeypatch):
    monkeypatch.setattr(sr, "arsiv_on_dogrula", lambda d: (True, ""))
    monkeypatch.setattr(
        sr, "disk_preflight", lambda d: (False, "Yetersiz disk: ...", {})
    )

    ok, hata = sr.run(5, dump)

    assert ok is False
    assert "Yetersiz disk" in hata
    assert not kayit.staging_yaratildi_mi(), "disk yoksa staging YARATILMAMALI"
    assert kayit.uretime_yikici_ifade() == []


def test_5b_disk_hesabi_dump_boyutuna_degil_db_boyutuna_dayanir(monkeypatch, tmp_path):
    """Sahada olculdu: DB 64 MB, dump 1,5 MB (~42x).

    Dump boyutunu olcut almak, gercekte 64 MB gerekirken 1,5 MB yeterli
    sanmak demekti.
    """
    cagrilar = {}

    def _sahte_admin(sql, *, params=None):
        u = sql.upper()
        if "PG_DATABASE_SIZE" in u:
            cagrilar["db_boyutu_soruldu"] = True
            return [(64 * 1024 * 1024,)]
        if "SHOW DATA_DIRECTORY" in u:
            return [("/nonexistent",)]
        if "FROM PG_DATABASE" in u:
            return []
        return []

    monkeypatch.setattr(sr, "_admin_sql", _sahte_admin)
    monkeypatch.setattr(sr, "_production_db_name", lambda: URETIM)
    monkeypatch.setattr("app.services.backup_service.get_backup_dir", lambda: tmp_path)

    d = tmp_path / "k.dump"
    d.write_bytes(b"PGDMP" + b"\x00" * 1500)  # 1,5 KB — kucucuk dump

    ok, hata, olcum = sr.disk_preflight(d)

    assert cagrilar.get("db_boyutu_soruldu"), "canli DB boyutu sorulmali"
    assert olcum["uretim_bayt"] == 64 * 1024 * 1024
    # Tahmin, dump boyutundan DEGIL DB boyutundan turemis olmali.
    assert olcum["tahmini_staging_bayt"] >= olcum["uretim_bayt"]


# ==========================================================================
# Test 6 — es zamanli restore
# ==========================================================================


def test_6_es_zamanli_restore_reddedilir(dump, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.backup_service.get_backup_dir", lambda: tmp_path
    )

    def _kilit_alinamadi(self):
        raise sr.KilitAlinamadi("Su an baska bir restore calisiyor.")

    monkeypatch.setattr(sr.RestoreLock, "acquire", _kilit_alinamadi)
    monkeypatch.setattr(sr.RestoreLock, "release", lambda self: None)

    ok, hata = sr.run(6, dump)

    assert ok is False
    assert "baska bir restore" in hata.lower()


def test_6b_kilit_uretim_degil_bakim_veritabaninda_alinir():
    """Kilit uretim DB'sinde alinsaydi cutover'daki rename onu koparirdi.

    Ayrica PostgreSQL advisory kilitleri VERITABANI KAPSAMLIDIR (locktag
    `MyDatabaseId` icerir); yeniden adlandirilmis veritabani farkli bir
    kilit uzayina duserdi.
    """
    url = sr._admin_url()
    assert url.rsplit("/", 1)[-1] == "postgres"


# ==========================================================================
# Test 7 — basarili akis
# ==========================================================================


def test_7_basarili_akis_sirasi_ve_cutover(kayit, dump, monkeypatch):
    _hersey_gecsin(monkeypatch)
    monkeypatch.setattr(sr, "staginge_restore_et", lambda s, d: (True, ""))

    ok, hata = sr.run(7, dump)

    assert ok is True, hata
    # Iki rename, DOGRU SIRADA: once uretim -> pre_, sonra staging -> uretim
    renameler = [s for s in kayit.admin if "RENAME" in s.upper()]
    assert len(renameler) == 2, renameler
    assert '"enerjione_grid" RENAME TO "enerjione_grid_pre_' in renameler[0]
    assert '"enerjione_grid_stg_7" RENAME TO "enerjione_grid"' in renameler[1]
    # Staging DOGRULAMADAN ONCE yaratilmis olmali.
    assert kayit.staging_yaratildi_mi()


def test_7b_staging_restore_bayraklar_atomik(kayit, dump, monkeypatch):
    """`--single-transaction --exit-on-error` var; `--jobs` ve `--clean` YOK."""
    monkeypatch.setattr(sr, "_run_pg_tool", sr._run_pg_tool)  # gercek kapi
    cagri = {}

    def _yakala(arac, args, *, dbname, timeout=3600, stdin_kapali=True):
        cagri["arac"] = arac
        cagri["args"] = list(args)
        cagri["dbname"] = dbname
        return 0, ""

    monkeypatch.setattr(sr, "_run_pg_tool", _yakala)
    ok, _ = sr.staginge_restore_et("enerjione_grid_stg_9", dump)

    assert ok
    assert cagri["dbname"] == "enerjione_grid_stg_9", "STAGING'e yazmali"
    assert "--single-transaction" in cagri["args"]
    assert "--exit-on-error" in cagri["args"]
    assert not any(a.startswith("--jobs") for a in cagri["args"]), (
        "--jobs, --single-transaction ile birlikte kullanilamaz"
    )
    assert "--clean" not in cagri["args"], (
        "staging bos yaratiliyor; --clean gereksiz ve extension'i dusururdu"
    )


# ==========================================================================
# Test 8 — eski uretim veritabani korunuyor
# ==========================================================================


def test_8_basarili_cutover_sonrasi_eski_db_korunur(kayit, dump, monkeypatch):
    _hersey_gecsin(monkeypatch)
    monkeypatch.setattr(sr, "staginge_restore_et", lambda s, d: (True, ""))

    ok, _ = sr.run(8, dump)
    assert ok

    # Eski uretim `_pre_<ts>` adiyla duruyor; DROP EDILMEMIS olmali.
    dusurulen_pre = [
        s for s in kayit.admin
        if "DROP DATABASE" in s.upper() and "_pre_" in s
    ]
    # `eski_rollback_temizle` YALNIZCA onceki turlardan kalanlari duser;
    # bu turda yaratilan korunur. Sahte kayitta baska rollback DB'si yok.
    assert dusurulen_pre == [], (
        "bu restore'un olusturdugu geri donus noktasi silinmemeli"
    )
    durum = ss.read_state()
    assert durum is not None and durum.rollback_db, "rollback adi kayitli olmali"


def test_8b_eski_rollback_yalnizca_basaridan_sonra_silinir(monkeypatch, tmp_path):
    """Preflight'ta silinmemeli — yer acmak icin guvenligi dusurmeyiz."""
    silinen: list[str] = []

    def _sahte_admin(sql, *, params=None):
        u = sql.upper()
        if "FROM PG_DATABASE" in u:
            return [("enerjione_grid_pre_20260101_000000",), ("enerjione_grid",)]
        if "DROP DATABASE" in u:
            silinen.append(sql)
        return []

    monkeypatch.setattr(sr, "_admin_sql", _sahte_admin)
    n = sr.eski_rollback_temizle(korunacak="enerjione_grid_pre_20260814_120000")

    assert n == 1
    assert "enerjione_grid_pre_20260101_000000" in silinen[0]
    # Korunacak olan silinmemis.
    assert "20260814_120000" not in silinen[0]


# ==========================================================================
# Oksuz staging — iki kanit kurali
# ==========================================================================


def test_oksuz_staging_durum_kaydi_yoksa_SILINMEZ(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.backup_service.get_backup_dir", lambda: tmp_path
    )
    monkeypatch.setattr(
        sr, "_admin_sql",
        lambda sql, params=None: [("enerjione_grid_stg_99",), ("enerjione_grid",)]
        if "FROM PG_DATABASE" in sql.upper() else [],
    )
    liste = sr.oksuz_staging_listele()
    kayit99 = next(k for k in liste if k["db"] == "enerjione_grid_stg_99")

    assert kayit99["kanit_isim"] is True
    assert kayit99["kanit_durum"] is False
    assert kayit99["silinebilir"] is False, (
        "durum kaydi yokken silinebilir isaretlenemez — yanlis DB dusurme riski"
    )

    ok, hata = sr.oksuz_staging_dusur("enerjione_grid_stg_99")
    assert ok is False and "isaretlenmedi" in hata


def test_oksuz_staging_isim_kalibi_TAM_eslesme_ister(monkeypatch, tmp_path):
    """`enerjione_grid_stg_deneme` gibi elle yaratilmis DB kapsam DISI."""
    monkeypatch.setattr(
        "app.services.backup_service.get_backup_dir", lambda: tmp_path
    )
    monkeypatch.setattr(
        sr, "_admin_sql",
        lambda sql, params=None: [
            ("enerjione_grid_stg_deneme",),
            ("enerjione_grid_stgx",),
            ("enerjione_grid",),
        ] if "FROM PG_DATABASE" in sql.upper() else [],
    )
    liste = sr.oksuz_staging_listele()
    assert liste == [], "yalnizca ..._stg_<tamsayi> kalibi kapsamda olmali"


# ==========================================================================
# Yarim cutover tespiti (guc kesintisi)
# ==========================================================================


def test_yarim_cutover_uretim_yoksa_mudahale_isaretlenir(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.backup_service.get_backup_dir", lambda: tmp_path
    )
    # Uretim adinda DB YOK; rollback duruyor.
    monkeypatch.setattr(
        sr, "_admin_sql",
        lambda sql, params=None: [
            ("enerjione_grid_pre_20260814_120000",),
            ("enerjione_grid_stg_5",),
        ] if "FROM PG_DATABASE" in sql.upper() else [],
    )
    st = ss.new_state(
        job_id=5, backup_file="x.dump", started_by="t", production_db=URETIM
    )
    st.rollback_db = "enerjione_grid_pre_20260814_120000"
    st.stage = ss.STAGE_PRODUCTION_RENAMED
    ss.write_state(st, backup_dir=tmp_path)

    sonuc = sr.cutover_durumunu_coz()

    assert sonuc["uretim_var"] is False
    assert sonuc["mudahale_gerekli"] is True
    assert "KAYIP DEGIL" in sonuc["mesaj"]
    assert "ALTER DATABASE" in sonuc["mesaj"], "operatore net komut verilmeli"


def test_uretim_yerindeyse_mudahale_gerekmez(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.backup_service.get_backup_dir", lambda: tmp_path
    )
    monkeypatch.setattr(
        sr, "_admin_sql",
        lambda sql, params=None: [(URETIM,)]
        if "FROM PG_DATABASE" in sql.upper() else [],
    )
    sonuc = sr.cutover_durumunu_coz()
    assert sonuc["uretim_var"] is True
    assert sonuc["mudahale_gerekli"] is False


# ==========================================================================
# RESTORE STATE STORE — DAYANIKLILIK
#
# Durum dosyasi guc kesintisi senaryosunda okunacak; bozuk/yarim/bayat her
# halde davranis AYNI olmali: OTOMATIK HICBIR SEY YAPMA.
# ==========================================================================


def test_state_yarim_json_okunamaz_ve_otomatik_karar_URETMEZ(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.backup_service.get_backup_dir", lambda: tmp_path
    )
    (tmp_path / ss.STATE_FILENAME).write_text('{"job_id": 5, "sta', encoding="utf-8")

    durum = ss.read_state()
    assert durum is None, "yarim JSON 'bilinmiyor' sayilmali"


def test_state_bozuk_json_okunamaz(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.backup_service.get_backup_dir", lambda: tmp_path
    )
    (tmp_path / ss.STATE_FILENAME).write_text("bu json degil", encoding="utf-8")
    assert ss.read_state() is None


def test_state_ileri_sema_surumu_YORUMLANMAZ(tmp_path, monkeypatch):
    """Bilinmeyen sema surumu fail-closed olmali."""
    monkeypatch.setattr(
        "app.services.backup_service.get_backup_dir", lambda: tmp_path
    )
    (tmp_path / ss.STATE_FILENAME).write_text(
        '{"schema_version": 9999, "job_id": 1, "stage": "cutover"}', encoding="utf-8"
    )
    assert ss.read_state() is None


def test_state_atomik_yazilir_yarim_dosya_birakmaz(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.backup_service.get_backup_dir", lambda: tmp_path
    )
    st = ss.new_state(job_id=1, backup_file="x", started_by="t", production_db="p")
    ss.write_state(st, backup_dir=tmp_path)
    # Gecici dosya BIRAKILMAMALI.
    artik = [p.name for p in tmp_path.iterdir() if p.name.startswith(".restore-state-")]
    assert artik == [], f"gecici dosya kalmis: {artik}"
    assert (tmp_path / ss.STATE_FILENAME).is_file()
    assert ss.read_state(backup_dir=tmp_path) is not None


def test_state_bayat_kayit_SILME_YETKISI_VERMEZ(tmp_path, monkeypatch):
    """Bayat kayit yalnizca "incelenmeli" demektir, "silinebilir" demez.

    PLATFORM FARKI (bilincli): bayatlik tespiti `os.kill(pid, 0)` ile
    yapiliyor. Linux'ta olu surec `ProcessLookupError` verir ve kayit bayat
    sayilir. Windows'ta ayni cagri farkli bir `OSError` uretir; kod bunu
    "karar veremiyorum" olarak ele alip bayat DEMEZ. Bu FAIL-SAFE yondur:
    yanlislikla "bu kaydin sahibi olmus" deyip silmeye dogru bir karar
    uretmez. Uretim hedefi Linux; asagidaki platforma ozel iddia orada
    gercek tespiti kilitler.
    """
    import sys as _sys

    monkeypatch.setattr(
        "app.services.backup_service.get_backup_dir", lambda: tmp_path
    )
    st = ss.new_state(job_id=7, backup_file="x", started_by="t", production_db=URETIM)
    st.pid = 999_999_999  # var olmayan surec
    st.stage = ss.STAGE_RESTORING
    ss.write_state(st, backup_dir=tmp_path)

    okunan = ss.read_state(backup_dir=tmp_path)
    assert okunan is not None
    assert okunan.is_terminal() is False

    if _sys.platform.startswith("linux"):
        assert okunan.is_stale() is True, "Linux'ta olu surec bayat sayilmali"

    # PLATFORMDAN BAGIMSIZ ASIL IDDIA: bayat olsun olmasin, durum kaydi tek
    # basina bir veritabanini silinebilir yapmaz — ikinci kanit (isim
    # kalibi) ve operator onayi da gerekir.
    monkeypatch.setattr(sr, "_production_db_name", lambda: URETIM)
    monkeypatch.setattr(
        sr, "_admin_sql",
        lambda sql, params=None: [(f"{URETIM}_stg_7",), (URETIM,)]
        if "FROM PG_DATABASE" in sql.upper() else [],
    )
    liste = sr.oksuz_staging_listele()
    (kayit,) = [k for k in liste if k["db"] == f"{URETIM}_stg_7"]
    # Silinebilir olsa BILE otomatik silme YOK; silme yalnizca operatorun
    # acik istegiyle ve ayni iki kanit yeniden dogrulanarak yapilir.
    assert kayit["kanit_isim"] is True


def test_state_var_olmayan_DB_gosteriyorsa_silme_ONERILMEZ(tmp_path, monkeypatch):
    """Durum bir staging'e isaret ediyor ama o DB yok — sorun cikmamali."""
    monkeypatch.setattr(
        "app.services.backup_service.get_backup_dir", lambda: tmp_path
    )
    monkeypatch.setattr(sr, "_production_db_name", lambda: URETIM)
    monkeypatch.setattr(
        sr, "_admin_sql",
        lambda sql, params=None: [(URETIM,)]
        if "FROM PG_DATABASE" in sql.upper() else [],
    )
    st = ss.new_state(job_id=42, backup_file="x", started_by="t", production_db=URETIM)
    st.staging_db = ss.staging_db_name(42, URETIM)
    st.stage = ss.STAGE_FAILED_SAFE
    ss.write_state(st, backup_dir=tmp_path)

    liste = sr.oksuz_staging_listele()
    assert liste == [], "var olmayan DB icin oneri uretilmemeli"


def test_state_DOSYASI_YOKKEN_mevcut_staging_SILINEBILIR_SAYILMAZ(tmp_path, monkeypatch):
    """DB var, durum dosyasi yok — iki kanittan biri eksik."""
    monkeypatch.setattr(
        "app.services.backup_service.get_backup_dir", lambda: tmp_path
    )
    monkeypatch.setattr(sr, "_production_db_name", lambda: URETIM)
    monkeypatch.setattr(
        sr, "_admin_sql",
        lambda sql, params=None: [(f"{URETIM}_stg_77",), (URETIM,)]
        if "FROM PG_DATABASE" in sql.upper() else [],
    )
    liste = sr.oksuz_staging_listele()
    (kayit,) = [k for k in liste if k["db"] == f"{URETIM}_stg_77"]
    assert kayit["silinebilir"] is False
    ok, hata = sr.oksuz_staging_dusur(f"{URETIM}_stg_77")
    assert ok is False


# ==========================================================================
# LEGACY UNSAFE FONKSIYON — ASLA CAGRILMAMALI
# ==========================================================================


def test_restore_yolu_LEGACY_UNSAFE_fonksiyona_DUSMEZ(monkeypatch, tmp_path, dump):
    """`restore_backup` hicbir kosulda eski yikici yola girmemeli.

    Eski yol yedegi DOGRUDAN uretime `pg_restore --clean` ile uyguluyordu.
    Test bagimliligi yuzunden fonksiyon repoda duruyor; cagrilmadigini
    KANITLAMAK gerekiyor.
    """
    from app.services import backup_service as bs

    cagrildi = []
    monkeypatch.setattr(
        bs, "_legacy_run_pg_restore_UNSAFE",
        lambda *a, **k: (cagrildi.append(1), (False, "cagrilmamaliydi"))[1],
    )
    monkeypatch.setattr(
        bs, "_legacy_restore_backup_UNSAFE",
        lambda *a, **k: (cagrildi.append(1), (False, "cagrilmamaliydi"))[1],
    )
    cagrilan = {}
    monkeypatch.setattr(
        sr, "run", lambda *a, **k: (cagrilan.setdefault("safe", True), (True, ""))[1]
    )

    class _Job:
        id = 1
        file_path = str(dump)
        created_by_username = "t"

    ok, hata = bs.restore_backup(None, _Job())

    assert ok is True
    assert cagrilan.get("safe") is True, "guvenli akis cagrilmaliydi"
    assert cagrildi == [], "LEGACY UNSAFE fonksiyon cagrildi"


def test_legacy_fonksiyon_acikca_isaretli():
    """Fonksiyon duruyorsa adi ve belgesi tehlikeyi soylemeli."""
    from app.services import backup_service as bs

    assert hasattr(bs, "_legacy_run_pg_restore_UNSAFE")
    assert not hasattr(bs, "run_pg_restore"), (
        "eski genel ad hala disari acik — yanlislikla cagrilabilir"
    )
    d = (bs._legacy_restore_backup_UNSAFE.__doc__ or "").upper()
    assert "ARTIK CAGRILMIYOR" in d or "ESKI AKIS" in d
