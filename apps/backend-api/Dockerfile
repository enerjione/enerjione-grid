# Backend API — FastAPI + SQLAlchemy + RabbitMQ consumer
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Sistem paketleri:
#   - build-essential, libpq-dev: psycopg2 build icin
#   - curl: healthcheck icin
#   - gnupg, ca-certificates: PostgreSQL APT reposunu eklemek icin
#   - postgresql-client-16: pg_dump / pg_restore (DB yedek alma).
#     Postgres server (compose) 16 — istemcinin de ayni major surum olmali,
#     yoksa "server version mismatch" hatasi alinir.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
        gnupg \
        ca-certificates \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-16 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Supply-chain safety: requirements.txt pip-compile ile uretilirse her satirda
# `--hash=sha256:...` icerir. Boyle bir durumda `--require-hashes` zorlanir
# (pip hash dogrulamasini her paket icin yapar; uyusmazsa build basarisiz olur).
# Hash icermeyen satir varsa pip --require-hashes'i ignore eder ve standart kurar
# (geriye uyumluluk). Operator hash-locked requirements icin:
#   pip install pip-tools && pip-compile --generate-hashes requirements.in
# grep yorumlar (#-prefix) HARIC sadece veri satirlarinda `--hash=` arar.
# Eski mantik: `grep -q -- '--hash='` requirements.txt'in usteki yorum
# bloguna takiliyordu ("# Once requirements.txt contains `--hash=sha256:...`")
# -> --require-hashes koluna giriyor -> ilk unhashed satirda fail.
RUN if grep -qE '^[^#]*--hash=' requirements.txt; then \
        pip install --require-hashes -r requirements.txt; \
    else \
        pip install -r requirements.txt; \
    fi

COPY app ./app
COPY scripts ./scripts

# Non-root user — container escape exploit'lerinde host root'a yakin
# olmamak icin. UID 10001 reserved-range; backup-data volume host'unda
# (`/var/lib/e1-backups`) bu UID'in yazma yetkisi olmali — Dockerfile
# `chown` ile /app + /var/lib/e1-backups'i kendisine atar.
RUN groupadd -r e1 --gid=10001 \
 && useradd -r -g e1 --uid=10001 --home-dir=/app --shell=/sbin/nologin e1 \
 && mkdir -p /var/lib/e1-backups \
 && chown -R e1:e1 /app /var/lib/e1-backups
USER e1

EXPOSE 8000

# Uvicorn workers tek tutuluyor — alarm engine, IEC 104 server'lari ve telemetry
# consumer thread'leri tek process state'inde paylasilan singleton'lara dayaniyor.
# `--proxy-headers`: X-Forwarded-* parse et.
# `--forwarded-allow-ips=*`: TRUSTED proxy IP listesi. Default "127.0.0.1"
# (uvicorn) container icinde frontend-web nginx baska IP'den geldigi icin
# tanimaz; "*" kabul edilir cunku 8000 portu compose internal network'unde
# (host'a expose edilmemis), saldirgan zaten direkt erisemez. Production'da
# sadece nginx container'i 8000'e ulasabilir.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
