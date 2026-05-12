"""Rate limiting — slowapi tabanli, in-memory backend.

Kullanim:
    from app.core.rate_limit import limiter

    @router.post("/login")
    @limiter.limit("5/minute")
    async def login(request: Request, ...):
        ...

Default key: client IP (X-Forwarded-For destekli). Production'da reverse
proxy varsa uvicorn `--forwarded-allow-ips` ile gerçek IP'yi gönderir.

In-memory backend: tek process'lik instance icin yeterli. Multi-replica
deploy'da Redis backend gerekir (`slowapi[redis]`):
    limiter = Limiter(key_func=get_remote_address, storage_uri="redis://...")
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

# Default key function: get_remote_address X-Forwarded-For'i de okur, ama
# reverse proxy zincirinde guvenli IP icin uvicorn `--forwarded-allow-ips`
# parametresi gerekli. Aksi takdirde saldirgan X-Forwarded-For header'i
# spoof ederek rate-limit'i bypass edebilir.
limiter = Limiter(
    key_func=get_remote_address,
    # Default limit yok — sadece explicit dekorator'la limit konan endpoint'ler
    # kontrole girer. Boylece API'nin tamami yanlislikla rate-limit'lenmez.
    default_limits=[],
)
