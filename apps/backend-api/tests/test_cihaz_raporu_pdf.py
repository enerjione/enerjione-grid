"""CIHAZ DURUM RAPORU (PDF) — `GET /devices/{code}/report.pdf`.

Bu testler dort kurali kilitliyor:

  1. ISKELET CIHAZ TURUNDEN TURETILIR. "Olcum unitesi" karari kaynak ADINA
     bakilarak verilemez: SN 2.0'da `master` olcum yapar, Pole Master Kit'te
     YAPMAZ (modem/besleme/GPS tasir). Karar sinyal katalogundan cikar, yani
     katalogtan tanimlanan yeni bir model de dogru iskeletle raporlanir.

  2. SETIN HABERLESME DEGERLERI KITTEN OKUNUR. Bir Pole Master Kit setinin
     kaydinda `master.*` telemetrisi HIC YOKTUR; bunlari setin kendi
     kaydindan okuyan her ekran onlari sonsuza kadar bos gosterir.

  3. YESIL YALAN YOK. Haberlesmesi kopmus cihaz icin gateway `comm_lost`
     kalitesiyle 0.0 basar. Backend bu okumayi alarm degerlendirmesine
     SOKMAZ; rapor da "Normal" yazmamali — yoksa musteriye giden belge
     sunucunun kararini gecersiz kilar.

  4. KAPSAM RAPORA DA UYGULANIR. Rapor musteri logosu, hat/direk bilgisi ve
     GPS koordinati tasir; kapsam disina sizmasi kabul edilemez.

Ayrica sinyal adlarinin frontend `tr.json` ile ayrismadigi dogrulanir:
ayrisirsa kullanici ekranda "Asiri Akim Acmasi" gorup raporda "Overcurrent
Tripped" indirir.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.data.device_models import PMK_SET_MODEL, POLE_MASTER_KIT_MODEL
from app.db.base import Base
from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.models.enums import CommunicationStatus, UserRole
from app.models.grid_topology import Line, LineSegment, Pole, Region
from app.models.system_event import SystemEvent
from app.models.telemetry_latest import TelemetryLatest
from app.models.user import User

FRONTEND_TR = (
    Path(__file__).resolve().parents[2]
    / "frontend-web"
    / "src"
    / "shared"
    / "i18n"
    / "resources"
    / "tr.json"
)
BACKEND_LABELS = (
    Path(__file__).resolve().parents[1] / "app" / "data" / "signal_labels_tr.json"
)


@pytest.fixture()
def db():
    import app.models

    for module in pkgutil.iter_modules(app.models.__path__):
        importlib.import_module(f"app.models.{module.name}")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=True)()
    try:
        _katalog(session)
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def karo_yok(monkeypatch):
    """Karo cekmeyi kapat: test internete cikmasin, zaman asimi beklemesin.

    Figur zemin olmadan da uretilir — raporun karoya BAGIMLI olmamasi zaten
    istenen davranis.
    """
    from app.services import map_tile_service

    def bos(*_args, **_kwargs):
        raise map_tile_service.MapTileError("MAP_TILE_OFFLINE", "test")

    monkeypatch.setattr(map_tile_service, "get_tile", bos)
    monkeypatch.setattr(map_tile_service, "read_tile", lambda *_a, **_k: None)


@pytest.fixture(autouse=True)
def esik_onbellegi_temiz():
    """Batarya esigi onbellegi testler arasi sizmasin (modul duzeyinde tutuluyor)."""
    from app.services import device_profile_service

    device_profile_service.invalidate_cache()
    yield
    device_profile_service.invalidate_cache()


def _katalog(db) -> None:
    """Gercek sinyal kataloglarini yukle — iskelet karari ONDAN cikiyor."""
    from app.services.signal_catalog_seed import seed_default_signals

    seed_default_signals(db, strict=False)
    db.commit()


def _kullanici(db, role: UserRole, username: str = "eng") -> User:
    user = User(
        username=username,
        full_name="Fikret Şafak",
        email=f"{username}@example.com",
        hashed_password="x",
        role=role,
    )
    db.add(user)
    db.commit()
    return user


def _sebeke(db) -> None:
    db.add(Region(id=1, code="GRZ", name="GARZAN ÇAKILLI"))
    db.add(Line(id=1, region_id=1, code="ANA", name="ANA HAT"))
    for seq in range(1, 5):
        db.add(
            Pole(
                id=seq,
                line_id=1,
                sequence_no=seq,
                name=f"Direk #{seq}",
                latitude=38.10 + seq * 0.002,
                longitude=41.19 + seq * 0.002,
            )
        )
    db.commit()


def _sn2(db, *, code: str = "SN2-01") -> Device:
    device = Device(
        code=code,
        name="Çakıllı 2",
        model="horstmann_sn_2_0",
        ip_address="10.0.0.11",
        latitude=38.104,
        longitude=41.194,
        battery_percent=87.0,
        serial_number="9001",
        installation_date=date(2025, 4, 17),
        communication_status=CommunicationStatus.ONLINE,
        last_update_at=datetime.now(timezone.utc),
    )
    db.add(device)
    db.commit()
    db.add(LineSegment(id=1, line_id=1, from_pole_id=2, to_pole_id=3, device_id=device.id))
    db.commit()
    return device


def _kit(db) -> tuple[Device, list[Device]]:
    """Fiziksel kit + uc set (uretim yolunun kendisiyle)."""
    from app.services import device_kit_service

    kit = Device(
        code="PMK-01",
        name="Çakıllı Kit",
        model=POLE_MASTER_KIT_MODEL,
        ip_address="10.0.0.20",
        latitude=38.106,
        longitude=41.196,
        battery_percent=64.0,
        communication_status=CommunicationStatus.ONLINE,
        last_update_at=datetime.now(timezone.utc),
    )
    db.add(kit)
    db.commit()
    sets = device_kit_service.create_subunits(db, kit, 3)
    db.commit()
    db.add(LineSegment(id=2, line_id=1, from_pole_id=1, to_pole_id=2, device_id=sets[1].id))
    db.commit()
    return kit, sets


def _telemetri(db, device_id: int, key: str, value=None, *, text=None, quality="good") -> None:
    now = datetime.now(timezone.utc)
    db.add(
        TelemetryLatest(
            device_id=device_id,
            signal_key=key,
            value=value,
            value_string=text,
            quality=quality,
            source_timestamp=now,
            updated_at=now,
        )
    )
    db.commit()


def _rapor(db, device_code: str, user: User):
    from app.api.devices import device_report_pdf

    return device_report_pdf(device_code, current_user=user, db=db)


# ---------------------------------------------------------------------------
# 1. Iskelet cihaz turunden turetilir
# ---------------------------------------------------------------------------
def test_sn2_de_master_bir_olcum_kanalidir(db):
    """SN 2.0: uc kanal (master/sat01/sat02) — `master` OLCUM YAPAR."""
    from app.services.device_report_service import collect_device_report

    _sebeke(db)
    device = _sn2(db)
    data = collect_device_report(db, device)

    assert data.kind == "simple"
    assert [c.source for c in data.channels] == ["master", "sat01", "sat02"]
    # Cihazin bataryasi master unitenindir ve kayitta hesaplanmis durur.
    assert data.channels[0].battery_percent == pytest.approx(87.0)


def test_kitte_master_olcum_kanali_DEGILDIR(db):
    """Pole Master Kit: hic olcum kanali yok, bagli setler listesi var.

    Kaynak ADINA bakan bir kural burada kirilir: kitin de bir `master`i var
    ama o modem/besleme/GPS tasiyan ortak RTU'dur. Kite dokuz uydu icin
    kanal acmak da yanlis olurdu — uydu telemetrisi SETLERE yonlendirilir,
    kit kaydinda hicbir zaman gorunmez.
    """
    from app.services.device_report_service import collect_device_report

    _sebeke(db)
    kit, _sets = _kit(db)
    data = collect_device_report(db, kit)

    assert data.kind == "kit"
    assert data.channels == []
    assert len(data.subunits) == 3
    assert data.subunits[0].satellites == "01, 02, 03"


def test_sette_uc_uydu_kanali_ve_fiziksel_uydu_numarasi(db):
    """Set: ucu de uydu olan uc kanal; ikinci setin uydulari 04/05/06."""
    from app.services.device_report_service import collect_device_report

    _sebeke(db)
    _kit_row, sets = _kit(db)
    data = collect_device_report(db, sets[1])

    assert data.kind == "set"
    assert [c.source for c in data.channels] == ["sat01", "sat02", "sat03"]
    assert [c.satellite_no for c in data.channels] == [4, 5, 6]


# ---------------------------------------------------------------------------
# 2. Setin haberlesme degerleri KITTEN okunur
# ---------------------------------------------------------------------------
def test_setin_rtu_degerleri_KIT_kaydindan_gelir(db):
    """Setin kaydinda `master.*` YOKTUR; modem/besleme kitten okunmali.

    Setin kendi kaydindan okunsaydi bu satirlar sonsuza kadar bos kalirdi —
    hata da vermeden.
    """
    from app.services.device_report_service import collect_device_report

    _sebeke(db)
    kit, sets = _kit(db)
    _telemetri(db, kit.id, "master.info_modem_ip_address", text="10.20.30.40")
    _telemetri(db, kit.id, "master.device_temperature", value=31.5)
    _telemetri(db, kit.id, "master.solar_power", value=1.0)

    data = collect_device_report(db, sets[0])
    tum = [deger for pairs in data.rtu_groups.values() for _etiket, deger in pairs]

    assert "10.20.30.40" in tum, tum
    # Kitin OLCUMLERI de rapora girer. Bu bolum "bilgi sinyalleri" diye
    # tanimlansaydi kitin gercek olcumleri (besleme, sicaklik) HIC gorunmezdi.
    assert "31,5 °C" in tum, tum
    assert "Aktif" in tum, tum


def test_setin_pili_FIZIKSEL_uydudan_okunur(db):
    """Setin `sat01` bolmesi sanal addir; gerilim kit kaydinda `sat04`te durur.

    Ikinci setin ilk unitesi fiziksel olarak Satellite 04'tur. Sabit
    turetme kullanilsaydi YANLIS uydunun pili dogru diye gosterilirdi ve
    ekranda hicbir hata gorunmezdi.
    """
    from app.services.device_report_service import collect_device_report

    _sebeke(db)
    kit, sets = _kit(db)
    # Kit kaydinda gercek uydu numarasiyla: 3.71 V = %100 (kod varsayilani).
    _telemetri(db, kit.id, "sat04.battery_voltage_satellite", value=3.71)

    data = collect_device_report(db, sets[1])
    assert data.channels[0].battery_percent == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# 3. Yesil yalan yok
# ---------------------------------------------------------------------------
def test_comm_lost_okumasi_NORMAL_yazilmaz(db):
    """`comm_lost` kalitesiyle gelen 0.0 "Guvenilmez" basilir, "Normal" degil."""
    from app.services.device_report_service import collect_device_report

    _sebeke(db)
    device = _sn2(db)
    _telemetri(db, device.id, "master.overcurrent_tripped", value=0.0, quality="comm_lost")
    _telemetri(db, device.id, "sat01.overcurrent_tripped", value=0.0, quality="good")

    data = collect_device_report(db, device)
    satir = next(
        r for r in data.groups["protection"] if r.suffix == "overcurrent_tripped"
    )
    assert satir.values["master"][0] == "Güvenilmez"
    assert satir.values["sat01"][0] == "Normal"


def test_veri_gelmeyen_sinyal_satiri_BASILMAZ(db):
    """Hicbir kanalda degeri olmayan nokta tabloya girmez, SAYISI yazilir.

    Bir cihazin katalogunda 150'yi askin nokta var; hepsini "Veri yok"
    diye basmak, gercekten bilinen degerleri duvarin icinde gorunmez
    kilardi.
    """
    from app.services.device_report_service import collect_device_report

    _sebeke(db)
    device = _sn2(db)
    _telemetri(db, device.id, "master.actual_current", value=12.5)

    data = collect_device_report(db, device)
    basilan = {r.suffix for rows in data.groups.values() for r in rows}

    assert basilan == {"actual_current"}
    # SN 2.0'in uc kanalinda ~43 farkli olcum noktasi var; biri disindaki
    # hepsi elendi ve sayisi raporda yaziyor.
    assert data.hidden_signal_count > 30


# ---------------------------------------------------------------------------
# 4. Kapsam
# ---------------------------------------------------------------------------
def test_kapsam_disi_operator_rapor_alamaz(db):
    _sebeke(db)
    _sn2(db)
    operator = _kullanici(db, UserRole.OPERATOR, username="saha")

    with pytest.raises(HTTPException) as err:
        _rapor(db, "SN2-01", operator)
    assert err.value.status_code == 403


def test_bulunmayan_cihaz_404(db):
    with pytest.raises(HTTPException) as err:
        _rapor(db, "YOK-99", _kullanici(db, UserRole.ENGINEER))
    assert err.value.status_code == 404


def test_seti_gorunen_kullanici_KITIN_raporunu_alabilir(db, monkeypatch):
    """Kit hicbir kesime baglanmaz; kapsam filtresinden her zaman duserdi.

    Kural liste ucundakiyle ayni: setlerinden en az biri gorunuyorsa kit de
    gorunur — aksi halde setleri gorebilen kullanici kitin raporuna 403 alirdi.
    """
    from app.api import devices as devices_api

    _sebeke(db)
    _kit_row, sets = _kit(db)
    operator = _kullanici(db, UserRole.OPERATOR, username="saha")
    monkeypatch.setattr(
        devices_api, "get_visible_device_ids", lambda _db, _user: {sets[1].id}
    )

    response = _rapor(db, "PMK-01", operator)
    assert response.body[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# Belge gercekten uretiliyor mu
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("hedef", ["sn2", "kit", "set"])
def test_pdf_uretilir(db, hedef):
    """Uc cihaz turu de gercek bir PDF uretir."""
    _sebeke(db)
    device = _sn2(db)
    kit, sets = _kit(db)
    _telemetri(db, device.id, "master.actual_current", value=12.5)
    _telemetri(db, device.id, "master.info_ipv4_address", text="10.0.0.11")
    _telemetri(db, kit.id, "master.info_modem_imei", text="356938035643809")
    _telemetri(db, sets[0].id, "sat01.overcurrent_tripped", value=1.0)
    db.add(
        AlarmEvent(
            device_id=device.id,
            level="critical",
            title="Aşırı akım & yüksek sıcaklık <kontrol>",
            description="Test",
            signal_key="sat01.overcurrent_tripped",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
    )
    db.add(
        SystemEvent(
            category="device",
            event_type="device_command_queued",
            severity="info",
            message="Komut kuyruğa alındı",
            device_code=device.code,
            metadata_json=json.dumps(
                {"_i18n": {"key": "device_command_queued", "params": {"command": "reset_all_fcis"}}}
            ),
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    kod = {"sn2": device.code, "kit": kit.code, "set": sets[0].code}[hedef]
    response = _rapor(db, kod, _kullanici(db, UserRole.ENGINEER))

    assert response.media_type == "application/pdf"
    assert response.body[:5] == b"%PDF-"
    assert len(response.body) > 4000
    assert f'filename="cihaz-{kod}-' in response.headers["Content-Disposition"]


def test_serbest_metindeki_isaretler_raporu_dusurmez(db):
    """Alarm basligindaki `&` ve `<...>` PDF kurulumunu HATA ile dusurmemeli.

    reportlab Paragraph icerigini XML gibi ayristirir; kacislama olmadan
    "Aşırı akım & yüksek sıcaklık <kontrol>" gibi siradan bir baslik raporu
    tamamen basarisiz kilardi. Daha sinsisi: `<kontrol>` BILINMEYEN ETIKET
    sayilip sessizce atilir ve baslik eksik basilirdi.
    """
    from app.services.report_layout import esc

    _sebeke(db)
    device = _sn2(db)
    db.add(
        AlarmEvent(
            device_id=device.id,
            level="warning",
            title="R&D <yenilendi>",
            description="x",
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    response = _rapor(db, device.code, _kullanici(db, UserRole.ENGINEER))
    assert response.body[:5] == b"%PDF-"
    assert esc("R&D <yenilendi>") == "R&amp;D &lt;yenilendi&gt;"


def test_dosya_adindaki_cihaz_kodu_temizlenir(db):
    """`devices.code` serbest metin: `/` iceren bir kod basligi kirardi."""
    _sebeke(db)
    device = _sn2(db, code='SN2/01 "A"')

    response = _rapor(db, device.code, _kullanici(db, UserRole.ENGINEER))
    disposition = response.headers["Content-Disposition"]

    assert 'filename="cihaz-SN2-01-A-' in disposition
    assert disposition.count('"') == 2


# ---------------------------------------------------------------------------
# Sinyal adlari ekranla ayni mi
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not FRONTEND_TR.exists(), reason="frontend kaynagi yok (backend-only checkout)"
)
def test_sinyal_adlari_frontend_ile_AYNI():
    """Ayna dosyasi tr.json > signals ile BIREBIR — eksik/fazla/degismis yok."""
    with FRONTEND_TR.open(encoding="utf-8") as handle:
        frontend = json.load(handle)["signals"]
    with BACKEND_LABELS.open(encoding="utf-8") as handle:
        backend = json.load(handle)

    assert backend == frontend, (
        "Sinyal adlari ayrismis. Duzeltmek icin tr.json'daki `signals` blogunu "
        f"{BACKEND_LABELS.name} dosyasina kopyalayin."
    )


def test_sozlukte_olmayan_sinyal_KIRILMAZ():
    """Ceviri yoksa katalog adina, o da yoksa sonege dusulur."""
    from app.services.signal_labels import signal_label

    assert signal_label("sat07.fault_current") == "Arıza Akımı"  # sonek uzerinden
    assert signal_label("master.yeni_nokta", "New Point") == "New Point"
    assert signal_label("master.yeni_nokta") == "yeni_nokta"


@pytest.mark.parametrize(
    "deger,ondalik,beklenen",
    [
        (40.0, 0, "40"),   # kuyruk kirpma sayinin KENDISINI yememeli
        (100.0, 0, "100"),
        (0.0, 0, "0"),
        (1234.5, 6, "1234,5"),
        (33.560, 6, "33,56"),
        (12.0, 6, "12"),
        (-0.0, 6, "0"),
    ],
)
def test_sayi_bicimi(deger, ondalik, beklenen):
    """Ondalik yokken `rstrip("0")` rakam yiyordu: 40 sayac "4" basiliyordu.

    Makul gorunen bir rakam oldugu icin bu, raporu okuyan kisinin fark
    edemeyecegi turden bir hataydi.
    """
    from app.services.device_report_service import _num

    assert _num(deger, ondalik) == beklenen


def test_sayaclar_ozet_seridinde_TAM_basilir(db):
    """Uctan sifirli bir sayac degeri kirpilmadan raporda yer almali."""
    from app.services.device_report_service import collect_device_report

    _sebeke(db)
    device = _sn2(db)
    _telemetri(db, device.id, "master.permanent_fault_counter", value=40.0)
    _telemetri(db, device.id, "master.momentary_fault_counter", value=100.0)

    data = collect_device_report(db, device)
    assert data.permanent_faults == "40"
    assert data.momentary_faults == "100"


def test_engelleyen_kaliteler_BACKEND_ILE_ayni():
    """Rapor, alarm motorunun "bu okumaya guvenme" karariyla ayni kumeyi kullanmali."""
    from app.services.device_report_service import _ENGELLEYEN_KALITELER
    from app.services.tag_engine_service import ALARM_BLOCKING_QUALITIES

    assert _ENGELLEYEN_KALITELER == frozenset(ALARM_BLOCKING_QUALITIES)
