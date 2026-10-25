# Small appliance image: no front-end build toolchain, runs as non-root.

# HEIC support (ADR-0009). pillow-heif is compiled here against Debian's own
# libheif, never installed from a PyPI wheel: the wheels bundle x265, a GPLv2-only
# HEVC encoder, which does not combine with this repository's AGPL-3.0. Only the
# compiled wheel leaves this stage; the compiler and headers do not.
FROM python:3.12-slim AS heif-build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential pkgconf libheif-dev \
 && rm -rf /var/lib/apt/lists/*
COPY requirements-heif.txt .
RUN pip wheel --no-cache-dir --no-deps --no-binary pillow-heif -w /wheels -r requirements-heif.txt

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
#   libheif1 -> HEIC photos. Debian packages each codec as its own plugin:
#            libheif1 depends on the libde265 decoder (plus the AV1 decoders) and
#            only RECOMMENDS the x265 encoder, so --no-install-recommends keeps
#            x265 out. The check below fails the build if it ever slips in.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata gosu curl libheif1 \
 && rm -rf /var/lib/apt/lists/* \
 && if dpkg-query -W -f '${Package}\n' | grep -i x265 \
       || find / -xdev -iname '*x265*' -print 2>/dev/null | grep . ; then \
      echo "x265 found in the image; HEIC must be decoder-only (ADR-0009)" >&2; exit 1; \
    fi

# Non-root app user. uid/gid are adjustable at runtime via PUID/PGID so a
# host-mounted volume stays writable (see entrypoint.sh).
RUN groupadd -g 1000 appgroup \
 && useradd -u 1000 -g appgroup -d /app -s /usr/sbin/nologin appuser

WORKDIR /app

# Install Python deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=heif-build /wheels /tmp/wheels
RUN pip install --no-cache-dir --no-deps /tmp/wheels/*.whl \
 && rm -rf /tmp/wheels \
 && python -c "import pillow_heif as h; i = h.libheif_info(); \
assert 'libde265' in i['decoders'], i; assert 'x265' not in i['encoders'], i"

# Application code and bundled (committed) assets - no Node/Tailwind/PostCSS.
COPY app ./app
COPY static ./static
COPY templates ./templates
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Container-local state, outside the data directory: the appliance's memory of
# which data directory it last saw (see app/persistence.py). Chowned here for
# the default user and again in entrypoint.sh once PUID/PGID are known.
# NOT under /tmp, which some hardened setups mount as tmpfs.
RUN mkdir -p /var/lib/taplist && chown appuser:appgroup /var/lib/taplist

# NO `VOLUME ["/data"]` here, deliberately - do not add one back.
# With the directive, an unmapped data directory silently receives an anonymous
# volume: writable, accepting every write, surviving nothing, and impossible to
# tell apart from a named volume at runtime (both ext4, same device). Without
# it, an unmapped data directory has no mountinfo entry at all, which is what
# lets the app detect the misconfiguration and warn the operator instead of
# quietly losing their manual beers. tests/test_persistence.py fails if this
# comes back.
EXPOSE 8080
LABEL tv-taplist-version=${TVTAPLIST_VERSION}

# Hit the lightweight health endpoint. Shell form so ${PORT} expands at runtime.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

# Entrypoint starts as root to fix /data ownership, then exec's uvicorn as appuser.
ENTRYPOINT ["/entrypoint.sh"]
