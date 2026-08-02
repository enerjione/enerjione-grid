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

GUVEN SINIRI — NEDEN COMPOSE GOVDESI KABUL EDILMIYOR
-----------------------------------------------------
Bu ajan compose YAML'ini KENDISI uretir. Backend yalnizca kucuk bir SKALER
PARAMETRE kumesi gonderir (imaj, token, URL'ler, portlar) ve her biri kati
bir regex'ten gecer. Container'dan gelen serbest metin compose ASLA
calistirilmaz.

Onceki tasarim compose govdesini aynen alip bir regex KARA LISTESI ile
suzuyordu (privileged, docker.sock, host mount...). Kara liste YAML'in
esnekligi karsisinda yetersizdi; su varyantlarin HEPSI suzgecten geciyordu:

    volumes:                      # uzun-form bind — "- /etc" desenine uymaz
      - type: bind
        source: /etc
        target: /host-etc
      - /run/docker.sock:/var/run/docker.sock   # /var/run DEGIL -> uymaz
    volumes:                      # named volume ile host koku
      hostroot:
        driver: local
        driver_opts: { type: none, device: /, o: bind }
    security_opt: ["apparmor:unconfined", "seccomp:unconfined"]
    cgroup: host
    build: { context: / }
    privileged: yes               # YAML 1.1 bool; "true" desenine uymaz

Bunlarin her biri host'ta root'a cikis demekti. Allowlist'te bu sinif hatalar
yapisal olarak imkansiz: sablon sabittir, yalnizca dogrulanmis skalerler
yerine konur.

FORBIDDEN_PATTERNS artik bir savunma degil, KENDI cikitimiza karsi son bir
saglik kontrolu (sablon degisikliginde kazayla tehlikeli bir alan eklenmesin).

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
import time
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

# Uretilen compose metni ust siniri — sablon sabit oldugu icin bu yalnizca
# bir saglik kontrolu (parametreler sismis olmasin).
MAX_COMPOSE_BYTES = 64 * 1024

# --- Parametre allowlist'i --------------------------------------------------
# Her deger cifte tirnakli bir YAML skalerinin ICINE konuyor. Bu yuzden
# regex'ler tirnak, ters bolu, yeni satir ve `$` gibi kacis/enterpolasyon
# karakterlerini KESINLIKLE disarida birakmali — aksi halde bir deger
# skalerden tasip yeni bir YAML alani tanimlayabilirdi.
#
# Docker imaj referansi: [registry/]repo[:tag][@sha256:...]
IMAGE_RE = re.compile(
    r"^[a-z0-9]([a-z0-9._\-]*[a-z0-9])?"           # ilk bilesen (registry veya repo)
    r"(:[0-9]+)?"                                   # opsiyonel registry portu
    r"(/[a-z0-9]([a-z0-9._\-]*[a-z0-9])?)*"         # yol bilesenleri
    r"(:[A-Za-z0-9._\-]{1,128})?"                   # tag
    r"(@sha256:[a-f0-9]{64})?$"                     # digest
)
# Gateway token: backend uretiyor (secrets.token_urlsafe benzeri).
TOKEN_RE = re.compile(r"^[A-Za-z0-9._~\-]{16,255}$")
# http(s) backend URL — kullanici girebilir, bu yuzden kati.
BACKEND_URL_RE = re.compile(r"^https?://[A-Za-z0-9._\-]{1,253}(:[0-9]{1,5})?(/[A-Za-z0-9._\-/]*)?$")
# nats://[user[:pass]@]host[:port]
NATS_URL_RE = re.compile(
    r"^nats://([A-Za-z0-9._~\-]{1,64}(:[A-Za-z0-9._~\-]{1,128})?@)?"
    r"[A-Za-z0-9._\-]{1,253}(:[0-9]{1,5})?/?$"
)
# Gateway adi UI'dan gelen serbest metin: tirnak/kontrol karakteri YOK.
NAME_RE = re.compile(r"^[^\"'\\\r\n\t$`]{0,120}$")
ALLOWED_APP_ENVIRONMENTS = ("development", "staging", "production")

# Isteklerde kabul edilen parametre anahtarlari. Fazlasi REDDEDILIR (bilinmeyen
# anahtar = protokol uyusmazligi veya kotu niyet; sessizce yutmuyoruz).
ALLOWED_PARAM_KEYS = frozenset({
    "image", "token", "backend_url", "nats_url", "host_port",
    "app_environment", "initiating_port_base", "initiating_port_count",
})

# Compose sablonu — apps/backend-api/app/services/gateway_compose.py icindeki
# `_COMPOSE_TEMPLATE` ile BIREBIR AYNI olmali. "Baska cihaza kur" akisinda
# kullanici backend'in urettigi dosyayi indiriyor; iki taraf ayrisirsa ayni
# gateway iki farkli sekilde kurulur.
#
# Ikisini birbirine baglayan test:
#   apps/backend-api/tests/test_gateway_agent_compose_parity.py
# Sablonu burada degistirirsen orayi da degistir; test aksi halde kirmizi olur.
COMPOSE_TEMPLATE = """\
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

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Z0-9_]+)\s*\}\}")

# Imaj cekme uzun surebilir (ilk kurulumda ~200 MB).
PULL_TIMEOUT_SEC = 900
UP_TIMEOUT_SEC = 300
DOWN_TIMEOUT_SEC = 120
DOCKER_QUERY_TIMEOUT_SEC = 30
# Kayit defteri sorgusu AG uzerinden; yerel docker sorgusundan uzun tutuluyor
# ama rapor turunu kilitlemeyecek kadar kisa.
REMOTE_DIGEST_TIMEOUT_SEC = 25

# KENDI cikitimiza karsi son saglik kontrolu. Artik bir savunma hatti DEGIL
# (compose'u biz uretiyoruz, disardan metin almiyoruz); amaci sablona kazayla
# tehlikeli bir alan eklenmesini yakalamak. Bu listede bir sey tetiklenirse
# hata bizdedir: COMPOSE_TEMPLATE'e bakilmali.
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
def _open_dir_nofollow(path: str, mode: int = 0o750) -> int:
    """Dizini symlink TAKIP ETMEDEN olustur/dogrula; izinleri fd uzerinden ver.

    NEDEN: `_write_json` yalnizca yolun SON bilesenini koruyor. ARCHIVE_DIR
    (= <STATE_DIR>/archive) backend container'inin yazabildigi paylasilan
    dizinin ICINDE; container onu yeniden adlandirip yerine symlink
    birakabiliyor:

        mv .../archive .../.a && ln -s /etc .../archive

    `os.makedirs(exist_ok=True)` symlink'i "zaten dizin" sayip sessizce
    geciyor ve ardindan root, arsiv dosyasini SALDIRGANIN sectigi dizine
    yaziyor. Ayni desenin e1-netd'deki hali (`os.chmod(ARCHIVE_DIR, ...)`)
    daha da agirdi: root olarak istenen host dizininin iznini degistiriyordu.

    Koruma: os.mkdir (varsa EEXIST) + O_NOFOLLOW (symlink ise ELOOP) +
    O_DIRECTORY (dizin degilse ENOTDIR) + fchmod (yola degil fd'ye).
    Donen fd `dir_fd` olarak kullanilmali; cagiran taraf KAPATIR.
    """
    try:
        os.mkdir(path, mode)
    except FileExistsError:
        pass
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fchmod(fd, mode)
    except (AttributeError, PermissionError, OSError):
        pass
    return fd


def _write_json(
    path: str, payload: dict, mode: int = 0o640, dir_fd: int | None = None
) -> None:
    """Atomik yaz: once .tmp, sonra rename. Backend yarim dosya okumasin.

    `dir_fd` verilirse `path` bir DOSYA ADIDIR ve tum islemler o dizin
    tanimlayicisina gore yapilir; boylece yol uzerindeki dizinler dogrulama
    ile yazma arasinda degistirilemez (bkz. `_open_dir_nofollow`).

    SYMLINK TAKIBI KAPALI — bu bir YETKI SINIRI.
    ---------------------------------------------
    `.tmp` yolu backend container'inin (uid 10001) YAZABILDIGI paylasilan
    dizindedir; bu ajan ise ROOT olarak calisir. Duz `open(tmp, "w")`
    kullanildiginda container oraya onceden bir symlink birakip root'a
    istedigi host dosyasini truncate + uzerine yazdirabiliyordu.

    Somut saldiri: container icinde
        ln -s /etc/systemd/system/e1-rad-report.service <state_dir>/state.json.tmp
    30 saniye icinde timer root olarak kosar, symlink'i TAKIP eder ve unit
    dosyasini JSON ile ezer. Sonraki boot'ta sure-dolunca-kapatma zorlayicisi
    OLU olur — yani ozelligin tek guvenlik garantisi sessizce devre disi kalir.
    Ayni primitif /etc/shadow, /etc/sudoers.d/* ya da /etc/cron.d icin de
    kullanilabilir; container'daki cap_drop/no-new-privileges/read_only
    sertlestirmesinin TAMAMI bu tek satirdan asiliyordu. `os.replace` sonrasi
    symlink kayboldugu icin iz de birakmiyordu.

    Koruma uc katmanli:
      * O_NOFOLLOW : son bilesen symlink ise acmayi REDDEDER
      * O_EXCL     : dosya zaten varsa reddeder (onceden konmus tuzak)
      * fchmod/fchown: izinler YOLA degil ACIK FD'ye uygulanir; acma ile
        izin verme arasindaki yaris penceresi kapanir
    Bayat bir .tmp kalmissa once silinir — symlink'i silmek hedefe DOKUNMAZ.
    """
    tmp = f"{path}.tmp"
    # Bayat/tuzak .tmp temizligi. Symlink'i unlink etmek yalnizca link'i siler.
    try:
        if dir_fd is None:
            os.unlink(tmp)
        else:
            os.unlink(tmp, dir_fd=dir_fd)
    except (FileNotFoundError, OSError):
        pass
    # O_NOFOLLOW Windows'ta yok; gelistirici makinesinde de import edilebilsin.
    _NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
    open_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW
    if dir_fd is None:
        fd = os.open(tmp, open_flags, mode)
    else:
        fd = os.open(tmp, open_flags, mode, dir_fd=dir_fd)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
        try:
            os.fchmod(fh.fileno(), mode)
            # Grup hedef dizinden alinir; dizin fd'si varsa yolu tekrar
            # cozmek yerine onu kullan (yeni yaris penceresi acmayalim).
            gid = os.fstat(dir_fd).st_gid if dir_fd is not None else os.stat(STATE_DIR).st_gid
            os.fchown(fh.fileno(), 0, gid)
        except (AttributeError, PermissionError, OSError):
            pass
    if dir_fd is None:
        os.replace(tmp, path)
    else:
        os.replace(tmp, path, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)


def _read_json(path: str) -> dict | None:
    """Symlink TAKIP ETMEDEN okur.

    Yazma tarafi korundu ama okuma da onemli: container paylasilan dizine
    `/etc/shadow`a isaret eden bir symlink birakirsa, root ajan onu okuyup
    icerigini durum/rapor dosyasina (container'in OKUYABILDIGI) yansitabilir
    — yani dosya SIZDIRMA primitifi olurdu.
    """
    try:
        _NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
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


# Uzak digest sorgusu AGDAN gecer; her rapor turunda sormamak icin
# onbelleklenir. `report` dakikada birkac kez kosabiliyor ve kayit defterine
# o siklikta gitmenin karsiligi yok — yeni imaj dakikalar icinde degil,
# gunler icinde cikiyor.
_REMOTE_DIGEST_TTL_SEC = 900.0
_remote_digest_cache: dict = {}


def _local_digest(image: str) -> str:
    """Calisan imajin kayit defteri digest'i. Bulunamazsa bos string.

    `RepoDigests[0]` "repo@sha256:..." bicimindedir; yalnizca sha kismi
    dondurulur ki uzak digest ile dogrudan karsilastirilabilsin.
    """
    docker = shutil.which("docker")
    if not docker or not image:
        return ""
    rc, out = _run(
        [docker, "image", "inspect", image, "--format", "{{index .RepoDigests 0}}"],
        DOCKER_QUERY_TIMEOUT_SEC,
    )
    if rc != 0:
        return ""
    ham = out.strip()
    return ham.split("@", 1)[1] if "@" in ham else ""


def _remote_digest(image: str) -> str:
    """Kayit defterindeki etiketin MANIFEST LISTESI digest'i.

    NEDEN `buildx imagetools`, `manifest inspect` DEGIL:
      `docker manifest inspect -v` cok mimarili bir imajda ALT platform
      digest'lerini dondurur (linux/amd64 + attestation). Onu yerel
      `RepoDigests` ile karsilastirmak FARKLI SEVIYELERI karsilastirmak
      olur ve HER ZAMAN farkli cikar — yani sistem surekli "guncelleme var"
      derdi ve tum kullanicilara bos bildirim yagardi.
      `buildx imagetools inspect --format '{{.Manifest.Digest}}'` ust
      seviye liste digest'ini verir ve `RepoDigests` ile BIREBIR eslesir
      (cihazda dogrulandi).
    """
    docker = shutil.which("docker")
    if not docker or not image:
        return ""
    simdi = time.monotonic()
    onbellek = _remote_digest_cache.get(image)
    if onbellek and (simdi - onbellek[1]) < _REMOTE_DIGEST_TTL_SEC:
        return onbellek[0]
    rc, out = _run(
        [docker, "buildx", "imagetools", "inspect", image,
         "--format", "{{.Manifest.Digest}}"],
        REMOTE_DIGEST_TIMEOUT_SEC,
    )
    deger = out.strip() if rc == 0 and out.strip().startswith("sha256:") else ""
    # Basarisiz sorgu da onbelleklenir: ag yoksa her turda 30 saniye
    # beklemeyelim. Bos deger "bilinmiyor" demek, "guncel" demek DEGIL.
    _remote_digest_cache[image] = (deger, simdi)
    return deger


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
        # Guncelleme kontrolu. `update_available` UCUNCU BIR DURUM tasir:
        # None = BILINMIYOR (kayit defterine ulasilamadi). False ile ayni
        # sayilmamali — "guncel" demek, sormadan verilmis bir iddia olurdu.
        yerel = _local_digest(info.get("image") or "")
        uzak = _remote_digest(info.get("image") or "")
        info["image_digest"] = yerel
        info["remote_digest"] = uzak
        info["update_available"] = (
            None if (not yerel or not uzak) else (yerel != uzak)
        )
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
    dir_fd = None
    try:
        # Symlink takip ETMEDEN ac: ARCHIVE_DIR container'in yazabildigi
        # paylasilan dizinin icinde (bkz. _open_dir_nofollow).
        dir_fd = _open_dir_nofollow(ARCHIVE_DIR, 0o750)
        stamp = _now_iso().replace(":", "").replace("-", "")
        name = f"{stamp}-{str(raw.get('id', 'unknown'))[:12]}.json"
        # Sirlar arsivde tutulmaz: params.token gateway kimligidir, params
        # icindeki nats_url de parola gomulu gelir. Ikisi de maskelenir;
        # kalan parametreler teshis icin saklanir.
        redacted = {k: v for k, v in raw.items() if k != "compose"}
        raw_params = raw.get("params")
        if isinstance(raw_params, dict):
            safe_params = dict(raw_params)
            if "token" in safe_params:
                safe_params["token"] = "***"
            if "nats_url" in safe_params:
                # Parolayi at, host'u birak (teshiste ise yarar).
                safe_params["nats_url"] = re.sub(
                    r"://[^@/]*@", "://***@", str(safe_params["nats_url"])
                )
            redacted["params"] = safe_params
        _write_json(name, {"request": redacted, "result": result}, dir_fd=dir_fd)
    except OSError as exc:
        _log(f"arsivleme basarisiz: {exc}")
    finally:
        if dir_fd is not None:
            os.close(dir_fd)
    try:
        os.unlink(REQUEST_PATH)
    except OSError:
        pass


def _require_int(params: dict, key: str, low: int, high: int, default: int | None = None) -> int:
    """params[key]'i tam sayi olarak al ve aralik kontrolu yap.

    bool KASITLA reddedilir: Python'da bool int'in alt sinifidir ve
    `True` sessizce 1 olarak gecerdi.
    """
    raw = params.get(key, default)
    if raw is None:
        raise ValueError(f"{key} eksik")
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{key} tam sayi olmali")
    if not (low <= raw <= high):
        raise ValueError(f"{key} aralik disi ({low}-{high}): {raw}")
    return raw


def _require_str(params: dict, key: str, pattern: "re.Pattern[str]", label: str) -> str:
    raw = params.get(key)
    if not isinstance(raw, str):
        raise ValueError(f"{key} metin olmali")
    value = raw.strip()
    if not pattern.match(value):
        raise ValueError(f"{key} gecersiz ({label})")
    return value


def _validate_params(params: object) -> dict:
    """Kurulum parametrelerini allowlist ile dogrula. Hata -> ValueError.

    Sadece ALLOWED_PARAM_KEYS kabul edilir; bilinmeyen anahtar hata verir
    (protokol uyusmazligini sessizce yutmak, ileride eklenen bir alanin
    dogrulanmadan sablona girmesi riskini yaratirdi).
    """
    if not isinstance(params, dict):
        raise ValueError("params nesnesi eksik")
    unknown = set(params) - ALLOWED_PARAM_KEYS
    if unknown:
        raise ValueError(f"bilinmeyen parametre(ler): {sorted(unknown)}")

    out: dict = {
        "image": _require_str(params, "image", IMAGE_RE, "docker imaj referansi"),
        "token": _require_str(params, "token", TOKEN_RE, "16-255 guvenli karakter"),
        "backend_url": _require_str(params, "backend_url", BACKEND_URL_RE, "http(s) URL"),
        "nats_url": _require_str(params, "nats_url", NATS_URL_RE, "nats:// URL"),
        "host_port": _require_int(params, "host_port", 1, 65535, 8020),
        "initiating_port_base": _require_int(params, "initiating_port_base", 1024, 65000, 20100),
        "initiating_port_count": _require_int(params, "initiating_port_count", 0, 1000, 0),
    }
    env = str(params.get("app_environment") or "production").strip()
    if env not in ALLOWED_APP_ENVIRONMENTS:
        raise ValueError(f"app_environment gecersiz: {env!r}")
    out["app_environment"] = env

    last = out["initiating_port_base"] + out["initiating_port_count"] - 1
    if out["initiating_port_count"] > 0 and last > 65535:
        raise ValueError(f"initiating port bloku tasiyor (son port {last})")
    return out


def _initiating_ports_block(base: int, count: int) -> str:
    """compose `ports:` bloguna eklenecek initiating port satiri.

    gateway_compose.py `_build_initiating_ports_block` ile AYNI mantik.
    count=0 -> bos satir (sadece health portu publish edilir).
    """
    if count <= 0:
        return ""
    last = base + count - 1
    container_last = 20100 + count - 1
    return f'      - "{base}-{last}:20100-{container_last}"'


def render_compose(code: str, name: str, params: dict) -> str:
    """Dogrulanmis parametrelerden compose YAML'i uret.

    Bu fonksiyon compose'un TEK kaynagidir; disardan YAML kabul edilmez.
    """
    values = {
        "GATEWAY_CODE": code,
        "GATEWAY_CODE_LOWER": code.lower(),
        "GATEWAY_TOKEN": params["token"],
        "GATEWAY_NAME": name,
        "BACKEND_API_URL": params["backend_url"].rstrip("/"),
        "NATS_URL": params["nats_url"],
        "HOST_HEALTH_PORT": str(params["host_port"]),
        "IMAGE": params["image"],
        "APP_ENVIRONMENT": params["app_environment"],
        "INITIATING_PORTS_BLOCK": _initiating_ports_block(
            params["initiating_port_base"], params["initiating_port_count"]
        ),
    }

    def _sub(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in values:
            raise ValueError(f"sablonda bilinmeyen yer tutucu: {key}")
        return values[key]

    body = _PLACEHOLDER_RE.sub(_sub, COMPOSE_TEMPLATE)

    # Kendi cikitimiza karsi saglik kontrolu (bkz. FORBIDDEN_PATTERNS).
    # Buraya dusmek sablonda bir hata oldugu anlamina gelir.
    if len(body.encode("utf-8")) > MAX_COMPOSE_BYTES:
        raise ValueError("uretilen compose beklenmedik sekilde buyuk")
    for pattern, label in FORBIDDEN_PATTERNS:
        if re.search(pattern, body):
            raise ValueError(f"URETILEN compose tehlikeli alan iceriyor: {label} (sablon hatasi)")
    return body


def _validate(request: dict) -> dict:
    """Istegi ajanin kendi kurallariyla dogrula. Hata -> ValueError."""
    action = str(request.get("action") or "").strip()
    if action not in ("install", "remove", "restart", "update"):
        raise ValueError(f"desteklenmeyen aksiyon: {action or '(bos)'}")

    code = str(request.get("code") or "").strip()
    if not CODE_RE.match(code):
        raise ValueError("gecersiz gateway kodu")

    name = str(request.get("name") or "")[:120]
    if not NAME_RE.match(name):
        raise ValueError("gateway adi gecersiz karakter iceriyor")

    clean = {"action": action, "code": code, "name": name}

    if action == "install":
        # Serbest metin compose ARTIK KABUL EDILMIYOR. Eski surum bir backend
        # bu alani gonderiyorsa net hata ver — sessizce parametresiz devam
        # etmek "kurulum takildi" gibi gorunurdu.
        if "compose" in request:
            raise ValueError(
                "bu ajan compose govdesi kabul etmiyor; backend guncel degil "
                "(update.sh ile backend + ajan birlikte guncellenmeli)"
            )
        clean["params"] = _validate_params(request.get("params"))

    return clean


def _write_status(payload: dict) -> None:
    payload.setdefault("at", _now_iso())
    _write_json(STATUS_PATH, payload)


def _do_install(req: dict, compose_cmd: list[str]) -> dict:
    code = req["code"]
    directory = os.path.join(GATEWAY_ROOT, code)
    path = _compose_path(code)
    project = _project_name(code)

    # compose'u BIZ uretiyoruz (dogrulanmis parametrelerden); container'dan
    # gelen bir YAML calistirilmaz.
    compose_body = render_compose(code, req.get("name") or "", req["params"])

    os.makedirs(directory, mode=0o750, exist_ok=True)
    # compose icinde gateway token'i var — sadece root okuyabilsin.
    fd = os.open(f"{path}.tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(compose_body)
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


def _do_update(req: dict, compose_cmd: list[str]) -> dict:
    """Yeni imaji cek ve container'i yeniden olustur.

    `restart`ten FARKI: restart ayni imajla yeniden baslatir, bu once
    `pull` yapar. Compose dosyasi DEGISMEZ — imaj etiketi ayni, degisen
    sey etiketin isaret ettigi digest.

    Cekme BASARISIZ olursa container'a DOKUNULMAZ: yarim bir guncelleme
    yerine calisan eski surumde kalmak dogru davranis.
    """
    code = req["code"]
    path = _compose_path(code)
    if not os.path.isfile(path):
        return {"ok": False, "stage": "update", "message": "gateway bu cihazda kurulu degil", "detail": ""}
    project = _project_name(code)

    # ASAMALAR BILDIRILIYOR (install akisindaki gibi). Onceden `update`
    # yalnizca EN SONDA tek bir sonuc yaziyordu: arayuz istegi gonderdikten
    # sonra is bitene kadar hicbir sey goremiyor, operator "basti mi,
    # basmadi mi" diye bakiyordu.
    #
    # Sure onemsiz degil: imaj cekme saha kosullarinda (4G) dakikalar
    # surebilir. En uzun ve en sik hata veren adim `pull` oldugu icin ayri
    # bildirilmesi teshis acisindan da degerli.
    _write_status({"id": req["id"], "action": "update", "code": code,
                   "stage": "pull", "running": True})
    rc, out = _run(compose_cmd + ["-p", project, "-f", path, "pull"], UP_TIMEOUT_SEC)
    if rc != 0:
        return {"ok": False, "stage": "pull", "message": "yeni imaj indirilemedi", "detail": out}

    _write_status({"id": req["id"], "action": "update", "code": code,
                   "stage": "up", "running": True})
    rc, out = _run(compose_cmd + ["-p", project, "-f", path, "up", "-d"], UP_TIMEOUT_SEC)
    if rc != 0:
        return {"ok": False, "stage": "up", "message": "guncel imajla baslatilamadi", "detail": out}

    # Onbellegi dusur: guncelleme sonrasi rapor ESKI sonucu gostermesin.
    _remote_digest_cache.clear()
    return {"ok": True, "stage": "done", "message": "guncellendi", "detail": out}


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
        elif req["action"] == "update":
            result = _do_update(req, compose_cmd)
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
