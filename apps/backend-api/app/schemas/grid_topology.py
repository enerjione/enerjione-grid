from datetime import datetime

from pydantic import BaseModel, Field


# ----- Region -----

class RegionBase(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, max_length=20)
    is_active: bool = True


class RegionCreate(RegionBase):
    pass


class RegionUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, max_length=20)
    is_active: bool | None = None


class RegionRead(RegionBase):
    id: int
    created_at: datetime
    line_count: int = 0

    class Config:
        from_attributes = True


# ----- Line -----

class LineBase(BaseModel):
    region_id: int
    code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, max_length=20)
    is_active: bool = True
    # Bransman: bu hat baska bir hattin diregine bagliysa o pole'un id'si.
    branched_from_pole_id: int | None = None


class LineCreate(LineBase):
    pass


class LineUpdate(BaseModel):
    region_id: int | None = None
    code: str | None = Field(default=None, min_length=1, max_length=80)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, max_length=20)
    is_active: bool | None = None
    branched_from_pole_id: int | None = None


class LineRead(LineBase):
    id: int
    created_at: datetime
    pole_count: int = 0
    segment_count: int = 0

    class Config:
        from_attributes = True


# ----- Pole -----

class PoleBase(BaseModel):
    line_id: int
    sequence_no: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=120)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    # Direk tipi: 'pole' | 'transformer' | 'breaker' (varsayilan: pole)
    pole_type: str = Field(default="pole", max_length=20)


class PoleCreate(PoleBase):
    pass


class PoleUpdate(BaseModel):
    sequence_no: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    pole_type: str | None = Field(default=None, max_length=20)


class PoleRead(PoleBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


# ----- LineSegment -----

class LineSegmentBase(BaseModel):
    line_id: int
    from_pole_id: int
    to_pole_id: int
    device_id: int | None = None
    # Cihazin slot icindeki fiziksel konumu (0..1). NULL = otomatik dagilim.
    device_position_t: float | None = None
    # FCI yon oryantasyonu: "green_forward" | "red_forward" | None.
    device_orientation: str | None = None


class LineSegmentCreate(LineSegmentBase):
    pass


class LineSegmentUpdate(BaseModel):
    from_pole_id: int | None = None
    to_pole_id: int | None = None
    device_id: int | None = None
    device_position_t: float | None = None
    device_orientation: str | None = None


class LineSegmentRead(LineSegmentBase):
    id: int
    created_at: datetime
    # UI render kolaylığı için expand edilmiş alanlar:
    from_pole_seq: int | None = None
    to_pole_seq: int | None = None
    device_code: str | None = None
    device_name: str | None = None

    class Config:
        from_attributes = True


# ----- Detay (UI tek-sayfa için) -----

class LineDetail(BaseModel):
    """Mühendislik > Hat Yönetimi sayfasında bir hattın tüm direkleri + segmentleri.
    Tek API çağrısıyla render için."""
    line: LineRead
    poles: list[PoleRead]
    segments: list[LineSegmentRead]


class GridSnapshot(BaseModel):
    """Anasayfa haritasi icin tek istekte tum topoloji ozeti.
    Tum bolgeler, hatlar, direkler ve segmentler ile cihaz baglari beraber."""
    regions: list[RegionRead]
    lines: list[LineRead]
    poles: list[PoleRead]
    segments: list[LineSegmentRead]


class PoleReorderItem(BaseModel):
    """Drag-to-reorder sonrası tek bir direk için yeni sıra."""
    pole_id: int
    sequence_no: int = Field(ge=1)


class PoleReorderRequest(BaseModel):
    """Hat içindeki tüm direklerin yeni sıralarını topluca yollar.

    Frontend listede sürükleyip bıraktıktan sonra son haldeki sıralamayı yollar.
    Backend tüm sequence_no'ları **tek transaction** içinde günceller; arada
    UNIQUE(line_id, sequence_no) constraint çakışmasını önlemek için önce
    geçici negatif değerlere taşıyıp sonra hedef değerlere yazar."""
    line_id: int
    items: list[PoleReorderItem]


# ----- Excel Import (sablon + onizleme + commit) -----

class ImportRowError(BaseModel):
    """Import sirasinda bir Excel satirindaki hata (satir no + mesaj)."""
    row: int
    message: str


class ImportPreviewResponse(BaseModel):
    """Onizleme (dry-run): DB'ye yazmadan neyin olusacaginin ozeti + hatalar."""
    regions: int
    lines: int
    poles: int
    devices: int
    errors: list[ImportRowError]


class ImportCommitResponse(BaseModel):
    """Commit sonucu: gercekten yaratilan/guncellenen sayilar + kalan hatalar."""
    regions_created: int
    regions_updated: int
    lines_created: int
    lines_updated: int
    poles_created: int
    poles_updated: int
    segments_created: int
    skipped: int
    errors: list[ImportRowError]
