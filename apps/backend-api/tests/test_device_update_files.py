"""Guncelleme dosyasi adlandirmasi — dokumana ve sinyal kataloguna karsi.

Bu testlerin degeri sunda: yanlis adlandirilmis bir dosya HATA URETMEZ. Cihaz
FTP'ye baglanir, aradigi adi bulamaz, sessizce geri doner. Ne log, ne olay, ne
alarm. Yani bu alanda "test yesil ama sahada calismiyor" degil, "sahada
calismiyor ve kimse fark etmiyor" riski var.

Beklenen degerler HH-EW-25-019 Rev 1.0 dokumanindaki iki ornek tablodan
birebir alindi.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.device_update_files import (
    FTP_DIRECTORY_MAX,
    FTP_PASSWORD_MAX,
    FTP_USERNAME_MAX,
    InvalidUpdateTarget,
    UpdateKind,
    UpdatePlan,
    UpdateScope,
    build_plan,
    normalize_fw_version,
)


# --- Dokumanin TEKIL tablosu (Seri No 49904) -------------------------------
@pytest.mark.parametrize(
    ("kind", "beklenen_ad", "beklenen_index"),
    [
        (UpdateKind.CONFIG, "49904_Configuration.csv", 0),
        (UpdateKind.DNP3_POINTS, "49904_DNP3_settings.bin", 1),
        (UpdateKind.FIRMWARE, "49904_Firmware.utf", 2),
    ],
)
def test_tekil_adlandirma_dokumanla_birebir(kind, beklenen_ad, beklenen_index) -> None:
    plan = build_plan(kind, UpdateScope.SINGLE, serial_number="49904")
    assert plan.filename == beklenen_ad
    assert plan.dnp3_index == beklenen_index


# --- Dokumanin TOPLU tablosu (FW 2.338.55) ---------------------------------
@pytest.mark.parametrize(
    ("kind", "beklenen_ad", "beklenen_index"),
    [
        (UpdateKind.CONFIG, "V2_338_55_Configuration.csv", 10),
        (UpdateKind.DNP3_POINTS, "V2_338_55_DNP3_settings.bin", 11),
        (UpdateKind.FIRMWARE, "V2_338_55_Firmware.utf", 12),
    ],
)
def test_toplu_adlandirma_dokumanla_birebir(kind, beklenen_ad, beklenen_index) -> None:
    plan = build_plan(kind, UpdateScope.BULK, fw_version="2.338.55")
    assert plan.filename == beklenen_ad
    assert plan.dnp3_index == beklenen_index


@pytest.mark.parametrize(
    "girdi", ["2.338.55", "V2_338_55", "v2.338.55", "  2.338.55  ", "2_338_55"]
)
def test_surum_bicimleri_ayni_ada_cikar(girdi) -> None:
    """Kullanici surumu nokta ile de alt cizgi ile de yazabilir.

    Donusumu kullaniciya birakmak, tek yanlis karakterde sessiz basarisizlik
    demekti; normalize burada yapiliyor.
    """
    assert normalize_fw_version(girdi) == "V2_338_55"


@pytest.mark.parametrize("bozuk", ["2.338", "surum", "", "2.338.55.1", "a.b.c"])
def test_gecersiz_surum_REDDEDILIR(bozuk) -> None:
    with pytest.raises(InvalidUpdateTarget):
        normalize_fw_version(bozuk)


def test_seri_numarasi_YOKSA_tekil_guncelleme_REDDEDILIR() -> None:
    """SESSIZCE topluya dusmemeli.

    Dusseydi, tek cihaz guncellemek isteyen bir islem AYNI SURUMDEKI TUM
    cihazlari gunceller. Bu, geri alinamaz bir yanlis hedefleme.
    """
    with pytest.raises(InvalidUpdateTarget):
        build_plan(UpdateKind.FIRMWARE, UpdateScope.SINGLE, serial_number=None)
    with pytest.raises(InvalidUpdateTarget):
        build_plan(UpdateKind.FIRMWARE, UpdateScope.SINGLE, serial_number="   ")


def test_toplu_guncelleme_surumsuz_REDDEDILIR() -> None:
    with pytest.raises(InvalidUpdateTarget):
        build_plan(UpdateKind.CONFIG, UpdateScope.BULK, fw_version=None)


@pytest.mark.parametrize("bozuk", ["49904/../x", "49 904", "sn*20", "a" * 21])
def test_dosya_adini_bozabilecek_seri_REDDEDILIR(bozuk) -> None:
    """Ayirici karakter iceren seri, FTP yolunu dizin disina tasiyabilir."""
    with pytest.raises(InvalidUpdateTarget):
        build_plan(UpdateKind.CONFIG, UpdateScope.SINGLE, serial_number=bozuk)


def test_yol_uretimi_cift_slash_uretmez() -> None:
    plan = build_plan(UpdateKind.FIRMWARE, UpdateScope.SINGLE, serial_number="20")
    assert plan.path_in("/SN20/FOTA/") == "/SN20/FOTA/20_Firmware.utf"
    assert plan.path_in("/SN20/FOTA") == "/SN20/FOTA/20_Firmware.utf"


def test_ad_ve_KOMUT_birlikte_doner() -> None:
    """Ad ile tetikleyici ayri ayri secilirse eslesmeme riski dogar.

    Toplu ad + tekil komut (ya da tersi) yine sessiz basarisizlik uretir;
    bu yuzden ikisi tek nesnede.
    """
    tekil = build_plan(UpdateKind.FIRMWARE, UpdateScope.SINGLE, serial_number="49904")
    toplu = build_plan(UpdateKind.FIRMWARE, UpdateScope.BULK, fw_version="2.338.55")
    assert isinstance(tekil, UpdatePlan)
    assert tekil.command_slug == "master.firmware_update"
    assert toplu.command_slug == "master.trigger_firmware_download"
    assert tekil.command_slug != toplu.command_slug


# --- ASIL KORUMA: iki kaynak ayrisirsa yakala ------------------------------
def test_indeksler_SINYAL_KATALOGU_ile_TUTARLI() -> None:
    """DNP3 indeksleri iki yerde yasiyor: burada ve sinyal katalogunda.

    Gercek komut adresi katalogdan cozuluyor (`resolve_command_index`). Biri
    degisip digeri kalirsa YANLIS KOMUT gider ve bu, calisan bir sistemde
    sessizce yanlis islem yapmak demektir. Test ikisini karsilastirir.
    """
    veri = Path(__file__).resolve().parents[1] / "app/data/horstmann_sn2_signals.json"
    ham = json.loads(veri.read_text(encoding="utf-8"))
    kayitlar = ham if isinstance(ham, list) else ham.get("signals", ham)

    katalog = {
        s["key"]: int(s["dnp3_index"])
        for s in kayitlar
        if isinstance(s, dict) and s.get("data_type") == "binary_output"
    }

    beklenen = [
        (UpdateKind.CONFIG, UpdateScope.SINGLE, "49904", None),
        (UpdateKind.DNP3_POINTS, UpdateScope.SINGLE, "49904", None),
        (UpdateKind.FIRMWARE, UpdateScope.SINGLE, "49904", None),
        (UpdateKind.CONFIG, UpdateScope.BULK, None, "2.338.55"),
        (UpdateKind.DNP3_POINTS, UpdateScope.BULK, None, "2.338.55"),
        (UpdateKind.FIRMWARE, UpdateScope.BULK, None, "2.338.55"),
    ]

    for kind, scope, seri, surum in beklenen:
        plan = build_plan(kind, scope, serial_number=seri, fw_version=surum)
        assert plan.command_slug in katalog, (
            f"{plan.command_slug} sinyal katalogunda yok — komut cozulemez"
        )
        assert katalog[plan.command_slug] == plan.dnp3_index, (
            f"{plan.command_slug}: katalog {katalog[plan.command_slug]}, "
            f"dokuman {plan.dnp3_index} — iki kaynak ayrismis"
        )


def test_ftp_alan_sinirlari_dokumanla_uyumlu() -> None:
    """Dokuman '<30' / '<20' diyor; yani son gecerli uzunluk 29 ve 19."""
    assert FTP_USERNAME_MAX == 29
    assert FTP_PASSWORD_MAX == 19
    assert FTP_DIRECTORY_MAX == 29
