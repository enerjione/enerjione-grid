"""Cevrimdisi harita karolari — disk onbellegi, alan indirme, servis.

MIMARI: TUM karolar backend uzerinden gecer (`/map-tiles/{layer}/{z}/{x}/{y}`).
Tek adres hem cevrimici hem cevrimdisi calisir; "alan indirme" bu onbellegi
ONDEN doldurmaktan ibarettir.

ONCELIK (varsayilan: internet onceligi -- `map_tile_prefer_online`):
    internet VAR  -> karo internetten gelir (guncel kalir), kopyasi diske yazilir
    internet YOK  -> indirilmis kopya kullanilir
Baglanti tek bir istekte patlarsa kisa sure "cevrimdisi" isaretlenir ve o sure
boyunca dogrudan diskten servis edilir; aksi halde her karo ayri ayri zaman
asimi bekler ve harita hic acilmaz.

Alternatif (frontend'in dogrudan internete gitmesi + ayri bir offline katman)
denenmedi cunku Leaflet'te "once yerel, olmazsa uzak" davranisi istemci
tarafinda temiz kurulamiyor; sunucu tarafinda tek satir.

ONEMLI — KARO KAYNAGI VE LISANS:
    Varsayilan yukari akislar (OSM, Esri, OpenTopoMap, CARTO) ucretsiz servis
    verir ama TOPLU INDIRMEYE izin vermez. Bu modul bu yuzden:
      * indirmeyi kullanicinin sectigi KUCUK bir dikdortgenle sinirlar,
      * zoom ust sinirini dusuk tutar (karo sayisi 4^z ile patlar),
      * es zamanli baglantiyi 2 ile sinirlar ve her istek arasi bekler,
      * tanimlayici bir User-Agent gonderir.
    Yine de yuzlerce cihaza buyuk alanlar dagitacaksaniz kendi karo
    sunucunuzu (veya lisansli bir saglayiciyi) `E1_MAP_TILE_UPSTREAM_*` ile
    tanimlayin; asagidaki varsayilanlar saha basina kucuk alan icindir.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import requests

from app.core.config import settings


# ---------------------------------------------------------------------------
# Katman kayitlari
# ---------------------------------------------------------------------------
# Frontend katman ANAHTARINI gonderir, URL'i degil: boylece yukari akis adresi
# tek yerde durur ve istemci keyfi bir adrese proxy yaptiramaz (SSRF).
@dataclass(frozen=True)
class TileLayerDef:
    key: str
    label: str
    url_template: str
    #: Saglayicinin GERCEKTEN karo urettigi en yuksek zoom.
    #:
    #: Bunun ustunde karo istemek bos donmuyor — Esri ornegin uzerinde
    #: "Map data not yet available" yazan GECERLI bir PNG'yi HTTP 200 ile
    #: dondurur. Yani sinir asildiginda hata yolu tetiklenmez, sorun dogrudan
    #: goruntuye karisir ve o karo cevrimdisi onbellege de yazilir.
    #: Istemci tarafinda karsiligi Leaflet `maxNativeZoom`'udur; ustundeki
    #: seviyeler son gercek karo buyutulerek gosterilir.
    max_zoom: int
    subdomains: tuple[str, ...] = ()
    attribution: str = ""


LAYERS: dict[str, TileLayerDef] = {
    "osm": TileLayerDef(
        key="osm",
        label="Sokak (OSM)",
        url_template="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        max_zoom=19,
        subdomains=("a", "b", "c"),
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    ),
    "satellite": TileLayerDef(
        key="satellite",
        label="Uydu (Esri)",
        # DIKKAT: Esri {z}/{y}/{x} sirasi kullanir, digerleri {z}/{x}/{y}.
        url_template="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        # 19 DEGIL 18: Esri World Imagery Turkiye kirsalinda 18'de biter ve
        # 19 istendiginde "Map data not yet available" karosu doner (bkz.
        # max_zoom aciklamasi). Frontend `maxNativeZoom` ile ayni sinirda.
        max_zoom=18,
        attribution="Tiles &copy; Esri — Source: Esri, Maxar, Earthstar Geographics",
    ),
    "topo": TileLayerDef(
        key="topo",
        label="Topografya (OpenTopoMap)",
        url_template="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        max_zoom=17,
        subdomains=("a", "b", "c"),
        attribution='Map data: &copy; OpenStreetMap, SRTM | Style: OpenTopoMap (CC-BY-SA)',
    ),
    "dark": TileLayerDef(
        key="dark",
        label="Karanlik (CARTO)",
        url_template="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
        max_zoom=20,
        subdomains=("a", "b", "c", "d"),
        attribution='&copy; OpenStreetMap &copy; CARTO',
    ),
}


class MapTileError(Exception):
    """Kullaniciya gosterilebilir hata (kod + mesaj)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _ensure_zoom_supported(layer: TileLayerDef, z: int) -> None:
    """Katmanin veri sagladigi zoom araligi disinda ISTEK YAPILMAZ.

    Sinirin ustunde saglayici hata dondurmez; Esri ornegin uzerinde
    "Map data not yet available" yazan gecerli bir PNG'yi HTTP 200 ile
    verir. Kontrol edilmezse o goruntu hem kullaniciya cizilir hem de
    cevrimdisi onbellege KALICI olarak yazilir — sonra internet olsa bile
    o karede gri "veri yok" gorulur.

    Guncel istemci `maxNativeZoom` sayesinde bu isteklere zaten cikmaz;
    burasi eski sekmeler ve dogrudan API cagrilari icin ikinci savunma.
    """
    if z < 0 or z > layer.max_zoom:
        raise MapTileError(
            "MAP_ZOOM_UNSUPPORTED",
            f"{layer.label} katmani en fazla zoom {layer.max_zoom} destekler (istenen: {z})",
        )


# ---------------------------------------------------------------------------
# Slippy map matematigi
# ---------------------------------------------------------------------------
def deg2num(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """WGS84 -> karo indeksi (Web Mercator, OSM 'slippy map' semasi)."""
    lat = max(min(lat, 85.05112878), -85.05112878)
    lat_rad = math.radians(lat)
    n = 2.0**zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    # Kutup/kenar durumlarinda tasabilir; gecerli araliga kirp.
    return max(0, min(int(n) - 1, x)), max(0, min(int(n) - 1, y))


def tile_range(bbox: tuple[float, float, float, float], zoom: int) -> tuple[int, int, int, int]:
    """bbox=(guney, bati, kuzey, dogu) -> (x_min, y_min, x_max, y_max)."""
    south, west, north, east = bbox
    x_min, y_min = deg2num(north, west, zoom)  # kuzeybati -> sol ust
    x_max, y_max = deg2num(south, east, zoom)  # guneydogu -> sag alt
    if x_min > x_max:
        x_min, x_max = x_max, x_min
    if y_min > y_max:
        y_min, y_max = y_max, y_min
    return x_min, y_min, x_max, y_max


def count_tiles(bbox: tuple[float, float, float, float], zoom_min: int, zoom_max: int) -> int:
    total = 0
    for z in range(zoom_min, zoom_max + 1):
        x0, y0, x1, y1 = tile_range(bbox, z)
        total += (x1 - x0 + 1) * (y1 - y0 + 1)
    return total


def _iter_tiles(
    bbox: tuple[float, float, float, float], zoom_min: int, zoom_max: int
) -> Iterator[tuple[int, int, int]]:
    for z in range(zoom_min, zoom_max + 1):
        x0, y0, x1, y1 = tile_range(bbox, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                yield z, x, y


# ---------------------------------------------------------------------------
# Disk
# ---------------------------------------------------------------------------
def _root() -> Path:
    return Path(settings.map_tile_dir)


def tile_path(layer: str, z: int, x: int, y: int) -> Path:
    return _root() / layer / str(z) / str(x) / f"{y}.png"


def _packs_file() -> Path:
    return _root() / "packs.json"


def _walk_cache_size() -> int:
    """Diski gercekten tarar. PAHALI — sadece ilk olcumde cagrilir.

    SADECE KARO dosyalari sayilir. `packs.json` ayni dizinde yasiyor ama
    metadata; hem "disk kullanimi" gostergesinde karo verisi beklenir hem de
    her kayitta boyutu degistigi icin calisan sayac ondan sapardi.
    """
    total = 0
    root = _root()
    if not root.exists():
        return 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".png"):
                continue
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return total


# Onbellek boyutu CALISAN BIR SAYAC olarak tutulur.
#
# Neden: eskiden `cache_size_bytes()` her cagrildiginda tum agaci `os.walk`
# ediyordu ve bu fonksiyon HER KARO ISTEGINDE (kota guard'i) + her `summary`
# cagrisinda (arayuz 2 sn'de bir yeniliyor) calisiyordu. On binlerce dosyada
# bu, harita acilisini dakikalara cikaran asil sebepti. Artik disk yalnizca
# BIR KEZ taranir; sonrasinda yazma/silme sayaci gunceller.
_size_lock = threading.Lock()
_cache_bytes: int | None = None


def cache_size_bytes() -> int:
    global _cache_bytes
    with _size_lock:
        if _cache_bytes is None:
            _cache_bytes = _walk_cache_size()
        return _cache_bytes


def _add_cache_bytes(delta: int) -> None:
    global _cache_bytes
    with _size_lock:
        if _cache_bytes is None:
            return  # henuz olculmedi; ilk olcum zaten dogru degeri alir
        _cache_bytes = max(0, _cache_bytes + delta)


def _reset_cache_size() -> None:
    global _cache_bytes
    with _size_lock:
        _cache_bytes = None


# ---------------------------------------------------------------------------
# Yukari akistan karo cekme
# ---------------------------------------------------------------------------
_session_lock = threading.Lock()
_session: requests.Session | None = None


def _http() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            # Tanimlayici User-Agent: OSM kullanim politikasi bunu SART kosar,
            # jenerik/eksik UA ile istekler engellenir.
            s.headers["User-Agent"] = settings.map_tile_user_agent
            _session = s
        return _session


def _upstream_url(layer: TileLayerDef, z: int, x: int, y: int) -> str:
    url = layer.url_template.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
    if layer.subdomains:
        # Alan adi dagitimi: karo koordinatindan deterministik sec ki ayni
        # karo hep ayni sunucudan gelsin (uzak taraf cache'i isabet etsin).
        url = url.replace("{s}", layer.subdomains[(x + y) % len(layer.subdomains)])
    return url


# --- Baglanti durumu -------------------------------------------------------
# Her karo icin "internet var mi" diye ayri bir kontrol yapmak pahali olurdu.
# Bunun yerine SON SONUCU hatirliyoruz: bir istek patlayinca kisa bir sure
# cevrimdisi sayiyoruz ve o sure boyunca dogrudan diskten servis ediyoruz.
# Aksi halde internet gittiginde her karo ayri ayri zaman asimini bekler ve
# harita dakikalarca acilmazdi.
_conn_lock = threading.Lock()
_offline_until: float = 0.0


def _upstream_ok() -> bool:
    with _conn_lock:
        return time.monotonic() >= _offline_until


def _mark_offline() -> None:
    global _offline_until
    with _conn_lock:
        _offline_until = time.monotonic() + settings.map_tile_offline_cooldown_sec


def _mark_online() -> None:
    global _offline_until
    with _conn_lock:
        _offline_until = 0.0


def is_online() -> bool:
    """Yukari akis su an erisilebilir sayiliyor mu? (arayuz gostergesi)"""
    return _upstream_ok()


def fetch_tile(layer_key: str, z: int, x: int, y: int) -> bytes:
    """Yukari akistan tek karo ceker. Diske YAZMAZ."""
    layer = LAYERS.get(layer_key)
    if layer is None:
        raise MapTileError("MAP_LAYER_UNKNOWN", f"Bilinmeyen katman: {layer_key}")
    _ensure_zoom_supported(layer, z)
    resp = _http().get(_upstream_url(layer, z, x, y), timeout=settings.map_tile_timeout_sec)
    resp.raise_for_status()
    return resp.content


def store_tile(layer_key: str, z: int, x: int, y: int, data: bytes) -> None:
    path = tile_path(layer_key, z, x, y)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomik yazim: yarim dosya okunmasin (paralel indirme + servis birlikte).
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_bytes(data)
    existed = path.exists()
    old_size = path.stat().st_size if existed else 0
    os.replace(tmp, path)
    _add_cache_bytes(len(data) - old_size)


def read_tile(layer_key: str, z: int, x: int, y: int) -> bytes | None:
    try:
        return tile_path(layer_key, z, x, y).read_bytes()
    except (OSError, ValueError):
        return None


def get_tile(layer_key: str, z: int, x: int, y: int) -> tuple[bytes, str]:
    """Karoyu dondurur. -> (icerik, kaynak) ; kaynak: 'cache' | 'upstream'.

    Once disk. Yoksa ve cevrimici cekme aciksa yukari akistan al ve YAZ.
    Boylece kullanicinin gezindigi alan kendiliginden onbellege girer.
    """
    if layer_key not in LAYERS:
        raise MapTileError("MAP_LAYER_UNKNOWN", f"Bilinmeyen katman: {layer_key}")
    _ensure_zoom_supported(LAYERS[layer_key], z)

    def _store(data: bytes) -> None:
        # Gezinme sirasinda biriken onbellek sinirsiz buyumesin.
        if cache_size_bytes() >= settings.map_tile_max_cache_bytes:
            return
        try:
            store_tile(layer_key, z, x, y, data)
        except OSError:
            pass  # disk dolu/izin yok — servis etmeye devam

    if settings.map_tile_prefer_online and settings.map_tile_online_fallback and _upstream_ok():
        # INTERNET ONCELIKLI: baglanti varken guncel karo kullanilir,
        # indirilmis kopya yalnizca yedektir.
        try:
            data = fetch_tile(layer_key, z, x, y)
            _mark_online()
            _store(data)
            return data, "upstream"
        except Exception:  # noqa: BLE001 - internet gitti / uzak sunucu hatasi
            # Sonraki karolar bekleyip tek tek zaman asimina UGRAMASIN diye
            # kisa sureli "cevrimdisi" isaretle; o sure boyunca dogrudan
            # diskten servis edilir.
            _mark_offline()

    cached = read_tile(layer_key, z, x, y)
    if cached is not None:
        return cached, "cache"

    if not settings.map_tile_online_fallback:
        raise MapTileError("MAP_TILE_OFFLINE", "Karo onbellekte yok ve cevrimici cekme kapali")

    # Buraya iki yoldan gelinir: (a) internet onceligi kapali, (b) internet
    # onceliginde yukari akis patladi ve karo onbellekte de yok.
    data = fetch_tile(layer_key, z, x, y)
    _mark_online()
    _store(data)
    return data, "upstream"


# ---------------------------------------------------------------------------
# Alan paketleri (indirme isleri)
# ---------------------------------------------------------------------------
@dataclass
class Pack:
    id: str
    name: str
    layer: str
    bbox: tuple[float, float, float, float]
    zoom_min: int
    zoom_max: int
    tile_total: int
    tile_done: int = 0
    tile_failed: int = 0
    bytes_written: int = 0
    status: str = "pending"  # pending | running | done | failed | cancelled
    error: str = ""
    created_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["bbox"] = list(self.bbox)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Pack":
        data = dict(d)
        data["bbox"] = tuple(data.get("bbox") or (0, 0, 0, 0))  # type: ignore[assignment]
        known = {f for f in Pack.__dataclass_fields__}  # type: ignore[attr-defined]
        return Pack(**{k: v for k, v in data.items() if k in known})


_packs_lock = threading.RLock()
_packs: dict[str, Pack] = {}
_cancel_flags: set[str] = set()
_active_thread: threading.Thread | None = None
_loaded = False


def _load_packs() -> None:
    global _loaded
    with _packs_lock:
        if _loaded:
            return
        _loaded = True
        try:
            raw = json.loads(_packs_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for item in raw if isinstance(raw, list) else []:
            try:
                pack = Pack.from_dict(item)
            except TypeError:
                continue
            # Yeniden baslatma sonrasi "running" kalmis is yoktur; yarim
            # birakilmis say ki kullanici tekrar baslatabilsin.
            if pack.status in ("running", "pending"):
                pack.status = "failed"
                pack.error = "Servis yeniden baslatildi"
            _packs[pack.id] = pack


def _save_packs() -> None:
    with _packs_lock:
        items = [p.to_dict() for p in _packs.values()]
    path = _packs_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def list_packs() -> list[Pack]:
    _load_packs()
    with _packs_lock:
        return sorted(_packs.values(), key=lambda p: p.created_at, reverse=True)


def get_pack(pack_id: str) -> Pack | None:
    _load_packs()
    with _packs_lock:
        return _packs.get(pack_id)


def _validate_request(
    bbox: tuple[float, float, float, float], layer_key: str, zoom_min: int, zoom_max: int
) -> int:
    layer = LAYERS.get(layer_key)
    if layer is None:
        raise MapTileError("MAP_LAYER_UNKNOWN", f"Bilinmeyen katman: {layer_key}")
    south, west, north, east = bbox
    if not (-90 <= south < north <= 90) or not (-180 <= west < east <= 180):
        raise MapTileError("MAP_BBOX_INVALID", "Secilen alan gecersiz")
    if zoom_min < 0 or zoom_max < zoom_min:
        raise MapTileError("MAP_ZOOM_INVALID", "Zoom araligi gecersiz")
    if zoom_max > min(layer.max_zoom, settings.map_tile_max_download_zoom):
        raise MapTileError(
            "MAP_ZOOM_TOO_HIGH",
            f"En fazla zoom {min(layer.max_zoom, settings.map_tile_max_download_zoom)} indirilebilir",
        )
    total = count_tiles(bbox, zoom_min, zoom_max)
    if total > settings.map_tile_max_pack_tiles:
        raise MapTileError(
            "MAP_AREA_TOO_LARGE",
            f"Secilen alan cok buyuk ({total} karo). "
            f"Sinir {settings.map_tile_max_pack_tiles}; alani kucultun veya zoom'u dusurun.",
        )
    return total


def estimate(
    bbox: tuple[float, float, float, float], layer_key: str, zoom_min: int, zoom_max: int
) -> dict:
    """Indirmeden once karo sayisi + kaba boyut tahmini."""
    total = _validate_request(bbox, layer_key, zoom_min, zoom_max)
    return {
        "tile_count": total,
        "estimated_bytes": total * settings.map_tile_avg_bytes,
        "max_tiles": settings.map_tile_max_pack_tiles,
    }


def _run_pack(pack_id: str) -> None:
    pack = get_pack(pack_id)
    if pack is None:
        return
    with _packs_lock:
        pack.status = "running"
    _save_packs()

    written = 0
    done = 0
    failed = 0
    lock = threading.Lock()

    def work(item: tuple[int, int, int]) -> None:
        nonlocal written, done, failed
        z, x, y = item
        if pack_id in _cancel_flags:
            return
        # Zaten indirilmis karoyu tekrar cekme: hem hizli hem yukari akisa
        # saygili (ayni alan iki kez indirilirse ikincisi neredeyse bedava).
        if tile_path(pack.layer, z, x, y).exists():
            with lock:
                done += 1
            return
        try:
            data = fetch_tile(pack.layer, z, x, y)
            store_tile(pack.layer, z, x, y, data)
            with lock:
                written += len(data)
                done += 1
        except Exception:  # noqa: BLE001 - tek karo hatasi isi bitirmemeli
            with lock:
                failed += 1
        # Politika geregi nazik davran: es zamanlilik zaten 2, ustune kisa bekleme.
        time.sleep(settings.map_tile_request_delay_sec)

    try:
        tiles = list(_iter_tiles(pack.bbox, pack.zoom_min, pack.zoom_max))
        with ThreadPoolExecutor(max_workers=settings.map_tile_concurrency) as pool:
            for idx, _ in enumerate(pool.map(work, tiles), start=1):
                if idx % 50 == 0:
                    with _packs_lock:
                        pack.tile_done = done
                        pack.tile_failed = failed
                        pack.bytes_written = written
                    _save_packs()
        with _packs_lock:
            pack.tile_done = done
            pack.tile_failed = failed
            pack.bytes_written = written
            if pack_id in _cancel_flags:
                pack.status = "cancelled"
            elif failed and done == 0:
                pack.status = "failed"
                pack.error = "Hicbir karo indirilemedi (internet yok olabilir)"
            else:
                pack.status = "done"
            pack.finished_at = datetime.now(timezone.utc).isoformat()
    except Exception as exc:  # noqa: BLE001
        with _packs_lock:
            pack.status = "failed"
            pack.error = str(exc)[:300]
            pack.finished_at = datetime.now(timezone.utc).isoformat()
    finally:
        _cancel_flags.discard(pack_id)
        _save_packs()


# --- Kuyruk ----------------------------------------------------------------
# Alanlar TEK TEK degil SIRAYLA indirilir: kullanici birkac kucuk alan secip
# arka arkaya kuyruga atabilir. Ayni anda yalnizca BIR is calisir; paralel
# indirme hem yukari akisi zorlar hem ilerleme takibini anlamsizlastirir.
#
# Buyuk bir alani tek parca indirmek yerine kucuk parcalara bolmek pratikte
# daha saglam: bir parca basarisiz olsa digerleri etkilenmez ve biten parca
# hemen kullanilabilir hale gelir.
def _next_pending() -> Pack | None:
    with _packs_lock:
        pending = [p for p in _packs.values() if p.status == "pending"]
    if not pending:
        return None
    # Eklenme sirasi korunur (FIFO).
    return sorted(pending, key=lambda p: p.created_at)[0]


def _worker_loop() -> None:
    while True:
        pack = _next_pending()
        if pack is None:
            return
        _run_pack(pack.id)


def _ensure_worker() -> None:
    global _active_thread
    with _packs_lock:
        if _active_thread is not None and _active_thread.is_alive():
            return  # calisan isci kuyrugun kalanini da alir
        _active_thread = threading.Thread(
            target=_worker_loop, daemon=True, name="map-pack-worker"
        )
        _active_thread.start()


def start_pack(
    *,
    name: str,
    layer: str,
    bbox: tuple[float, float, float, float],
    zoom_min: int,
    zoom_max: int,
) -> Pack:
    total = _validate_request(bbox, layer, zoom_min, zoom_max)
    _load_packs()
    with _packs_lock:
        pack = Pack(
            id=str(uuid.uuid4()),
            name=name.strip() or "Alan",
            layer=layer,
            bbox=bbox,
            zoom_min=zoom_min,
            zoom_max=zoom_max,
            tile_total=total,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _packs[pack.id] = pack
    _save_packs()
    _ensure_worker()
    return pack


def restart_pack(pack_id: str) -> Pack | None:
    """Yarim kalmis (iptal/basarisiz) bir alani kuyruga geri koyar.

    UCUZ: diskteki karolar atlanir (bkz. `_run_pack`), yalnizca eksikler
    indirilir. Bu yuzden kesintiye ugrayan bir indirmeyi bastan baslatmak
    yerine "devam ettirmek" dogru davranis.
    """
    pack = get_pack(pack_id)
    if pack is None or pack.status in ("pending", "running"):
        return None
    _cancel_flags.discard(pack_id)
    with _packs_lock:
        pack.status = "pending"
        pack.tile_done = 0
        pack.tile_failed = 0
        pack.bytes_written = 0
        pack.error = ""
        pack.finished_at = ""
    _save_packs()
    _ensure_worker()
    return pack


def cancel_pack(pack_id: str) -> bool:
    pack = get_pack(pack_id)
    if pack is None or pack.status not in ("pending", "running"):
        return False
    _cancel_flags.add(pack_id)
    return True


def delete_pack(pack_id: str, *, remove_tiles: bool = False) -> bool:
    """Paket kaydini siler. remove_tiles=True ise alandaki karolari da siler.

    Karolar KATMAN AGACINDA ortak: iki paket ayni karoyu paylasabilir, bu
    yuzden silme varsayilan olarak KAPALI — kayit gider, disk kalir.
    """
    pack = get_pack(pack_id)
    if pack is None:
        return False
    if pack.status in ("pending", "running"):
        _cancel_flags.add(pack_id)
    if remove_tiles:
        for z, x, y in _iter_tiles(pack.bbox, pack.zoom_min, pack.zoom_max):
            path = tile_path(pack.layer, z, x, y)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            try:
                path.unlink()
                _add_cache_bytes(-size)
            except OSError:
                continue
    with _packs_lock:
        _packs.pop(pack_id, None)
    _save_packs()
    return True


def clear_cache() -> None:
    """Tum karo onbellegini siler (paket kayitlari dahil)."""
    root = _root()
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    with _packs_lock:
        _packs.clear()
    _reset_cache_size()
    _save_packs()


def summary() -> dict:
    packs = list_packs()
    return {
        "layers": [
            {
                "key": layer.key,
                "label": layer.label,
                "max_zoom": layer.max_zoom,
                "attribution": layer.attribution,
            }
            for layer in LAYERS.values()
        ],
        "cache_bytes": cache_size_bytes(),
        "max_cache_bytes": settings.map_tile_max_cache_bytes,
        "max_download_zoom": min(
            settings.map_tile_max_download_zoom, max(l.max_zoom for l in LAYERS.values())
        ),
        "max_pack_tiles": settings.map_tile_max_pack_tiles,
        "online_fallback": settings.map_tile_online_fallback,
        "prefer_online": settings.map_tile_prefer_online,
        "online": is_online(),
        "packs": [p.to_dict() for p in packs],
    }
