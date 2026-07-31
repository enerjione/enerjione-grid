"""Cevrimdisi harita karolari: matematik, oncelik politikasi, kuyruk.

Testler ASLA internete cikmaz — `fetch_tile` her yerde sahte ile degistirilir.
"""

from __future__ import annotations

import math
import time

import pytest

from app.services import map_tile_service as mts


PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100
# Batman civari kucuk bir dikdortgen: (guney, bati, kuzey, dogu)
BBOX = (37.85, 41.05, 37.92, 41.20)


@pytest.fixture
def tiles(tmp_path, monkeypatch):
    """Izole karo dizini + sahte yukari akis. Sayac ve paketler sifirlanir."""
    monkeypatch.setattr(mts.settings, "map_tile_dir", str(tmp_path))
    monkeypatch.setattr(mts.settings, "map_tile_request_delay_sec", 0.0)
    monkeypatch.setattr(mts.settings, "map_tile_prefer_online", True)
    monkeypatch.setattr(mts.settings, "map_tile_online_fallback", True)
    monkeypatch.setattr(mts, "_packs", {})
    monkeypatch.setattr(mts, "_loaded", True)
    monkeypatch.setattr(mts, "_cache_bytes", None)
    monkeypatch.setattr(mts, "_offline_until", 0.0)

    state = {"online": True, "calls": 0}

    def fake_fetch(layer, z, x, y):
        state["calls"] += 1
        if not state["online"]:
            raise RuntimeError("Name or service not known")
        return PNG

    monkeypatch.setattr(mts, "fetch_tile", fake_fetch)
    return state


def _drain(timeout: float = 20.0) -> None:
    """Kuyruk bosalana kadar bekle."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(p.status in ("pending", "running") for p in mts.list_packs()):
            return
        time.sleep(0.02)
    raise AssertionError("Indirme kuyrugu zaman asimina ugradi")


# ---------------------------------------------------------------------------
# Slippy-map matematigi
# ---------------------------------------------------------------------------
def _num2deg(x: int, y: int, z: int) -> tuple[float, float]:
    """deg2num'un tersi (OSM wiki formulu) -> karonun sol UST kosesi."""
    n = 2.0**z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


@pytest.mark.parametrize(
    "lat,lon,zoom",
    [
        (39.93, 32.86, 12),   # Ankara
        (41.0082, 28.9784, 15),  # Istanbul
        (37.87, 41.13, 13),   # Batman
        (-33.8688, 151.2093, 8),  # guney yarimkure
        (51.5074, -0.1278, 10),   # bati boylam (negatif)
    ],
)
def test_deg2num_tile_contains_point(lat, lon, zoom):
    """Donen karonun sinirlari verilen noktayi KAPSAMALI.

    Sabit beklenen degerler yerine gidis-donus: formul yanlis olursa nokta
    karonun disina duser.
    """
    x, y = mts.deg2num(lat, lon, zoom)
    top, left = _num2deg(x, y, zoom)
    bottom, right = _num2deg(x + 1, y + 1, zoom)
    assert bottom <= lat <= top
    assert left <= lon <= right


def test_tile_range_corners_match_deg2num():
    zoom = 14
    x0, y0, x1, y1 = mts.tile_range(BBOX, zoom)
    south, west, north, east = BBOX
    assert (x0, y0) == mts.deg2num(north, west, zoom)  # kuzeybati
    assert (x1, y1) == mts.deg2num(south, east, zoom)  # guneydogu


def test_count_tiles_quadruples_per_zoom():
    """Her zoom kademesi karo sayisini ~4 katina cikarir.

    Indirme sinirlarinin neden dusuk tutuldugunu sabitler.
    """
    z15 = mts.count_tiles(BBOX, 15, 15)
    z16 = mts.count_tiles(BBOX, 16, 16)
    assert 3.0 < z16 / z15 < 5.0


# ---------------------------------------------------------------------------
# Dogrulama / sinirlar
# ---------------------------------------------------------------------------
def test_estimate_rejects_area_over_limit(tiles, monkeypatch):
    monkeypatch.setattr(mts.settings, "map_tile_max_pack_tiles", 100)
    with pytest.raises(mts.MapTileError) as exc:
        mts.estimate(BBOX, "osm", 10, 17)
    assert exc.value.code == "MAP_AREA_TOO_LARGE"


def test_estimate_rejects_zoom_over_limit(tiles):
    with pytest.raises(mts.MapTileError) as exc:
        mts.estimate(BBOX, "osm", 10, 19)
    assert exc.value.code == "MAP_ZOOM_TOO_HIGH"


def test_estimate_rejects_inverted_bbox(tiles):
    with pytest.raises(mts.MapTileError) as exc:
        mts.estimate((37.92, 41.20, 37.85, 41.05), "osm", 10, 14)
    assert exc.value.code == "MAP_BBOX_INVALID"


def test_unknown_layer_rejected(tiles):
    """Istemci katman ANAHTARI gonderir; keyfi adrese proxy yapilamaz (SSRF)."""
    with pytest.raises(mts.MapTileError) as exc:
        mts.get_tile("../../etc/passwd", 12, 1, 1)
    assert exc.value.code == "MAP_LAYER_UNKNOWN"


# ---------------------------------------------------------------------------
# Oncelik politikasi: internet varken indirilen kopya KULLANILMAZ
# ---------------------------------------------------------------------------
def test_online_preferred_even_when_cached(tiles):
    mts.store_tile("osm", 12, 2421, 1551, PNG)
    _data, source = mts.get_tile("osm", 12, 2421, 1551)
    assert source == "upstream"


def test_falls_back_to_cache_when_offline(tiles):
    mts.get_tile("osm", 12, 2421, 1551)  # onbellege girsin
    tiles["online"] = False
    _data, source = mts.get_tile("osm", 12, 2421, 1551)
    assert source == "cache"


def test_offline_cooldown_stops_retrying_upstream(tiles):
    """Baglanti koptuktan sonra her karo ayri ayri zaman asimi BEKLEMEMELI."""
    mts.get_tile("osm", 12, 2421, 1551)
    tiles["online"] = False
    mts.get_tile("osm", 12, 2421, 1551)  # ilk basarisizlik -> cevrimdisi isareti
    before = tiles["calls"]
    for _ in range(25):
        mts.get_tile("osm", 12, 2421, 1551)
    assert tiles["calls"] == before


def test_recovers_when_connection_returns(tiles):
    mts.get_tile("osm", 12, 2421, 1551)
    tiles["online"] = False
    mts.get_tile("osm", 12, 2421, 1551)
    tiles["online"] = True
    mts._mark_online()
    _data, source = mts.get_tile("osm", 12, 2421, 1551)
    assert source == "upstream"


def test_cache_first_when_prefer_online_disabled(tiles, monkeypatch):
    monkeypatch.setattr(mts.settings, "map_tile_prefer_online", False)
    mts.store_tile("osm", 12, 2421, 1551, PNG)
    before = tiles["calls"]
    _data, source = mts.get_tile("osm", 12, 2421, 1551)
    assert source == "cache"
    assert tiles["calls"] == before  # yukari akisa hic gidilmedi


def test_offline_and_missing_tile_raises(tiles, monkeypatch):
    monkeypatch.setattr(mts.settings, "map_tile_online_fallback", False)
    with pytest.raises(mts.MapTileError) as exc:
        mts.get_tile("osm", 12, 999, 999)
    assert exc.value.code == "MAP_TILE_OFFLINE"


# ---------------------------------------------------------------------------
# Onbellek boyutu sayaci (harita acilis hizinin asil belirleyicisi)
# ---------------------------------------------------------------------------
def test_cache_size_counter_matches_disk(tiles):
    for i in range(40):
        mts.store_tile("osm", 14, 9000 + i, 6000, PNG)
    assert mts.cache_size_bytes() == mts._walk_cache_size()


def test_cache_size_does_not_walk_disk_on_every_call(tiles, monkeypatch):
    """REGRESYON: eskiden her karo istegi tum agaci tariyordu.

    `_walk_cache_size` ilk olcumden sonra BIR DAHA cagrilmamali; aksi halde
    on binlerce karoda harita acilisi dakikalar suruyordu.
    """
    mts.store_tile("osm", 14, 9000, 6000, PNG)
    mts.cache_size_bytes()  # ilk olcum: bir kez taramak serbest

    walks = {"n": 0}
    real = mts._walk_cache_size

    def counting_walk():
        walks["n"] += 1
        return real()

    monkeypatch.setattr(mts, "_walk_cache_size", counting_walk)
    for i in range(50):
        mts.get_tile("osm", 13, 4800 + i, 3100)
    assert walks["n"] == 0


def test_delete_pack_updates_size_counter(tiles):
    pack = mts.start_pack(name="A", layer="osm", bbox=BBOX, zoom_min=12, zoom_max=13)
    _drain()
    assert mts.cache_size_bytes() > 0
    mts.delete_pack(pack.id, remove_tiles=True)
    assert mts.cache_size_bytes() == mts._walk_cache_size()


# ---------------------------------------------------------------------------
# Indirme kuyrugu
# ---------------------------------------------------------------------------
def test_multiple_areas_are_queued_not_rejected(tiles):
    """Parca parca indirme: arka arkaya secilen alanlar SIRAYA girer."""
    ids = [
        mts.start_pack(
            name=f"A{i}",
            layer="osm",
            bbox=(37.85, 41.05 + i * 0.3, 37.90, 41.15 + i * 0.3),
            zoom_min=12,
            zoom_max=13,
        ).id
        for i in range(3)
    ]
    _drain()
    assert all(mts.get_pack(pid).status == "done" for pid in ids)


def test_queue_is_fifo(tiles):
    names = ["ilk", "orta", "son"]
    for i, name in enumerate(names):
        mts.start_pack(
            name=name,
            layer="osm",
            bbox=(37.85, 41.05 + i * 0.3, 37.90, 41.15 + i * 0.3),
            zoom_min=12,
            zoom_max=12,
        )
    _drain()
    finished = sorted(mts.list_packs(), key=lambda p: p.finished_at)
    assert [p.name for p in finished] == names


def test_restart_skips_tiles_already_on_disk(tiles):
    """Sürdürme UCUZ olmali: diskteki karolar tekrar indirilmez."""
    pack = mts.start_pack(name="A", layer="osm", bbox=BBOX, zoom_min=12, zoom_max=13)
    _drain()
    pack = mts.get_pack(pack.id)
    pack.status = "failed"  # yarim kalmis gibi isaretle

    before = tiles["calls"]
    mts.restart_pack(pack.id)
    _drain()
    assert mts.get_pack(pack.id).status == "done"
    assert tiles["calls"] == before  # tek bir karo bile yeniden indirilmedi


def test_restart_rejects_running_pack(tiles):
    pack = mts.start_pack(name="A", layer="osm", bbox=BBOX, zoom_min=12, zoom_max=12)
    # pending veya running iken tekrar kuyruga atilamaz
    assert mts.restart_pack(pack.id) is None
    _drain()


def test_pack_fails_when_upstream_unreachable(tiles):
    tiles["online"] = False
    pack = mts.start_pack(name="A", layer="osm", bbox=BBOX, zoom_min=12, zoom_max=12)
    _drain()
    done = mts.get_pack(pack.id)
    assert done.status == "failed"
    assert done.tile_done == 0


def test_partial_failure_still_counts_as_done(tiles, monkeypatch):
    """Bazi karolar inmezse is BASARISIZ sayilmaz; inenler kullanilabilir."""
    calls = {"n": 0}

    def flaky(layer, z, x, y):
        calls["n"] += 1
        if (x + y) % 3 == 0:
            raise RuntimeError("upstream 503")
        return PNG

    monkeypatch.setattr(mts, "fetch_tile", flaky)
    pack = mts.start_pack(name="A", layer="osm", bbox=BBOX, zoom_min=12, zoom_max=13)
    _drain()
    done = mts.get_pack(pack.id)
    assert done.status == "done"
    assert done.tile_failed > 0
    assert done.tile_done > 0
