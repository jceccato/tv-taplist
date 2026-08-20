#!/usr/bin/env bash
# End-to-end Docker test for the TV Tap List appliance.
#
# Builds the image, runs the container in DEMO_MODE on a fresh MAPPED HOST
# DIRECTORY - the way an install is meant to run, never a named volume - waits
# for the healthcheck, and asserts the running behaviour:
#   - /healthz returns ok
#   - /api/board serves the seeded demo taps
#   - the display HTML has zero external-origin references
#   - /admin redirects unauthenticated users to the login page
#   - the app process runs as NON-ROOT and /data is writable (PUID/PGID path)
#   - Docker reports the container "healthy"
#   - a mapped data directory IS a mount, and an unmapped one is NOT
#
# Usage (from WSL Ubuntu, with Docker installed and your user in the docker group):
#   bash scripts/docker_test.sh
#
# Override the source dir if the repo lives elsewhere on the Windows mount:
#   SRC=/mnt/c/temp/TVTapList bash scripts/docker_test.sh
set -euo pipefail

SRC="${SRC:-$(cd "$(dirname "$0")/.." && pwd)}"
IMAGE="tv-taplist:test"
NAME="tv-taplist-test"
UNMAPPED_NAME="tv-taplist-test-unmapped"
DATA_HOST_DIR="$(mktemp -d)"
HOST_PORT="${HOST_PORT:-18080}"
BASE="http://127.0.0.1:${HOST_PORT}"

pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILED=1; }
FAILED=0

cleanup() {
  echo "== cleanup =="
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker rm -f "$UNMAPPED_NAME" >/dev/null 2>&1 || true
  rm -rf "$DATA_HOST_DIR"
}
trap cleanup EXIT

echo "== preparing a clean WSL-native build context =="
# Building from a native ext4 path avoids /mnt/c perf and any line-ending issues.
BUILD_DIR="$(mktemp -d)"
# Copy only what the image needs (mirrors .dockerignore intent).
for item in app static templates Dockerfile entrypoint.sh requirements.txt .dockerignore; do
  cp -r "$SRC/$item" "$BUILD_DIR/"
done

echo "== docker build =="
docker build -t "$IMAGE" "$BUILD_DIR"
rm -rf "$BUILD_DIR"

echo "== run container (DEMO_MODE, fresh mapped host directory) =="
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" \
  -e ADMIN_PASSWORD=dockertest \
  -e SESSION_SECRET=dockertestsecret \
  -e TZ=Australia/Sydney \
  -e DEMO_MODE=true \
  -e FORWARDED_ALLOW_IPS=127.0.0.1 \
  -p "${HOST_PORT}:8080" \
  -v "${DATA_HOST_DIR}:/data" \
  "$IMAGE" >/dev/null

echo "== wait for healthy =="
for i in $(seq 1 30); do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$NAME" 2>/dev/null || echo starting)"
  [ "$status" = "healthy" ] && break
  sleep 1
done
echo "  health status: ${status:-unknown}"

echo "== assertions =="
# 1. health
if curl -fsS "$BASE/healthz" | grep -q '"status":"ok"'; then pass "/healthz ok"; else fail "/healthz"; fi

# 2. board serves demo taps
board="$(curl -fsS "$BASE/api/board")"
if echo "$board" | grep -q '"num_taps": *[1-9]'; then pass "/api/board has taps"; else fail "/api/board taps"; fi
if echo "$board" | grep -q 'West Coast IPA'; then pass "/api/board demo data present"; else fail "/api/board demo data"; fi

# 3. zero external origins in the display HTML
html="$(curl -fsS "$BASE/")"
if echo "$html" | grep -Eq 'https?://'; then fail "display HTML has external origins"; else pass "display HTML has zero external origins"; fi

# 4. admin redirects to login
code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/admin")"
loc="$(curl -s -o /dev/null -D - "$BASE/admin" | tr -d '\r' | awk 'tolower($1)=="location:"{print $2}')"
if [ "$code" = "303" ] && [ "$loc" = "/admin/login" ]; then pass "/admin -> login redirect"; else fail "/admin redirect (code=$code loc=$loc)"; fi

# 5. runs as non-root + /data writable (PUID/PGID handling)
# Check the owner of PID 1 (the real app process). `docker exec` defaults to the
# image user (root), so it can't be used to prove the app dropped privileges.
uid="$(docker exec "$NAME" stat -c '%u' /proc/1)"
user="$(docker exec "$NAME" stat -c '%U' /proc/1)"
if [ "$uid" != "0" ]; then pass "app process (PID 1) is non-root (uid=$uid user=$user)"; else fail "PID 1 is root"; fi
if docker exec "$NAME" sh -c 'test -w /data && test -f /data/config.json'; then pass "/data writable + config.json created"; else fail "/data not writable"; fi

# 6. the data directory is a mount when mapped, and is not when it isn't.
# The image declares no VOLUME, which is what makes "no mountinfo entry" mean
# exactly one thing: nobody mapped anything here. app/persistence.py keys its
# "not mapped" warning on that, so this is the container-level proof of the
# boolean, alongside the unit-level Dockerfile guard in tests/test_persistence.py.
if docker exec "$NAME" sh -c 'grep -q " /data " /proc/self/mountinfo'; then
  pass "mapped data directory is a mount"
else
  fail "mapped data directory is not a mount"
fi
if docker run --rm --entrypoint sh "$IMAGE" -c 'grep -q " /data " /proc/self/mountinfo'; then
  fail "unmapped data directory is a mount - has a VOLUME directive come back?"
else
  pass "unmapped data directory is NOT a mount"
fi

# 7. the unmapped case warns at startup, and a mapped one stays silent.
# Not DEMO_MODE here: a demo box is deliberately allowed to be disposable and
# logs the same finding at INFO, which would not prove anything.
docker rm -f "$UNMAPPED_NAME" >/dev/null 2>&1 || true
docker run -d --name "$UNMAPPED_NAME" \
  -e ADMIN_PASSWORD=dockertest -e SESSION_SECRET=dockertestsecret \
  "$IMAGE" >/dev/null
for i in $(seq 1 15); do
  docker logs "$UNMAPPED_NAME" 2>&1 | grep -q "application started" && break
  sleep 1
done
unmapped_log="$(docker logs "$UNMAPPED_NAME" 2>&1)"
docker rm -f "$UNMAPPED_NAME" >/dev/null 2>&1 || true
if echo "$unmapped_log" | grep -q "not a mapped host directory"; then
  pass "unmapped container logs the persistence warning"
else
  fail "unmapped container did not warn"
fi
if docker logs "$NAME" 2>&1 | grep -q "WARNING taplist.persistence"; then
  fail "mapped container logged a persistence warning"
else
  pass "mapped container logs no persistence warning"
fi

# 8. healthcheck status
if [ "${status:-}" = "healthy" ]; then pass "docker healthcheck healthy"; else fail "healthcheck=$status"; fi

echo "== container logs (tail) =="
docker logs --tail 25 "$NAME" 2>&1 | sed 's/^/  | /'

echo
if [ "$FAILED" = "0" ]; then
  echo -e "\033[32mALL DOCKER TESTS PASSED\033[0m"
else
  echo -e "\033[31mSOME DOCKER TESTS FAILED\033[0m"
  exit 1
fi
