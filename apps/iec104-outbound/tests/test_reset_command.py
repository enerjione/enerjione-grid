"""IEC 104 RESET komutu (C_SC_NA_1) — uctan uca.

URUN KARARI
-----------
IEC 104 uzerinden kabul edilen TEK kontrol komutu ariza gostergesi
RESET'idir (`reset_all_fcis`). Analog cikis / setpoint ve diger kontrol
tipleri kapsam disi.

NEDEN IKI KATMANLI KORUMA
-------------------------
Tip filtresi (45) TEK BASINA YETMEZ. `PATCH /signals/{key}` katalogdaki
`iec104_type_id` alanini duzenlenebilir yapiyor; biri `firmware_update`
veya `config_update` gibi bir binary_output'a 45 verirse, yalnizca tipe
bakan bir kontrol o komutu SCADA'ya acardi — yani yanlis bir katalog
duzenlemesi UZAKTAN FIRMWARE TETIKLEMEYE donusurdu. Bu yuzden slug
allowlist'i de var ve backend tarafinda BIR KEZ DAHA dogrulaniyor.

ONAY ile SONLANDIRMANIN ANLAMI FARKLI
-------------------------------------
    ACT_CON  (COT=7)  -> komut KABUL EDILDI (kuyruga alindi)
    ACT_TERM (COT=10) -> komut TAMAMLANDI  (cihaz sonucu bildirdi)

Cihaz NAT arkasindaki gateway'in ardinda; komut ona config-poll ile gidiyor
(~30 sn). Ikisini birlestirip ACT_CON'u sonuc yerine kullanmak, SCADA
operatorune "reset yapildi" demek olurdu — oysa yalnizca kuyruga alinmistir.
Bu, depoda kapatilan "yesil yalan" sinifinin ta kendisi.
"""

from __future__ import annotations

import asyncio
import socket
import struct

import pytest

from iec104_outbound.encoder import (
    COT_ACTIVATION,
    COT_ACTIVATION_CON,
    COT_ACTIVATION_TERM,
    TYPE_C_SC_NA_1,
    _encode_dui,
    _encode_ioa,
    parse_single_command,
)
from iec104_outbound.registry import (
    ALLOWED_COMMAND_SLUGS,
    build_point_registry,
)

CA = 7
RESET_IOA = 9001
STARTDT_ACT = bytes([0x68, 0x04, 0x07, 0x00, 0x00, 0x00])


def _cihaz(kod="DEV-001", ca=CA):
    return {"code": kod, "is_active": True, "iec104_common_address": ca}


def _komut_sinyali(key, ioa=RESET_IOA):
    return {
        "key": key,
        "is_active": True,
        "data_type": "binary_output",
        "iec104_type_id": TYPE_C_SC_NA_1,
        "iec104_ioa": ioa,
    }


# ---------------------------------------------------------------------------
# Registry — hangi komut kabul ediliyor
# ---------------------------------------------------------------------------

def test_izin_verilen_komut_YALNIZCA_reset():
    assert ALLOWED_COMMAND_SLUGS == frozenset({"reset_all_fcis"})


def test_reset_komutu_REGISTRY_e_giriyor():
    reg = build_point_registry(
        target_id=1, default_common_address=CA,
        devices=[_cihaz()], signals=[_komut_sinyali("master.reset_all_fcis")],
    )
    assert len(reg.commands) == 1
    c = reg.commands[0]
    assert c.command_slug == "reset_all_fcis"
    assert (c.common_address, c.ioa) == (CA, RESET_IOA)
    # Kontrol noktasi IZLEME noktasi olarak yayinlanmamali.
    assert reg.points == (), "kontrol noktasi izleme listesine sizmis"


@pytest.mark.parametrize(
    "slug",
    ["firmware_update", "config_update", "trigger_firmware_download",
     "dnp3_config_update", "reset_tamper_alarm"],
)
def test_IZINSIZ_binary_output_tip_45_ile_bile_giremiyor(slug: str):
    """Katalog duzenlemesiyle uzaktan firmware tetikleme yolunu kapatir."""
    reg = build_point_registry(
        target_id=1, default_common_address=CA,
        devices=[_cihaz()], signals=[_komut_sinyali(f"master.{slug}")],
    )
    assert reg.commands == (), (
        f"{slug} tip 45 ile registry'ye girdi — SCADA'dan tetiklenebilir hale gelir"
    )


def test_her_cihaz_KENDI_CA_si_ile_ayri_komut_noktasi():
    """Ayni IOA farkli CA'larda farkli cihaza bakmali; aksi halde reset
    YANLIS fidere gider."""
    reg = build_point_registry(
        target_id=1, default_common_address=CA,
        devices=[_cihaz("DEV-001", 7), _cihaz("DEV-002", 8)],
        signals=[_komut_sinyali("master.reset_all_fcis")],
    )
    harita = reg.command_by_address()
    assert harita[(7, RESET_IOA)].device_code == "DEV-001"
    assert harita[(8, RESET_IOA)].device_code == "DEV-002"


# ---------------------------------------------------------------------------
# Cerceve cozumleme
# ---------------------------------------------------------------------------

def _sc_asdu(*, ca: int, ioa: int, sco: int) -> bytes:
    return (
        _encode_dui(type_id=TYPE_C_SC_NA_1, num_ix=1, sq=False,
                    cause=COT_ACTIVATION, originator=0, common_address=ca)
        + _encode_ioa(ioa) + bytes((sco,))
    )


def test_komut_cozumlemesi_alanlari_dogru():
    c = parse_single_command(_sc_asdu(ca=CA, ioa=RESET_IOA, sco=0x81))
    assert c.common_address == CA and c.ioa == RESET_IOA
    assert c.select is True and c.on is True


def test_COKLU_nesne_reddediliyor():
    """SQ=1 ya da num_ix>1 kabul edilseydi tek cerceveyle coklu reset
    tetiklenebilirdi; kapsam sessizce genislerdi."""
    asdu = bytearray(_sc_asdu(ca=CA, ioa=RESET_IOA, sco=0x01))
    asdu[1] = 0x02          # num_ix = 2
    assert parse_single_command(bytes(asdu)) is None
    asdu[1] = 0x81          # SQ = 1
    assert parse_single_command(bytes(asdu)) is None


def test_kisa_cerceve_COKERTMIYOR():
    assert parse_single_command(b"\x2d\x01\x06") is None


# ---------------------------------------------------------------------------
# Uctan uca — gercek soket
# ---------------------------------------------------------------------------

def _cerceve_oku(sock) -> bytes | None:
    bas = b""
    while len(bas) < 2:
        p = sock.recv(2 - len(bas))
        if not p:
            return None
        bas += p
    govde = b""
    while len(govde) < bas[1]:
        p = sock.recv(bas[1] - len(govde))
        if not p:
            return None
        govde += p
    return bas + govde


def _apdu(asdu: bytes) -> bytes:
    apci = struct.pack("<HH", 0, 0)
    return bytes([0x68, len(apci) + len(asdu)]) + apci + asdu


class _SahteIstemci:
    """CommandClient yerine gecer; ag olmadan davranisi surer."""

    # Zamanlama gercek istemciyle AYNI alanlardan okunuyor; testler saha
    # periyodunu beklemek zorunda kalmasin.
    result_timeout_sec = 1.5
    result_poll_sec = 0.05

    def __init__(self, *, reddet=False, sonuc="ok"):
        self.reddet = reddet
        self.sonuc = sonuc
        self.cagrilar: list[dict] = []

    def queue(self, *, device_code, command, peer):
        from iec104_outbound.command_client import CommandAccepted, CommandRejected

        self.cagrilar.append({"device_code": device_code, "command": command, "peer": peer})
        if self.reddet:
            raise CommandRejected("not_allowed_for_protocol", "red")
        return CommandAccepted(id=42, status="pending")

    def result(self, command_id):
        return self.sonuc, self.sonuc


async def _senaryo(istemci, sco=0x01, ioa=RESET_IOA, ca=CA, beklenen=1) -> list[bytes]:
    from iec104_outbound.server import IEC104Server

    reg = build_point_registry(
        target_id=1, default_common_address=CA,
        devices=[_cihaz()], signals=[_komut_sinyali("master.reset_all_fcis")],
    )
    server = IEC104Server(
        name="t", host="127.0.0.1", port=0, registry=reg, command_client=istemci,
    )
    await server.start()
    port = server._server.sockets[0].getsockname()[1]  # noqa: SLF001
    cerceveler: list[bytes] = []

    def _master() -> None:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.settimeout(8)
            sock.sendall(STARTDT_ACT)
            _cerceve_oku(sock)                                   # STARTDT_CON
            sock.sendall(_apdu(_sc_asdu(ca=ca, ioa=ioa, sco=sco)))
            # YALNIZCA beklenen kadar cerceve okunur. Her testte 2 okumak,
            # ACT_TERM gelmeyen durumlarda soket zaman asimini bekletiyor ve
            # dosyayi 52 saniyeye cikariyordu.
            for _ in range(beklenen):
                try:
                    c = _cerceve_oku(sock)
                except socket.timeout:
                    break
                if c is None:
                    break
                cerceveler.append(c)

    loop = asyncio.get_running_loop()
    await asyncio.wait_for(loop.run_in_executor(None, _master), timeout=25)
    await server.stop()
    return cerceveler


def _asdu(cerceve: bytes) -> bytes:
    return cerceve[6:]


def _cot(cerceve: bytes) -> int:
    return _asdu(cerceve)[2] & 0x3F


def _negatif(cerceve: bytes) -> bool:
    return bool(_asdu(cerceve)[2] & 0x40)


def test_reset_KABUL_edilip_backende_iletiliyor():
    istemci = _SahteIstemci()
    cerceveler = asyncio.run(_senaryo(istemci))
    assert istemci.cagrilar, "komut backend'e HIC iletilmedi"
    assert istemci.cagrilar[0]["command"] == "reset_all_fcis"
    assert istemci.cagrilar[0]["device_code"] == "DEV-001"
    assert cerceveler, "hicbir yanit gelmedi"
    assert _cot(cerceveler[0]) == COT_ACTIVATION_CON
    assert not _negatif(cerceveler[0]), "kabul edilen komut negatif onay aldi"


def test_onay_komutu_AYNEN_yansitiyor():
    """IOA/SCO degisirse master komutu eslestiremez ve TIMEOUT'a duser."""
    cerceveler = asyncio.run(_senaryo(_SahteIstemci()))
    c = parse_single_command(_asdu(cerceveler[0]))
    assert c is not None, "onay cercevesi C_SC_NA_1 degil"
    assert c.ioa == RESET_IOA and c.sco == 0x01 and c.common_address == CA


def test_tamamlaninca_ACT_TERM_geliyor():
    cerceveler = asyncio.run(_senaryo(_SahteIstemci(sonuc="ok"), beklenen=2))
    assert len(cerceveler) >= 2, "ACT_TERM gonderilmedi — master komutu asili birakir"
    assert _cot(cerceveler[1]) == COT_ACTIVATION_TERM
    assert not _negatif(cerceveler[1])


def test_cihaz_basarisiz_bildirince_NEGATIF_ACT_TERM():
    """Basarisiz reset'i olumlu sonlandirmak 'yesil yalan' olurdu."""
    cerceveler = asyncio.run(_senaryo(_SahteIstemci(sonuc="failed"), beklenen=2))
    assert len(cerceveler) >= 2
    assert _cot(cerceveler[1]) == COT_ACTIVATION_TERM
    assert _negatif(cerceveler[1]), "basarisiz komut olumlu sonlandirildi"


def test_backend_reddedince_NEGATIF_onay():
    cerceveler = asyncio.run(_senaryo(_SahteIstemci(reddet=True)))
    assert cerceveler
    assert _cot(cerceveler[0]) == COT_ACTIVATION_CON
    assert _negatif(cerceveler[0]), "reddedilen komut olumlu onaylandi"


def test_TANINMAYAN_adres_negatif_onay_aliyor():
    istemci = _SahteIstemci()
    cerceveler = asyncio.run(_senaryo(istemci, ioa=1234))
    assert not istemci.cagrilar, "taninmayan adres backend'e iletildi"
    assert cerceveler and _negatif(cerceveler[0])


def test_SELECT_asamasi_komutu_UYGULAMIYOR():
    """SELECT 'sec ve dogrula' adimidir; uygulamak reset'i erken tetiklerdi."""
    istemci = _SahteIstemci()
    cerceveler = asyncio.run(_senaryo(istemci, sco=0x81))
    assert not istemci.cagrilar, "SELECT asamasinda komut UYGULANDI"
    assert cerceveler and _cot(cerceveler[0]) == COT_ACTIVATION_CON
    assert not _negatif(cerceveler[0]), "SELECT reddedildi — master EXECUTE gondermez"


def test_SCS_OFF_reddediliyor():
    istemci = _SahteIstemci()
    cerceveler = asyncio.run(_senaryo(istemci, sco=0x00))
    assert not istemci.cagrilar
    assert cerceveler and _negatif(cerceveler[0])


def test_istemci_YAPILANDIRILMAMISSA_negatif_onay():
    """Sessizce kuyruga alinmis gibi davranmak en kotu sonuc olurdu."""
    cerceveler = asyncio.run(_senaryo(None))
    assert cerceveler and _negatif(cerceveler[0])


# ---------------------------------------------------------------------------
# Istemci kopmasi HATA DEGIL
#
# SAHADA GORULDU: SCADA master'i her kapandiginda gunluge ERROR seviyesinde
# "iec104_client_handler_crashed" + tam traceback basiliyordu. Sebep
# `BrokenPipeError`in yakalanan kopma tiplerinden BIRI OLMAMASIYDI —
# yaziciya (`_drain_outbox`) yazarken kopan soket tam olarak bunu uretir,
# yani her oturum sonunda goruluyordu.
#
# Iki zarari vardi: operator "crashed" gorup gercek bir ariza sandi, ve
# gunlukte GERCEK hatalar bu gurultunun icinde kayboldu.
# ---------------------------------------------------------------------------

def _handler_kaynak() -> str:
    import inspect
    import re

    from iec104_outbound.server import IEC104Server

    kaynak = inspect.getsource(IEC104Server._handle_client)
    kaynak = re.sub(r'""".*?"""', "", kaynak, flags=re.DOTALL)
    return re.sub(r"^\s*#.*$", "", kaynak, flags=re.MULTILINE)


@pytest.mark.parametrize(
    "hata",
    ["BrokenPipeError", "ConnectionResetError", "ConnectionAbortedError",
     "asyncio.IncompleteReadError"],
)
def test_kopma_tipi_HATA_olarak_loglanmiyor(hata: str):
    kod = _handler_kaynak()
    # Sinir, fonksiyonun ILK `except Exception`i DEGIL — iceride daha erken
    # bir tane var (TEST_ACT gonderimi) ve ona gore dilimlemek slice'i ters
    # cevirip testi bos bir metin uzerinde calistiriyordu.
    i_yakala = kod.find("except (")
    i_genel = kod.find("iec104_client_handler_crashed")
    assert i_yakala != -1, "kopma dali yok"
    assert i_genel != -1 and i_genel > i_yakala, "genel hata dali kopma dalindan once"
    kopma_blogu = kod[i_yakala:i_genel]
    assert hata in kopma_blogu, (
        f"{hata} kopma dalinda yakalanmiyor — genel dala duser ve her "
        "oturum sonunda ERROR + traceback basar"
    )


def test_gercek_hatalar_HALA_loglaniyor():
    """Kopma dalini genisletirken genel dali kaldirmak ya da SEVIYESINI
    dusurmek, gercek bir cokusu sessizce yutmak olurdu.

    Ilk yazimda yalnizca mesaj metnini ariyordum; `logger.exception` ->
    `logger.debug` yapan mutasyon KACTI. Seviye de kilitleniyor: `exception`
    hem ERROR seviyesi hem de traceback demek."""
    kod = _handler_kaynak()
    assert "except Exception:" in kod
    assert 'logger.exception("iec104_client_handler_crashed' in kod, (
        "gercek cokus ERROR + traceback ile loglanmiyor"
    )


def test_oturum_temizligi_HER_DURUMDA_calisiyor():
    """Kopma dali `pass` yerine log basiyor; `finally` blogunun atlanmadigini
    dogruluyoruz — atlanirsa yazici gorevi sizar."""
    kod = _handler_kaynak()
    i_finally = kod.find("finally:")
    assert i_finally != -1, "finally blogu yok — kopan oturum gorev sizdirir"
    finally_blok = kod[i_finally:]
    assert "_sessions.discard(session)" in finally_blok
    assert "_drain_task" in finally_blok
