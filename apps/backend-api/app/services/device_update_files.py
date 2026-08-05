"""Horstmann SN2.0 guncelleme dosyalarinin AD ve YOL uretimi.

Kaynak: HH-EW-25-019 Rev 1.0 "Smart Navigator 2.0 - FTP for FW Upgrade and
Configuration Change" (Horstmann, 02.04.2025).

NEDEN AYRI BIR MODUL
--------------------
Cihaz, FTP dizininde ARADIGI ADI birebir bilir ve baska hicbir dosyaya
bakmaz. Ad bir karakter tutmazsa cihaz dosyayi SESSIZCE yok sayar:

    - DNP3 binary output gider, cihaz DNP oturumunu kapatir,
    - FTP'ye baglanir, aradigini bulamaz, geri doner,
    - hicbir hata, hicbir olay, hicbir kayit olusmaz.

Yani en olasi ariza bicimi "calismadi" degil, "hicbir sey olmadi". Bu yuzden
adi KULLANICI YAZMAZ; burada uretilir ve yuklemeden once dogrulanir.

KAPSAM (scope) NEDEN VAR
------------------------
Dokumanda iki adlandirma semasi tanimli:

  TEKIL  — ad cihazin SERI NUMARASINDAN turer. Tek bir uniteyi/seti gunceller.
  TOPLU  — ad hedef FIRMWARE SURUMUNDEN turer. Ayni surumdeki tum uniteler
           ayni dosyayi indirir.

Ikisi FARKLI DNP3 binary output indeksleriyle tetiklenir (0/1/2 ve 10/11/12).
Ad ile tetikleyici indeks BIRBIRINE BAGLIDIR: toplu adla tekil komut gonderen
(veya tersi) bir akis, yine sessiz basarisizlik uretir. Bu yuzden ikisi tek
yerde, tek tabloda tutuluyor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "UpdateKind",
    "UpdateScope",
    "UpdatePlan",
    "InvalidUpdateTarget",
    "build_plan",
    "normalize_fw_version",
    "FTP_USERNAME_MAX",
    "FTP_PASSWORD_MAX",
    "FTP_DIRECTORY_MAX",
]


class InvalidUpdateTarget(ValueError):
    """Hedef (seri no / surum) dokumanin bekledigi bicimde degil."""


class UpdateKind(str, Enum):
    """Guncelleme turu — dosya uzantisini ve komutu belirler."""

    CONFIG = "config"          # cihaz parametreleri (.csv)
    DNP3_POINTS = "dnp3"       # DNP3 nokta listesi (.bin)
    FIRMWARE = "firmware"      # gomulu yazilim (.utf)


class UpdateScope(str, Enum):
    """Hedefleme bicimi — dosya adinin neye gore turedigini belirler."""

    SINGLE = "single"  # seri numarasina gore
    BULK = "bulk"      # firmware surumune gore


# Dokumandaki iki tablo. Her satir: (uzanti son eki, tekil indeks, toplu indeks).
#
# Indeksler burada BILGI AMACLI; gercek adres `SignalCatalog`'tan
# `resolve_command_index` ile cozulur (cihaz profili degisebilir). Buradaki
# deger, katalogla tutarliligi sinayan testin referansidir — ikisi ayrisirsa
# test kirmizi olur ve sessiz uyumsuzluk sahaya cikmaz.
_SPEC: dict[UpdateKind, tuple[str, int, int]] = {
    UpdateKind.CONFIG: ("_Configuration.csv", 0, 10),
    UpdateKind.DNP3_POINTS: ("_DNP3_settings.bin", 1, 11),
    UpdateKind.FIRMWARE: ("_Firmware.utf", 2, 12),
}

# Slug'lar `app/data/horstmann_sn2_signals.json` ile ayni olmali.
_SLUG: dict[tuple[UpdateKind, UpdateScope], str] = {
    (UpdateKind.CONFIG, UpdateScope.SINGLE): "master.config_update",
    (UpdateKind.DNP3_POINTS, UpdateScope.SINGLE): "master.dnp3_config_update",
    (UpdateKind.FIRMWARE, UpdateScope.SINGLE): "master.firmware_update",
    (UpdateKind.CONFIG, UpdateScope.BULK): "master.trigger_config_download",
    (UpdateKind.DNP3_POINTS, UpdateScope.BULK): "master.trigger_dnp3_config_download",
    (UpdateKind.FIRMWARE, UpdateScope.BULK): "master.trigger_firmware_download",
}

# Cihaz FTP ekranindaki alan sinirlari (dokuman "Configuration" bolumu).
# "<30" / "<20" yaziyor; yani 30 DEGIL 29 son gecerli uzunluk.
FTP_USERNAME_MAX = 29
FTP_PASSWORD_MAX = 19
FTP_DIRECTORY_MAX = 29

# Seri numarasi dokuman orneklerinde saf rakam (49904, SN20 cihazinda 20).
# Yine de harf iceren seriler icin kapiyi kapatmiyoruz; yasakladigimiz sey
# dosya adini bozabilecek ayirici karakterler.
_SERIAL_RE = re.compile(r"^[A-Za-z0-9]{1,20}$")

# "2.338.55" -> V2_338_55 . Dokuman ornegi: FW 2.338.55 -> V2_338_55_Firmware.utf
_VERSION_RE = re.compile(r"^v?(\d{1,3})[._](\d{1,3})[._](\d{1,3})$", re.IGNORECASE)


def normalize_fw_version(raw: str) -> str:
    """'2.338.55' | 'V2_338_55' | 'v2.338.55'  ->  'V2_338_55'.

    Kullanici surumu nokta ile yazar (cihaz ekraninda oyle gorunur), dosya adi
    ise alt cizgi ister. Donusumu kullaniciya biraktigimizda tek bir yanlis
    karakter sessiz basarisizlik demek; o yuzden burada normalize ediyoruz.
    """
    m = _VERSION_RE.match((raw or "").strip())
    if not m:
        raise InvalidUpdateTarget(
            f"Firmware surumu 'x.yyy.zz' biciminde olmali (alinan: {raw!r})"
        )
    return "V{}_{}_{}".format(*m.groups())


def _normalize_serial(raw: str) -> str:
    serial = (raw or "").strip()
    if not _SERIAL_RE.match(serial):
        raise InvalidUpdateTarget(
            "Seri numarasi bos olamaz ve yalnizca harf/rakam icerebilir "
            f"(alinan: {raw!r})"
        )
    return serial


@dataclass(frozen=True)
class UpdatePlan:
    """Tek bir guncellemenin dosya adi + tetikleyici komutu, birlikte.

    Ikisi ayri ayri uretilirse eslesmeme riski dogar (toplu ad + tekil komut).
    Tek bir nesnede dondurulmelerinin sebebi bu.
    """

    kind: UpdateKind
    scope: UpdateScope
    filename: str
    command_slug: str
    dnp3_index: int
    target: str  # seri no ya da normalize edilmis surum — kayit/log icin

    def path_in(self, directory: str) -> str:
        """Cihazin FTP dizini altindaki tam yol (POSIX, tek '/')."""
        return f"{(directory or '/').rstrip('/')}/{self.filename}"


def build_plan(
    kind: UpdateKind,
    scope: UpdateScope,
    *,
    serial_number: str | None = None,
    fw_version: str | None = None,
) -> UpdatePlan:
    """Dosya adini ve tetikleyici komutu birlikte uretir.

    TEKIL kapsamda `serial_number`, TOPLU kapsamda `fw_version` ZORUNLUDUR.
    Eksik olani sessizce tamamlamak (orn. seri yoksa toplu'ya dusmek) yanlis
    cihazlari guncellemeye kadar gidebilecegi icin hata veriyoruz.
    """
    suffix, single_idx, bulk_idx = _SPEC[kind]

    if scope is UpdateScope.SINGLE:
        if not serial_number:
            raise InvalidUpdateTarget(
                "Tekil guncelleme icin cihazin seri numarasi gerekli; "
                "cihaz kaydinda seri numarasi bos."
            )
        target = _normalize_serial(serial_number)
        index = single_idx
    else:
        if not fw_version:
            raise InvalidUpdateTarget(
                "Toplu guncelleme icin hedef firmware surumu gerekli."
            )
        target = normalize_fw_version(fw_version)
        index = bulk_idx

    return UpdatePlan(
        kind=kind,
        scope=scope,
        filename=f"{target}{suffix}",
        command_slug=_SLUG[(kind, scope)],
        dnp3_index=index,
        target=target,
    )
