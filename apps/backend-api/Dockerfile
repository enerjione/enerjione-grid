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
#   - iputils-ping: saha araclari ping testi (/field-tools/ping). Non-root
#     kullanicida da calisir: Docker `net.ipv4.ping_group_range` sysctl'ini
#     varsayilan acik verir, iputils ICMP datagram soketi kullanir.
#   - iputils-tracepath: saha araclari traceroute (/field-tools/traceroute).
#     Klasik `traceroute` CAP_NET_RAW ister; tracepath root GEREKTIRMEZ.
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
        iputils-ping \
        iputils-tracepath \
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
COPY alembic.ini ./alembic.ini
COPY alembic_migrations ./alembic_migrations

# Non-root user — container escape exploit'lerinde host root'a yakin
# olmamak icin. UID 10001 reserved-range.
#
# HER NAMED VOLUME HEDEFI BURADA YARATILIP `chown` EDILMELIDIR — aksi halde
# ozellik SESSIZCE calismaz. Docker, imajda BULUNMAYAN bir volume hedefini
# `root:root` sahipli yaratir; uid 10001 oraya yazamaz.
#
# YASANAN ARIZA (2026-08-05): harita ozelligi eklenirken compose'a
# `map-tile-data` volume'u eklendi ama bu satira EKLENMEDI. Sonuc: cevrimdisi
# harita indirmesi 577 karonun 577'sini basariyla indirdi, hicbirini
# yazamadi ve arayuz "Hicbir karo indirilemedi (internet yok olabilir)" dedi
# — yani hata agi suclu gosterdi, gercek sebep `PermissionError` idi.
# Teshis zor: `fetch_tile` calisiyor, DNS calisiyor, `online: True`.
#
# Yeni bir volume eklerken: docker-compose.yml + BU SATIR birlikte guncellenir.
RUN groupadd -r e1 --gid=10001 \
 && useradd -r -g e1 --uid=10001 --home-dir=/app --shell=/sbin/nologin e1 \
 && mkdir -p /var/lib/e1-backups /var/lib/e1-license /var/lib/e1-map-tiles \
 && chown -R e1:e1 /app /var/lib/e1-backups /var/lib/e1-license /var/lib/e1-map-tiles
USER e1

EXPOSE 8000

# Worker sayisi VARSAYILAN 1 — bilincli.
#
# Eskiden tek worker ZORUNLUYDU: alarm motoru, IEC 104 sunuculari ve telemetri
# tuketicisi tek surecte paylasilan singleton'lara dayaniyordu; ikinci bir
# worker onlari ikinci kez calistirirdi (yedek iki kez, bildirim iki kez, IEC
# 104 portu cakisir).
#
# Bu kisit KALKTI: arka plan isleri artik Postgres advisory lock ile tek
# surecte kosuyor (app/core/service_role.py) ve WS yayini NATS uzerinden
# surecler arasi tasiniyor. Yani UVICORN_WORKERS>1 guvenli.
#
# Yine de varsayilan 1: coklu worker her worker basina ayri bir Python sureci
# (~150-250MB) ve ayri DB pool'u demek. Saha cihazlari (mini PC) sinirli
# bellekle calisiyor; buyutme kararini kurulum vermeli, imaj degil.
#
# `--proxy-headers`: X-Forwarded-* parse et.
# `--forwarded-allow-ips=*`: TRUSTED proxy IP listesi. Default "127.0.0.1"
# (uvicorn) container icinde frontend-web nginx baska IP'den geldigi icin
# tanimaz; "*" kabul edilir cunku 8000 portu compose internal network'unde
# (host'a expose edilmemis), saldirgan zaten direkt erisemez. Production'da
# sadece nginx container'i 8000'e ulasabilir.
#
# NOT: migration YALNIZCA burada, uvicorn baslamadan ONCE ve TEK kez kosar.
# Coklu worker'da bile uvicorn kendi worker'larini bu komuttan SONRA fork
# eder, dolayisiyla migration yarisi olusmaz.
ENV UVICORN_WORKERS=1
CMD ["sh", "-c", "python -m scripts.migrate_db && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-1} --proxy-headers --forwarded-allow-ips=*"]
