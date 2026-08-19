from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from pydantic import BaseModel

from app.core.config import settings
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models.user import User
from app.models.user_fcm_token import UserFcmToken
from app.schemas.auth import LoginRequest, SetupPasswordRequest, TokenResponse
from app.schemas.user import (
    MIN_PASSWORD_LENGTH,
    LanguageUpdateRequest,
    SelfPasswordChangeRequest,
    SelfProfileUpdateRequest,
    UserRead,
)


# Auth cookie ismi — frontend Authorization header yerine bu cookie ile
# gelirse `get_current_user` cookie'den okur. HttpOnly + SameSite=Lax: XSS
# sonrasi token cikartilamaz + CSRF zorlasir. `Secure` ISTEGIN SEMASINA gore
# konur (bkz. _istek_https_mi); "production" oldugu icin degil.
_AUTH_COOKIE_NAME = "e1_session"


def _istek_https_mi(request: Request) -> bool:
    """Istek GERCEKTEN HTTPS uzerinden mi geldi?

    TLS cogu kurulumda DISARIDA (host nginx / Caddy) sonlandirilir; istek
    backend'e duz HTTP olarak ulasir ve tek kanit `X-Forwarded-Proto`
    basligidir. Baslik yoksa istegin kendi semasina bakilir.

    Basligin ILK degeri alinir: zincirde birden fazla proxy varsa
    "https, http" gibi virgullu bir liste gelebilir ve istemciye en yakin
    olan bastakidir.

    GUVEN SINIRI: backend host'a port acmiyor; yalnizca compose agindan,
    frontend nginx'i uzerinden erisilebilir. Yani bu basligi disaridan bir
    istemci uyduramaz — ureten taraf bizim nginx'imizdir.
    """
    ham = (request.headers.get("x-forwarded-proto") or "").split(",")[0]
    ham = ham.strip().lower()
    if ham:
        return ham == "https"
    return request.url.scheme == "https"

# Account lockout esikleri. SlowAPI IP-bazli 5/dk limitin UZERINE eklenen
# hesap-bazli savunma — saldirgan farkli IP'lerden veya X-Forwarded-For
# spoof ile gelse bile ayni username icin 10 hatadan sonra hesap kilitlenir.
_MAX_FAILED_LOGIN = 10
_LOCKOUT_MINUTES = 15


class FcmTokenRegisterRequest(BaseModel):
    token: str
    platform: str | None = None
    device_label: str | None = None


class FcmTokenDeleteRequest(BaseModel):
    token: str

# Frontend i18n ile uyumlu desteklenen diller. Yeni dil eklerken hem
# bu listeye hem de frontend resources/<code>.json dosyasina ekle.
SUPPORTED_LANGUAGES = {"tr", "en"}
from app.api.deps import get_current_user
from app.services.auth_service import (
    clear_password_reset_token,
    create_access_token,
    get_password_hash,
    revoke_user_sessions,
    verify_password,
)
from app.services.event_service import record_event

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: Session = Depends(get_db),
):
    """Login — brute-force koruma + HttpOnly cookie session.

    5/dakika online brute force'u zorlastirir (1M parola = 138 gun).
    6. istek 429 doner.

    Cookie auth (yeni, onerilen): basarili login'de `Set-Cookie: e1_session`
    HttpOnly + Secure (prod) + SameSite=Strict + Max-Age=access_token_minutes
    ile gonderilir. XSS sonrasi JS `document.cookie` okuyamaz (HttpOnly),
    token'i exfiltrate edemez. CSRF SameSite=Strict ile zorlasir.

    Geriye uyumluluk: response body'sinde `access_token` da donulur — eski
    frontend localStorage akisi calismaya devam eder. Bir sonraki major
    release'te body'den access_token cikarilacak.

    NOT: Reverse proxy arkasinda `X-Forwarded-For` spoof'a karsi koruma
    icin uvicorn `--forwarded-allow-ips=*` ile baslatilmali (Dockerfile CMD).

    Audit kaydi: yalnizca BASARILI login'de yazilir (DoS amplification onlemi).
    """
    _ = request  # slowapi key_func icin gerekli; kullanilmiyor
    stmt = select(User).where(User.username == payload.username)
    user = db.scalar(stmt)

    # Kilitli hesap kontrolu — kullanici varsa ve locked_until > now ise
    # parola dogrulamayi atla, dogrudan 423 don. Boylece kilit suresi
    # icinde dogru parola bile login etmez (kasten — saldirgan dogru
    # parolayi bulmus olsa bile kilit suresi bitene kadar beklemeli).
    now = datetime.now(timezone.utc)
    if user is not None and user.locked_until is not None and user.locked_until > now:
        remaining = int((user.locked_until - now).total_seconds())
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Account temporarily locked. Try again in {remaining // 60 + 1} minute(s).",
        )

    # Davet edilmis ama henuz sifre belirlememis kullanici (hashed_password=NULL)
    # icin verify_password False doner — bu davranis istenen: token sahibi
    # olmayan kimse giremez. Kullaniciya UI'da "henuz aktivasyon yapilmadi"
    # mesaji gosterilmek istenirse ayri bir status code donulebilir; simdilik
    # standart 401 (enumeration koruma) yeterli.
    if not user or not user.hashed_password or not verify_password(payload.password, user.hashed_password):
        # Basarisiz deneme — user varsa sayaci artir; esik asilinca kilitle.
        # User yoksa kayit tutulmaz (enumeration korumasi: sayac sahte
        # username'lerle baska bir hesabi kilitlemek icin kullanilamaz).
        if user is not None:
            user.failed_login_count = (user.failed_login_count or 0) + 1
            locked = False
            if user.failed_login_count >= _MAX_FAILED_LOGIN:
                user.locked_until = now + timedelta(minutes=_LOCKOUT_MINUTES)
                locked = True
            # Audit kaydi: basarisiz deneme + kilit. record_event commit yapmaz;
            # asagidaki db.commit() topluca yazar.
            record_event(
                db,
                category="auth",
                event_type="login_failed_locked" if locked else "login_failed",
                severity="warning" if locked else "info",
                actor_username=user.username,
                message=(
                    f"{user.username} hesabi {_LOCKOUT_MINUTES} dk kilitlendi "
                    f"({_MAX_FAILED_LOGIN} basarisiz deneme)"
                    if locked
                    else f"{user.username} basarisiz login denemesi "
                    f"({user.failed_login_count}/{_MAX_FAILED_LOGIN})"
                ),
                i18n_key="login_failed_locked" if locked else "login_failed",
                i18n_params={
                    "user": user.username,
                    "count": user.failed_login_count,
                    "max": _MAX_FAILED_LOGIN,
                    "minutes": _LOCKOUT_MINUTES,
                },
            )
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    # Basarili login — sayaci ve kilit alanini temizle. Bir onceki basarisiz
    # denemeleri sifirla, boylece "9 hatadan sonra 1 dogru" senaryosunda
    # bir sonraki yanlis 1. denemeden tekrar sayilir.
    if user.failed_login_count or user.locked_until:
        user.failed_login_count = 0
        user.locked_until = None

    access_token, ttl_sec, jti = create_access_token(user.username, remember_me=payload.remember_me)

    # Aktif oturum kaydi — installer 'Aktif Oturumlar' sayfasinda gorur
    # ve istedigini revoke edebilir.
    #
    # BU BLOK BEST-EFFORT DEGILDIR (eskiden oyleydi; bkz. asagidaki 503 yolu).
    # Kural: BASARILI TOKEN == KALICI OLARAK YAZILMIS UserSession.
    #
    # YASANAN ACIK: insert `except Exception: pass` ile yutuluyordu ve token
    # yine de kullaniciya donuyordu. Satiri olmayan token'i HICBIR SEY iptal
    # EDEMEZ: `revoke_user_sessions` onu bulamaz (parola degisimi etkisiz
    # kalir), installer 'Aktif Oturumlar'da goremez, surec yeniden basladiginda
    # bellek blacklist'i de bosalir. Yani tam olarak DB'nin sikintili oldugu
    # anda -- iptal etme ihtimalinin en dusuk oldugu anda -- iptal edilemez,
    # 7 gune kadar yasayan bir token uretiliyordu.
    from app.core.client_ip import client_ip_from_request
    from app.models.user_session import UserSession

    try:
        client_ip = client_ip_from_request(request)
        ua = (request.headers.get("user-agent") or "")[:255] or None
        now = datetime.now(timezone.utc)

        # Ayni tarayicidan onceki oturumu kapat. Frontend TEK token tutuyor
        # (cookie + localStorage) ve login cevabi onu ezer; dolayisiyla ayni
        # (kullanici, IP, user-agent) uclusune ait eski jti artik hicbir
        # istemcide durmuyor — sadece listede "5 kere aktif" gorunuyor.
        # Bu satirlari superseded olarak isaretliyoruz.
        superseded = list(
            db.execute(
                select(UserSession).where(
                    UserSession.user_id == user.id,
                    UserSession.revoked_at.is_(None),
                    UserSession.ip_address == client_ip,
                    UserSession.user_agent == ua,
                )
            ).scalars()
        )
        for old in superseded:
            old.revoked_at = now
        if superseded:
            import logging
            logging.getLogger(__name__).info(
                "user_session_superseded username=%s count=%d",
                user.username, len(superseded),
            )

        db.add(UserSession(
            jti=jti,
            user_id=user.id,
            ip_address=client_ip,
            user_agent=ua,
            # ttl_sec create_access_token'dan geliyor (remember_me'ye gore
            # 8 saat / 7 gun). Token exp'i ile ayni ani yaziyoruz ki liste
            # sorgusu gecmis oturumlari filtreleyebilsin.
            expires_at=now + timedelta(seconds=ttl_sec),
        ))
        # Audit kaydi da AYNI transaction'da: oturum satiri ile giris olayi
        # birlikte yazilir ya da hic yazilmaz.
        record_event(
            db,
            category="auth",
            event_type="user_login",
            severity="info",
            actor_username=user.username,
            message=f"{user.username} signed in",
            i18n_key="user_login",
            i18n_params={"user": user.username},
        )
        # Cerez/token URETILMEDEN once kalicilik garanti altina alinir.
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        # Ham DB hatasi / SQL parametreleri LOGLANMAZ (jti, IP, token
        # parcalari oraya sizabilir); yalnizca kullanici adi + hata SINIFI.
        import logging
        logging.getLogger(__name__).error(
            "user_session_persist_failed username=%s error=%s",
            user.username, type(exc).__name__,
        )
        # Kimlik dogrulamasi GECTI ama oturum kaydi yazilamadi: token
        # verilmez. 503 = "sunucu su an giris veremiyor"; 401 yaniltici
        # olurdu (parola dogruydu) ve istemciyi parolayi degistirmeye iterdi.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Oturum kaydi olusturulamadi, lutfen tekrar deneyin.",
        ) from exc

    # HttpOnly cookie — XSS sonrasi token exfiltrate olmaz. Cookie max_age
    # token'in gercek TTL'i ile ayni (remember_me=true ise 7 gun, aksi
    # halde 8 saat).
    # `Secure` bayragi ORTAMA degil ISTEGIN SEMASINA baglanir.
    #
    # YASANAN ARIZA: burada `app_env == production` kullaniliyordu. Saha
    # cihazi APP_ENV=production ile calisiyor ama TLS'i YOK — arayuze
    # http://enerjione.local ile eriliyor. `Secure` cerezi tarayici duz
    # HTTP'de GONDERMEZ. Sonuc:
    #   * normal API cagrilari kurtuluyordu (frontend ayrica Bearer basligi
    #     yolluyor), bu yuzden ariza uzun sure fark edilmedi;
    #   * ama harita karolari <img> ile isteniyor ve <img> baslik GONDEREMEZ,
    #     yalnizca cerez gonderir. Cerez gitmeyince her karo 401 aliyordu ve
    #     sahada harita HIC acilmiyordu — indirilmis cevrimdisi karo onbellegi
    #     dahil, cunku ona ulasan yol da ayni uctan geciyor.
    #   * localhost'ta gorunmuyordu: tarayicilar localhost'u guvenli sayip
    #     `Secure` cerezi kabul eder. `enerjione.local` icin etmez.
    #
    # Semayi X-Forwarded-Proto'dan okuyoruz: TLS disarida (host nginx/Caddy)
    # sonlandirildiginda istek backend'e duz HTTP gelir ama bu baslik "https"
    # tasir. Boylece HTTPS kurulumda `Secure` KORUNUR, TLS'siz LAN cihazinda
    # cerez calisir. Basligi frontend nginx'i uretiyor; ust katmanin degerini
    # ezmemesi icin oradaki `$e1_forwarded_proto` map'i eklendi.
    https_uzerinden = _istek_https_mi(request)
    # samesite: "strict" cok kati — interval/polling/iframe gibi
    # subresource fetch'lerinde tarayici bazen cookie'yi gondermiyor
    # (Chrome/Firefox guvenlik politikalari). "lax" cookie standart
    # subresource istekleri ve SPA navigasyonlari icin daha guvenilir;
    # CSRF korumasi token tabanli mantikla (jti revocation) ayrica saglanir.
    response.set_cookie(
        key=_AUTH_COOKIE_NAME,
        value=access_token,
        max_age=ttl_sec,
        httponly=True,
        secure=https_uzerinden,
        samesite="lax",
        path="/",
    )

    return TokenResponse(
        access_token=access_token,
        role=user.role,
        username=user.username,
        must_change_password=bool(user.must_change_password),
    )


@router.get("/me", response_model=UserRead)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/setup-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
def setup_password(
    request: Request,
    payload: SetupPasswordRequest,
    db: Session = Depends(get_db),
):
    """Davet edilmis kullanici ilk sifresini token ile belirler.

    Admin yeni user yarattiginda token uretilir + email/link gonderilir.
    Kullanici setup-password sayfasinda token + yeni sifre POST eder; backend
    SHA-256 hash'i ile eslestirip sifreyi set eder.

    Tek kullanim: basari sonrasi token_hash NULL'a alinir; ayni link tekrar
    kullanilamaz. 7 gunluk TTL (token_expires_at).

    Auth gerekmez (token zaten secret) ama rate-limit 5/dk brute-force korumasi.
    """
    _ = request
    import hashlib

    token = (payload.token or "").strip()
    new_pwd = (payload.new_password or "")
    if not token or len(token) < 16:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")
    if len(new_pwd) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Sifre en az {MIN_PASSWORD_LENGTH} karakter olmali.",
        )

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    user = db.scalar(
        select(User).where(User.password_reset_token_hash == token_hash)
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token gecersiz veya kullanilmis.",
        )
    now = datetime.now(timezone.utc)
    son_gecerlilik = user.password_reset_token_expires_at
    if son_gecerlilik is not None and son_gecerlilik.tzinfo is None:
        # Kolon `DateTime(timezone=True)`; Postgres aware doner. SQLite gibi
        # tz tasimayan backend'lerde naive gelir ve karsilastirma TypeError
        # ile 500 uretirdi. Yazarken UTC yazildigi icin UTC sayiyoruz
        # (`consume_ws_ticket` ayni deseni kullaniyor).
        son_gecerlilik = son_gecerlilik.replace(tzinfo=timezone.utc)
    if son_gecerlilik is None or son_gecerlilik < now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token suresi dolmus. Admin'den yeni davet linki isteyin.",
        )

    user.hashed_password = get_password_hash(new_pwd)
    # TEK KULLANIM: bilet burada tuketilir. Ayni link ikinci kez calismaz
    # (yukaridaki hash aramasi artik eslesmez).
    clear_password_reset_token(user)
    user.must_change_password = False
    user.failed_login_count = 0
    user.locked_until = None
    # Bu da bir parola BELIRLEME yolu: davet akisinda normalde acik oturum
    # olmaz (parolasiz hesap giris yapamaz), ama admin reset'i token'i
    # temizlemedigi icin "parolasi olan hesap + hala gecerli davet linki"
    # bilesimi mumkun. Ayni kurali burada da uygula.
    revoke_user_sessions(db, user.id, actor_user_id=user.id)

    record_event(
        db,
        category="auth",
        event_type="password_setup",
        severity="info",
        actor_username=user.username,
        message=f"{user.username} davet token'i ile sifresini belirledi",
        i18n_key="password_setup",
        i18n_params={"user": user.username},
    )
    db.commit()
    return None


class WsTicketResponse(BaseModel):
    """WS handshake icin kisa-omurlu tek-kullanimlik bilet.

    Frontend HTTP ile bilet ister (auth header/cookie), sonra WS'i
    `?ticket=<TICKET>` ile acar. Eski yapida JWT URL query'sinde geliyordu
    → nginx access log + browser history + Referer'a sizar. Ticket 30sn
    TTL ve tek kullanim (consume sonrasi revoke).
    """

    ticket: str
    expires_in_sec: int


@router.post("/ws-ticket", response_model=WsTicketResponse)
@limiter.limit("60/minute")
def create_ws_ticket(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """WebSocket handshake icin tek-kullanimlik 30sn TTL bilet uretir.

    Bilet `username` + `jti` ile in-memory cache'te tutulur; WS endpoint'i
    bileti consume edip revoke eder. Bu sayede:
      * JWT URL'de gorunmez (Authorization header / cookie auth-suz konum)
      * Ticket WS dosyalanmadan once revoke olur (replay yok)
      * `jti` tasindigi icin WS baglanti boyunca oturum iptalini gorebilir
      * Multi-replica deploy'da Redis'e tasinmasi gerek (TODO).

    Rate-limit 60/dakika — normal client'lar reconnect sirasinda 1-2 bilet
    alir; abusive client (in-memory cache flood) engellenir.
    """
    from app.services.auth_service import issue_ws_ticket

    # `get_current_user` token'i cozerken jti'yi request.state'e koyuyor.
    jti = getattr(request.state, "auth_jti", None)
    ticket, ttl = issue_ws_ticket(current_user.username, jti)
    return WsTicketResponse(ticket=ticket, expires_in_sec=ttl)


@router.patch("/me", response_model=UserRead)
def update_me(
    payload: SelfProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.full_name = payload.full_name
    current_user.email = payload.email
    # Telefon ve fotograf: `None` "dokunma" degil "TEMIZLE" demektir. Arayuz
    # her kaydetmede iki alani da mevcut degerleriyle birlikte gonderir;
    # boylece "numarayi sil" gibi bir istek ayri bir uc nokta gerektirmez.
    current_user.phone_number = payload.phone_number
    current_user.avatar_url = payload.avatar_url
    db.commit()
    db.refresh(current_user)
    return current_user


@router.put("/me/language", response_model=UserRead)
def update_my_language(
    payload: LanguageUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    code = (payload.language or "").strip().lower()
    if code not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Desteklenmeyen dil: {payload.language}",
        )
    current_user.language = code
    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/me/change-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
def change_my_password(
    request: Request,
    response: Response,
    payload: SelfPasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ = request  # slowapi key_func icin gerekli
    # 10/dk: kullanici kendi parola degistirme isteklerini yapabilir ama
    # ele gecirilmis oturumdan brute-force "current_password" denemesi
    # zorlasir (1M parola = 69 gun).
    if not current_user.hashed_password or not verify_password(
        payload.current_password, current_user.hashed_password
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is wrong")
    # Yeni sifre eski ile ayni ise reddet (force-change senaryosunda
    # default-pwd'yi tekrar girip flag'i kapatma fail-open'i onler).
    if verify_password(payload.new_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yeni sifre eskisiyle ayni olamaz.",
        )
    # Force-change flag'ini sifirla — kullanici basariyla sifresini degistirdi.
    current_user.must_change_password = False

    current_user.hashed_password = get_password_hash(payload.new_password)

    # PAROLA DEGISTI -> ONCEDEN VERILMIS HER OTURUM DUSER.
    #
    # YASANAN ACIK: parola degisimi yalnizca hash'i guncelliyordu. Parolasinin
    # ele gecirildigini fark edip degistiren kullanici, saldirganin elindeki
    # JWT'yi HIC etkilemiyordu: o token kendi TTL'i (8 saat, "beni hatirla"
    # ile 7 GUN) boyunca tam yetkiyle calismaya devam ediyordu. Yani parola
    # degistirmek ele gecirilmis bir oturumu kapatmiyordu.
    #
    # Kendi oturumu da dusurulur (baska cihazda acik olan da). Arayuz bir
    # sonraki cagrida 401 alip giris ekranina duser; kullanici YENI parolasiyla
    # girer. "Sadece digerlerini dusur" secenegi bilerek yok: parolasini
    # degistiren kullanicinin niyeti "her yerde kes"tir.
    #
    # Iptal AYNI transaction'da: asagidaki tek `db.commit()` hem hash'i hem
    # `revoked_at` satirlarini yazar. Ikisi birlikte olur ya da hic olmaz.
    dusen = revoke_user_sessions(db, current_user.id, actor_user_id=current_user.id)

    # Bekleyen davet/reset bileti de duser: parola degistikten sonra eski
    # link ile hesabi geri almak mumkun olmamali.
    bilet_dustu = clear_password_reset_token(current_user)

    record_event(
        db,
        category="auth",
        event_type="password_changed",
        severity="info",
        actor_username=current_user.username,
        message=f"{current_user.username} changed password",
        # Yalnizca sayi/bayrak — token/hash/parola DEGIL.
        metadata={"revoked_sessions": dusen, "reset_token_cleared": bilet_dustu},
        i18n_key="password_changed",
        i18n_params={"user": current_user.username},
    )
    db.commit()

    # Artik gecersiz olan oturum cerezini de temizle; aksi halde tarayici
    # olu bir cookie tasimaya devam eder ve her istek 401 log'u uretir.
    response.delete_cookie(key=_AUTH_COOKIE_NAME, path="/")
    return None


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Logout — JWT `jti` claim'ini revocation blacklist'ine ekle.

    Header'daki JWT decode edilir, jti + exp okunur ve blacklist'e konulur.
    Bu token'la yapilacak sonraki istekler 401 doner. Cati 0.x'te in-memory
    blacklist; multi-replica deploy icin Redis backend gerek (TODO).
    """
    # JWT'yi HEM Authorization header'dan HEM cookie'den dene — cookie-only
    # frontend kullanicilari icin cookie'deki jti'yi de revoke et. Yoksa
    # cookie silinse bile token TTL bitene kadar Bearer ile yeniden
    # kullanilabilirdi. Iki kaynaktan da decode ederiz; ayni jti olsa bile
    # revoke_jti idempotent (set'e ekler).
    try:
        from jose import jwt as _jwt

        from app.core.config import settings as _settings
        from app.services.auth_service import revoke_jti

        tokens_to_revoke: list[str] = []
        auth_header = request.headers.get("authorization") or ""
        if auth_header.lower().startswith("bearer "):
            tokens_to_revoke.append(auth_header[7:].strip())
        cookie_token = request.cookies.get(_AUTH_COOKIE_NAME)
        if cookie_token:
            tokens_to_revoke.append(cookie_token.strip())

        from app.models.user_session import UserSession as _UserSession
        from datetime import datetime as _datetime, timezone as _timezone

        for token in tokens_to_revoke:
            if not token:
                continue
            try:
                payload = _jwt.decode(
                    token, _settings.secret_key, algorithms=[_settings.algorithm]
                )
                jti = payload.get("jti")
                exp = payload.get("exp")
                if jti and exp:
                    revoke_jti(str(jti), float(exp))
                    # DB'de session.revoked_at isaretle
                    sess = db.get(_UserSession, str(jti))
                    if sess is not None and sess.revoked_at is None:
                        sess.revoked_at = _datetime.now(_timezone.utc)
                        db.commit()
            except Exception:  # noqa: BLE001
                # Tek bir token decode hatasi diger token'lari engellemesin
                pass
    except Exception:  # noqa: BLE001 — logout audit'i bozulmasin
        import logging as _logging

        _logging.getLogger(__name__).debug("logout_jti_revoke_failed", exc_info=True)

    # HttpOnly cookie'yi temizle — Set-Cookie ile expired tarih.
    # path="/" mutlaka login'deki ile ayni olmali yoksa tarayici silmez.
    response.delete_cookie(key=_AUTH_COOKIE_NAME, path="/")

    record_event(
        db,
        category="auth",
        event_type="user_logout",
        severity="info",
        actor_username=current_user.username,
        message=f"{current_user.username} signed out",
        i18n_key="user_logout",
        i18n_params={"user": current_user.username},
    )
    db.commit()
    return None


# ---------------------------------------------------------------------------
# FCM TOKEN MANAGEMENT (mobile push)
# ---------------------------------------------------------------------------


@router.post("/me/fcm-token", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
def register_fcm_token(
    request: Request,
    payload: FcmTokenRegisterRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mobil cihaz token'ini kullaniciya kaydeder (idempotent).

    Ayni token zaten varsa user'a (yeni veya degisen sahibe) atanir ve
    last_seen guncellenir.

    Rate-limit 20/dk — normal mobile reconnect 1-2 cagri yapar; ele
    gecirilmis user-account ile token cycling DB row spam'i engellenir.
    """
    _ = request  # slowapi key_func
    token = (payload.token or "").strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="token is empty")
    existing = db.scalar(select(UserFcmToken).where(UserFcmToken.token == token))
    if existing:
        existing.user_id = current_user.id
        existing.platform = payload.platform or existing.platform
        existing.device_label = payload.device_label or existing.device_label
    else:
        db.add(UserFcmToken(
            user_id=current_user.id,
            token=token,
            platform=payload.platform,
            device_label=payload.device_label,
        ))
    db.commit()
    return None


@router.delete("/me/fcm-token", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
def delete_fcm_token(
    request: Request,
    payload: FcmTokenDeleteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Logout sirasinda mobil app kendi token'ini siler."""
    _ = request
    token = (payload.token or "").strip()
    if not token:
        return None
    row = db.scalar(select(UserFcmToken).where(
        UserFcmToken.token == token, UserFcmToken.user_id == current_user.id
    ))
    if row is not None:
        db.delete(row)
        db.commit()
    return None
