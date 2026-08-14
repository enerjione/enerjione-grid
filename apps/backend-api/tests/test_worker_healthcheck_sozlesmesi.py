"""Worker healthcheck'leri: DOGRU PORTA, IMAJDA GERCEKTEN VAR OLAN ikiliyle.

YAKALADIGI HATA SINIFI
----------------------
Docker healthcheck'i BASARISIZ olduğunda sessizdir: container `unhealthy`
isaretlenir, kimse bakmazsa hicbir sey olmaz. Yani yanlis yazilmis bir
healthcheck, healthcheck'in HIC olmamasindan daha kotudur — "izliyoruz"
sanilir.

En kolay yapilan hata `curl`/`wget` kopyalamaktir. Bu depodaki worker
imajlarinin TAMAMI `python:3.11-slim` (whatsapp `node:20-slim`) tabanlidir
ve Dockerfile'lari HICBIR paket kurmaz:

    python:3.11-slim  ->  curl YOK, wget YOK, python VAR
    node:20-slim      ->  curl YOK, wget YOK, python YOK, node VAR
    *-alpine          ->  wget VAR (BusyBox applet'i), curl YOK

`backend-api` imaji curl'u ACIKCA kurar (`apt-get install ... curl`), bu
yuzden onun healthcheck'i curl kullanabilir — ve bu tam da yanlis kopyalamaya
davet eden ornektir.

NEDEN COMPOSE'A BAKIYORUZ
-------------------------
Probe'lar 2.92.0'da imaj katmaninda (`HEALTHCHECK` yonergesi) eklendi ve
calisiyordu, ama `docker-compose.yml`de gorunmuyorlardi. Operatorun (ve bu
depoyu denetleyen kisinin) okudugu sozlesme compose'dur; orada gorunmemesi
"healthcheck yok" sonucuna goturuyordu. Compose tanimi imajdakini EZER,
dolayisiyla artik tek otorite compose'tur ve burasi kilitlenmelidir.
"""

from __future__ import annotations

import http.server
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

_KOK = Path(__file__).resolve().parents[3]
_COMPOSE = _KOK / "docker-compose.yml"

#: Taban imaj -> o imajda KESIN bulunan HTTP probe ikilileri.
#: Buradaki iddia "Dockerfile hicbir sey kurmuyorsa taban imajda ne var"dir.
_TABANDA_VAR = {
    "python:3.11-slim": {"python", "python3"},
    "node:20-slim": {"node"},
    "alpine": {"wget"},
}

#: Worker servisleri: (compose servis adi, kaynak dizini, health portu).
#: `tag-engine-b` BILEREK yok — `extends: tag-engine` ile ayni healthcheck'i
#: devralir ve testi ayrica kosmak ayni seyi iki kez dogrulamak olurdu;
#: devralmanin GERCEKTEN oldugu asagida ayri bir testle kilitleniyor.
WORKERLAR = [
    ("tag-engine", "apps/tag-engine", 8011),
    ("alarm-service", "apps/alarm-service", 8012),
    ("notification-worker", "apps/notification-worker", 8013),
    ("iec104-outbound", "apps/iec104-outbound", 8014),
    ("ftp-server", "apps/ftp-server", 8015),
    ("whatsapp-web-gateway", "apps/whatsapp-web-gateway", 8016),
    ("modbus-outbound", "apps/modbus-outbound", 8017),
]


def _compose() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def _servis(ad: str) -> dict:
    servisler = _compose()["services"]
    assert ad in servisler, f"compose'ta '{ad}' servisi yok"
    return servisler[ad]


def _dockerfile(dizin: str) -> str:
    yol = _KOK / dizin / "Dockerfile"
    assert yol.is_file(), f"Dockerfile yok: {yol}"
    return yol.read_text(encoding="utf-8")


def _kurulan_paketler(dockerfile: str) -> set[str]:
    """`apt-get install` / `apk add` ile GERCEKTEN kurulan paket adlari."""
    paketler: set[str] = set()
    for m in re.finditer(r"(?:apt-get install|apk add)([^\n&|]*)", dockerfile):
        for kelime in m.group(1).split():
            if kelime.startswith("-"):
                continue
            paketler.add(kelime.strip("\\"))
    return paketler


def _taban_imaj(dockerfile: str) -> str:
    """Son `FROM` satiri — cok asamali build'de calisan imaj odur."""
    from_satirlari = re.findall(r"^FROM\s+(\S+)", dockerfile, re.MULTILINE)
    assert from_satirlari, "Dockerfile'da FROM yok"
    return from_satirlari[-1]


# --------------------------------------------------------------------------
# 1) Healthcheck VAR MI ve DOGRU PORTA MI bakiyor
# --------------------------------------------------------------------------


@pytest.mark.parametrize("servis_adi,dizin,port", WORKERLAR)
def test_workerda_healthcheck_var_ve_dogru_porta_bakiyor(servis_adi, dizin, port):
    servis = _servis(servis_adi)
    hc = servis.get("healthcheck")
    assert hc, (
        f"{servis_adi}: compose'ta healthcheck YOK. Imaj katmaninda olabilir "
        f"ama compose operatorun okudugu sozlesmedir; orada gorunmeyen bir "
        f"probe 'izleme yok' olarak okunur."
    )
    test = hc.get("test")
    assert isinstance(test, list) and test and test[0] == "CMD", (
        f"{servis_adi}: healthcheck.test exec-form ['CMD', ...] olmali. "
        f"CMD-SHELL kabuk gerektirir; exec-form'da `|| exit 1` gibi kabuk "
        f"ifadeleri ikiliye DUZ ARGUMAN olarak gecer ve probe hep patlar."
    )
    komut = " ".join(test)
    assert f"127.0.0.1:{port}" in komut, (
        f"{servis_adi}: probe {port} portuna bakmali (WORKER_HEALTH_PORT ile "
        f"AYNI). Bulunan: {komut}"
    )
    # `localhost` DEGIL literal 127.0.0.1: bazi imajlarda ::1'e cozulup
    # yalnizca IPv4 dinleyen bir sunucuda baglanti reddi verir.
    assert "localhost" not in komut, (
        f"{servis_adi}: probe'da 'localhost' kullanilmis. IPv6 (::1) cozumu "
        f"yalnizca IPv4 dinleyen health sunucusunda baglanti reddi verir; "
        f"literal 127.0.0.1 kullanin."
    )


def test_worker_health_portu_env_ile_probe_ayni():
    """`WORKER_HEALTH_PORT` ile probe'un baktigi port AYRISMAMALI."""
    for servis_adi, _dizin, _port in WORKERLAR:
        servis = _servis(servis_adi)
        env = servis.get("environment") or {}
        env_port = str(env.get("WORKER_HEALTH_PORT", "")).strip()
        assert env_port, f"{servis_adi}: WORKER_HEALTH_PORT tanimli degil"
        komut = " ".join(servis["healthcheck"]["test"])
        assert f"127.0.0.1:{env_port}" in komut, (
            f"{servis_adi}: WORKER_HEALTH_PORT={env_port} ama probe farkli bir "
            f"porta bakiyor: {komut}"
        )


# --------------------------------------------------------------------------
# 2) Kullanilan ikili IMAJDA GERCEKTEN VAR MI  (asil tuzak)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("servis_adi,dizin,port", WORKERLAR)
def test_probe_ikilisi_imajda_gercekten_var(servis_adi, dizin, port):
    dockerfile = _dockerfile(dizin)
    taban = _taban_imaj(dockerfile)
    kurulan = _kurulan_paketler(dockerfile)

    beklenen: set[str] = set()
    for anahtar, ikililer in _TABANDA_VAR.items():
        if anahtar in taban:
            beklenen |= ikililer
    beklenen |= kurulan  # apt/apk ile kurulanlar da kullanilabilir

    ikili = _servis(servis_adi)["healthcheck"]["test"][1]
    assert ikili in beklenen, (
        f"{servis_adi}: healthcheck '{ikili}' kullaniyor ama taban imaj "
        f"'{taban}' ve Dockerfile'da kurulan paketler {sorted(kurulan) or '(yok)'}. "
        f"Bu ikili imajda YOKTUR; probe 'executable file not found' ile HER "
        f"TURDA duser ve servis KALICI unhealthy gorunur. "
        f"Kullanilabilecekler: {sorted(beklenen)}"
    )


@pytest.mark.parametrize("servis_adi,dizin,port", WORKERLAR)
def test_kurulmamis_curl_wget_kullanilmiyor(servis_adi, dizin, port):
    """En sik yapilan hata: backend-api'nin curl probe'unu kopyalamak."""
    dockerfile = _dockerfile(dizin)
    kurulan = _kurulan_paketler(dockerfile)
    taban = _taban_imaj(dockerfile)
    ikili = _servis(servis_adi)["healthcheck"]["test"][1]

    for yasak in ("curl", "wget"):
        if yasak in kurulan or ("alpine" in taban and yasak == "wget"):
            continue  # gercekten var
        assert ikili != yasak, (
            f"{servis_adi}: '{yasak}' kullanilmis ama ne taban imajda "
            f"({taban}) ne de Dockerfile'da kurulu. backend-api curl'u ACIKCA "
            f"kurar (apt-get install ... curl); onun probe'unu kopyalamak bu "
            f"servisleri kalici unhealthy yapar."
        )


# --------------------------------------------------------------------------
# 3) Zamanlama degerleri makul mu
# --------------------------------------------------------------------------


@pytest.mark.parametrize("servis_adi,dizin,port", WORKERLAR)
def test_healthcheck_zamanlamasi_makul(servis_adi, dizin, port):
    hc = _servis(servis_adi)["healthcheck"]
    for alan in ("interval", "timeout", "retries", "start_period"):
        assert alan in hc, f"{servis_adi}: healthcheck.{alan} yok"

    def _sn(deger: str) -> int:
        m = re.fullmatch(r"(\d+)([sm])", str(deger))
        assert m, f"{servis_adi}: sure bicimi taninmadi: {deger}"
        return int(m.group(1)) * (60 if m.group(2) == "m" else 1)

    aralik, zaman_asimi = _sn(hc["interval"]), _sn(hc["timeout"])
    assert zaman_asimi < aralik, (
        f"{servis_adi}: timeout ({zaman_asimi}s) interval'den ({aralik}s) kucuk "
        f"olmali; aksi halde probe'lar ust uste biner."
    )
    # Saglik sunuculari TEK IPLIKLI HTTPServer. Dar bir timeout, yavas tek bir
    # istemci yuzunden SAHTE unhealthy uretir.
    assert zaman_asimi >= 3, f"{servis_adi}: timeout cok dar ({zaman_asimi}s)"
    assert int(hc["retries"]) >= 2, (
        f"{servis_adi}: retries>=2 olmali — tek kacirilan probe unhealthy "
        f"yapmamali."
    )


# --------------------------------------------------------------------------
# 4) Probe KOMUTU GERCEKTEN CALISIYOR MU  (kagit uzerinde degil)
# --------------------------------------------------------------------------


class _Stub(http.server.BaseHTTPRequestHandler):
    kod = 200

    def do_GET(self):  # noqa: N802
        # YOL KONTROLU SART: gercek worker'larin hepsi `/health` disindaki
        # yollara 404 doner (or. tag_engine/main.py, ftp_server/health.py).
        # Stub her yola 200 donseydi, compose'daki yol bir gun `/healthz`
        # olarak yazilsa test yine gecerdi ve hata ancak sahada gorulurdu.
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(type(self).kod)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):  # noqa: A003
        return


def _stub_sunucu(kod: int):
    _Stub.kod = kod
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


#: Compose probe'unu calistirilabilir hale getiren donusum: imajdaki `python`
#: yerine testin yorumlayicisi, servisin portu yerine stub sunucunun portu.
def _calistirilabilir_probe(servis_adi: str, port: int) -> list[str] | None:
    ham = _servis(servis_adi)["healthcheck"]["test"]
    ikili = ham[1]
    if ikili != "python":
        # `node` ve `wget` bu makinede olmayabilir; onlar statik olarak
        # dogrulanir (ikili/port/yol assert'leri), calistirilmaz.
        return None
    gercek_port = str(_servis(servis_adi)["environment"]["WORKER_HEALTH_PORT"])
    return [sys.executable] + [p.replace(gercek_port, str(port)) for p in ham[2:]]


#: Python probe'u kullanan TUM servisler. Yalnizca tag-engine'i calistirmak
#: yetmezdi: kalan alti dizgeden birindeki bir yazim hatasi (yanlis port,
#: bozuk ifade, eksik tirnak) hicbir testi dusurmeden sahaya cikardi.
_PYTHON_SERVISLERI = [
    (ad, dz, pr)
    for ad, dz, pr in WORKERLAR
    if _servis(ad)["healthcheck"]["test"][1] == "python"
]


@pytest.mark.parametrize("servis_adi,dizin,port", _PYTHON_SERVISLERI)
def test_probe_saglikli_uctan_sifir_doner(servis_adi, dizin, port):
    komut = _calistirilabilir_probe(servis_adi, 0)
    assert komut is not None
    srv = _stub_sunucu(200)
    try:
        komut = _calistirilabilir_probe(servis_adi, srv.server_port)
        rc = subprocess.run(komut, capture_output=True).returncode
        assert rc == 0, f"{servis_adi}: 200 donen uc icin probe basarili olmali"
    finally:
        srv.shutdown()


@pytest.mark.parametrize("servis_adi,dizin,port", _PYTHON_SERVISLERI)
def test_probe_takilan_uctan_sifirdan_farkli_doner(servis_adi, dizin, port):
    """Uc 503 dondugunde (bekci 'stalled' der) probe BASARISIZ olmali."""
    srv = _stub_sunucu(503)
    try:
        komut = _calistirilabilir_probe(servis_adi, srv.server_port)
        rc = subprocess.run(komut, capture_output=True).returncode
        assert rc != 0, f"{servis_adi}: 503 donen uc icin probe basarisiz olmali"
    finally:
        srv.shutdown()


@pytest.mark.parametrize("servis_adi,dizin,port", _PYTHON_SERVISLERI)
def test_probe_kapali_porttan_sifirdan_farkli_doner(servis_adi, dizin, port):
    """Surec olmus/port dinlenmiyorsa probe BASARISIZ olmali."""
    srv = _stub_sunucu(200)
    bos_port = srv.server_port
    srv.shutdown()
    srv.server_close()
    komut = _calistirilabilir_probe(servis_adi, bos_port)
    rc = subprocess.run(komut, capture_output=True).returncode
    assert rc != 0, f"{servis_adi}: kapali port icin probe basarisiz olmali"


def test_probe_yanlis_yola_giderse_basarisiz_olur():
    """Yol kontrolunun GERCEKTEN calistigini kanitlar (stub 404 doner)."""
    srv = _stub_sunucu(200)
    try:
        komut = _calistirilabilir_probe("tag-engine", srv.server_port)
        bozuk = [p.replace("/health", "/healthz") for p in komut]
        rc = subprocess.run(bozuk, capture_output=True).returncode
        assert rc != 0, (
            "yanlis yola giden probe basarili donuyor — bu, compose'daki "
            "yolun sessizce bozulabilecegi anlamina gelir"
        )
    finally:
        srv.shutdown()


# --------------------------------------------------------------------------
# 5) Bekci ile healthcheck GOREV AYRIMI
# --------------------------------------------------------------------------


def test_healthcheck_restart_ettirmeye_calismiyor():
    """Compose'da healthcheck'e bagli bir restart mekanizmasi OLMAMALI.

    Docker `unhealthy` bir container'i YENIDEN BASLATMAZ, yalnizca isaretler.
    Kurtarma her serviste bekcinin (watchdog) isidir. Bu ayrimi bulandiran
    bir autoheal/restart-on-unhealthy eklentisi girerse burada yakalanir.
    """
    ham = _COMPOSE.read_text(encoding="utf-8").lower()
    for iz in ("autoheal", "willfarrell/autoheal", "restart_policy: on-unhealthy"):
        assert iz not in ham, (
            f"compose'da '{iz}' izi var. Healthcheck GORUNURLUK icindir; "
            f"kilitlenmeden kurtarma bekcinin isidir (servis kendini "
            f"sonlandirir, restart: unless-stopped geri kaldirir)."
        )


def test_tag_engine_replikasi_healthchecki_devralir():
    """`tag-engine-b` extends ile ayni probe'u almali (elle kopya OLMAMALI)."""
    servisler = _compose()["services"]
    b = servisler["tag-engine-b"]
    assert b.get("extends", {}).get("service") == "tag-engine", (
        "tag-engine-b artik extends kullanmiyor — healthcheck'i devralmiyor "
        "olabilir; kendi blogunu eklemek gerekir."
    )
    assert "healthcheck" not in b, (
        "tag-engine-b'ye ayri bir healthcheck yazilmis. extends zaten "
        "devrediyor; iki kopya zamanla ayrisir."
    )


def test_tum_worker_servisleri_listede():
    """Compose'a yeni bir WORKER_HEALTH_PORT'lu servis eklenirse test de bilsin."""
    servisler = _compose()["services"]
    portlu = {
        ad
        for ad, s in servisler.items()
        if isinstance(s.get("environment"), dict)
        and "WORKER_HEALTH_PORT" in s["environment"]
    }
    bilinen = {ad for ad, _d, _p in WORKERLAR}
    eksik = portlu - bilinen
    assert not eksik, (
        f"compose'da health portu olan ama bu testte listelenmeyen servis(ler): "
        f"{sorted(eksik)} — WORKERLAR listesine ekleyin."
    )
