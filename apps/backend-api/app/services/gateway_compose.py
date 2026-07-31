"""Per-gateway docker compose / .env renderlayici.

Frontend "Yeni gateway ekle" akisinda kullanici hazir bir compose dosyasi
indirir. Backend bu modulu kullanarak compose YAML'ini uretir; token + kod
veritabaninda olusturulup buradan cekilir.

Tasarim:
  - Sablon string sabit. Yer tutucu format: ``{{KEY}}``.
  - Saha kotuksanir / dogrulanir; eksik degerler hata firlatir (Pydantic FastAPI
    katmaninda yakalar).
  - Iki cikti: ``compose`` (docker-compose.yml) ve ``env`` (host'ta python ile
    dogrudan calistirma icin .env). Frontend ihtiyaca gore ikisinden birini ister.

Initiating mode port araligi:
  - Sadece kullanicinin belirttigi sayida port acilir (default 50). 600 port
    publish etmek Docker iptables'i siserir; cogu kurulumda 0-10 initiating
    cihaz vardir, 50 buffer yeterli.
  - Coklu gateway icin her gateway'e benzersiz blok atanir (20100, 21100, ...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse


_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Z0-9_]+)\s*\}\}")


# Sablon: gateway repo'sundaki ``docker/compose.template.yml`` ile birebir
# eslesmeli — gateway tarafina disardan erisim olmadigi icin string sabit.
_COMPOSE_TEMPLATE = """\
# EnerjiOne DNP3 Gateway — {{GATEWAY_CODE}}
# Kurulum: docker compose -f e1-gw-{{GATEWAY_CODE_LOWER}}.yml up -d

name: e1-gateway-{{GATEWAY_CODE_LOWER}}

services:
  gateway:
    image: {{IMAGE}}
    container_name: e1-gw-{{GATEWAY_CODE_LOWER}}
    restart: unless-stopped
    environment:
      GATEWAY_CODE: "{{GATEWAY_CODE}}"
      GATEWAY_TOKEN: "{{GATEWAY_TOKEN}}"
      GATEWAY_NAME: "{{GATEWAY_NAME}}"
      APP_ENVIRONMENT: "{{APP_ENVIRONMENT}}"
      GATEWAY_MODE: "dnp3"
      BACKEND_API_URL: "{{BACKEND_API_URL}}"
      BACKEND_API_VERIFY_SSL: "true"
      # Public IP + HTTP icin gateway production guard'i 'https' bekler.
      # TLS henuz kurulu degilse (Caddy/Traefik/Cloudflare yok) bu flag ile
      # bilincli opt-out — gateway boot'ta WARN log atar, calismaya devam eder.
      # Kullanici TLS terminator kurunca BACKEND_API_URL'i https:// yapip
      # bu flag'i 'false' yapabilir.
      GATEWAY_INSECURE_ALLOW_PLAINTEXT: "true"
      # NATS JetStream — gateway'in telemetri yayin yolu (RabbitMQ kaldirildi).
      NATS_URL: "{{NATS_URL}}"
      NATS_SUBJECT_PREFIX: "e1.telemetry.raw"
      WORKER_HEALTH_HOST: "0.0.0.0"
      WORKER_HEALTH_PORT: "8020"
      DEFAULT_POLL_INTERVAL_SEC: "1"
      MAX_PARALLEL_DEVICES: "100"
      DNP3_LOCAL_ADDRESS: "1"
      DNP3_TCP_PORT: "20000"
      DNP3_RESPONSE_TIMEOUT_SEC: "5"
      DNP3_READ_STRATEGY: "event_driven"
      DNP3_EVENT_BASELINE_INTERVAL_SEC: "30"
      LOG_LEVEL: "INFO"
      LOG_FORMAT: "json"
      SHOW_GATEWAY_TOKEN_ON_START: "false"
    ports:
      - "127.0.0.1:{{HOST_HEALTH_PORT}}:8020"
{{INITIATING_PORTS_BLOCK}}
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - state:/app/.gateway_state
    networks:
      - e1
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8020/health',timeout=3).status==200 else sys.exit(1)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s

volumes:
  state:
    name: e1-gw-{{GATEWAY_CODE_LOWER}}-state

networks:
  e1:
    name: e1
    external: false
    enable_ipv6: false
"""


_ENV_TEMPLATE = """\
# EnerjiOne DNP3 Gateway — {{GATEWAY_CODE}}
# Docker disinda calistirma: python -m dnp3_gateway --env-file ./e1-gw-{{GATEWAY_CODE_LOWER}}.env

GATEWAY_CODE={{GATEWAY_CODE}}
GATEWAY_TOKEN={{GATEWAY_TOKEN}}
GATEWAY_NAME={{GATEWAY_NAME}}
APP_ENVIRONMENT={{APP_ENVIRONMENT}}

GATEWAY_MODE=dnp3
DNP3_LIBRARY=dnp3py

BACKEND_API_URL={{BACKEND_API_URL}}
BACKEND_API_VERIFY_SSL=true
# Public IP + HTTP icin gateway production guard'i 'https' bekler. TLS
# yokken bu flag ile bilincli opt-out — boot'ta WARN log atilir.
GATEWAY_INSECURE_ALLOW_PLAINTEXT=true
CONFIG_REFRESH_SEC=30

# NATS JetStream — gateway'in telemetri yayin yolu (RabbitMQ kaldirildi).
NATS_URL={{NATS_URL}}
NATS_SUBJECT_PREFIX=e1.telemetry.raw

WORKER_HEALTH_HOST=0.0.0.0
WORKER_HEALTH_PORT=8020

DEFAULT_POLL_INTERVAL_SEC=1
MAX_PARALLEL_DEVICES=100

DNP3_LOCAL_ADDRESS=1
DNP3_TCP_PORT=20000
DNP3_RESPONSE_TIMEOUT_SEC=5
DNP3_READ_STRATEGY=event_driven
DNP3_EVENT_BASELINE_INTERVAL_SEC=30

LOG_LEVEL=INFO
LOG_FORMAT=json
SHOW_GATEWAY_TOKEN_ON_START=false
"""


_CODE_REGEX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")


@dataclass(frozen=True)
class ComposeRenderInput:
    code: str
    token: str
    name: str
    backend_url: str
    # NATS JetStream URL — gateway'in tek telemetri yayinlama yolu. Eski
    # `rabbitmq_url` parametresi kaldirildi (gateway artik RabbitMQ kullanmiyor).
    nats_url: str
    host_port: int = 8020
    image: str = "ghcr.io/enerjione/enerjione-grid-dnp3-gateway:latest"
    app_environment: Literal["development", "staging", "production"] = "production"
    # Initiating cihaz portu icin host tarafi baslangic (multi-gateway port
    # catismasi onleme — her gateway'e benzersiz blok 20100, 21100, ...).
    initiating_port_base: int = 20100
    # Acilacak port sayisi = max initiating cihaz. 0 ise hic port acilmaz
    # (sadece listening cihazlar; default kurulumun bu olmasi beklenir cunku
    # gateway cihaza TCP client olarak baglanir, dinleme portu gerekmez).
    initiating_port_count: int = 0


class ComposeRenderError(ValueError):
    pass


def _validate(args: ComposeRenderInput) -> None:
    if not _CODE_REGEX.match(args.code):
        raise ComposeRenderError(
            f"GATEWAY_CODE gecersiz: {args.code!r} "
            "(alfanumerik, '-' veya '_', 2-64 karakter, harf/rakamla baslar)"
        )
    if len(args.token) < 16:
        raise ComposeRenderError("GATEWAY_TOKEN cok kisa (>=16 karakter olmali)")
    if not 1 <= args.host_port <= 65535:
        raise ComposeRenderError(f"host_port aralik disi: {args.host_port}")
    if not args.backend_url.strip():
        raise ComposeRenderError("backend_url bos olamaz")
    if not args.nats_url.strip():
        raise ComposeRenderError("nats_url bos olamaz")
    if args.initiating_port_count < 0 or args.initiating_port_count > 1000:
        raise ComposeRenderError(
            f"initiating_port_count gecersiz: {args.initiating_port_count} (0-1000)"
        )
    if args.initiating_port_count > 0:
        last_port = args.initiating_port_base + args.initiating_port_count - 1
        if not (1024 <= args.initiating_port_base <= 65000) or last_port > 65535:
            raise ComposeRenderError(
                f"initiating_port_base aralik disi: base={args.initiating_port_base} "
                f"count={args.initiating_port_count} last={last_port}"
            )


def _build_initiating_ports_block(args: ComposeRenderInput) -> str:
    """Compose'un ports: bloguna eklenecek initiating port satiri.

    count=0 ise tamamen bos doner — sadece listening cihazlar olan gateway'ler
    Docker'da tek port (health) ile calisir, iptables temiz kalir.

    count>0 ise tek satir: "20100-20149:20100-20149" formatinda.
    Sol (host) gateway-spesifik blok; sag (container) hep 20100'den baslar
    (gateway kodu ic portta sabit). Boylece her container ayni binary, sadece
    publish edilen portlar farkli.
    """
    if args.initiating_port_count <= 0:
        return ""
    base = args.initiating_port_base
    last = base + args.initiating_port_count - 1
    container_last = 20100 + args.initiating_port_count - 1
    return f'      - "{base}-{last}:20100-{container_last}"'


def _replacements(args: ComposeRenderInput) -> dict[str, str]:
    return {
        "GATEWAY_CODE": args.code,
        "GATEWAY_CODE_LOWER": args.code.lower(),
        "GATEWAY_TOKEN": args.token,
        "GATEWAY_NAME": args.name,
        "BACKEND_API_URL": args.backend_url.rstrip("/"),
        "NATS_URL": args.nats_url,
        "HOST_HEALTH_PORT": str(args.host_port),
        "IMAGE": args.image,
        "APP_ENVIRONMENT": args.app_environment,
        "INITIATING_PORTS_BLOCK": _build_initiating_ports_block(args),
    }


def _apply_template(template: str, replacements: dict[str, str]) -> str:
    def _sub(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in replacements:
            raise ComposeRenderError(f"sablonda bilinmeyen yer tutucu: {{{{{key}}}}}")
        return replacements[key]

    return _PLACEHOLDER_RE.sub(_sub, template)


def validate_render_input(args: ComposeRenderInput) -> None:
    """Parametreleri dogrula, compose URETMEDEN.

    "Bu cihaza kur" akisi compose'u host ajanina uretiyor (guvenlik: ajan
    disardan YAML kabul etmiyor, bkz. infra/appliance/e1-gwd.py). Ama
    kullaniciya HEMEN 400 donebilmek icin ayni dogrulamayi burada da
    kosturuyoruz — hatanin asenkron status.json'da gorunmesini beklemesin.
    """
    _validate(args)


def render_compose(args: ComposeRenderInput) -> str:
    _validate(args)
    return _apply_template(_COMPOSE_TEMPLATE, _replacements(args))


def render_env(args: ComposeRenderInput) -> str:
    _validate(args)
    return _apply_template(_ENV_TEMPLATE, _replacements(args))


def filename_for(args: ComposeRenderInput, *, kind: Literal["compose", "env"]) -> str:
    """Indirilecek dosyanin adi: e1-gw-<code>.yml veya e1-gw-<code>.env."""
    suffix = "yml" if kind == "compose" else "env"
    return f"e1-gw-{args.code.lower()}.{suffix}"


def normalize_backend_url_for_container(url: str) -> str:
    """localhost / 127.0.0.1 -> host.docker.internal cevirimi.

    Container icinden host'a erisim ihtiyaci. Kullanici frontend'de
    "http://localhost:8000" yazsa bile compose dosyasi icinde
    "http://host.docker.internal:8000" olarak yazilir; aksi halde container
    kendisini gosterir.
    """
    parsed = urlparse(url.strip())
    if parsed.hostname in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        netloc = "host.docker.internal"
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
        return parsed._replace(netloc=netloc).geturl()
    return url.strip()


def derive_rabbitmq_url(backend_url: str) -> str:
    """LEGACY — geriye uyumluluk icin. Gateway artik RabbitMQ kullanmiyor."""
    parsed = urlparse(backend_url.strip())
    host = parsed.hostname or "localhost"
    return f"amqp://hsl:hsl@{host}:5672/"


def derive_nats_url(backend_url: str, *, gateway_password: str = "") -> str:
    """Backend URL'inden ayni hostname uzerinde NATS URL turetir.

    Gateway compose dosyasi indirilirken default fallback olarak kullanilir.
    Production'da kullanici endpoint'e ?nats_url= ile explicit override
    gonderebilir; aksi halde backend ile ayni host'ta NATS oldugu varsayilir.

    NATS auth: `infra/nats/nats-server.conf`'ta `gateway` user var ve sadece
    `e1.telemetry.raw.>` publish edebilir. `gateway_password` Settings'tan
    okunur; URL'e gomulerek gateway compose indirilirken otomatik auth saglanir.
    Bos password ile uretilen URL anonimdir → NATS deny-all ile reddeder.
    """
    parsed = urlparse(backend_url.strip())
    host = parsed.hostname or "localhost"
    if gateway_password.strip():
        return f"nats://gateway:{gateway_password.strip()}@{host}:4222"
    # Anonim fallback — production'da reddedilir; sadece dev/test icin.
    return f"nats://{host}:4222"
