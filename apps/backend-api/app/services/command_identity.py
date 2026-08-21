"""KOMUT KIMLIGI — veritabani restore'undan BAGIMSIZ, tekrar kullanilamaz.

YASANAN OLAY
------------
Gateway GW-002'nin defterinde ve backend `device_commands` tablosunda AYNI
tamsayi kimlik (39-42) FARKLI TARIHLI, FARKLI komutlar icin tekrar
kullanildi. Gateway DOGRU davrandi: defterinde o kimligi gorup fiziksel
islemi TEKRARLAMADI ve eski dayanikli ACK'i yeniden gonderdi. Backend ise
yeni komut icin BASKA bir teslim jetonu bekledigi icin `token_mismatch`
uretti; `sent_at` dolmadi, 120 saniyelik tazelik penceresi doldu ve komut
`failed` oldu.

Yani kimse hata yapmadi: iki taraf da sozlesmeye uydu. HATALI OLAN SEY
KIMLIGIN KENDISIYDI.

NEDEN AUTOINCREMENT YETMIYOR
----------------------------
`device_commands.id` bir PostgreSQL SERIAL'iydi ve sequence'in degeri
VERITABANININ ICINDE yasiyor. Veritabani daha eski bir ana alindiginda
sequence de o ana donuyor ve daha once DAGITILMIS kimlikler yeniden
uretiliyor. Gateway defteri ise BASKA BIR MAKINEDE, BASKA BIR DISKTE
duruyor ve geri gitmiyor.

Bu bir "yanlis kod" degil, YANLIS KIMLIK KAYNAGI problemidir: kimlik,
kendisini tuketen sistemle ayni yasam dongusunu paylasmiyor.

TASARIM
-------
    kimlik = epoch_ms * 1000 + rastgele(0..999)

Uc ozellik birden gerekiyordu ve bu bicim ucunu de veriyor:

1. RESTORE'DAN BAGIMSIZ. Belirleyici bilesen DUVAR SAATIDIR; veritabani
   geri alinsa da saat geri gitmez. Restore sonrasi uretilen kimlik, restore
   ONCESI dagitilmis kimliklerden BUYUK olur.

2. MONOTON (yaklasik). Rastgele bir kimlik cazipti ama YANLIS olurdu:
   `GET /devices/{code}/commands` `order_by(id.desc()).limit(N)` ile "en
   yeni once" diyor. Sirasiz kimlikte en yeni komut listeye HIC GIRMEYEBILIR
   — operator gonderdigi komutu goremezdi. Milisaniye onekli bicim bu
   davranisi korur.

3. JAVASCRIPT'TE KAYIPSIZ. Arayuz kimligi `number` olarak tasiyor ve
   JavaScript 2^53 (9_007_199_254_740_991) uzerinde TAMSAYI HASSASIYETINI
   KAYBEDER. Bugun uretilen deger ~1.79e15; tavan 2255 yilinda gelir. Tam
   63-bit rastgele bir kimlik tarayicida SESSIZCE BOZULURDU.

NEDEN UUID DEGIL
----------------
Gateway defterinin idempotency anahtari `command_id INTEGER PRIMARY KEY`
(SQLite). Yalnizca Grid'e bir UUID eklemek gateway'in mukerrer-calistirma
korumasini DEGISTIRMEZDI — problem oldugu yerde kalirdi. UUID'ye gecmek
gateway sozlesmesi degisikligi gerektirir; bu is Grid ile sinirli.

NEDEN DB ICI HIGH-WATER TEK BASINA YETMEZ
-----------------------------------------
"En yuksek dagitilmis kimligi bir tabloda tut" cozumu ayni tuzaga duser:
o tablo da yedegin icindedir ve restore ile GERIYE DONER. Buradaki
`_son` sayaci bu yuzden TEK BASINA guvence degildir — yalnizca saatin
geri gitmesine karsi ikincil bir korumadir. Restore'a karsi guvence
duvar saatinin kendisidir.

CAKISMA MODELI
--------------
Ayni milisaniyede 1000 yuva var. Komutlar insan eylemleriyle ve
yapilandirma uygulamalariyla uretiliyor (saniyede tek haneli); ayni
milisaniyeye iki komut dusmesi bile nadir, ayni yuvaya dusmesi 1/1000.
Yine de sansa birakilmiyor: cakisma olursa birincil anahtar reddeder ve
cagiran taraf yeni bir kimlikle yeniden dener (`yeni_kimlik` her cagrida
taze deger uretir).
"""

from __future__ import annotations

import secrets
import threading
import time

#: Milisaniye basina yuva sayisi. Kimligin son uc hanesi rastgeledir.
#:
#: 1000 SECILDI cunku `epoch_ms * 1000` bugun ~1.79e15 uretiyor ve
#: JavaScript'in guvenli tamsayi tavani 9.007e15 — yani 2255 yilina kadar
#: yer var. Carpani buyutmek (or. 4096) yuva sayisini artirirdi ama tavani
#: 2039'a cekerdi; bu takas kabul edilmedi.
YUVA = 1000

#: Uretilebilecek en buyuk kimlik — JavaScript'in guvenli tamsayi tavani.
#: Bu sinir asilirsa arayuzde kimlik SESSIZCE bozulur.
AZAMI_KIMLIK = 9_007_199_254_740_991

_kilit = threading.Lock()
_son = 0


def _simdi_ms() -> int:
    """Duvar saati (epoch, milisaniye).

    MONOTONIK SAAT KULLANILMAZ ve bu bilincli: `time.monotonic()` surec
    baslangicina goredir, yani yeniden baslatmada sifirlanir ve restore'a
    karsi hicbir sey soylemez. Buradaki tek amac RESTORE'DAN BAGIMSIZ,
    ILERI GIDEN bir referans.
    """
    return int(time.time() * 1000)


def yeni_kimlik() -> int:
    """Yeni bir komut kimligi uretir.

    Her cagri TAZE deger dondurur; cakisma halinde cagiran taraf yeniden
    cagirabilir.
    """
    global _son
    with _kilit:
        taban = _simdi_ms() * YUVA
        # SAAT GERI GIDERSE (NTP duzeltmesi) bu surecte uretilmis en yuksek
        # kimligin ALTINA DUSMEYIZ. Bu, restore'a karsi degil SAAT
        # SICRAMASINA karsi bir korumadir; restore korumasi saatin kendisi.
        if taban <= _son:
            taban = _son + 1
        kimlik = taban + secrets.randbelow(YUVA)
        if kimlik <= _son:
            kimlik = _son + 1
        _son = kimlik
    return kimlik


def taban_yukselt(en_yuksek: int | None) -> None:
    """Bilinen en yuksek kimligi surec sayacina isler.

    Acilista `max(device_commands.id)` ile cagrilir. TEK BASINA BIR
    GUVENCE DEGILDIR — o deger de yedegin icindedir ve restore ile geriye
    doner. Amaci dar: saat geri gitmisken uretilen kimligin, ayni
    veritabaninda ZATEN duran bir kimlikle cakismasini engellemek.
    """
    global _son
    if en_yuksek is None:
        return
    with _kilit:
        if en_yuksek > _son:
            _son = int(en_yuksek)


def eski_kimlik_mi(kimlik: int) -> bool:
    """Kimlik ESKI sequence semasindan mi?

    Gecis sonrasi tablodaki kimlikler iki kusaktan gelir: eski sequence
    (kucuk sayilar) ve yeni uretici (~1.79e15). Ayrim yalnizca TESHIS
    icindir — okuma, ACK ve sonuc yollari IKISINI DE aynen isler.
    """
    return 0 < kimlik < 1_000_000_000_000


__all__ = ["AZAMI_KIMLIK", "YUVA", "eski_kimlik_mi", "taban_yukselt", "yeni_kimlik"]
