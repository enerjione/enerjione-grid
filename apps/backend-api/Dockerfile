# Backend API — FastAPI + SQLAlchemy + RabbitMQ consumer
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Sistem paketleri: psycopg2 build için libpq, healthcheck için curl.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts

EXPOSE 8000

# Uvicorn workers tek tutuluyor — alarm engine, IEC 104 server'lari ve telemetry
# consumer thread'leri tek process state'inde paylasilan singleton'lara dayaniyor.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
