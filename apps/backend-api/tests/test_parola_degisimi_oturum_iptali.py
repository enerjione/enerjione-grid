"""Parola degisimi ONCEDEN verilmis her oturumu dusurur.

YASANAN ACIK
------------
`POST /auth/me/change-password` ve `POST /users/{id}/reset-password` yalnizca
`users.hashed_password` alanini guncelliyordu. `user_sessions` tablosu, jti
iptali ve `get_current_user`'daki `revoked_at` kontrolu ZATEN VARDI — ama
parola yollarinin hicbiri onlara dokunmuyordu.

Sonuc: parolasinin ele gecirildigini fark eden kullanici parolasini
degistirdiginde saldirganin elindeki JWT HIC etkilenmiyordu. Token kendi TTL'i
boyunca -- 8 saat, "beni hatirla" ile 7 GUN -- tam yetkiyle calismaya devam
ediyordu. Ayni sey yoneticinin reset'i icin de gecerliydi; oysa o ucun asil
kullanim sebebi "hesap ele gecirildi / calisan ayrildi"dir. Yani her iki
kurtarma yolu da tam olarak kurtarmasi gereken senaryoda ISE YARAMIYORDU.

Bu dosya sozlesmeyi UCTAN UCA surer: gercek uygulama, gercek login, gercek
JWT, gercek `get_current_user`.

NEDEN TestClient DEGIL HAM ASGI
-------------------------------
`starlette.testclient` httpx gerektiriyor ve bu proje httpx'e bagli degil
(ayni gerekce: `test_license_gate.py`, `test_route_auth_boundary.py`).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.models.enums import UserRole
from app.models.user import User
from app.models.user_session import UserSession
from app.services.auth_service import get_password_hash

import app.models  # noqa: F401  model kayitlari Base.metadata'ya girsin

PREFIX = settings.api_prefix
ME = f"{PREFIX}/auth/me"
ESKI = "EskiParola!12345"
YENI = "YeniParola!67890"


@pytest.fixture()
def ortam(tmp_path):
    """Tek kullanimlik SQLite + gercek FastAPI app.

    `app.db.session` modulundeki `SessionLocal` REBIND edilir; `get_db` ve
    servisler onu cagri aninda modul global'inden okudugu icin bu yeterli.
    Lisans kilidi ve hiz siniri test disi birakilir, teardown'da geri konur.
    """
    from app.core.rate_limit import limiter
    from app.db import session as db_session
    from app.main import app
    from app.services import license_service

    eng = create_engine(
        f"sqlite:///{(tmp_path / 'auth.db').as_posix()}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng, autoflush=False, autocommit=False, future=True)

    eski_engine, eski_session = db_session.engine, db_session.SessionLocal
    eski_kilit, eski_limit = license_service.is_api_locked, limiter.enabled
    db_session.engine, db_session.SessionLocal = eng, Session
    license_service.is_api_locked = lambda: False
    limiter.enabled = False
    try:
        yield _Ortam(app, Session)
    finally:
        db_session.engine, db_session.SessionLocal = eski_engine, eski_session
        license_service.is_api_locked = eski_kilit
        limiter.enabled = eski_limit
        eng.dispose()


class _Ortam:
    def __init__(self, app, Session):
        self.app = app
        self.Session = Session

    # --- ham ASGI ---------------------------------------------------------
    def cagir(self, method, path, *, body=None, token=None, ua="test/1.0"):
        """Tek istek surer -> (status, json|None)."""
        ham = json.dumps(body).encode() if body is not None else b""
        basliklar = [(b"host", b"testserver"), (b"user-agent", ua.encode())]
        if body is not None:
            basliklar.append((b"content-type", b"application/json"))
        if token:
            basliklar.append((b"authorization", f"Bearer {token}".encode()))

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "path": path, "raw_path": path.encode(),
            "root_path": "", "scheme": "http", "query_string": b"",
            "headers": basliklar, "client": ("127.0.0.1", 5555),
            "server": ("testserver", 80), "app": self.app,
        }
        gonderilen: list[dict] = []
        govde_verildi = False

        async def receive():
            nonlocal govde_verildi
            if govde_verildi:
                return {"type": "http.disconnect"}
            govde_verildi = True
            return {"type": "http.request", "body": ham, "more_body": False}

        async def send(msg):
            gonderilen.append(msg)

        asyncio.run(self.app(scope, receive, send))
        durum = next(
            (m["status"] for m in gonderilen if m["type"] == "http.response.start"), None
        )
        self.son_basliklar = [
            (k.decode().lower(), v.decode())
            for m in gonderilen
            if m["type"] == "http.response.start"
            for k, v in m.get("headers", [])
        ]
        icerik = b"".join(
            m.get("body", b"") for m in gonderilen if m["type"] == "http.response.body"
        )
        try:
            return durum, (json.loads(icerik) if icerik else None)
        except ValueError:
            return durum, None

    # --- kisayollar -------------------------------------------------------
    def kullanici(self, ad, parola=ESKI, rol=UserRole.INSTALLER) -> int:
        s = self.Session()
        try:
            u = User(
                username=ad, email=f"{ad}@test.local", full_name=ad.title(),
                hashed_password=get_password_hash(parola), role=rol,
                must_change_password=False,
            )
            s.add(u)
            s.commit()
            return u.id
        finally:
            s.close()

    def giris(self, ad, parola, ua="test/1.0"):
        durum, govde = self.cagir(
            "POST", f"{PREFIX}/auth/login", ua=ua,
            body={"username": ad, "password": parola, "remember_me": False},
        )
        return durum, (govde or {}).get("access_token")

    def me(self, token, ua="test/1.0") -> int:
        return self.cagir("GET", ME, token=token, ua=ua)[0]

    def parola_degistir(self, token, eski=ESKI, yeni=YENI, ua="test/1.0") -> int:
        return self.cagir(
            "POST", f"{PREFIX}/auth/me/change-password", token=token, ua=ua,
            body={"current_password": eski, "new_password": yeni},
        )[0]


# --------------------------------------------------------------------------
# T1 — kullanicinin kendi parola degisimi
# --------------------------------------------------------------------------

def test_kendi_parolasini_degistiren_eski_token_401(ortam):
    """ACIGIN TA KENDISI: degisimden ONCE verilmis token calismayi surduruyordu."""
    ortam.kullanici("t1")
    durum, token = ortam.giris("t1", ESKI)
    assert durum == 200 and token

    assert ortam.me(token) == 200, "on kosul: token degisimden once gecerli"
    assert ortam.parola_degistir(token) == 204

    assert ortam.me(token) == 401, (
        "Parola degisti ama ESKI token hala kabul ediliyor — ele gecirilmis "
        "oturum kapanmiyor."
    )


# --------------------------------------------------------------------------
# T2 — yonetici reset'i
# --------------------------------------------------------------------------

def test_yonetici_reseti_hedefin_oturumunu_dusurur(ortam):
    """Reset ucunun kullanim sebebi zaten 'hesabi geri al'dir."""
    ortam.kullanici("adm")
    kurban_id = ortam.kullanici("kurban", rol=UserRole.OPERATOR)
    _, adm_token = ortam.giris("adm", ESKI, ua="adm/1.0")
    _, kurban_token = ortam.giris("kurban", ESKI, ua="kurban/1.0")

    assert ortam.me(kurban_token, ua="kurban/1.0") == 200

    durum, _ = ortam.cagir(
        "POST", f"{PREFIX}/users/{kurban_id}/reset-password",
        token=adm_token, ua="adm/1.0", body={"new_password": YENI},
    )
    assert durum == 204

    assert ortam.me(kurban_token, ua="kurban/1.0") == 401, (
        "Yonetici parolayi resetledi ama hedefin eski token'i hala gecerli."
    )
    # Yoneticinin kendi oturumu ayakta kalmali; aksi halde reset yapan kisi
    # her seferinde disari atilirdi.
    assert ortam.me(adm_token, ua="adm/1.0") == 200


# --------------------------------------------------------------------------
# T3 / T4 — yeni parola calisir, eski parola calismaz
# --------------------------------------------------------------------------

def test_yeni_parola_ile_giris_calisir_eski_parola_reddedilir(ortam):
    """Iptal 'hesabi kilitleme' olmamali: yeni oturum acilabilmeli."""
    ortam.kullanici("t3")
    _, token = ortam.giris("t3", ESKI)
    assert ortam.parola_degistir(token) == 204

    durum_yeni, yeni_token = ortam.giris("t3", YENI, ua="t3-yeni/1.0")
    assert durum_yeni == 200 and yeni_token
    assert ortam.me(yeni_token, ua="t3-yeni/1.0") == 200, (
        "Parola degisiminden sonra acilan YENI oturum da dusurulmus."
    )

    durum_eski, _ = ortam.giris("t3", ESKI, ua="t3-eski/1.0")
    assert durum_eski == 401


# --------------------------------------------------------------------------
# T5 — iptal kullaniciya ozel
# --------------------------------------------------------------------------

def test_baska_kullanicinin_oturumu_etkilenmez(ortam):
    """`user_id` filtresi dusesse tek parola degisimi TUM sahayi disari atardi."""
    ortam.kullanici("a")
    ortam.kullanici("b")
    _, a_token = ortam.giris("a", ESKI, ua="a/1.0")
    _, b_token = ortam.giris("b", ESKI, ua="b/1.0")

    assert ortam.parola_degistir(a_token, ua="a/1.0") == 204

    assert ortam.me(b_token, ua="b/1.0") == 200, (
        "A'nin parola degisimi B'nin oturumunu da dusurdu."
    )


# --------------------------------------------------------------------------
# T8 — ayni kullanicinin TUM cihazlari
# --------------------------------------------------------------------------

def test_ayni_kullanicinin_tum_oturumlari_duser(ortam):
    """Yalnizca istegi yapan oturumu dusurmek yetmez: saldirgan BASKA cihazda."""
    ortam.kullanici("cok")
    _, token_a = ortam.giris("cok", ESKI, ua="cihazA/1.0")
    _, token_b = ortam.giris("cok", ESKI, ua="cihazB/1.0")
    assert ortam.me(token_a, ua="cihazA/1.0") == 200
    assert ortam.me(token_b, ua="cihazB/1.0") == 200

    # Degisimi B'den yap; A saldirganin cihazi olsun.
    assert ortam.parola_degistir(token_b, ua="cihazB/1.0") == 204

    assert ortam.me(token_a, ua="cihazA/1.0") == 401, (
        "Istegi yapmayan DIGER cihazin oturumu ayakta kaldi."
    )
    assert ortam.me(token_b, ua="cihazB/1.0") == 401

    durum_c, token_c = ortam.giris("cok", YENI, ua="cihazC/1.0")
    assert durum_c == 200
    assert ortam.me(token_c, ua="cihazC/1.0") == 200


# --------------------------------------------------------------------------
# Kalicilik — iptal bellekte degil DB'de
# --------------------------------------------------------------------------

def test_iptal_veritabanina_yazilir(ortam):
    """In-memory blacklist container restart'ta bosalir; DB'deki isaret kalir.

    `E1_API_WORKERS>1` iken blacklist zaten SUREC BASINA ayridir — iptalin
    surecler arasi tek otoritesi `user_sessions.revoked_at`'tir.
    """
    kid = ortam.kullanici("kalici")
    _, token = ortam.giris("kalici", ESKI)
    assert ortam.parola_degistir(token) == 204

    s = ortam.Session()
    try:
        satirlar = s.query(UserSession).filter(UserSession.user_id == kid).all()
        assert satirlar, "login oturum satiri yaratmali"
        assert all(r.revoked_at is not None for r in satirlar), (
            "Iptal yalnizca bellekte yapilmis; DB satiri hala aktif gorunuyor."
        )
    finally:
        s.close()


def test_surec_yeniden_baslasa_da_eski_token_reddedilir(ortam):
    """Iptalin OTORITESI DB olmali — in-memory blacklist degil.

    NEDEN AYRI BIR TEST: iptali hem `revoked_at` hem de surec-ici blacklist
    tasiyor. Tek surecte ikisi de reddettigi icin, DB kontrolu tamamen
    kaldirilsa bile testler yesil kalir ve gercek acik gorunmez.

    Oysa uretimde blacklist YETMEZ:
      * `E1_API_WORKERS>1` iken her uvicorn sureci KENDI dict'ini tutar;
        iptali yapan surec disindaki isciler token'i kabul etmeye devam eder,
      * container restart'ta dict bosalir ve iptal edilmis token DIRILIR.

    Blacklist'i bosaltmak tam olarak bu iki durumu taklit eder.
    """
    from app.services import auth_service

    ortam.kullanici("restart")
    _, token = ortam.giris("restart", ESKI)
    assert ortam.parola_degistir(token) == 204

    # "Yeni surec": blacklist bos, geriye yalnizca DB kaliyor.
    with auth_service._REVOKED_LOCK:
        auth_service._REVOKED_JTI.clear()

    assert ortam.me(token) == 401, (
        "Iptal yalnizca surec-ici blacklist ile uygulaniyor; restart veya "
        "ikinci uvicorn iscisi eski token'i kabul eder."
    )


def test_canli_websocket_de_iptali_gorur(ortam, monkeypatch):
    """Uzun omurlu WS kanali parola degisiminden SONRA akmaya devam etmemeli.

    Bu urunde refresh-token YOK; token ureten tek uc `/auth/login`. Ama
    "iptal ettim sandigim erisim aslinda acik" riskini tasiyan ikinci bir
    uzun-omurlu yol var: canli deger WS'i. `ws_live` hem el sikismada hem de
    baglanti boyunca periyodik olarak `_is_session_revoked` cagirir; bu test
    o kapinin parola degisimini GORDUGUNU dogrular (gormezse soket, token
    coktan iptal edilmisken saatlerce veri akitmaya devam eder).
    """
    from app.api import ws_live
    from app.api.ws_live import _is_session_revoked
    from app.services import auth_service

    # ws_live `SessionLocal`i IMPORT aninda kendi modul global'ine aliyor;
    # fixture'in `app.db.session` uzerindeki rebind'i oraya ulasmaz.
    monkeypatch.setattr(ws_live, "SessionLocal", ortam.Session)

    ortam.kullanici("wsuser")
    _, token = ortam.giris("wsuser", ESKI)
    jti = auth_service.jwt.decode(
        token, settings.secret_key, algorithms=[settings.algorithm]
    )["jti"]
    assert _is_session_revoked(jti) is False

    assert ortam.parola_degistir(token) == 204

    # Surec-ici blacklist'i bosalt: WS kapisinin DB'yi okudugunu dogrula.
    with auth_service._REVOKED_LOCK:
        auth_service._REVOKED_JTI.clear()

    assert _is_session_revoked(jti) is True, (
        "Parola degisti ama canli WS kanali oturumu hala gecerli sayiyor."
    )


# --------------------------------------------------------------------------
# Atomiklik — parola ile iptal AYNI commit'te
# --------------------------------------------------------------------------

def test_iptal_patlarsa_parola_da_yazilmaz(ortam, monkeypatch):
    """'Parola degisti ama oturum iptali hic yapilmadi' durumu OLUSAMAMALI.

    Iptal cagiranin transaction'ina katiliyor ve commit'ten ONCE calisiyor;
    patlarsa istek 500 ile duser ve yeni hash de yazilmaz. Kullanici eski
    parolasiyla devam eder — yani hicbir zaman "degisti" denip acik kalmaz.
    """
    from app.api import auth as auth_api

    ortam.kullanici("atomik")
    _, token = ortam.giris("atomik", ESKI)

    def _patla(*_a, **_k):
        raise RuntimeError("iptal basarisiz")

    monkeypatch.setattr(auth_api, "revoke_user_sessions", _patla)
    with pytest.raises(RuntimeError):
        ortam.parola_degistir(token)

    monkeypatch.undo()
    # Yeni parola YAZILMAMIS olmali; eski parola hala gecerli.
    durum_yeni, _ = ortam.giris("atomik", YENI, ua="atomik-yeni/1.0")
    assert durum_yeni == 401, "Iptal patlamasina ragmen yeni parola yazilmis."
    durum_eski, _ = ortam.giris("atomik", ESKI, ua="atomik-eski/1.0")
    assert durum_eski == 200


# ==========================================================================
# F1 — OTURUM KAYDI BEST-EFFORT DEGIL
# ==========================================================================

def test_t9_oturum_satiri_yazilamazsa_token_verilmez(ortam):
    """BASARILI TOKEN == KALICI OLARAK YAZILMIS UserSession.

    Eskiden insert `except Exception: pass` ile yutuluyordu ve token yine de
    donuyordu. Satiri olmayan token'i HICBIR SEY iptal edemez: parola degisimi
    onu bulamaz, installer listede goremez, surec restart'inda bellek
    blacklist'i de bosalir. Yani DB'nin bozuk oldugu anda -- iptal sansinin en
    dusuk oldugu anda -- 7 gune kadar yasayan iptal edilemez bir token
    uretiliyordu.

    Ariza `user_sessions` tablosunu dusurerek taklit ediliyor: gercek bir
    "tablo yok / erisilemiyor" hatasi, sahte bir mock degil.
    """
    ortam.kullanici("t9")
    with ortam.Session() as s:
        s.execute(text("DROP TABLE user_sessions"))
        s.commit()

    durum, token = ortam.giris("t9", ESKI)

    assert durum == 503, f"oturum yazilamadi ama login {durum} dondu"
    assert token is None, "oturum kaydi yokken token govdede dondu"
    cerezler = [v for k, v in ortam.son_basliklar if k == "set-cookie"]
    assert not cerezler, f"oturum kaydi yokken Set-Cookie gonderildi: {cerezler}"


# ==========================================================================
# F2 — DOGRULAMA FAIL-CLOSED
# ==========================================================================

def test_t10_oturum_satiri_yoksa_401(ortam):
    """Satirsiz token gecerli sayilamaz — iptal edilmesi imkansiz bir token'dir.

    Bellek blacklist'ine hic dokunulmuyor: reddin TEK sebebi satirin
    yoklugu olmali.
    """
    ortam.kullanici("t10")
    _, token = ortam.giris("t10", ESKI)
    assert ortam.me(token) == 200

    with ortam.Session() as s:
        s.execute(text("DELETE FROM user_sessions"))
        s.commit()

    assert ortam.me(token) == 401, (
        "user_sessions satiri silinmis token hala kabul ediliyor."
    )


def test_t11_oturum_sorgusu_patlarsa_istek_gecmez(ortam):
    """Oturum durumu BILINMIYORSA istek gecmemeli.

    Eskiden lookup `except Exception: pass` ile sariliydi: `user_sessions`
    sorgusu patladigi anda (havuz tukendi, restore suruyor, tablo kilitli)
    iptal kontrolu sessizce atlaniyor ve istek 200 ile devam ediyordu. Iptal
    edilmis token tam da bu anlarda gecerli hale geliyordu.

    200 KESINLIKLE olmamali; sozlesme 503.
    """
    ortam.kullanici("t11")
    _, token = ortam.giris("t11", ESKI)
    assert ortam.me(token) == 200

    with ortam.Session() as s:
        s.execute(text("DROP TABLE user_sessions"))
        s.commit()

    durum = ortam.me(token)
    assert durum != 200, "oturum sorgusu patladi ama istek 200 ile gecti (fail-open)"
    assert durum == 503, f"fail-closed sozlesmesi 503 bekliyor, {durum} geldi"


# ==========================================================================
# F3 — BEKLEYEN DAVET/RESET BILETI
# ==========================================================================

def _bilet_ver(ortam, username: str) -> str:
    """Kullaniciya bekleyen bir davet/reset bileti yaz; ham token'i don."""
    from app.services.invitation_service import generate_invitation_token

    s = ortam.Session()
    try:
        u = s.query(User).filter(User.username == username).one()
        ham = generate_invitation_token(u)
        s.commit()
        return ham
    finally:
        s.close()


def _bilet_kullan(ortam, ham_token: str, yeni_parola: str):
    return ortam.cagir(
        "POST", f"{PREFIX}/auth/setup-password",
        body={"token": ham_token, "new_password": yeni_parola},
    )


def test_t12_admin_reset_eski_bileti_gecersiz_kilar(ortam):
    """Reset, hesabi elinde tutan icin engel degil gecikme olmamali.

    `password_reset_token_hash` yalnizca setup-password yolunda
    temizleniyordu. Bekleyen 7 gun TTL'li davet linki, yonetici parolayi
    resetledikten SONRA da parolayi yeniden belirlemeye yetiyordu.
    """
    ortam.kullanici("adm12")
    kurban_id = ortam.kullanici("kurban12", rol=UserRole.OPERATOR)
    ham = _bilet_ver(ortam, "kurban12")
    _, adm_token = ortam.giris("adm12", ESKI, ua="adm12/1")

    durum, _ = ortam.cagir(
        "POST", f"{PREFIX}/users/{kurban_id}/reset-password",
        token=adm_token, ua="adm12/1", body={"new_password": YENI},
    )
    assert durum == 204

    durum_bilet, govde = _bilet_kullan(ortam, ham, "SaldirganParola!1")
    assert durum_bilet == 400, (
        f"admin reset'ten sonra eski davet bileti hala calisiyor ({durum_bilet}: {govde})"
    )
    # Bilet gecersiz oldugu icin parola yoneticinin belirledigi degerde kalmali.
    assert ortam.giris("kurban12", YENI, ua="kurban12/2")[0] == 200
    assert ortam.giris("kurban12", "SaldirganParola!1", ua="kurban12/3")[0] == 401


def test_t13_kendi_parola_degisimi_eski_bileti_gecersiz_kilar(ortam):
    """Kullanici parolasini degistirdiginde bekleyen bilet de dusmeli."""
    ortam.kullanici("t13")
    ham = _bilet_ver(ortam, "t13")
    _, token = ortam.giris("t13", ESKI)

    assert ortam.parola_degistir(token) == 204

    durum_bilet, govde = _bilet_kullan(ortam, ham, "SaldirganParola!1")
    assert durum_bilet == 400, (
        f"parola degisiminden sonra eski bilet hala calisiyor ({durum_bilet}: {govde})"
    )
    assert ortam.giris("t13", YENI, ua="t13/2")[0] == 200


def test_t14_setup_bileti_tek_kullanimlik(ortam):
    """Ayni davet linki ikinci kez parola belirleyememeli."""
    s = ortam.Session()
    try:
        u = User(
            username="davetli", email="davetli@test.local", full_name="Davetli",
            hashed_password=None, role=UserRole.OPERATOR, must_change_password=False,
        )
        s.add(u)
        s.commit()
    finally:
        s.close()
    ham = _bilet_ver(ortam, "davetli")

    ilk, _ = _bilet_kullan(ortam, ham, "IlkParola!123")
    assert ilk == 204

    ikinci, govde = _bilet_kullan(ortam, ham, "IkinciParola!123")
    assert ikinci == 400, f"ayni bilet ikinci kez kabul edildi ({ikinci}: {govde})"

    # Parola ILK kullanimda belirlenen degerde kalmali.
    assert ortam.giris("davetli", "IlkParola!123", ua="davetli/1")[0] == 200
    assert ortam.giris("davetli", "IkinciParola!123", ua="davetli/2")[0] == 401
