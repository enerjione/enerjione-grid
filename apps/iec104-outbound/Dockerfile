FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY iec104_outbound ./iec104_outbound

# Non-root user — UID 10001 (e1).
# NOT: iec104 standardi 2404 portunda dinler — >1024 oldugu icin root
# yetkisi gerekmiyor; non-root da bagliayabilir.
RUN groupadd -r e1 --gid=10001 \
 && useradd -r -g e1 --uid=10001 --home-dir=/app --shell=/sbin/nologin e1 \
 && chown -R e1:e1 /app
USER e1

# Health endpoint (default 8014); IEC 104 server portlari calisma aninda
# DB'deki outbound_targets kayitlarindan dinamik olarak baglanir.
EXPOSE 8014

HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8014/health',timeout=3).status==200 else sys.exit(1)" || exit 1

CMD ["python", "-m", "iec104_outbound"]
