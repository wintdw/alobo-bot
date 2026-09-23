# syntax=docker/dockerfile:1
# alobo-bot: read-only cheapest-pickleball-court finder + report viewer.
# Build: docker compose build      Run: docker compose up -d

# --- Build stage: the project wheel. Needs the metadata, the README it points
#     at and src/; none of that reaches the runtime image.
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip \
    pip wheel --no-deps --wheel-dir /wheel .

# --- Runtime stage.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ALOBO_BOT_HOME=/app

# uid 1000 so it matches the typical host owner of the bind-mounted ./data.
# data/ is runtime state (reports, raw snapshots); only the empty dir is baked in.
RUN useradd --uid 1000 --user-group alobobot \
 && install -d -o alobobot -g alobobot /app/data

WORKDIR /app

# Dependencies, in a layer keyed on pyproject.toml alone (its only source of
# truth, read here so the list cannot drift): editing src/ never re-downloads or
# re-resolves them.
COPY pyproject.toml /tmp/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/pip \
    python -c 'import tomllib; p = tomllib.load(open("/tmp/pyproject.toml", "rb"))["project"]; print("\n".join(p.get("dependencies", []) + p.get("optional-dependencies", {}).get("server", [])))' > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt \
 && rm -f /tmp/requirements.txt /tmp/pyproject.toml

# The package itself, from the built wheel instead of a copy of the source tree:
# the code lives once, in site-packages, and /app stays config + data.
RUN --mount=type=bind,from=build,source=/wheel,target=/wheel \
    pip install --no-deps --no-cache-dir /wheel/alobo_bot-*.whl

# Runtime config; keep it a thin layer so overriding it is a cheap bind mount.
COPY --chown=alobobot:alobobot config.yaml ./

USER alobobot

# `alobo-bot serve` = report viewer + on-demand POST /find.
EXPOSE 8085
ENTRYPOINT ["alobo-bot"]
CMD ["serve"]
