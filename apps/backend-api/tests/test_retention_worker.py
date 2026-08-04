"""Retention worker davranis testleri.

Neden test: bu worker sahada TEK BASINA diskin dolmasini engelliyor. Iki
ozelligi sessizce bozulabilir ve bozuldugu ancak disk dolunca anlasilir:

  1. BATCH'LI SILME — LIMIT'li turlar. Onceki surumde LIMIT yoktu ve saatlik
     tetikte ~1.1M satir tek transaction'da siliniyordu (WAL sismesi, uzun
     lock, autovacuum'un geride kalmasi). Buradaki testler silmenin gercekten
     turlere bolundugunu VE tur tavanina ragmen dogru sonucu verdigini
     dogrular.
  2. FIFO ADET TAVANI — `system_events` icin sure bazli pencerenin yaninda
     emniyet subabi. En ESKI kayitlar dusmeli, en YENILER kalmali; ters
     calisirsa denetim kaydinin ise yarar kismi silinir.

`telemetry` purge'u burada test EDILMIYOR: PostgreSQL'e ozgu
`DELETE ... USING` + `ROW_NUMBER() OVER` kullaniyor, sqlite'ta kosmaz.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.models.outbox_event import OutboxEvent
from app.models.processed_message import ProcessedMessage
from app.models.system_event import SystemEvent
from app.services import telemetry_retention


@pytest.fixture()
def session_factory(monkeypatch):
    """sqlite in-memory + worker'in SessionLocal'ini ona yonlendir.

    StaticPool kullanmadan `:memory:` her baglantida SIFIRDAN acilir; worker
    her turda YENI session actigi icin tablolar kaybolurdu. Tek dosya-benzeri
    paylasilan bellek icin `shared cache` yerine tek Connection'a bagli
    sessionmaker yeterli: engine'i `poolclass` ile tekil tutuyoruz.
    """
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            ProcessedMessage.__table__,
            SystemEvent.__table__,
            OutboxEvent.__table__,
        ],
    )
    factory = sessionmaker(bind=engine, autoflush=False, future=True)
    monkeypatch.setattr(telemetry_retention, "SessionLocal", factory)
    return factory


def _seed_processed(factory, *, old: int, fresh: int, cutoff_hours: int) -> None:
    now = datetime.now(timezone.utc)
    db = factory()
    try:
        for i in range(old):
            db.add(
                ProcessedMessage(
                    consumer_name="c",
                    message_id=f"old-{i}",
                    processed_at=now - timedelta(hours=cutoff_hours + 1),
                )
            )
        for i in range(fresh):
            db.add(
                ProcessedMessage(
                    consumer_name="c",
                    message_id=f"new-{i}",
                    processed_at=now,
                )
            )
        db.commit()
    finally:
        db.close()


def _count(factory, model) -> int:
    db = factory()
    try:
        return int(db.scalar(select(func.count(model.id))) or 0)
    finally:
        db.close()


def test_processed_messages_purge_removes_only_old(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "processed_messages_retention_hours", 24)
    monkeypatch.setattr(settings, "retention_delete_batch", 1000)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 50)
    _seed_processed(session_factory, old=120, fresh=30, cutoff_hours=24)

    removed = telemetry_retention.RetentionWorker().purge_processed_messages()

    assert removed == 120
    assert _count(session_factory, ProcessedMessage) == 30


def test_purge_is_batched_and_still_completes(session_factory, monkeypatch):
    """Batch boyutu kucuk olsa da TUM eski satirlar silinmeli.

    batch=10, 95 eski satir -> 10 tur (son tur 5 satir, break). Tur tavani
    50 oldugu icin sigar.
    """
    monkeypatch.setattr(settings, "processed_messages_retention_hours", 24)
    monkeypatch.setattr(settings, "retention_delete_batch", 10)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 50)
    _seed_processed(session_factory, old=95, fresh=5, cutoff_hours=24)

    removed = telemetry_retention.RetentionWorker().purge_processed_messages()

    assert removed == 95
    assert _count(session_factory, ProcessedMessage) == 5


def test_batch_cap_limits_one_run_and_leaves_rest(session_factory, monkeypatch):
    """Tur tavani asilirsa purge DURMALI, kalan satirlar sonraki tetige kalmali.

    Bu davranis kasitli: tek bir tetigin saatlerce surup DB'yi mesgul etmesini
    onler. Kalan satirlarin silinmedigini dogruluyoruz ki tavan sessizce
    "hepsini sildim" yalanini soylemesin.
    """
    monkeypatch.setattr(settings, "processed_messages_retention_hours", 24)
    monkeypatch.setattr(settings, "retention_delete_batch", 10)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 3)
    _seed_processed(session_factory, old=100, fresh=0, cutoff_hours=24)

    removed = telemetry_retention.RetentionWorker().purge_processed_messages()

    assert removed == 30  # 3 tur x 10
    assert _count(session_factory, ProcessedMessage) == 70


def _seed_events(
    factory,
    count: int,
    *,
    age_days: int = 0,
    category: str = "telemetry",   # varsayilan: FIFO ile dusurulebilir gurultu
    severity: str = "info",
) -> None:
    now = datetime.now(timezone.utc)
    db = factory()
    try:
        for i in range(count):
            db.add(
                SystemEvent(
                    category=category,
                    event_type="t",
                    severity=severity,
                    message=f"m{i}",
                    created_at=now - timedelta(days=age_days),
                )
            )
        db.commit()
    finally:
        db.close()


def test_system_events_age_purge(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "system_events_retention_days", 730)
    monkeypatch.setattr(settings, "system_events_max_rows", 0)  # tavan kapali
    monkeypatch.setattr(settings, "retention_delete_batch", 1000)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 50)
    _seed_events(session_factory, 40, age_days=800)  # 2 yildan eski
    _seed_events(session_factory, 10, age_days=1)    # taze

    removed = telemetry_retention.RetentionWorker()._purge_system_events()

    assert removed == 40
    assert _count(session_factory, SystemEvent) == 10


def test_age_purge_applies_to_protected_categories_too(session_factory, monkeypatch):
    """SURE bazli pencere HER kategoriye uygulanir — koruma yalnizca FIFO icin.

    2 yillik saklama karari tum olaylar icindir; guvenlik kayitlari da 730
    gun sonra duser. Koruma, tavan asildiginda 'once neyi feda ederiz'
    sorusuna aittir.
    """
    monkeypatch.setattr(settings, "system_events_retention_days", 730)
    monkeypatch.setattr(settings, "system_events_max_rows", 0)
    monkeypatch.setattr(settings, "retention_delete_batch", 1000)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 50)
    _seed_events(session_factory, 15, age_days=800, category="security")

    removed = telemetry_retention.RetentionWorker()._purge_system_events()

    assert removed == 15
    assert _count(session_factory, SystemEvent) == 0


def test_system_events_fifo_cap_drops_oldest(session_factory, monkeypatch):
    """Adet tavani asilinca EN ESKI gurultu kayitlari dusmeli, yeniler kalmali."""
    monkeypatch.setattr(settings, "system_events_retention_days", 730)
    monkeypatch.setattr(settings, "system_events_max_rows", 20)
    monkeypatch.setattr(settings, "retention_delete_batch", 1000)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 50)
    _seed_events(session_factory, 50, age_days=1)  # hepsi taze -> sure silmez

    removed = telemetry_retention.RetentionWorker()._purge_system_events()

    assert removed == 30
    assert _count(session_factory, SystemEvent) == 20

    # Kalanlar EN YENI 20 olmali (id'si en buyuk olanlar).
    db = session_factory()
    try:
        ids = [r for r in db.scalars(select(SystemEvent.id).order_by(SystemEvent.id.asc())).all()]
    finally:
        db.close()
    assert ids == list(range(31, 51)), "FIFO ters calisiyor: yeniler silinmis"


def test_fifo_cap_never_drops_audit_categories(session_factory, monkeypatch):
    """FIFO tavani DENETIM kayitlarina DOKUNMAMALI.

    Bu testin somut gerekcesi var: naive bir 'en eskiyi sil' FIFO'su tam
    tersini yapar. Bir olay firtinasinda en eski satirlar genelde kurulum
    donemindeki security/auth/license kayitlaridir; yani gecici telemetri
    gurultusu ugruna denetim izinin en degerli kismi silinir.

    Senaryo: 10 ESKI guvenlik kaydi + 40 YENI telemetri gurultusu, tavan 20.
    Beklenen: yalnizca telemetri satirlari duser, 10 guvenlik kaydi AYNEN kalir.
    """
    monkeypatch.setattr(settings, "system_events_retention_days", 730)
    monkeypatch.setattr(settings, "system_events_max_rows", 20)
    monkeypatch.setattr(settings, "retention_delete_batch", 1000)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 50)
    # Once (en eski id'ler) guvenlik kayitlari, sonra telemetri gurultusu.
    _seed_events(session_factory, 10, age_days=1, category="security", severity="warning")
    _seed_events(session_factory, 40, age_days=1, category="telemetry")

    telemetry_retention.RetentionWorker()._purge_system_events()

    db = session_factory()
    try:
        remaining = db.execute(
            select(SystemEvent.category, func.count(SystemEvent.id)).group_by(SystemEvent.category)
        ).all()
    finally:
        db.close()
    counts = {c: n for c, n in remaining}
    assert counts.get("security") == 10, (
        f"FIFO denetim kaydi sildi: {counts} — guvenlik kayitlari korunmaliydi"
    )
    assert counts.get("telemetry", 0) < 40, "gurultu satirlari dusurulmeliydi"


def test_fifo_cap_protects_warning_and_error_severities(session_factory, monkeypatch):
    """Gurultu kategorisinde bile warning/error seviyesi FIFO ile silinmez.

    `outbound` dusurulebilir bir kategori ama oradaki bir `error` satiri
    entegrasyon arizasinin kanitidir; info gurultusuyle ayni kefeye konmaz.
    """
    monkeypatch.setattr(settings, "system_events_retention_days", 730)
    monkeypatch.setattr(settings, "system_events_max_rows", 5)
    monkeypatch.setattr(settings, "retention_delete_batch", 1000)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 50)
    _seed_events(session_factory, 10, age_days=1, category="outbound", severity="error")
    _seed_events(session_factory, 20, age_days=1, category="outbound", severity="info")

    telemetry_retention.RetentionWorker()._purge_system_events()

    db = session_factory()
    try:
        errors = db.scalar(
            select(func.count(SystemEvent.id)).where(SystemEvent.severity == "error")
        )
    finally:
        db.close()
    assert errors == 10, "error seviyeli outbound kayitlari FIFO ile silinmemeli"


def test_fifo_cap_disabled_when_zero(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "system_events_retention_days", 730)
    monkeypatch.setattr(settings, "system_events_max_rows", 0)
    monkeypatch.setattr(settings, "retention_delete_batch", 1000)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 50)
    _seed_events(session_factory, 50, age_days=1)

    removed = telemetry_retention.RetentionWorker()._purge_system_events()

    assert removed == 0
    assert _count(session_factory, SystemEvent) == 50


# ---------------------------------------------------------------------------
# outbox_events — SAHADA PATLAYAN KUSUR
#
# Olcum (2026-08-03, 100 cihaz): tablo 1.7 GB / 2.32M satir, EN ESKI SATIR 36
# DAKIKALIK — ayarli pencere 15 dakika oldugu halde. Silme hizi (1.000 satir/sn)
# uretimin (~1.074 satir/sn) ALTINDAYDI.
#
# Buradaki testler UC ayri davranisi ayirir; ucu de yanlis yapilirsa sonuc
# ya veri kaybi ya sinirsiz buyume olur.
# ---------------------------------------------------------------------------

def test_outbox_purge_ZAMANLAYICIYA_KAYITLI():
    """Kayitli olmayan purge SESSIZCE hic kosmaz.

    Sahadaki kusur tam da "temizlik var ama yetismiyor" idi; temizligin
    baska bir thread'e tasinmasi sirasinda kayit unutulursa sonuc daha
    kotusu olur: hic kosmaz ve bu ancak disk dolunca fark edilir.
    """
    jobs = telemetry_retention.RetentionWorker()._jobs()
    kayit = {j[0]: j for j in jobs}
    assert "outbox_events" in kayit, "outbox purge retention worker'a kayitli degil"

    _label, periyot_fn, fn, _last = kayit["outbox_events"]
    assert fn.__name__ == "purge_outbox_events"
    assert periyot_fn() == settings.outbox_purge_interval_sec, (
        "outbox purge periyodu ayardan okunmuyor"
    )


def _seed_outbox(
    factory,
    count: int,
    *,
    prefix: str,
    published: bool = True,
    published_age_min: float = 0.0,
    dead_letter_age_days: float | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    db = factory()
    try:
        for i in range(count):
            db.add(
                OutboxEvent(
                    topic="telemetry.raw_received",
                    dedup_key=f"{prefix}-{i}",
                    payload_json="{}",
                    published=published,
                    created_at=now,
                    published_at=(
                        now - timedelta(minutes=published_age_min) if published else None
                    ),
                    dead_letter_at=(
                        None
                        if dead_letter_age_days is None
                        else now - timedelta(days=dead_letter_age_days)
                    ),
                )
            )
        db.commit()
    finally:
        db.close()


def test_outbox_yayinlanmis_ESKI_satirlar_siliniyor(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "outbox_published_retention_minutes", 15)
    monkeypatch.setattr(settings, "retention_delete_batch", 1000)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 50)
    _seed_outbox(session_factory, 40, prefix="old", published_age_min=60)
    _seed_outbox(session_factory, 10, prefix="new", published_age_min=1)

    removed = telemetry_retention.RetentionWorker().purge_outbox_events()

    assert removed == 40
    assert _count(session_factory, OutboxEvent) == 10


def test_outbox_YAYINLANMAMIS_satira_ASLA_dokunulmuyor(session_factory, monkeypatch):
    """At-least-once'in tek dayanagi.

    Bu satirlar teslim edilmeyi BEKLIYOR. Yaslari ne olursa olsun silinmeleri
    KALICI VERI KAYBIDIR: SCADA'ya hic gitmemis bir ariza olayi geri gelmez.
    """
    monkeypatch.setattr(settings, "outbox_published_retention_minutes", 15)
    monkeypatch.setattr(settings, "retention_delete_batch", 1000)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 50)
    _seed_outbox(session_factory, 25, prefix="pending", published=False)

    removed = telemetry_retention.RetentionWorker().purge_outbox_events()

    assert removed == 0
    assert _count(session_factory, OutboxEvent) == 25, (
        "yayinlanmamis outbox satiri silinmis — teslim edilmemis olay kayboldu"
    )


def test_outbox_DEAD_LETTER_kisa_pencereye_TAKILMIYOR(session_factory, monkeypatch):
    """Dead-letter satir 15 DAKIKADA degil, kendi (14 gun) penceresinde duser.

    Gerekce: `last_error` sutunu "SCADA'ya su olay neden gitmedi" sorusunun
    TEK cevabi. Yayinlanmislarla ayni kefeye konursa kanit, operator sorunu
    fark etmeden silinir.
    """
    monkeypatch.setattr(settings, "outbox_published_retention_minutes", 15)
    monkeypatch.setattr(settings, "outbox_dead_letter_retain_days", 14)
    monkeypatch.setattr(settings, "retention_delete_batch", 1000)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 50)
    # 1 saat once damgalanmis: yayinlanmis penceresi (15 dk) coktan gecti.
    _seed_outbox(
        session_factory, 12, prefix="dl-taze", published=False,
        dead_letter_age_days=1.0 / 24.0,
    )

    removed = telemetry_retention.RetentionWorker().purge_outbox_events()

    assert removed == 0
    assert _count(session_factory, OutboxEvent) == 12, (
        "dead-letter satir kisa pencereye takilmis — teshis kaniti kayboldu"
    )


def test_outbox_DEAD_LETTER_kendi_penceresi_dolunca_siliniyor(
    session_factory, monkeypatch
):
    """Uzun tutmak = sonsuza kadar tutmak DEGIL; aksi halde bir hata
    firtinasi tabloyu kalici sisirir."""
    monkeypatch.setattr(settings, "outbox_published_retention_minutes", 15)
    monkeypatch.setattr(settings, "outbox_dead_letter_retain_days", 14)
    monkeypatch.setattr(settings, "retention_delete_batch", 1000)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 50)
    _seed_outbox(
        session_factory, 7, prefix="dl-eski", published=False, dead_letter_age_days=20
    )
    _seed_outbox(
        session_factory, 5, prefix="dl-yeni", published=False, dead_letter_age_days=2
    )

    removed = telemetry_retention.RetentionWorker().purge_outbox_events()

    assert removed == 7
    assert _count(session_factory, OutboxEvent) == 5


def test_outbox_silme_PARTI_PARTI_ve_tur_TAVANLI(session_factory, monkeypatch):
    """Tek DELETE ile milyonlarca satir silmek WAL'i patlatir, tabloyu kilitler.

    batch=10, tavan=3 -> bir tetikte en fazla 30 satir. Kalanlar sonraki
    tetige kalmali; tavan sessizce "hepsini sildim" yalanini soylememeli.
    """
    monkeypatch.setattr(settings, "outbox_published_retention_minutes", 15)
    monkeypatch.setattr(settings, "retention_delete_batch", 10)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 3)
    _seed_outbox(session_factory, 100, prefix="old", published_age_min=60)

    removed = telemetry_retention.RetentionWorker().purge_outbox_events()

    assert removed == 30
    assert _count(session_factory, OutboxEvent) == 70


def test_outbox_penceresi_YENIDEN_TESLIM_tabani_altina_INEMEZ(
    session_factory, monkeypatch
):
    """Env'e 0 yazan operator (ya da agresif disk_guard) dedup korumasini
    mesaj HALA yeniden teslim edilebilirken kaldirmamali."""
    monkeypatch.setattr(settings, "outbox_published_retention_minutes", 0)
    monkeypatch.setattr(settings, "retention_delete_batch", 1000)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 50)
    taban_dk = telemetry_retention.REDELIVERY_WINDOW_SEC / 60.0
    # Taban penceresinin ICINDE (yani korunmasi gereken) satirlar.
    _seed_outbox(
        session_factory, 9, prefix="taze", published_age_min=taban_dk / 2.0
    )

    removed = telemetry_retention.RetentionWorker().purge_outbox_events()

    assert removed == 0, (
        "0 dakika verilince taban uygulanmadi — dedup korumasi mesaj hala "
        "yeniden teslim edilebilirken kalkti"
    )
    assert _count(session_factory, OutboxEvent) == 9


def test_disk_guard_DEAD_LETTER_a_dokunmuyor(monkeypatch):
    """Disk baskisi, hata ayiklama kanitini yok etme gerekcesi olamaz.

    Disk guard outbox purge'unu KISALTILMIS bir `retention_minutes` ile
    cagirabilir; ama `dead_letter_days` gecerse damgali satirlar da o
    kisaltilmis pencereye takilir ve `last_error` kaniti disk baskisiyla
    silinir. Dosyanin "NELERE ASLA DOKUNULMAZ" ilkesi buna izin vermez.
    """
    from app.services import disk_guard

    cagrilar: list[dict] = []

    class _Casus:
        # Gercek imza `RetentionWorker(*, paced=...)`; taklit onu kabul
        # etmezse test, kodun degil taklidin eksikligiyle patlar.
        def __init__(self, **_kw):
            pass

        def purge_processed_messages(self, **kw):
            return 0

        def purge_telemetry(self, **kw):
            return 0

        def purge_outbox_events(self, **kw):
            cagrilar.append(kw)
            return 0

    monkeypatch.setattr(telemetry_retention, "RetentionWorker", _Casus)

    disk_guard._relieve_aggressive()

    assert cagrilar, "disk guard outbox purge'unu hic cagirmadi"
    assert "retention_minutes" in cagrilar[0]
    assert "dead_letter_days" not in cagrilar[0], (
        "disk guard dead-letter penceresini kisaltiyor — teshis kaniti "
        "disk baskisiyla siliniyor"
    )


def test_processed_messages_window_covers_redelivery_by_wide_margin():
    """Idempotency penceresi gercek redelivery penceresinden COK buyuk olmali.

    Gercek pencere = ack_wait(60sn) x max_deliver. Bu testin amaci birinin
    retention'i "disk kazanalim" diye redelivery penceresinin altina
    dusurmesini engellemek — o durumda duplicate telemetri yazilir.
    """
    redelivery_sec = 60 * settings.nats_worker_max_deliver
    window_sec = settings.processed_messages_retention_hours * 3600
    assert window_sec >= redelivery_sec * 10, (
        f"processed_messages penceresi ({window_sec}sn) redelivery penceresinin "
        f"({redelivery_sec}sn) 10 katindan kisa — duplicate riski."
    )
