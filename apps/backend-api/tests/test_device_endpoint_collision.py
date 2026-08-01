"""Ayni gateway'de ayni (IP, port) IKI cihaza verilemez.

YASANAN (2026-08-01, saha test cihazi)
---------------------------------------
Iki cihaz yanlislikla ayni uc noktaya (192.168.211.1:20001) ayarlanmisti.
Horstmann outstation'i `CloseExisting` modunda calisiyor — yeni bir istemci
baglaninca MEVCUT baglantiyi kapatiyor. Karsilikli tahliye dongusu olustu:

    d baglanir -> d15 baglanir -> outstation d'yi atar
    -> d yeniden baglanir -> d15'i atar -> ...

Gateway gunlugunde **2.172 `link_close`** birikti; iki cihaz icin sayilar
1.198 ve 1.196 idi — neredeyse esit, cunku sirayla birbirlerini atiyorlardi.
Cihazlar surekli "bayat" olup toparlandi, telemetri kesintili geldi.

BELIRTI KAYNAGINA HIC BENZEMIYORDU. Gorunen sey "ag kararsiz, cihazlar
kopuyor" idi; gercekte tek bir yanlis port alaniydi. Port duzeltildikten
sonra 15 dakikada SIFIR kapanma olustu.

Sistem buna sessizce izin veriyordu — bu testler o sessizligi kaldiriyor.
"""

from __future__ import annotations

import inspect
import re

from app.api import devices as devices_api


def _kod(kaynak: str) -> str:
    kaynak = re.sub(r'""".*?"""', "", kaynak, flags=re.DOTALL)
    return re.sub(r"^\s*#.*$", "", kaynak, flags=re.MULTILINE)


def test_kontrol_fonksiyonu_VAR():
    assert hasattr(devices_api, "_require_unique_endpoint")


def test_OLUSTURMA_yolunda_kontrol_ediliyor():
    kod = _kod(inspect.getsource(devices_api.create_device))
    assert "_require_unique_endpoint(" in kod, (
        "cihaz olustururken uc nokta cakismasi kontrol edilmiyor"
    )


def test_GUNCELLEME_yolunda_kontrol_ediliyor():
    """Asil hata burada yapilmisti: mevcut bir cihazin portu duzenlenirken
    baskasininkiyle ayni yapildi."""
    kod = _kod(inspect.getsource(devices_api.update_device))
    assert "_require_unique_endpoint(" in kod, (
        "cihaz guncellenirken uc nokta cakismasi kontrol edilmiyor — hata "
        "TAM OLARAK bu yoldan yapilmisti"
    )


def test_guncellemede_cihazin_KENDISI_haric_tutuluyor():
    """Aksi halde bir cihaz kendi adresiyle cakisir ve hicbir guncelleme
    yapilamazdi."""
    kod = _kod(inspect.getsource(devices_api.update_device))
    assert "exclude_device_id=device.id" in kod


def test_guncellemede_MEVCUT_degerlerle_harmanlaniyor():
    """Yalnizca `changes` icindekilere bakmak, IP degisip port ayni
    kaldiginda cakismayi KACIRIRDI."""
    kod = _kod(inspect.getsource(devices_api.update_device))
    for alan in ("ip_address", "dnp3_outstation_port", "gateway_code"):
        assert f'changes.get("{alan}", device.' in kod, (
            f"{alan} icin mevcut deger fallback'i yok — kismi guncellemede "
            "cakisma kacirilir"
        )


def test_kapsam_GATEWAY_bazinda():
    """Farkli gateway'ler farkli ag segmentlerinde olabilir ve ayni IP'yi
    mesru sekilde gorebilirler; global kontrol yanlis red uretirdi."""
    kod = _kod(inspect.getsource(devices_api._require_unique_endpoint))
    assert "Device.gateway_code == gateway_code" in kod, (
        "kontrol gateway bazinda degil — farkli aglardaki mesru ayni IP'ler "
        "reddedilir"
    )


def test_eksik_bilgide_SESSIZCE_geciliyor():
    """gateway/IP/port'tan biri yoksa cakisma tanimsizdir; kontrol yanlis
    red uretmemeli."""
    kod = _kod(inspect.getsource(devices_api._require_unique_endpoint))
    assert "if not gateway_code or not ip_address or not port:" in kod
    assert "return" in kod


def test_cakismada_409_donuyor():
    kod = _kod(inspect.getsource(devices_api._require_unique_endpoint))
    assert "HTTP_409_CONFLICT" in kod


def test_hata_mesaji_SEBEBI_acikliyor():
    """Belirti kaynagina benzemedigi icin mesajin ne olacagini soylemesi
    sart: operator 'ag kararsiz' diye saatler kaybetmesin."""
    kod = inspect.getsource(devices_api._require_unique_endpoint)
    for ipucu in ("ayni adresi", "kopuk"):
        assert ipucu in kod, f"hata mesaji sonucu anlatmiyor ({ipucu})"
