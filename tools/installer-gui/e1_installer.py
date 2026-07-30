"""EnerjiOne Grid — Saha Kurulum Araci (GUI).

Cihazi ethernet ile baglayip bu araci calistirirsiniz: IP, SSH bilgileri ve
kurulum anahtarlari girilir, "Kurulumu Baslat" denir. Arac SSH ile baglanir,
kurulum dosyasini uretip cihaza gonderir, calistirir ve ciktiyi sagdaki
terminale CANLI akitir.

Neden Tkinter: Python ile birlikte gelir. Saha dizustunde ekstra bir arayuz
kutuphanesi kurmak zorunda kalmayalim. Tek dis bagimlilik paramiko (SSH).

ONEMLI TASARIM NOTLARI
----------------------
* Tk NESNELERINE SADECE ANA IS PARCACIGINDAN dokunulur. SSH isi ayri bir
  thread'de kosar ve satirlari bir Queue'ya birakir; arayuz `after()` ile
  bu kuyrugu bosaltir. Aksi halde Tk rastgele cokerdi.
* Anahtarlar EKRANA YAZILMAZ. Terminal ciktisinda gorunurlerse maskelenir;
  profil kaydinda hic tutulmaz.
* Cihazda PTY istiyoruz: install.sh terminal gorunce ilerleme cubugunu
  cizer. Bunun bedeli ANSI kodlari ve `\\r` ile satir yenileme; ikisi de
  burada cozuluyor (bkz. TerminalView).
"""

from __future__ import annotations

import json
import os
import queue
import re
import socket
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    import paramiko
except ImportError:  # pragma: no cover - kurulum talimati
    raise SystemExit(
        "paramiko bulunamadi. Kurmak icin:\n"
        "    pip install -r tools/installer-gui/requirements.txt"
    )

APP_TITLE = "EnerjiOne Grid — Saha Kurulum Araci"
REPO_SLUG = "enerjione/enerjione-grid"
REMOTE_TMP = "/tmp/e1-kurulum.sh"
APP_DIR = "/opt/enerjione-grid"

# Profil: SIR ICERMEZ. Sadece tekrar tekrar yazmak zorunda kalinmayan alanlar.
PROFILE_PATH = Path(os.environ.get("APPDATA", Path.home())) / "EnerjiOneGrid" / "installer.json"

# SGR/CSI dizileri: "\033[1;33m" gibi. Renkleri metne katmadan atiyoruz.
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

# Surum listesindeki sabit girdiler. Anahtar = ekranda gorunen, deger = ref.
VERSION_LATEST = "En son yayin (onerilen)"
VERSION_EDGE = "Gelistirme surumu (main) — test icin"


def _asset_dir() -> Path:
    """Gorsellerin bulundugu dizin.

    EXE olarak paketlendiginde (PyInstaller --onefile) program gecici bir
    dizine acilir ve `__file__` ORAYI gosterir; varliklar da oraya kopyalanir.
    `sys._MEIPASS` o dizinin yoludur. Kaynaktan calistirildiginda boyle bir
    oznitelik yoktur ve dosyanin yani basina bakariz.
    """
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) / "assets" if base else Path(__file__).resolve().parent / "assets"


def fetch_releases(token: str, limit: int = 15) -> list[str]:
    """Depodaki yayin etiketlerini getirir (yeniden eskiye).

    Depo private oldugu icin istek anahtarla imzalanir. Basarisiz olursa
    bos liste doner — arayuz yine calisir, sadece liste dolmaz. Kurulum
    aracinin surum listesi cekemedi diye kullanilamaz hale gelmesi kotu
    olurdu.
    """
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO_SLUG}/releases?per_page={limit}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "EnerjiOneGrid-Installer",
            **({"Authorization": f"token {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return []
    return [r["tag_name"] for r in data if isinstance(r, dict) and r.get("tag_name")]


# ---------------------------------------------------------------------------
# Kurulum dosyasi uretimi
# ---------------------------------------------------------------------------
# packaging/make-provisioner.sh ile AYNI isi yapar, sadece burada Python ile.
# Ayri tutmak yerine ayni sablonu tekrarlamamiz bilincli: bu arac cihazda
# calismaz, kurulum dizinine bagimli olmasin istiyoruz (tek dosya tasinabilir).
PROVISIONER = r"""#!/usr/bin/env bash
# EnerjiOne Grid — kurulum dosyasi (GUI tarafindan uretildi). GIZLIDIR.
set -euo pipefail

E1_GHCR_TOKEN='{ghcr}'
E1_TAILSCALE_AUTHKEY='{tskey}'
E1_TAILSCALE_TAGS='{tstags}'
E1_REPO_SLUG='{slug}'
E1_CUSTOMER='{customer}'
E1_SITE='{site}'
# Kurulacak surum.
#   E1_REF       : install.sh'in kodu getirecegi ref. BOS ise install.sh
#                  "en son yayin tag'i" diye yorumlar — arac ile cihaz
#                  arasinda surum bilgisi ayrismaz.
#   BOOTSTRAP_REF: install.sh'in KENDISININ indirilecegi ref. Somut olmak
#                  ZORUNDA (bos olsa URL bozulurdu). Belirli bir surum
#                  secildiyse ayni tag, aksi halde main.
E1_REF='{ref}'
BOOTSTRAP_REF='{bootstrap}'
E1_APPLIANCE='{appliance}'
E1_BUILD='{build}'
# Internet yoksa bu ag ile baglanilir (bos = deneme yapilmaz).
E1_WIFI_SSID='{wifi_ssid}'
E1_WIFI_PASSWORD='{wifi_pass}'

if [[ $EUID -ne 0 ]]; then echo "root ile calistirilmali" >&2; exit 1; fi

# 1) Anahtarlari kalici yere yaz — repo disi, kurulum dizini silinse de kalir.
install -d -m 700 /etc/enerjione-grid
( umask 077
  {{
    echo "# EnerjiOne Grid kurulum anahtarlari — GIZLI"
    echo "E1_GHCR_TOKEN=${{E1_GHCR_TOKEN}}"
    [[ -n "$E1_TAILSCALE_AUTHKEY" ]] && echo "E1_TAILSCALE_AUTHKEY=${{E1_TAILSCALE_AUTHKEY}}"
    [[ -n "$E1_TAILSCALE_TAGS" ]]    && echo "E1_TAILSCALE_TAGS=${{E1_TAILSCALE_TAGS}}"
  }} > /etc/enerjione-grid/install.env
)
chmod 600 /etc/enerjione-grid/install.env
echo "  Anahtarlar yazildi: /etc/enerjione-grid/install.env"

# 2) curl yoksa kur
command -v curl >/dev/null 2>&1 || {{
  echo "  curl kuruluyor..."
  DEBIAN_FRONTEND=noninteractive apt-get update -q
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q curl ca-certificates
}}

# 2b) INTERNET — kurulumun tamami buna bagli
# Cihaz ethernet ile kurulumcunun dizustune bagli olabilir ve o baglantida
# INTERNET OLMAYABILIR. Bu durumda `git clone` ve imaj indirme oluyor; hata
# da "depo bulunamadi" gibi yaniltici bir yerde cikiyor. Once burada tespit
# edip, WiFi bilgisi verildiyse cihazi agi baglayip devam ediyoruz.
_internet_var() {{
  curl -fsS --max-time 8 -o /dev/null https://api.github.com 2>/dev/null
}}

if _internet_var; then
  echo "  Internet erisimi: VAR"
else
  echo "  Internet erisimi: YOK"
  if [[ -n "$E1_WIFI_SSID" ]]; then
    echo "  WiFi agina baglaniliyor: ${{E1_WIFI_SSID}}"
    if ! command -v nmcli >/dev/null 2>&1; then
      echo "HATA: nmcli (NetworkManager) yok; WiFi otomatik ayarlanamiyor." >&2
      echo "      Cihaza elle internet verin veya NetworkManager kurun:" >&2
      echo "        sudo apt-get install -y network-manager" >&2
      exit 1
    fi
    # Radyo kapali olabilir (rfkill / nmcli radio off).
    nmcli radio wifi on >/dev/null 2>&1 || true
    # Tarama listesini tazele; nmcli bazen onbellekten eski liste doner ve
    # "ag bulunamadi" der.
    nmcli device wifi rescan >/dev/null 2>&1 || true
    sleep 3
    if nmcli device wifi connect "$E1_WIFI_SSID" \
         ${{E1_WIFI_PASSWORD:+password "$E1_WIFI_PASSWORD"}} >/dev/null 2>&1; then
      echo "  WiFi baglantisi kuruldu."
    else
      echo "HATA: WiFi agina baglanilamadi ($E1_WIFI_SSID)." >&2
      echo "      Ag adi ve parolayi kontrol edin. Gorunen aglar:" >&2
      nmcli -t -f SSID,SIGNAL device wifi list 2>/dev/null | head -10 | sed 's/^/        /' >&2
      exit 1
    fi
    # DHCP + DNS oturana kadar biraz bekle, sonra tekrar dogrula.
    for _ in 1 2 3 4 5 6; do
      _internet_var && break
      sleep 3
    done
    if _internet_var; then
      echo "  Internet erisimi: VAR (WiFi uzerinden)"
    else
      echo "HATA: WiFi baglandi ama internete cikilamiyor." >&2
      echo "      Ag parolali bir portal isteyebilir veya DNS engelli olabilir." >&2
      exit 1
    fi
  else
    echo "HATA: Cihazda internet yok ve WiFi bilgisi verilmedi." >&2
    echo "      Kurulum GitHub'dan indirme yapacagi icin internet ZORUNLU." >&2
    echo "      Kurulum aracindan WiFi ag adi/parolasi girin veya cihaza" >&2
    echo "      kablolu internet verin." >&2
    exit 1
  fi
fi

# 3) install.sh'i private depodan cek
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
URL="https://raw.githubusercontent.com/${{E1_REPO_SLUG}}/${{BOOTSTRAP_REF}}/install.sh"
echo "  Kurulum scripti indiriliyor..."
if ! curl -fsSL -H "Authorization: token ${{E1_GHCR_TOKEN}}" "$URL" -o "$TMP" || [[ ! -s "$TMP" ]]; then
  echo "HATA: kurulum scripti indirilemedi. Anahtar gecersiz veya suresi dolmus olabilir." >&2
  exit 1
fi

# 4) Kurulumu calistir
export E1_GHCR_TOKEN E1_TAILSCALE_AUTHKEY E1_TAILSCALE_TAGS E1_CUSTOMER E1_SITE
[[ -n "$E1_REF" ]] && export E1_REF
[[ "$E1_APPLIANCE" != "auto" ]] && export E1_APPLIANCE
[[ "$E1_BUILD" == "1" ]] && export E1_BUILD
export ASSUME_YES=1
bash "$TMP"
"""


@dataclass
class Profile:
    """Tekrar girilmesin diye saklanan alanlar. SIR YOK."""

    host: str = "192.168.1.100"
    port: int = 22
    user: str = "enerjione"
    key_path: str = ""
    customer: str = ""
    site: str = ""
    # Bos = "en son yayin". Bilerek dal adi DEGIL: sahaya kurulan sey
    # yayinlanmis bir surum olmali, gelistirme dalinin ucu degil.
    ref: str = ""

    @classmethod
    def load(cls) -> "Profile":
        try:
            return cls(**json.loads(PROFILE_PATH.read_text(encoding="utf-8")))
        except Exception:
            return cls()

    def save(self) -> None:
        try:
            PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            PROFILE_PATH.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except Exception:
            pass  # profil kaydedilemedi diye kurulum durmasin


# ---------------------------------------------------------------------------
# Terminal gorunumu
# ---------------------------------------------------------------------------
class TerminalView(ttk.Frame):
    """Salt okunur, koyu temali metin alani.

    Iki kritik davranis:
      * ANSI renk kodlari temizlenir (yoksa ekranda "[0;32m" cop kalir).
      * `\r` SATIRI YENILER, yeni satir acmaz. install.sh ilerleme cubugunu
        `\r` ile ciziyor; duz eklenseydi ekran binlerce satirla dolardi.
    """

    def __init__(self, master):
        super().__init__(master)
        self.text = tk.Text(
            self, wrap="none", bg="#0d1117", fg="#c9d1d9",
            insertbackground="#c9d1d9", font=("Consolas", 10),
            relief="flat", padx=10, pady=8,
        )
        yscroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=yscroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        for tag, color in (
            ("ok", "#3fb950"), ("err", "#f85149"),
            ("warn", "#d29922"), ("info", "#58a6ff"), ("dim", "#8b949e"),
        ):
            self.text.tag_configure(tag, foreground=color)
        self.text.configure(state="disabled")
        self._autoscroll = True

    def set_autoscroll(self, on: bool) -> None:
        self._autoscroll = on

    def write(self, chunk: str) -> None:
        # ANSI temizligi BURADA yapilir, cagiranda degil: bu widget'a nereden
        # yazilirsa yazilsin ekranda "[0;32m" gibi cop kalmasin. (Anahtar
        # maskeleme ayri bir is; o SSH katmaninda yapiliyor.)
        chunk = ANSI_RE.sub("", chunk)
        self.text.configure(state="normal")
        for part in re.split(r"(\r\n|\r|\n)", chunk):
            if part in ("\n", "\r\n"):
                self.text.insert("end", "\n")
            elif part == "\r":
                # Imleci satir basina al: sonraki yazim bu satirin uzerine gelsin.
                self.text.delete("end-1l linestart", "end-1c")
            elif part:
                self.text.insert("end", part, self._tag_for(part))
        if self._autoscroll:
            self.text.see("end")
        self.text.configure(state="disabled")

    @staticmethod
    def _tag_for(line: str) -> str:
        s = line.lstrip()
        if s.startswith(("✓", "OK", "TAMAM")):
            return "ok"
        if s.startswith(("✗", "HATA", "ERROR")):
            return "err"
        if s.startswith(("!", "UYARI", "WARN")):
            return "warn"
        if s.startswith(("·", "[")):
            return "info"
        return "dim"

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def dump(self) -> str:
        return self.text.get("1.0", "end")


# ---------------------------------------------------------------------------
# Kurulum is parcacigi
# ---------------------------------------------------------------------------
class InstallWorker(threading.Thread):
    """SSH islerini ana thread'i bloklamadan yurutur.

    Arayuze DOGRUDAN dokunmaz; her sey `out` kuyruguna ("kind", payload)
    ciftleri olarak birakilir.
    """

    def __init__(self, cfg: dict, out: "queue.Queue"):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.out = out
        self._stop = threading.Event()
        self.client: paramiko.SSHClient | None = None

    def cancel(self) -> None:
        self._stop.set()

    # -- kuyruk yardimcilari ------------------------------------------------
    def log(self, text: str) -> None:
        self.out.put(("log", text))

    def status(self, text: str) -> None:
        self.out.put(("status", text))

    def done(self, ok: bool, msg: str) -> None:
        self.out.put(("done", (ok, msg)))

    # -- akis ---------------------------------------------------------------
    def run(self) -> None:
        action = self.cfg.get("action", "install")
        label = {"install": "Kurulum", "update": "Guncelleme",
                 "uninstall": "Kaldirma", "appliance": "Mini PC katmani",
                 "wifi_scan": "WiFi taramasi",
                 "tailscale": "VPN kurulumu"}.get(action, "Islem")
        try:
            self._connect()
            if self._stop.is_set():
                return self.done(False, "Kullanici tarafindan iptal edildi.")

            if action == "install":
                self._upload()
                if self._stop.is_set():
                    return self.done(False, "Kullanici tarafindan iptal edildi.")
                rc = self._execute()
            elif action == "update":
                # Surum secildiyse ona gec; secilmediyse en son yayina.
                # `--yes`: arayuzden onay zaten alindi, cihazda tekrar sorma.
                ver = self.cfg.get("ref", "")
                extra = f" --version {ver.lstrip('v')}" if ver and ver != "main" else ""
                extra = " --edge" if ver == "main" else extra
                if self.cfg.get("build"):
                    extra += " --build"
                rc = self._remote_only(
                    f"sudo -S -p '' bash {APP_DIR}/update.sh --yes{extra}",
                    "Guncelleme calisiyor...")
            elif action == "uninstall":
                # --purge-dir BILEREK yok: kurulum dizinini silmek yerine
                # birakiyoruz ki yedekler ve .env elde kalsin. Tam silme
                # ayri ve acik bir islem olmali.
                flags = "--yes" + (" --keep-images" if self.cfg.get("keep_images") else "")
                rc = self._remote_only(
                    f"sudo -S -p '' bash {APP_DIR}/uninstall.sh {flags}",
                    "Kaldiriliyor...")
            elif action == "appliance":
                # Mini PC katmani: WiFi AP + e1-grid.local + ag ajani.
                # Kurulumda WiFi karti yoksa atlaniyor; sonradan elle
                # kurmak icin bu yol var (arayuz de ayni komutu oneriyor).
                rc = self._remote_only(
                    f"sudo -S -p '' bash {APP_DIR}/infra/appliance/setup-appliance.sh",
                    "Mini PC katmani kuruluyor...")
            elif action == "wifi_scan":
                rc = self._wifi_scan()
            elif action == "tailscale":
                # Anahtar /etc/enerjione-grid/install.env'de; script onu
                # kendisi okuyor. Anahtar yoksa hicbir sey yapmadan 0 doner.
                rc = self._remote_only(
                    f"sudo -S -p '' bash {APP_DIR}/infra/appliance/setup-tailscale.sh",
                    "Uzaktan bakim VPN'i kuruluyor...")
            else:
                return self.done(False, f"Bilinmeyen islem: {action}")

            if rc == 0:
                self.done(True, f"{label} tamamlandi.")
            else:
                self.done(False, f"{label} basarisiz (cikis kodu {rc}).")
        except paramiko.AuthenticationException:
            self.log("\n✗ SSH kimlik dogrulama basarisiz.\n")
            self.done(False, "Kullanici adi / parola / anahtar hatali.")
        except (socket.timeout, OSError) as exc:
            self.log(f"\n✗ Baglanti kurulamadi: {exc}\n")
            self.done(False, "Cihaza ulasilamadi — IP ve kablo baglantisini kontrol edin.")
        except Exception as exc:  # noqa: BLE001 - arayuze tasinmali
            self.log(f"\n✗ Beklenmeyen hata: {type(exc).__name__}: {exc}\n")
            self.done(False, "Beklenmeyen hata; terminal ciktisina bakin.")
        finally:
            if self.client:
                try:
                    self.client.close()
                except Exception:
                    pass

    def _connect(self) -> None:
        c = self.cfg
        self.status("Baglaniliyor...")
        self.log(f"· {c['user']}@{c['host']}:{c['port']} adresine baglaniliyor...\n")
        self.client = paramiko.SSHClient()
        # Saha cihazi her kurulumda yeni host key uretir; bilinmeyen anahtari
        # kabul ediyoruz. Bu, dogrudan ethernet ile baglanan bir cihaz icin
        # makul; internet uzerinden kullanilacaksa host key sabitlenmelidir.
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kwargs = dict(hostname=c["host"], port=c["port"], username=c["user"],
                      timeout=15, banner_timeout=20, auth_timeout=20)
        if c.get("key_path"):
            kwargs["key_filename"] = c["key_path"]
            if c.get("password"):
                kwargs["passphrase"] = c["password"]
        else:
            kwargs["password"] = c.get("password") or ""
        self.client.connect(**kwargs)
        self.log("✓ SSH baglantisi kuruldu\n\n")

    def _wifi_scan(self) -> int:
        """Cihazdaki WiFi aglarini tara ve arayuze LISTE olarak don.

        Ciktiyi terminale dokmuyoruz: nmcli'nin insan-okur bicimi sayfalayici
        artiklari ("lines 1-39"), renk kodlari ve mukerrer SSID'ler iceriyor —
        ekrani cop ediyordu. `--terse` makine-okur bicim verir; burada
        ayristirilip SSID'ler tekillestiriliyor.
        """
        self.status("WiFi aglari taraniyor...")
        # TERM=dumb + --terse: sayfalayici ve renk yok.
        cmd = (
            "sudo -S -p '' env TERM=dumb sh -c '"
            "nmcli radio wifi on >/dev/null 2>&1; "
            "nmcli device wifi rescan >/dev/null 2>&1; sleep 3; "
            "nmcli --terse --fields SSID,SIGNAL,SECURITY device wifi list'"
        )
        cikti: list[str] = []
        rc = self._run_remote(cmd, topla=cikti)

        # nmcli --terse: "SSID:SIGNAL:SECURITY", ozel karakterler \: ile kacisli
        aglar: dict[str, tuple[int, str]] = {}
        for satir in "".join(cikti).splitlines():
            parcalar = re.split(r"(?<!\\):", satir.strip())
            if len(parcalar) < 2:
                continue
            ssid = parcalar[0].replace("\\:", ":").strip()
            if not ssid or ssid == "--":
                continue  # gizli ag — secilemez, listeye koymanin anlami yok
            try:
                sinyal = int(parcalar[1])
            except ValueError:
                continue
            guvenlik = parcalar[2] if len(parcalar) > 2 else ""
            # Ayni ag birden fazla eriximi noktasindan gorunuyor; EN GUCLU
            # olani tut. Aksi halde liste ayni adla onlarca satir oluyordu.
            if ssid not in aglar or sinyal > aglar[ssid][0]:
                aglar[ssid] = (sinyal, guvenlik)

        sirali = sorted(aglar.items(), key=lambda kv: -kv[1][0])
        self.out.put(("wifi_list", sirali))
        if sirali:
            self.log(f"✓ {len(sirali)} ag bulundu — soldaki listeden secin\n")
        else:
            self.log("! Ag bulunamadi. WiFi karti var mi, radyo acik mi?\n")
        return rc

    def _remote_only(self, cmd: str, title: str) -> int:
        """Cihazda hazir duran bir scripti calistir (dosya gondermeden).

        Guncelleme ve kaldirma icin: ikisi de zaten kurulu sistemin kendi
        scriptleri. Anahtar cihazda (/etc/enerjione-grid/install.env), yeniden
        gondermeye gerek yok.
        """
        self.status(title)
        self.log(f"· {cmd}\n\n")
        return self._run_remote(cmd)

    def _upload(self) -> None:
        self.status("Kurulum dosyasi gonderiliyor...")
        script = PROVISIONER.format(
            ghcr=self.cfg["ghcr_token"],
            tskey=self.cfg.get("ts_key", ""),
            tstags=self.cfg.get("ts_tags", "tag:e1-appliance"),
            slug=REPO_SLUG,
            ref=self.cfg.get("ref", ""),
            bootstrap=self.cfg.get("bootstrap") or "main",
            appliance=self.cfg.get("appliance", "auto"),
            build="1" if self.cfg.get("build") else "",
            wifi_ssid=self.cfg.get("wifi_ssid", ""),
            wifi_pass=self.cfg.get("wifi_pass", ""),
            customer=self.cfg.get("customer", ""),
            site=self.cfg.get("site", ""),
        )
        sftp = self.client.open_sftp()
        try:
            with sftp.open(REMOTE_TMP, "w") as fh:
                fh.write(script)
            # Icinde canli anahtar var: baska kullanici okuyamasin.
            sftp.chmod(REMOTE_TMP, 0o600)
        finally:
            sftp.close()
        self.log(f"✓ Kurulum dosyasi gonderildi ({len(script)} bayt)\n\n")

    def _execute(self) -> int:
        self.status("Kurulum calisiyor...")
        # Kurulum dosyasi calistiktan sonra SILINIR: icinde canli anahtar var,
        # cihazda kalmasin.
        return self._run_remote(
            f"sudo -S -p '' bash {REMOTE_TMP}; rc=$?; rm -f {REMOTE_TMP}; exit $rc")

    def _run_remote(self, cmd: str, topla: list[str] | None = None) -> int:
        """Uzak komutu calistirir ve ciktisini CANLI kuyruga akitir.

        `topla` verilirse cikti terminale YAZILMAZ, listeye biriktirilir —
        makine-okur ciktilar (nmcli --terse) ekrani kirletmesin diye.
        """
        password = self.cfg.get("password") or ""
        # PTY istiyoruz: install.sh terminal gorunce ilerleme cubugunu cizer.
        # `sudo -S` parolayi stdin'den okur; -p '' ile istemi bastiririz ki
        # terminale karisik bir "password:" satiri dusmesin.
        chan = self.client.get_transport().open_session()
        chan.get_pty(term="xterm", width=100, height=40)
        chan.exec_command(cmd)

        if password:
            chan.sendall(password + "\n")

        chan.settimeout(1.0)
        secret_values = [v for v in (self.cfg.get("ghcr_token"), self.cfg.get("ts_key"), password) if v]

        while True:
            if self._stop.is_set():
                chan.close()
                self.log("\n! Kullanici iptal etti.\n")
                return 130
            try:
                if chan.recv_ready():
                    data = chan.recv(8192).decode("utf-8", errors="replace")
                    if topla is not None:
                        topla.append(self._sanitize(data, secret_values))
                    else:
                        self.log(self._sanitize(data, secret_values))
                    continue
                if chan.exit_status_ready() and not chan.recv_ready():
                    break
                # Kanal bos ama is bitmemis: CPU'yu yakmadan bekle.
                # paramiko'nun ic alanlarina (status_event) yaslanmiyoruz;
                # surum degisiminde sessizce bozulabilir.
                time.sleep(0.05)
            except socket.timeout:
                continue
        # Kanalda kalan son baytlari da al.
        while chan.recv_ready():
            son = self._sanitize(chan.recv(8192).decode("utf-8", errors="replace"), secret_values)
            (topla.append(son) if topla is not None else self.log(son))
        return chan.recv_exit_status()

    @staticmethod
    def _sanitize(text: str, secrets: list[str]) -> str:
        """ANSI kodlarini at, anahtarlar ciktida gorunurse maskele."""
        text = ANSI_RE.sub("", text)
        for s in secrets:
            if s and len(s) > 6:
                text = text.replace(s, s[:4] + "…" + s[-2:])
        return text


# ---------------------------------------------------------------------------
# Arayuz
# ---------------------------------------------------------------------------
class InstallerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x860")
        self.minsize(1060, 700)

        self.profile = Profile.load()
        self.queue: queue.Queue = queue.Queue()
        self.worker: InstallWorker | None = None
        self._last_action = "install"

        self._build()
        self._pump()

    @staticmethod
    def _load_png(path: Path) -> "tk.PhotoImage | None":
        """PNG yukle; bulunamazsa None don (arac gorselsiz de calissin)."""
        try:
            return tk.PhotoImage(file=str(path))
        except (tk.TclError, OSError):
            return None

    # -- yerlesim -----------------------------------------------------------
    def _build(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        head = ttk.Frame(self, padding=(14, 10))
        head.pack(fill="x")

        # Logo + pencere simgesi. Gorseller ONCEDEN dogru boyuta getirildi
        # (assets/); Tkinter'in PhotoImage'i yalnizca tam sayi bolme ile
        # kucultebiliyor ve 3261px'lik markali logoyu makul bir boyuta
        # indiremiyordu. Referanslari nesnede TUTUYORUZ: yerel degiskende
        # kalsalardi cop toplayici alir ve gorsel bos gorunurdu.
        assets = _asset_dir()
        self._img_logo = self._load_png(assets / "logo.png")
        self._img_icon = self._load_png(assets / "favicon.png")

        if self._img_logo is not None:
            ttk.Label(head, image=self._img_logo).pack(side="left", padx=(0, 10))
        else:
            # Gorsel yoksa arac yine calissin — sadece yazi ile.
            ttk.Label(head, text="EnerjiOne Grid",
                      font=("Segoe UI", 15, "bold")).pack(side="left")
        if self._img_icon is not None:
            try:
                self.iconphoto(True, self._img_icon)
            except tk.TclError:
                pass

        ttk.Label(head, text="Saha Kurulum Araci", font=("Segoe UI", 11),
                  foreground="#6e7681").pack(side="left")
        self.status_lbl = ttk.Label(head, text="Hazir", foreground="#6e7681")
        self.status_lbl.pack(side="right")

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # Sol panel: ustte form, altta SABIT eylem seridi. KAYDIRMA YOK —
        # pencere tam ekran aciliyor ve form buna sigacak sekilde derli
        # toplu tutuldu (IP+port ayni satirda, aciklama paragraflari yok).
        left = ttk.Frame(body, width=290)
        left.pack_propagate(False)   # ic icerik paneli genisletmesin
        body.add(left, weight=0)

        actions = ttk.Frame(left, padding=(0, 8, 0, 0))
        actions.pack(side="bottom", fill="x")      # once alt serit yer alsin

        form = ttk.Frame(left, padding=(0, 0, 12, 0))
        form.pack(side="top", fill="both", expand=True)

        term_wrap = ttk.Frame(body)
        body.add(term_wrap, weight=1)

        self._build_form(form)
        self._build_actions(actions)
        self._build_terminal(term_wrap)

        # Tam ekran ac: saha kurulumunda terminal ciktisi ne kadar genis
        # olursa o kadar iyi. 'zoomed' Windows'ta calisir; digerlerinde
        # -zoomed ozniteligi denenir, o da yoksa normal boyutta kalir.
        try:
            self.state("zoomed")
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)
            except tk.TclError:
                pass

    def _build_form(self, parent: ttk.Frame) -> None:
        """Sol panel — SEKMELI.

        Onceden alti ayri kutu alt alta duruyordu; ekran "parca parca"
        gorunuyor ve her alan icin asagi kaydirmak gerekiyordu. Sekmeler
        ilgili alanlari bir arada tutuyor ve panel yuksekligini sabitliyor.
        """
        self.vars: dict[str, tk.Variable] = {}

        nb = ttk.Notebook(parent)
        nb.pack(fill="both", expand=True)
        sek_cihaz = ttk.Frame(nb, padding=12)
        sek_kurulum = ttk.Frame(nb, padding=12)
        sek_ag = ttk.Frame(nb, padding=12)
        nb.add(sek_cihaz, text="  Cihaz  ")
        nb.add(sek_kurulum, text="  Kurulum  ")
        nb.add(sek_ag, text="  Ağ  ")
        for f in (sek_cihaz, sek_kurulum, sek_ag):
            f.columnconfigure(1, weight=1)

        def row(kap, r: int, etiket: str, key: str, default="", show=None):
            ttk.Label(kap, text=etiket).grid(row=r, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=default)
            ttk.Entry(kap, textvariable=var, show=show).grid(
                row=r, column=1, sticky="ew", pady=4, padx=(8, 0))
            self.vars[key] = var

        def baslik(kap, r: int, metin: str):
            ttk.Label(kap, text=metin, font=("Segoe UI", 9, "bold"),
                      foreground="#57606a").grid(
                row=r, column=0, columnspan=2, sticky="w", pady=(10, 2))

        # ================= CIHAZ =================
        ttk.Label(sek_cihaz, text="IP adresi").grid(row=0, column=0, sticky="w", pady=4)
        ipf = ttk.Frame(sek_cihaz)
        ipf.grid(row=0, column=1, sticky="ew", pady=4, padx=(8, 0))
        ipf.columnconfigure(0, weight=1)
        self.vars["host"] = tk.StringVar(value=self.profile.host)
        ttk.Entry(ipf, textvariable=self.vars["host"]).grid(row=0, column=0, sticky="ew")
        ttk.Label(ipf, text=":").grid(row=0, column=1, padx=2)
        self.vars["port"] = tk.StringVar(value=str(self.profile.port))
        ttk.Entry(ipf, textvariable=self.vars["port"], width=5).grid(row=0, column=2)

        row(sek_cihaz, 1, "Kullanıcı", "user", self.profile.user)
        row(sek_cihaz, 2, "Parola", "password", show="•")

        ttk.Label(sek_cihaz, text="SSH anahtarı").grid(row=3, column=0, sticky="w", pady=4)
        kf = ttk.Frame(sek_cihaz)
        kf.grid(row=3, column=1, sticky="ew", pady=4, padx=(8, 0))
        kf.columnconfigure(0, weight=1)
        self.vars["key_path"] = tk.StringVar(value=self.profile.key_path)
        ttk.Entry(kf, textvariable=self.vars["key_path"]).grid(row=0, column=0, sticky="ew")
        ttk.Button(kf, text="...", width=3, command=self._pick_key).grid(row=0, column=1, padx=(4, 0))

        baslik(sek_cihaz, 4, "SAHA KİMLİĞİ")
        row(sek_cihaz, 5, "Müşteri *", "customer", self.profile.customer)
        row(sek_cihaz, 6, "Saha / proje *", "site", self.profile.site)

        # ================= KURULUM =================
        ttk.Label(sek_kurulum, text="GitHub anahtarı *").grid(row=0, column=0, sticky="w", pady=4)
        gf = ttk.Frame(sek_kurulum)
        gf.grid(row=0, column=1, sticky="ew", pady=4, padx=(8, 0))
        gf.columnconfigure(0, weight=1)
        self.vars["ghcr_token"] = tk.StringVar()
        ttk.Entry(gf, textvariable=self.vars["ghcr_token"], show="•").grid(
            row=0, column=0, sticky="ew")
        # Anahtar uretme sayfasi kapsamlar ON-DOLDURULMUS acilir; kullanici
        # hangi kutuyu isaretleyecegini aramak zorunda kalmasin.
        ttk.Button(gf, text="Al", width=4, command=self._token_al).grid(
            row=0, column=1, padx=(4, 0))

        row(sek_kurulum, 1, "Tailscale anahtarı", "ts_key", show="•")

        baslik(sek_kurulum, 2, "SÜRÜM")
        self.version_var = tk.StringVar(value=VERSION_LATEST)
        self.version_box = ttk.Combobox(sek_kurulum, textvariable=self.version_var,
                                        state="readonly")
        self.version_box["values"] = (VERSION_LATEST, VERSION_EDGE)
        self.version_box.grid(row=3, column=0, columnspan=2, sticky="ew", pady=2)
        ttk.Button(sek_kurulum, text="Sürümleri yenile", command=self._refresh_versions)             .grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        # TUZAK: "main" secildiginde kod main'den gelir ama IMAJ etiketi
        # VERSION dosyasindan cozulur. VERSION yayinlanmis bir surumle
        # ayniysa pull BASARILI olur ve tag'in ESKI imajlari iner — yani
        # scriptler yeni, uygulama eski. Derleme bunu keser.
        self.build_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            sek_kurulum,
            text="İmajları cihazda derle (main'i test ederken şart)",
            variable=self.build_var,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

        baslik(sek_kurulum, 6, "CİHAZ TİPİ")
        self.appliance_box = ttk.Combobox(
            sek_kurulum, state="readonly",
            values=(
                "Otomatik belirle (önerilen)",
                "Saha kutusu — WiFi ağı + e1-grid.local kur",
                "Sunucu / sanal makine — WiFi ağı kurma",
            ))
        self.appliance_box.current(0)
        self.appliance_box.grid(row=7, column=0, columnspan=2, sticky="ew", pady=2)

        # ================= AG =================
        ttk.Label(sek_ag, text="Cihazda internet yoksa kurulum GitHub'dan\n"
                          "indirme yapamaz. Ağı buradan tanımlayın.",
                  foreground="#57606a", justify="left").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        ttk.Button(sek_ag, text="Cihazdaki ağları tara", command=self._wifi_scan)             .grid(row=1, column=0, columnspan=2, sticky="ew")

        ttk.Label(sek_ag, text="WiFi ağı").grid(row=2, column=0, sticky="w", pady=(10, 4))
        self.vars["wifi_ssid"] = tk.StringVar()
        # Combobox: taramadan gelen aglar buraya dolar ama ELLE de yazilabilir
        # (gizli ag veya taramada gorunmeyen durumlar icin).
        self.wifi_box = ttk.Combobox(sek_ag, textvariable=self.vars["wifi_ssid"])
        self.wifi_box.grid(row=2, column=1, sticky="ew", pady=(10, 4), padx=(8, 0))

        row(sek_ag, 3, "WiFi parolası", "wifi_pass", show="•")

        self.wifi_bilgi = ttk.Label(sek_ag, text="", foreground="#57606a")
        self.wifi_bilgi.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _build_actions(self, parent: ttk.Frame) -> None:
        """Sabit eylem seridi — kaydirma alaninin disinda, hep gorunur."""
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)

        # Birincil eylemler yan yana: dikey yer kazandiriyor ve en sik
        # kullanilanlar bir arada duruyor.
        self.test_btn = ttk.Button(parent, text="Baglantiyi Test Et", command=self._test)
        self.test_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3), pady=2)
        self.start_btn = ttk.Button(parent, text="Kur", command=self._start)
        self.start_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0), pady=2)

        self.update_btn = ttk.Button(parent, text="Guncelle", command=self._update)
        self.update_btn.grid(row=1, column=0, columnspan=2, sticky="ew", pady=2)

        ttk.Separator(parent, orient="horizontal")             .grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 6))

        # Sonradan kurulabilen katmanlar.
        self.appliance_btn = ttk.Button(parent, text="Mini PC katmani",
                                        command=lambda: self._extra("appliance"))
        self.appliance_btn.grid(row=3, column=0, sticky="ew", padx=(0, 3), pady=2)
        self.tailscale_btn = ttk.Button(parent, text="Uzaktan bakim VPN",
                                        command=lambda: self._extra("tailscale"))
        self.tailscale_btn.grid(row=3, column=1, sticky="ew", padx=(3, 0), pady=2)

        # Kaldirma en altta, ayri ve tek basina: yanlislikla tiklanmasin.
        self.uninstall_btn = ttk.Button(parent, text="Sistemi Kaldir",
                                        command=self._uninstall)
        self.uninstall_btn.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 2))

        self.cancel_btn = ttk.Button(parent, text="Iptal", command=self._cancel,
                                     state="disabled")
        self.cancel_btn.grid(row=5, column=0, columnspan=2, sticky="ew", pady=2)

        self.progress = ttk.Progressbar(parent, mode="indeterminate")
        self.progress.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _build_terminal(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Label(bar, text="Kurulum ciktisi", font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Button(bar, text="Kaydet", command=self._save_log).pack(side="right", padx=2)
        ttk.Button(bar, text="Temizle", command=lambda: self.term.clear()).pack(side="right", padx=2)
        self.autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Otomatik kaydir", variable=self.autoscroll,
                        command=lambda: self.term.set_autoscroll(self.autoscroll.get())).pack(side="right", padx=8)

        self.term = TerminalView(parent)
        self.term.pack(fill="both", expand=True)
        self.term.write("EnerjiOne Grid saha kurulum aracina hos geldiniz.\n\n"
                        "1. Cihazi ethernet ile bilgisayariniza baglayin\n"
                        "2. Soldaki alanlari doldurun\n"
                        "3. Once 'Baglantiyi Test Et', sonra 'Kurulumu Baslat'\n\n")

    # -- eylemler -----------------------------------------------------------
    def _pick_key(self) -> None:
        p = filedialog.askopenfilename(title="SSH ozel anahtari secin")
        if p:
            self.vars["key_path"].set(p)

    def _wifi_scan(self) -> None:
        """Cihazdaki WiFi arayuzunden gorunen aglari listele.

        Tarama CIHAZDA yapilmali — kurulumcunun dizustunun gordugu aglar
        cihazin gordukleriyle ayni degil (farkli anten, farkli konum).
        """
        cfg = self._collect()
        if not cfg:
            return
        cfg["action"] = "wifi_scan"
        self.term.write("\n-- WiFi aglari taraniyor --\n")
        self._run_worker(cfg, test_only=False)

    def _wifi_doldur(self, aglar: list) -> None:
        """Taramadan gelen aglari combobox'a yaz (en guclu ustte)."""
        if not aglar:
            self.wifi_bilgi.configure(text="Ağ bulunamadı.")
            return
        self.wifi_box["values"] = [ssid for ssid, _ in aglar]
        # Zaten bir sey yazilmissa kullanicinin girdisini EZME.
        if not self.vars["wifi_ssid"].get().strip():
            self.wifi_box.current(0)
        en_iyi = aglar[0]
        self.wifi_bilgi.configure(
            text=f"{len(aglar)} ağ bulundu · en güçlü: {en_iyi[0]} (%{en_iyi[1][0]})")

    def _token_al(self) -> None:
        """GitHub anahtar uretme sayfasini KAPSAMLAR DOLU olarak ac.

        Kullanicilar dogru kutulari bulmakta zorlaniyor; GitHub `scopes`
        parametresini destekliyor ve sayfa isaretli gelir. Geriye yalnizca
        'Generate token' demek ve degeri kopyalamak kaliyor.
        """
        import webbrowser
        url = (
            "https://github.com/settings/tokens/new"
            "?scopes=repo,read:packages"
            "&description=EnerjiOne%20Grid%20saha%20kurulumu"
        )
        messagebox.showinfo(
            APP_TITLE,
            "Tarayıcıda anahtar üretme sayfası açılacak.\n\n"
            "1. Sayfada 'repo' ve 'read:packages' ZATEN İŞARETLİ gelir\n"
            "2. Expiration (süre) seçin\n"
            "3. En altta 'Generate token' düğmesine basın\n"
            "4. Çıkan 'ghp_...' değerini kopyalayıp buraya yapıştırın\n\n"
            "Not: Değer YALNIZCA BİR KEZ gösterilir; sayfayı kapatmadan kopyalayın.")
        webbrowser.open(url)

    def _refresh_versions(self) -> None:
        """Yayin listesini GitHub'dan tazele. Anahtar gerekir (depo private)."""
        token = self.vars["ghcr_token"].get().strip()
        if not token:
            messagebox.showinfo(APP_TITLE, "Once GitHub kurulum anahtarini girin.")
            return
        self.status_lbl.configure(text="Surumler aliniyor...")
        self.update_idletasks()
        tags = fetch_releases(token)
        if not tags:
            self.status_lbl.configure(text="Surum listesi alinamadi")
            self.term.write("! Surum listesi alinamadi — anahtari ve internet baglantisini kontrol edin.\n"
                            "  'En son yayin' secenegi yine calisir: cihaz en guncel yayini kendisi bulur.\n")
            return
        self.version_box["values"] = (VERSION_LATEST, *tags, VERSION_EDGE)
        self.status_lbl.configure(text=f"{len(tags)} yayin bulundu")
        self.term.write(f"· Yayinlar: {', '.join(tags[:5])}"
                        f"{' …' if len(tags) > 5 else ''}\n")

    def _selected_ref(self) -> str:
        """Secimi install.sh'in anladigi ref'e cevir.

        'En son yayin' -> BOS string. install.sh bos E1_REF'i "en son v* tag"
        diye yorumluyor; somut bir tag yazmak yerine bunu birakmak, arac ile
        cihaz arasinda surum bilgisi ayrismasini onler.
        """
        sel = self.version_var.get()
        if sel == VERSION_LATEST:
            return ""
        if sel == VERSION_EDGE:
            return "main"
        return sel

    def _collect(self) -> dict | None:
        cfg = {k: v.get().strip() for k, v in self.vars.items()}
        if not cfg["host"]:
            messagebox.showerror(APP_TITLE, "IP adresi bos olamaz.")
            return None
        if not cfg["user"]:
            messagebox.showerror(APP_TITLE, "SSH kullanici adi bos olamaz.")
            return None
        if not cfg["password"] and not cfg["key_path"]:
            messagebox.showerror(APP_TITLE, "Parola veya SSH anahtari verilmeli.")
            return None
        try:
            cfg["port"] = int(cfg["port"] or 22)
        except ValueError:
            messagebox.showerror(APP_TITLE, "SSH portu sayi olmali.")
            return None

        if not cfg["customer"] or not cfg["site"]:
            messagebox.showerror(
                APP_TITLE,
                "Musteri ve saha adi zorunludur.\n\n"
                "Cihaz uzaktan bakim listesinde bu adla gorunur; bos "
                "gecilirse hangi kutunun hangi saha oldugu anlasilmaz.")
            return None

        # Surum secimi -> iki ayri deger.
        #   ref       : install.sh'in kodu getirecegi ref (bos = en son yayin)
        #   bootstrap : install.sh'in kendisinin indirilecegi ref (somut olmali)
        cfg["ref"] = self._selected_ref()
        cfg["bootstrap"] = cfg["ref"] or "main"
        # Combobox sirasi -> install.sh'in bekledigi deger.
        cfg["appliance"] = ("auto", "1", "0")[self.appliance_box.current()]
        cfg["build"] = bool(self.build_var.get())
        return cfg

    def _save_profile(self, cfg: dict) -> None:
        # SIR KAYDEDILMEZ — sadece tekrar yazilmasi zahmetli alanlar.
        Profile(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                key_path=cfg["key_path"], customer=cfg["customer"],
                site=cfg["site"], ref=cfg["ref"]).save()

    def _test(self) -> None:
        cfg = self._collect()
        if not cfg:
            return
        cfg["action"] = "test"
        self._save_profile(cfg)
        self.term.write("\n── Baglanti testi ──\n")
        self._run_worker(cfg, test_only=True)

    def _start(self) -> None:
        cfg = self._collect()
        if not cfg:
            return
        if not cfg["ghcr_token"]:
            messagebox.showerror(APP_TITLE, "GitHub kurulum anahtari zorunlu.")
            return
        if not messagebox.askyesno(
            APP_TITLE,
            f"{cfg['user']}@{cfg['host']} adresine kurulum yapilacak.\n\n"
            "Cihazdaki mevcut EnerjiOne Grid kurulumu guncellenecek.\nDevam edilsin mi?",
        ):
            return
        self._save_profile(cfg)
        self.term.write("\n── Kurulum baslatiliyor ──\n")
        self._run_worker(cfg, test_only=False)

    def _update(self) -> None:
        cfg = self._collect()
        if not cfg:
            return
        hedef = cfg["ref"] or "en son yayin"
        if not messagebox.askyesno(
            APP_TITLE,
            f"{cfg['user']}@{cfg['host']} guncellenecek.\n\n"
            f"Hedef surum: {hedef}\n\n"
            "Guncelleme oncesi veritabani yedegi OTOMATIK alinir.\n"
            "Devam edilsin mi?",
        ):
            return
        cfg["action"] = "update"
        self._save_profile(cfg)
        self.term.write("\n-- Guncelleme baslatiliyor --\n")
        self._run_worker(cfg, test_only=False)

    def _extra(self, action: str) -> None:
        """Sonradan kurulabilen katmanlar (mini PC / VPN).

        Ikisi de cihazdaki hazir scriptleri calistiriyor; kurulum sirasinda
        atlanmis olabilirler (or. sanal makinede WiFi karti yok -> appliance
        atlanir, anahtar verilmemisse VPN atlanir).
        """
        cfg = self._collect()
        if not cfg:
            return
        aciklama = {
            "appliance": ("Mini PC katmani kurulacak:\n\n"
                          "  - Sifresiz 'EnerjiOne Grid' WiFi agi\n"
                          "  - e1-grid.local adresi (mDNS)\n"
                          "  - Arayuzden IP/DNS ayari yapan ag ajani\n\n"
                          "Cihazin ag ayarlari degisecek. Devam edilsin mi?"),
            "tailscale": ("Uzaktan bakim VPN'i kurulacak.\n\n"
                          "Anahtar cihazdaki /etc/enerjione-grid/install.env\n"
                          "dosyasindan okunur; yoksa islem bir sey yapmaz.\n\n"
                          "Devam edilsin mi?"),
        }[action]
        if not messagebox.askyesno(APP_TITLE, f"{cfg['user']}@{cfg['host']}\n\n{aciklama}"):
            return
        cfg["action"] = action
        baslik = {"appliance": "Mini PC katmani", "tailscale": "VPN kurulumu"}[action]
        self.term.write(f"\n-- {baslik} --\n")
        self._run_worker(cfg, test_only=False)

    def _uninstall(self) -> None:
        cfg = self._collect()
        if not cfg:
            return
        # IKI ASAMALI ONAY: bu islem TUM VERIYI siler ve geri alinamaz.
        # Tek tiklamayla ulasilabilir olmasi kabul edilemez.
        if not messagebox.askyesno(
            APP_TITLE,
            f"DIKKAT - {cfg['user']}@{cfg['host']}\n\n"
            "EnerjiOne Grid kaldirilacak:\n"
            "  - Tum container'lar durdurulup silinecek\n"
            "  - VERITABANI VE TUM OLCUM GECMISI SILINECEK\n"
            "  - Docker imajlari silinecek\n\n"
            "Bu islem GERI ALINAMAZ. Devam edilsin mi?",
            icon="warning",
        ):
            return
        if not messagebox.askyesno(
            APP_TITLE,
            "Son onay.\n\nVeritabani yedeginiz var mi?\n\n"
            "Kaldirmayi gercekten onayliyor musunuz?",
            icon="warning", default="no",
        ):
            return
        cfg["action"] = "uninstall"
        self.term.write("\n-- Kaldirma baslatiliyor --\n")
        self._run_worker(cfg, test_only=False)


    def _run_worker(self, cfg: dict, test_only: bool) -> None:
        self._last_action = cfg.get("action", "install")
        self.worker = _TestWorker(cfg, self.queue) if test_only else InstallWorker(cfg, self.queue)
        self._busy(True)
        self.worker.start()

    def _cancel(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.status_lbl.configure(text="Iptal ediliyor...")

    def _busy(self, on: bool) -> None:
        for b in (self.start_btn, self.test_btn, self.update_btn,
                  self.uninstall_btn, self.appliance_btn, self.tailscale_btn):
            b.configure(state="disabled" if on else "normal")
        self.cancel_btn.configure(state="normal" if on else "disabled")
        if on:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _save_log(self) -> None:
        p = filedialog.asksaveasfilename(defaultextension=".txt",
                                         initialfile="kurulum-cikti.txt")
        if p:
            Path(p).write_text(self.term.dump(), encoding="utf-8")
            messagebox.showinfo(APP_TITLE, f"Kaydedildi:\n{p}")

    # -- kuyruk pompasi (ana thread) ---------------------------------------
    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self.term.write(payload)
                elif kind == "wifi_list":
                    self._wifi_doldur(payload)
                elif kind == "status":
                    self.status_lbl.configure(text=payload)
                elif kind == "done":
                    ok, msg = payload
                    self._busy(False)
                    self.status_lbl.configure(text=msg)
                    self.term.write(f"\n{'✓' if ok else '✗'} {msg}\n")
                    if ok and self._last_action == "install":
                        host = self.vars["host"].get().strip()
                        self.term.write(
                            f"\nArayuz : http://{host}/\n"
                            "Giris  : installer / ChangeMe123!  (mutlaka degistirin)\n\n"
                        )
                    self.bell()
        except queue.Empty:
            pass
        self.after(60, self._pump)


class _TestWorker(InstallWorker):
    """Sadece baglanir ve sistem bilgisini yazar; kurulum yapmaz."""

    def run(self) -> None:
        try:
            self._connect()
            for label, cmd in (
                ("Sistem", ". /etc/os-release 2>/dev/null && echo \"$PRETTY_NAME\" || uname -a"),
                ("Mimari", "uname -m"),
                ("Bellek", "free -h 2>/dev/null | awk 'NR==2{print $2\" toplam, \"$7\" bos\"}'"),
                ("Disk", "df -h / | awk 'NR==2{print $2\" toplam, \"$4\" bos\"}'"),
                ("Docker", "docker --version 2>/dev/null || echo 'kurulu degil (kurulum kuracak)'"),
                ("sudo", "sudo -n true 2>/dev/null && echo 'parolasiz' || echo 'parola gerekli'"),
                ("Kurulu surum", "cat /opt/enerjione-grid/VERSION 2>/dev/null || echo 'yok (ilk kurulum)'"),
                # Kurulumun TAMAMI internete bagli; en sik takilma noktasi bu.
                ("Internet", "curl -fsS --max-time 8 -o /dev/null https://api.github.com "
                             "&& echo 'VAR' || echo 'YOK — WiFi bilgisi girin'"),
                ("WiFi arayuzu", "nmcli -t -f DEVICE,TYPE device 2>/dev/null | "
                                 "awk -F: '$2==\"wifi\"{print $1}' | head -1 || echo 'yok'"),
            ):
                _, out, _ = self.client.exec_command(cmd, timeout=10)
                val = out.read().decode("utf-8", errors="replace").strip() or "?"
                self.log(f"  {label:<14}: {val}\n")
            self.done(True, "Baglanti basarili.")
        except paramiko.AuthenticationException:
            self.log("\n✗ Kimlik dogrulama basarisiz.\n")
            self.done(False, "Kullanici adi / parola / anahtar hatali.")
        except Exception as exc:  # noqa: BLE001
            self.log(f"\n✗ {type(exc).__name__}: {exc}\n")
            self.done(False, "Cihaza ulasilamadi.")
        finally:
            if self.client:
                try:
                    self.client.close()
                except Exception:
                    pass


def main() -> None:
    InstallerApp().mainloop()


if __name__ == "__main__":
    main()
