"""Guncelleme akisi ASAMA BILDIRMELI (`_do_update`).

YASANAN SORUN
-------------
`_do_install` her adimda `_write_status(... running=True)` yaziyordu, ama
`_do_update` YALNIZCA EN SONDA tek bir sonuc yaziyordu.

Arayuz tarafinda sonuc: operator "Guncelle"ye basiyor, ekranda hicbir sey
degismiyor, is bitene kadar (olculen sure 12 sn; sahada 4G uzerinden imaj
cekme dakikalar surebilir) "bastim mi basmadim mi" belli olmuyordu. Tekrar
basanlar ikinci istegin reddedildigini gosteren ham `request_pending`
metnini goruyordu.

En uzun ve en sik hata veren adim `pull`. Ayri bildirilmesi hem ilerleme
gostergesi hem teshis degeri tasiyor.

NEDEN AST
---------
Metin aramasi ("_write_status gecior mu") yaniltir: fonksiyonun herhangi bir
yerinde gecmesi yeterli olurdu, oysa onemli olan cagrilarin `pull` ve `up`
adimlarindan ONCE olmasi. Bu yuzden cagri sirasi AST ile inceleniyor.
"""

from __future__ import annotations

import ast
from pathlib import Path

_AJAN = Path(__file__).resolve().parents[1] / "e1-gwd.py"


def _fonksiyon(ad: str) -> ast.FunctionDef:
    agac = ast.parse(_AJAN.read_text(encoding="utf-8"))
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.FunctionDef) and dugum.name == ad:
            return dugum
    raise AssertionError(f"{ad} bulunamadi — e1-gwd.py yeniden duzenlenmis olabilir")


def _cagri_sirasi(fn: ast.FunctionDef) -> list[str]:
    """Fonksiyon govdesindeki ilgili cagrilari SIRAYLA dondurur.

    `_write_status({... "stage": "X" ...})` -> "status:X"
    `_run(... "pull" ...)`                  -> "run:pull"
    """
    sira: list[str] = []
    for dugum in ast.walk(fn):
        if not isinstance(dugum, ast.Call):
            continue
        ad = getattr(dugum.func, "id", None) or getattr(dugum.func, "attr", None)
        if ad == "_write_status" and dugum.args and isinstance(dugum.args[0], ast.Dict):
            sozluk = dugum.args[0]
            for anahtar, deger in zip(sozluk.keys, sozluk.values):
                if (
                    isinstance(anahtar, ast.Constant)
                    and anahtar.value == "stage"
                    and isinstance(deger, ast.Constant)
                ):
                    sira.append(f"status:{deger.value}")
        elif ad == "_run":
            metin = ast.dump(dugum)
            for adim in ("pull", "up"):
                if f"'{adim}'" in metin:
                    sira.append(f"run:{adim}")
                    break
    return sira


def test_guncelleme_imaj_cekmeden_once_asama_bildiriyor() -> None:
    sira = _cagri_sirasi(_fonksiyon("_do_update"))
    assert "status:pull" in sira, (
        "_do_update imaj cekmeden once asama bildirmiyor — arayuz en uzun "
        "adim boyunca sessiz kalir"
    )
    assert sira.index("status:pull") < sira.index("run:pull"), (
        "asama bildirimi `pull` cagrisindan SONRA yapiliyor; ilerleme "
        "gostergesi is bittikten sonra guncellenir"
    )


def test_guncelleme_baslatmadan_once_asama_bildiriyor() -> None:
    sira = _cagri_sirasi(_fonksiyon("_do_update"))
    assert "status:up" in sira, "_do_update baslatma adimini bildirmiyor"
    assert sira.index("status:up") < sira.index("run:up")


def test_kurulum_akisi_da_asama_bildirmeye_devam_ediyor() -> None:
    """`_do_install` zaten dogru davraniyordu; geri gitmesin."""
    sira = _cagri_sirasi(_fonksiyon("_do_install"))
    for asama in ("status:validate", "status:pull", "status:up"):
        assert asama in sira, f"_do_install {asama} bildirimini kaybetmis"
