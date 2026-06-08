# V13 Universe Statistical Context Engine — backend image.
# Builds the yearline_universe package and runs the daily universe scan.
# Frontend is out of scope; this image is a batch/CLI backend that emits JSON.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install the package first (better layer caching). Core deps only by default;
# add ".[live]" for live yfinance/Yahoo pulls.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .
# For live data instead of the bundled cache, build with:
#   RUN pip install ".[live]"

# App content.
COPY config ./config
COPY scripts ./scripts
COPY data ./data

# Persist generated artifacts and (optionally) the price cache via volumes.
VOLUME ["/app/exports", "/app/data/price_cache"]

# Default: run the mega-cap universe from the bundled cache and write exports.
# Override args at `docker run` time (e.g. a different config, or --provider auto).
ENTRYPOINT ["python", "scripts/run_universe_mvp.py"]
CMD ["config/universe_mega_cap_ai_infra.yaml", "--provider", "cache"]
