"""Anasayfanin daralttigi sinyal anahtarlari KATALOGDA gercekten var olmali.

NEDEN
-----
Anasayfa artik `/signals/live` ucunu `signal_keys` ile daraltiyor: 600 cihazda
193 sinyalin tamamini istemek 115.800 satirlik (~42 MB) bir yanit demekti ve
WS koptugunda periyot 30 sn'den 5 sn'ye dustugu icin yuk 6 katlaniyordu.

Daraltmanin sessiz bir tehlikesi var: anahtarlardan biri YANLIS yazilirsa
(ya da katalogda yeniden adlandirilirsa) backend o sinyali bulamaz, BOS
doner ve hata VERMEZ. Kullanici anasayfada batarya/sinyal alanlarini bos
gorur; hicbir yerde "sinyal adi degisti" diye bir iz olmaz.

Bu test frontend'deki listeyi fabrika katalogu ile karsilastirir; ayrisma PR
aninda yakalanir.

NOT: harita DETAY MODALI cok daha fazla sinyal okuyor (26 sonek x 3 kaynak).
O liste bilerek daraltilmadi — modal yalnizca SECILI cihaz icin acildigindan
frontend o cihazin TAMAMINI ayrica cekiyor. Boylece burada tutulan liste kisa
kalir ve modal yeni bir sinyal okumaya basladiginda burayi guncellemeyi
unutma riski olusmaz.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
APP_TSX = REPO / "apps" / "frontend-web" / "src" / "app" / "App.tsx"
KATALOG = (
    REPO / "apps" / "backend-api" / "app" / "data" / "horstmann_sn2_signals.json"
)


def _dashboard_keys() -> list[str]:
    """App.tsx icindeki `DASHBOARD_SIGNAL_KEYS` dizisini okur."""
    kaynak = APP_TSX.read_text(encoding="utf-8")
    m = re.search(
        r"const DASHBOARD_SIGNAL_KEYS = \[(.*?)\]\s*as const;",
        kaynak,
        re.DOTALL,
    )
    assert m, "App.tsx icinde DASHBOARD_SIGNAL_KEYS bulunamadi"
    return re.findall(r'"([^"]+)"', m.group(1))


def _katalog_anahtarlari() -> set[str]:
    ham = json.loads(KATALOG.read_text(encoding="utf-8"))
    sinyaller = ham if isinstance(ham, list) else ham.get("signals", ham)
    return {s["key"] for s in sinyaller if s.get("key")}


def test_dosyalar_BULUNDU():
    """Yol yanlissa asagidaki testler sessizce yesil kalirdi."""
    assert APP_TSX.is_file(), APP_TSX
    assert KATALOG.is_file(), KATALOG


def test_liste_BOS_DEGIL():
    keys = _dashboard_keys()
    assert keys, "daraltma listesi bos — tum kartezyen cekilir"
    assert len(keys) <= 12, (
        f"{len(keys)} anahtar: liste buyumus. Anasayfa gercekten bu kadar "
        "sinyal mi okuyor? Degilse daraltmanin anlami kalmaz."
    )


@pytest.mark.parametrize("anahtar", _dashboard_keys())
def test_anahtar_KATALOGDA_var(anahtar: str):
    """Yanlis yazilan bir anahtar hata vermez — sessizce BOS doner."""
    assert anahtar in _katalog_anahtarlari(), (
        f"'{anahtar}' fabrika katalogunda YOK. Anasayfa bu alani sessizce bos "
        "gosterir; backend hata vermez."
    )


def test_anahtarlar_AKTIF_sinyaller():
    """Pasif bir sinyal `/signals/live` yanitinda hic yer almaz."""
    ham = json.loads(KATALOG.read_text(encoding="utf-8"))
    sinyaller = ham if isinstance(ham, list) else ham.get("signals", ham)
    durum = {s["key"]: s.get("is_active", True) for s in sinyaller if s.get("key")}
    for anahtar in _dashboard_keys():
        assert durum.get(anahtar, False), f"'{anahtar}' katalogda pasif"


def test_uc_signal_keys_parametresini_DESTEKLIYOR():
    """Frontend daraltiyor ama backend yok sayarsa kazanc olmaz."""
    import inspect

    from app.api import signals

    imza = inspect.signature(signals.list_live_values)
    assert "signal_keys" in imza.parameters, (
        "/signals/live `signal_keys` parametresini desteklemiyor — frontend "
        "daraltmasi sunucuda yok sayilir"
    )
