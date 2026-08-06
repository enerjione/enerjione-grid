"""FTP dosya erisimi — gomulu volume ya da harici sunucu, TEK kapi.

"Cihaza uygula" akisi ve harici mod yoklamasi bu modulu kullanir. Cagiran
taraf modu BILMEZ: `write_config` / `read_remote_configs` ayarlara bakip
dogru yolu secer.

  gomulu : FTP koku bizim volume'umuz (`FTP_ROOT`, backend'e de bagli).
           Dosya islemleri duz dosya sistemi islemidir — ftplib GEREKMEZ,
           kendi sunucumuza TCP'den baglanmak anlamsiz dolambac olurdu.
  harici : musterinin sunucusuna stdlib `ftplib` ile cikilir. Yeni bagimlilik
           YOK — tek ihtiyac STOR/RETR/LIST ve ftplib bunlarin hepsini yapar.

YAZMA ATOMIKLIGI
----------------
Cihaz, `config_update` komutunu aldiginda dosyayi HEMEN okuyabilir. Yarim
yazilmis bir config'in cihazca okunmasi tanimsiz davranistir. Bu yuzden iki
modda da once gecici ada yazilir, sonra tek hamlede asil ada tasinir
(os.replace / RNFR+RNTO). Rename'i desteklemeyen harici sunucuda duz STOR'a
dusulur — riskli ama calisir; log'da gorunur.

DIZIN STRATEJISI
----------------
Cihazin FTP ekranindaki "Dir" alani nereye bakacagini belirler (orn.
/SN20/FOTA/).

  gomulu : en guvenilir ipucu cihazin DAHA ONCE yazdigi dosyanin yeridir —
           yazarken once mevcut `<seri>_Configuration.csv` aranir ve
           BULUNDUGU YERE yazilir; hic yoksa ayarlardaki `directory`.
  harici : DOGRUDAN ayarlardaki `directory` — sozlesme acik (herkes ayni
           dizini kullanir) ve WAN uzerinde agac taramak hem yavasti hem
           baglantiyi erken kapatan sunucularda yazmayi dusuruyordu.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from ftplib import FTP, all_errors as ftp_errors, error_perm
from io import BytesIO

from sqlalchemy.orm import Session

from app.services import ftp_settings_service

logger = logging.getLogger(__name__)

#: Gomulu modun kok dizini — ftp-server ile paylasilan volume.
FTP_ROOT = os.getenv("FTP_ROOT", "/data/ftp")

#: Harici sunucu baglanti zaman asimi (saniye). Uzun tutmak, arayuzdeki
#: "Cihaza uygula" istegini dakikalarca asili birakmaktan baska ise yaramaz.
TIMEOUT_SEC = 15

#: Dizin taramasinda inilen en fazla derinlik (kok=0). Cihazlar tipik olarak
#: /SNxx/FOTA/ gibi iki seviye kullanir; sinirsiz tarama, yanlis yapilandirilmis
#: bir sunucuda sonsuz gezinmeye donusebilirdi.
_MAX_DEPTH = 2

#: Okunacak config dosyasi ust siniri (bayt). Gercek dosya ~1 KB.
MAX_CONFIG_BYTES = 1024 * 1024


class FtpAccessError(Exception):
    """FTP islemi basarisiz — mesaj kullaniciya gosterilebilir."""


def _hata(exc: BaseException) -> str:
    """Istisnayi BOS OLMAYAN metne cevirir.

    `EOFError` gibi bazi istisnalarin str()'i bostur; sahada kullanici
    "Harici FTP'ye yazilamadi: " diye biten, sebepsiz bir uyari gordu.
    Metin yoksa istisna turunun adi gosterilir (orn. "EOFError — baglanti
    sunucu tarafindan kapatildi" tespiti oradan yapilir).
    """
    metin = str(exc).strip()
    return metin or type(exc).__name__


@dataclass(frozen=True)
class RemoteConfig:
    """Uzak sunucuda bulunan bir `<seri>_Configuration.csv`."""

    path: str        # dizin + ad (sunucudaki tam yol)
    filename: str
    size: int | None
    mtime: str | None  # MDTM ham degeri (YYYYMMDDHHMMSS) — karsilastirma icin


# --- baglanti ---------------------------------------------------------------
def _connect(db: Session) -> tuple[FTP, str]:
    """Ayarlardaki harici sunucuya baglanir; (ftp, taban_dizin) doner."""
    ayar = ftp_settings_service.get_settings(db)
    if not ayar.host:
        raise FtpAccessError("Harici FTP sunucu adresi ayarlanmamis.")
    parola = ftp_settings_service.get_password(ayar)
    if not parola:
        raise FtpAccessError("FTP parolasi ayarlanmamis.")
    try:
        ftp = FTP()
        ftp.connect(ayar.host, int(ayar.port or 21), timeout=TIMEOUT_SEC)
    except OSError as exc:
        raise FtpAccessError(f"FTP sunucusuna baglanilamadi: {_hata(exc)}") from exc
    try:
        ftp.login(ayar.username, parola)
    except error_perm as exc:
        _quit(ftp)
        raise FtpAccessError(f"FTP kimlik dogrulamasi reddedildi: {_hata(exc)}") from exc
    except ftp_errors as exc:
        # 4xx/bozuk yanit dahil TUM ftplib hatalari. Daha once yalnizca
        # error_perm yakalaniyordu; error_temp/error_reply 500'e donusuyor ve
        # kullanici gercek sebebi goremiyordu ("Baglanti sinanamadi").
        _quit(ftp)
        raise FtpAccessError(f"FTP oturumu acilamadi: {_hata(exc)}") from exc
    return ftp, (ayar.directory or "/")


def _remote_dirs(ftp: FTP, base: str) -> list[str]:
    """Taban dizin + sinirli derinlikte alt dizinler.

    MLSD her sunucuda yok; NLST + cwd denemesiyle ayirt edilir: bir ada cwd
    yapilabiliyorsa dizindir. Yavas gorunur ama dosya sayisi kucuk (cihaz
    basina 1-2 dosya) ve yoklama araligi dakikalar mertebesinde.
    """
    dirs = [base]
    sinir = [(base, 0)]
    while sinir:
        yol, derinlik = sinir.pop()
        if derinlik >= _MAX_DEPTH:
            continue
        try:
            adlar = ftp.nlst(yol)
        except ftp_errors:
            continue
        for ad in adlar:
            # NLST bazen tam yol, bazen yalnizca ad doner — ikisini de ele al.
            tam = ad if ad.startswith("/") or ad.startswith(yol) else f"{yol.rstrip('/')}/{ad}"
            if tam.rstrip("/") == yol.rstrip("/"):
                continue
            try:
                onceki = ftp.pwd()
                ftp.cwd(tam)
                ftp.cwd(onceki)
            except ftp_errors:
                continue  # dosya (ya da girilemeyen dizin)
            dirs.append(tam)
            sinir.append((tam, derinlik + 1))
    return dirs


def read_remote_configs(db: Session) -> list[RemoteConfig]:
    """Harici sunucudaki `<seri>_Configuration.csv` dosyalarini listeler."""
    import re

    desen = re.compile(r"^[A-Za-z0-9]{1,20}_Configuration\.csv$")
    ftp, taban = _connect(db)
    try:
        sonuc: list[RemoteConfig] = []
        for dizin in _remote_dirs(ftp, taban):
            try:
                adlar = ftp.nlst(dizin)
            except ftp_errors:
                continue
            for ad in adlar:
                dosya = ad.rsplit("/", 1)[-1]
                if not desen.match(dosya):
                    continue
                tam = ad if ad.startswith("/") else f"{dizin.rstrip('/')}/{dosya}"
                boyut: int | None = None
                zaman: str | None = None
                try:
                    boyut = ftp.size(tam)
                except ftp_errors:
                    pass
                try:
                    yanit = ftp.voidcmd(f"MDTM {tam}")
                    zaman = yanit.split(" ", 1)[-1].strip()
                except ftp_errors:
                    pass
                sonuc.append(RemoteConfig(path=tam, filename=dosya, size=boyut, mtime=zaman))
        return sonuc
    finally:
        _quit(ftp)


def download_remote(db: Session, path: str) -> bytes:
    """Uzak dosyayi indirir. Boyut siniri asilirsa keser ve hata verir."""
    ftp, _ = _connect(db)
    try:
        tampon = BytesIO()

        def _yaz(parca: bytes) -> None:
            if tampon.tell() + len(parca) > MAX_CONFIG_BYTES:
                raise FtpAccessError(f"Dosya cok buyuk (> {MAX_CONFIG_BYTES} bayt): {path}")
            tampon.write(parca)

        try:
            ftp.retrbinary(f"RETR {path}", _yaz)
        except ftp_errors as exc:
            raise FtpAccessError(f"Dosya indirilemedi: {path} ({_hata(exc)})") from exc
        return tampon.getvalue()
    finally:
        _quit(ftp)


def ensure_embedded_dir(directory: str) -> str:
    """Gomulu volume'da dizini olusturur (varsa dokunmaz); tam yolu doner.

    Ayarlar kaydedilirken cagrilir: cihaz FTP ekranindaki "Dir" degeri var
    olmayan bir dizini gosterirse cihaz 550 alip durur ve bu sahada "baglandi
    ama dosya gitmiyor" olarak gorunur. ftp-server acilista standart dizini
    (/SN20/FOTA/) zaten kurar; burasi ARAYUZDEN secilen farkli dizinleri de
    kapsar.
    """
    alt = (directory or "/").strip("/")
    hedef = os.path.join(FTP_ROOT, alt) if alt else FTP_ROOT
    if not os.path.realpath(hedef).startswith(os.path.realpath(FTP_ROOT)):
        raise FtpAccessError(f"FTP koku disinda dizin: {directory!r}")
    os.makedirs(hedef, exist_ok=True)
    return hedef


def find_config_on_ftp(db: Session, filename: str) -> bytes | None:
    """FTP'de `filename` var mi diye bakar; varsa icerigini dondurur.

    "Cihazdan cek" DNP3 komutu calismadiginda (sahada su an oyle) dosya
    cihaz tarafindan ZATEN yazilmis olabilir — sistem sormadan once FTP'ye
    kendisi bakar. None = dosya yok (hata DEGIL); erisim sorunu ise
    FtpAccessError firlatilir ki kullanici sebebi gorsun.
    """
    ayar = ftp_settings_service.get_settings(db)
    if ayar.mode != "harici":
        for kok_dizin, _dirs, dosyalar in os.walk(FTP_ROOT):
            if filename in dosyalar:
                yol = os.path.join(kok_dizin, filename)
                if os.path.getsize(yol) > MAX_CONFIG_BYTES:
                    raise FtpAccessError(f"Dosya cok buyuk: {filename}")
                with open(yol, "rb") as fh:
                    return fh.read()
        return None

    # Harici mod: once sozlesme geregi beklenen yol (taban dizin), o yoksa
    # sinirli tarama — cihaz farkli bir alt dizine yazmis olabilir.
    ftp, taban = _connect(db)
    try:
        tampon = BytesIO()

        def _yaz(parca: bytes) -> None:
            if tampon.tell() + len(parca) > MAX_CONFIG_BYTES:
                raise FtpAccessError(f"Dosya cok buyuk: {filename}")
            tampon.write(parca)

        try:
            ftp.retrbinary(f"RETR {taban.rstrip('/')}/{filename}", _yaz)
            return tampon.getvalue()
        except error_perm:
            pass  # taban dizinde yok — sinirli taramaya dus
        except ftp_errors as exc:
            raise FtpAccessError(f"Dosya okunamadi: {_hata(exc)}") from exc

        for dizin in _remote_dirs(ftp, taban):
            try:
                adlar = ftp.nlst(dizin)
            except ftp_errors:
                continue
            for ad in adlar:
                if ad.rsplit("/", 1)[-1] == filename:
                    tam = ad if ad.startswith("/") else f"{dizin.rstrip('/')}/{filename}"
                    tampon = BytesIO()
                    try:
                        ftp.retrbinary(f"RETR {tam}", _yaz)
                    except ftp_errors as exc:
                        raise FtpAccessError(f"Dosya okunamadi: {_hata(exc)}") from exc
                    return tampon.getvalue()
        return None
    finally:
        _quit(ftp)


# --- yazma ------------------------------------------------------------------
def write_config(db: Session, *, filename: str, raw: bytes) -> str:
    """Config dosyasini cihazin gorecegi yere yazar; yazilan yolu doner.

    Mod ayarina gore gomulu volume'a ya da harici sunucuya gider — cagiran
    taraf ayrimi bilmez.
    """
    ayar = ftp_settings_service.get_settings(db)
    if ayar.mode == "harici":
        return _write_external(db, filename=filename, raw=raw)
    return _write_embedded(db, filename=filename, raw=raw)


def _write_embedded(db: Session, *, filename: str, raw: bytes) -> str:
    """Gomulu mod: paylasilan volume'a dogrudan yazar (once tmp, sonra replace)."""
    ayar = ftp_settings_service.get_settings(db)
    hedef_dizin: str | None = None

    # Cihazin daha once yazdigi dosya nerede? Ayni yere yaz.
    for kok_dizin, _dirs, dosyalar in os.walk(FTP_ROOT):
        if filename in dosyalar:
            hedef_dizin = kok_dizin
            break

    if hedef_dizin is None:
        # Hic yoksa ayarlardaki dizine (kok'e gore) yaz.
        alt = (ayar.directory or "/").strip("/")
        hedef_dizin = os.path.join(FTP_ROOT, alt) if alt else FTP_ROOT
        try:
            os.makedirs(hedef_dizin, exist_ok=True)
        except OSError as exc:
            raise FtpAccessError(f"FTP dizini olusturulamadi: {_hata(exc)}") from exc

    hedef = os.path.join(hedef_dizin, filename)
    # Kok disina cikma korumasi — directory ayari kullanicidan geliyor.
    if not os.path.realpath(hedef).startswith(os.path.realpath(FTP_ROOT)):
        raise FtpAccessError(f"FTP koku disinda hedef: {hedef}")

    gecici = hedef + ".tmp"
    try:
        with open(gecici, "wb") as f:
            f.write(raw)
        os.replace(gecici, hedef)
    except OSError as exc:
        try:
            os.unlink(gecici)
        except OSError:
            pass
        raise FtpAccessError(f"FTP volume'una yazilamadi: {_hata(exc)}") from exc

    rel = os.path.relpath(hedef, FTP_ROOT).replace(os.sep, "/")
    logger.info("config gomulu FTP'ye yazildi: %s (%d bayt)", rel, len(raw))
    return rel


def _write_external(db: Session, *, filename: str, raw: bytes) -> str:
    """Harici mod: musterinin sunucusuna STOR (tmp + RNTO ile atomik).

    DOGRUDAN ayarlardaki dizine yazilir — dizin taramasi YAPILMAZ. Harici
    modda sozlesme acik: cihazlar ve bu yazilim AYNI yapilandirilmis dizini
    kullanir. Onceki surum once tum agaci tarayip dosyayi ariyordu; WAN
    uzerinde onlarca gidis-donus demekti ve baglantiyi erken kapatan
    sunucularda yazma EOFError ile dusuyordu (sahada yasandi).
    """
    ftp, taban = _connect(db)
    try:
        hedef_dizin = taban
        hedef = f"{hedef_dizin.rstrip('/')}/{filename}"
        gecici = f"{hedef_dizin.rstrip('/')}/.tmp_{filename}"
        try:
            ftp.storbinary(f"STOR {gecici}", BytesIO(raw))
            try:
                # Var olan dosyanin ustune rename cogu sunucuda calisir;
                # calismayan sunucuda once sil, sonra tasi.
                try:
                    ftp.rename(gecici, hedef)
                except error_perm:
                    try:
                        ftp.delete(hedef)
                    except error_perm:
                        pass
                    ftp.rename(gecici, hedef)
            except error_perm:
                # Rename hic desteklenmiyor: duz STOR'a dus. Atomik degil ama
                # calisir; iz log'da kalsin.
                logger.warning(
                    "harici FTP rename desteklemiyor, duz STOR kullanildi: %s", hedef
                )
                ftp.storbinary(f"STOR {hedef}", BytesIO(raw))
                try:
                    ftp.delete(gecici)
                except ftp_errors:
                    pass
        except ftp_errors as exc:
            raise FtpAccessError(f"Harici FTP'ye yazilamadi: {_hata(exc)}") from exc

        logger.info("config harici FTP'ye yazildi: %s (%d bayt)", hedef, len(raw))
        return hedef
    finally:
        _quit(ftp)


# --- sinama -----------------------------------------------------------------
def test_connection(db: Session) -> tuple[bool, str, int | None]:
    """Harici sunucuya baglan, dizine gir, TABAN dizindeki config'leri say.

    (ok, detay, config_dosya_sayisi) doner. HICBIR KOSULDA hata firlatmaz —
    olumsuz sonuc da bir SONUCTUR ve `detail` gercek sebebi soyler. Eskiden
    yakalanmayan bir ftplib hatasi 500'e donusuyor ve kullanici yalnizca
    "Baglanti sinanamadi" goruyordu; sunucusuna mi, kimlige mi, dizine mi
    takildigini bilemiyordu.

    Tarama SIG tutulur (yalnizca taban dizin): sinamanin isi erisimi
    dogrulamak, envanter cikarmak degil. Alt dizinleri de gezen derin tarama
    WAN uzerinde onlarca gidis-donus demek ve istegi zaman asimina surukler;
    derin islerin yeri yoklama worker'i.
    """
    try:
        try:
            ftp, taban = _connect(db)
        except FtpAccessError as exc:
            return False, str(exc), None
        try:
            try:
                ftp.cwd(taban)
            except ftp_errors as exc:
                return False, f"Dizine girilemedi: {taban} ({_hata(exc)})", None
            try:
                import re

                desen = re.compile(r"^[A-Za-z0-9]{1,20}_Configuration\.csv$")
                sayi = sum(
                    1 for ad in ftp.nlst(taban) if desen.match(ad.rsplit("/", 1)[-1])
                )
                return True, "Baglanti basarili.", sayi
            except ftp_errors as exc:
                # Baglanti + login + cwd calisti; listeleme (veri kanali)
                # sorunu AYRI raporlanir — tipik sebep pasif mod portlarinin
                # guvenlik duvarinda kapali olmasi.
                return True, (
                    "Baglanti basarili ama dizin listelenemedi (veri kanali "
                    f"kurulamadi — pasif mod portlari kapali olabilir): {_hata(exc)}"
                ), None
        finally:
            _quit(ftp)
    except Exception as exc:  # noqa: BLE001 - sinama ASLA 500 uretmemeli
        logger.warning("ftp sinama beklenmeyen hata", exc_info=True)
        return False, f"Sinama basarisiz: {_hata(exc)}", None


def _quit(ftp: FTP) -> None:
    """Baglantiyi nazikce kapat; kapanis hatasi asil sonucu golgelemesin."""
    try:
        ftp.quit()
    except Exception:  # noqa: BLE001
        try:
            ftp.close()
        except Exception:  # noqa: BLE001
            pass
