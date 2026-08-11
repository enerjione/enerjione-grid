"""Gateway imajinin KAYIT DEFTERINDEKI surumu — cihazdan bagimsiz okuma.

NEDEN VAR
---------
Surum kesfi tamamen host ajaninin `docker buildx imagetools inspect`
cagrisina bagliydi. Buildx, Docker Engine kurulumlarinda siklikla YOKTUR
(`apt install docker.io` onu getirmez). O cihazlarda:

  * `remote_digest` bos kalir  -> `update_available = None` ("Surum bilinmiyor")
  * `remote_version` bos kalir -> HEDEF SURUM ekranda hic gorunmez

Yani kayit defterinde yeni surum yayinlanmis olsa bile operator ekranda
onu goremiyor, "guncelleme var" bilgisine hic ulasamiyordu — 2026-08-11'de
sahada tam bu yasandi: `:latest` 1.6.2'ye tasinmisken ekran "Surum
bilinmiyor" diyordu ve cihaz 1.6.1'de kaldi.

Bu modul ayni sorunun cevabini BACKEND'den, dogrudan kayit defterinin HTTP
API'sinden alir: docker'a, buildx'e ya da gateway'in o cihazda kurulu
olmasina bagli DEGILDIR. Backend zaten disari cikiyor (harita karolari,
uygulama surum kontrolu).

NE DEGISMEDI
------------
Guncelleme KARARI hala digest karsilastirmasidir; bu modul yalnizca uzak
digest/surum icin IKINCI bir kaynak. Ajan kendi degerini bildirebiliyorsa
ONUN degeri kullanilir (cihazin gerceklige en yakin olani odur).

SINIR — YETKI
-------------
Yalnizca ANONIM cekilebilen (public) paketler okunur. Paket private ise
token gerekir; o durumda alanlar bos kalir ve sebep `remote_error` ile
disariya bildirilir. Sessizce "guncel" DENMEZ.

SINIR — BLOKLAMAZ
-----------------
`lookup()` ag beklemez: onbellegi doner ve gerekiyorsa arka planda tazeler
(stale-while-revalidate). Sorgu ~3 HTTP istegidir; arayuz ajan durumunu
guncelleme sirasinda saniyede bir yokladigi icin istek icinde beklemek
ekrani kitlerdi.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import requests

from app.schemas.gateway_agent import GatewayAgentStatus

logger = logging.getLogger(__name__)

#: Basarili sorgu ne kadar taze sayilir. Yeni imaj gunler icinde cikiyor;
#: ajanin kendi uzak digest onbellegi de 900 sn.
TTL_OK_SEC = 900.0
#: Basarisiz sorguda tekrar deneme araligi. Ag yoksa her istekte yeniden
#: denemek arayuzu yavaslatir; 15 dakika beklemek de fazla.
TTL_FAIL_SEC = 120.0

_HTTP_TIMEOUT_SEC = 5.0

#: Manifest isterken kabul edilen medya tipleri. Cok mimarili imajda ONCE
#: index doner; tek mimarili imajda dogrudan manifest.
_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)

#: OCI standart surum etiketi — ajanin `_local_version` ile AYNI anahtar
#: (infra/appliance/e1-gwd.py). Iki taraf farkli anahtar okursa ekranda
#: "1.6.1 -> (bos)" gibi yarim bir karsilastirma cikar.
_VERSION_LABEL = "org.opencontainers.image.version"


@dataclass(frozen=True)
class RegistryImage:
    """Kayit defterindeki etiketin isaret ettigi imaj."""

    version: str | None = None
    #: Manifest (liste) digest'i — ajanin `image_digest` alaniyla AYNI
    #: seviye, dogrudan karsilastirilabilir (bkz. e1-gwd `_remote_digest`).
    digest: str | None = None
    error: str | None = None


_lock = threading.Lock()
#: image_ref -> (fetched_at_monotonic, RegistryImage)
_cache: dict[str, tuple[float, RegistryImage]] = {}
#: Ayni referans icin es zamanli iki sorgu atmamak icin.
_inflight: set[str] = set()


# ---------------------------------------------------------------------------
# Referans ayristirma
# ---------------------------------------------------------------------------
def parse_image_ref(ref: str | None) -> tuple[str, str, str] | None:
    """'ghcr.io/enerjione/x:latest' -> ('ghcr.io', 'enerjione/x', 'latest').

    Digest'e sabitlenmis referansta digest ATILIR: takip edilen sey etikettir
    (ajan da boyle davranir). Ayristirilamayan referansta None.
    """
    text = (ref or "").strip()
    if not text:
        return None
    text = text.split("@", 1)[0]  # repo:tag@sha256:... -> repo:tag
    if not text:
        return None

    parcalar = text.split("/")
    ilk = parcalar[0]
    # Ilk parca ancak nokta/iki nokta iceriyorsa (veya localhost ise) HOST'tur;
    # aksi halde Docker Hub kastediliyor ("nginx", "library/nginx").
    if len(parcalar) > 1 and ("." in ilk or ":" in ilk or ilk == "localhost"):
        host = ilk
        kalan = "/".join(parcalar[1:])
    else:
        host = "registry-1.docker.io"
        kalan = text if len(parcalar) > 1 else f"library/{text}"

    # Etiket son ":"dan sonra — ama o ":" son "/"dan SONRA olmali, yoksa
    # host portudur ("localhost:5000/x").
    tag = "latest"
    egik = kalan.rfind("/")
    iki_nokta = kalan.rfind(":")
    if iki_nokta > egik:
        tag = kalan[iki_nokta + 1 :] or "latest"
        kalan = kalan[:iki_nokta]
    if not kalan:
        return None
    return host, kalan, tag


# ---------------------------------------------------------------------------
# Kayit defteri sorgusu
# ---------------------------------------------------------------------------
def _token(host: str, repo: str) -> str | None:
    """Anonim pull token'i. Alinamazsa None (token'siz denenir)."""
    try:
        resp = requests.get(
            f"https://{host}/token",
            params={"service": host, "scope": f"repository:{repo}:pull"},
            timeout=_HTTP_TIMEOUT_SEC,
        )
        if resp.status_code != 200:
            return None
        return resp.json().get("token")
    except Exception:  # noqa: BLE001 - token yoksa token'siz denenir
        return None


def _get(url: str, token: str | None, accept: str) -> requests.Response:
    headers = {"Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return requests.get(url, headers=headers, timeout=_HTTP_TIMEOUT_SEC)


def _platform_manifest(index: dict) -> str | None:
    """Index icinden calistirilabilir mimarinin manifest digest'i.

    ATTESTATION girdileri ATLANIR: `platform.architecture == "unknown"`
    olanlar imza/SBOM kayitlaridir, icinde `config` yoktur ve surum etiketi
    onlardan okunamaz.
    """
    girdiler = index.get("manifests") or []
    adaylar = [
        m
        for m in girdiler
        if isinstance(m, dict)
        and (m.get("platform") or {}).get("architecture") not in (None, "unknown")
    ]
    for m in adaylar:
        if (m.get("platform") or {}).get("architecture") == "amd64":
            return m.get("digest")
    return adaylar[0].get("digest") if adaylar else None


def fetch(image_ref: str) -> RegistryImage:
    """Etiketin digest'ini ve surumunu kayit defterinden oku (BLOKLAR).

    Uc istek: token -> manifest -> config blob. Surum etiketi imajin config
    blob'unda durur; imaji INDIRMEK gerekmez.
    """
    ayristirilmis = parse_image_ref(image_ref)
    if ayristirilmis is None:
        return RegistryImage(error="Imaj referansi ayristirilamadi")
    host, repo, tag = ayristirilmis

    try:
        token = _token(host, repo)
        base = f"https://{host}/v2/{repo}"
        resp = _get(f"{base}/manifests/{tag}", token, _ACCEPT)
        if resp.status_code == 401 or resp.status_code == 403:
            return RegistryImage(
                error="Kayit defteri yetki istiyor (paket public degil olabilir)"
            )
        if resp.status_code == 404:
            return RegistryImage(error=f"Etiket kayit defterinde yok: {tag}")
        if resp.status_code != 200:
            return RegistryImage(error=f"Kayit defteri yanit vermedi (HTTP {resp.status_code})")

        digest = resp.headers.get("Docker-Content-Digest") or None
        govde = resp.json()

        # Surum etiketi icin config blob'una inilir. Index geldiyse once
        # mimariye ait manifest cekilir.
        config_digest: str | None = None
        if govde.get("manifests"):
            alt = _platform_manifest(govde)
            if alt:
                alt_resp = _get(f"{base}/manifests/{alt}", token, _ACCEPT)
                if alt_resp.status_code == 200:
                    config_digest = ((alt_resp.json().get("config") or {}).get("digest")) or None
        else:
            config_digest = ((govde.get("config") or {}).get("digest")) or None

        version: str | None = None
        if config_digest:
            blob = _get(f"{base}/blobs/{config_digest}", token, "application/json")
            if blob.status_code == 200:
                etiketler = ((blob.json().get("config") or {}).get("Labels")) or {}
                ham = etiketler.get(_VERSION_LABEL)
                if isinstance(ham, str) and ham.strip():
                    version = ham.strip()[:40]

        if not digest and not version:
            return RegistryImage(error="Kayit defteri yanitinda digest/surum yok")
        return RegistryImage(version=version, digest=digest)
    except Exception as exc:  # noqa: BLE001 - ag/DNS/TLS: sebebi disariya ver
        logger.warning("gateway_release_lookup_failed image=%s error=%s", image_ref, exc)
        return RegistryImage(error=f"Kayit defterine ulasilamadi: {type(exc).__name__}")


def _refresh_async(image_ref: str) -> None:
    """Arka planda tazele — istek icinde ag beklenmesin."""
    with _lock:
        if image_ref in _inflight:
            return
        _inflight.add(image_ref)

    def calis() -> None:
        try:
            sonuc = fetch(image_ref)
            with _lock:
                _cache[image_ref] = (time.monotonic(), sonuc)
        finally:
            with _lock:
                _inflight.discard(image_ref)

    threading.Thread(target=calis, name="gw-release-lookup", daemon=True).start()


def lookup(image_ref: str) -> tuple[RegistryImage | None, bool]:
    """(onbellekteki_deger, tazeleme_suruyor).

    BLOKLAMAZ: taze deger varsa onu doner; bayat/yoksa arka plan tazelemesi
    baslatir ve `pending=True` bildirir — arayuz "sorgulanıyor" gosterip
    birazdan tekrar sorabilir.
    """
    if not image_ref:
        return None, False
    simdi = time.monotonic()
    with _lock:
        kayit = _cache.get(image_ref)
        suruyor = image_ref in _inflight
    if kayit is not None:
        yas = simdi - kayit[0]
        ttl = TTL_OK_SEC if kayit[1].error is None else TTL_FAIL_SEC
        if yas < ttl:
            return kayit[1], suruyor
    _refresh_async(image_ref)
    return (kayit[1] if kayit else None), True


def clear_cache() -> None:
    """Testler ve elle tazeleme icin."""
    with _lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# Ajan durumunu zenginlestirme
# ---------------------------------------------------------------------------
def enrich_agent_status(durum: GatewayAgentStatus) -> GatewayAgentStatus:
    """Ajanin bildiremedigi uzak surum/digest bilgisini kayit defterinden tamamla.

    KURAL: ajanin kendi degeri VARSA ona dokunulmaz — cihazin gordugu sey
    gerceklige en yakin olandir (ozel kayit defteri, ayna, cevrimdisi kopya).
    Yalnizca bos alanlar doldurulur.
    """
    for gw in durum.gateways:
        takip = (gw.tracked_image or gw.image or "").strip()
        if not takip:
            continue
        if gw.remote_digest and gw.remote_version:
            gw.remote_source = "agent"
            continue

        deger, suruyor = lookup(takip)
        gw.remote_pending = suruyor and deger is None
        if deger is None:
            continue
        if deger.error:
            # Ajan da bilmiyordu, kayit defteri de cevap vermedi: SEBEBI yaz.
            # "Bilinmiyor" demek yeterli degil — operator neyi duzeltecegini
            # bilmeli (paket private mi, cihaz internete cikmiyor mu).
            gw.remote_error = deger.error
            continue

        gw.remote_source = "registry"
        if not gw.remote_digest and deger.digest:
            gw.remote_digest = deger.digest
        if not gw.remote_version and deger.version:
            gw.remote_version = deger.version
        # Karar HALA digest karsilastirmasi. Yerel digest bilinmiyorsa
        # (imaj elle kurulmus, RepoDigests yok) "guncel/degil" DEMEYIZ.
        if gw.update_available is None and gw.image_digest and gw.remote_digest:
            gw.update_available = gw.image_digest != gw.remote_digest
    return durum
