"""Desteklenen cihaz modelleri.

MODEL LISTESI IKI KAYNAKTAN GELIR
---------------------------------
1. `BUILTIN_MODELS` — kod icinde tanimli, INSANCA OKUNUR etiketi olanlar.
2. `signal_catalog.model` — katalogta sinyali tanimlanmis HER model.

Ikisinin BIRLESIMI kullanilir.

NEDEN KATALOGDAN DA TURETILIYOR
-------------------------------
Sinyal katalogu API'den duzenlenebiliyor (POST/PATCH/DELETE /signals), yani
yeni bir modelin adres haritasi surum cikarmadan girilebiliyordu. Ama model
listesi yalnizca bu dosyadaki sozlukten geliyordu; sonuc tutarsizdi:

  * yeni modelin sinyallerini girebiliyordunuz,
  * ama o modeli hicbir cihaza ATAYAMIYORDUNUZ (form dropdown'inda yok),
  * ustelik `GET /signals?model=<yeni>` "Unknown device model" ile 400
    donuyordu — kendi girdiginiz veriyi listeleyemiyordunuz.

Yeni bir Horstmann modeli (ya da ileride baska bir marka) eklemek icin
SINYALLERI tanimlamak yeterli olmali; kod degisikligi ve yeni imaj
gerekmemeli. Adres haritasi VERIDIR, kod degil.

`BUILTIN_MODELS` yine de duruyor: bilinen modellerin arayuzde "Horstmann
Smart Navigator 2.0" gibi duzgun bir adi olsun diye. Katalogdan gelen ama
burada karsiligi olmayan bir model KENDI KODUYLA gosterilir — cirkin ama
DOGRU; uydurma bir etiket uretmiyoruz.

`code` veritabaninda `devices.model` ve `signal_catalog.model` kolonlarinda
saklanan kanonik degerdir; degistirme.
"""

from __future__ import annotations

from typing import Any

DEFAULT_MODEL: str = "horstmann_sn_2_0"

#: Insanca okunur etiketi olan, kod icinde tanimli modeller.
BUILTIN_MODELS: dict[str, str] = {
    "horstmann_sn_2_0": "Horstmann Smart Navigator 2.0",
}

#: Geriye donuk ad.
MODELS = BUILTIN_MODELS


def _katalog_modelleri(db: Any) -> list[str]:
    """Katalogta sinyali olan model kodlari.

    Hata HICBIR kosulda listeyi dusurmemeli: veritabani okunamazsa yerlesik
    listeye duseriz. Model dropdown'inin bos gelmesi, cihaz eklemeyi tamamen
    engellerdi.
    """
    if db is None:
        return []
    try:
        from sqlalchemy import select

        from app.models.signal_catalog import SignalCatalog

        satirlar = db.scalars(
            select(SignalCatalog.model).where(SignalCatalog.model.is_not(None)).distinct()
        ).all()
        return [str(m).strip() for m in satirlar if m and str(m).strip()]
    except Exception:  # noqa: BLE001
        return []


def _birlesim(db: Any = None) -> dict[str, str]:
    """kod -> etiket. Yerlesik etiketler kazanir; katalogdan gelen yeni
    kodlar kendi kodlariyla listelenir."""
    sonuc = dict(BUILTIN_MODELS)
    for kod in _katalog_modelleri(db):
        sonuc.setdefault(kod, kod)
    return sonuc


def is_valid_model(code: str, db: Any = None) -> bool:
    """`db` verilirse katalogdaki modeller de gecerli sayilir.

    `db` opsiyoneldir: oturum verilmeyen cagrilarda davranis eskisiyle ayni
    kalir (yalnizca yerlesik modeller).
    """
    return code in _birlesim(db)


def model_label(code: str, db: Any = None) -> str:
    return _birlesim(db).get(code, code)


def list_models(db: Any = None) -> list[dict[str, str]]:
    """Arayuzun model dropdown'ini besleyen liste.

    Yerlesikler once, katalogdan gelenler alfabetik — operator once bildigi
    modeli gorsun.
    """
    tum = _birlesim(db)
    yerlesik = [{"code": k, "label": tum[k]} for k in BUILTIN_MODELS if k in tum]
    ek = [{"code": k, "label": v} for k, v in sorted(tum.items()) if k not in BUILTIN_MODELS]
    return yerlesik + ek
