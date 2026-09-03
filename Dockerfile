# Build path for servers that have Docker but not nix.
#
# Dependencies come from pyproject.toml, so this does not introduce a second
# dependency list - pip resolves the same names the flake pins.
#
#     docker compose up -d --build
#
# The nix path (`nix build .#dockerImage` + `docker load`) produces a pinned,
# reproducible image and is preferred where nix is available; this one trades
# reproducibility for the ability to build anywhere.

FROM python:3.13-slim AS build

# Wheels exist for every dependency, but argon2-cffi falls back to building
# from source on architectures without one.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml ./
COPY memecal ./memecal

RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir .


FROM python:3.13-slim

# tzdata: der Tageswechsel - und damit das Freischalten der Türchen - muss in
# Europe/Berlin liegen, nicht in UTC.
# ca-certificates: für die HTTPS-Requests an YouTube.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --create-home --uid 10001 memecal

COPY --from=build /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Berlin \
    MEMECAL_DATA_DIR=/data

# Das Volume ist das Einzige, was hier nicht neu gebaut werden kann.
RUN mkdir -p /data && chown memecal:memecal /data
VOLUME ["/data"]

USER memecal
WORKDIR /data
EXPOSE 8000

ENTRYPOINT ["memecal"]
CMD ["--host", "0.0.0.0", "--port", "8000"]
