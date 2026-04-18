FROM python:3.11-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim AS runtime

WORKDIR /app

# Create non-root user
RUN useradd -m -u 1000 botuser

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY . .

# Create data/log directories
RUN mkdir -p data logs && chown -R botuser:botuser /app

USER botuser

# Default: backtest mode
ENTRYPOINT ["python", "main.py"]
CMD ["--mode", "backtest"]
