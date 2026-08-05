"""Horstmann Configuration.csv codec — gercek cihaz verisiyle.

Asagidaki satirlar SN20 (seri 50984) cihazindan alinan gercek
`50984_Configuration.csv` dosyasindandir; beklenen degerler ayni cihazin
Explorer XML ciktisindan (`50984.xml`) okunmustur. Yani bu testler bizim
yorumumuzu degil, CIHAZIN KENDI iki ciktisinin birbirini tutmasini sinar.

Dosya sonundaki 4 baytlik ayak COZULDU:

    <checksum: 2 bayt LE> FF FF        checksum = (-sum(payload)) & 0xFFFF

Gercek 1095 baytlik cihaz dosyasinda dogrulandi: saklanan 0x3ED4 == hesaplanan,
60 girdinin 60'i ayristirildi, gidis-donus bayt bayt ayni.

EN KRITIK TESTLER: `test_gidis_donus_BAYT_BAYT_ayni` ve
`test_CRLF_satir_sonu_KORUNUR`. Checksum satir sonlarini DA kapsar; CRLF'i
LF'e "normalize" etmek toplami degistirir ve cihaz dosyayi reddeder.
"""

from __future__ import annotations

import pytest

from app.services.horstmann_config_codec import (
    MARKER,
    ConfigParseError,
    calculate_checksum,
    decode_int,
    decode_text,
    encode_int,
    parse,
    parse_catalog,
    render,
)

# Gercek dosyadan bir kesit. Satir sonlari CRLF — cihazin dosyasi oyle ve
# checksum satir sonlarini DA kapsar; LF'e cevirmek dosyayi gecersiz kilar.
_SATIRLAR = [
    b"3811,01,01,00",
    b"2010,C6,02,A005",
    b"2010,E0,02,3C00",
    b"2107,01,04,84030000",
    b"3050,02,04,90D00300",
    b"3200,01,04,10270000",
    b"3701,01,02,F000",
    b"3706,01,04,B80B0000",
    b"4426,01,02,7017",
    b"2000,0F,11,5B6E6F7420636F6E666967757265645D00",
    b"2010,1A,14,323032362D30382D30352031353A35323A353100",
    b"1202,02,00",
]
GERCEK_SATIRLAR = b"\r\n".join(_SATIRLAR) + b"\r\n"
GERCEK_DOSYA = (
    GERCEK_SATIRLAR
    + calculate_checksum(GERCEK_SATIRLAR).to_bytes(2, "little")
    + MARKER
)


# --- Cihazin iki ciktisi birbirini tutuyor mu ------------------------------
@pytest.mark.parametrize(
    ("cat_index", "beklenen", "ad"),
    [
        ("2010C6", 1440, "Dial-In Interval (min)"),
        ("2010E0", 60, "Session Timeout"),
        ("210701", 900, "Reporting Period Gateway"),
        ("305002", 250000, "Min Trip Current @200ms"),
        ("320001", 10000, "Nominal Voltage"),
        ("370101", 240, "Reset Time (min)"),
        ("370601", 3000, "Current Reset Threshold"),
        ("442601", 6000, "Conductor Temp Alarm"),
    ],
)
def test_sayisal_degerler_XML_ile_TUTUYOR(cat_index, beklenen, ad) -> None:
    doc = parse(GERCEK_DOSYA)
    girdi = doc.get(cat_index)
    assert girdi is not None, f"{cat_index} ayristirilamadi ({ad})"
    assert girdi.as_int() == beklenen, ad


def test_metin_alanlari_NUL_dolgusundan_arinir() -> None:
    doc = parse(GERCEK_DOSYA)
    assert doc.get("20000F").as_text() == "[not configured]"
    assert doc.get("20101A").as_text() == "2026-08-05 15:52:51"


def test_UZUNLUK_ONALTILIK_okunur() -> None:
    """Ondalik saymak SESSIZCE yanlis sonuc verir.

    `2000,0F,11,...` degeri 0x11 = 17 bayttir. Ondalik okunsaydi 11 bayt
    beklenir, uzunluk denetimi patlar ve dosya ayristirilamazdi -- ya da daha
    kotusu, denetim olmasa deger yarim okunurdu.
    """
    doc = parse(GERCEK_DOSYA)
    assert doc.get("20000F").length == 17
    assert doc.get("20101A").length == 20


def test_sifir_uzunluklu_girdi_DEGER_alanisiz_olur() -> None:
    """`1202,02,00` satirinda dorduncu alan HIC YOKTUR."""
    doc = parse(GERCEK_DOSYA)
    girdi = doc.get("120202")
    assert girdi is not None
    assert girdi.length == 0 and girdi.raw == b""


# --- ASIL KORUMA -----------------------------------------------------------
def test_gidis_donus_BAYT_BAYT_ayni() -> None:
    doc = parse(GERCEK_DOSYA)
    assert render(doc) == GERCEK_DOSYA


def test_checksum_ALGORITMASI_gercek_cihaz_dosyasiyla_dogrulandi() -> None:
    """`sum(payload) + checksum ≡ 0 (mod 0x10000)`.

    Gercek dosyada (seri 50984) saklanan deger 0x3ED4 idi ve bu formul onu
    birebir uretiyordu. Burada ozelligi sabit bir dosyaya baglamadan sinariz.
    """
    for govde in (GERCEK_SATIRLAR, b"", b"A", b"\xff" * 300):
        assert (sum(govde) + calculate_checksum(govde)) & 0xFFFF == 0


def test_okunan_dosyanin_checksum_u_DOGRULANIR() -> None:
    doc = parse(GERCEK_DOSYA)
    assert doc.checksum_valid is True
    assert doc.stored_checksum == calculate_checksum(GERCEK_SATIRLAR)


def test_BOZUK_checksum_yakalanir_ama_PATLAMAZ() -> None:
    """Bozuk dosyayi acip kullaniciya gosterebilmek, hic acamamaktan iyidir.

    Ama sessizce gecmez: `checksum_valid` False doner ve karar cagirana kalir.
    """
    bozuk = GERCEK_SATIRLAR + b"\x00\x00" + MARKER
    doc = parse(bozuk)
    assert doc.checksum_valid is False
    assert doc.get("2010C6").as_int() == 1440  # icerik yine de okunabiliyor


def test_ayagi_olmayan_dosyada_checksum_valid_None() -> None:
    """Kismi/bozuk dosya: "gecerli" ile "bilinmiyor" ayni sey DEGIL."""
    doc = parse(b"2010,C6,02,A005\r\n")
    assert doc.checksum_valid is None


def test_CRLF_satir_sonu_KORUNUR() -> None:
    """LF'e cevirmek toplami degistirir ve dosyayi gecersiz kilar."""
    doc = parse(GERCEK_DOSYA)
    assert doc.newline == "\r\n"
    assert b"\r\n" in render(doc)


def test_BOM_ilk_satiri_BOZMAZ() -> None:
    """Explorer bazen BOM ekliyor; basta kalirsa ilk GROUP okunamaz."""
    doc = parse(b"\xef\xbb\xbf" + GERCEK_DOSYA)
    assert doc.get("381101") is not None


# --- Degistirme ------------------------------------------------------------
def test_deger_degistirince_yalnizca_O_girdi_degisir() -> None:
    doc = parse(GERCEK_DOSYA)
    doc.set_int("2010C6", 720)  # Dial-In Interval 1440 -> 720

    yeni = parse(render(doc))
    assert yeni.get("2010C6").as_int() == 720
    # Digerleri aynen duruyor
    assert yeni.get("320001").as_int() == 10000
    # Checksum YENIDEN hesaplandi ve gecerli — eski toplam tasinsaydi dosya
    # cihaz tarafindan reddedilirdi.
    assert yeni.checksum_valid is True
    # Uzunluk korunmus: 2 bayt
    assert yeni.get("2010C6").length == 2


def test_UZUNLUK_degistirmek_REDDEDILIR() -> None:
    """Cihaz her girdiyi SABIT genislikte okur."""
    doc = parse(GERCEK_DOSYA)
    with pytest.raises(ConfigParseError):
        doc.set_raw("2010C6", b"\x01\x02\x03")


def test_sigmayan_deger_SESSIZCE_KIRPILMAZ() -> None:
    """2 baytlik alana 70000 yazmak, kirpilirsa 4464 olurdu.

    Sessiz kirpma en tehlikeli hatadir: kullanici 70000 girer, cihaza 4464
    gider, kimse fark etmez.
    """
    doc = parse(GERCEK_DOSYA)
    with pytest.raises(ConfigParseError):
        doc.set_int("2010C6", 70000)


def test_olmayan_girdi_YARATILMAZ() -> None:
    doc = parse(GERCEK_DOSYA)
    with pytest.raises(KeyError):
        doc.set_int("FFFF01", 1)


# --- Bozuk girdi -----------------------------------------------------------
def test_uzunluk_ile_deger_UYUSMAZSA_patlar() -> None:
    """Sessizce kabul etmek, yarim okunmus bir ayari cihaza yazmak olurdu."""
    with pytest.raises(ConfigParseError, match="uzunluk"):
        parse(b"2010,C6,04,A005\n")


def test_ayristirilamayan_satir_SESSIZCE_ATILMAZ() -> None:
    """Kaybedilen satir = geri yazildiginda kaybolan bir ayar."""
    doc = parse(b"2010,C6,02,A005\nBOZUK SATIR\n")
    assert doc.unparsed and doc.unparsed[0][1] == "BOZUK SATIR"


# --- Sayi donusumleri ------------------------------------------------------
def test_little_endian_dogru_yonde() -> None:
    assert decode_int(bytes.fromhex("A005")) == 1440
    assert encode_int(1440, 2).hex().upper() == "A005"
    assert decode_int(bytes.fromhex("90D00300")) == 250000
    assert decode_text(b"abc\x00\x00") == "abc"


# --- XML katalogu ----------------------------------------------------------
XML = b"""<?xml version="1.0" encoding="utf-8"?>
<Explorer><Navigator><ObjectCatalog Version="0.01">
  <CatalogObject>
    <CatIndex>2010C6</CatIndex>
    <ObjectValue ObjectIndex="0">
      <Meaning>Dial -In Interval</Meaning><ObjVal>1440</ObjVal><Unit>min</Unit>
    </ObjectValue>
  </CatalogObject>
  <CatalogObject>
    <CatIndex>F10401</CatIndex>
    <ObjectValue ObjectIndex="0"><Meaning>PLL1</Meaning><ObjVal>101</ObjVal></ObjectValue>
    <ObjectValue ObjectIndex="1"><Meaning>PLL2</Meaning><ObjVal>127</ObjVal></ObjectValue>
  </CatalogObject>
</ObjectCatalog></Navigator></Explorer>"""


def test_katalog_CSV_ile_eslesiyor() -> None:
    """Katalogun tek isi ham girdiye ANLAM vermek."""
    katalog = parse_catalog(XML)
    doc = parse(GERCEK_DOSYA)

    girdi = doc.get("2010C6")
    anlam = katalog[girdi.cat_index]
    assert anlam.meaning == "Dial -In Interval"
    assert anlam.unit == "min"
    # Katalogdaki metin deger ile CSV'den cozdugumuz sayi ayni seyi soylemeli.
    assert int(anlam.value) == girdi.as_int() == 1440


def test_katalog_coklu_ObjectValue_da_ILKINI_alir() -> None:
    """CSV tarafinda her (group,index) TEK satirdir; eslesme birebir olmali."""
    katalog = parse_catalog(XML)
    assert katalog["F10401"].meaning == "PLL1"
    assert katalog["F10401"].value == "101"
