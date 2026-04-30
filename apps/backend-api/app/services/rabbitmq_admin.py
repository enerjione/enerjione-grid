"""RabbitMQ Management HTTP API uzerinden kullanici/permission yonetimi.

Cati yazilim baska bir manuel adim gerektirmesin diye:
  - Yeni gateway eklendiginde backend buradan otomatik bir user/password yaratir
  - Gateway silindiginde user temizlenir
  - Compose YAML'i indirildiginde bu cred zaten DB'de hazir oldugu icin
    docker compose up -d cagrisi disinda hicbir manuel is yapilmaz

RabbitMQ Management plugin Windows installer'da default olarak ENABLED gelir
(port 15672). Plugin kapaliysa bir kerelik:
    rabbitmq-plugins enable rabbitmq_management

Default cred admin/admin DEGIL — RabbitMQ ilk kurulumda guest/guest yaratir
ve "loopback_users = none" ile uzaktan giris kilitler. Backend management
API'ye localhost'tan erisecegi icin guest/guest ile yeterlidir.

Configuration:
  RABBITMQ_MANAGEMENT_URL    default http://localhost:15672
  RABBITMQ_ADMIN_USERNAME    default guest
  RABBITMQ_ADMIN_PASSWORD    default guest
"""

from __future__ import annotations

import logging
import secrets
import string
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RabbitMqUser:
    """Olusturulan kullanici icin gateway'e dondurulen veri."""

    username: str
    password: str
    vhost: str = "/"


class RabbitMqAdminError(RuntimeError):
    """Management API cagrisi basarisiz oldu — gateway olusturma akisini durdurur."""


def _gen_password(length: int = 32) -> str:
    """URL-safe karakterler — AMQP URL'i icine sorunsuz gomulebilir."""

    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class RabbitMqAdminClient:
    """RabbitMQ Management API icin minik HTTP client.

    Backend bu client'i tek bir yerde (gateway crud akisinda) kullanir;
    daha karmasik queue/binding yonetimi mevcut migration kodu (event_bus.py)
    tarafindan zaten ele alindigi icin kapsami DAR tutuyoruz.
    """

    def __init__(
        self,
        *,
        management_url: str,
        admin_username: str,
        admin_password: str,
        timeout_sec: float = 5.0,
    ) -> None:
        self._base = management_url.rstrip("/")
        self._auth = (admin_username, admin_password)
        self._timeout = timeout_sec

    def _url(self, path: str) -> str:
        return f"{self._base}{path}"

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        try:
            r = requests.request(
                method,
                self._url(path),
                auth=self._auth,
                timeout=self._timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise RabbitMqAdminError(
                f"RabbitMQ Management API erisilemiyor ({method} {path}): {exc}. "
                f"15672 portunda Management plugin'in calistigindan emin olun."
            ) from exc
        if r.status_code >= 400 and r.status_code != 404:
            # 404 silme cagrilarinda anlamli olabilir; caller karar verir
            raise RabbitMqAdminError(
                f"RabbitMQ Management API hata ({method} {path}): "
                f"{r.status_code} {r.text[:200]}"
            )
        return r

    # ----- public api ----------------------------------------------------

    def ping(self) -> bool:
        """Management API erisilebilir mi? Health check icin."""

        try:
            r = self._request("GET", "/api/overview")
        except RabbitMqAdminError:
            return False
        return r.status_code == 200

    def ensure_remote_logins_allowed(self) -> None:
        """`loopback_users` global parametresini "none" yapar.

        RabbitMQ default'unda guest kullanicisi sadece localhost'tan giris
        yapabilir. Docker container'dan gelen baglantilar uzak kabul edildigi
        icin reddedilir. Bu parametre runtime'da set edilebilir, restart
        gerektirmez. Set-once: idempotent.
        """

        # NOTE: Management API "global parameters" endpoint'i eski surumlerde
        # yok; alternatif olarak `rabbitmqctl set_parameter` kullanilabilir
        # ama backend Windows host'ta calistigi icin shell out etmek
        # istemiyoruz. RabbitMQ 3.8+ icin "policies" yerine sadece kullanici
        # bazinda whitelist'i bypass edip yeni dedicated user yaratiyoruz —
        # zaten production icin daha guvenli.
        #
        # Yani: bu metod su an no-op. Gateway icin yaratilan ozel kullanici
        # `loopback_users` listesinde olmadigi icin uzak'tan giris yapar.
        return None

    def create_gateway_user(
        self, *, gateway_code: str, vhost: str = "/", existing_password: str | None = None
    ) -> RabbitMqUser:
        """Gateway icin ayri user yaratir + sadece publish izni verir.

        - username: ``gw-<lowercased-code>``
        - password: rastgele 32 karakter (caller DB'ye kaydeder)
        - permissions: configure="" (queue yaratamaz), write=".*", read=""
          (publish-only). Gateway sadece exchange'e yayinlar; queue tanimi
          backend tarafinda bootstraplenir.

        Idempotent: ayni gateway_code icin tekrar cagrilirsa parolayi sifirlar
        (existing_password verilmemisse). Boylece compose dosyasi yeniden
        indirilirken senkron kalir.
        """

        username = f"gw-{gateway_code.lower()}"
        password = existing_password or _gen_password()
        # 1) user yarat / parola guncelle
        self._request(
            "PUT",
            f"/api/users/{username}",
            json={"password": password, "tags": ""},
        )
        # 2) permission ver — vhost path'inde "/" karakteri icin URL escape
        vhost_enc = "%2F" if vhost == "/" else vhost
        # configure: pika BlockingConnection.publish() oncesi exchange_declare
        # cagiriyor (idempotent passive declare bile "configure" izni ister).
        # Bu yuzden gateway'in yayinlayacagi exchange'i configure etmesine
        # izin veriyoruz — pratikte declare zaten backend tarafinda yapildigi
        # icin bu sadece "exchange_declare(passive=False)" cagrisinin gecmesini
        # saglar. Diger objelere (queue, binding, baska exchange) yetki yok.
        self._request(
            "PUT",
            f"/api/permissions/{vhost_enc}/{username}",
            json={
                "configure": "^hsl\\.events$",  # sadece hsl.events exchange'i
                "write": "^hsl\\.events$",      # sadece bu exchange'e publish
                "read": "",                     # consume yok
            },
        )
        logger.info(
            "rabbitmq_user_provisioned gateway=%s user=%s vhost=%s",
            gateway_code,
            username,
            vhost,
        )
        return RabbitMqUser(username=username, password=password, vhost=vhost)

    def delete_gateway_user(self, *, gateway_code: str) -> None:
        """Gateway silindiginde temizlik. 404 sessizce yutulur."""

        username = f"gw-{gateway_code.lower()}"
        try:
            r = self._request("DELETE", f"/api/users/{username}")
        except RabbitMqAdminError:
            logger.warning("rabbitmq_user_delete_failed gateway=%s", gateway_code)
            return
        if r.status_code == 404:
            return
        logger.info(
            "rabbitmq_user_removed gateway=%s user=%s",
            gateway_code,
            username,
        )


def build_amqp_url(
    *, host: str, user: RabbitMqUser, port: int = 5672
) -> str:
    """Gateway compose'una gomulecek AMQP URL'ini olusturur."""

    from urllib.parse import quote

    # Sifrede ozel karakter olabilir; URL-safe encode
    pw = quote(user.password, safe="")
    vhost = "" if user.vhost == "/" else f"/{quote(user.vhost.lstrip('/'), safe='')}"
    return f"amqp://{user.username}:{pw}@{host}:{port}{vhost or '/'}"
