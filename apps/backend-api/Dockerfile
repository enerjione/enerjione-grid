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
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts

EXPOSE 8000

# Uvicorn workers tek tutuluyor — alarm engine, IEC 104 server'lari ve telemetry
# consumer thread'leri tek process state'inde paylasilan singleton'lara dayaniyor.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
