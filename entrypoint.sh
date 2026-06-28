#!/bin/sh
# Entrypoint runs as root only to reconcile volume ownership, then drops to the
# non-root app user via gosu. This is the PUID/PGID pattern: a host-mounted
# /data is often owned by root or by an arbitrary host uid, which would break
# writes for a non-root process. We retag the app user to the requested
# PUID/PGID and chown /data so the app can write.
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
DATA_DIR="${DATA_DIR:-/data}"
PORT="${PORT:-8080}"
# Only trust forwarded headers from the known reverse proxy IP(s).
FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-127.0.0.1}"

# Align the app group/user ids with the host volume owner if asked.
if [ "$(id -g appuser)" != "$PGID" ]; then
  groupmod -o -g "$PGID" appgroup
fi
if [ "$(id -u appuser)" != "$PUID" ]; then
  usermod -o -u "$PUID" appuser
fi

mkdir -p "$DATA_DIR/taps" "$DATA_DIR/old_beers"
# Best-effort: don't fail boot if a read-only or already-correct mount rejects chown.
chown -R appuser:appgroup "$DATA_DIR" 2>/dev/null || true

echo "[entrypoint] starting uvicorn as appuser ($PUID:$PGID), data=$DATA_DIR, port=$PORT, trusted_proxies=$FORWARDED_ALLOW_IPS"

# --proxy-headers + --forwarded-allow-ips makes Uvicorn honour X-Forwarded-For
# and X-Forwarded-Proto, but ONLY from the trusted proxy IP(s).
exec gosu appuser:appgroup uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --proxy-headers \
  --forwarded-allow-ips "$FORWARDED_ALLOW_IPS"
