"""IEC 104 kontrol komutlarini backend'e ileten istemci.

NEDEN BACKEND UZERINDEN
-----------------------
Bu servisin DB baglantisi yok ve olmamali. Komut, arayuzden gelen komutla
AYNI allowlist'ten, AYNI gateway kontrolunden ve AYNI audit kaydindan
gecmeli; bunlarin hepsi backend'de. Burada yalnizca protokol tarafi var.

AKIS VE ZAMANLAMA — ACT_CON ile ACT_TERM'in ANLAMI FARKLI
----------------------------------------------------------
Cihaz NAT arkasindaki bir gateway'in ardinda ve komut ona config-poll ile
gidiyor (~30 sn). Yani "komut kabul edildi" ile "komut cihazda gerceklesti"
arasinda dakikalar olabilir. IEC 60870-5-101 §7.3.2 bunu zaten ayirir:

    ACT_CON  (COT=7)  -> komut KABUL EDILDI (kuyruga alindi)
    ACT_TERM (COT=10) -> komut TAMAMLANDI  (cihaz sonucu bildirdi)

Ikisini birlestirip ACT_CON'u sonuc yerine kullanmak, SCADA operatorune
"reset yapildi" demek olurdu — oysa yalnizca kuyruga alinmistir. Bu, bu
depoda kapatilan "yesil yalan" sinifinin ta kendisi.

Sonuc beklemenin bir siniri var: `COMMAND_RESULT_TIMEOUT_SEC` icinde sonuc
gelmezse NEGATIF ACT_TERM gonderilir. Sessiz kalmak master'i suresiz
bekletirdi.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)

#: Komut sonucunu beklerken toplam ust sinir (saniye). Gateway config-poll'u
#: ~30 sn oldugu icin tek bir poll turunu kacirmaya tolerans birakiyor.
COMMAND_RESULT_TIMEOUT_SEC = 120.0
#: Sonuc yoklama araligi (saniye).
COMMAND_RESULT_POLL_SEC = 3.0

#: Cihazin sonucu bildirdigi son durumlar.
TERMINAL_OK = frozenset({"ok"})
TERMINAL_FAIL = frozenset({"failed", "expired"})


@dataclass(frozen=True)
class CommandAccepted:
    id: int
    status: str


class CommandRejected(Exception):
    """Backend komutu REDDETTI. `reason` negatif onayin gerekcesi."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class CommandClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        timeout_sec: int = 8,
        result_timeout_sec: float = COMMAND_RESULT_TIMEOUT_SEC,
        result_poll_sec: float = COMMAND_RESULT_POLL_SEC,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        # Sonuc bekleme zamanlamasi ISTEMCIDE tutuluyor, modul sabiti olarak
        # DEGIL: server bunlari cagri aninda okur, boylece testler gercek
        # zamanlamayi beklemek zorunda kalmaz (aksi halde tek dosya 52 saniye
        # suruyordu) ve saha kurulumu da gateway poll periyoduna gore
        # ayarlanabilir.
        self.result_timeout_sec = result_timeout_sec
        self.result_poll_sec = result_poll_sec
        self._headers = {
            "X-Service-Token": service_token,
            "X-Service-Name": "iec104-outbound",
        }

    def queue(self, *, device_code: str, command: str, peer: str) -> CommandAccepted:
        """Komutu kuyruga aldirir. Reddedilirse `CommandRejected`."""
        url = f"{self.base_url}/internal/device-commands"
        payload = {
            "device_code": device_code,
            "command": command,
            "origin": "iec104",
            "peer": peer,
        }
        try:
            resp = requests.post(
                url, json=payload, headers=self._headers, timeout=self.timeout_sec
            )
        except requests.RequestException as exc:
            # Ag hatasi = komut kuyruga ALINMADI. Master'a olumlu donmek
            # kaybolmus bir reset'i "kabul edildi" gostermek olurdu.
            raise CommandRejected("backend_unreachable", str(exc)) from exc

        if resp.status_code == 400:
            raise CommandRejected(*_reason_from_response(resp))
        if resp.status_code != 200:
            raise CommandRejected("backend_error", f"HTTP {resp.status_code}")
        try:
            data: dict[str, Any] = resp.json()
            return CommandAccepted(id=int(data["id"]), status=str(data["status"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise CommandRejected("bad_response", str(exc)) from exc

    def result(self, command_id: int) -> tuple[str, str | None]:
        """(status, result_status) doner. Okunamazsa ("unknown", None)."""
        url = f"{self.base_url}/internal/device-commands/{command_id}"
        try:
            resp = requests.get(url, headers=self._headers, timeout=self.timeout_sec)
        except requests.RequestException as exc:
            logger.debug("iec104_komut_sonucu_okunamadi id=%d error=%s", command_id, exc)
            return "unknown", None
        if resp.status_code != 200:
            return "unknown", None
        try:
            data = resp.json()
            return str(data.get("status") or "unknown"), data.get("result_status")
        except ValueError:
            return "unknown", None


def _reason_from_response(resp: requests.Response) -> tuple[str, str]:
    """400 govdesinden makine okunabilir `reason` cikarir.

    Backend `{"detail": {"reason": ..., "message": ...}}` doner; eski surum ya
    da proxy araya girerse duz metin de gelebilir — ikisini de tolere ediyoruz
    cunku burada patlamak master'i yanitsiz birakirdi.
    """
    try:
        detail = resp.json().get("detail")
    except ValueError:
        return "rejected", resp.text[:200]
    if isinstance(detail, dict):
        return str(detail.get("reason") or "rejected"), str(detail.get("message") or "")
    return "rejected", str(detail)[:200]
