"""Uzaktan bakim izni (Uzaktan Bakim sayfasi) icin Pydantic sema'lari.

Kaynak: host'ta root ile calisan `e1-rad` ajaninin yazdigi state.json /
status.json dosyalari. Backend tailscale'i CALISTIRMAZ; izin/geri alma
istegini request.json olarak yazar.

TEMEL KURAL: uzaktan erisim VARSAYILAN KAPALIDIR. Musterinin yetkili
kullanicisi sureli izin verir; sure dolunca erisim kendiliginden kapanir.
Sureyi HOST AJANI sayar — backend/DB/container tamamen kapali olsa bile izin
kapanmali. Bu yuzden burada bir "son tarih" alani UYGULAMA otoritesi DEGIL,
yalnizca ajanin bildirdigi degerin aynasidir.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TailscaleInfo(BaseModel):
    """Dugumun tailnet durumu. Authkey ASLA bu yapida tasinmaz."""

    installed: bool = False
    version: str | None = None
    daemon_running: bool = False
    # Running | Stopped | NeedsLogin | NoState | Starting
    backend_state: str | None = None
    # Dugum tailnet'e kayitli mi. `down`/`shields-up` bunu BOZMAZ; yalnizca
    # `tailscale logout` bozar ve ajan onu asla calistirmaz.
    registered: bool = False
    hostname: str | None = None
    dns_name: str | None = None
    ipv4: str | None = None
    tags: list[str] = Field(default_factory=list)
    # None = anahtar suresi uygulanmiyor (etiketli katilimda istenen durum).
    key_expiry: str | None = None
    key_expired: bool = False
    ssh_enabled: bool = False
    shields_up: bool = True


class RemoteAccessGrant(BaseModel):
    """SU ANKI izin penceresi. Kapali ise open=False ve alanlar None."""

    # OLCULEN deger: ajan `tailscale debug prefs` gercekten ShieldsUp=false
    # diyorsa true yazar. Niyet degil olcum yayinlanir.
    open: bool = False
    # Ajan durumu okuyabildi mi (cok eski tailscale'de prefs alani olmayabilir).
    verified: bool = False
    # prefs_unreadable | open_failed | close_failed
    mismatch: str | None = None
    session_id: str | None = None
    granted_by: str | None = None
    granted_by_role: str | None = None
    granted_at: str | None = None
    expires_at: str | None = None
    duration_minutes: int | None = None
    reason: str | None = None
    # ui | install | update | cli
    source: str | None = None
    # ASAGIDAKI IKISINI AJAN DEGIL BACKEND HESAPLAR (bkz. servis notu):
    # ajan 30 sn'de bir yazar, arada deger bayatlar ve UI geri sayimi tutmaz.
    remaining_seconds: int | None = None
    # Son tarih gecmis ama ajan hala "acik" rapor ediyor -> ajan durmus
    # olabilir. `open` alanini KAPALI diye DEGISTIRMIYORUZ (yalan olurdu);
    # UI bu bayrakla kirmizi uyari gosterir.
    overdue: bool = False


class RemoteAccessSession(BaseModel):
    """Kapanmis son oturumun ozeti (denetim uzlastirmasi bunu kullanir)."""

    session_id: str | None = None
    granted_by: str | None = None
    granted_by_role: str | None = None
    granted_at: str | None = None
    expires_at: str | None = None
    ended_at: str | None = None
    # expired | revoked | clock_skew | lease_corrupt | cli
    end_reason: str | None = None
    duration_minutes: int | None = None
    reason: str | None = None
    source: str | None = None


class RemoteAccessApplyStatus(BaseModel):
    """Ajanin isledigi son istegin sonucu (status.json)."""

    request_id: str | None = None
    action: str | None = None       # grant | revoke
    status: str | None = None       # applying | applied | failed
    error: str | None = None
    at: str | None = None
    applied: dict | None = None


class RemoteAccessStatus(BaseModel):
    """Uzaktan Bakim sayfasinin TEK okuma kaynagi."""

    # Ajan kullanilabilir mi (dizin var/yazilabilir + en az bir rapor yazilmis).
    available: bool
    # state_dir_missing | state_dir_not_writable | agent_never_reported |
    # state_stale
    reason: str | None = None
    # Ajanin bildirdigi kullanilamazlik sebebi (tailscale tarafi):
    # tailscale_not_installed | daemon_not_running | not_registered | key_expired
    agent_reason: str | None = None
    updated_at: str | None = None
    # state.json'in yasi (sn). Buyukse zamanlayici durmus demektir ve o zaman
    # acik bir izin KENDILIGINDEN KAPANMAZ — UI bunu kirmizi gostermeli.
    state_age_seconds: float | None = None
    # shields (dugum online kalir, gelen baglanti reddedilir) | down (tunel de iner)
    lock_mode: str | None = None
    tailscale: TailscaleInfo = Field(default_factory=TailscaleInfo)
    access: RemoteAccessGrant = Field(default_factory=RemoteAccessGrant)
    last_session: RemoteAccessSession | None = None
    # Islenmeyi bekleyen istek — UI "uygulaniyor" gosterir.
    pending: bool = False
    last_apply: RemoteAccessApplyStatus | None = None
    # UI hazir sure butonlarini bu sinirlara gore eler.
    min_duration_minutes: int = 15
    max_duration_minutes: int = 1440
    # Izin verme yetkisi SADECE engineer rolunde. UI butonu buna gore gizler;
    # asil kontrol backend'de (bkz. api/remote_access.py).
    can_grant: bool = False


class RemoteAccessGrantRequest(BaseModel):
    """Sureli izin istegi.

    Ust sinir 1440 dk (24 saat) BILEREK sabittir: sinirsiz sure "her zaman
    acik"i geri getirir, yani ozelligin tamamini iptal ederdi. Daha uzunu
    ikinci bir izin ister — ve o tekrar eden riza kaydi bu ozelligin asil
    degeridir. Alt sinir 15 dk: tunelin ayaga kalkmasi + insanin baglanmasi
    bundan kisa surede bitmez, 1 dakikalik izin yalnizca denetim gurultusu
    uretirdi.

    Nihai otorite AJANDIR: ayni sinirlari bagimsiz uygular ve son tarihi
    KENDI saatinden hesaplar.
    """

    duration_minutes: int = Field(..., ge=15, le=1440)
    # Riza kaydinin degerini artirir (ariza/talep no). Zorunlu DEGIL: acil
    # durumda kullaniciyi bloklamaz.
    reason: str | None = Field(default=None, max_length=200)
    # Tailscale SSH de acilsin mi. Host tarafinda E1_TAILSCALE_SSH=0 ise
    # istege bakilmaksizin kapali kalir.
    allow_ssh: bool = True

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, value: str | None) -> str | None:
        cleaned = (value or "").strip()
        return cleaned or None


class RemoteAccessRevokeRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=200)

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, value: str | None) -> str | None:
        cleaned = (value or "").strip()
        return cleaned or None


class RemoteAccessAccepted(BaseModel):
    """Istek kuyruga alindi — uygulama ajanda asenkron surer."""

    request_id: str
    action: Literal["grant", "revoke"]
    # TAHMINI son tarih. KESIN deger ajanin hesabidir (kendi saati + kendi ust
    # siniri); UI bir sonraki GET /status ile tazeler.
    expires_at: str | None = None
