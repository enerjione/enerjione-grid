"""ARIZA RAPORU (PDF) — `GET /faults/{id}/report.pdf`.

Rapor eskiden tarayicinin `window.print()` ciktisiydi. Iki kural bu testlerle
kilitleniyor:

  1. RAPOR KAYITTAN URETILIR. Ekrandaki kirmizi bolge CANLI alarm durumundan
     turetilir; alarm resetlenince (ariza kapandiktan sonra) kaybolur ve
     yazdirilan rapor hattin tamamini saglam gosterirdi. Sunucu tarafi
     `last_red_device_id` / `first_green_device_id` alanlarini kullanir —
     hic acik alarm olmasa bile bolge cizilir.

  2. KAPSAM RAPORA DA UYGULANIR. Rapor musteri logosu, saha notlari ve direk
     koordinatlari tasir; operator kendi sorumluluk alani disindaki bir
     arizanin raporunu ALAMAZ.
"""

from __future__ import annotations

import importlib
import pkgutil
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.device import Device
from app.models.enums import UserRole
from app.models.fault import FaultComment, FaultEvent
from app.models.grid_topology import Line, LineSegment, Pole, Region
from app.models.user import User


@pytest.fixture()
def db():
    import app.models

    for module in pkgutil.iter_modules(app.models.__path__):
        importlib.import_module(f"app.models.{module.name}")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def karo_yok(monkeypatch):
    """Karo cekmeyi kapat: test internete cikmasin, zaman asimi beklemesin.

    Figur zemin olmadan da uretilir (cizgi semasi) — raporun karoya BAGIMLI
    olmamasi zaten istenen davranis.
    """
    from app.services import map_tile_service

    def bos(*_args, **_kwargs):
        raise map_tile_service.MapTileError("MAP_TILE_OFFLINE", "test")

    monkeypatch.setattr(map_tile_service, "get_tile", bos)


def _sebeke(db) -> FaultEvent:
    """Bir bolge + hat + 6 direk + iki cihazli iki segment + ariza kaydi."""
    db.add(Region(id=1, code="GRZ", name="GARZAN ÇAKILLI"))
    db.add(Line(id=1, region_id=1, code="ANA", name="ANA HAT"))
    for seq in range(1, 7):
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
    db.add(
        Device(
            id=11,
            code="SN2-11",
            name="Çakıllı 2",
            ip_address="10.0.0.11",
            latitude=38.104,
            longitude=41.194,
        )
    )
    db.add(
        Device(
            id=12,
            code="SN2-12",
            name="Çakıllı 5",
            ip_address="10.0.0.12",
            latitude=38.110,
            longitude=41.200,
        )
    )
    db.add(LineSegment(id=1, line_id=1, from_pole_id=2, to_pole_id=3, device_id=11))
    db.add(LineSegment(id=2, line_id=1, from_pole_id=5, to_pole_id=6, device_id=12))

    opened = datetime.now(timezone.utc) - timedelta(days=1)
    fault = FaultEvent(
        id=5,
        line_id=1,
        region_id=1,
        last_red_device_id=11,
        first_green_device_id=12,
        from_pole_id=3,
        to_pole_id=5,
        from_pole_seq=3,
        to_pole_seq=5,
        zone_start_m=312.0,
        zone_end_m=624.0,
        zone_length_m=312.0,
        status="closed",
        opened_at=opened,
        resolved_at=opened + timedelta(hours=4),
        closed_at=opened + timedelta(hours=5),
        resolution_note="İzolatör değiştirildi.",
        cause_code="tree_contact",
        cause_detail="4. direkte dal teması",
        fault_kind="permanent",
        phase="abc",
        fault_current_a=1240.4,
    )
    db.add(fault)
    db.add(
        FaultComment(
            id=1,
            fault_id=5,
            author_username="fsafak",
            body="Dal kesildi & izolatör değiştirildi <kontrol edildi>",
            created_at=opened + timedelta(hours=3),
        )
    )
    db.commit()
    return fault


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


def test_rapor_pdf_uretilir(db):
    """Engineer icin rapor uretilir ve gercek bir PDF doner."""
    from app.api.faults import fault_report_pdf

    _sebeke(db)
    response = fault_report_pdf(5, current_user=_kullanici(db, UserRole.ENGINEER), db=db)

    assert response.media_type == "application/pdf"
    assert response.body[:5] == b"%PDF-"
    assert 'filename="ariza-5-' in response.headers["Content-Disposition"]
    # Kapanmis ariza: kayit "closed" oldugu halde rapor uretilebilmeli —
    # rapor gecmise donuk alinan bir belgedir.
    assert len(response.body) > 5000


def test_bolge_canli_alarmdan_degil_KAYITTAN_cizilir(db):
    """Hic ACIK alarm yokken bile ariza bolgesi cizilir.

    Ekran haritasi bunu yapamaz (alarm resetlendiginde bolge kaybolur);
    raporun tek dogruluk kaynagi ariza kaydidir.
    """
    from app.services.fault_report_map import collect_fault_geometry

    fault = _sebeke(db)
    geometry = collect_fault_geometry(db, fault)

    assert geometry is not None
    # Kayittaki iki cihazin arasi KIRMIZI, oncesi/sonrasi saglam.
    assert len(geometry.fault) >= 2
    assert len(geometry.pre_ok) >= 2
    assert len(geometry.post_ok) >= 2
    roles = {d.device_id: d.role for d in geometry.devices}
    assert roles == {11: "red", 12: "green"}
    # Aralik direkleri isaretli: #3 baslangic, #5 bitis.
    zone = {p.sequence_no: p.role for p in geometry.poles}
    assert zone[3] == "start" and zone[5] == "end" and zone[1] == "other"


def test_kapsam_disi_operator_rapor_alamaz(db):
    """Operator kendi alani disindaki arizanin raporunu ALAMAZ (403)."""
    from app.api.faults import fault_report_pdf

    _sebeke(db)
    operator = _kullanici(db, UserRole.OPERATOR, username="saha")

    with pytest.raises(HTTPException) as err:
        fault_report_pdf(5, current_user=operator, db=db)
    assert err.value.status_code == 403


def test_bulunmayan_ariza_404(db):
    from app.api.faults import fault_report_pdf

    with pytest.raises(HTTPException) as err:
        fault_report_pdf(9999, current_user=_kullanici(db, UserRole.ENGINEER), db=db)
    assert err.value.status_code == 404


def test_serbest_metin_kacislanir_ve_satir_sonu_korunur():
    """`&` ve `<...>` metin olarak KALIR, satir sonu <br/> olur.

    reportlab Paragraph icerigini XML gibi ayristirir. Kacislama olmadan iki
    ayri sey oluyordu: bare `&` belge kurulumunu dusuruyor, `<yenilendi>` ise
    bilinmeyen bir ETIKET sayilip SESSIZCE ATILIYORDU — kapanis notundan bir
    kelime eksik cikiyor ve kimse fark etmiyordu. Rapor bu yuzden yalnizca
    "PDF uretildi mi" ile dogrulanamaz.
    """
    from app.services.fault_report_service import _note_box, _Styles

    box = _note_box(_Styles(), "ÇÖZÜM NOTU", "R&D <yenilendi> dedi.\nikinci satır")
    metin = box._cellvalues[1][0].text

    assert "&amp;" in metin
    assert "&lt;yenilendi&gt;" in metin
    assert "<br/>" in metin


def test_serbest_metindeki_isaretler_raporu_dusurmez(db):
    """Yorumdaki `&` ve `<...>` PDF kurulumunu HATA ile dusurmemeli.

    reportlab Paragraph icerigini XML gibi ayristirir; kacislama olmadan
    "Dal kesildi & izolatör <kontrol edildi>" gibi siradan bir saha notu
    raporu tamamen basarisiz kiliyordu.
    """
    from app.api.faults import fault_report_pdf

    _sebeke(db)
    response = fault_report_pdf(5, current_user=_kullanici(db, UserRole.ENGINEER), db=db)
    assert response.body[:5] == b"%PDF-"
