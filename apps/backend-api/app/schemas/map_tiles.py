"""Cevrimdisi harita karo paketleri I/O semalari."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MapLayerInfo(BaseModel):
    key: str
    label: str
    max_zoom: int
    attribution: str


class MapPack(BaseModel):
    id: str
    name: str
    layer: str
    bbox: list[float]
    zoom_min: int
    zoom_max: int
    tile_total: int
    tile_done: int
    tile_failed: int
    bytes_written: int
    status: Literal["pending", "running", "done", "failed", "cancelled"]
    error: str = ""
    created_at: str = ""
    finished_at: str = ""


class MapTileSummary(BaseModel):
    layers: list[MapLayerInfo]
    cache_bytes: int
    max_cache_bytes: int
    max_download_zoom: int
    max_pack_tiles: int
    online_fallback: bool
    packs: list[MapPack]


class MapAreaBase(BaseModel):
    """Kullanicinin haritada sectigi dikdortgen + zoom araligi."""

    layer: str = Field(min_length=1, max_length=32)
    south: float = Field(ge=-90, le=90)
    west: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)
    zoom_min: int = Field(ge=0, le=20)
    zoom_max: int = Field(ge=0, le=20)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_bounds(self):
        # Sifir alanli/ters dikdortgen: kullanici haritada yanlislikla tek
        # noktaya tiklamis olabilir; karo sayisi 0 cikip sessizce "bitti"
        # gorunmesin diye burada reddediyoruz.
        if self.north <= self.south:
            raise ValueError("Kuzey sinir guney sinirdan buyuk olmali")
        if self.east <= self.west:
            raise ValueError("Dogu sinir bati sinirdan buyuk olmali")
        if self.zoom_max < self.zoom_min:
            raise ValueError("zoom_max, zoom_min'den kucuk olamaz")
        return self

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.south, self.west, self.north, self.east)


class MapAreaEstimate(MapAreaBase):
    """Indirmeden once tahmin istegi."""


class MapEstimateResult(BaseModel):
    tile_count: int
    estimated_bytes: int
    max_tiles: int


class MapPackCreate(MapAreaBase):
    name: str = Field(default="", max_length=120)
