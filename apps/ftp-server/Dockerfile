FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY ftp_server ./ftp_server

# NOT: FTP kontrol portu 21 (<1024) ayricalikli — root bind gerekir. Container
# izole (tek gorev, writable volume). Non-root yapmak icin port>1024 + host map
# gerekir; deneme surumunde root + izole container tercih edildi.
EXPOSE 21 8015

HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8015/health',timeout=3).status==200 else sys.exit(1)" || exit 1

CMD ["python", "-m", "ftp_server"]
