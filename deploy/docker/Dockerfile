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

# The container has no git, and preflight FAILS on an unknown SHA -- a result
# that cannot be tied to code is not reproducible. So the SHA is baked in at
# build time and read from the environment by app/forwardtest/identity.py.
ARG GIT_SHA=unknown
ARG GIT_DIRTY=1
ENV DELTABOT_GIT_SHA=${GIT_SHA} \
    DELTABOT_GIT_DIRTY=${GIT_DIRTY} \
    TZ=UTC

COPY --from=builder /install /usr/local
COPY deltabt ./deltabt
COPY app ./app
COPY pyproject.toml ./

# numba caches compiled kernels; give it a writable tmpfs path so the root
# filesystem can stay read-only.
ENV NUMBA_CACHE_DIR=/tmp/numba DELTABT_DATA=/tmp/deltabt-data

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin bot \
    && mkdir -p /tmp/numba && chown -R 10001:10001 /tmp/numba
USER 10001

# Compile the numba kernels AT BUILD TIME. Cold, the first strategy evaluation
# blocks the event loop for 3.0s (warm: 1.3ms), and it would otherwise happen
# on the first live 5m close -- after the bot has declared itself ready --
# silently delaying fills and queueing ticks on every container start.
RUN python -c "\
import numpy as np, pandas as pd;\
from app.strategy.rules import evaluate;\
from app.config.strategy import FROZEN;\
n=800; c=63000*np.exp(np.cumsum(np.random.default_rng(0).standard_normal(n)*1e-3));\
df=pd.DataFrame({'time':np.arange(n)*300,'open':c,'high':c*1.001,'low':c*0.999,'close':c,'volume':np.ones(n)});\
evaluate(df,df,FROZEN,symbol='WARMUP');\
print('numba kernels compiled')"


EXPOSE 8000

# Health is data freshness, not process liveness -- see app/monitoring/health.py.
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD python -c "import sys,urllib.request; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=4).status==200 else 1)"

ENTRYPOINT ["python", "-m", "app"]
