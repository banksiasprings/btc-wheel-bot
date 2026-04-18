# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app
COPY requirements.txt .

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libffi-dev && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt && \
    apt-get purge -y gcc libffi-dev && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

COPY --from=builder /install /usr/local
COPY . .

RUN mkdir -p logs data

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT ["python", "main.py"]
CMD ["--mode=backtest"]
