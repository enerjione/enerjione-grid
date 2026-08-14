"""WS bileti SURECLER ARASINDA gecerli olmali.

YASANAN KUSUR
-------------
Bilet `/auth/ws-ticket` ile uretiliyor, hemen ardindan WS upgrade istegiyle
tuketiliyor — ama bunlar IKI AYRI TCP BAGLANTISI. `E1_API_WORKERS>1` iken
uvicorn istekleri surecler arasinda dagitiyor ve bilet A surecinde uretilip
B surecine dusebiliyor. Bilet surec-ici bir dict'te tutuldugu icin B onu
BULAMIYOR ve baglanti `1008 invalid_credentials` ile kapaniyordu.

Istemcideki token fallback'i yalnizca bilet ALINAMAZSA devreye giriyor
(`useLiveValuesSocket.ts`); 1008 kapanisi o dala girmedigi icin sonuc
backoff'lu yeniden baglanma dongusu oluyordu — her denemede yeni bilet, yine
rastgele surec. 4 surecte deneme basina ~%25 tutma olasiligi, yani canli
degerler ve harita KALICI OLARAK KARARSIZ.

BURADA SINANAN DAVRANISLAR
--------------------------
  1. Bilet paylasimli depoya (DB) yaziliyor — yani bileti ureten surecten
     BASKA bir surec de gorebiliyor.
  2. Tek kullanim korunuyor: ikinci tuketim None doner (replay korumasi).
  3. Suresi dolmus bilet reddedilir.
  4. Bilet uretimi suresi dolmus satirlari temizler (tablo sismesin).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.ws_ticket import WsTicket
from app.services import auth_service


@pytest.fixture()
def paylasimli_db(monkeypatch):
    """Tum 'surecler'in paylastigi tek depo.

    `issue_ws_ticket` / `consume_ws_ticket` kendi kisa-omurlu session'ini
    acar; ikisi de AYNI engine'e baglanir. Gercek kurulumda bu paylasim
    Postgres, testte tek bir in-memory SQLite baglantisi (StaticPool).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[WsTicket.__table__])
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    # Iki fonksiyon da `from app.db.session import SessionLocal` cagrisini
    # CALISMA ANINDA yaptigi icin buradaki yama ikisini de yakalar.
    monkeypatch.setattr("app.db.session.SessionLocal", Session)
    yield Session


def test_bilet_paylasimli_depoya_yazilir(paylasimli_db):
    """Bileti ureten surecten BASKA biri de gorebilmeli."""
    bilet, ttl = auth_service.issue_ws_ticket("operator1", jti="jti-abc")

    assert ttl > 0
    # "Baska bir surec" gozuyle bak: bagimsiz bir session ac.
    with paylasimli_db() as baska_surec:
        satir = baska_surec.get(WsTicket, bilet)
        assert satir is not None, "bilet paylasimli depoda YOK — surec-ici kalmis"
        assert satir.username == "operator1"
        assert satir.jti == "jti-abc"


def test_bileti_baska_surec_tuketebilir(paylasimli_db):
    """Asil regresyon: uretim ve tuketim ayri sureclerde olabilir."""
    bilet, _ = auth_service.issue_ws_ticket("operator1", jti="jti-abc")

    sonuc = auth_service.consume_ws_ticket(bilet)

    assert sonuc == ("operator1", "jti-abc")


def test_ayni_bilet_IKINCI_KEZ_kullanilamaz(paylasimli_db):
    """Tek kullanim (replay korumasi) surecler arasinda da gecerli."""
    bilet, _ = auth_service.issue_ws_ticket("operator1", jti="jti-abc")

    assert auth_service.consume_ws_ticket(bilet) == ("operator1", "jti-abc")
    assert auth_service.consume_ws_ticket(bilet) is None


def test_gecersiz_bilet_reddedilir(paylasimli_db):
    assert auth_service.consume_ws_ticket("olmayan-bilet") is None
    assert auth_service.consume_ws_ticket("") is None


def test_suresi_dolmus_bilet_reddedilir(paylasimli_db):
    """TTL gecmisse bilet kabul edilmemeli."""
    bilet, _ = auth_service.issue_ws_ticket("operator1", jti="jti-abc")
    with paylasimli_db() as s:
        satir = s.get(WsTicket, bilet)
        satir.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        s.commit()

    assert auth_service.consume_ws_ticket(bilet) is None


def test_uretim_suresi_dolmuslari_temizler(paylasimli_db):
    """Tablo sismesin: yeni bilet uretimi olu satirlari atar."""
    with paylasimli_db() as s:
        s.add(
            WsTicket(
                ticket="eski",
                username="x",
                jti=None,
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            )
        )
        s.commit()

    auth_service.issue_ws_ticket("operator1", jti="jti-abc")

    with paylasimli_db() as s:
        kalanlar = s.scalars(select(WsTicket.ticket)).all()
        assert "eski" not in kalanlar
