#!/usr/bin/env python3
"""e1-gwd — EnerjiOne Grid gateway kurulum ajani (host tarafi, root).

Neden ayri bir ajan?
--------------------
Backend container'i non-root (uid 10001) calisir ve Docker daemon'a ERISEMEZ.
"Gateway'i bu cihaza kur" akisi icin container'a /var/run/docker.sock vermek
gerekirdi; docker soketi = host'ta root demektir, yani backend ele gecirilirse
tum makine gider. compose'daki cap_drop/no-new-privileges/read_only
sertlestirmesinin tamami anlamsizlasirdi. Onun yerine e1-netd ile ayni desen:

    backend (container)  --yazar-->  /var/lib/e1-grid/gw/request.json
    e1-gwd (host, root)  --okur -->  dogrular -> docker compose up -d

Container'in tek yetkisi bir JSON dosyasi yazmak. Ajan gelen compose'u KENDI
kurallariyla yeniden dogrular (bkz. FORBIDDEN_PATTERNS): privileged, docker
soketi, host network/pid gibi kacis vektorleri iceren bir compose reddedilir.
Yani backend ele gecirilse bile bu ajan uzerinden host'a cikilamaz.

Kullanim (systemd tarafindan cagrilir):
    e1-gwd.py report   -> state.json'i tazele (timer, 30 sn)
    e1-gwd.py apply    -> request.json'i isle (path unit, dosya degisince)

Kurulan her gateway ayri bir compose projesidir:
    /opt/enerjione-grid/gateways/<code>/docker-compose.yml
    proje adi: e1-gw-<code>
Ana stack'e (docker-compose.yml) DOKUNULMAZ; gateway kaldirilinca ana sistem
etkilenmez.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

# --- Sabitler ---------------------------------------------------------------
STATE_DIR = os.environ.get("E1_GW_STATE_DIR", "/var/lib/e1-grid/gw")
REQUEST_PATH = os.path.join(STATE_DIR, "request.json")
STATE_PATH = os.path.join(STATE_DIR, "state.json")
STATUS_PATH = os.path.join(STATE_DIR, "status.json")
# Islenmis istekler buraya tasinir (audit + tekrar tetiklenmeyi onler).
ARCHIVE_DIR = os.path.join(STATE_DIR, "archive")

# Gateway compose dosyalarinin kok dizini.
GATEWAY_ROOT = os.environ.get("E1_GW_ROOT", "/opt/enerjione-grid/gateways")

SCHEMA_VERSION = 1

# Gateway kodu: dosya yolu ve compose proje adi olarak kullanilacagi icin
# katı. Path traversal ("../") ve bosluk bu regex ile zaten disarida kalir.
CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,49}$")

# compose metni ust siniri — sisip diski doldurmasin.
MAX_COMPOSE_BYTES = 64 * 1024

# Imaj cekme uzun surebilir (ilk kurulumda ~200 MB).
PULL_TIMEOUT_SEC = 900
UP_TIMEOUT_SEC = 300
DOWN_TIMEOUT_SEC = 120
DOCKER_QUERY_TIMEOUT_SEC = 30

# Backend'e guvenmeyen ikinci savunma hatti. Bu desenlerden biri compose'da
# gecerse istek reddedilir — hepsi container'dan host'a cikis vektorudur.
FORBIDDEN_PATTERNS = [
    (r"(?im)^\s*privileged\s*:\s*true", "privileged"),
    (r"(?i)/var/run/docker\.sock", "docker_socket"),
    (r"(?im)^\s*network_mode\s*:\s*[\"']?host", "host_network"),
    (r"(?im)^\s*pid\s*:\s*[\"']?host", "host_pid"),
    (r"(?im)^\s*ipc\s*:\s*[\"']?host", "host_ipc"),
    (r"(?im)^\s*cap_add\s*:", "cap_add"),
    (r"(?im)^\s*devices\s*:", "devices"),
    (r"(?im)^\s*userns_mode\s*:", "userns_mode"),
    # Host kok dizinini veya sistem yollarini mount etme girisimi.
    (r"(?im)^\s*-\s*[\"']?/(etc|root|boot|sys|proc)(/|:)", "host_path_mount"),
    (r"(?im)^\s*-\s*[\"']?/:", "host_root_mount"),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(msg: str) -> None:
    # systemd journal'a duser; stdout yeterli.
    print(f"[e1-gwd] {msg}", flush=True)


# --- Dosya yazma ------------------------------------------------------------
def _write_json(path: str, payload: dict, mode: int = 0o640) -> None:
    """Atomik yaz: once .tmp, sonra rename. Backend yarim dosya okumasin."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, mode)
    # Grup sahipligi backend container uid'sine ayarlanmis dizinden miras alinir.
    try:
        st = os.stat(STATE_DIR)
        os.chown(tmp, 0, st.st_gid)
    except (AttributeError, PermissionError, OSError):
        pass
    os.replace(tmp, path)


def _read_json(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


# --- Docker yardimcilari ----------------------------------------------------
def _compose_cmd() -> list[str] | None:
    """Kullanilabilir compose komutunu bul (v2 plugin > v1 binary)."""
    docker = shutil.which("docker")
    if docker:
        probe = subprocess.run(
            [docker, "compose", "version"],
            capture_output=True,
            text=True,
            timeout=DOCKER_QUERY_TIMEOUT_SEC,
        )
        if probe.returncode == 0:
            return [docker, "compose"]
    legacy = shutil.which("docker-compose")
    if legacy:
        return [legacy]
    return None


def _run(cmd: list[str], timeout: float) -> tuple[int, str]:
    """Komutu calistir; (rc, stdout+stderr son kismi) dondur."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"zaman asimi ({int(timeout)} sn)"
    except OSError as exc:
        return 127, str(exc)
    out = (proc.stdout or "") + (proc.stderr or "")
    # Log kuyrugu: UI'da gosterilecek, sinirli tut.
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return proc.returncode, "\n".join(lines[-40:])


def _project_name(code: str) -> str:
    # Compose proje adi kucuk harf ve [a-z0-9_-] olmali.
    slug = re.sub(r"[^a-z0-9_-]", "-", code.lower())
    return f"e1-gw-{slug}"


def _compose_path(code: str) -> str:
    return os.path.join(GATEWAY_ROOT, code, "docker-compose.yml")


# --- report: kurulu gateway'leri state.json'a yaz ---------------------------
def _installed_codes() -> list[str]:
    try:
        entries = sorted(os.listdir(GATEWAY_ROOT))
    except OSError:
        return []
    return [
        name
        for name in entries
        if CODE_RE.match(name) and os.path.isfile(_compose_path(name))
    ]


def _container_info(code: str) -> dict:
    """Gateway container'inin calisma durumu (docker ps ciktisindan)."""
    docker = shutil.which("docker")
    if not docker:
        return {"state": "unknown"}
    rc, out = _run(
        [
            docker,
            "ps",
            "--all",
            "--filter",
            f"label=com.docker.compose.project={_project_name(code)}",
            "--format",
            "{{.Names}}\t{{.State}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}",
        ],
        DOCKER_QUERY_TIMEOUT_SEC,
    )
    if rc != 0 or not out.strip():
        return {"state": "absent"}
    first = out.splitlines()[0].split("\t")
    while len(first) < 5:
        first.append("")
    return {
        "container": first[0],
        "state": first[1] or "unknown",
        "status": first[2],
        "image": first[3],
        "ports": first[4],
    }


def build_state() -> dict:
    compose = _compose_cmd()
    gateways = []
    for code in _installed_codes():
        meta = _read_json(os.path.join(GATEWAY_ROOT, code, "meta.json")) or {}
        info = _container_info(code)
        info["code"] = code
        info["name"] = meta.get("name")
        info["installed_at"] = meta.get("installed_at")
        gateways.append(info)
    return {
        "schema": SCHEMA_VERSION,
        "updated_at": _now_iso(),
        "docker_available": compose is not None,
        "gateways": gateways,
    }


def cmd_report() -> int:
    os.makedirs(STATE_DIR, exist_ok=True)
    _write_json(STATE_PATH, build_state())
    return 0


# --- apply: request.json'i isle --------------------------------------------
def _archive_request(raw: dict, result: dict) -> None:
    """Islenmis istegi arsive tasi; request.json'i sil."""
    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        stamp = _now_iso().replace(":", "").replace("-", "")
        name = f"{stamp}-{str(raw.get('id', 'unknown'))[:12]}.json"
        # compose govdesi arsivde tutulmaz (token icerir).
        redacted = {k: v for k, v in raw.items() if k != "compose"}
        redacted["compose_bytes"] = len(str(raw.get("compose") or ""))
        _write_json(os.path.join(ARCHIVE_DIR, name), {"request": redacted, "result": result})
    except OSError as exc:
        _log(f"arsivleme basarisiz: {exc}")
    try:
        os.unlink(REQUEST_PATH)
    except OSError:
        pass


def _validate(request: dict) -> dict:
    """Istegi ajanin kendi kurallariyla dogrula. Hata -> ValueError."""
    action = str(request.get("action") or "").strip()
    if action not in ("install", "remove", "restart"):
        raise ValueError(f"desteklenmeyen aksiyon: {action or '(bos)'}")

    code = str(request.get("code") or "").strip()
    if not CODE_RE.match(code):
        raise ValueError("gecersiz gateway kodu")

    clean = {"action": action, "code": code, "name": str(request.get("name") or "")[:255]}

    if action == "install":
        compose = request.get("compose")
        if not isinstance(compose, str) or not compose.strip():
            raise ValueError("compose govdesi bos")
        if len(compose.encode("utf-8")) > MAX_COMPOSE_BYTES:
            raise ValueError("compose govdesi cok buyuk")
        for pattern, label in FORBIDDEN_PATTERNS:
            if re.search(pattern, compose):
                # Backend ele gecirilmis olabilir; sadece reddet ve logla.
                raise ValueError(f"compose reddedildi (yasakli ayar: {label})")
        clean["compose"] = compose

    return clean


def _write_status(payload: dict) -> None:
    payload.setdefault("at", _now_iso())
    _write_json(STATUS_PATH, payload)


def _do_install(req: dict, compose_cmd: list[str]) -> dict:
    code = req["code"]
    directory = os.path.join(GATEWAY_ROOT, code)
    path = _compose_path(code)
    project = _project_name(code)

    os.makedirs(directory, mode=0o750, exist_ok=True)
    # compose icinde gateway token'i var — sadece root okuyabilsin.
    fd = os.open(f"{path}.tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(req["compose"])
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(f"{path}.tmp", path)

    base = compose_cmd + ["-p", project, "-f", path]

    # 1) Docker'in kendi dogrulamasi: gecersiz YAML/alan burada yakalanir.
    _write_status({"id": req["id"], "action": "install", "code": code,
                   "stage": "validate", "running": True})
    rc, out = _run(base + ["config", "-q"], DOCKER_QUERY_TIMEOUT_SEC)
    if rc != 0:
        return {"ok": False, "stage": "validate", "message": "compose dogrulanamadi", "detail": out}

    # 2) Imaji cek — ayri adim, cunku en uzun suren ve en sik hata veren yer.
    _write_status({"id": req["id"], "action": "install", "code": code,
                   "stage": "pull", "running": True})
    rc, out = _run(base + ["pull", "--quiet"], PULL_TIMEOUT_SEC)
    if rc != 0:
        return {"ok": False, "stage": "pull", "message": "imaj indirilemedi", "detail": out}

    # 3) Baslat.
    _write_status({"id": req["id"], "action": "install", "code": code,
                   "stage": "up", "running": True})
    rc, out = _run(base + ["up", "-d", "--remove-orphans"], UP_TIMEOUT_SEC)
    if rc != 0:
        return {"ok": False, "stage": "up", "message": "container baslatilamadi", "detail": out}

    _write_json(
        os.path.join(directory, "meta.json"),
        {"code": code, "name": req.get("name"), "installed_at": _now_iso(), "project": project},
    )
    return {"ok": True, "stage": "done", "message": "gateway calisiyor", "detail": out}


def _do_remove(req: dict, compose_cmd: list[str]) -> dict:
    code = req["code"]
    directory = os.path.join(GATEWAY_ROOT, code)
    path = _compose_path(code)
    project = _project_name(code)

    if not os.path.isfile(path):
        # Zaten yok — kaldirma istegi idempotent olmali.
        return {"ok": True, "stage": "done", "message": "kurulu degil", "detail": ""}

    rc, out = _run(
        compose_cmd + ["-p", project, "-f", path, "down", "--remove-orphans"],
        DOWN_TIMEOUT_SEC,
    )
    if rc != 0:
        return {"ok": False, "stage": "down", "message": "container durdurulamadi", "detail": out}
    try:
        shutil.rmtree(directory)
    except OSError as exc:
        return {"ok": False, "stage": "cleanup", "message": f"dizin silinemedi: {exc}", "detail": out}
    return {"ok": True, "stage": "done", "message": "gateway kaldirildi", "detail": out}


def _do_restart(req: dict, compose_cmd: list[str]) -> dict:
    code = req["code"]
    path = _compose_path(code)
    if not os.path.isfile(path):
        return {"ok": False, "stage": "restart", "message": "gateway bu cihazda kurulu degil", "detail": ""}
    rc, out = _run(
        compose_cmd + ["-p", _project_name(code), "-f", path, "restart"],
        UP_TIMEOUT_SEC,
    )
    if rc != 0:
        return {"ok": False, "stage": "restart", "message": "yeniden baslatilamadi", "detail": out}
    return {"ok": True, "stage": "done", "message": "yeniden baslatildi", "detail": out}


def cmd_apply() -> int:
    raw = _read_json(REQUEST_PATH)
    if raw is None:
        # Path unit dosya silinirken de tetiklenebilir; sessiz cik.
        return 0

    request_id = str(raw.get("id") or "unknown")
    os.makedirs(STATE_DIR, exist_ok=True)

    try:
        req = _validate(raw)
    except ValueError as exc:
        _log(f"istek reddedildi: {exc}")
        result = {"ok": False, "stage": "validate", "message": str(exc), "detail": ""}
        result.update({"id": request_id, "action": raw.get("action"), "code": raw.get("code")})
        _write_status(result)
        _archive_request(raw, result)
        return 1

    req["id"] = request_id
    compose_cmd = _compose_cmd()
    if compose_cmd is None:
        result = {"id": request_id, "action": req["action"], "code": req["code"], "ok": False,
                  "stage": "docker", "message": "docker compose bulunamadi", "detail": ""}
        _write_status(result)
        _archive_request(raw, result)
        return 1

    _log(f"{req['action']} -> {req['code']}")
    try:
        if req["action"] == "install":
            result = _do_install(req, compose_cmd)
        elif req["action"] == "remove":
            result = _do_remove(req, compose_cmd)
        else:
            result = _do_restart(req, compose_cmd)
    except OSError as exc:
        result = {"ok": False, "stage": req["action"], "message": str(exc), "detail": ""}

    result.update({"id": request_id, "action": req["action"], "code": req["code"], "running": False})
    _write_status(result)
    _archive_request(raw, result)
    # Durum degisti; state.json'i hemen tazele ki UI beklemesin.
    try:
        _write_json(STATE_PATH, build_state())
    except OSError:
        pass
    _log(f"sonuc: {'OK' if result.get('ok') else 'HATA'} — {result.get('message')}")
    return 0 if result.get("ok") else 1


def main(argv: list[str]) -> int:
    action = argv[1] if len(argv) > 1 else "report"
    if action == "report":
        return cmd_report()
    if action == "apply":
        return cmd_apply()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
