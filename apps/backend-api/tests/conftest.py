"""pytest kok konfigurasyon: `src` yolunu ve ortak fixture'lari hazirlar.

backend-api paketinin kendi icinde `app/` altinda yasadigi icin testler
`apps/backend-api` dizininden kosturulmali. Bu conftest `app` paketinin
bulunabilmesi icin `sys.path`'i ayarlar.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def lisans_kilidi_kapali():
    """Lisans kilidini bu test boyunca ACIK tut (ham-ASGI testleri icin).

    NEDEN GEREKLI
    -------------
    `LicenseGateMiddleware` DEFAULT-DENY calisir: lisanssiz bir kurulumda
    `api_prefix` altindaki beyaz listede OLMAYAN her yol 403 doner ve handler
    HIC CALISMAZ. `/gateways/*` bilerek beyaz listede degildir — lisanssiz
    kurulum urun isi yapmayi birakmali.

    Handler'i DOGRUDAN cagiran testler middleware'i hic gormez; ama uygulamayi
    ham ASGI ile suren testler tam olarak bu kapiya carpar. Kilit, ortamdan
    okunan lisans durumuna baglidir: gelistirici makinesinde `.env` ile
    "valid", CI'da lisans yok. Yani fixture'siz bir ASGI testi YERELDE GECER,
    CI'DA DUSER — ve dusme sebebi test ettigi seyle ilgisizdir.

    Bu yuzden kilit durumu ORTAMDAN degil testten gelir. Lisans kapisinin
    KENDI davranisi ayrica ve acikca test ediliyor (`test_license_gate.py`,
    ve bu ucun kilit KAPSAMINDA oldugunu dogrulayan
    `test_device_runtime_health.py::test_device_health_ucu_lisans_kilidi_KAPSAMINDA`).

    Yama noktasi middleware'in cagirdigi isim: `is_api_locked` her istekte
    yeniden okunur, dolayisiyla modul ozniteligini degistirmek yeterli ve
    `get_enforcement_state` onbellegine de takilmaz.
    """
    from app.services import license_service

    onceki = license_service.is_api_locked
    license_service.is_api_locked = lambda: False
    try:
        yield
    finally:
        license_service.is_api_locked = onceki
