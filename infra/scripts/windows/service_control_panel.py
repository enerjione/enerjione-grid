"""Horstman Smart Logger - Servis Kontrol Paneli.

Özellikler:
- Tüm aksiyonlar arka plan thread'inde çalışır (UI hiç donmaz).
- Windows'ta child process tree'si `taskkill /T /F` ile düzgün kapatılır.
- Servis stdout/stderr akışları 500 satır ring-buffer'da tutulur (kurulum
  adımları için "Çıktıyı Göster" penceresinde canlı izlenebilir).
- "Kurulum" sekmesi pip/npm install ve installer hesabı oluşturmayı GUI'den
  yapar — CMD'e hiç gerek kalmaz.
- "Akıllı Başlat" Windows servisleri → backend → diğerleri şeklinde sırayla
  ama her biri kendi thread'inde ilerler.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Callable


CONFIG_FILE = Path(__file__).with_name("service_control_panel.config.json")
# Template fallback: gercek config dosyasi .gitignore'da; ilk calistirmada yoksa
# .example.json'i ornek olarak kullanip kopyalanmasini istiyoruz.
CONFIG_EXAMPLE_FILE = Path(__file__).with_name("service_control_panel.config.example.json")
# Proje kökü: infra/scripts/windows/service_control_panel.py → parents[3] = repo kökü.
# working_dir göreli yazılırsa (örn. "apps/backend-api") bu köke göre çözülür; böylece
# proje farklı bir dizine taşındığında config'i elle güncellemeye gerek kalmaz.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
REFRESH_MS = 1500
LOG_BUFFER_SIZE = 500

# Windows'ta CREATE_NEW_PROCESS_GROUP bayrağı yeni proces grubu oluşturur.
CREATE_NEW_PROCESS_GROUP = 0x00000200 if os.name == "nt" else 0
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ---------------------------------------------------------------------------
# Gorsel palet
# ---------------------------------------------------------------------------
# Web arayuzuyle ayni tonlar (slate + turuncu vurgu) kullanildi ki panel
# uygulamanin parcasi gibi dursun. Tek yerden degistirilebilsin diye sozluk.
PALETTE = {
    "canvas": "#eef1f6",       # pencere zemini
    "card": "#ffffff",         # kart/satir zemini
    "row_alt": "#f8fafc",      # zebra satir
    "row_head": "#f1f5f9",     # tablo baslik seridi
    "border": "#dfe4ec",       # kart kenarligi
    "border_soft": "#eef2f7",  # satir ayiricisi
    "text": "#0f172a",
    "muted": "#64748b",
    "accent": "#c2410c",       # EnerjiOne turuncusu (koyu ton)
    "accent_soft": "#fff1e6",
    "btn": "#e2e8f0",
    "btn_hover": "#cbd5e1",
    "ok": "#15803d",
    "warn": "#b45309",
    "bad": "#dc2626",
    "info": "#1d4ed8",
}

# Aksiyon butonu turleri. ttk.Button 'vista' temasinda arka plan rengini
# yok sayiyordu (butun butonlar ayni gri gorunuyordu); tk.Button + bu
# renklerle Baslat/Durdur/Yeniden Baslat gorsel olarak ayrisiyor.
#
# Satir butonlari DOLGU DEGIL, yumusak tint + renkli yazi: 11 satir x 3 dolu
# renkli buton ekrani cok gurultulu yapiyordu. Vurgu gereken tek yer ust
# seritteki "Akilli Baslat" (kind="primary", dolgu).
BUTTON_KINDS = {
    "start":   {"bg": "#e8f6ee", "hover": "#d3edde", "active": "#c2e6d1", "fg": "#15803d"},
    "stop":    {"bg": "#fdecec", "hover": "#fadada", "active": "#f6c9c9", "fg": "#b91c1c"},
    "restart": {"bg": "#eaf1fb", "hover": "#d8e6f8", "active": "#c7daf4", "fg": "#1d4ed8"},
    "neutral": {"bg": "#eef1f6", "hover": "#e2e8f0", "active": "#cbd5e1", "fg": "#334155"},
    "primary": {"bg": "#15803d", "hover": "#166534", "active": "#14532d", "fg": "#ffffff"},
}

# Servis tablosu kolon duzeni — baslik seridi ve satirlar AYNI listeyi
# kullanir, aksi halde kolonlar kayiyor.
SERVICE_COL_LABELS = ("SERVİS", "TİP", "DURUM", "SAĞLIK", "AKSİYON")
SERVICE_COL_WIDTHS = (210, 150, 175, 250, 300)


@dataclass
class ServiceConfig:
    name: str
    service_type: str
    health_host: str = "127.0.0.1"
    health_port: int = 0
    windows_service_name: str = ""
    working_dir: str = ""
    command: list[str] | None = None
    env: dict[str, str] | None = None


@dataclass
class BackendSettings:
    """Kontrol panelinin backend API ile haberlesebilmesi icin gerekli ayarlar.

    Panel artik installer hesabiyla JWT login yapmaz; backend'in
    `internal_service_token` degeri ile `X-Service-Token` header'inin
    alisveris etmesi yeterli. Boylece kisisel bir kullanici hesabina bagimli
    kalmaz ve token backend .env'i ile senkronize edilir."""

    base_url: str = "http://127.0.0.1:8000/api/v1"
    service_token: str = "change-me-internal-token"


@dataclass
class RemoteGateway:
    """Backend'den cekilen gateway kaydi. Panel satirlari bu yapiyi gosterir."""

    code: str
    name: str
    host: str
    listen_port: int
    control_host: str
    control_port: int
    is_active: bool
    last_seen_at: str | None
    device_code_prefix: str | None
    batch_interval_sec: int
    max_devices: int


@dataclass
class SetupTask:
    name: str
    working_dir: str
    command: list[str]
    description: str = ""


@dataclass
class ServiceRuntime:
    process: subprocess.Popen | None = None
    pending: bool = False  # start/stop sürüyor
    pending_action: str | None = None  # "start" | "stop" — UI'da başlatılıyor / durduruluyor ayrımı
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_BUFFER_SIZE))


def _resolve_working_dir(raw: str) -> str:
    """Config'teki working_dir mutlaksa olduğu gibi, göreli ise PROJECT_ROOT'a göre çözülür."""
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return str(path)


def _parse_services(rows: list[dict]) -> list[ServiceConfig]:
    items: list[ServiceConfig] = []
    for row in rows:
        items.append(
            ServiceConfig(
                name=row["name"],
                service_type=row["type"],
                health_host=row.get("health_host", "127.0.0.1"),
                health_port=int(row.get("health_port", 0)),
                windows_service_name=row.get("windows_service_name", ""),
                working_dir=_resolve_working_dir(row.get("working_dir", "")),
                command=row.get("command"),
                env=row.get("env", {}),
            )
        )
    return items


def read_config() -> tuple[list[ServiceConfig], list[ServiceConfig], BackendSettings]:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    services = _parse_services(data.get("services", []))
    gateways = _parse_services(data.get("gateways", []))
    backend_raw = data.get("backend") or {}
    backend = BackendSettings(
        base_url=backend_raw.get("base_url", "http://127.0.0.1:8000/api/v1").rstrip("/"),
        service_token=backend_raw.get("service_token", "change-me-internal-token"),
    )
    return services, gateways, backend


def is_port_open(host: str, port: int) -> bool:
    if port <= 0:
        return False
    candidates = [host]
    if host not in {"localhost", "127.0.0.1", "::1"}:
        candidates.extend(["localhost", "127.0.0.1"])
    else:
        candidates.extend(["localhost", "127.0.0.1", "::1"])

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            with socket.create_connection((candidate, port), timeout=1.0):
                return True
        except OSError:
            continue
    return False


def run_ps(cmd: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


def get_windows_services_state(names: list[str]) -> dict[str, str]:
    clean_names = [name for name in names if name]
    if not clean_names:
        return {}
    quoted = ",".join([f"'{name}'" for name in clean_names])
    cmd = (
        "$names=@("
        + quoted
        + "); "
        "$items=Get-Service -Name $names -ErrorAction SilentlyContinue | "
        "Select-Object Name,Status; "
        "$items | ConvertTo-Json -Compress"
    )
    try:
        result = run_ps(cmd, timeout=10)
    except subprocess.TimeoutExpired:
        return {}
    if result.returncode != 0:
        return {}
    raw = (result.stdout or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    rows = data if isinstance(data, list) else [data]
    mapped: dict[str, str] = {}
    for row in rows:
        service_name = str(row.get("Name", "")).strip()
        raw_status = row.get("Status", "UNKNOWN")
        status = _normalize_windows_status(raw_status)
        if service_name:
            mapped[service_name] = status
    return mapped


def _normalize_windows_status(raw_status: object) -> str:
    enum_map = {
        "1": "STOPPED",
        "2": "START_PENDING",
        "3": "STOP_PENDING",
        "4": "RUNNING",
        "5": "CONTINUE_PENDING",
        "6": "PAUSE_PENDING",
        "7": "PAUSED",
    }
    normalized = str(raw_status).strip().upper()
    return enum_map.get(normalized, normalized if normalized else "UNKNOWN")


def _kill_process_tree(pid: int) -> None:
    """Windows'ta child'ları dahil process ağacını kapat."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=8,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception:
            pass
    else:
        try:
            os.kill(pid, 15)
        except Exception:
            pass


def find_pids_listening_on_port(port: int) -> list[int]:
    """Belirtilen TCP portunda LISTEN durumunda olan proses pid'lerini döndürür.

    Paneli yeniden başlattığımızda daha önceden ayaga kalkmış frontend/tag-engine
    gibi servisleri durdurmak için gerekli (PID runtime dict'inde yok)."""
    if port <= 0 or os.name != "nt":
        return []
    # Önce PowerShell — Windows 10/11'de Get-NetTCPConnection hazır gelir.
    ps_cmd = (
        f"$c = Get-NetTCPConnection -LocalPort {int(port)} -State Listen "
        "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess; "
        "if ($c) { $c | Sort-Object -Unique }"
    )
    try:
        result = run_ps(ps_cmd, timeout=6)
        if result.returncode == 0 and (result.stdout or "").strip():
            pids: list[int] = []
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    pid = int(line)
                    if pid > 0 and pid not in pids:
                        pids.append(pid)
            if pids:
                return pids
    except Exception:
        pass
    # Fallback: netstat -ano
    try:
        proc = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            timeout=6,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return []
    pids: list[int] = []
    port_suffix = f":{port}"
    for raw_line in (proc.stdout or "").splitlines():
        parts = raw_line.split()
        if len(parts) < 5:
            continue
        if parts[0].upper() != "TCP":
            continue
        local = parts[1]
        state = parts[3].upper()
        if state != "LISTENING":
            continue
        if not local.endswith(port_suffix):
            continue
        try:
            pid = int(parts[4])
        except ValueError:
            continue
        if pid > 0 and pid not in pids:
            pids.append(pid)
    return pids


class BackendApiError(RuntimeError):
    pass


class BackendClient:
    """Backend API ile HTTP konusmasini saglayan kucuk istemci.

    Panelin gateway listesini backend'den cekmesi ve gateway'i aktif/pasife
    alabilmesi icin kullanilir. Kullanici oturumuna bagimli degildir; backend'in
    `internal_service_token` degeri ile eslesen `X-Service-Token` header'i
    gonderilir. `/internal/*` endpoint'leri bu sayede kisisel bir hesap
    acilmadan da calisir."""

    def __init__(self, settings: BackendSettings, request_timeout: float = 6.0) -> None:
        self.settings = settings
        self.request_timeout = request_timeout

    def _build_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        base = self.settings.base_url.rstrip("/")
        if not path.startswith("/"):
            path = "/" + path
        return f"{base}{path}"

    def _request(self, method: str, path: str, *, body: dict | None = None) -> dict | list | None:
        url = self._build_url(path)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"X-Service-Token": self.settings.service_token}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout) as resp:
                if resp.status == 204:
                    return None
                raw = resp.read().decode("utf-8")
                if not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            if exc.code == 401:
                raise BackendApiError(
                    "Servis token'i reddedildi. `service_control_panel.config.json` "
                    "icindeki `backend.service_token` degerinin backend .env "
                    "`INTERNAL_SERVICE_TOKEN` ile ayni oldugundan emin ol."
                ) from exc
            raise BackendApiError(
                f"{method} {path} basarisiz ({exc.code}): {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise BackendApiError(
                f"{method} {path} erisim hatasi: {exc.reason}"
            ) from exc

    def list_gateways(self) -> list[RemoteGateway]:
        data = self._request("GET", "/internal/gateways") or []
        result: list[RemoteGateway] = []
        if not isinstance(data, list):
            return result
        for row in data:
            if not isinstance(row, dict):
                continue
            result.append(
                RemoteGateway(
                    code=str(row.get("code") or ""),
                    name=str(row.get("name") or ""),
                    host=str(row.get("host") or ""),
                    listen_port=int(row.get("listen_port") or 0),
                    control_host=str(row.get("control_host") or row.get("host") or "127.0.0.1"),
                    control_port=int(row.get("control_port") or 0),
                    is_active=bool(row.get("is_active", True)),
                    last_seen_at=row.get("last_seen_at"),
                    device_code_prefix=row.get("device_code_prefix"),
                    batch_interval_sec=int(row.get("batch_interval_sec") or 5),
                    max_devices=int(row.get("max_devices") or 200),
                )
            )
        return result

    def enable_gateway(self, code: str) -> None:
        self._request("POST", f"/internal/gateways/{code}/enable")

    def disable_gateway(self, code: str) -> None:
        self._request("POST", f"/internal/gateways/{code}/disable")


class ServiceControlPanel:
    def __init__(
        self,
        root: tk.Tk,
        services: list[ServiceConfig],
        gateways: list[ServiceConfig],
        backend_settings: BackendSettings,
    ) -> None:
        self.root = root
        self.services = services
        self.gateways = gateways
        self.all_services = services + gateways
        self.runtimes: dict[str, ServiceRuntime] = {
            svc.name: ServiceRuntime() for svc in self.all_services
        }
        self.rows: dict[str, dict] = {}
        self.log_windows: dict[str, tk.Toplevel] = {}
        self.log_text_widgets: dict[str, tk.Text] = {}
        self.status_queue: list[dict[str, tuple[str, str]]] = []
        self._stop_event = threading.Event()
        self._event_log_max = 2000
        self._event_log_pending: list[tuple[float, str, str, str]] = []
        self._event_log_lock = threading.Lock()
        self._event_log_total = 0
        self._last_state_snapshot: dict[str, tuple[str, str]] = {}
        self._last_action_info_text = ""
        self.event_tree: ttk.Treeview | None = None
        self.event_autoscroll_var: tk.BooleanVar | None = None

        # Uzak gateway yonetimi icin backend istemcisi.
        self.backend_settings = backend_settings
        self.backend_client = BackendClient(backend_settings)
        self.remote_gateways: list[RemoteGateway] = []
        self.remote_gw_tree: ttk.Treeview | None = None
        self._remote_gw_refresh_lock = threading.Lock()
        self._remote_gw_last_error = ""

        self._setup_tasks = self._build_setup_tasks()
        self._build_ui()
        self._log_event("INFO", "Panel", "Servis Kontrol Paneli başlatıldı.")
        self._start_status_worker()
        self._apply_status_updates()
        self._start_remote_gateway_worker()

    # ------------------------------------------------------------------ UI ---

    def _build_ui(self) -> None:
        self.root.title("EnerjiOne Grid — Servis Kontrol Paneli")
        # Pencere ARTIK yeniden boyutlandirilabilir. Onceki surumde
        # minsize == maxsize == 1320x820 ve resizable(False, False) idi;
        # 11 servis 820px'e sigmadigi icin son satir (Frontend Web)
        # erisilemez halde kaliyordu. Buyutme/maximize serbest, servis
        # listesi de kendi icinde scroll ediyor (bkz. _build_service_table).
        self.root.geometry("1360x860")
        self.root.minsize(1080, 620)
        self.root.resizable(True, True)
        self._configure_styles()
        self.root.configure(bg=PALETTE["canvas"])

        top = tk.Frame(self.root, bg=PALETTE["canvas"])
        top.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)

        # ---------- Ust serit: baslik + canli durum ----------
        header = tk.Frame(top, bg=PALETTE["canvas"])
        header.pack(fill=tk.X, pady=(0, 12))

        brand = tk.Frame(header, bg=PALETTE["canvas"])
        brand.pack(side=tk.LEFT)
        tk.Label(
            brand,
            text="Servis Kontrol Paneli",
            bg=PALETTE["canvas"],
            fg=PALETTE["text"],
            font=("Segoe UI Semibold", 15),
        ).pack(anchor="w")
        tk.Label(
            brand,
            text="EnerjiOne Grid · yerel servis yonetimi",
            bg=PALETTE["canvas"],
            fg=PALETTE["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor="w")

        # Durum kutusu — eskiden ayri bir LabelFrame satiriydi, artik
        # basligin sagindaki canli bir serit.
        status_wrap = self._card(header)
        status_wrap.pack(side=tk.RIGHT, padx=(24, 0))
        status_inner = tk.Frame(status_wrap, bg=PALETTE["card"])
        status_inner.pack(fill=tk.X, padx=12, pady=9)
        self.status_dot = tk.Label(
            status_inner, text="●", bg=PALETTE["card"], fg=PALETTE["muted"],
            font=("Segoe UI", 11),
        )
        self.status_dot.pack(side=tk.LEFT, padx=(0, 8))
        self.action_info = tk.Label(
            status_inner,
            text="Hazır.",
            bg=PALETTE["card"],
            fg=PALETTE["text"],
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
        )
        self.action_info.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # ---------- Hizli aksiyonlar ----------
        actions_card = self._card(top)
        actions_card.pack(fill=tk.X, pady=(0, 12))
        actions_head = tk.Frame(actions_card, bg=PALETTE["card"])
        actions_head.pack(fill=tk.X, padx=14, pady=(11, 0))
        tk.Label(
            actions_head,
            text="HIZLI AKSİYONLAR",
            bg=PALETTE["card"],
            fg=PALETTE["muted"],
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w")

        actions = tk.Frame(actions_card, bg=PALETTE["card"])
        actions.pack(fill=tk.X, padx=14, pady=(8, 13))
        groups = [
            ("Uygulamalar", [
                # Panelin ana eylemi — tek dolgu renkli buton.
                ("Akıllı Başlat", self.smart_start_all, "primary"),
                ("Durdur", self.stop_all, "stop"),
                ("Yeniden Başlat", self.restart_all, "restart"),
            ]),
            ("Gatewayler", [
                ("Başlat", self.start_gateways, "start"),
                ("Durdur", self.stop_gateways, "stop"),
                ("Yeniden Başlat", self.restart_gateways, "restart"),
            ]),
        ]
        for col, (group_label, buttons) in enumerate(groups):
            block = tk.Frame(actions, bg=PALETTE["card"])
            block.grid(row=0, column=col, sticky="w", padx=(0, 28))
            tk.Label(
                block,
                text=group_label,
                bg=PALETTE["card"],
                fg=PALETTE["text"],
                font=("Segoe UI Semibold", 9),
            ).pack(anchor="w", pady=(0, 6))
            row = tk.Frame(block, bg=PALETTE["card"])
            row.pack(anchor="w")
            for text, cmd, kind in buttons:
                self._pill_button(row, text, cmd, kind).pack(side=tk.LEFT, padx=(0, 7))

        # ---------- Sekmeler ----------
        notebook = ttk.Notebook(top)
        notebook.pack(fill=tk.BOTH, expand=True)
        core_tab = ttk.Frame(notebook, padding=(0, 10, 0, 0))
        gateway_tab = ttk.Frame(notebook, padding=(4, 8, 4, 8))
        setup_tab = ttk.Frame(notebook, padding=(10, 10, 10, 10))
        events_tab = ttk.Frame(notebook, padding=(8, 8, 8, 8))
        notebook.add(core_tab, text="  Temel Servisler  ")
        notebook.add(gateway_tab, text="  Gateway Yönetimi  ")
        notebook.add(setup_tab, text="  Kurulum  ")
        notebook.add(events_tab, text="  Olay Günlüğü  ")

        self._build_service_table(core_tab, self.services)
        self._build_remote_gateways_tab(gateway_tab)
        self._build_setup_tab(setup_tab)
        self._build_events_tab(events_tab)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------- gorsel yardim ---

    def _card(self, parent: tk.Misc) -> tk.Frame:
        """Beyaz, ince kenarli kart cercevesi (ttk.LabelFrame yerine).

        ttk.LabelFrame'in kenar/zemin rengi 'vista' temasinda kontrol
        edilemiyordu; duz tk.Frame ile 1px kenarlik daha temiz duruyor.
        """
        return tk.Frame(
            parent,
            bg=PALETTE["card"],
            highlightbackground=PALETTE["border"],
            highlightthickness=1,
            bd=0,
        )

    def _pill_button(
        self, parent: tk.Misc, text: str, command: Callable[[], None], kind: str
    ) -> tk.Button:
        """Renkli, flat aksiyon butonu.

        ttk.Button 'vista' temasinda arka plan rengini kabul etmiyor (hepsi
        ayni griydi); tk.Button ile start/stop/restart renkleri ayrisiyor.
        """
        spec = BUTTON_KINDS[kind]
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=spec["bg"],
            fg=spec["fg"],
            activebackground=spec["active"],
            activeforeground=spec["fg"],
            font=("Segoe UI Semibold", 9),
            relief=tk.FLAT,
            bd=0,
            padx=14,
            pady=7,
            cursor="hand2",
            highlightthickness=0,
        )
        btn.bind("<Enter>", lambda _e, b=btn, s=spec: b.configure(bg=s["hover"]))
        btn.bind("<Leave>", lambda _e, b=btn, s=spec: b.configure(bg=s["bg"]))
        return btn

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        # 'clam' secildi: 'vista' temasi arka plan/kenar renklerini yok
        # sayiyor, bu yuzden butun kartlar/sekme seridi sistem grisinde
        # kaliyordu. clam tam temalanabilir.
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("TFrame", background=PALETTE["canvas"])
        style.configure("TLabel", background=PALETTE["canvas"], foreground=PALETTE["text"])
        style.configure(
            "TButton", padding=(10, 7), font=("Segoe UI Semibold", 9),
            background=PALETTE["btn"], foreground=PALETTE["text"],
            borderwidth=0, focusthickness=0,
        )
        style.map(
            "TButton",
            background=[("active", PALETTE["btn_hover"]), ("disabled", PALETTE["border"])],
            foreground=[("disabled", PALETTE["muted"])],
        )
        for name, key in (
            ("Primary.TButton", "start"),
            ("Warn.TButton", "stop"),
            ("Secondary.TButton", "restart"),
            ("Setup.TButton", "neutral"),
        ):
            spec = BUTTON_KINDS[key]
            style.configure(
                name, padding=(10, 7), font=("Segoe UI Semibold", 9),
                background=spec["bg"], foreground=spec["fg"],
                borderwidth=0, focusthickness=0,
            )
            style.map(
                name,
                background=[("active", spec["hover"]), ("disabled", PALETTE["border"])],
                foreground=[("disabled", PALETTE["muted"])],
            )

        # Sekme seridi
        style.configure("TNotebook", background=PALETTE["canvas"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            padding=(16, 9),
            font=("Segoe UI Semibold", 9),
            background=PALETTE["canvas"],
            foreground=PALETTE["muted"],
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", PALETTE["card"])],
            foreground=[("selected", PALETTE["accent"])],
        )

        # Treeview (Gateway / Olay Gunlugu sekmeleri)
        style.configure(
            "Treeview",
            background=PALETTE["card"],
            fieldbackground=PALETTE["card"],
            foreground=PALETTE["text"],
            rowheight=26,
            borderwidth=0,
            font=("Segoe UI", 9),
        )
        style.configure(
            "Treeview.Heading",
            background=PALETTE["row_head"],
            foreground=PALETTE["muted"],
            font=("Segoe UI Semibold", 9),
            borderwidth=0,
        )
        style.map("Treeview", background=[("selected", PALETTE["accent_soft"])],
                  foreground=[("selected", PALETTE["accent"])])

        style.configure("Vertical.TScrollbar", background=PALETTE["btn"],
                        troughcolor=PALETTE["canvas"], borderwidth=0, arrowsize=12)
        style.configure("TLabelframe", background=PALETTE["canvas"],
                        borderwidth=1, relief="solid")
        style.configure("TLabelframe.Label", background=PALETTE["canvas"],
                        foreground=PALETTE["text"], font=("Segoe UI Semibold", 9))

    def _build_service_table(self, parent: ttk.Frame, services: list[ServiceConfig]) -> None:
        """Servis listesi — KAYDIRILABILIR.

        Onceki surumde satirlar dogrudan sabit yuksekli bir frame'e
        grid'leniyordu; pencere 820px'e kilitli oldugu icin 11. servis
        (Frontend Web) ekranin altinda kaliyor ve hicbir sekilde
        gorulemiyordu. Artik Canvas + Scrollbar var, fare tekerlegi de
        bagli.
        """
        shell = self._card(parent)
        shell.pack(fill=tk.BOTH, expand=True)

        # --- Sabit baslik satiri (scroll etmez) ---
        head = tk.Frame(shell, bg=PALETTE["row_head"])
        head.pack(fill=tk.X)
        for i, (text, width) in enumerate(zip(SERVICE_COL_LABELS, SERVICE_COL_WIDTHS)):
            tk.Label(
                head, text=text, bg=PALETTE["row_head"], fg=PALETTE["muted"],
                font=("Segoe UI Semibold", 8), anchor="w",
            ).grid(row=0, column=i, sticky="w", padx=(16 if i == 0 else 10, 10), pady=10)
            head.grid_columnconfigure(i, minsize=width, weight=1 if i == 3 else 0)
        tk.Frame(head, bg=PALETTE["border"], height=1).grid(
            row=0, column=0, columnspan=len(SERVICE_COL_WIDTHS), sticky="wes"
        )

        # --- Kaydirilabilir govde ---
        body = tk.Frame(shell, bg=PALETTE["card"])
        body.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(body, bg=PALETTE["card"], highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        table = tk.Frame(canvas, bg=PALETTE["card"])
        window_id = canvas.create_window((0, 0), window=table, anchor="nw")

        def _on_table_resize(_event: object) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_resize(event: object) -> None:
            # Ic frame'i canvas genisligine yay ki kolonlar hizali kalsin.
            canvas.itemconfigure(window_id, width=event.width)  # type: ignore[attr-defined]

        table.bind("<Configure>", _on_table_resize)
        canvas.bind("<Configure>", _on_canvas_resize)

        def _on_wheel(event: object) -> None:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")  # type: ignore[attr-defined]

        # Fare tekerlegi: imlec liste uzerindeyken bagla, cikinca birak
        # (global bind sekmelerdeki diger scroll alanlarini bozardi).
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        for idx, svc in enumerate(services):
            self._build_service_row(table, idx, svc)

    def _build_service_row(self, table: tk.Frame, idx: int, svc: ServiceConfig) -> None:
        """Tek servis satiri.

        Satir, TAM GENISLIKTE bir kendi frame'i olarak kurulur (onceki surumde
        hucreler dogrudan tabloya grid'leniyordu; zebra zemini yalnizca etiket
        genisligi kadar boyaniyor, arada kesik gri bloklar olusuyordu).
        """
        row_bg = PALETTE["card"] if idx % 2 == 0 else PALETTE["row_alt"]

        row = tk.Frame(table, bg=row_bg)
        row.grid(row=idx, column=0, sticky="we")
        table.grid_columnconfigure(0, weight=1)
        for i, width in enumerate(SERVICE_COL_WIDTHS):
            row.grid_columnconfigure(i, minsize=width, weight=1 if i == 3 else 0)

        def cell(col: int, **kw) -> tk.Label:
            lbl = tk.Label(row, bg=row_bg, anchor="w", **kw)
            lbl.grid(row=0, column=col, sticky="w",
                     padx=(16 if col == 0 else 10, 10), pady=9)
            return lbl

        # NOT: Tk font boyutu TAM SAYI olmali (9.5 -> TclError).
        name_lbl = cell(0, text=svc.name, fg=PALETTE["text"],
                        font=("Segoe UI Semibold", 10))
        type_lbl = cell(1, text=self._friendly_service_type(svc.service_type),
                        fg=PALETTE["muted"], font=("Segoe UI", 9))
        state_lbl = cell(2, text="—", fg=PALETTE["muted"],
                         font=("Segoe UI Semibold", 9))
        health_lbl = cell(3, text="—", fg=PALETTE["muted"], font=("Segoe UI", 9))

        btns = tk.Frame(row, bg=row_bg)
        btns.grid(row=0, column=4, sticky="e", padx=(10, 16), pady=6)
        start_btn = self._pill_button(btns, "Başlat", lambda s=svc: self.start_service(s), "start")
        stop_btn = self._pill_button(btns, "Durdur", lambda s=svc: self.stop_service(s), "stop")
        restart_btn = self._pill_button(
            btns, "Yeniden Başlat", lambda s=svc: self.restart_service(s), "restart"
        )
        start_btn.pack(side=tk.LEFT, padx=(0, 6))
        stop_btn.pack(side=tk.LEFT, padx=(0, 6))
        restart_btn.pack(side=tk.LEFT)

        # Satirlar arasi 1px ayirici — tam genislikte, ayri grid satirinda.
        tk.Frame(table, bg=PALETTE["border_soft"], height=1).grid(
            row=idx, column=0, sticky="wes"
        )

        self.rows[svc.name] = {
            "state": state_lbl,
            "health": health_lbl,
            "cfg": svc,
            "start_btn": start_btn,
            "stop_btn": stop_btn,
            "restart_btn": restart_btn,
        }

    # ------------------------------------------------------------- Kurulum ---

    def _build_setup_tasks(self) -> list[SetupTask]:
        tasks: list[SetupTask] = []
        seen: set[str] = set()
        for svc in self.all_services:
            if svc.service_type != "process" or not svc.working_dir or not svc.command:
                continue
            workdir_key = os.path.normcase(os.path.normpath(svc.working_dir))
            if workdir_key in seen:
                continue
            seen.add(workdir_key)
            cmd0 = (svc.command[0] or "").lower() if svc.command else ""
            if cmd0 == "npm":
                tasks.append(
                    SetupTask(
                        name=f"{svc.name} → npm install",
                        working_dir=svc.working_dir,
                        command=["cmd", "/c", "npm", "install"],
                        description="Frontend Node paketlerini yükler.",
                    )
                )
            elif cmd0 == "py":
                py_cmd = svc.command[:2] if len(svc.command) >= 2 and svc.command[1] == "-3.11" else ["py"]
                tasks.append(
                    SetupTask(
                        name=f"{svc.name} → pip install",
                        working_dir=svc.working_dir,
                        command=[*py_cmd, "-m", "pip", "install", "-r", "requirements.txt"],
                        description="Python bağımlılıklarını requirements.txt üzerinden kurar.",
                    )
                )

        backend = next(
            (svc for svc in self.services if svc.name.lower().startswith("backend")),
            None,
        )
        if backend and backend.working_dir:
            py_cmd = ["py", "-3.11"]
            if backend.command and len(backend.command) >= 2 and backend.command[1] == "-3.11":
                py_cmd = backend.command[:2]
            tasks.append(
                SetupTask(
                    name="Kurulumcu (Installer) Hesabı Oluştur / Sıfırla",
                    working_dir=backend.working_dir,
                    command=[*py_cmd, "scripts/seed_installer.py"],
                    description=(
                        "Varsayılan kurulumcu hesabını (username=installer, password=ChangeMe123!) oluşturur "
                        "veya şifresini sıfırlar. PostgreSQL ve Backend'in DB'ye erişmiş olması gerekir."
                    ),
                )
            )
            tasks.append(
                SetupTask(
                    name="Varsayılan Sinyalleri Seed Et",
                    working_dir=backend.working_dir,
                    command=[
                        *py_cmd,
                        "-c",
                        "from app.db.session import SessionLocal; from app.services.signal_catalog_seed import seed_default_signals; db=SessionLocal();"
                        " seed_default_signals(db); print('OK')",
                    ],
                    description="Horstmann SN2 için varsayılan sinyal kataloğunu veritabanına ekler (idempotent).",
                )
            )

        return tasks

    def _build_setup_tab(self, parent: ttk.Frame) -> None:
        intro = ttk.Label(
            parent,
            text=(
                "Kurulum adımları — CMD'e gerek kalmadan bağımlılıkları yükleyin ve ilk "
                "çalışma için gerekli hesap/seed işlemlerini çalıştırın. Her buton arka "
                "planda çalışır; çıktıyı her zaman 'Çıktıyı Göster' ile izleyebilirsiniz."
            ),
            wraplength=1260,
            justify="left",
            foreground="#334155",
        )
        intro.pack(anchor="w", pady=(0, 8))

        bulk_bar = ttk.Frame(parent)
        bulk_bar.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(
            bulk_bar,
            text="Tüm Bağımlılıkları Kur",
            command=self.setup_install_all_deps,
            style="Primary.TButton",
            width=28,
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(
            bulk_bar,
            text="(pip install + npm install tüm servisler için; seed adımları dahil değildir)",
            foreground="#64748b",
        ).pack(side=tk.LEFT)

        table = ttk.Frame(parent)
        table.pack(fill=tk.BOTH, expand=True)
        header_font = ("Segoe UI", 10, "bold")
        ttk.Label(table, text="Görev", font=header_font).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Label(table, text="Açıklama", font=header_font).grid(row=0, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(table, text="Aksiyon", font=header_font).grid(row=0, column=2, sticky="w", padx=6, pady=4)
        table.grid_columnconfigure(0, minsize=320)
        table.grid_columnconfigure(1, minsize=700)
        table.grid_columnconfigure(2, minsize=220)

        for idx, task in enumerate(self._setup_tasks, start=1):
            ttk.Label(table, text=task.name, font=("Segoe UI", 9, "bold")).grid(
                row=idx, column=0, sticky="w", padx=6, pady=5
            )
            ttk.Label(table, text=task.description, wraplength=680, justify="left").grid(
                row=idx, column=1, sticky="w", padx=6, pady=5
            )
            row_buttons = ttk.Frame(table)
            ttk.Button(
                row_buttons,
                text="Çalıştır",
                style="Setup.TButton",
                width=12,
                command=lambda t=task: self.run_setup_task(t),
            ).pack(side=tk.LEFT, padx=2)
            ttk.Button(
                row_buttons,
                text="Çıktıyı Göster",
                style="TButton",
                width=14,
                command=lambda t=task: self.open_setup_log_window(t),
            ).pack(side=tk.LEFT, padx=2)
            row_buttons.grid(row=idx, column=2, sticky="w", padx=6, pady=5)

        if not self._setup_tasks:
            ttk.Label(
                table,
                text="(Servis konfigürasyonu boş — önce service_control_panel.config.json dosyasını doldurun.)",
                foreground="#64748b",
            ).grid(row=1, column=0, columnspan=3, sticky="w", padx=6, pady=5)

    def run_setup_task(self, task: SetupTask) -> None:
        rt_key = f"__setup__::{task.name}"
        rt = self.runtimes.get(rt_key)
        if rt is None:
            rt = ServiceRuntime()
            self.runtimes[rt_key] = rt
        if rt.process and rt.process.poll() is None:
            self._set_action_info(f"Kurulum '{task.name}' zaten çalışıyor.")
            return
        self._set_action_info(f"Kurulum başlıyor: {task.name}")
        self._run_in_thread(
            "setup",
            task.name,
            lambda: self._exec_setup_command(rt_key, task),
        )

    def _exec_setup_command(self, rt_key: str, task: SetupTask) -> None:
        rt = self.runtimes[rt_key]
        try:
            if not task.working_dir or not Path(task.working_dir).exists():
                self._set_action_info_threadsafe(
                    f"Kurulum '{task.name}': çalışma dizini bulunamadı ({task.working_dir}).",
                    is_error=True,
                )
                return
            rt.logs.append(f"[start] {task.name} — {' '.join(task.command)}")
            self._schedule_log_refresh(rt_key)
            process = self._spawn_process(task.command, task.working_dir, os.environ.copy())
            rt.process = process
            self._pump_output(rt_key, process)
            rc = process.wait()
            rt.logs.append(f"[end] exit={rc}")
            self._schedule_log_refresh(rt_key)
            if rc == 0:
                self._set_action_info_threadsafe(f"Kurulum bitti: {task.name} (OK).")
            else:
                self._set_action_info_threadsafe(
                    f"Kurulum başarısız: {task.name} (exit={rc}). 'Çıktıyı Göster' ile logu inceleyin.",
                    is_error=True,
                )
        except Exception as ex:
            rt.logs.append(f"[error] {ex}")
            self._schedule_log_refresh(rt_key)
            self._set_action_info_threadsafe(
                f"Kurulum hatası '{task.name}': {ex}",
                is_error=True,
            )
        finally:
            rt.process = None

    def setup_install_all_deps(self) -> None:
        deps_tasks = [
            task for task in self._setup_tasks if "install" in task.name.lower()
        ]
        if not deps_tasks:
            self._set_action_info("Kurulacak bağımlılık görevi bulunamadı.")
            return
        self._set_action_info("Tüm bağımlılıklar kuruluyor... (arka planda)")
        self._run_in_thread(
            "setup",
            "bulk-install",
            lambda: self._run_bulk_setup(deps_tasks),
        )

    def _run_bulk_setup(self, tasks: list[SetupTask]) -> None:
        for task in tasks:
            rt_key = f"__setup__::{task.name}"
            rt = self.runtimes.get(rt_key)
            if rt is None:
                rt = ServiceRuntime()
                self.runtimes[rt_key] = rt
            self._set_action_info_threadsafe(f"Kurulum: {task.name}...")
            self._exec_setup_command(rt_key, task)
        self._set_action_info_threadsafe("Tüm bağımlılık kurulum adımları tamamlandı.")

    def open_setup_log_window(self, task: SetupTask) -> None:
        rt_key = f"__setup__::{task.name}"
        if rt_key not in self.runtimes:
            self.runtimes[rt_key] = ServiceRuntime()
        self._ensure_log_window(rt_key, title=f"Kurulum Logu — {task.name}")

    # --------------------------------------------------------------- core ---

    def start_service(self, svc: ServiceConfig) -> None:
        self._run_in_thread("start", svc.name, lambda: self._start_service_sync(svc))

    def stop_service(self, svc: ServiceConfig) -> None:
        self._run_in_thread("stop", svc.name, lambda: self._stop_service_sync(svc))

    def restart_service(self, svc: ServiceConfig) -> None:
        self._run_in_thread("restart", svc.name, lambda: self._restart_service_sync(svc))

    def _start_service_sync(self, svc: ServiceConfig) -> None:
        rt = self.runtimes[svc.name]
        rt.pending_action = "start"
        rt.pending = True
        try:
            if svc.service_type == "windows_service":
                if not svc.windows_service_name:
                    self._set_action_info_threadsafe(
                        f"{svc.name}: Windows servis adı tanımlı değil.", is_error=True
                    )
                    return
                try:
                    result = run_ps(
                        f"Start-Service -Name '{svc.windows_service_name}'",
                        timeout=45,
                    )
                except subprocess.TimeoutExpired:
                    self._set_action_info_threadsafe(
                        f"{svc.name}: başlatma zaman aşımı (45sn).", is_error=True
                    )
                    return
                if result.returncode == 0:
                    self._set_action_info_threadsafe(f"{svc.name}: başlatıldı.")
                else:
                    self._set_action_info_threadsafe(
                        f"{svc.name}: başlatılamadı. {(result.stderr or '').strip()}",
                        is_error=True,
                    )
                return

            if rt.process is not None and rt.process.poll() is None:
                self._set_action_info_threadsafe(f"{svc.name}: zaten çalışıyor.")
                return

            env = os.environ.copy()
            if svc.env:
                env.update({str(k): str(v) for k, v in svc.env.items()})
            cwd = svc.working_dir or str(Path.cwd())
            try:
                process = self._spawn_process(svc.command or [], cwd, env)
            except Exception as ex:
                self._set_action_info_threadsafe(
                    f"{svc.name}: başlatılamadı. {ex}. "
                    f"'Kurulum' sekmesinden bağımlılıkları yüklemeyi deneyin.",
                    is_error=True,
                )
                rt.logs.append(f"[error] {ex}")
                self._schedule_log_refresh(svc.name)
                return
            rt.process = process
            rt.logs.append(f"[start] PID={process.pid} cwd={cwd}")
            self._schedule_log_refresh(svc.name)
            self._set_action_info_threadsafe(f"{svc.name}: başlatıldı (PID {process.pid}).")
            threading.Thread(
                target=self._pump_output,
                args=(svc.name, process),
                daemon=True,
            ).start()
        finally:
            rt.pending = False
            rt.pending_action = None

    def _stop_service_sync(self, svc: ServiceConfig) -> None:
        rt = self.runtimes[svc.name]
        rt.pending_action = "stop"
        rt.pending = True
        try:
            if svc.service_type == "windows_service":
                if not svc.windows_service_name:
                    self._set_action_info_threadsafe(
                        f"{svc.name}: Windows servis adı tanımlı değil.", is_error=True
                    )
                    return
                try:
                    result = run_ps(
                        f"Stop-Service -Name '{svc.windows_service_name}' -Force",
                        timeout=45,
                    )
                except subprocess.TimeoutExpired:
                    self._set_action_info_threadsafe(
                        f"{svc.name}: durdurma zaman aşımı (45sn).", is_error=True
                    )
                    return
                if result.returncode == 0:
                    self._set_action_info_threadsafe(f"{svc.name}: durduruldu.")
                else:
                    self._set_action_info_threadsafe(
                        f"{svc.name}: durdurulamadı. {(result.stderr or '').strip()}",
                        is_error=True,
                    )
                return

            process = rt.process
            if process is not None and process.poll() is None:
                pid = process.pid
                rt.logs.append(f"[stop] PID={pid} için taskkill /T /F")
                self._schedule_log_refresh(svc.name)
                _kill_process_tree(pid)
                try:
                    process.wait(timeout=8)
                    self._set_action_info_threadsafe(
                        f"{svc.name}: durduruldu (PID {pid})."
                    )
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except Exception:
                        pass
                    self._set_action_info_threadsafe(
                        f"{svc.name}: zorla sonlandırıldı."
                    )
                rt.process = None
                return

            # Panel bu prosesi başlatmadı ya da PID kayboldu (panel restart edildi).
            # is_port_open() bazı durumlarda (IPv6-only, panelin connect istegini
            # reddeden bir dinleyici vb.) False dondurebilir; bu yuzden dogrudan
            # Windows'a "bu portu hangi pid dinliyor?" diye sorup gelen pid'leri
            # oldurmeyi deneriz.
            if svc.health_port:
                pids = find_pids_listening_on_port(svc.health_port)
                if pids:
                    for pid in pids:
                        rt.logs.append(
                            f"[stop-external] port={svc.health_port} PID={pid} taskkill /T /F"
                        )
                        _kill_process_tree(pid)
                    self._schedule_log_refresh(svc.name)
                    # Port serbest kalana kadar veya yeni bir dinleyici kalmayana
                    # kadar kısa süre bekle (max 6sn).
                    end = time.time() + 6.0
                    remaining: list[int] = pids
                    while time.time() < end:
                        remaining = find_pids_listening_on_port(svc.health_port)
                        if not remaining:
                            break
                        time.sleep(0.3)
                    if remaining:
                        self._set_action_info_threadsafe(
                            f"{svc.name}: port {svc.health_port} hâlâ dinleniyor "
                            f"(PID: {', '.join(str(p) for p in remaining)}).",
                            is_error=True,
                        )
                    else:
                        self._set_action_info_threadsafe(
                            f"{svc.name}: durduruldu (dış PID: "
                            f"{', '.join(str(p) for p in pids)})."
                        )
                    return

            self._set_action_info_threadsafe(
                f"{svc.name}: aktif proses bulunamadı (port {svc.health_port} "
                "üzerinde dinleyici yok)."
            )
        finally:
            rt.pending = False
            rt.pending_action = None

    def _restart_service_sync(self, svc: ServiceConfig) -> None:
        if svc.service_type == "windows_service":
            try:
                result = run_ps(
                    f"Restart-Service -Name '{svc.windows_service_name}' -Force",
                    timeout=60,
                )
            except subprocess.TimeoutExpired:
                self._set_action_info_threadsafe(
                    f"{svc.name}: yeniden başlatma zaman aşımı.", is_error=True
                )
                return
            if result.returncode == 0:
                self._set_action_info_threadsafe(f"{svc.name}: yeniden başlatıldı.")
            else:
                self._set_action_info_threadsafe(
                    f"{svc.name}: yeniden başlatılamadı. {(result.stderr or '').strip()}",
                    is_error=True,
                )
            return
        self._stop_service_sync(svc)
        time.sleep(0.5)
        self._start_service_sync(svc)

    # --------------------------------------------------------------- bulk ---

    def smart_start_all(self) -> None:
        self._set_action_info("Akıllı başlatma: altyapı kontrol → backend → diğerleri...")
        self._run_in_thread("bulk", "smart-start", self._smart_start_sync)

    def _smart_start_sync(self) -> None:
        # Windows servisleri (PostgreSQL, RabbitMQ) altyapi olarak kabul edilir;
        # sadece DURUYORSA start denenir. Admin yetki yoksa hata uyarilir, sistem
        # durdurulmaz. Zaten calisanlara dokunulmaz (RabbitMQ gibi harici
        # servisler bozulmasin).
        windows_svcs = [svc for svc in self.services if svc.service_type == "windows_service"]
        infra_missing: list[ServiceConfig] = []
        for svc in windows_svcs:
            if not svc.windows_service_name:
                continue
            state = get_windows_services_state([svc.windows_service_name]).get(
                svc.windows_service_name, ""
            )
            if state == "RUNNING":
                self._set_action_info_threadsafe(
                    f"{svc.name}: zaten çalışıyor, dokunulmadı."
                )
                continue
            self._set_action_info_threadsafe(
                f"{svc.name}: durumda '{state or 'BİLİNMİYOR'}' — başlatma deneniyor."
            )
            self._start_service_sync(svc)
            final_state = get_windows_services_state([svc.windows_service_name]).get(
                svc.windows_service_name, ""
            )
            if final_state != "RUNNING" and not (
                svc.health_port and is_port_open(svc.health_host, svc.health_port)
            ):
                infra_missing.append(svc)

        self._wait_for_health(windows_svcs, deadline_sec=25)

        if infra_missing:
            names = ", ".join(s.name for s in infra_missing)
            self._set_action_info_threadsafe(
                f"Altyapı hazır değil: {names}. Admin yetki gerekebilir; yine de "
                "uygulamalar başlatılacak (bağlandıklarında işleyiş devam eder).",
                is_error=True,
            )

        backend = next(
            (svc for svc in self.services if svc.name.lower().startswith("backend")),
            None,
        )
        if backend:
            self._start_service_sync(backend)
            self._wait_for_health([backend], deadline_sec=40)

        remaining = [
            svc
            for svc in self.services
            if svc.service_type != "windows_service" and svc is not backend
        ]
        threads: list[threading.Thread] = []
        for svc in remaining:
            th = threading.Thread(
                target=self._start_service_sync, args=(svc,), daemon=True
            )
            th.start()
            threads.append(th)
        for th in threads:
            th.join(timeout=30)
        self._set_action_info_threadsafe("Akıllı başlatma tamamlandı.")

    def _wait_for_health(self, svcs: list[ServiceConfig], deadline_sec: int = 30) -> None:
        end = time.time() + deadline_sec
        pending = list(svcs)
        while pending and time.time() < end:
            pending = [
                svc
                for svc in pending
                if not (svc.health_port and is_port_open(svc.health_host, svc.health_port))
            ]
            if not pending:
                return
            time.sleep(0.5)

    def _app_services(self) -> list[ServiceConfig]:
        """Toplu aksiyonlarda dokunulan 'uygulama' prosesleri.

        PostgreSQL ve RabbitMQ altyapi olarak kabul edildigi icin toplu
        durdurma/yeniden baslatmada atlanir; tek tek istenirse yine her
        servis satirindan durdurulabilir.
        """
        return [svc for svc in self.services if svc.service_type != "windows_service"]

    def stop_all(self) -> None:
        self._set_action_info(
            "Uygulamalar durduruluyor (PostgreSQL/RabbitMQ hariç)..."
        )
        self._run_in_thread(
            "bulk",
            "stop-all",
            lambda: self._parallel(self._app_services(), self._stop_service_sync),
        )

    def restart_all(self) -> None:
        self._set_action_info(
            "Uygulamalar yeniden başlatılıyor (PostgreSQL/RabbitMQ hariç)..."
        )
        self._run_in_thread("bulk", "restart-all", self._smart_restart_sync)

    def _smart_restart_sync(self) -> None:
        self._parallel(self._app_services(), self._stop_service_sync)
        time.sleep(0.7)
        self._smart_start_sync()

    def start_gateways(self) -> None:
        self._set_action_info("Gatewayler başlatılıyor...")
        self._run_in_thread(
            "bulk",
            "gw-start",
            lambda: self._parallel(self.gateways, self._start_service_sync),
        )

    def stop_gateways(self) -> None:
        self._set_action_info("Gatewayler durduruluyor...")
        self._run_in_thread(
            "bulk",
            "gw-stop",
            lambda: self._parallel(self.gateways, self._stop_service_sync),
        )

    def restart_gateways(self) -> None:
        self._set_action_info("Gatewayler yeniden başlatılıyor...")
        self._run_in_thread(
            "bulk",
            "gw-restart",
            lambda: self._parallel(self.gateways, self._restart_service_sync),
        )

    def _parallel(
        self, svcs: list[ServiceConfig], fn: Callable[[ServiceConfig], None]
    ) -> None:
        threads: list[threading.Thread] = []
        for svc in svcs:
            th = threading.Thread(target=fn, args=(svc,), daemon=True)
            th.start()
            threads.append(th)
        for th in threads:
            th.join(timeout=60)

    # --------------------------------------------------------------- util ---

    def _run_in_thread(self, category: str, label: str, fn: Callable[[], None]) -> None:
        def runner() -> None:
            try:
                fn()
            except Exception as ex:
                self._set_action_info_threadsafe(
                    f"[{category}/{label}] beklenmeyen hata: {ex}", is_error=True
                )

        threading.Thread(target=runner, daemon=True).start()

    def _spawn_process(
        self, command: list[str], cwd: str, env: dict[str, str]
    ) -> subprocess.Popen:
        if not command:
            raise RuntimeError("Komut boş.")

        attempts: list[list[str]] = [command]
        cmd0 = (command[0] or "").lower()
        if cmd0 == "npm":
            attempts.insert(0, ["cmd", "/c", *command])
        elif cmd0 == "py" and len(command) >= 4 and command[2] == "-m":
            attempts.append([sys.executable, "-m", *command[3:]])

        last_error: Exception | None = None
        for candidate in attempts:
            try:
                return subprocess.Popen(
                    candidate,
                    cwd=cwd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
                    bufsize=1,
                )
            except Exception as ex:
                last_error = ex
        raise RuntimeError(f"Process başlatılamadı: {last_error}")

    def _pump_output(self, runtime_key: str, process: subprocess.Popen) -> None:
        rt = self.runtimes.get(runtime_key)
        if rt is None:
            rt = ServiceRuntime()
            self.runtimes[runtime_key] = rt
        try:
            if process.stdout is None:
                return
            for raw in process.stdout:
                line = raw.rstrip("\r\n")
                rt.logs.append(line)
                self._schedule_log_refresh(runtime_key)
        except Exception as ex:
            rt.logs.append(f"[pump-error] {ex}")
            self._schedule_log_refresh(runtime_key)

    # ------------------------------------------------------ event log ------

    # --------------------------------------------- remote gateways tab ----

    def _build_remote_gateways_tab(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(
            toolbar,
            text="Gateway'ler",
            font=("Segoe UI", 11, "bold"),
            foreground="#0f172a",
        ).pack(side=tk.LEFT)
        ttk.Label(
            toolbar,
            text="  · Web uygulamasındaki liste ile aynıdır",
            foreground="#64748b",
        ).pack(side=tk.LEFT)

        ttk.Button(
            toolbar,
            text="Yenile",
            command=self.refresh_remote_gateways,
            style="Secondary.TButton",
            width=10,
        ).pack(side=tk.RIGHT, padx=(8, 0))

        self.remote_gw_status_var = tk.StringVar(value="Liste yükleniyor…")
        ttk.Label(
            parent,
            textvariable=self.remote_gw_status_var,
            foreground="#64748b",
        ).pack(anchor="w", pady=(0, 6))

        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = (
            "gateway",
            "control",
            "polling",
            "reach",
            "last_seen",
        )
        tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            height=10,
            selectmode="browse",
        )
        tree.heading("gateway", text="Gateway")
        tree.heading("control", text="Uzak adres")
        tree.heading("polling", text="Veri toplama")
        tree.heading("reach", text="Uzak erişim")
        tree.heading("last_seen", text="Son görülme")
        tree.column("gateway", width=220, anchor="w", stretch=True)
        tree.column("control", width=160, anchor="w", stretch=False)
        tree.column("polling", width=100, anchor="center", stretch=False)
        tree.column("reach", width=100, anchor="center", stretch=False)
        tree.column("last_seen", width=150, anchor="w", stretch=False)

        tree.tag_configure("active", foreground="#15803d")
        tree.tag_configure("inactive", foreground="#b45309")
        tree.tag_configure("error", foreground="#b91c1c")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.remote_gw_tree = tree

        action_bar = ttk.Frame(parent)
        action_bar.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(
            action_bar,
            text="Seçin ve:",
            foreground="#64748b",
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            action_bar,
            text="Başlat",
            style="Primary.TButton",
            command=lambda: self._remote_gateway_action("enable"),
            width=12,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            action_bar,
            text="Durdur",
            style="Warn.TButton",
            command=lambda: self._remote_gateway_action("disable"),
            width=12,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            action_bar,
            text="Yeniden Başlat",
            style="Secondary.TButton",
            command=lambda: self._remote_gateway_action("restart"),
            width=16,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(
            action_bar,
            text="  Uzak tarafta birkaç saniye içinde uygulanır.",
            foreground="#94a3b8",
        ).pack(side=tk.LEFT, padx=(8, 0))

    def refresh_remote_gateways(self) -> None:
        self._run_in_thread(
            "remote-gateways",
            "remote-gw-refresh",
            self._refresh_remote_gateways_sync,
        )

    def _refresh_remote_gateways_sync(self) -> None:
        if not self._remote_gw_refresh_lock.acquire(blocking=False):
            return
        try:
            try:
                gateways = self.backend_client.list_gateways()
                self._remote_gw_last_error = ""
            except BackendApiError as exc:
                self._remote_gw_last_error = str(exc)
                self._set_action_info_threadsafe(
                    f"Gateway listesi alınamadı: {exc}", is_error=True
                )
                self.root.after(0, self._apply_remote_gateway_error)
                return
            self.remote_gateways = gateways
            self.root.after(0, self._apply_remote_gateway_rows)
        finally:
            self._remote_gw_refresh_lock.release()

    def _apply_remote_gateway_rows(self) -> None:
        tree = self.remote_gw_tree
        if tree is None:
            return
        for item in tree.get_children(""):
            tree.delete(item)
        if not self.remote_gateways:
            self.remote_gw_status_var.set(
                "Henüz kayıtlı gateway yok. Web uygulamasında "
                "Mühendislik → Cihazlar bölümünden ekleyebilirsiniz."
            )
            return
        for gw in self.remote_gateways:
            label = f"{gw.name}  ({gw.code})"
            if gw.control_port and int(gw.control_port) > 0:
                control_str = f"{gw.control_host}:{gw.control_port}"
                reach = "Evet" if is_port_open(gw.control_host, gw.control_port) else "Hayır"
            else:
                control_str = f"{gw.control_host}  (uzaktan izleme kapalı)"
                reach = "—"
            polling = "Açık" if gw.is_active else "Duraklatıldı"
            tag = "active" if gw.is_active else "inactive"
            last_seen = gw.last_seen_at or "—"
            tree.insert(
                "",
                "end",
                iid=gw.code,
                values=(
                    label,
                    control_str,
                    polling,
                    reach,
                    last_seen,
                ),
                tags=(tag,),
            )
        n = len(self.remote_gateways)
        self.remote_gw_status_var.set(f"{n} gateway listelendi.")

    def _apply_remote_gateway_error(self) -> None:
        tree = self.remote_gw_tree
        if tree is None:
            return
        for item in tree.get_children(""):
            tree.delete(item)
        self.remote_gw_status_var.set(
            f"Sunucuya ulaşılamıyor ({self._remote_gw_last_error})"
        )

    def _remote_gateway_action(self, action: str) -> None:
        tree = self.remote_gw_tree
        if tree is None:
            return
        selection = tree.selection()
        if not selection:
            self._set_action_info(
                "Önce listeden bir gateway seçin.", is_error=True
            )
            return
        gateway_code = selection[0]
        gateway = next(
            (gw for gw in self.remote_gateways if gw.code == gateway_code),
            None,
        )
        if gateway is None:
            return

        def worker() -> None:
            try:
                if action == "enable":
                    self.backend_client.enable_gateway(gateway.code)
                    self._set_action_info_threadsafe(
                        f"{gateway.name}: veri toplama açıldı."
                    )
                elif action == "disable":
                    self.backend_client.disable_gateway(gateway.code)
                    self._set_action_info_threadsafe(
                        f"{gateway.name}: veri toplama duraklatıldı."
                    )
                elif action == "restart":
                    self.backend_client.disable_gateway(gateway.code)
                    self._set_action_info_threadsafe(
                        f"{gateway.name}: yeniden başlatılıyor…"
                    )
                    time.sleep(3.0)
                    self.backend_client.enable_gateway(gateway.code)
                    self._set_action_info_threadsafe(
                        f"{gateway.name}: yeniden başlatıldı."
                    )
            except BackendApiError as exc:
                self._set_action_info_threadsafe(
                    f"{gateway.code}: {exc}", is_error=True
                )
                return
            self._refresh_remote_gateways_sync()

        self._run_in_thread(
            f"remote-gw-{gateway.code}",
            f"remote-gw-{action}",
            worker,
        )

    def _start_remote_gateway_worker(self) -> None:
        """Her 15 saniyede bir backend'den gateway listesini tazeler."""
        self.refresh_remote_gateways()

        def _loop() -> None:
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=15)
                if self._stop_event.is_set():
                    return
                try:
                    self._refresh_remote_gateways_sync()
                except Exception:
                    pass

        threading.Thread(target=_loop, daemon=True).start()

    def _build_events_tab(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(
            toolbar,
            text="Olay Günlüğü",
            font=("Segoe UI", 11, "bold"),
            foreground="#0f172a",
        ).pack(side=tk.LEFT)

        ttk.Label(
            toolbar,
            text=" · servis başlatma, durdurma, sağlık ve akıllı başlatma olayları",
            foreground="#475569",
        ).pack(side=tk.LEFT)

        self.event_autoscroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            toolbar,
            text="Otomatik aşağı kaydır",
            variable=self.event_autoscroll_var,
        ).pack(side=tk.RIGHT, padx=(8, 0))

        ttk.Button(
            toolbar,
            text="Dışa Aktar",
            command=self._export_event_log,
            style="Secondary.TButton",
            width=12,
        ).pack(side=tk.RIGHT, padx=(8, 0))

        ttk.Button(
            toolbar,
            text="Temizle",
            command=self._clear_event_log,
            style="Warn.TButton",
            width=10,
        ).pack(side=tk.RIGHT, padx=(8, 0))

        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("time", "level", "source", "message")
        tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            height=18,
            selectmode="extended",
        )
        tree.heading("time", text="Zaman")
        tree.heading("level", text="Seviye")
        tree.heading("source", text="Kaynak")
        tree.heading("message", text="Mesaj")
        tree.column("time", width=130, anchor="w", stretch=False)
        tree.column("level", width=70, anchor="center", stretch=False)
        tree.column("source", width=200, anchor="w", stretch=False)
        tree.column("message", width=780, anchor="w", stretch=True)

        tree.tag_configure("INFO", foreground="#1f2937")
        tree.tag_configure("OK", foreground="#15803d")
        tree.tag_configure("WARN", foreground="#b45309")
        tree.tag_configure("ERROR", foreground="#b91c1c")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.event_tree = tree

    def _log_event(self, level: str, source: str, message: str) -> None:
        """Olay Günlüğü tabına bir satır ekler. Her thread'den çağrılabilir."""
        ts = time.time()
        with self._event_log_lock:
            self._event_log_pending.append((ts, level, source, message))

    def _flush_event_log(self) -> None:
        if not self._event_log_pending:
            return
        with self._event_log_lock:
            pending = self._event_log_pending
            self._event_log_pending = []

        tree = self.event_tree
        if tree is None:
            return

        for ts, level, source, message in pending:
            time_text = time.strftime("%H:%M:%S", time.localtime(ts))
            tag = level if level in {"INFO", "OK", "WARN", "ERROR"} else "INFO"
            tree.insert(
                "",
                "end",
                values=(time_text, level, source, message),
                tags=(tag,),
            )
            self._event_log_total += 1

        children = tree.get_children("")
        if len(children) > self._event_log_max:
            to_drop = len(children) - self._event_log_max
            for item in children[:to_drop]:
                tree.delete(item)

        if self.event_autoscroll_var is not None and self.event_autoscroll_var.get():
            last = tree.get_children("")
            if last:
                tree.see(last[-1])

    def _clear_event_log(self) -> None:
        tree = self.event_tree
        if tree is None:
            return
        for item in tree.get_children(""):
            tree.delete(item)
        self._event_log_total = 0
        self._log_event("INFO", "Panel", "Olay günlüğü temizlendi.")

    def _export_event_log(self) -> None:
        tree = self.event_tree
        if tree is None:
            return
        try:
            from tkinter import filedialog
        except Exception:
            return
        default_name = f"olay_gunlugu_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        path = filedialog.asksaveasfilename(
            title="Olay günlüğünü dışa aktar",
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[("Metin dosyası", "*.txt"), ("Tümü", "*.*")],
        )
        if not path:
            return
        lines: list[str] = []
        for item in tree.get_children(""):
            vals = tree.item(item, "values")
            if len(vals) >= 4:
                lines.append(
                    f"{vals[0]}  [{vals[1]:<5}]  {vals[2]:<30}  {vals[3]}"
                )
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            self._log_event("OK", "Panel", f"Olay günlüğü dışa aktarıldı: {path}")
        except Exception as ex:
            self._log_event("ERROR", "Panel", f"Dışa aktarma hatası: {ex}")

    # ----------------------------------------------------------- log ui ----

    def _ensure_log_window(self, runtime_key: str, title: str) -> None:
        existing = self.log_windows.get(runtime_key)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_set()
            return
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("980x520")
        win.minsize(700, 400)
        header = ttk.Frame(win, padding=8)
        header.pack(fill=tk.X)
        ttk.Label(header, text=title, font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        ttk.Button(
            header,
            text="Temizle",
            command=lambda: self._clear_log(runtime_key),
            style="TButton",
            width=10,
        ).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(
            header,
            text="Yenile",
            command=lambda: self._refresh_log_window(runtime_key),
            style="TButton",
            width=10,
        ).pack(side=tk.RIGHT, padx=(4, 0))
        text = scrolledtext.ScrolledText(
            win, wrap=tk.NONE, font=("Consolas", 9), bg="#0f172a", fg="#e2e8f0"
        )
        text.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        text.configure(state=tk.DISABLED)
        self.log_windows[runtime_key] = win
        self.log_text_widgets[runtime_key] = text
        win.protocol(
            "WM_DELETE_WINDOW", lambda: self._close_log_window(runtime_key)
        )
        self._refresh_log_window(runtime_key)

    def _close_log_window(self, runtime_key: str) -> None:
        win = self.log_windows.pop(runtime_key, None)
        self.log_text_widgets.pop(runtime_key, None)
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass

    def _refresh_log_window(self, runtime_key: str) -> None:
        widget = self.log_text_widgets.get(runtime_key)
        if widget is None:
            return
        rt = self.runtimes.get(runtime_key)
        content = "\n".join(rt.logs) if rt else ""
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, content)
        widget.see(tk.END)
        widget.configure(state=tk.DISABLED)

    def _clear_log(self, runtime_key: str) -> None:
        rt = self.runtimes.get(runtime_key)
        if rt is not None:
            rt.logs.clear()
        self._refresh_log_window(runtime_key)

    def _schedule_log_refresh(self, runtime_key: str) -> None:
        self.root.after(0, lambda: self._refresh_log_window(runtime_key))

    # ----------------------------------------------------- status & info ---

    def _set_action_info(self, text: str, is_error: bool = False) -> None:
        color = PALETTE["bad"] if is_error else PALETTE["ok"]
        self.action_info.configure(text=text, fg=color)
        # Basligin yanindaki nokta da durumu yansitsin.
        if getattr(self, "status_dot", None) is not None:
            self.status_dot.configure(fg=color)
        if text and text != self._last_action_info_text:
            self._last_action_info_text = text
            level, source, message = self._classify_action_info(text, is_error)
            self._log_event(level, source, message)

    def _set_action_info_threadsafe(self, text: str, is_error: bool = False) -> None:
        self.root.after(0, lambda: self._set_action_info(text, is_error))

    @staticmethod
    def _classify_action_info(text: str, is_error: bool) -> tuple[str, str, str]:
        """Durum satirini (servis, seviye, mesaj) olarak ayristirir."""
        if is_error:
            level = "ERROR"
        elif any(kw in text.lower() for kw in ("başlatıldı", "durduruldu", "yeniden başlatıldı", "bitti", "tamamlandı")):
            level = "OK"
        elif any(kw in text.lower() for kw in ("uyarı", "warn", "durdurulamadı", "başlatılamadı", "hata")):
            level = "WARN"
        else:
            level = "INFO"
        source = "Panel"
        message = text
        if ":" in text:
            head, _, tail = text.partition(":")
            head = head.strip()
            if 1 <= len(head) <= 40 and len(head.split()) <= 6:
                source = head
                message = tail.strip() or text
        return level, source, message

    def _poll_status(self) -> None:
        status_payload: dict[str, tuple[str, str]] = {}
        windows_names = [
            svc.windows_service_name
            for svc in self.all_services
            if svc.service_type == "windows_service"
        ]
        windows_states = get_windows_services_state(windows_names)

        for svc in self.all_services:
            healthy = "UP" if is_port_open(svc.health_host, svc.health_port) else "DOWN"
            if svc.service_type == "windows_service":
                state = windows_states.get(svc.windows_service_name, "")
                if not state:
                    state = "RUNNING_EXTERNAL" if healthy == "UP" else "NOT_FOUND"
            else:
                rt = self.runtimes.get(svc.name)
                process = rt.process if rt else None
                pending = bool(rt and rt.pending)
                if pending:
                    if rt and getattr(rt, "pending_action", None) == "stop":
                        state = "STOP_PENDING"
                    else:
                        state = "START_PENDING"
                elif process is not None and process.poll() is None:
                    state = "RUNNING"
                else:
                    state = "RUNNING_EXTERNAL" if healthy == "UP" else "STOPPED"
            status_payload[svc.name] = (state, healthy)

        self._detect_and_log_state_changes(status_payload)
        self.status_queue = [status_payload]

    def _detect_and_log_state_changes(
        self, current: dict[str, tuple[str, str]]
    ) -> None:
        prev = self._last_state_snapshot
        for svc_name, (state, healthy) in current.items():
            prev_state, prev_health = prev.get(svc_name, ("", ""))
            if prev_state == "" and prev_health == "":
                continue
            if state != prev_state:
                level = "OK" if state in {"RUNNING", "RUNNING_EXTERNAL"} else (
                    "WARN" if state == "STOPPED" else "INFO"
                )
                if state == "NOT_FOUND":
                    level = "ERROR"
                self._log_event(
                    level,
                    svc_name,
                    f"Durum: {prev_state or '-'} → {state}",
                )
            if healthy != prev_health:
                if healthy == "UP":
                    self._log_event("OK", svc_name, "Sağlık: erişilebilir.")
                else:
                    self._log_event("WARN", svc_name, "Sağlık: erişilemiyor.")
        self._last_state_snapshot = dict(current)

    def _start_status_worker(self) -> None:
        def _worker() -> None:
            while not self._stop_event.is_set():
                try:
                    self._poll_status()
                except Exception:
                    pass
                self._stop_event.wait(REFRESH_MS / 1000)

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_status_updates(self) -> None:
        latest = self.status_queue[-1] if self.status_queue else None
        if latest:
            for svc_name, (state, healthy) in latest.items():
                row = self.rows.get(svc_name)
                if not row:
                    continue
                state_lbl: tk.Label = row["state"]
                health_lbl: tk.Label = row["health"]
                cfg: ServiceConfig = row["cfg"]
                state_text, state_color = self._format_state(state)
                health_text, health_color = self._format_health(
                    healthy, cfg.health_host, cfg.health_port
                )
                state_lbl.configure(text=state_text, fg=state_color)
                health_lbl.configure(text=health_text, fg=health_color)
            self.status_queue.clear()
        self._flush_event_log()
        self.root.after(250, self._apply_status_updates)

    @staticmethod
    def _format_state(state: str) -> tuple[str, str]:
        normalized = state.upper().strip()
        if normalized == "RUNNING":
            return "● Çalışıyor", PALETTE["ok"]
        if normalized == "RUNNING_EXTERNAL":
            return "● Çalışıyor (dış)", PALETTE["ok"]
        if normalized == "STOPPED":
            return "● Durdu", PALETTE["warn"]
        if normalized == "STOP_PENDING":
            return "◌ Durduruluyor…", PALETTE["warn"]
        if normalized in {"START_PENDING", "CONTINUE_PENDING"}:
            return "◌ Başlatılıyor…", PALETTE["info"]
        if normalized in {"PAUSED", "PAUSE_PENDING"}:
            return "◍ Duraklatıldı", "#7c3aed"
        if normalized == "NOT_FOUND":
            return "○ Servis bulunamadı", PALETTE["bad"]
        return f"● {normalized.title()}", PALETTE["muted"]

    @staticmethod
    def _format_health(health: str, host: str, port: int) -> tuple[str, str]:
        if health == "UP":
            return f"● Erişilebilir · {host}:{port}", PALETTE["ok"]
        if port <= 0:
            return "○ kontrol yok", PALETTE["muted"]
        return f"● Erişilemiyor · {host}:{port}", PALETTE["bad"]

    @staticmethod
    def _friendly_service_type(service_type: str) -> str:
        if service_type == "windows_service":
            return "Windows Servisi"
        if service_type == "process":
            return "Uygulama Prosesi"
        return service_type

    # --------------------------------------------------------------- quit ---

    def _on_close(self) -> None:
        self._stop_event.set()
        running = [
            rt for rt in self.runtimes.values() if rt.process and rt.process.poll() is None
        ]
        if running:
            answer = messagebox.askyesnocancel(
                "Kapat",
                f"{len(running)} süreç hâlâ çalışıyor. Paneli kapatırken bunları durdurayım mı?",
            )
            if answer is None:
                return
            if answer:
                for rt in running:
                    if rt.process and rt.process.poll() is None:
                        try:
                            _kill_process_tree(rt.process.pid)
                        except Exception:
                            pass
        time.sleep(0.15)
        try:
            self.root.destroy()
        except Exception:
            pass


def _ensure_config_file() -> None:
    """Config dosyasi yoksa .example.json template'ini kopyala.

    Gercek config dosyasi `.gitignore`'da; secret degerleri commit'e
    sizmasin diye sadece `.example.json` repo'da. Ilk calistirmada
    operator dosyayi gormez ise template'i kopyalayip uyari verir.
    """
    if CONFIG_FILE.exists():
        return
    if not CONFIG_EXAMPLE_FILE.exists():
        raise FileNotFoundError(
            f"Config dosyasi yok: {CONFIG_FILE}\n"
            f"Ornek template'i de bulunamadi: {CONFIG_EXAMPLE_FILE}\n"
            "Repo'dan service_control_panel.config.example.json'i kopyalayin."
        )
    import shutil

    shutil.copyfile(CONFIG_EXAMPLE_FILE, CONFIG_FILE)
    print(
        f"[panel] Config dosyasi olusturuldu: {CONFIG_FILE}\n"
        f"        Template kullanildi: {CONFIG_EXAMPLE_FILE.name}\n"
        "        ONEMLI: INTERNAL_SERVICE_TOKEN ve diger placeholder degerleri\n"
        "        production icin .env'inizdeki gercek degerlere gore guncelleyin."
    )


def main() -> None:
    _ensure_config_file()
    services, gateways, backend_settings = read_config()
    root = tk.Tk()
    app = ServiceControlPanel(root, services, gateways, backend_settings)
    _ = app
    root.mainloop()


if __name__ == "__main__":
    main()
