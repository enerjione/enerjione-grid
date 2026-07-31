"""Ayni ayarin birden fazla dosyada tanimlandigi yerlerde TUTARLILIK.

Neden test: `ACCESS_TOKEN_MINUTES` DORT yerde tanimliydi ve DORDU DE farkliydi:

    config.py               1440   (24 saat — gerekcesi yazili varsayilan)
    docker-compose.yml     43200   (30 gun  — sahada gecerli olan buydu)
    .env.example           43200   (30 gun)
    backend/.env.example      30   (yarim saat)

Sonuc: "beni hatirla" kutucugu islevsizdi — isaretlemeyen kullanici da 30
gunluk token aliyordu, cunku compose taban TTL'i remember-me TTL'ine esitlemis.
Bunlar birbirine bakmadan degistirilebilen dosyalar; senkronu insan
disiplinine birakmak yerine kilitliyoruz.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "apps" / "backend-api"


def _read(p: Path) -> str:
    assert p.is_file(), f"beklenen dosya yok: {p}"
    return p.read_text(encoding="utf-8")


def _compose_default(var: str) -> int | None:
    """docker-compose.yml icindeki `VAR: ${VAR:-DEGER}` varsayilanini okur."""
    text = _read(REPO / "docker-compose.yml")
    m = re.search(rf"^\s*{var}:\s*\$\{{{var}:-(\d+)\}}", text, re.MULTILINE)
    return int(m.group(1)) if m else None


def _env_example_value(path: Path, var: str) -> int | None:
    m = re.search(rf"^{var}=(\d+)\s*$", _read(path), re.MULTILINE)
    return int(m.group(1)) if m else None


def _settings_default(field: str) -> int:
    """config.py'deki alan varsayilanini KAYNAKTAN okur.

    Settings() ornegi olusturmuyoruz: ortam degiskenleri (CI'da veya
    gelistiricinin kabuğunda set olabilir) varsayilani ezip testi yaniltirdi.
    """
    text = _read(BACKEND / "app" / "core" / "config.py")
    m = re.search(rf"^\s*{field}:\s*int\s*=\s*([\d_]+)", text, re.MULTILINE)
    assert m, f"config.py icinde {field} varsayilani bulunamadi"
    return int(m.group(1).replace("_", ""))


ACCESS = "access_token_minutes"
REMEMBER = "remember_me_token_minutes"


def test_access_token_minutes_is_same_everywhere():
    expected = _settings_default(ACCESS)
    sources = {
        "docker-compose.yml": _compose_default("ACCESS_TOKEN_MINUTES"),
        ".env.example": _env_example_value(REPO / ".env.example", "ACCESS_TOKEN_MINUTES"),
        "backend/.env.example": _env_example_value(
            BACKEND / ".env.example", "ACCESS_TOKEN_MINUTES"
        ),
    }
    mismatched = {k: v for k, v in sources.items() if v is not None and v != expected}
    assert not mismatched, (
        f"ACCESS_TOKEN_MINUTES config.py'de {expected} ama su dosyalarda farkli: "
        f"{mismatched}. Dordunu birlikte guncelleyin."
    )


def test_base_token_ttl_is_shorter_than_remember_me():
    """Taban TTL, 'beni hatirla' TTL'ine ESIT veya ondan UZUN olmamali.

    Esitlerse kutucuk hicbir sey degistirmez; uzun olursa kutucuk isaretlemek
    oturumu KISALTIR. Ikisi de tasarimin tersi.
    """
    base = _settings_default(ACCESS)
    remember = _settings_default(REMEMBER)
    assert base < remember, (
        f"access_token_minutes ({base}) remember_me_token_minutes ({remember}) "
        "degerinden KISA olmali; aksi halde 'beni hatirla' anlamsizlasir."
    )


def test_compose_does_not_equal_remember_me_ttl():
    """Compose'un taban TTL'i remember-me TTL'ine esitlenmemeli.

    Yasanan hata tam olarak buydu: compose 43200 veriyordu ve bu
    remember_me_token_minutes ile ayniydi.
    """
    compose = _compose_default("ACCESS_TOKEN_MINUTES")
    if compose is None:
        pytest.skip("compose'da ACCESS_TOKEN_MINUTES varsayilani yok")
    assert compose != _settings_default(REMEMBER), (
        "docker-compose.yml taban oturum TTL'ini 'beni hatirla' TTL'ine "
        "esitlemis — kutucugu isaretlemeyen kullanici da uzun token alir."
    )


def test_access_token_ttl_is_sane():
    """Saha kullanimi icin makul aralik: 15 dakika - 7 gun."""
    value = _settings_default(ACCESS)
    assert 15 <= value <= 10_080, f"access_token_minutes akil disi: {value}"


@pytest.mark.parametrize(
    "path",
    [REPO / ".env.example", BACKEND / ".env.example"],
    ids=["root", "backend"],
)
def test_env_examples_declare_access_token(path: Path):
    """Ornek dosyalar ayari GOSTERMELI — operator degeri gorebilmeli."""
    assert _env_example_value(path, "ACCESS_TOKEN_MINUTES") is not None, (
        f"{path.name} icinde ACCESS_TOKEN_MINUTES yok"
    )
