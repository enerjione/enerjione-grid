# Güvenlik & Operasyonel Sertleştirme Yol Haritası

**Hazırlık tarihi:** 2026-05-08
**Kapsam:** `Horstmann Smart Logger DNP3 Gateway` (saha) + `Horstman Smart Logger` platformu (backend-api, tag-engine, alarm-service, notification-worker, iec104-outbound, frontend-web).
**Yöntem:** İki paralel kod denetimi sonucu birleştirilmiş bulgular; her madde **dosya:satır referansı** + **gerçekçi etki senaryosu** + **hızlı/kalıcı çözüm** ile.

> Bu doküman bir "tek seferlik liste" değil — her sprint başında üzerinden geçilip "yapıldı / yapılacak" işaretlenecek **canlı yol haritası**. Yapılan maddeleri silmeyin; tarih + commit hash bırakarak işaretleyin.

---

## İçerik

1. [İlk hafta paketi (4-6 saat)](#1-ilk-hafta-paketi-blast-radius-büyük-efor-küçük)
2. [Sprint 1 — Yüksek riskli (3-5 gün)](#2-sprint-1--yüksek-riskli)
3. [Sprint 2-3 — Orta vadeli sertleştirme](#3-sprint-2-3--orta-vadeli-sertleştirme)
4. [Düşük öncelikli iyileştirmeler](#4-düşük-öncelikli-iyileştirmeler)
5. [Korunması gereken pozitifler (regresyon önle)](#5-korunması-gereken-pozitifler)
6. [İzleme ve ölçüm önerileri](#6-i̇zleme-ve-ölçüm-önerileri)
7. [Madde durumları (kanban)](#7-madde-durumları)

---

## 1. İlk hafta paketi — blast radius büyük, efor küçük

Bu dört iş tek bir PR'a sığar; production'a açık olan her sistemde **bugün** yapılması gereken minimum sertleştirme.

### 1.1 [KRİTİK] JWT secret ve internal service token default'larını üretimde reddet

**Sorun:** `apps/backend-api/app/core/config.py:8` `secret_key="change-me-in-production"`, satır 41 `internal_service_token="change-me-internal-token"`. Aynı default tag-engine, alarm-service, notification-worker'da fallback olarak da var.

**Etki:** `.env` yüklenmeden başlatılırsa **herkes JWT üretebilir**, internal endpoint'ler tamamen açık.

**Çözüm:**
```python
# apps/backend-api/app/core/config.py içinde Settings sınıfına:
@field_validator("secret_key", "internal_service_token")
@classmethod
def _no_default_in_prod(cls, v: str, info) -> str:
    if v.startswith("change-me") and \
       (info.data.get("app_environment") or os.getenv("APP_ENVIRONMENT")) in ("staging", "production"):
        raise RuntimeError(f"{info.field_name} production'da default olamaz")
    return v
```
Aynı kontrolü tag-engine/alarm-service/notification-worker `main.py`'lerinin başına da koy.

**Doğrulama:** `APP_ENVIRONMENT=production` + default token → süreç fail.

---

### 1.2 [KRİTİK] CORS wildcard + credentials kombinasyonu

**Sorun:** `apps/backend-api/app/main.py:20-27` `allow_origin_regex=".*"` **ve** `allow_credentials=True` aynı anda.

**Etki:** Tarayıcılar bunu çoğunlukla bloke etse de FastAPI sessizce permissive çalışıyor. Saldırgan domain'inden açılan tab kullanıcı cookie/auth header ile istek atabilir.

**Çözüm:** Production'da `CORS_ORIGINS` env'i zorunlu yap; "*" görüldüğünde:
```python
if "*" in _cors_origins and settings.app_environment == "production":
    raise RuntimeError("CORS_ORIGINS production'da '*' olamaz")
```
ve `allow_origin_regex` yerine `allow_origins=settings.cors_origin_list` kullan.

**Doğrulama:** Production env + `CORS_ORIGINS=*` → süreç fail; `CORS_ORIGINS=https://app.formelektrik.com` → istek geçer, başka domain → 403.

---

### 1.3 [YÜKSEK] Gateway sağlık endpoint'i 0.0.0.0 → 127.0.0.1

**Sorun:** `Horstmann Smart Logger DNP3 Gateway/Dockerfile:63` → `WORKER_HEALTH_HOST=0.0.0.0`. Container ağında dinleyen herkes `/info`, `/metrics` endpoint'lerinden config_version, instance_id, uptime, son hata mesajı gibi bilgilere yetkisiz erişebiliyor.

**Etki:** Bilgi sızması — saldırgan fingerprint için kullanır.

**Çözüm:** Dockerfile'da default'ı `127.0.0.1` yap. Compose template zaten `127.0.0.1:{HOST_HEALTH_PORT}:8020` ile sızdırıyor; container içi loopback yeterli.

**Doğrulama:** Container içine `exec` olup `curl http://127.0.0.1:8020/health` → 200; container ağındaki başka servisten çağırınca → connection refused.

---

### 1.4 [YÜKSEK] /login rate limit + brute-force koruması

**Sorun:** `apps/backend-api/app/api/auth.py:20-39` rate limit yok. `seed_installer.py:10`'daki `ChangeMe123!` default şifresi (madde 2.5) saniyeler içinde brute-force ile bulunur.

**Çözüm:**
```bash
pip install slowapi
```
```python
# apps/backend-api/app/main.py
from slowapi import Limiter
from slowapi.util import get_remote_address
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

# auth.py
@router.post("/login")
@limiter.limit("5/minute")
def login(request: Request, ...): ...
```

**Doğrulama:** 6. başarısız deneme → 429.

---

## 2. Sprint 1 — Yüksek riskli

### 2.1 [YÜKSEK] IEC 104 server peer whitelist zorunlu

**Sorun:** `apps/iec104-outbound/iec104_outbound/config.py` → `IEC104_DEFAULT_LISTEN_HOST="0.0.0.0"`; `server.py` `allowed_peers` boşsa erişim serbest. IEC 104 standardı authentication içermiyor; tek savunma IP whitelist.

**Etki:** Ağdaki herhangi biri sahte SCADA gibi davranıp clock sync atar veya general interrogation spam'iyle DoS uygular.

**Çözüm:**
- `outbound_targets` tablosuna `allowed_peer_ips: VARCHAR(500)` (CSV) ekle.
- `bootstrap.py` deploy sırasında bu listeyi `IEC104Server(allowed_peers=...)` parametresine geçir.
- UI'da bu alanı zorunlu yap (boş bırakılırsa server **deploy edilmez**, target `is_active=False` zorla).

**Doğrulama:** Whitelist dışı IP'den `socket.connect("gateway", 2404)` → handshake öncesi disconnect log'u: `iec104_client_rejected_whitelist`.

---

### 2.2 [YÜKSEK] REST outbound dispatch SSRF koruması

**Sorun:** `apps/backend-api/app/services/outbound_dispatch_service.py:102-109` → `urllib.request.urlopen(req, timeout=8)` URL filtresi yok.

**Etki:** Engineer yetkili kullanıcı outbound target oluştururken `http://127.0.0.1:5432`, `http://169.254.169.254/latest/meta-data/` (cloud IAM credential leak), `http://10.x.x.x/admin` gibi internal endpoint'lere telemetri payload'ı gönderebilir.

**Çözüm:**
```python
import ipaddress, socket
from urllib.parse import urlparse

def _validate_outbound_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http/https allowed")
    if not parsed.hostname:
        raise ValueError("hostname yok")
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(parsed.hostname))
    except (socket.gaierror, ValueError):
        raise ValueError("hostname resolve edilemedi")
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise ValueError("private/loopback/metadata adresi gönderilemez")
```
`OutboundTargetCreate` ve `OutboundTargetUpdate` pydantic modellerinde `@field_validator("endpoint")` olarak çağır.

**Doğrulama:** `endpoint=http://169.254.169.254/...` → 422 ValidationError.

---

### 2.3 [YÜKSEK] Gateway TLS verification production zorlaması

**Sorun:** `Horstmann Smart Logger DNP3 Gateway/src/dnp3_gateway/config.py:374-379` — `BACKEND_API_VERIFY_SSL=false` development'ta serbest, default environment "development". Yanlış env ile prod deploy → MITM açık.

**Çözüm:** Environment kontrolünü `("staging","production")` listesinden `!= "development"` yap (yani **kapsayıcı** olsun, yeni eklenecek `qa`, `preprod` gibi env'ler de korunsun). Ayrıca development'ta `verify=False` warn log düşür.

**Doğrulama:** `APP_ENVIRONMENT=production BACKEND_API_VERIFY_SSL=false` → SystemExit, bilgilendirici mesaj.

---

### 2.4 [YÜKSEK] Gateway SQLite outbox bound + disk monitoring

**Sorun:** `Horstmann Smart Logger DNP3 Gateway/src/dnp3_gateway/messaging/outbox.py:44-45` → `DEFAULT_MAX_PENDING=500_000`. RabbitMQ 1 gün down → 100 cihaz × ~200 msg/dakika ≈ 28M mesaj birikir; SQLite bloat → inode tükenir → restart başarısız.

**Çözüm:**
- `DEFAULT_MAX_PENDING=50_000` (≈8 saat broker outage tolerance) — tunable kalır.
- `/health` body'sine `outbox_pending` (mevcut) + **`disk_free_mb`** ekle: `shutil.disk_usage(state_dir).free // 1_048_576`.
- Disk free < 500 MB ise `/health` → 503.

**Doğrulama:** `tmpfs` 100MB ile container çalıştır, broker'ı kapat; `outbox_pending` 50K'ya ulaşınca yeni publish'ler `OutboxFullError` döner ama gateway donmaz.

---

### 2.5 [YÜKSEK] İlk login'de şifre değiştirme zorunlu

**Sorun:** `apps/backend-api/scripts/seed_installer.py:10` → `DEFAULT_PASSWORD = "ChangeMe123!"`. README'de "ilk girişte değiştir" notu var ama enforcement yok.

**Çözüm:**
- `users` tablosuna `password_change_required: BOOLEAN NOT NULL DEFAULT FALSE` ekle (migration).
- `seed_installer.py` yeni kullanıcıyı `password_change_required=True` ile yarat.
- `auth.py` login endpoint'i token'la birlikte `must_change_password: bool` döner; **bu flag true ise** `/auth/change-password` dışındaki tüm endpoint'ler 403 döner (middleware ile).
- Frontend login akışı `must_change_password` görürse direkt değiştirme formuna yönlendirir.

**Doğrulama:** Yeni installer ile login → token alır ama herhangi bir API çağrısı 403 ("password change required") döner; şifre değiştirildikten sonra normal akış.

---

### 2.6 [YÜKSEK] Internal service token sabit-zaman karşılaştırma

**Sorun:** `apps/backend-api/app/api/internal.py:27-29` → `if token != settings.internal_service_token`. Timing attack'a açık.

**Çözüm:**
```python
import hmac
def _require_service_token(token: str | None) -> None:
    if token is None or not hmac.compare_digest(
        token.encode("utf-8"),
        settings.internal_service_token.encode("utf-8"),
    ):
        raise HTTPException(status_code=401, detail="Invalid service token")
```

**Doğrulama:** Mevcut testler geçer; yan etki yok.

---

## 3. Sprint 2-3 — Orta vadeli sertleştirme

### 3.1 [ORTA] Pydantic mass assignment koruması

`alarm_rules.py:45`, `outbound_targets.py:74`, `signals.py:53`, `responsibility_areas.py:113` `Model(**payload.model_dump())` — şema dikkatli yazıldığı sürece güvenli, ama yarın biri `is_active`/`created_by`/`owner_id` ekleyince bypass açılır.

**Çözüm:** Tüm Create/Update şemalarında:
```python
class OutboundTargetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ...
```
Ek olarak `Model(**payload.model_dump(exclude={"is_active"}))` gibi explicit dışlama.

---

### 3.2 [ORTA] Access token ömrü + refresh token

**Sorun:** `apps/backend-api/app/core/config.py:13` → `access_token_minutes: int = 43_200` (~30 gün). Logout token'ı revoke etmiyor.

**Çözüm:**
- Access token 60 dakika.
- Refresh token (7 gün, DB'de `refresh_tokens` tablosu, hash'li saklanır, JTI claim).
- Logout → refresh token revoke.
- Token blacklist tablosu + günlük cleanup job.

---

### 3.3 [ORTA] Recovery burst rate-limit

**Sorun:** Recovery onaylandığında `mark_all_dirty` 100 cihaz × 175 sinyal × N gateway = 17.5K-100K mesaj burst → publisher confirm timeout → reconnect → cascade.

**Çözüm:** Outbox publish hızını sınırla (token bucket; `MAX_PUBLISH_RATE_PER_SEC=500`, default tunable). Recovery'nin doğal yayılmasını sağlar; SCADA ~30 sn'de tüm cihazları "good" görür.

---

### 3.4 [ORTA] Tag-engine ve alarm-service idempotency

**Sorun:** `tag-engine/main.py:100-132` ve alarm-service consumer'ı `processed_messages` tablosunu **kontrol etmiyor**. RabbitMQ requeue'da aynı mesaj 2 kez işlenebilir.

**Çözüm:** Consumer callback'in başında:
```python
exists = db.scalar(select(ProcessedMessage).where(
    ProcessedMessage.consumer_name == "alarm_service",
    ProcessedMessage.message_id == message_id,
))
if exists:
    ch.basic_ack(delivery_tag=method.delivery_tag)
    return
# işle, sonra processed_messages'a INSERT + ack tek transaction'da
```

---

### 3.5 [ORTA] Backend config response input validation

**Sorun:** Gateway `config_client.py:271-346` `device.ip_address` sadece `str()` cast, `dnp3_object_group` 0-255 range check yok.

**Çözüm:** Gateway tarafında pydantic model:
```python
from ipaddress import ip_address
class _DeviceConfigPayload(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    ip_address: str
    @field_validator("ip_address")
    def _valid_ip(cls, v): ip_address(v); return v
```
Backend ele geçirilirse gateway en azından sahte IP'leri reddeder.

---

### 3.6 [ORTA] Outbound dispatch dead-letter PII azalt

**Sorun:** `outbound_dispatch_service.py:97` dead-letter metadata'ya `"payload": payload` koyuyor. Telemetri PII içermez ama event log retention gereksiz.

**Çözüm:** Tam payload yerine:
```python
"payload_summary": {"keys": list(payload.keys()), "size_bytes": len(json.dumps(payload))},
"payload_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16],
```

---

### 3.7 [ORTA] IDOR — entity ownership filter

**Sorun:** `devices.py`, `alarms.py` role check (engineer/operator) yapıyor ama "bu cihaz bu kullanıcının sorumluluk alanında mı" kontrolü yok. Çok-tenant senaryoda engineer tüm cihazları görür.

**Çözüm:** ResponsibilityArea üzerinden filtre helper'ı:
```python
def filter_by_responsibility(query, user: User):
    if user.role == UserRole.INSTALLER:
        return query  # tam erişim
    return query.join(Device.responsibility_area).where(
        ResponsibilityArea.id.in_([a.id for a in user.responsibility_areas])
    )
```
List + detail + update endpoint'lerinde uygula.

---

### 3.8 [ORTA] Container hardening

**Çözüm:** Compose'lara:
```yaml
services:
  gateway:
    read_only: true
    tmpfs:
      - /tmp
    cap_drop: [ALL]
    security_opt:
      - no-new-privileges:true
    volumes:
      - state:/app/.gateway_state  # state RW kalır
```
Aynısı backend-api, tag-engine, alarm-service container'larına da.

---

## 4. Düşük öncelikli iyileştirmeler

| ID | Konu | Çözüm |
|---|---|---|
| 4.1 | Requirements pin | `pip-tools` ile `requirements.lock` üret; build reproducible |
| 4.2 | NTP/clock skew | `/health` endpoint'inde NTP offset (`ntplib` veya systemd-timesyncd query); >5 sn warning |
| 4.3 | yadnp3 fallback uyarısı | Gateway startup'ta `DNP3_LIBRARY=nfm-dnp3` ise `signal_catalog.data_type=='string'` sinyaller için warning log |
| 4.4 | DNP3 SA evaluasyonu | Horstmann SN2 firmware'i Secure Authentication v5/v6 destekliyor mu? Destekliyorsa adapter implement (uzun vadeli) |
| 4.5 | Frontend XSS audit | `dangerouslySetInnerHTML`, `eval`, raw HTML render için tarama (`grep -r dangerouslySetInnerHTML apps/frontend-web/src`) |
| 4.6 | Backup test prosedürü | DB backup restore drill (örn. ayda bir staging'e restore + smoke test) |
| 4.7 | Healthcheck timeout | Dockerfile `HEALTHCHECK timeout=3s` → 10s; slow start tolerance |
| 4.8 | Frontend token storage | Access token localStorage'da mı cookie'de mi? localStorage XSS'e açık; httpOnly cookie + SameSite=strict daha iyi |

---

## 5. Korunması gereken pozitifler

Bunlar **regresyon önle** listesi — refactor yaparken kaybetmemeye dikkat:

- ✅ **Şifre saklama:** `pbkdf2_sha256` ([`auth_service.py:8`](../apps/backend-api/app/services/auth_service.py#L8)). Argon2'ye geçiş ileride güzel olur ama acil değil.
- ✅ **RabbitMQ gateway user şifresi:** `secrets.choice()` ile 32 karakter cryptographically secure ([`rabbitmq_admin.py:49-53`](../apps/backend-api/app/services/rabbitmq_admin.py#L49-L53)).
- ✅ **Migration stratejisi:** `ALTER TABLE IF NOT EXISTS` idempotent, downtime yok ([`main.py:54+`](../apps/backend-api/app/main.py#L54)).
- ✅ **Gateway non-root:** `Dockerfile:68-71` uid 1000 hsl user.
- ✅ **Gateway log redaction:** `logging_setup.py` AMQP password regex + `register_secret()`.
- ✅ **Outbox pattern:** Gateway SQLite ile durable buffer, broker outage'da veri kaybı yok.
- ✅ **Recovery state machine:** Yeni eklediğimiz fresh-frame doğrulaması (commit `6144123`) sahte online'ı engelliyor.
- ✅ **DLX (dead-letter exchange):** Tag-engine ve alarm-service hatalı mesajları DLX'e yolluyor; `processed_messages` tablosu altyapı hazır (consumer'ların kullanması için 3.4 gerekli).
- ✅ **Health endpoint authentication örüntüsü:** `/refresh-all` POST'unda token kontrolü var; aynı pattern `/info`, `/metrics`'e de yayılmalı (madde 1.3).

---

## 6. İzleme ve ölçüm önerileri

Aşağıdaki metrikler **Prometheus + Grafana** veya benzeri bir stack ile çıkarılırsa hata erken yakalanır:

| Metrik | Kaynak | Eşik |
|---|---|---|
| `gateway_outbox_pending` | gateway `/health` | >10K → warning, >40K → critical |
| `gateway_disk_free_mb` | gateway `/health` (madde 2.4) | <1000 → warning, <500 → critical |
| `gateway_last_config_refresh_age_sec` | gateway `/health` | >300 → warning |
| `gateway_recovery_count_5min` | log `yadnp3_device_recovered` | >cihaz_sayısı × 0.3 → flap alarmı |
| `iec104_active_servers` | iec104-outbound `/health` | beklenen != gerçek → critical |
| `iec104_messages_processed_rate` | iec104-outbound `/health` | 0 ama active_servers >0 → consumer kopuk |
| `backend_login_failure_rate` | system_events `event_type=login_failed` | >10/dk → brute-force alarmı |
| `rabbitmq_queue_depth` | RabbitMQ management API | >50K → consumer geride kaldı |
| `dlx_message_count` | RabbitMQ DLX queues | >0 → işlenmemiş hata var |
| `cert_days_until_expiry` | TLS sertifikaları | <30 → renew uyarısı |

---

## 7. Madde durumları

> Bu tabloyu PR'larla beraber güncelleyin. Bir madde tamamlandığında: durum + commit hash + tarih.

| ID | Konu | Seviye | Tahmini efor | Durum | Commit / Not |
|---|---|---|---|---|---|
| 1.1 | Default secret production reddi | KRİTİK | 1 saat | ⏳ Yapılacak | — |
| 1.2 | CORS wildcard + credentials | KRİTİK | 1 saat | ⏳ Yapılacak | — |
| 1.3 | Gateway health 0.0.0.0 → 127.0.0.1 | YÜKSEK | 30 dk | ⏳ Yapılacak | — |
| 1.4 | /login rate limit | YÜKSEK | 2 saat | ⏳ Yapılacak | — |
| 2.1 | IEC 104 peer whitelist zorunlu | YÜKSEK | 1 gün | ⏳ Yapılacak | — |
| 2.2 | REST outbound SSRF koruması | YÜKSEK | 4 saat | ⏳ Yapılacak | — |
| 2.3 | Gateway TLS prod zorlaması | YÜKSEK | 30 dk | ⏳ Yapılacak | — |
| 2.4 | Outbox bound + disk monitoring | YÜKSEK | 4 saat | ⏳ Yapılacak | — |
| 2.5 | Şifre değiştirme zorunluluğu | YÜKSEK | 1 gün | ⏳ Yapılacak | — |
| 2.6 | Internal token sabit-zaman compare | YÜKSEK | 15 dk | ⏳ Yapılacak | — |
| 3.1 | Pydantic `extra=forbid` | ORTA | 2 saat | ⏳ Yapılacak | — |
| 3.2 | Token TTL + refresh token | ORTA | 2 gün | ⏳ Yapılacak | — |
| 3.3 | Recovery burst rate-limit | ORTA | 1 gün | ⏳ Yapılacak | — |
| 3.4 | Consumer idempotency | ORTA | 1 gün | ⏳ Yapılacak | — |
| 3.5 | Gateway config validation | ORTA | 4 saat | ⏳ Yapılacak | — |
| 3.6 | DLX PII azalt | ORTA | 2 saat | ⏳ Yapılacak | — |
| 3.7 | IDOR ownership filter | ORTA | 2 gün | ⏳ Yapılacak | — |
| 3.8 | Container hardening | ORTA | 4 saat | ⏳ Yapılacak | — |
| 4.1 | Requirements lock | DÜŞÜK | 2 saat | ⏳ Yapılacak | — |
| 4.2 | NTP/clock skew metric | DÜŞÜK | 2 saat | ⏳ Yapılacak | — |
| 4.3 | yadnp3 fallback uyarısı | DÜŞÜK | 1 saat | ⏳ Yapılacak | — |
| 4.4 | DNP3 SA evaluasyonu | DÜŞÜK | 1 hafta R&D | ⏳ Yapılacak | — |
| 4.5 | Frontend XSS audit | DÜŞÜK | 4 saat | ⏳ Yapılacak | — |
| 4.6 | Backup restore drill | DÜŞÜK | aylık recurring | ⏳ Yapılacak | — |
| 4.7 | Healthcheck timeout artır | DÜŞÜK | 15 dk | ⏳ Yapılacak | — |
| 4.8 | Token storage strategy | DÜŞÜK | 1 gün | ⏳ Yapılacak | — |

**Durum simgeleri:**
- ⏳ Yapılacak
- 🚧 Devam ediyor (PR açık)
- ✅ Tamamlandı (commit hash + tarih ile)
- 🚫 İptal (gerekçe zorunlu)
- 📦 Bağımlı (önce başka bir madde)

---

## Ek: Hızlı referans — ilk PR template'i

İlk haftada açacağınız PR için bir başlangıç:

```
Başlık: security: ilk hafta sertleştirme paketi (1.1, 1.2, 1.3, 1.4)

Kapsam:
- backend-api/app/core/config.py — production'da default secret reddi
- backend-api/app/main.py — CORS wildcard + credentials kontrolü
- gateway/Dockerfile — health endpoint 127.0.0.1
- backend-api/app/api/auth.py — slowapi rate limit (5/dk)

Test planı:
- [ ] APP_ENVIRONMENT=production + SECRET_KEY=change-me-in-production → backend startup fail
- [ ] APP_ENVIRONMENT=production + CORS_ORIGINS=* → backend startup fail
- [ ] CORS_ORIGINS=https://valid → istek geçer; başka domain → 403
- [ ] Gateway container'a exec olup curl 127.0.0.1:8020/health → 200
- [ ] Gateway container ağındaki başka container'dan health → connection refused
- [ ] /login 6. başarısız deneme → 429

Risk: orta. CORS değişikliği staging'de smoke test gerekli (frontend domain'i whitelist'te olmalı).

Geri alma: env değerleri eski default'a, slowapi import'ları geri alınır, Dockerfile satırı revert.
```

---

**Bu doküman sahibi:** Form Elektrik / Horstmann Smart Logger ekibi
**Son güncelleme:** 2026-05-08
**Sonraki gözden geçirme:** İlk hafta paketi tamamlandıktan sonra (madde 1.1-1.4 ✅).
