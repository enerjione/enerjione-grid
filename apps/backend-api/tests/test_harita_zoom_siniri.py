"""Karo saglayicisinin veri sinirini ASMA.

YASANAN SORUN
-------------
Uydu katmaninda cok yaklasinca karolarin uzerinde "Map data not yet
available" yaziyordu.

Sebep sessiz: Esri World Imagery, veri OLMAYAN bir zoom icin HTTP 404 ya da
hata DONDURMEZ — uzerinde o metnin yazili oldugu GECERLI bir PNG'yi HTTP 200
ile verir. Yani:
  * istemcide `tileerror` tetiklenmez, yedek yola gecis calismaz,
  * backend karoyu gecerli sanip CEVRIMDISI ONBELLEGE YAZAR; sonra internet
    olsa bile o karede kalici olarak "veri yok" gorunur.

Cozum sinirdan hic gecmemek: istemci `maxNativeZoom` ile ustune karo
istemiyor (son gercek karoyu buyutuyor), backend de ikinci savunma olarak
sinir disi istegi reddediyor.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.services import map_tile_service as mts

FRONTEND = Path(__file__).resolve().parents[2] / "frontend-web" / "src"


def test_uydu_katmani_18in_USTUNU_istemez():
    """Esri Turkiye kirsalinda 18'de biter; 19 'Map data not yet available'."""
    assert mts.LAYERS["satellite"].max_zoom == 18


def test_sinir_ustu_zoom_YUKARI_AKISA_CIKMAZ():
    """Istek gonderilmemeli — gonderilirse gecerli ama BOS bir karo doner."""
    with pytest.raises(mts.MapTileError) as ex:
        mts.fetch_tile("satellite", 19, 1, 1)
    assert ex.value.code == "MAP_ZOOM_UNSUPPORTED"


def test_sinir_ustu_zoom_ONBELLEGE_YAZILMAZ():
    """`get_tile` sinir kontrolunu fetch'ten ONCE yapmali; aksi halde kirli
    karo diske yazilir ve internet varken bile servis edilir."""
    with pytest.raises(mts.MapTileError) as ex:
        mts.get_tile("satellite", 20, 1, 1)
    assert ex.value.code == "MAP_ZOOM_UNSUPPORTED"


def test_sinir_ICI_zoom_reddedilmez():
    """Kontrol fazla siki olmamali — 18 gecerli bir seviye."""
    mts._ensure_zoom_supported(mts.LAYERS["satellite"], 18)
    mts._ensure_zoom_supported(mts.LAYERS["osm"], 19)


def test_zoom_hatasi_404e_esler():
    """Leaflet 404'te sessizce bos birakir; 4xx/5xx konsolu doldurur."""
    from app.api.map_tiles import _http_error

    exc = _http_error(mts.MapTileError("MAP_ZOOM_UNSUPPORTED", "x"))
    assert exc.status_code == 404


# ---------------------------------------------------------------- istemci


def _map_tiles_ts() -> str:
    return (FRONTEND / "shared" / "mapTiles.ts").read_text(encoding="utf-8")


def test_istemci_ve_backend_uydu_siniri_AYNI():
    """Ikisi ayrisirsa istemci yine sinir disi karo ister ve sorun geri gelir."""
    kaynak = _map_tiles_ts()
    blok = kaynak.split('key: "satellite"', 1)[1].split("},", 1)[0]
    m = re.search(r"maxNativeZoom:\s*(\d+)", blok)
    assert m, "uydu katmaninda maxNativeZoom tanimli degil"
    assert int(m.group(1)) == mts.LAYERS["satellite"].max_zoom


def test_istemci_maxNativeZoomun_USTUNE_yaklasabiliyor():
    """Kullanici talebi: daha fazla yakinlasabilmek. Karo buyutulerek
    gosterilir — goruntu yumusar ama direkler ayrisir."""
    kaynak = _map_tiles_ts()
    m = re.search(r"const MAX_ZOOM = (\d+)", kaynak)
    assert m, "MAX_ZOOM tanimli degil"
    assert int(m.group(1)) > mts.LAYERS["satellite"].max_zoom
    # Leaflet varsayilani 18'dir; sinir gercekten yukselmis olmali.
    assert int(m.group(1)) >= 20


def test_karo_katmani_maxNativeZoom_GECIRIYOR():
    """Tanim dosyasinda deger olsa da TileLayer'a gecirilmezse ise yaramaz."""
    kaynak = (FRONTEND / "components" / "ResilientTileLayer.tsx").read_text(encoding="utf-8")
    assert "maxNativeZoom={" in kaynak, (
        "ResilientTileLayer maxNativeZoom gecirmiyor — Leaflet yine saglayicida "
        "olmayan seviyeler icin karo ister"
    )


def test_tum_katmanlarda_maxNativeZoom_TANIMLI():
    """Biri unutulursa o katmanda ayni sorun sessizce geri doner."""
    kaynak = _map_tiles_ts()
    anahtarlar = re.findall(r'key: "(\w+)"', kaynak)
    assert set(anahtarlar) == set(mts.LAYERS), "istemci/backend katman listesi ayristi"
    # Tip tanimindaki `maxNativeZoom: number` degil, GERCEK degerler sayilir.
    degerler = re.findall(r"maxNativeZoom:\s*(\d+)", kaynak)
    assert len(degerler) == len(anahtarlar), (
        f"{len(anahtarlar)} katman var ama {len(degerler)} tanesinde maxNativeZoom degeri "
        "tanimli — eksik katman saglayicida olmayan seviyeler icin karo ister"
    )


def test_indirilebilir_zoom_ozeti_SINIRI_asmaz():
    """Cevrimdisi alan indirme ekrani, veri olmayan seviyeyi teklif etmemeli."""
    ozet = mts.summary()
    veri = json.loads(json.dumps(ozet, default=str))
    for katman in veri["layers"]:
        assert katman["max_zoom"] == mts.LAYERS[katman["key"]].max_zoom
