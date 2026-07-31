"""JetStream bus kurulana kadar YENIDEN DENENIR (denetim A6).

YASANAN SORUN
-------------
`start_bus_if_enabled()` TEK bir deneme yapiyordu; basarisiz olursa `_bus`
None'da KILITLENIYORDU ve hicbir kod yolu onu yeniden baslatmiyordu.

SENARYO
-------
NATS container'i cokup yeniden baslarken backend de yeniden baslar (OOM kill,
`update.sh backend`, watchdog). `depends_on: service_healthy` YALNIZCA
`compose up` siralamasinda gecerlidir — restart'ta UYGULANMAZ.

Backend NATS hazir olmadan kalkar, tek deneme basarisiz olur ve NATS saniyeler
icinde saglikli hale gelse bile telemetri BIR DAHA HIC yayinlanmaz:
  * outbox_flush_worker her turda RuntimeError alir,
  * cihazlar arayuzde "Kesik" gorunur,
  * `outbox_events` published=False satirlarla SINIRSIZ buyur
    (purge_published_outbox yalnizca yayinlanmislari siler).

/health 503 doner ama compose'da autoheal yok ve `restart: unless-stopped`
healthcheck'e TEPKI VERMEZ. Basinda kimse olmayan bir saha cihazi aylarca veri
kaydetmeden ayakta kalabilir.
"""

from __future__ import annotations

import inspect

from app.services import jetstream_bus


def test_ilk_deneme_basarisizsa_GOZETMEN_baslar():
    """Testin ozu: tek deneme yeterli degil."""
    kaynak = inspect.getsource(jetstream_bus.start_bus_if_enabled)
    assert "_ensure_supervisor()" in kaynak, (
        "bus kurulamazsa yeniden deneyen bir gozetmen yok — NATS geri gelse "
        "bile telemetri bir daha hic yayinlanmaz"
    )


def test_gozetmen_bus_yoksa_YENIDEN_dener():
    kaynak = inspect.getsource(jetstream_bus._supervisor_loop)
    assert "_try_start_once" in kaynak
    assert "_bus is not None" in kaynak, "bus varken bosuna yeniden kuruluyor olabilir"


def test_gozetmen_TEK_kez_baslar():
    """Her cagride yeni thread acsaydi surec thread ile dolardi."""
    kaynak = inspect.getsource(jetstream_bus._ensure_supervisor)
    assert "is_alive()" in kaynak


def test_kapanista_gozetmen_ONCE_durdurulur():
    """Aksi halde kapanis sirasinda bus'i yeniden kurup asili baglanti birakir."""
    kaynak = inspect.getsource(jetstream_bus.stop_bus)
    stop_idx = kaynak.index("_supervisor_stop.set()")
    lock_idx = kaynak.index("with _lock")
    assert stop_idx < lock_idx, "gozetmen bus kapatildiktan SONRA durduruluyor"


def test_yeniden_deneme_araligi_MAKUL():
    """Cok sik: NATS'i doverdi. Cok seyrek: kesinti gereksiz uzardi."""
    assert 5 <= jetstream_bus._RETRY_INTERVAL_SEC <= 60


def test_gozetmen_daemon_thread():
    """Daemon olmazsa surec kapanmaz (shutdown asili kalir)."""
    kaynak = inspect.getsource(jetstream_bus._ensure_supervisor)
    assert "daemon=True" in kaynak


# --------------------------------------------------------------- davranis


def test_bus_kurulunca_gozetmen_YENIDEN_KURMAZ(monkeypatch):
    """Idempotency: bus ayaktayken `_try_start_once` erken donmeli."""
    denemeler = []

    class _SahteBus:
        def start(self):
            denemeler.append(1)
            return True

        def stop(self):
            return None

    monkeypatch.setattr(jetstream_bus, "JetStreamBus", lambda **k: _SahteBus())
    monkeypatch.setattr(jetstream_bus, "_bus", None)

    jetstream_bus._try_start_once(quiet=True)
    assert len(denemeler) == 1

    # Ikinci cagri yeni bir bus KURMAMALI.
    jetstream_bus._try_start_once(quiet=True)
    assert len(denemeler) == 1, "bus ayaktayken yeniden kuruldu"

    monkeypatch.setattr(jetstream_bus, "_bus", None)


def test_basarisiz_deneme_bus_u_NONE_birakir(monkeypatch):
    """Yarim kurulmus bir bus saklanirsa publish'ler sessizce kaybolurdu."""

    class _BozukBus:
        def start(self):
            return False

        def stop(self):
            return None

    monkeypatch.setattr(jetstream_bus, "JetStreamBus", lambda **k: _BozukBus())
    monkeypatch.setattr(jetstream_bus, "_bus", None)

    jetstream_bus._try_start_once(quiet=True)
    assert jetstream_bus.get_bus() is None
