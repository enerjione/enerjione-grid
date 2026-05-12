FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY tag_engine ./tag_engine

# Non-root user — UID 10001 (e1).
RUN groupadd -r e1 --gid=10001 \
 && useradd -r -g e1 --uid=10001 --home-dir=/app --shell=/sbin/nologin e1 \
 && chown -R e1:e1 /app
USER e1

EXPOSE 8011

# Health probe — worker /health endpoint'inde status:ok doner.
# wget alpine'da yok; busybox wget veya curl ekledigimizi varsayma; basit
# python urllib check ile.
HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8011/health',timeout=3).status==200 else sys.exit(1)" || exit 1

CMD ["python", "-m", "tag_engine.main"]
