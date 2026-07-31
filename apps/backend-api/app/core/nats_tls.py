"""NATS baglantilari icin TLS baglami.

NEDEN GEREKLI
-------------
NATS istemci portu (4222) tum arayuzlere aciktir ve gateway'ler ona
`nats://gateway:<parola>@<host>:4222` ile baglanir. TLS olmadan HEM gateway
parolasi HEM de tum telemetri DUZ METIN gider. Ayni agdaki (ya da 4G
yolundaki) biri parolayi yakalayip sahte telemetri enjekte edebilir: uydurma
kritik ariza uretmek ya da `fault_indicator`i normal gonderip GERCEK arizayi
maskelemek.

NEDEN AYRI BIR CA DOSYASI
-------------------------
Saha cihazlarinin genelde herkese acik bir alan adi/sabit IP'si yok, dolayisiyla
Let's Encrypt gibi bir otorite dogrulama yapamaz. Sertifikalar kuruluma ozel,
kendinden imzali bir CA ile uretilir (infra/scripts/linux/nats-tls-setup.sh).

`ssl.create_default_context(cafile=...)` YALNIZCA verilen CA'ya guvenir —
isletim sisteminin guven deposu KULLANILMAZ. Bu bilincli: aksi halde herkese
acik herhangi bir otoriteden alinmis bir sertifika da kabul edilirdi ve
kendinden imzali CA'nin sagladigi daraltma kaybolurdu.

VARSAYILAN: KAPALI
------------------
`NATS_CA_FILE` bos ise None doner ve baglanti bugunku gibi kurulur. TLS'i
acmak iki adimli bilincli bir islemdir (sertifika uret + bayragi ac); yarim
acilmis bir TLS tum telemetriyi durdururdu.
"""

from __future__ import annotations

import logging
import ssl

logger = logging.getLogger(__name__)

_cached: ssl.SSLContext | None = None
_cached_for: str | None = None


def nats_tls_context() -> ssl.SSLContext | None:
    """`NATS_CA_FILE` ayarliysa dogrulayici SSL baglami, degilse None.

    Onbellekli: her yeniden baglanmada sertifika dosyasini diskten okumak
    gereksiz I/O olurdu (tuketici kopukluk halinde saniyede bir deneyebilir).
    """
    global _cached, _cached_for

    from app.core.config import settings

    ca_file = (getattr(settings, "nats_ca_file", "") or "").strip()
    if not ca_file:
        return None
    if _cached is not None and _cached_for == ca_file:
        return _cached

    try:
        ctx = ssl.create_default_context(cafile=ca_file)
    except (OSError, ssl.SSLError):
        # Dosya yok/bozuk. TLS'siz devam ETMIYORUZ: sessizce duz metne dusmek,
        # operatorun "TLS acik" sandigi bir kurulumda parolayi acikta
        # gondermek olurdu. Cagiran taraf baglanti hatasi gorur ve sebebi
        # log'da yazar.
        logger.exception(
            "nats_tls_ca_unreadable path=%s — TLS baglami kurulamadi; "
            "baglanti TLS'siz DENENMEYECEK",
            ca_file,
        )
        raise

    # Sunucu adi dogrulamasi ACIK kalir (create_default_context varsayilani).
    # Kapatmak, dogru CA'dan alinmis ama BASKA bir makineye ait sertifikayi
    # kabul etmek demek olurdu.
    _cached = ctx
    _cached_for = ca_file
    logger.info("nats_tls_enabled ca_file=%s", ca_file)
    return ctx
