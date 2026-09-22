# --- Build stage ---
FROM python:3.11-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Runtime stage ---
FROM python:3.11-slim

RUN groupadd -r smartstock && useradd -r -g smartstock -d /app smartstock
WORKDIR /app

COPY --from=builder /install /usr/local
COPY . .

RUN mkdir -p data outputs work && chown -R smartstock:smartstock /app
USER smartstock

ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/api/health')" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
