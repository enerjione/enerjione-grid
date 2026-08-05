"""FTP config yonetimi — ayarlar, gomulu yazma, cihazdan yutma, yoklama.

Odak, sahada geri donusu pahali hatalar:

  - Parolanin DB'ye ACIK yazilmasi (sifreli saklanmali)
  - Uretilen parolanin cihaz ekranina SIGMAMASI ya da okunamaz karakter
    icermesi (her yanlis okunan karakter bir saha ziyareti)
  - Config'in cihazin BAKMADIGI dizine yazilmasi ("komut gitti, bir sey
    olmadi" olarak ortaya cikar)
  - Ayni dosyanin her yoklamada YENI surum uretmesi (gecmisin collenmesi)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.device import Device
from app.models.telemetry_latest import TelemetryLatest
from app.services import device_config_service as cfg_svc
from app.services import ftp_client_service, ftp_settings_service
from app.services.horstmann_config_codec import MARKER, calculate_checksum


def _dosya(dial_in: int = 1440) -> bytes:
    govde = (
        b"\r\n".join(
            [
                b"2010,C6,02," + dial_in.to_bytes(2, "little").hex().upper().encode(),
                b"1202,02,00",
            ]
        )
        + b"\r\n"
    )
    return govde + calculate_checksum(govde).to_bytes(2, "little") + MARKER


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def cihaz(db):
    d = Device(
        code="SN20", name="SN20", model="horstmann_sn_2_0",
        ip_address="192.168.1.10", latitude=0.0, longitude=0.0,
    )
    db.add(d)
    db.flush()
    db.add(
        TelemetryLatest(
            device_id=d.id,
            signal_key=cfg_svc.SERIAL_SIGNAL,
            value=50984.0,
            source_timestamp=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.flush()
    return d


# --- ayarlar ---------------------------------------------------------------
def test_ayarlar_singleton_ve_varsayilanlar(db) -> None:
    a = ftp_settings_service.get_settings(db)
    assert a.id == 1
    assert a.mode == "gomulu"
    assert a.username == "device"
    # Ikinci cagri AYNI satiri dondurmeli, yenisini yaratmamali.
    assert ftp_settings_service.get_settings(db).id == a.id


def test_parola_DB_YE_ACIK_YAZILMAZ(db) -> None:
    ftp_settings_service.update_settings(db, updates={"password": "GizliParola7"})
    satir = ftp_settings_service.get_settings(db)
    assert satir.password_enc != "GizliParola7"
    assert satir.password_enc.startswith("enc:v1:")
    # Ama okundugunda acik metin geri gelmeli (cihaz ekranina girilecek).
    assert ftp_settings_service.get_password(satir) == "GizliParola7"


def test_bos_parola_MEVCUDU_SILMEZ(db) -> None:
    """PUT'ta parola alaninin bos gelmesi 'dokunma' demektir, 'sil' degil."""
    ftp_settings_service.update_settings(db, updates={"password": "GizliParola7"})
    ftp_settings_service.update_settings(db, updates={"password": None, "port": 2121})
    satir = ftp_settings_service.get_settings(db)
    assert ftp_settings_service.get_password(satir) == "GizliParola7"
    assert satir.port == 2121


def test_harici_kimlik_DAHILI_sunucuya_SIZMAZ(db) -> None:
    """SAHADA YASANDI (2026-08-05): kullanici harici modu yapilandirinca
    musteri sunucusunun kimligi dahili sunucuya da geciyordu — cihazlar ve
    kullanici bir anda 'device' ile giremez olmustu. Harici alanlari
    guncellemek dahili kimlige ASLA dokunmamali."""
    ftp_settings_service.update_settings(
        db, updates={"embedded_password": "DahiliParola9"}
    )
    ftp_settings_service.update_settings(
        db,
        updates={
            "mode": "harici",
            "host": "77.83.37.44",
            "username": "ENERJIONE",
            "password": "MusteriParola1",
        },
    )
    satir = ftp_settings_service.get_settings(db)
    # Dahili taraf oldugu gibi durur; ftp-server'in servis ettigi kimlik bu.
    assert satir.embedded_username == "device"
    assert ftp_settings_service.get_embedded_password(satir) == "DahiliParola9"
    # Harici taraf da kendi alanlarinda.
    assert satir.username == "ENERJIONE"
    assert ftp_settings_service.get_password(satir) == "MusteriParola1"


def test_dahili_kimlik_guncellemesi_HARICIYI_degistirmez(db) -> None:
    ftp_settings_service.update_settings(
        db, updates={"username": "ENERJIONE", "password": "MusteriParola1"}
    )
    ftp_settings_service.update_settings(
        db, updates={"embedded_username": "cihaz", "embedded_password": "YeniParola22"}
    )
    satir = ftp_settings_service.get_settings(db)
    assert satir.username == "ENERJIONE"
    assert ftp_settings_service.get_password(satir) == "MusteriParola1"
    assert satir.embedded_username == "cihaz"
    assert ftp_settings_service.get_embedded_password(satir) == "YeniParola22"


def test_uretilen_parola_cihaz_ekranina_SIGAR_ve_OKUNABILIR() -> None:
    """Cihaz ekrani <20 karakter kabul eder; 0/O, 1/l/I ve simge YOK —
    parola elle girilecek."""
    yasak = set("0O1lI")
    for _ in range(200):
        p = ftp_settings_service.generate_password()
        assert len(p) < 20
        assert p.isalnum()
        assert not (set(p) & yasak)
        # Her siniftan en az bir karakter (politika garantisi).
        assert any(c.islower() for c in p)
        assert any(c.isupper() for c in p)
        assert any(c.isdigit() for c in p)


# --- gomulu yazma ----------------------------------------------------------
def test_gomulu_yazma_CIHAZIN_ESKI_DOSYASININ_YERINE_yazar(db, tmp_path, monkeypatch) -> None:
    """Cihazin FTP ekranindaki 'Dir' degerini goremeyiz; en guvenilir ipucu
    cihazin daha once yazdigi dosyanin YERI. Baska dizine yazmak 'komut
    gitti, bir sey olmadi' uretir."""
    monkeypatch.setattr(ftp_client_service, "FTP_ROOT", str(tmp_path))
    cihaz_dizini = tmp_path / "SN20" / "FOTA"
    cihaz_dizini.mkdir(parents=True)
    (cihaz_dizini / "50984_Configuration.csv").write_bytes(_dosya(1440))

    yol = ftp_client_service.write_config(
        db, filename="50984_Configuration.csv", raw=_dosya(720)
    )
    assert yol == "SN20/FOTA/50984_Configuration.csv"
    assert (cihaz_dizini / "50984_Configuration.csv").read_bytes() == _dosya(720)
    # Gecici dosya ORTALIKTA KALMAMALI — cihaz onu config sanabilir.
    assert list(cihaz_dizini.iterdir()) == [cihaz_dizini / "50984_Configuration.csv"]


def test_gomulu_yazma_dosya_yoksa_AYARLARDAKI_dizine(db, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ftp_client_service, "FTP_ROOT", str(tmp_path))
    ftp_settings_service.update_settings(db, updates={"directory": "/SN20/FOTA/"})
    yol = ftp_client_service.write_config(
        db, filename="50984_Configuration.csv", raw=_dosya()
    )
    assert yol == "SN20/FOTA/50984_Configuration.csv"
    assert (tmp_path / "SN20" / "FOTA" / "50984_Configuration.csv").exists()


def test_gomulu_yazma_KOK_DISINA_CIKAMAZ(db, tmp_path, monkeypatch) -> None:
    """`directory` ayari kullanicidan gelir; '../' ile volume disina yazmak
    konteynerin baska dosyalarini ezebilirdi."""
    monkeypatch.setattr(ftp_client_service, "FTP_ROOT", str(tmp_path / "kok"))
    (tmp_path / "kok").mkdir()
    ftp_settings_service.update_settings(db, updates={"directory": "../disari"})
    with pytest.raises(ftp_client_service.FtpAccessError):
        ftp_client_service.write_config(db, filename="50984_Configuration.csv", raw=_dosya())


# --- cihazdan yutma --------------------------------------------------------
def test_seri_ile_cihaz_bulunur(db, cihaz) -> None:
    assert cfg_svc.find_device_id_by_serial(db, "50984") == cihaz.id
    assert cfg_svc.find_device_id_by_serial(db, "99999") is None


def test_ayni_icerik_YENI_SURUM_URETMEZ(db, cihaz) -> None:
    """Cihaz her cagrida ayni dosyayi yazarsa gecmis collenir ve gercek
    degisiklikler gorunmez olur."""
    v1 = cfg_svc.ingest_pulled_config(
        db, device_id=cihaz.id, ham=_dosya(1440), filename="50984_Configuration.csv"
    )
    assert v1 is not None and v1.version == 1 and v1.source == "cihazdan_cekildi"

    tekrar = cfg_svc.ingest_pulled_config(
        db, device_id=cihaz.id, ham=_dosya(1440), filename="50984_Configuration.csv"
    )
    assert tekrar is None
    assert len(cfg_svc.list_versions(db, cihaz.id)) == 1

    degisen = cfg_svc.ingest_pulled_config(
        db, device_id=cihaz.id, ham=_dosya(720), filename="50984_Configuration.csv"
    )
    assert degisen is not None and degisen.version == 2


# --- ozet ucu --------------------------------------------------------------
def test_ozet_cihaz_basina_YALNIZCA_guncel_surumu_doner(db, cihaz) -> None:
    """Cihaz listesi rozetleri tek istekle beslenir; cihaz basina istek
    atmak 500 cihazlik sahada listeyi acilamaz yapardi."""
    from app.api.device_configs import config_ozeti

    cfg_svc.create_version(db, device_id=cihaz.id, raw=_dosya(1440), source="yuklendi")
    cfg_svc.create_version(db, device_id=cihaz.id, raw=_dosya(720), source="duzenlendi")

    ozet = config_ozeti(db=db, _u=None)
    assert len(ozet) == 1
    assert ozet[0]["device_id"] == cihaz.id
    assert ozet[0]["version"] == 2
    assert ozet[0]["source"] == "duzenlendi"


# --- harici mod yoklamasi --------------------------------------------------
def test_yoklama_degisen_dosyayi_surume_cevirir(db, cihaz, monkeypatch) -> None:
    """Uzak sunucu MOCK'lanir: yoklayicinin isi listele->indir->yut zinciri;
    FTP protokolunun kendisi degil."""
    from app.services.ftp_poll_worker import FtpPollWorker

    uzak_icerik = {"/SN20/FOTA/50984_Configuration.csv": _dosya(1440)}
    # Config alanlari sabit genislikte oldugu icin dosya BOYUTU degismez;
    # degisikligi yakalayan sey MDTM'dir. Mock bunu yansitir: her yeniden
    # yazista mtime ilerler.
    uzak_mtime = {"/SN20/FOTA/50984_Configuration.csv": "20260805120000"}

    def sahte_listele(_db):
        return [
            ftp_client_service.RemoteConfig(
                path=p, filename=p.rsplit("/", 1)[-1],
                size=len(icerik), mtime=uzak_mtime[p],
            )
            for p, icerik in uzak_icerik.items()
        ]

    def sahte_indir(_db, path):
        return uzak_icerik[path]

    monkeypatch.setattr(ftp_client_service, "read_remote_configs", sahte_listele)
    monkeypatch.setattr(ftp_client_service, "download_remote", sahte_indir)

    w = FtpPollWorker()
    w._sweep(db)
    surumler = cfg_svc.list_versions(db, cihaz.id)
    assert len(surumler) == 1 and surumler[0].source == "cihazdan_cekildi"

    # Ayni imza (size+mtime) -> ikinci turda indirme/yutma YOK.
    w._sweep(db)
    assert len(cfg_svc.list_versions(db, cihaz.id)) == 1

    # Cihaz dosyayi yeniden yazdi: icerik VE mtime degisti -> yeni surum.
    uzak_icerik["/SN20/FOTA/50984_Configuration.csv"] = _dosya(720)
    uzak_mtime["/SN20/FOTA/50984_Configuration.csv"] = "20260805130000"
    w._sweep(db)
    assert len(cfg_svc.list_versions(db, cihaz.id)) == 2


def test_yoklama_eslesmeyen_seriyi_RASTGELE_cihaza_yazmaz(db, cihaz, monkeypatch) -> None:
    from app.services.ftp_poll_worker import FtpPollWorker

    monkeypatch.setattr(
        ftp_client_service,
        "read_remote_configs",
        lambda _db: [
            ftp_client_service.RemoteConfig(
                path="/99999_Configuration.csv", filename="99999_Configuration.csv",
                size=10, mtime=None,
            )
        ],
    )
    monkeypatch.setattr(
        ftp_client_service, "download_remote",
        lambda _db, _p: (_ for _ in ()).throw(AssertionError("indirilmemeliydi"))
    )
    w = FtpPollWorker()
    w._sweep(db)
    assert cfg_svc.list_versions(db, cihaz.id) == []
