# Small appliance image: no front-end build toolchain, runs as non-root.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    PORT=8080

# Build-time version tag (git tag or short SHA), made available as an env var
# so the update checker can compare against the latest GitHub release.
ARG VERSION=dev
ENV TVTAPLIST_VERSION=$VERSION

# Runtime-only OS deps:
#   tzdata -> honour the TZ env var for archive timestamps / daily boundary
#   gosu   -> drop privileges from the perms-fixing entrypoint to the app user
#   curl   -> container HEALTHCHECK
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata gosu curl \
 && rm -rf /var/lib/apt/lists/*

# Non-root app user. uid/gid are adjustable at runtime via PUID/PGID so a
# host-mounted volume stays writable (see entrypoint.sh).
RUN groupadd -g 1000 appgroup \
 && useradd -u 1000 -g appgroup -d /app -s /usr/sbin/nologin appuser

WORKDIR /app

# Install Python deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code and bundled (committed) assets - no Node/Tailwind/PostCSS.
COPY app ./app
COPY static ./static
COPY templates ./templates
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

VOLUME ["/data"]
EXPOSE 8080
LABEL tv-taplist-version=${TVTAPLIST_VERSION}

# Hit the lightweight health endpoint. Shell form so ${PORT} expands at runtime.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

# Entrypoint starts as root to fix /data ownership, then exec's uvicorn as appuser.
ENTRYPOINT ["/entrypoint.sh"]
