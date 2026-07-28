FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY modbus_outbound ./modbus_outbound

# Non-root user — UID 10001 (e1).
# Modbus standart portu 502'dir ve <1024'tur; non-root bind edebilmesi icin
# compose'da `net.ipv4.ip_unprivileged_port_start=0` sysctl'i veriliyor
# (container namespace'ine ozel, host politikasini degistirmez).
RUN groupadd -r e1 --gid=10001 \
 && useradd -r -g e1 --uid=10001 --home-dir=/app --shell=/sbin/nologin e1 \
 && chown -R e1:e1 /app
USER e1

# Health endpoint (8017); Modbus dinleme portlari calisma aninda
# outbound_targets kayitlarindan dinamik baglanir.
EXPOSE 8017

HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8017/health',timeout=3).status==200 else sys.exit(1)" || exit 1

CMD ["python", "-m", "modbus_outbound"]
