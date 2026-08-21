"""Log gosterimi icin credential maskeleme.

BU DOSYA BIR KOPYADIR — kanonik surum:
`apps/backend-api/app/core/redaction.py`.

Servisler AYRI imajlar ve aralarinda ortak bir Python paketi yok; paket
kurmak bes Dockerfile'i degistirmek demekti. Repo'nun bu duruma verdigi
cevap KOPYA + PARITE TESTI (ayni kalip `gateway_compose` renderer'inda
da var): kopyalar bayt bayt ayni olmak zorunda ve bunu test kilitler
(`test_nats_credential_redaction.py`).

NE COZUYOR
----------
Baglanti URL'leri kimlik tasiyor:

    nats://backend:SuperSecretPassword@nats:4222

Bu deger `logger.info("jetstream_bus_ready url=%s", self._url)` gibi
satirlarda DUZ METIN olarak log'a dusuyordu. Log'lar teshis icin destege
gonderiliyor, `docker logs` ile ekrana basiliyor ve saha cihazinda diskte
duruyor — yani parola bir anda uc ayri yerde kopyalanmis oluyor.

YALNIZCA GOSTERIM ICIN
----------------------
Bu modul CALISMA ZAMANI URL'INI DEGISTIRMEZ. NATS istemcisine giden deger
ham haliyle gitmeye devam eder; maskelenmis metin yalnizca log satirinda
kullanilir. Maskelenmis URL'yi baglanti icin kullanmak, sifresi "***" olan
bir sunucuya baglanmayi denemek demektir.

FAIL-SAFE
---------
Ayristirilamayan bir girdide HAM DEGER GERI DONMEZ. Ayristiramadigimiz bir
metin, icinde parola OLMADIGI anlamina gelmez; supheli girdiyi oldugu gibi
log'a basmak tam da onlemeye calistigimiz seydir. Bu yuzden cozulemeyen
girdi `<gecersiz-url>` olur.

Bu fonksiyon HICBIR KOSULDA firlatmaz: log yolunda atilan bir istisna,
teshis icin yazilan satiri ikinci bir arizaya cevirirdi.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#: Maskesi konulan sorgu parametreleri (kucuk harfle karsilastirilir).
_SECRET_QUERY_KEYS = frozenset(
    {"password", "passwd", "pass", "token", "secret", "api_key", "apikey", "auth"}
)

#: Maskelenmis degerin gosterimi.
MASK = "***"

#: Cozulemeyen girdi icin. Ham deger DONDURULMEZ.
_INVALID = "<gecersiz-url>"


def _redact_userinfo(netloc: str) -> str:
    """`user:pass@host:port` -> `user:***@host:port`.

    KOLONSUZ USERINFO DA MASKELENIR. NATS'ta `nats://TOKEN@host:4222`
    bicimi TOKEN KIMLIK DOGRULAMASIDIR — orada tek parca olan sey kullanici
    adi degil, sirrin ta kendisidir. "Kullanici adi gorunur kalabilir"
    kuralini duz uygulamak bu bicimde tokeni log'a basardi.
    """
    if "@" not in netloc:
        return netloc
    # Son "@" ayiricidir: parola icinde "@" olabilir.
    userinfo, _, host = netloc.rpartition("@")
    if not userinfo:
        return host
    if ":" in userinfo:
        kullanici, _, _parola = userinfo.partition(":")
        return f"{kullanici}:{MASK}@{host}"
    return f"{MASK}@{host}"


def _redact_query(query: str) -> str:
    """Sorgu dizesindeki sir tasiyan parametreleri maskele."""
    if not query:
        return query
    # `keep_blank_values`: `?token=` gibi bos deger de korunur, kaybolmaz.
    ciftler = parse_qsl(query, keep_blank_values=True)
    if not ciftler:
        return query
    temiz = [
        (ad, MASK if ad.lower() in _SECRET_QUERY_KEYS else deger)
        for ad, deger in ciftler
    ]
    # `safe="*"`: maske yildizlari percent-encode EDILMEZ. Aksi halde
    # log'da `token=%2A%2A%2A` gorunur — okunmasi zor ve maskelendigi
    # ilk bakista anlasilmaz.
    return urlencode(temiz, safe="*")


def _redact_single(url: str) -> str:
    parcalar = urlsplit(url)
    # Sema yoksa bu bir URL degil; ham metni log'a koymayiz.
    if not parcalar.scheme or not parcalar.netloc:
        return _INVALID
    return urlunsplit(
        (
            parcalar.scheme,
            _redact_userinfo(parcalar.netloc),
            parcalar.path,
            _redact_query(parcalar.query),
            parcalar.fragment,
        )
    )


def redact_url_credentials(url: object) -> str:
    """Baglanti URL'sinin LOG GOSTERIMI. Calisma zamani degeri degismez.

    NATS birden fazla sunucuyu VIRGULLE ayrilmis tek bir dizede kabul eder
    (`nats://a:1@h1:4222,nats://b:2@h2:4222`); her parca ayri ayri
    maskelenir, yoksa ikinci sunucunun parolasi log'a duserdi.
    """
    try:
        if url is None:
            return ""
        metin = str(url).strip()
        if not metin:
            return ""
        return ",".join(_redact_single(p.strip()) for p in metin.split(","))
    except Exception:  # noqa: BLE001
        # Log yolunda ISTISNA OLMAZ. Neyin patladigini bilmiyoruz; ham
        # degeri dondurmek de yasak, o yuzden sabit bir isaret.
        return _INVALID


__all__ = ["MASK", "redact_url_credentials"]
