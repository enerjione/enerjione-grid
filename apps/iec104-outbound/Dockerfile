FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY iec104_outbound ./iec104_outbound

# Health endpoint (default 8014); IEC 104 server portlari calisma aninda
# DB'deki outbound_targets kayitlarindan dinamik olarak baglanir.
EXPOSE 8014

CMD ["python", "-m", "iec104_outbound"]
