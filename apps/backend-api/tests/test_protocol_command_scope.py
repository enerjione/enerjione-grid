"""Protokol uzerinden gelen cihaz komutlarinin KAPSAMI.

URUN KARARI
-----------
IEC 104 uzerinden kabul edilen TEK kontrol komutu ariza gostergesi
RESET'idir (`reset_all_fcis`).

NEDEN BURADA DA KONTROL VAR — IKINCI SAVUNMA KATMANI
-----------------------------------------------------
Birinci katman iec104-outbound'un registry'sinde: yalnizca izin verilen
slug'lar (CA, IOA) haritasina girer. Ama o katman KATALOG VERISINE dayaniyor
ve katalog `PATCH /signals/{key}` ile duzenlenebiliyor. Birinci katman
atlanirsa (yanlis yapilandirma, baska bir protokol servisi, ya da dogrudan
internal uca yapilan bir cagri) komutun burada durmasi gerekir.

Somut risk: katalogda `firmware_update` binary_output'una IEC 104 komut tipi
verilmesi. Tek katmanli bir tasarimda bu, yanlis bir katalog duzenlemesini
UZAKTAN FIRMWARE TETIKLEMEYE cevirirdi.

Arayuz yolu KASITLI olarak daha genis: orada kimligi dogrulanmis, rolu belli
bir kullanici var. IEC 104 tarafinda yalnizca TCP baglantisi olan bir master
var.
"""

from __future__ import annotations

import pytest

from app.services import device_command_service as svc


def test_protokol_allowlisti_YALNIZCA_reset():
    assert svc.PROTOCOL_ALLOWED_SLUGS == frozenset({"reset_all_fcis"})


@pytest.mark.parametrize(
    "slug",
    [
        "firmware_update",
        "config_update",
        "dnp3_config_update",
        "trigger_firmware_download",
        "trigger_config_download",
        "start_csv_upload",
        "enable_password",
        "enable_local_communication",
    ],
)
def test_TEHLIKELI_komutlar_protokolden_gecmiyor(slug: str):
    """Katalog yanlis duzenlenmis olsa bile burada durmali."""
    with pytest.raises(svc.CommandRejected) as exc:
        svc.queue_protocol_command(
            db=None, device_code="DEV-001", slug=slug, origin="iec104", peer="10.0.0.5",
        )
    assert exc.value.reason == "not_allowed_for_protocol", (
        f"{slug} protokol allowlist'inde durmadi"
    )


def test_izinli_komut_allowlist_kontrolunu_GECIYOR():
    """Allowlist reddetmemeli; sonraki adim (cihaz arama) DB istiyor,
    dolayisiyla farkli bir hata bekliyoruz — onemli olan `not_allowed_for_protocol`
    OLMAMASI."""
    with pytest.raises(Exception) as exc:
        svc.queue_protocol_command(
            db=None, device_code="DEV-001", slug="reset_all_fcis",
            origin="iec104", peer="10.0.0.5",
        )
    reason = getattr(exc.value, "reason", None)
    assert reason != "not_allowed_for_protocol", (
        "izin verilen reset komutu allowlist'te takildi"
    )


def test_bilinmeyen_slug_reddediliyor():
    with pytest.raises(svc.CommandRejected) as exc:
        svc.queue_protocol_command(
            db=None, device_code="DEV-001", slug="uydurma_komut",
            origin="iec104", peer="10.0.0.5",
        )
    assert exc.value.reason == "not_allowed_for_protocol"


def test_arayuz_yolu_protokol_allowlistine_BAGLI_DEGIL():
    """Arayuz kasitli olarak daha genis: orada rol kontrolu var.

    `queue_command` protokol allowlist'ine BAKMAMALI; baksaydi installer
    firmware guncellemesi gonderemezdi.
    """
    import inspect

    kaynak = inspect.getsource(svc.queue_command)
    assert "PROTOCOL_ALLOWED_SLUGS" not in kaynak, (
        "arayuz yolu protokol allowlist'ine baglanmis — installer config/firmware "
        "komutlarini gonderemez hale gelir"
    )


def test_audit_kaydinda_KAPI_ayirt_ediliyor():
    """'Bu reset'i kim istedi' sorusunun cevabi UI kullanicisi ile SCADA
    master'i arasinda ayrilabilmeli."""
    import inspect

    kaynak = inspect.getsource(svc.queue_command)
    assert '"origin": origin' in kaynak, (
        "audit metadata'sinda komutun geldigi kapi (ui/iec104) yok"
    )


def test_protokol_aktoru_PEER_iceriyor():
    """Adli inceleme icin master'in adresi audit'e yazilmali."""
    import inspect

    kaynak = inspect.getsource(svc.queue_protocol_command)
    assert 'actor=f"{origin}:{peer}"' in kaynak, (
        "protokol komutunun aktoru master adresini icermiyor"
    )
