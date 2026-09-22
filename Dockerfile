# syntax=docker/dockerfile:1
# alobo-bot: read-only cheapest-pickleball-court finder + report viewer.
# Build: docker compose build      Run: docker compose up -d
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ALOBO_BOT_HOME=/app

WORKDIR /app

# Install the package first (layers well): metadata + src only, no data/.
COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ".[server]"

# Runtime config; keep it a thin layer so overriding it is a cheap bind mount.
COPY config.yaml ./

# Non-root runtime user. data/ is runtime state (reports, raw snapshots).
RUN mkdir -p /app/data && useradd --system --uid 10002 alobobot \
    && chown -R alobobot:alobobot /app

USER alobobot

# `alobo-bot serve` = report viewer + on-demand POST /find.
EXPOSE 8084
ENTRYPOINT ["alobo-bot"]
CMD ["serve"]
