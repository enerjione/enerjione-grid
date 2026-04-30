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

Gateway (collector) tarafindaki esdegeri:
  ``Horstmann Smart Logger DNP3 Gateway/scripts/render_compose.py``.
  Senkron tutmak gerekirse her iki yerdeki sablonu birlikte degistirin.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse


_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Z0-9_]+)\s*\}\}")


# Asagidaki sablonlar gateway repo'sundaki ``docker/compose.template.yml`` ve
# ``docker/.env.template`` ile birebir eslesmeli — gateway tarafina disardan
# erisim olmadigi icin string sabit olarak gomuldu.
_COMPOSE_TEMPLATE = """\
# Horstmann Smart Logger DNP3 Gateway -- otomatik uretildi (backend)
# Bu dosya tek bir gateway icin docker compose yapilandirmasidir.
#
# Kurulum (Ubuntu / Debian / Docker Engine 24+):
#   docker compose -f hsl-gw-{{GATEWAY_CODE_LOWER}}.yml up -d
#
# Coklu gateway: ayri kod + ayri host-port + ayri YAML dosyasi.
# Image: image build talimati icin: docs/DOCKER.md (gateway repo).

name: hsl-gateway-{{GATEWAY_CODE_LOWER}}

services:
  gateway:
    image: {{IMAGE}}
    container_name: hsl-gw-{{GATEWAY_CODE_LOWER}}
    restart: unless-stopped
    environment:
      GATEWAY_CODE: "{{GATEWAY_CODE}}"
      GATEWAY_TOKEN: "{{GATEWAY_TOKEN}}"
      GATEWAY_NAME: "{{GATEWAY_NAME}}"
      APP_ENVIRONMENT: "{{APP_ENVIRONMENT}}"
      GATEWAY_MODE: "dnp3"
      BACKEND_API_URL: "{{BACKEND_API_URL}}"
      BACKEND_API_VERIFY_SSL: "true"
      RABBITMQ_URL: "{{RABBITMQ_URL}}"
      RABBITMQ_EXCHANGE: "hsl.events"
      RABBITMQ_ROUTING_KEY: "telemetry.raw_received"
      WORKER_HEALTH_HOST: "0.0.0.0"
      WORKER_HEALTH_PORT: "8020"
      DEFAULT_POLL_INTERVAL_SEC: "5"
      MAX_PARALLEL_DEVICES: "50"
      DNP3_LOCAL_ADDRESS: "1"
      DNP3_TCP_PORT: "20000"
      DNP3_RESPONSE_TIMEOUT_SEC: "8"
      DNP3_READ_STRATEGY: "event_driven"
      DNP3_EVENT_BASELINE_INTERVAL_SEC: "60"
      LOG_LEVEL: "INFO"
      LOG_FORMAT: "json"
      SHOW_GATEWAY_TOKEN_ON_START: "false"
    ports:
      - "127.0.0.1:{{HOST_HEALTH_PORT}}:8020"
      # Initiating mode'daki cihazlar buraya outbound TCP baglantisi acar.
      # Backend cihaz basina 20100..20700 araliginda port atar; gateway her
      # initiating cihaz icin ayri TCP server kanali acar (OpenDNP3 kanal-
      # client 1-1 oldugu icin port mecbur). Saha cihazi frontend'deki
      # "Master IP Port" alanini bu portla doldurmali.
      - "20100-20700:20100-20700"
    # Container icinden host'a (cati yazilim/RabbitMQ ayni makinada ise)
    # erisim icin: host.docker.internal -> host-gateway. Linux Docker
    # 20.10+ bu ozel ismi kabul eder, Windows/macOS Docker Desktop'ta
    # zaten gomulu. BACKEND_API_URL "host.docker.internal" yazilirsa
    # gateway DNS'i bu IP'ye cevirir; "localhost"/"127.0.0.1" yanlistir
    # cunku container'in kendisini gosterir.
    #
    # network_mode: "host" YERINE extra_hosts kullanmak istenirse RabbitMQ
    # IPv6'da dinlemiyorsa pika getaddrinfo'da AF_INET6 sonucu donunce
    # "Network is unreachable" alir. Bu yuzden DNS'in IPv4 once cozumlemesini
    # garanti edecegimiz tek yol: container'a host'un IPv4 adresini direkt
    # extra_hosts ile mapping'i ile vermek. Burada "host-gateway" ozel
    # token Docker tarafindan IPv4 host adresine cevrilir.
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - state:/app/.gateway_state
    networks:
      - hsl
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
    name: hsl-gw-{{GATEWAY_CODE_LOWER}}-state

networks:
  hsl:
    name: hsl
    external: false
    # IPv4 zorla. Cati yazilimdaki RabbitMQ/PostgreSQL Windows host'unda
    # genelde sadece IPv4 dinler; container Docker'in default IPv6 prefix'i
    # uzerinden cozmeye calisirsa "Network is unreachable" alir. enable_ipv6
    # kapali oldugu icin host.docker.internal her zaman IPv4'e cevrilir.
    enable_ipv6: false
"""


_ENV_TEMPLATE = """\
# Horstmann Smart Logger DNP3 Gateway -- otomatik uretildi (backend)
# Tek bir gateway icin .env. Gateway'i Docker disinda calistiracaksaniz:
#   python -m dnp3_gateway --env-file ./hsl-gw-{{GATEWAY_CODE_LOWER}}.env

GATEWAY_CODE={{GATEWAY_CODE}}
GATEWAY_TOKEN={{GATEWAY_TOKEN}}
GATEWAY_NAME={{GATEWAY_NAME}}
APP_ENVIRONMENT={{APP_ENVIRONMENT}}

GATEWAY_MODE=dnp3
DNP3_LIBRARY=dnp3py

BACKEND_API_URL={{BACKEND_API_URL}}
BACKEND_API_VERIFY_SSL=true
CONFIG_REFRESH_SEC=30

RABBITMQ_URL={{RABBITMQ_URL}}
RABBITMQ_EXCHANGE=hsl.events
RABBITMQ_ROUTING_KEY=telemetry.raw_received

WORKER_HEALTH_HOST=0.0.0.0
WORKER_HEALTH_PORT=8020

DEFAULT_POLL_INTERVAL_SEC=5
MAX_PARALLEL_DEVICES=50

DNP3_LOCAL_ADDRESS=1
DNP3_TCP_PORT=20000
DNP3_RESPONSE_TIMEOUT_SEC=8
DNP3_READ_STRATEGY=event_driven
DNP3_EVENT_BASELINE_INTERVAL_SEC=60

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
    rabbitmq_url: str
    host_port: int = 8020
    image: str = "ghcr.io/fikretsafak/horstmann-dnp3-gateway:latest"
    app_environment: Literal["development", "staging", "production"] = "production"


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
    if not args.rabbitmq_url.strip():
        raise ComposeRenderError("rabbitmq_url bos olamaz")


def _replacements(args: ComposeRenderInput) -> dict[str, str]:
    return {
        "GATEWAY_CODE": args.code,
        "GATEWAY_CODE_LOWER": args.code.lower(),
        "GATEWAY_TOKEN": args.token,
        "GATEWAY_NAME": args.name,
        "BACKEND_API_URL": args.backend_url.rstrip("/"),
        "RABBITMQ_URL": args.rabbitmq_url,
        "HOST_HEALTH_PORT": str(args.host_port),
        "IMAGE": args.image,
        "APP_ENVIRONMENT": args.app_environment,
    }


def _apply_template(template: str, replacements: dict[str, str]) -> str:
    def _sub(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in replacements:
            raise ComposeRenderError(f"Sablonda doldurulmamis yer tutucu: {{{{ {key} }}}}")
        return replacements[key]

    return _PLACEHOLDER_RE.sub(_sub, template)


def render_compose(args: ComposeRenderInput) -> str:
    _validate(args)
    return _apply_template(_COMPOSE_TEMPLATE, _replacements(args))


def render_env(args: ComposeRenderInput) -> str:
    _validate(args)
    return _apply_template(_ENV_TEMPLATE, _replacements(args))


def filename_for(args: ComposeRenderInput, *, kind: Literal["compose", "env"]) -> str:
    suffix = "yml" if kind == "compose" else "env"
    return f"hsl-gw-{args.code.lower()}.{suffix}"


_LOCALHOST_NAMES = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _container_host(host: str) -> str:
    """Container icinden host makinaye erismek icin DNS adi.

    Kullanici frontend'de "localhost" veya "127.0.0.1" yazarsa bu container
    icinde container'in kendisini gosterir, host makinaya degil. Docker
    Desktop (Windows/macOS) ve Linux Docker 20.10+ ``host.docker.internal``
    ozel ismini destekler; compose template'inde ``extra_hosts: host-gateway``
    ile bu Linux'ta da garantiye alindi.
    """

    h = (host or "").strip().lower()
    if h in _LOCALHOST_NAMES:
        return "host.docker.internal"
    return host


def normalize_backend_url_for_container(backend_url: str) -> str:
    """backend_url'deki localhost/127.0.0.1'i host.docker.internal'a cevirir.

    Saha kurulumunda kullanici cati yazilim ile ayni makinada gateway
    calistiriyorsa (en yaygin senaryo) frontend "localhost" girer. Compose
    icindeki gateway container'i bu ismi yanlis cozumler — bu yuzden URL'i
    yazmadan once duzeltiyoruz.
    """

    parsed = urlparse(backend_url.strip())
    if not parsed.hostname:
        return backend_url
    new_host = _container_host(parsed.hostname)
    if new_host == parsed.hostname:
        return backend_url
    # urlunparse ile yeniden olustur (port + path + scheme korunur)
    netloc = new_host
    if parsed.port:
        netloc = f"{new_host}:{parsed.port}"
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        netloc = f"{userinfo}@{netloc}"
    return parsed._replace(netloc=netloc).geturl()


def derive_rabbitmq_url(backend_url: str) -> str:
    """backend_url'in host kismindan varsayilan AMQP URL'i turetir.

    Carı yazilim kurulumunda RabbitMQ varsayilan olarak ayni host'ta 5672
    portunda calisiyor; kullanicinin frontend'de ayrica RabbitMQ adresi
    girmesini onlemek icin backend host'u broker host'u olarak kullaniriz.
    "localhost"/"127.0.0.1" yazilmissa container icinden erisim icin
    host.docker.internal'a cevrilir. Kullanici farkli bir broker isterse
    endpoint'e ``rabbitmq_url`` parametresi gecerek bu davranisi override
    edebilir.
    """

    parsed = urlparse(backend_url.strip())
    host = _container_host(parsed.hostname or "127.0.0.1")
    # Cati yazilimdaki diger servisler (alarm-service, notification-worker,
    # tag-engine, backend-api) ayni broker'a "guest:guest" ile baglaniyor;
    # gateway de tutarli olsun. Ozel kullanici isteniyorsa frontend
    # rabbitmq_url query param'i ile override edebilir.
    return f"amqp://guest:guest@{host}:5672/"
