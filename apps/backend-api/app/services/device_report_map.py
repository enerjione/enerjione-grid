"""Cihaz Durum Raporunun KONUM figuru — cihaz hattin neresinde oturuyor.

NEDEN KOORDINAT YETMIYOR
------------------------
Rapor sahaya cikan ekiple birlikte gidiyor. "38,104512 / 41,194233" satiri
telefona yazilabilir ama cihazin hattin BASINDA mi ORTASINDA mi oldugunu,
komsu direklerin numaralarini ve hangi kesimi izledigini soylemiyor —
sahada sorulan sorular bunlar. Sol paneldeki mini harita da tam bu yuzden
var; rapor onun kagit karsiligidir.

CIZIM ORTAK: karo mozaigi, zoom secimi, olcek cubugu ve karo saglayici atfi
Ariza Raporu haritasindan gelir (bkz. `fault_report_map` sonundaki ortak
yardimcilar). Ikinci bir kopya, ayni belgeden cikan iki haritanin zamanla
farkli gorunmesi demek olurdu.

FIGUR ZORUNLU DEGIL: karo yoksa (cevrimdisi kurulum, indirilmemis alan) ya
da cihazin konumu bilinmiyorsa None doner ve rapor haritasiz cikar. Raporun
hic uretilmemesi, eksik bir figurden cok daha kotudur.

KIT ISTISNASI: fiziksel Pole Master Kit kaydi hicbir hat kesimine oturmaz —
hatta oturan onun SETLERIDIR. Kit raporunda figur, kite bagli setlerin
konumlarini birlikte gosterir; "kitin nerede oldugu" sorusunun sahadaki
karsiligi zaten budur.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.grid_topology import LineSegment, Pole
from app.services import device_kit_service, map_tile_service
from app.services.fault_report_map import (
    MAX_TILES,
    TILE_PX,
    draw_attribution,
    draw_pill,
    draw_scale_bar,
    load_report_font,
    pick_zoom,
    project_latlon,
    tile_mosaic,
)

LatLon = tuple[float, float]

C_LINE = (255, 255, 255)
C_LINE_CORE = (37, 99, 235)
C_SEGMENT = (233, 120, 0)  # marka turuncusu — cihazin izledigi kesim
C_POLE = (71, 85, 105)
C_SELF = (233, 120, 0)
C_OTHER = (100, 116, 139)
C_WHITE = (255, 255, 255)


@dataclass
class DeviceGeometry:
    """Figurun cizecegi her sey — karo/piksel bilgisi ICERMEZ."""

    #: Hattin tamami (varsa).
    line: list[LatLon] = field(default_factory=list)
    #: Cihazin izledigi kesim — vurgulu cizilir.
    segment: list[LatLon] = field(default_factory=list)
    #: (sira no, konum) — hattaki direkler.
    poles: list[tuple[int, LatLon]] = field(default_factory=list)
    #: (etiket, konum, bu rapor bu cihaza mi ait) — cihazlar.
    devices: list[tuple[str, LatLon, bool]] = field(default_factory=list)
    #: Cerceveye SIGDIRILACAK noktalar.
    focus: list[LatLon] = field(default_factory=list)


def _valid(lat: float | None, lon: float | None) -> bool:
    return (
        lat is not None
        and lon is not None
        and not (lat == 0 and lon == 0)
        and -90 <= lat <= 90
        and -180 <= lon <= 180
    )


def collect_device_geometry(db: Session, device: Device) -> DeviceGeometry | None:
    """Cihazin konum figuru icin geometriyi toplar. Konum yoksa None."""
    geometry = DeviceGeometry()
    segment = db.scalar(select(LineSegment).where(LineSegment.device_id == device.id))

    if segment is not None:
        poles = list(
            db.scalars(
                select(Pole)
                .where(Pole.line_id == segment.line_id)
                .order_by(Pole.sequence_no.asc())
            ).all()
        )
        geometry.line = [(p.latitude, p.longitude) for p in poles if _valid(p.latitude, p.longitude)]
        geometry.poles = [
            (p.sequence_no, (p.latitude, p.longitude))
            for p in poles
            if _valid(p.latitude, p.longitude)
        ]
        start = db.get(Pole, segment.from_pole_id)
        end = db.get(Pole, segment.to_pole_id)
        if start is not None and end is not None:
            kesim = [
                (p.latitude, p.longitude)
                for p in (start, end)
                if _valid(p.latitude, p.longitude)
            ]
            if len(kesim) == 2:
                geometry.segment = kesim
                geometry.focus.extend(kesim)
        # AYNI HATTAKI DIGER CIHAZLAR: cihazin komsulari, sahada "hangi
        # kelepceye gidiyorum" sorusunun yaniti. Gri cizilir — durumlari bu
        # raporun konusu degil ve renklendirmek olculmemis bir sey soylemek
        # olurdu.
        for other in db.scalars(
            select(Device)
            .join(LineSegment, LineSegment.device_id == Device.id)
            .where(LineSegment.line_id == segment.line_id)
        ).all():
            if not _valid(other.latitude, other.longitude):
                continue
            geometry.devices.append(
                (other.code, (other.latitude, other.longitude), other.id == device.id)
            )

    if _valid(device.latitude, device.longitude):
        point = (device.latitude, device.longitude)
        geometry.focus.append(point)
        if not any(is_self for _c, _p, is_self in geometry.devices):
            geometry.devices.append((device.code, point, True))

    # KIT: kendisi hicbir kesime oturmaz, setleri oturur.
    if device_kit_service.is_kit(device):
        for child in device_kit_service.list_subunits(db, device.id):
            if not _valid(child.latitude, child.longitude):
                continue
            point = (child.latitude, child.longitude)
            geometry.devices.append((child.code, point, True))
            geometry.focus.append(point)

    if not geometry.focus:
        return None
    if len(geometry.focus) == 1:
        # Tek nokta: zoom secici bir KUTU ister. Cevresinde ~150 m'lik bir
        # pencere acilir, aksi halde en yakin kademe secilir ve cihaz
        # taninmayacak kadar yakin bir uydu karesinde tek basina kalirdi.
        lat, lon = geometry.focus[0]
        d = 0.0015
        geometry.focus = [(lat - d, lon - d), (lat + d, lon + d)]
    return geometry


def render_device_map(
    geometry: DeviceGeometry,
    *,
    layer: str = "satellite",
    width: int = 1560,
    height: int = 700,
) -> bytes | None:
    """Figuru JPEG olarak dondurur. Uretilemezse None — rapor haritasiz cikar."""
    if len(geometry.focus) < 2:
        return None

    max_zoom = 18
    definition = map_tile_service.LAYERS.get(layer)
    if definition is not None:
        max_zoom = definition.max_zoom
    pad = 90
    zoom = pick_zoom(geometry.focus, width, height, max_zoom, pad)
    while zoom > 6:
        tiles = (int(width / TILE_PX) + 2) * (int(height / TILE_PX) + 2)
        if tiles <= MAX_TILES:
            break
        zoom -= 1

    xs, ys = zip(*(project_latlon(la, lo, zoom) for la, lo in geometry.focus))
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    origin = (cx - width / 2, cy - height / 2)

    try:
        canvas = tile_mosaic(layer, zoom, origin, width, height)
    except Exception:  # noqa: BLE001 - zemin olmasa da sema cizilir
        canvas = Image.new("RGB", (width, height), (233, 237, 242))
    draw = ImageDraw.Draw(canvas)

    def px(point: LatLon) -> tuple[float, float]:
        x, y = project_latlon(point[0], point[1], zoom)
        return x - origin[0], y - origin[1]

    # Hat once BEYAZ kilifla: uydu goruntusu koyu oldugunda (orman, asfalt)
    # ince renkli cizgi zeminde kayboluyor.
    if len(geometry.line) >= 2:
        path = [px(p) for p in geometry.line]
        draw.line(path, fill=C_LINE, width=11, joint="curve")
        draw.line(path, fill=C_LINE_CORE, width=5, joint="curve")
    if len(geometry.segment) >= 2:
        draw.line([px(p) for p in geometry.segment], fill=C_SEGMENT, width=9, joint="curve")

    seq_font = load_report_font(19, bold=True)
    for sequence_no, point in geometry.poles:
        x, y = px(point)
        if not (-40 <= x <= width + 40 and -40 <= y <= height + 40):
            continue
        draw.ellipse([x - 13, y - 13, x + 13, y + 13], fill=C_POLE, outline=C_WHITE, width=3)
        label = str(sequence_no)
        box = draw.textbbox((0, 0), label, font=seq_font)
        draw.text(
            (x - (box[2] - box[0]) / 2 - box[0], y - (box[3] - box[1]) / 2 - box[1]),
            label,
            font=seq_font,
            fill=C_WHITE,
        )

    label_font = load_report_font(21, bold=True)
    # Raporun cihazi EN USTTE cizilsin: komsu bir cihazla ayni piksele
    # dustugunde altta kalirsa figur yanlis cihazi isaret ediyor gorunurdu.
    for code, point, is_self in sorted(geometry.devices, key=lambda d: d[2]):
        x, y = px(point)
        if not (-40 <= x <= width + 40 and -40 <= y <= height + 40):
            continue
        fill = C_SELF if is_self else C_OTHER
        s = 16 if is_self else 11
        draw.rounded_rectangle(
            [x - s, y - s, x + s, y + s], radius=int(s * 0.45), fill=fill, outline=C_WHITE, width=3
        )
        # Simsek: cihazi direkten ayirir.
        k = s / 12.0
        draw.polygon(
            [
                (x + 1.5 * k, y - 8 * k),
                (x - 5 * k, y + 1 * k),
                (x - 0.5 * k, y + 1 * k),
                (x - 2 * k, y + 8 * k),
                (x + 5 * k, y - 1.5 * k),
                (x + 0.5 * k, y - 1.5 * k),
            ],
            fill=C_WHITE,
        )
        # Etiket YALNIZCA raporun cihaz(lar)ina: hepsine yazilirsa yogun bir
        # hatta figur okunmaz hale gelir.
        if is_self:
            tx = min(max(x + s + 14, 8), width - 260)
            ty = min(max(y - 14, 8), height - 46)
            draw_pill(draw, (tx, ty), code, label_font, fill, C_WHITE)

    draw_scale_bar(draw, canvas, zoom, geometry.focus[0][0], width, height)
    draw_attribution(draw, layer, width, height)

    out = io.BytesIO()
    # JPEG, PNG DEGIL: zemin fotografik uydu goruntusu; PNG'de figur tek
    # basina megabaytlara cikiyor ve rapor sahadan e-posta ile
    # gonderilemeyecek kadar buyuyor (Ariza Raporu ile ayni tercih).
    canvas.save(out, format="JPEG", quality=88, subsampling=0, optimize=True)
    return out.getvalue()


def render_device_map_for(db: Session, device: Device) -> bytes | None:
    """Giris kapisi: geometri + cizim. Hata OLURSA None (rapor haritasiz cikar)."""
    try:
        geometry = collect_device_geometry(db, device)
        if geometry is None:
            return None
        return render_device_map(geometry)
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "DeviceGeometry",
    "collect_device_geometry",
    "render_device_map",
    "render_device_map_for",
]
