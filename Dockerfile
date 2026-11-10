FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd --system --uid 1000 appuser \
    && mkdir -p /data && chown appuser /data
USER appuser
VOLUME ["/data"]
EXPOSE 8000

# Порт 8000 доступен только Caddy внутри сети docker, поэтому заголовкам прокси можно доверять.
CMD ["uvicorn", "--factory", "app.main:build_app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
