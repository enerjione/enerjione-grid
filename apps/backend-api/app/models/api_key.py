"""Kullanici-bazli API Key (GitHub PAT tarzi).

Her kullanici kendi hesabina ozel, scope'lu, sureli ve revoke edilebilir
token'lar uretebilir. Token'in plain hali yalnizca olusturulma aninda
gosterilir; veritabaninda SHA-256 hash olarak saklanir.

Tasarim notlari:
  * Token formati:  `hsl_pat_<32-byte-secret>` (base64url). Prefix'i kullanici
    UI'da gorur ki kazara push'larsa kolay tespit edilebilsin (GitHub'la ayni
    desen).
  * Scope alani CSV: `telemetry:read,devices:read,alarms:read`.
    Tam liste icin api_key_service.ALLOWED_SCOPES.
  * `expires_at` NULL ise suresiz. Yine de gunluk audit + manuel revoke yapilir.
  * `revoked_at` set olduktan sonra anahtar dogrulanmaz (verify'da fail).
  * `last_used_at` her basarili request sonrasi guncellenir (debounce ile —
    her saniye en fazla bir yazma) — DB yuku icin.
  * Token kazara push edildiyse audit log'da kullanan IP gozukur, hizli iptal.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Kullanicinin verdigi okunabilir isim — "Postman dev", "Grafana entegrasyon" vs.
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Token'in sha256 hex hash'i (64 karakter). Plain token sadece olusturma
    # aninda dondurulur; DB'de hash saklanir.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Token prefix (gosterim icin) — "hsl_pat_aB3..." gibi ilk 12 karakter.
    # Kullanici UI'da tum listeyi gorurken hangi token oldugunu hatirlasin.
    token_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    # CSV scope listesi. Bos string = hicbir scope (token kullanilamaz).
    scopes: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    # Olusturulma, son kullanim ve son revoke zaman damgalari.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # IP whitelist (CSV). Bos = herhangi bir IP'den kullanilabilir.
    allowed_ips: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Devre disi - revoke'tan farkli: token canli ama bir sure durdurulmus.
    # Revoke geri donulemez; is_active=False geri alinabilir.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
