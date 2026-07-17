"""Hat topolojisi Excel sablon uretme + import (onizlemeli).

Kullanici bos bir .xlsx sablonu indirir (Bolge/Hat/Direk/koordinat/cihaz),
doldurur, geri yukler. Sistem once ONIZLEME (parse_and_plan — DB'ye yazmadan
dogrulama) sonra ONAY ile COMMIT (apply_plan — tek transaction) yapar.

Tasarim:
  - Sablon tek sayfa duz tablo: her satir bir DIREK. Ayni Hat_Kodu altinda
    Direk_Sira 1..N ile o hattin direkleri olusur. Koordinatlar Pole'a yazilir.
  - Cihaz hucresi Excel dropdown (data validation): mevcut ATANMAMIS cihaz
    kodlari. Bir cihaz tek segmente baglanir (DB unique) — sablonda atanmislar
    listede yok, ayni cihaz iki satirda secilirse import HATA verir.
  - Cihaz OLUSTURMAZ; sadece mevcut cihazi koda gore segmente baglar.
  - Ekle/birlestir: Bolge/Hat kodu varsa guncelle, yoksa yarat; baska veriye
    dokunma. Direkler hat icin (sira bazli) upsert edilir.

Bu modul HTTP'den bagimsizdir (test edilebilir saf fonksiyonlar).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.grid_topology import Line, LineSegment, Pole, Region

# Sablon kolonlari (sira onemli — parse bunlara gore index'ler).
COLUMNS = [
    "Bolge_Kodu",
    "Bolge_Adi",
    "Hat_Kodu",
    "Hat_Adi",
    "Direk_Sira",
    "Direk_Adi",
    "Enlem",
    "Boylam",
    "Direk_Tipi",
    "Cihaz",
    "Cihaz_Sira",   # iki direk arasi coklu cihazda sira (1,2,3...)
    "Yon",          # FCI yon oryantasyonu: yesil | kirmizi
    "Bransman_Hat_Kodu",
    "Bransman_Direk_Sira",
]

POLE_TYPES = ["pole", "transformer", "breaker"]

# Yon (Excel) -> device_orientation (DB) esleme.
YON_TO_ORIENTATION = {
    "yesil": "green_forward",
    "yeşil": "green_forward",
    "kirmizi": "red_forward",
    "kırmızı": "red_forward",
    "green": "green_forward",
    "red": "red_forward",
}
ORIENTATION_TO_YON = {
    "green_forward": "yesil",
    "red_forward": "kirmizi",
}
YON_CHOICES = ["yesil", "kirmizi"]


# --------------------------------------------------------------------------- #
# Sablon uretme
# --------------------------------------------------------------------------- #
def _assigned_device_slots(db: Session) -> dict[int, tuple[str, str, int]]:
    """Cihaz id -> (bolge_kodu, hat_kodu, from_direk_sira).

    Mevcut sablon round-trip'inde cihaz zaten ayni slota bagli olabilir; bu hata
    degil, idempotent guncellemedir. Baska slot ise yine import hatasi.
    """
    regions = {r.id: r for r in db.scalars(select(Region)).all()}
    lines = {l.id: l for l in db.scalars(select(Line)).all()}
    poles = {p.id: p for p in db.scalars(select(Pole)).all()}
    out: dict[int, tuple[str, str, int]] = {}
    for s in db.scalars(select(LineSegment).where(LineSegment.device_id.is_not(None))).all():
        if s.device_id is None:
            continue
        line = lines.get(s.line_id)
        pole = poles.get(s.from_pole_id)
        region = regions.get(line.region_id) if line else None
        if line is None or pole is None or region is None:
            continue
        out[s.device_id] = (region.code, line.code, pole.sequence_no)
    return out


def _hex(color: str | None, fallback: str) -> str:
    """HEX rengi openpyxl formatina (AARRGGBB / RRGGBB) cevir. '#' at."""
    if not color:
        return fallback
    c = color.lstrip("#").strip()
    if len(c) == 6:
        return c.upper()
    return fallback


def _load_topology_rows(db: Session) -> list[dict]:
    """Sistemdeki tum Region->Line->Pole->Segment(cihaz) hiyerarsisini duz
    satirlara ac. Her satir bir DIREK; o direkten baslayan segmentteki cihaz(lar)
    ayri satirlarda tekrar eder (Cihaz_Sira ile). Bolge/Hat sirali."""
    regions = {r.id: r for r in db.scalars(select(Region).order_by(Region.name)).all()}
    lines = list(db.scalars(select(Line).order_by(Line.region_id, Line.code)).all())
    poles = list(db.scalars(select(Pole).order_by(Pole.line_id, Pole.sequence_no)).all())
    segments = list(db.scalars(select(LineSegment)).all())
    devices = {d.id: d for d in db.scalars(select(Device)).all()}

    poles_by_line: dict[int, list[Pole]] = {}
    for p in poles:
        poles_by_line.setdefault(p.line_id, []).append(p)

    # (line_id, from_seq) -> [ (order, device_code, orientation) ]
    seg_by_slot: dict[tuple[int, int], list[tuple[float, str, str | None]]] = {}
    seq_by_pole = {p.id: p.sequence_no for p in poles}
    for s in segments:
        if s.device_id is None:
            continue
        dev = devices.get(s.device_id)
        if dev is None:
            continue
        from_seq = seq_by_pole.get(s.from_pole_id)
        if from_seq is None:
            continue
        t = s.device_position_t if s.device_position_t is not None else 0.5
        seg_by_slot.setdefault((s.line_id, from_seq), []).append(
            (t, dev.code, s.device_orientation)
        )
    # Her slottaki cihazlari t'ye gore sirala (Cihaz_Sira 1..N).
    for key in seg_by_slot:
        seg_by_slot[key].sort(key=lambda x: x[0])

    rows: list[dict] = []
    lines_by_region: dict[int, list[Line]] = {}
    for ln in lines:
        lines_by_region.setdefault(ln.region_id, []).append(ln)

    for region_id, region in regions.items():
        region_lines = lines_by_region.get(region_id, [])
        for ln in region_lines:
            line_poles = poles_by_line.get(ln.id, [])
            branch_line_code = ""
            branch_pole_seq = ""
            if ln.branched_from_pole_id is not None:
                bp_seq = seq_by_pole.get(ln.branched_from_pole_id)
                # bagli oldugu hattin kodunu bul
                for other in lines:
                    if any(p.id == ln.branched_from_pole_id for p in poles_by_line.get(other.id, [])):
                        branch_line_code = other.code
                        break
                branch_pole_seq = bp_seq if bp_seq is not None else ""
            for p in line_poles:
                slot = seg_by_slot.get((ln.id, p.sequence_no), [])
                if slot:
                    for order, (_, dev_code, orient) in enumerate(slot, start=1):
                        rows.append({
                            "region": region, "line": ln, "pole": p,
                            "device_code": dev_code, "cihaz_sira": order,
                            "yon": ORIENTATION_TO_YON.get(orient or "", ""),
                            "branch_line_code": branch_line_code,
                            "branch_pole_seq": branch_pole_seq,
                        })
                else:
                    rows.append({
                        "region": region, "line": ln, "pole": p,
                        "device_code": "", "cihaz_sira": "", "yon": "",
                        "branch_line_code": branch_line_code,
                        "branch_pole_seq": branch_pole_seq,
                    })
    return rows


def build_template_workbook(db: Session) -> io.BytesIO:
    """Import sablonu (.xlsx). MEVCUT topolojiyi DOLU getirir + agac gorunum
    (Bolge/Hat gruplu, katlanabilir outline). Cihaz/Direk_Tipi/Yon dropdown'lu.

    Bos satirlar altta yeni ekleme icin. Doner: BytesIO."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    devices = list(db.scalars(select(Device).order_by(Device.code)).all())
    all_device_codes = [d.code for d in devices]

    wb = Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
    center = Alignment(horizontal="center", vertical="center")
    left = Alignment(horizontal="left", vertical="center")

    ws = wb.active
    ws.title = "Topoloji"
    ws.append(COLUMNS)
    for col_idx in range(1, len(COLUMNS) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center

    # Agac gorunum: her Bolge bir grup (outline level 1), her Hat alt grup
    # (level 2). Bolge/Hat basligi ilk satirinda dolu; ayni gruptaki alt
    # satirlarda Bolge/Hat kolonlari BOS (girinti hissi) — parse forward-fill
    # yapar. Renkli dolgu ile gruplar ayrilir.
    topo_rows = _load_topology_rows(db)
    excel_row = 2
    last_region_code = None
    last_line_key = None
    region_fill_cache: dict[str, PatternFill] = {}
    line_fill = PatternFill(start_color="EEF2FF", end_color="EEF2FF", fill_type="solid")

    for r in topo_rows:
        region = r["region"]
        line = r["line"]
        pole = r["pole"]
        region_code = region.code
        line_key = f"{region_code}|{line.code}"
        is_new_region = region_code != last_region_code
        is_new_line = line_key != last_line_key

        # Bolge/Hat kolonlari yalniz grup basinda dolu.
        vals = [
            region_code if is_new_region else "",
            region.name if is_new_region else "",
            line.code if is_new_line else "",
            line.name if is_new_line else "",
            pole.sequence_no,
            pole.name or "",
            pole.latitude,
            pole.longitude,
            pole.pole_type or "pole",
            r["device_code"],
            r["cihaz_sira"],
            r["yon"],
            r["branch_line_code"] if is_new_line else "",
            r["branch_pole_seq"] if is_new_line else "",
        ]
        ws.append(vals)

        # Outline: direk satirlari level 1 (Bolge/Hat basligi altinda katlanir).
        ws.row_dimensions[excel_row].outline_level = 1

        # Renklendirme: yeni hat satirinin Bolge/Hat hucrelerine hafif dolgu.
        if is_new_region:
            rc = _hex(region.color, "DBEAFE")
            if rc not in region_fill_cache:
                region_fill_cache[rc] = PatternFill(start_color=rc, end_color=rc, fill_type="solid")
            fill = region_fill_cache[rc]
            for col in (1, 2):
                ws.cell(row=excel_row, column=col).fill = fill
                ws.cell(row=excel_row, column=col).font = Font(bold=True)
        if is_new_line:
            for col in (3, 4):
                ws.cell(row=excel_row, column=col).fill = line_fill
                ws.cell(row=excel_row, column=col).font = Font(bold=True)

        last_region_code = region_code
        last_line_key = line_key
        excel_row += 1

    # Grup gorunumu: ozet ustte, +/- solda.
    ws.sheet_properties.outlinePr.summaryBelow = False

    # Bos ekleme satirlari (yeni hat/direk icin) — dropdown'lar buralara da uygulanir.
    blank_end = excel_row + 200

    widths = [12, 20, 10, 18, 10, 16, 12, 12, 13, 18, 10, 10, 18, 18]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = "A2"

    _add_dropdowns(wb, ws, all_device_codes, last_data_row=blank_end)

    # --- Yardim sheet ---
    _add_help_sheet(wb, header_font, header_fill)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _add_dropdowns(wb, ws, all_device_codes, last_data_row: int) -> None:
    """Direk_Tipi (I), Cihaz (J), Yon (L) kolonlarina acilir liste ekle."""
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.styles import Font, PatternFill

    end = max(last_data_row, 2)

    # Direk_Tipi = I
    type_dv = DataValidation(
        type="list", formula1='"' + ",".join(POLE_TYPES) + '"',
        allow_blank=True, showDropDown=False,
    )
    ws.add_data_validation(type_dv)
    type_dv.add(f"I2:I{end}")

    # Yon = L
    yon_dv = DataValidation(
        type="list", formula1='"' + ",".join(YON_CHOICES) + '"',
        allow_blank=True, showDropDown=False,
    )
    ws.add_data_validation(yon_dv)
    yon_dv.add(f"L2:L{end}")

    # Cihaz = J — gizli "Cihazlar" sheet'inden. Tum cihazlar listelenir
    # (dolu satirlar kendi cihazini gostersin diye); atanmislar da dahil.
    ws_dev = wb.create_sheet("Cihazlar")
    ws_dev.append(["Cihaz_Kodu"])
    ws_dev.cell(row=1, column=1).font = Font(bold=True, color="FFFFFF")
    ws_dev.cell(row=1, column=1).fill = PatternFill(
        start_color="1F2937", end_color="1F2937", fill_type="solid"
    )
    for code in all_device_codes:
        ws_dev.append([code])
    ws_dev.column_dimensions["A"].width = 24
    ws_dev.sheet_state = "hidden"
    if all_device_codes:
        last = len(all_device_codes) + 1
        dev_dv = DataValidation(
            type="list", formula1=f"Cihazlar!$A$2:$A${last}",
            allow_blank=True, showDropDown=False,
        )
        ws.add_data_validation(dev_dv)
        dev_dv.add(f"J2:J{end}")


def _add_help_sheet(wb, header_font, header_fill) -> None:
    ws_help = wb.create_sheet("Yardim")
    help_rows = [
        ["Kolon", "Açıklama"],
        ["Bolge_Kodu / Bolge_Adi", "Bölge kodu ve adı. Grup başında bir kez yazılır; alt direk satırlarında boş bırakılabilir (üstteki geçerli sayılır)."],
        ["Hat_Kodu / Hat_Adi", "Hat kodu ve adı. Aynı bölge içinde kod benzersiz. Grup başında bir kez yazılır."],
        ["Direk_Sira", "Hat üzerinde 1'den başlayan sıra. Boşluksuz artmalı."],
        ["Direk_Adi", "Opsiyonel direk adı."],
        ["Enlem / Boylam", "Ondalık derece. Enlem -90..90, Boylam -180..180."],
        ["Direk_Tipi", "pole / transformer / breaker. Boş = pole."],
        ["Cihaz", "Bu direğin başlattığı segmente bağlanacak cihaz (açılır liste). Boş bırakılabilir."],
        ["Cihaz_Sira", "İki direk arasında birden fazla cihaz varsa sıra (1, 2, 3...). Tek cihazda boş/1."],
        ["Yon", "Cihazın FCI yön oryantasyonu: yesil (A ucu ileri) / kirmizi (B ucu ileri). Opsiyonel."],
        ["Bransman_Hat_Kodu / _Direk_Sira", "Bu hat başka hattın direğinden dallanıyorsa o hattın kodu + direk sırası (opsiyonel)."],
    ]
    for r in help_rows:
        ws_help.append(r)
    ws_help.cell(row=1, column=1).font = header_font
    ws_help.cell(row=1, column=2).font = header_font
    ws_help.cell(row=1, column=1).fill = header_fill
    ws_help.cell(row=1, column=2).fill = header_fill
    ws_help.column_dimensions["A"].width = 28
    ws_help.column_dimensions["B"].width = 90


# --------------------------------------------------------------------------- #
# Parse + plan (onizleme icin — DB'ye yazmaz)
# --------------------------------------------------------------------------- #
@dataclass
class RowError:
    row: int          # 1-indexli Excel satir no (baslik = 1, veri 2'den)
    message: str


@dataclass
class ParsedDevice:
    """Bir slota (direk-sonrasi segment) baglanacak cihaz + sira + yon."""
    device_code: str
    cihaz_sira: int          # slot icindeki 1-tabanli sira
    orientation: str | None  # device_orientation (green_forward/red_forward/None)
    excel_row: int


@dataclass
class ParsedPole:
    region_code: str
    line_code: str
    sequence_no: int
    name: str | None
    latitude: float
    longitude: float
    pole_type: str
    excel_row: int
    # Bu direkten baslayan segmentteki cihaz(lar). Coklu olabilir (slot ici sira).
    devices: list[ParsedDevice] = field(default_factory=list)


@dataclass
class ParsedLine:
    region_code: str
    code: str
    name: str
    branch_line_code: str | None = None
    branch_pole_seq: int | None = None


@dataclass
class ImportPlan:
    regions: dict[str, str] = field(default_factory=dict)         # code -> name
    lines: dict[str, ParsedLine] = field(default_factory=dict)    # "region|line" -> ParsedLine
    poles: list[ParsedPole] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        device_count = sum(len(p.devices) for p in self.poles)
        return {
            "regions": len(self.regions),
            "lines": len(self.lines),
            "poles": len(self.poles),
            "devices": device_count,
            "errors": len(self.errors),
        }


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        # Excel virgullu ondalik gelebilir.
        if isinstance(v, str):
            v = v.strip().replace(",", ".")
        return float(v)
    except (ValueError, TypeError):
        return None


def _to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        if isinstance(v, float):
            return int(v)
        return int(str(v).strip())
    except (ValueError, TypeError):
        return None


def _clean(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def parse_and_plan(file_bytes: bytes, db: Session) -> ImportPlan:
    """Excel'i oku, dogrula, DB'ye YAZMADAN plan cikar. Satir-bazli hatalar
    plan.errors'da toplanir; hatasiz satirlar yine plana girer (commit onlari
    uygular, hataliyi atlar)."""
    from openpyxl import load_workbook

    plan = ImportPlan()

    try:
        wb = load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    except Exception:  # noqa: BLE001
        plan.errors.append(RowError(0, "Dosya okunamadı. Geçerli bir .xlsx dosyası mı?"))
        return plan

    if "Topoloji" in wb.sheetnames:
        ws = wb["Topoloji"]
    else:
        ws = wb.active

    # Mevcut cihazlar (kod -> id) ve atanmis cihazlar.
    device_by_code = {d.code: d for d in db.scalars(select(Device)).all()}
    assigned_slots = _assigned_device_slots(db)
    used_device_codes: set[str] = set()   # bu import icinde kullanilanlar (duplicate yakala)
    # (region_code, line_code, seq) -> ParsedPole (ayni direk coklu cihazda tekrar satirda gelir)
    pole_by_key: dict[tuple[str, str, int], ParsedPole] = {}
    # (region_code, line_code, seq) -> kullanilan cihaz_sira set (slot ici sira cakismasi yakala)
    slot_orders: dict[tuple[str, str, int], set[int]] = {}

    # Forward-fill: Bolge/Hat kolonlari agac gorunumunde grup basinda dolu,
    # alt satirlarda bos. Bos gelirse ustteki gecerli sayilir.
    ff_region_code = ""
    ff_region_name = ""
    ff_hat_code = ""
    ff_hat_name = ""
    ff_branch_line = ""
    ff_branch_seq: int | None = None

    rows_iter = ws.iter_rows(min_row=2, values_only=True)
    for idx, raw in enumerate(rows_iter, start=2):
        # Bos satiri atla.
        if raw is None or all((c is None or _clean(c) == "") for c in raw):
            continue
        cells = list(raw) + [None] * (len(COLUMNS) - len(raw))
        rec = dict(zip(COLUMNS, cells))

        # Forward-fill bolge/hat.
        rc = _clean(rec["Bolge_Kodu"])
        if rc:
            ff_region_code = rc
            ff_region_name = _clean(rec["Bolge_Adi"]) or rc
        hc = _clean(rec["Hat_Kodu"])
        if hc:
            ff_hat_code = hc
            ff_hat_name = _clean(rec["Hat_Adi"]) or hc
            # Bransman bilgisi hat basinda tasinir.
            ff_branch_line = _clean(rec["Bransman_Hat_Kodu"])
            ff_branch_seq = _to_int(rec["Bransman_Direk_Sira"])

        region_code = ff_region_code
        hat_code = ff_hat_code
        seq = _to_int(rec["Direk_Sira"])
        lat = _to_float(rec["Enlem"])
        lon = _to_float(rec["Boylam"])

        if not region_code:
            plan.errors.append(RowError(idx, "Bolge_Kodu boş (üstte de yok)."))
            continue
        if not hat_code:
            plan.errors.append(RowError(idx, "Hat_Kodu boş (üstte de yok)."))
            continue
        if seq is None or seq < 1:
            plan.errors.append(RowError(idx, "Direk_Sira geçersiz (1'den başlamalı)."))
            continue

        pole_key = (region_code, hat_code, seq)
        existing_pole = pole_by_key.get(pole_key)

        # Direk ilk kez goruluyor -> koordinat + tip dogrula, olustur.
        if existing_pole is None:
            if lat is None or not (-90 <= lat <= 90):
                plan.errors.append(RowError(idx, f"Enlem geçersiz: {rec['Enlem']!r} (-90..90)."))
                continue
            if lon is None or not (-180 <= lon <= 180):
                plan.errors.append(RowError(idx, f"Boylam geçersiz: {rec['Boylam']!r} (-180..180)."))
                continue
            pole_type = _clean(rec["Direk_Tipi"]).lower() or "pole"
            if pole_type not in POLE_TYPES:
                plan.errors.append(RowError(idx, f"Direk_Tipi geçersiz: {pole_type!r}."))
                continue
            # Bolge + hat plana ekle (upsert — kod bazli).
            if region_code not in plan.regions:
                plan.regions[region_code] = ff_region_name
            line_key = f"{region_code}|{hat_code}"
            if line_key not in plan.lines:
                plan.lines[line_key] = ParsedLine(
                    region_code=region_code, code=hat_code, name=ff_hat_name or hat_code,
                    branch_line_code=ff_branch_line or None, branch_pole_seq=ff_branch_seq,
                )
            existing_pole = ParsedPole(
                region_code=region_code, line_code=hat_code, sequence_no=seq,
                name=_clean(rec["Direk_Adi"]) or None,
                latitude=lat, longitude=lon, pole_type=pole_type, excel_row=idx,
            )
            pole_by_key[pole_key] = existing_pole
            plan.poles.append(existing_pole)
            slot_orders[pole_key] = set()

        # Cihaz (opsiyonel) — slota ekle.
        device_code_raw = _clean(rec["Cihaz"])
        if not device_code_raw:
            continue
        dev = device_by_code.get(device_code_raw)
        if dev is None:
            plan.errors.append(RowError(idx, f"Cihaz bulunamadı: {device_code_raw!r}."))
            continue
        if device_code_raw in used_device_codes:
            plan.errors.append(RowError(idx, f"Cihaz {device_code_raw!r} birden fazla kez seçilmiş."))
            continue
        assigned_slot = assigned_slots.get(dev.id)
        if assigned_slot is not None and assigned_slot != (region_code, hat_code, seq):
            plan.errors.append(RowError(idx, f"Cihaz {device_code_raw!r} zaten başka segmente bağlı."))
            continue

        # Cihaz_Sira: verilmezse mevcut slot doluluguna gore otomatik.
        cihaz_sira = _to_int(rec["Cihaz_Sira"])
        if cihaz_sira is None or cihaz_sira < 1:
            cihaz_sira = len(slot_orders[pole_key]) + 1
        if cihaz_sira in slot_orders[pole_key]:
            plan.errors.append(
                RowError(idx, f"{seq}. direk slotunda Cihaz_Sira {cihaz_sira} tekrar ediyor.")
            )
            continue
        slot_orders[pole_key].add(cihaz_sira)

        # Yon.
        yon_raw = _clean(rec["Yon"]).lower()
        orientation = YON_TO_ORIENTATION.get(yon_raw) if yon_raw else None
        if yon_raw and orientation is None:
            plan.errors.append(RowError(idx, f"Yon geçersiz: {yon_raw!r} (yesil/kirmizi)."))
            continue

        used_device_codes.add(device_code_raw)
        existing_pole.devices.append(
            ParsedDevice(
                device_code=device_code_raw, cihaz_sira=cihaz_sira,
                orientation=orientation, excel_row=idx,
            )
        )

    wb.close()
    return plan


# --------------------------------------------------------------------------- #
# Apply (commit — tek transaction)
# --------------------------------------------------------------------------- #
@dataclass
class ImportResult:
    regions_created: int = 0
    regions_updated: int = 0
    lines_created: int = 0
    lines_updated: int = 0
    poles_created: int = 0
    poles_updated: int = 0
    segments_created: int = 0
    skipped: int = 0
    errors: list[RowError] = field(default_factory=list)


def apply_plan(plan: ImportPlan, db: Session) -> ImportResult:
    """Plani tek transaction'da uygula. Sira: Region -> Line -> Pole ->
    Segment (+ bransman 2. gecis). Caller commit eder (record_event sonrasi).

    Coklu cihaz slotlarinda device_position_t Excel Cihaz_Sira'ya gore yazilir;
    tek cihaz 0.5 alir. Ekstra konum senkronuna gerek yok."""
    result = ImportResult(errors=list(plan.errors))

    # 1) Region upsert (kod bazli).
    region_by_code: dict[str, Region] = {}
    for code, name in plan.regions.items():
        existing = db.scalar(select(Region).where(Region.code == code))
        if existing is None:
            reg = Region(code=code, name=name)
            db.add(reg)
            db.flush()
            region_by_code[code] = reg
            result.regions_created += 1
        else:
            # Ad guncelle (yalnizca dolu ise ve degistiyse).
            if name and existing.name != name:
                existing.name = name
                result.regions_updated += 1
            region_by_code[code] = existing

    # 2) Line upsert (region+code bazli). Bransman 2. gecise birakilir.
    line_by_key: dict[str, Line] = {}   # "region|code" -> Line
    for key, pl in plan.lines.items():
        region = region_by_code.get(pl.region_code)
        if region is None:
            result.skipped += 1
            continue
        existing = db.scalar(
            select(Line).where(Line.region_id == region.id, Line.code == pl.code)
        )
        if existing is None:
            line = Line(region_id=region.id, code=pl.code, name=pl.name)
            db.add(line)
            db.flush()
            line_by_key[key] = line
            result.lines_created += 1
        else:
            if pl.name and existing.name != pl.name:
                existing.name = pl.name
                result.lines_updated += 1
            line_by_key[key] = existing

    # region|line_code -> Line (bransman cozumu icin de gerekli)
    line_by_region_code: dict[tuple[str, str], Line] = {}
    for key, line in line_by_key.items():
        rc, lc = key.split("|", 1)
        line_by_region_code[(rc, lc)] = line

    # 3) Pole upsert (line + sequence_no bazli). Pole id'lerini
    #    (region_code,line_code,seq) map'inde tut (ayni line code baska bolgede olabilir).
    pole_map: dict[tuple[str, str, int], Pole] = {}
    for pp in plan.poles:
        rc = pp.region_code
        line = line_by_region_code.get((rc, pp.line_code))
        if line is None:
            result.skipped += 1
            continue
        existing = db.scalar(
            select(Pole).where(Pole.line_id == line.id, Pole.sequence_no == pp.sequence_no)
        )
        if existing is None:
            pole = Pole(
                line_id=line.id,
                sequence_no=pp.sequence_no,
                name=pp.name,
                latitude=pp.latitude,
                longitude=pp.longitude,
                pole_type=pp.pole_type,
            )
            db.add(pole)
            db.flush()
            result.poles_created += 1
        else:
            existing.name = pp.name
            existing.latitude = pp.latitude
            existing.longitude = pp.longitude
            existing.pole_type = pp.pole_type
            pole = existing
            result.poles_updated += 1
        pole_map[(pp.region_code, pp.line_code, pp.sequence_no)] = pole

    # 4) Segment + cihaz. Cihaz, direk (seq) -> (seq+1) segmentine baglanir.
    #    Ayni slotta coklu cihaz: cihaz_sira'ya gore device_position_t yazilir
    #    (t = sira/(toplam+1) — esit dagilim, mevcut _slot_position formuluyle uyumlu).
    device_by_code = {d.code: d for d in db.scalars(select(Device)).all()}
    for pp in plan.poles:
        if not pp.devices:
            continue
        from_pole = pole_map.get((pp.region_code, pp.line_code, pp.sequence_no))
        to_pole = pole_map.get((pp.region_code, pp.line_code, pp.sequence_no + 1))
        if from_pole is None or to_pole is None:
            for pd in pp.devices:
                result.errors.append(
                    RowError(pd.excel_row, f"Cihaz için sonraki direk yok (segment kurulamadı): {pd.device_code}")
                )
            continue
        slot_total = len(pp.devices)
        # Sira'ya gore sirala (kucukten buyuge), t degerini ata.
        ordered = sorted(pp.devices, key=lambda d: d.cihaz_sira)
        for i, pd in enumerate(ordered, start=1):
            dev = device_by_code.get(pd.device_code)
            if dev is None:
                result.skipped += 1
                continue
            t = i / (slot_total + 1)  # esit dagilim; 1 cihaz -> 0.5
            already = db.scalar(select(LineSegment).where(LineSegment.device_id == dev.id))
            if already is not None:
                if already.from_pole_id == from_pole.id and already.to_pole_id == to_pole.id:
                    already.device_position_t = t
                    already.device_orientation = pd.orientation
                    continue
                result.errors.append(
                    RowError(pd.excel_row, f"Cihaz {pd.device_code} zaten bağlı — atlandı.")
                )
                continue
            seg = LineSegment(
                line_id=from_pole.line_id,
                from_pole_id=from_pole.id,
                to_pole_id=to_pole.id,
                device_id=dev.id,
                device_position_t=t,
                device_orientation=pd.orientation,
            )
            db.add(seg)
            db.flush()
            result.segments_created += 1

    # 5) Bransman (2. gecis) — tum hatlar olustuktan sonra.
    for key, pl in plan.lines.items():
        if not pl.branch_line_code or pl.branch_pole_seq is None:
            continue
        line = line_by_key.get(key)
        if line is None:
            continue
        # Bransmanin bagli olacagi hat: ayni bolgede aranir.
        target_pole = pole_map.get((pl.region_code, pl.branch_line_code, pl.branch_pole_seq))
        if target_pole is not None:
            line.branched_from_pole_id = target_pole.id

    return result
