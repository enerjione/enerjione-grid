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
    image: str = "hsl/dnp3-gateway:latest"
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
