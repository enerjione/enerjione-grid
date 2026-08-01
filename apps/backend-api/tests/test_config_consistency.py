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


# --------------------------------------------------------------------------
# Retention / TTL ayarlari
#
# Ayni sinif hata bu ayarlarda daha pahaliya patlar: retention degerleri
# UCUNU birden guncellemeyi unutmak "disk dolana kadar fark edilmeyen" bir
# sapma yaratir. Uc dosyayi birbirine kilitliyoruz.
# --------------------------------------------------------------------------

RETENTION_FIELDS = [
    ("telemetry_retention_minutes", "TELEMETRY_RETENTION_MINUTES"),
    ("telemetry_retention_interval_sec", "TELEMETRY_RETENTION_INTERVAL_SEC"),
    ("processed_messages_retention_hours", "PROCESSED_MESSAGES_RETENTION_HOURS"),
    ("processed_messages_interval_sec", "PROCESSED_MESSAGES_INTERVAL_SEC"),
    ("system_events_retention_days", "SYSTEM_EVENTS_RETENTION_DAYS"),
    ("system_events_interval_sec", "SYSTEM_EVENTS_INTERVAL_SEC"),
    ("system_events_max_rows", "SYSTEM_EVENTS_MAX_ROWS"),
    ("retention_delete_batch", "RETENTION_DELETE_BATCH"),
    ("retention_max_batches_per_run", "RETENTION_MAX_BATCHES_PER_RUN"),
    ("disk_guard_reserve_percent", "DISK_GUARD_RESERVE_PERCENT"),
    ("disk_guard_reserve_min_gb", "DISK_GUARD_RESERVE_MIN_GB"),
    ("disk_guard_interval_sec", "DISK_GUARD_INTERVAL_SEC"),
    ("disk_guard_emergency_backup_keep", "DISK_GUARD_EMERGENCY_BACKUP_KEEP"),
]


@pytest.mark.parametrize("field,env", RETENTION_FIELDS, ids=[f[1] for f in RETENTION_FIELDS])
def test_retention_defaults_match_everywhere(field: str, env: str):
    expected = _settings_default(field)
    sources = {
        "docker-compose.yml": _compose_default(env),
        ".env.example": _env_example_value(REPO / ".env.example", env),
    }
    mismatched = {k: v for k, v in sources.items() if v is not None and v != expected}
    assert not mismatched, (
        f"{env} config.py'de {expected} ama su dosyalarda farkli: {mismatched}. "
        "Ucunu birlikte guncelleyin."
    )


@pytest.mark.parametrize("field,env", RETENTION_FIELDS, ids=[f[1] for f in RETENTION_FIELDS])
def test_retention_settings_are_declared_in_examples(field: str, env: str):
    """Operator ayarin VARLIGINDAN haberdar olmali.

    Bu testin somut gerekcesi var: PROCESSED_MESSAGES_* ayarlari kodda
    `os.getenv` ile okunuyordu ama ne config.py'de ne ornek dosyalarda vardi.
    Sonuc: 7 gunluk varsayilan kimsenin gormedigi bir sekilde tabloyu
    ~180M satira cikariyordu.
    """
    assert _env_example_value(REPO / ".env.example", env) is not None, (
        f".env.example icinde {env} yok — operator bu ayari goremez"
    )
    assert _compose_default(env) is not None, (
        f"docker-compose.yml icinde {env} varsayilani yok"
    )


def test_processed_messages_window_exceeds_redelivery_window():
    """Idempotency penceresi NATS redelivery penceresinden buyuk olmali.

    Gercek pencere = ack_wait(60sn) x nats_worker_max_deliver. Bu degerin
    altina dusurmek duplicate telemetri yazilmasina yol acar.
    """
    max_deliver = _settings_default("nats_worker_max_deliver")
    window_sec = _settings_default("processed_messages_retention_hours") * 3600
    assert window_sec > 60 * max_deliver, (
        f"processed_messages penceresi ({window_sec}sn) redelivery penceresinden "
        f"({60 * max_deliver}sn) kisa — duplicate riski."
    )


NATS_BYTE_FIELDS = [
    ("nats_stream_raw_max_bytes", "NATS_STREAM_RAW_MAX_BYTES"),
    ("nats_stream_normalized_max_bytes", "NATS_STREAM_NORMALIZED_MAX_BYTES"),
    ("nats_stream_dlq_max_bytes", "NATS_STREAM_DLQ_MAX_BYTES"),
]


@pytest.mark.parametrize("field,env", NATS_BYTE_FIELDS, ids=[f[1] for f in NATS_BYTE_FIELDS])
def test_nats_stream_byte_caps_match_everywhere(field: str, env: str):
    expected = _settings_default(field)
    sources = {
        "docker-compose.yml": _compose_default(env),
        ".env.example": _env_example_value(REPO / ".env.example", env),
    }
    mismatched = {k: v for k, v in sources.items() if v is not None and v != expected}
    assert not mismatched, f"{env} config.py'de {expected} ama farkli: {mismatched}"


def test_nats_stream_caps_are_positive():
    """max_bytes=0 SINIRSIZ demektir — disk tavani ortadan kalkar.

    Bu testin gerekcesi: streamler bir zamanlar max_bytes=0 ile
    olusturuluyordu ve tek fren hesap seviyesindeki max_file_store idi; o da
    budama yapmaz, dolunca publish REDDEDILIR ve telemetri akisi durur.
    """
    for field, env in NATS_BYTE_FIELDS:
        value = _settings_default(field)
        assert value > 0, f"{env} sifir — stream disk tavani yok demektir"


def test_nats_stream_caps_stay_under_account_limit():
    """Stream tavanlari toplami, nats-server.conf hesap tavaninin ALTINDA olmali.

    Ustune cikarsa once HESAP tavani carpar ve tam da kacinmak istedigimiz
    davranis geri gelir: publish reddi = telemetri akisinin durmasi.
    """
    # .template okunur, `nats-server.conf` DEGIL: ikincisi .gitignore'da
    # (sifre hash'leri iceriyor) ve temiz bir klonda / CI'da HIC bulunmaz.
    # Takip edilen tek kaynak template'tir.
    conf = _read(REPO / "infra" / "nats" / "nats-server.conf.template")
    m = re.search(r"^\s*max_file_store:\s*(\d+)\s*GB", conf, re.MULTILINE)
    assert m, "nats-server.conf.template icinde max_file_store bulunamadi"
    account_bytes = int(m.group(1)) * 1000**3  # NATS 'GB' = 10^9
    total = sum(_settings_default(f) for f, _e in NATS_BYTE_FIELDS)
    assert total < account_bytes, (
        f"stream tavanlari toplami ({total}) hesap tavanini ({account_bytes}) "
        "asiyor — hesap tavani once carpar ve publish reddedilir"
    )


def test_disk_guard_reserve_is_sane():
    """Rezerv yuzdesi makul aralikta olmali.

    Cok dusuk (%0-2): guard cok gec devreye girer, mudahale icin alan kalmaz.
    Cok yuksek (%30+): diskin ucte biri bosa yatirilir.
    """
    pct = _settings_default("disk_guard_reserve_percent")
    assert 5 <= pct <= 25, f"disk_guard_reserve_percent akil disi: %{pct}"
    floor = _settings_default("disk_guard_reserve_min_gb")
    assert 1 <= floor <= 50, f"disk_guard_reserve_min_gb akil disi: {floor} GB"


def test_retention_batch_settings_are_sane():
    """Batch'li silmenin anlamli olmasi icin makul araliklar.

    batch cok kucukse purge periyoda yetismez, cok buyukse batch'li silmenin
    amaci (kisa lock, dusuk WAL tepe noktasi) ortadan kalkar.
    """
    batch = _settings_default("retention_delete_batch")
    cap = _settings_default("retention_max_batches_per_run")
    assert 1_000 <= batch <= 100_000, f"retention_delete_batch akil disi: {batch}"
    assert 1 <= cap <= 1_000, f"retention_max_batches_per_run akil disi: {cap}"


# ---------------------------------------------------------------------------
# NATS TLS — CA dizini, NATS'a BAGLANAN her servise mount edilmeli
#
# `NATS_CA_FILE` ISTEMCI tarafinda okunur. Dizin yalnizca `nats` servisine
# baglanirsa, .env'e `NATS_CA_FILE=/etc/nats/certs/ca.crt` yazan operator
# (compose yorumlarinin ONERDIGI deger budur) istemci container'inda OLMAYAN
# bir yolu gostermis olur; TLS el sikismasi CA dogrulamasinda duser.
#
# Bu kontrol once kabuk testinde satir satir yapiliyordu ve KIRILGANDI: compose
# CRLF satir sonlari kullandigi icin awk blok siniri kaciriyor, mount tamamen
# silindigi halde test GECIYORDU (mutasyon testi yakaladi). Burada gercek YAML
# ayristiricisi kullaniliyor — anchor'lar (`*nats-certs`) da kendiliginden
# cozuluyor, yani "anchor mi acik yol mu" ayrimi ortadan kalkiyor.
# ---------------------------------------------------------------------------

NATS_CERT_DIZINI = "./infra/nats/certs"

# NATS'a baglanan servisler. `nats`in kendisi haric: sunucu sertifikayi
# kendi mount'undan okur.
NATS_ISTEMCILERI = [
    "backend-api",
    "tag-engine",
    "alarm-service",
    "iec104-outbound",
    "modbus-outbound",
]


def _compose() -> dict:
    import yaml

    yol = REPO / "docker-compose.yml"
    return yaml.safe_load(yol.read_text(encoding="utf-8"))


@pytest.mark.parametrize("servis", NATS_ISTEMCILERI)
def test_nats_istemcilerine_ca_dizini_mount_edilmis(servis: str):
    compose = _compose()
    tanim = compose["services"].get(servis)
    assert tanim is not None, f"{servis} compose'da yok"

    volumes = tanim.get("volumes") or []
    kaynaklar = [str(v).split(":", 1)[0] for v in volumes if isinstance(v, str)]

    assert NATS_CERT_DIZINI in kaynaklar, (
        f"{servis}: NATS CA dizini mount edilmemis. NATS_CA_FILE container "
        f"icinde bulunamaz, TLS el sikismasi CA dogrulamasinda duser. "
        f"Mevcut mount kaynaklari: {kaynaklar}"
    )


@pytest.mark.parametrize("servis", NATS_ISTEMCILERI)
def test_ca_mount_SALT_OKUNUR(servis: str):
    """Istemcinin CA'yi degistirmesi icin bir sebep yok; yazilabilir mount
    gereksiz bir saldiri yuzeyi."""
    compose = _compose()
    volumes = compose["services"][servis].get("volumes") or []
    for v in volumes:
        if isinstance(v, str) and v.split(":", 1)[0] == NATS_CERT_DIZINI:
            assert v.rstrip().endswith(":ro"), f"{servis}: CA mount'u salt okunur degil ({v})"
            return
    pytest.fail(f"{servis}: CA mount'u bulunamadi")


def test_nats_sunucusu_da_sertifikayi_goruyor():
    compose = _compose()
    volumes = compose["services"]["nats"].get("volumes") or []
    kaynaklar = [str(v).split(":", 1)[0] for v in volumes if isinstance(v, str)]
    assert NATS_CERT_DIZINI in kaynaklar, (
        "nats sunucusu sertifika dizinini gormuyor — TLS acildiginda ayaga kalkmaz"
    )
