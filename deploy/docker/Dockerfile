# Delta India paper-trading bot.
#
# The image contains no credentials and no order-placement capability. It runs
# as a non-root user with a read-only root filesystem: nothing in normal
# operation writes to disk, because all state lives in PostgreSQL.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build tools are needed for numba/llvmlite wheels on some platforms and are
# dropped from the final image.
FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY deltabt ./deltabt
COPY app ./app
RUN pip install --prefix=/install ".[live]"

FROM base
COPY --from=builder /install /usr/local
COPY deltabt ./deltabt
COPY app ./app
COPY pyproject.toml ./

# numba caches compiled kernels; give it a writable tmpfs path so the root
# filesystem can stay read-only.
ENV NUMBA_CACHE_DIR=/tmp/numba DELTABT_DATA=/tmp/deltabt-data

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin bot
USER 10001

EXPOSE 8000

# Health is data freshness, not process liveness -- see app/monitoring/health.py.
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD python -c "import sys,urllib.request; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=4).status==200 else 1)"

ENTRYPOINT ["python", "-m", "app"]
