"""Ariza raporunun HARITA FIGURU — sunucuda uydu goruntusu uzerine cizim.

NEDEN SUNUCUDA URETILIYOR
-------------------------
Rapor eskiden tarayicinin "yazdir" ciktisiydi ve haritayi ekrandaki canli
Leaflet katmanindan aliyordu. Uc somut bedeli vardi:

  1. KAPALI ARIZADA HARITA YANLISTI. Ekrandaki kirmizi bolge CANLI alarm
     durumundan turetilir (`alarmActiveDeviceIds`); alarm resetlenince
     kaybolur ve iki hafta sonra alinan rapor hattin tamamini yesil
     gosterirdi. Burada bolge KAYITTAN gelir: `last_red_device_id` /
     `first_green_device_id` arizanin acildigi andaki gercektir, degismez.
  2. Cozunurluk ekran cozunurluguydu — kagitta bulanik cikiyordu.
  3. Isaretcilerin uzerine Leaflet'in kendi kontrolleri, ipuclari ve
     yakinlastirma dugmeleri biniyordu.

KAROLAR `map_tile_service` uzerinden gelir: once disk onbellegi, sonra
internet. Boylece saha cihazi cevrimdisiyken de (alan indirilmisse) rapor
haritali cikar; hic karo yoksa figur cizgi semasi olarak uretilir (harita
zemini olmadan da direk/cihaz dizilimi okunur).

`fault_map_render.py` ILE KARISTIRILMAMALI
------------------------------------------
O modul E-POSTA EKI icin kucuk bir PNG uretir (staticmap ile, dogrudan OSM'den)
ve kirmizi bolgeyi O ANDAKI acik alarmlardan cikarir — bildirim arizanin
acildigi anda gittigi icin orada dogru olan budur. Bu modul ise ARSIVE giden
raporu cizer: aylar sonra alinabilir, o yuzden yalnizca KAYDA bakar ve karo
onbellegini kullanir. Ikisini birlestirmek, iki farkli dogruluk kaynagini tek
fonksiyona sikistirmak olurdu.
"""

from __future__ import annotations

import io
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.device import Device
from app.models.fault import FaultEvent
from app.models.grid_topology import LineSegment, Pole
from app.services import map_tile_service
from app.services.report_layout import report_font_files

LatLon = tuple[float, float]

TILE_PX = 256
#: Tek figur icin cekilecek KARO TAVANI. Ustune cikilirsa bir zoom kademe
#: geri dusulur: rapor uretimi yukari akista yuzlerce istege donusmesin.
MAX_TILES = 90

# Ekrandaki harita ile ayni renk dili (fd-legend).
C_FAULT = (239, 68, 68)
C_OK = (34, 197, 94)
C_NEUTRAL = (71, 85, 105)
C_DEVICE_RED = (220, 38, 38)
C_DEVICE_GREEN = (16, 185, 129)
C_WHITE = (255, 255, 255)
C_INK = (15, 23, 42)


@dataclass
class DevicePoint:
    device_id: int
    point: LatLon
    label: str
    #: "red"  = arizayi goren SON cihaz (kayitta yazili)
    #: "green"= arizayi gormeyen ILK cihaz
    #: "other"= hattaki diger cihaz. Durumu KAYITTA YOK; gri cizilir.
    #:          Yesil/kirmizi boyamak, bilmedigimiz bir seyi raporda
    #:          olculmus gibi gostermek olurdu.
    role: str


@dataclass
class PolePoint:
    sequence_no: int
    point: LatLon
    #: "start" | "end" | "zone" | "other"
    role: str


@dataclass
class FaultGeometry:
    """Figurun cizecegi her sey — karo/piksel bilgisi ICERMEZ."""

    line: list[LatLon]
    pre_ok: list[LatLon]
    fault: list[LatLon]
    post_ok: list[LatLon]
    poles: list[PolePoint]
    devices: list[DevicePoint] = field(default_factory=list)
    #: Cerceveye SIGDIRILACAK noktalar (ariza bolgesi + iki uc cihaz).
    focus: list[LatLon] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Geometri — frontend'deki faultMapView.ts ile AYNI mantik
# ---------------------------------------------------------------------------
def _lerp(a: LatLon, b: LatLon, t: float) -> LatLon:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _split_polyline(path: list[LatLon], at: LatLon) -> tuple[list[LatLon], list[LatLon]]:
    """Polyline'i `at` noktasinda ikiye boler (en yakin kenara DIK izdusum).

    Nokta cizginin tam ustunde degildir — cihaz iki direk arasinda bir `t`
    oraninda durur. Duz "from -> to" varsayimi kivrimli hatta arizayi
    tarlanin ortasinda gosteriyordu.
    """
    if len(path) < 2:
        return path, []
    best_k, best_d, best_proj = 0, float("inf"), path[0]
    for k in range(len(path) - 1):
        ax, ay = path[k]
        bx, by = path[k + 1]
        dx, dy = bx - ax, by - ay
        len2 = dx * dx + dy * dy
        if len2 == 0:
            continue
        t = max(0.0, min(1.0, ((at[0] - ax) * dx + (at[1] - ay) * dy) / len2))
        proj = (ax + t * dx, ay + t * dy)
        d = (proj[0] - at[0]) ** 2 + (proj[1] - at[1]) ** 2
        if d < best_d:
            best_k, best_d, best_proj = k, d, proj
    return [*path[: best_k + 1], best_proj], [best_proj, *path[best_k + 1 :]]


def _meaningful(part: list[LatLon]) -> list[LatLon]:
    """Sifir uzunluklu parcayi eler — hattin ucunda sahte bir nokta birakirdi."""
    if len(part) < 2:
        return []
    lat0, lon0 = part[0]
    if any(abs(la - lat0) > 1e-9 or abs(lo - lon0) > 1e-9 for la, lo in part):
        return part
    return []


def collect_fault_geometry(db: Session, fault: FaultEvent) -> FaultGeometry | None:
    """Kayittan figur geometrisi. Hat/direk yoksa None (rapor haritasiz cikar)."""
    if fault.line_id is None:
        return None
    poles = list(
        db.scalars(
            select(Pole).where(Pole.line_id == fault.line_id).order_by(Pole.sequence_no)
        ).all()
    )
    if len(poles) < 2:
        return None

    line: list[LatLon] = [(p.latitude, p.longitude) for p in poles]

    # --- Hattaki cihazlar, bastan sona sirali ------------------------------
    segments = list(
        db.scalars(
            select(LineSegment)
            .where(LineSegment.line_id == fault.line_id)
            .where(LineSegment.device_id.is_not(None))
        ).all()
    )
    slots: dict[tuple[int, int], list[tuple[int, float]]] = {}
    for seg in segments:
        t = seg.device_position_t if seg.device_position_t is not None else 0.5
        slots.setdefault((seg.from_pole_id, seg.to_pole_id), []).append((seg.device_id, t))
    for key, arr in slots.items():
        arr.sort(key=lambda r: r[1])
        # Hepsi varsayilan 0.5 ise (elle konumlandirilmamis) esit dagit;
        # aksi halde ayni direk araligindaki cihazlar tek noktada ust uste biner.
        if len(arr) > 1 and all(abs(t - 0.5) < 1e-9 for _, t in arr):
            n = len(arr)
            slots[key] = [(dev, (i + 1) / (n + 1)) for i, (dev, _) in enumerate(arr)]

    device_ids = [dev for arr in slots.values() for dev, _ in arr]
    names: dict[int, str] = {}
    if device_ids:
        for d in db.scalars(select(Device).where(Device.id.in_(device_ids))).all():
            names[d.id] = d.name or d.code or f"#{d.id}"

    ordered: list[tuple[int, LatLon]] = []
    for i in range(len(poles) - 1):
        a, b = poles[i], poles[i + 1]
        for dev_id, t in slots.get((a.id, b.id), []):
            ordered.append((dev_id, _lerp(line[i], line[i + 1], t)))

    points_by_device = {dev_id: pt for dev_id, pt in ordered}

    # --- Ariza bolgesi: KAYITTAKI iki cihazin arasi ------------------------
    split_a = points_by_device.get(fault.last_red_device_id) if fault.last_red_device_id else None
    split_b = (
        points_by_device.get(fault.first_green_device_id) if fault.first_green_device_id else None
    )
    # Son "gordum" cihazindan sonra "gormedim" cihazi yoksa ariza hat ucuna
    # kadar suruyor olabilir; daraltacak olcum yok, son direk sinirdir.
    if split_a is not None and split_b is None:
        split_b = line[-1]

    pre_ok, fault_part, post_ok = line, [], []
    if split_a is not None and split_b is not None:
        pre_ok, rest = _split_polyline(line, split_a)
        fault_part, post_ok = _split_polyline(rest, split_b)
    else:
        # Cihaz kaydi yok (elle acilmis / topoloji degismis): bolgeyi direk
        # sira numaralarindan cikar.
        lo, hi = _zone_seq_range(fault)
        if lo is not None and hi is not None:
            zone = [
                (p.latitude, p.longitude) for p in poles if lo <= p.sequence_no <= hi
            ]
            if len(zone) >= 2:
                pre_ok = [(p.latitude, p.longitude) for p in poles if p.sequence_no <= lo]
                fault_part = zone
                post_ok = [(p.latitude, p.longitude) for p in poles if p.sequence_no >= hi]

    # --- Direk rolleri -----------------------------------------------------
    lo, hi = _zone_seq_range(fault)
    pole_points: list[PolePoint] = []
    for p in poles:
        if p.id == fault.from_pole_id:
            role = "start"
        elif p.id == fault.to_pole_id:
            role = "end"
        elif lo is not None and hi is not None and lo < p.sequence_no < hi:
            role = "zone"
        else:
            role = "other"
        pole_points.append(PolePoint(p.sequence_no, (p.latitude, p.longitude), role))

    devices: list[DevicePoint] = []
    for dev_id, pt in ordered:
        if dev_id == fault.last_red_device_id:
            role = "red"
        elif dev_id == fault.first_green_device_id:
            role = "green"
        else:
            role = "other"
        devices.append(DevicePoint(dev_id, pt, names.get(dev_id, f"#{dev_id}"), role))

    # Cerceve: ariza bolgesi + iki uc cihaz + bolgedeki direkler.
    focus: list[LatLon] = list(_meaningful(fault_part))
    focus += [d.point for d in devices if d.role in ("red", "green")]
    focus += [pp.point for pp in pole_points if pp.role in ("start", "end", "zone")]
    if len(focus) < 2:
        focus = line

    return FaultGeometry(
        line=line,
        pre_ok=_meaningful(pre_ok),
        fault=_meaningful(fault_part),
        post_ok=_meaningful(post_ok),
        poles=pole_points,
        devices=devices,
        focus=focus,
    )


def _zone_seq_range(fault: FaultEvent) -> tuple[int | None, int | None]:
    a, b = fault.from_pole_seq, fault.to_pole_seq
    if a is None or b is None:
        return None, None
    return (a, b) if a <= b else (b, a)


# ---------------------------------------------------------------------------
# Karo mozaigi + cizim
# ---------------------------------------------------------------------------
def _project(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """WGS84 -> Web Mercator PIKSEL (Leaflet ile ayni sema)."""
    n = TILE_PX * (2**zoom)
    x = (lon + 180.0) / 360.0 * n
    siny = min(max(math.sin(math.radians(lat)), -0.9999), 0.9999)
    y = (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi)) * n
    return x, y


def _pick_zoom(
    focus: list[LatLon], width: int, height: int, max_zoom: int, pad: int
) -> int:
    """Odak kutusunu cerceveye sigdiran EN YAKIN zoom."""
    avail_w, avail_h = max(1, width - 2 * pad), max(1, height - 2 * pad)
    for zoom in range(max_zoom, 4, -1):
        xs, ys = zip(*(_project(la, lo, zoom) for la, lo in focus))
        if (max(xs) - min(xs)) <= avail_w and (max(ys) - min(ys)) <= avail_h:
            return zoom
    return 5


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    paths = report_font_files()
    if paths is not None:
        try:
            return ImageFont.truetype(paths[1] if bold else paths[0], size)
        except OSError:
            pass
    return ImageFont.load_default()


def _tile_mosaic(
    layer: str, zoom: int, origin: tuple[float, float], width: int, height: int
) -> Image.Image:
    """Karolari tek tuvale yapistirir. Alinamayan karo YERINDE BOS kalir.

    Karolar ES ZAMANLI cekilir: tek tek beklenirse (internet oncelikli modda
    her karo ayri bir HTTP istegi) rapor uretimi on saniyeyi asiyordu. Es
    zamanlilik `map_tile_concurrency` — alan indirmedeki ayni nazik sinir,
    ayri bir ayar uretmiyoruz.
    """
    canvas = Image.new("RGB", (width, height), (233, 237, 242))
    ox, oy = origin
    x0, x1 = int(math.floor(ox / TILE_PX)), int(math.floor((ox + width) / TILE_PX))
    y0, y1 = int(math.floor(oy / TILE_PX)), int(math.floor((oy + height) / TILE_PX))
    span = 2**zoom
    coords = [
        (tx, ty)
        for ty in range(y0, y1 + 1)
        if 0 <= ty < span
        for tx in range(x0, x1 + 1)
    ]

    def load(coord: tuple[int, int]) -> tuple[tuple[int, int], Image.Image | None]:
        tx, ty = coord
        try:
            # ONCE DISK: `get_tile` varsayilan olarak internet oncelikli
            # calisir (`map_tile_prefer_online`) ve her karo icin HTTP istegi
            # atar. Rapor icin bu gereksiz — uydu goruntusu yillik guncellenir,
            # onbellekteki kopya ayni is goruyor. Operator arizayi ekranda
            # actiysa karolar zaten diskte: rapor aninda cikar.
            data = map_tile_service.read_tile(layer, zoom, tx % span, ty)
            if data is None:
                data, _src = map_tile_service.get_tile(layer, zoom, tx % span, ty)
            return coord, Image.open(io.BytesIO(data)).convert("RGB")
        except Exception:  # noqa: BLE001 - karo yoksa zemin bos kalir
            return coord, None

    workers = max(1, min(len(coords) or 1, settings.map_tile_concurrency))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for (tx, ty), tile in pool.map(load, coords):
            if tile is None:
                continue
            canvas.paste(tile, (int(round(tx * TILE_PX - ox)), int(round(ty * TILE_PX - oy))))
    return canvas


def _dashed(
    draw: ImageDraw.ImageDraw,
    pts: list[tuple[float, float]],
    color: tuple[int, int, int],
    width: int,
    dash: float,
    gap: float,
) -> None:
    """Kesik cizgi — ariza parcasi saglam parcadan bir bakista ayrilsin."""
    carry, drawing = 0.0, True
    for i in range(len(pts) - 1):
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        seg = math.hypot(x2 - x1, y2 - y1)
        pos = 0.0
        while pos < seg:
            step = (dash if drawing else gap) - carry
            end = min(seg, pos + step)
            if drawing:
                t0, t1 = pos / seg, end / seg
                draw.line(
                    [
                        (x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0),
                        (x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1),
                    ],
                    fill=color,
                    width=width,
                    joint="curve",
                )
            if end - pos + carry >= (dash if drawing else gap) - 1e-9:
                drawing, carry = not drawing, 0.0
            else:
                carry += end - pos
            pos = end


def _pill(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    bg: tuple[int, int, int],
    fg: tuple[int, int, int],
    pad: int = 6,
) -> None:
    x, y = xy
    box = draw.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    draw.rounded_rectangle(
        [x - pad, y - pad, x + w + pad, y + h + pad * 1.4],
        radius=int(pad + 2),
        fill=bg,
        outline=C_WHITE,
        width=2,
    )
    draw.text((x - box[0], y - box[1]), text, font=font, fill=fg)


def render_fault_map(
    geometry: FaultGeometry,
    *,
    layer: str = "satellite",
    width: int = 1560,
    height: int = 820,
) -> bytes | None:
    """Figuru PNG olarak dondurur. Uretilemezse None — rapor haritasiz cikar.

    Olculer PIKSEL: figur A4'te ~186 mm genisliginde basilir, yani ~210 dpi.
    Ekran goruntusunun (~96 dpi) iki katindan fazla; kagitta direk numaralari
    okunur kalir.
    """
    if len(geometry.focus) < 2:
        return None

    max_zoom = 18
    definition = map_tile_service.LAYERS.get(layer)
    if definition is not None:
        max_zoom = definition.max_zoom
    pad = 90
    zoom = _pick_zoom(geometry.focus, width, height, max_zoom, pad)

    # Karo tavani: gerekirse bir kademe geri dus.
    while zoom > 6:
        xs, ys = zip(*(_project(la, lo, zoom) for la, lo in geometry.focus))
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        ox, oy = cx - width / 2, cy - height / 2
        tiles = (int(width / TILE_PX) + 2) * (int(height / TILE_PX) + 2)
        if tiles <= MAX_TILES:
            break
        zoom -= 1
    xs, ys = zip(*(_project(la, lo, zoom) for la, lo in geometry.focus))
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    origin = (cx - width / 2, cy - height / 2)

    try:
        canvas = _tile_mosaic(layer, zoom, origin, width, height)
    except Exception:  # noqa: BLE001 - zemin olmasa da sema cizilir
        canvas = Image.new("RGB", (width, height), (233, 237, 242))

    draw = ImageDraw.Draw(canvas)

    def px(point: LatLon) -> tuple[float, float]:
        x, y = _project(point[0], point[1], zoom)
        return x - origin[0], y - origin[1]

    def path(points: list[LatLon]) -> list[tuple[float, float]]:
        return [px(p) for p in points]

    # Hattin tamami once BEYAZ kilifla cizilir: uydu goruntusu koyu oldugunda
    # (orman, asfalt) ince renkli cizgi zeminde kayboluyor.
    if len(geometry.line) >= 2:
        draw.line(path(geometry.line), fill=C_WHITE, width=11, joint="curve")
    for part in (geometry.pre_ok, geometry.post_ok):
        if len(part) >= 2:
            draw.line(path(part), fill=C_OK, width=6, joint="curve")
    if len(geometry.fault) >= 2:
        _dashed(draw, path(geometry.fault), C_FAULT, 8, 26, 16)

    # --- Direkler ----------------------------------------------------------
    seq_font = _load_font(19, bold=True)
    for pole in geometry.poles:
        x, y = px(pole.point)
        if not (-40 <= x <= width + 40 and -40 <= y <= height + 40):
            continue
        if pole.role == "start":
            fill = C_FAULT
        elif pole.role == "end":
            fill = C_OK
        elif pole.role == "zone":
            fill = (245, 158, 11)
        else:
            fill = C_NEUTRAL
        r = 16 if pole.role != "other" else 12
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill, outline=C_WHITE, width=3)
        label = str(pole.sequence_no)
        box = draw.textbbox((0, 0), label, font=seq_font)
        draw.text(
            (x - (box[2] - box[0]) / 2 - box[0], y - (box[3] - box[1]) / 2 - box[1]),
            label,
            font=seq_font,
            fill=C_WHITE,
        )

    # --- Cihazlar ----------------------------------------------------------
    label_font = _load_font(21, bold=True)
    for dev in geometry.devices:
        x, y = px(dev.point)
        if not (-40 <= x <= width + 40 and -40 <= y <= height + 40):
            continue
        if dev.role == "red":
            fill = C_DEVICE_RED
        elif dev.role == "green":
            fill = C_DEVICE_GREEN
        else:
            fill = (100, 116, 139)
        s = 15 if dev.role != "other" else 11
        draw.rounded_rectangle(
            [x - s, y - s, x + s, y + s], radius=int(s * 0.45), fill=fill, outline=C_WHITE, width=3
        )
        # Simsek: cihazin "olcum yapan" oldugunu direkten ayirir.
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
        # Etiket YALNIZCA iki anahtar cihaza: hepsine yazilirsa figur
        # okunmaz hale geliyor, oysa sahada aranan bu ikisi.
        if dev.role in ("red", "green"):
            tx = min(max(x + s + 14, 8), width - 260)
            ty = min(max(y - 14, 8), height - 46)
            _pill(draw, (tx, ty), dev.label, label_font, fill, C_WHITE)

    _draw_scale_bar(draw, canvas, zoom, geometry.focus[0][0], width, height)
    _draw_attribution(draw, layer, width, height)

    out = io.BytesIO()
    # JPEG, PNG DEGIL: zemin fotografik uydu goruntusu ve PNG'de figur tek
    # basina ~2,2 MB tutuyordu — rapor 4 MB'a cikiyor, sahadan e-posta ile
    # gonderilmesi zorlasiyordu. Kalite 88 + subsampling kapali: ince renkli
    # cizgiler ve direk numaralari 210 dpi'da bozulmadan ~350 KB'a iniyor.
    canvas.save(out, format="JPEG", quality=88, subsampling=0, optimize=True)
    return out.getvalue()


def render_fault_map_png(db: Session, fault: FaultEvent) -> bytes | None:
    """BILDIRIM EKI icin PNG (e-posta gomulu gorsel + WhatsApp medyasi).

    Rapor figuru ile AYNI cizim; sadece olcu kucuk. Rapor A4'e ~210 dpi
    basilir, bildirim ise telefonda okunur — buyuk PNG'yi WhatsApp gateway'i
    yeniden sikistiriyor ve mail kutusunu sisiriyordu.

    `notification_dispatch_service` bu adi cagirir ve hatayi yutar (harita
    olmasa da bildirim gitmeli); figur iki adima bolundugunde (geometri +
    cizim) bu giris kapisi korundu, aksi halde bildirimler sessizce
    haritasiz gidiyordu.
    """
    geometry = collect_fault_geometry(db, fault)
    if geometry is None:
        return None
    return render_fault_map(geometry, width=1040, height=600)


def _draw_scale_bar(
    draw: ImageDraw.ImageDraw,
    canvas: Image.Image,
    zoom: int,
    lat: float,
    width: int,
    height: int,
) -> None:
    """Olcek cubugu — "tahmini mesafe" rakamini haritada dogrulanabilir kilar."""
    m_per_px = 156543.03392 * math.cos(math.radians(lat)) / (2**zoom)
    if m_per_px <= 0:
        return
    target_px = width * 0.18
    for meters in (50, 100, 200, 500, 1000, 2000, 5000):
        bar_px = meters / m_per_px
        if bar_px >= target_px:
            break
    else:
        meters, bar_px = 5000, 5000 / m_per_px
    font = _load_font(20, bold=True)
    x0, y0 = 26, height - 44
    text = f"{meters} m" if meters < 1000 else f"{meters // 1000} km"
    draw.rounded_rectangle(
        [x0 - 10, y0 - 24, x0 + bar_px + 84, y0 + 16], radius=8, fill=(255, 255, 255, 235)
    )
    draw.line([(x0, y0), (x0 + bar_px, y0)], fill=C_INK, width=4)
    for x in (x0, x0 + bar_px):
        draw.line([(x, y0 - 9), (x, y0 + 5)], fill=C_INK, width=4)
    draw.text((x0 + bar_px + 12, y0 - 14), text, font=font, fill=C_INK)


def _draw_attribution(
    draw: ImageDraw.ImageDraw, layer: str, width: int, height: int
) -> None:
    """Karo saglayici adi — lisans sarti (Esri/OSM atifi zorunlu)."""
    definition = map_tile_service.LAYERS.get(layer)
    if definition is None or not definition.attribution:
        return
    raw = definition.attribution
    # Basit HTML temizligi: kaynak metni <a> etiketleri iceriyor.
    text = ""
    inside = False
    for ch in raw:
        if ch == "<":
            inside = True
        elif ch == ">":
            inside = False
        elif not inside:
            text += ch
    text = text.replace("&copy;", "(c)").strip()
    if not text:
        return
    font = _load_font(16)
    box = draw.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    x, y = width - w - 18, height - h - 16
    draw.rectangle([x - 6, y - 5, x + w + 6, y + h + 6], fill=(255, 255, 255))
    draw.text((x - box[0], y - box[1]), text, font=font, fill=(71, 85, 105))


# ---------------------------------------------------------------------------
# ORTAK CIZIM YARDIMCILARI
#
# Asagidakiler rapor TURUNDEN bagimsizdir: karo mozaigi, zoom secimi, olcek
# cubugu ve karo saglayici atfi her harita figurunde ayni. Cihaz Durum Raporu
# haritasi (`device_report_map`) bunlari kullanir; ikinci bir kopya, ayni
# belgeden cikan iki haritanin zamanla FARKLI gorunmesi demek olurdu — ve
# atif (karo lisansinin sarti) yalnizca birinde guncellenirdi.
#
# Tanimlarin SONUNDA duruyorlar: modul tepesine konsalardi `_draw_scale_bar`
# daha tanimlanmadan okunur ve modul import edilemezdi.
# ---------------------------------------------------------------------------
project_latlon = _project
pick_zoom = _pick_zoom
tile_mosaic = _tile_mosaic
load_report_font = _load_font
draw_pill = _pill
draw_scale_bar = _draw_scale_bar
draw_attribution = _draw_attribution
