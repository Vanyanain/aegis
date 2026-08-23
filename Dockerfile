# ---- stage 1: build the console ----
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- stage 2: serve ----
FROM python:3.12-slim

# Tesseract backs the OCR step in Side B. Without it the arithmetic and typography layers
# silently return zeros and every uploaded document scores as authentic -- a failure that
# looks like success, so it is installed rather than made optional.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr libtesseract-dev libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rules/ ./rules/
COPY aegis/ ./aegis/
COPY synth/ ./synth/
COPY models/ ./models/
COPY docs/ ./docs/
COPY data/*.parquet ./data/
COPY data/evidence_demo/ ./data/evidence/
COPY --from=web /web/dist ./web/dist

ENV PYTHONUNBUFFERED=1 PORT=8080
EXPOSE 8080

# Single worker: the store holds the ledger and every scored case in memory, so additional
# workers would duplicate several hundred megabytes for no throughput gain on this workload.
CMD exec uvicorn aegis.api.main:app --host 0.0.0.0 --port ${PORT} --workers 1
