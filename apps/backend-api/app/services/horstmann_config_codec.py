"""Horstmann SN2.0 `<seri>_Configuration.csv` okuyucu/yazici.

FORMAT (2026-08-05'te gercek cihaz dosyasiyla cozuldu ve 8/8 dogrulandi)
-----------------------------------------------------------------------
Her satir dort alan:

    GROUP(4 hex) , INDEX(2 hex) , UZUNLUK(2 hex) , DEGER(hex)

  - UZUNLUK ONALTILIK ve BAYT sayisidir. Ondalik saymak sessizce yanlis
    sonuc verir: `2000,0F,11,...` degeri 17 bayttir (0x11), 11 degil.
  - Sayisal degerler LITTLE-ENDIAN: `2010,C6,02,A005` -> 0x05A0 = 1440.
  - Metin degerleri ASCII + NUL dolgulu.
  - UZUNLUK 00 ise DEGER alani hic yoktur (satirda 3 alan bulunur).

Explorer'in urettigi `<seri>.xml` (ObjectCatalog) insan-okunur eslenigidir:
XML'deki `CatIndex` = GROUP + INDEX birlesimi. Ornegin `2010C6` "Dial-In
Interval" 1440 dk, yukaridaki satirla ayni degerdir. Bu esleme sayesinde ham
CSV'yi anlamli adlarla gosterebiliyoruz.

DOSYA SONU: 4 BAYTLIK AYAK
--------------------------
Son CSV satirindan sonra dort bayt gelir:

    <checksum: 2 bayt little-endian> FF FF

Checksum, KENDISINDEN ONCEKI TUM BAYTLARIN 16-bitlik iki'ye tumleyenidir:

    checksum = (-sum(payload)) & 0xFFFF

Yani `sum(payload) + checksum ≡ 0 (mod 0x10000)`. Cihazin kabul ettigi gercek
bir dosyada dogrulandi (seri 50984): saklanan 0x3ED4 == hesaplanan 0x3ED4.
Satir sonlari CRLF'tir ve checksum'a DAHILDIR — LF'e cevirmek toplami
degistirir ve dosyayi gecersiz kilar.

Bu yuzden `render()` checksum'u YENIDEN HESAPLAR. Degeri degistirmek, girdi
eklemek/cikarmak ve sifirdan dosya uretmek guvenlidir.

BOZUK CHECKSUM SESSIZCE GECMEZ
------------------------------
`parse()` okurken dogrular ve sonucu `checksum_valid` alaninda bildirir; ama
PATLAMAZ. Sebep: cihazdan gelen ya da elle uretilmis bozuk bir dosyayi
okuyup KULLANICIYA GOSTEREBILMEK, hic acamamaktan iyidir. Karar cagirana
birakilir — ama karar VERILMEK ZORUNDADIR, cunku alan her zaman doldurulur.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from xml.etree import ElementTree

__all__ = [
    "ConfigEntry",
    "ConfigDocument",
    "CatalogEntry",
    "ConfigParseError",
    "MARKER",
    "calculate_checksum",
    "parse",
    "render",
    "parse_catalog",
    "decode_int",
    "encode_int",
    "decode_text",
]

#: Dosya sonu isaretleyicisi. Checksum'dan SONRA gelir.
MARKER = b"\xff\xff"


def calculate_checksum(payload: bytes) -> int:
    """Cihazin kullandigi 16-bit iki'ye tumleyen saglama toplami.

    `sum(payload) + checksum ≡ 0 (mod 0x10000)`. Gercek cihaz dosyasinda
    dogrulandi (seri 50984 -> 0x3ED4).
    """
    return (-sum(payload)) & 0xFFFF


class ConfigParseError(ValueError):
    """Dosya beklenen bicimde degil."""


# GROUP,INDEX,LEN[,VALUE] — bosluklara toleransli, buyuk/kucuk harf serbest.
_LINE_RE = re.compile(
    r"^\s*([0-9A-Fa-f]{4})\s*,\s*([0-9A-Fa-f]{2})\s*,\s*([0-9A-Fa-f]{2})\s*(?:,\s*([0-9A-Fa-f]*)\s*)?$"
)


@dataclass(frozen=True)
class ConfigEntry:
    """Tek bir yapilandirma girdisi."""

    group: str          # "2010" (buyuk harf, 4 hane)
    index: str          # "C6"
    raw: bytes          # ham deger baytlari (uzunluk = len(raw))
    line_no: int        # kaynaktaki satir numarasi (hata mesajlari icin)

    @property
    def cat_index(self) -> str:
        """XML `CatIndex` karsiligi — katalogla eslesmek icin."""
        return f"{self.group}{self.index}"

    @property
    def length(self) -> int:
        return len(self.raw)

    def as_int(self) -> int:
        return decode_int(self.raw)

    def as_text(self) -> str:
        return decode_text(self.raw)


@dataclass
class ConfigDocument:
    """Ayristirilmis dosya.

    `checksum_valid`: okunan dosyanin ayagi tutuyor muydu? `None` = dosyada
    ayak yoktu (kismi/bozuk dosya). `render()` her zaman DOGRU checksum uretir,
    yani bu alan yalnizca GIRDININ durumunu anlatir.
    """

    entries: list[ConfigEntry] = field(default_factory=list)
    newline: str = "\r\n"
    checksum_valid: bool | None = None
    stored_checksum: int | None = None
    # Ayristirilamayan satirlar. Sessizce ATILMAZ: kaybedilen bir satir,
    # geri yazildiginda cihazin bir ayarini kaybetmesi demektir.
    unparsed: list[tuple[int, str]] = field(default_factory=list)

    def get(self, cat_index: str) -> ConfigEntry | None:
        hedef = cat_index.upper()
        for e in self.entries:
            if e.cat_index == hedef:
                return e
        return None

    def set_raw(self, cat_index: str, raw: bytes) -> None:
        """Bir girdinin degerini degistirir — UZUNLUK KORUNMALIDIR.

        Cihaz her girdiyi sabit genislikte okur; uzunlugu degistirmek dosyanin
        geri kalanini kaydirmaz ama girdinin kendisini anlamsiz kilar.
        """
        hedef = cat_index.upper()
        for i, e in enumerate(self.entries):
            if e.cat_index == hedef:
                if len(raw) != e.length:
                    raise ConfigParseError(
                        f"{hedef}: uzunluk degistirilemez "
                        f"({e.length} bayt bekleniyor, {len(raw)} verildi)"
                    )
                self.entries[i] = replace(e, raw=raw)
                return
        raise KeyError(f"{hedef} dosyada yok")

    def set_int(self, cat_index: str, value: int) -> None:
        mevcut = self.get(cat_index)
        if mevcut is None:
            raise KeyError(f"{cat_index.upper()} dosyada yok")
        self.set_raw(cat_index, encode_int(value, mevcut.length))


@dataclass(frozen=True)
class CatalogEntry:
    """XML ObjectCatalog'dan tek bir anlam kaydi."""

    cat_index: str
    meaning: str
    value: str | None
    unit: str | None


# --- deger donusumleri -----------------------------------------------------
def decode_int(raw: bytes) -> int:
    """Little-endian isaretsiz tamsayi."""
    return int.from_bytes(raw, "little", signed=False)


def encode_int(value: int, length: int) -> bytes:
    """Little-endian, SABIT uzunluk. Sigmayan deger sessizce kirpilmaz."""
    if value < 0:
        raise ConfigParseError(f"negatif deger desteklenmiyor: {value}")
    try:
        return value.to_bytes(length, "little", signed=False)
    except OverflowError as exc:  # noqa: TRY003
        raise ConfigParseError(
            f"{value} degeri {length} bayta sigmiyor (en fazla "
            f"{256 ** length - 1})"
        ) from exc


def decode_text(raw: bytes) -> str:
    """ASCII metin; NUL dolgusu atilir.

    `latin-1` KULLANILIYOR cunku cozulemeyen bayt olmamalidir: metin gibi
    gorunmeyen bir alani metin sanip veri kaybetmektense, ham baytlar
    birebir korunur.
    """
    return raw.split(b"\x00", 1)[0].decode("latin-1")


# --- ayristirma ------------------------------------------------------------
def parse(data: bytes) -> ConfigDocument:
    """Ham dosya baytlarini ayristirir.

    `bytes` alir, `str` DEGIL: dosyanin kuyrugu metin degildir ve herhangi bir
    kod cozme adimi onu bozar.
    """
    if not isinstance(data, (bytes, bytearray)):  # noqa: UP038
        raise ConfigParseError("parse() ham bayt bekler (str degil)")

    data = bytes(data)
    # UTF-8 BOM: Explorer bazen ekliyor. Basta birakilirsa ilk satirin GROUP
    # alani okunamaz hale gelir.
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]

    # --- 4 baytlik ayak: <checksum:2 LE> FF FF -----------------------------
    checksum_valid: bool | None = None
    stored_checksum: int | None = None
    if len(data) >= 4 and data[-2:] == MARKER:
        stored_checksum = int.from_bytes(data[-4:-2], "little")
        govde = data[:-4]
        checksum_valid = calculate_checksum(govde) == stored_checksum
        data = govde
    # Ayak yoksa `checksum_valid` None kalir — cagiran bunu gorup karar verir.

    newline = "\r\n" if b"\r\n" in data else "\n"
    ayirici = newline.encode()

    entries: list[ConfigEntry] = []
    unparsed: list[tuple[int, str]] = []

    for i, ham in enumerate(data.split(ayirici), start=1):
        if not ham.strip():
            continue
        try:
            metin = ham.decode("ascii")
        except UnicodeDecodeError:
            unparsed.append((i, ham.hex()))
            continue

        m = _LINE_RE.match(metin)
        if not m:
            unparsed.append((i, metin))
            continue

        group, index, uzunluk_hex, deger_hex = m.groups()
        uzunluk = int(uzunluk_hex, 16)
        deger_hex = (deger_hex or "").strip()

        if len(deger_hex) % 2:
            raise ConfigParseError(
                f"satir {i}: deger tek sayida hane iceriyor ({deger_hex!r})"
            )
        raw = bytes.fromhex(deger_hex) if deger_hex else b""

        if len(raw) != uzunluk:
            raise ConfigParseError(
                f"satir {i} ({group}{index}): uzunluk alani 0x{uzunluk_hex} "
                f"({uzunluk} bayt) diyor ama deger {len(raw)} bayt"
            )

        entries.append(
            ConfigEntry(
                group=group.upper(), index=index.upper(), raw=raw, line_no=i
            )
        )

    return ConfigDocument(
        entries=entries,
        newline=newline,
        unparsed=unparsed,
        checksum_valid=checksum_valid,
        stored_checksum=stored_checksum,
    )


def render(doc: ConfigDocument) -> bytes:
    """Belgeyi yazar ve checksum'u YENIDEN HESAPLAR.

    Satir sonu `doc.newline`'dan gelir ve checksum'a DAHILDIR: CRLF'i LF'e
    cevirmek toplami degistirip dosyayi gecersiz kilardi. Bu yuzden okunan
    dosyanin satir sonu korunur, "normalize" edilmez.
    """
    ayirici = doc.newline.encode()
    satirlar: list[bytes] = []
    for e in doc.entries:
        alanlar = [e.group, e.index, f"{e.length:02X}"]
        if e.length:
            alanlar.append(e.raw.hex().upper())
        satirlar.append(",".join(alanlar).encode("ascii"))

    govde = ayirici.join(satirlar) + ayirici
    return govde + calculate_checksum(govde).to_bytes(2, "little") + MARKER


# --- XML katalogu ----------------------------------------------------------
def parse_catalog(data: bytes | str) -> dict[str, CatalogEntry]:
    """Explorer'in `<seri>.xml` ObjectCatalog dosyasini okur.

    Donus: CatIndex -> CatalogEntry. CSV'deki ham girdilere ANLAM vermek icin.

    Coklu `ObjectValue` iceren kayitlarda (orn. PLL1/PLL2) YALNIZCA
    `ObjectIndex="0"` alinir: CSV tarafinda her (group,index) cifti tek bir
    satirdir, dolayisiyla eslesme birebirdir.
    """
    kok = ElementTree.fromstring(data if isinstance(data, str) else data.decode("utf-8-sig"))

    katalog: dict[str, CatalogEntry] = {}
    for nesne in kok.iter("CatalogObject"):
        ci = nesne.findtext("CatIndex")
        if not ci:
            continue
        ci = ci.strip().upper()
        for deger in nesne.findall("ObjectValue"):
            if (deger.get("ObjectIndex") or "0") != "0":
                continue
            katalog[ci] = CatalogEntry(
                cat_index=ci,
                meaning=(deger.findtext("Meaning") or "").strip(),
                value=(deger.findtext("ObjVal") or None),
                unit=(deger.findtext("Unit") or None),
            )
            break
    return katalog
