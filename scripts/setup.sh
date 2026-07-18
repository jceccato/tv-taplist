#!/usr/bin/env bash
# Guided installer for TV Tap List.
#
# Asks a handful of questions, writes a .env you can re-edit later, and (optionally)
# starts the container with Docker Compose. Run it from a checkout of the repo:
#
#   git clone https://github.com/jceccato/tv-taplist.git
#   cd tv-taplist
#   ./scripts/setup.sh
#
# Safe to re-run: it shows your current .env values as defaults and never starts
# anything without asking.
set -euo pipefail

# --- locate the repo root (this script lives in scripts/) --------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"
ENV_FILE="$REPO_ROOT/.env"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
info() { printf '  %s\n' "$1"; }
warn() { printf '\033[33m! %s\033[0m\n' "$1"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$1" >&2; exit 1; }

[ -f "$REPO_ROOT/docker-compose.yml" ] || die "run this from the repo root (docker-compose.yml not found)."

# --- prerequisites -----------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "Docker is not installed. See https://docs.docker.com/engine/install/"
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  die "Docker Compose is not available. Install the Docker Compose plugin."
fi

# --- helpers -----------------------------------------------------------------
# Read an existing value out of .env (so re-runs prefill sensibly).
env_get() { [ -f "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | head -n1 || true; }

ask() { # ask "Prompt" "default" -> echoes the answer
  local prompt="$1" default="${2:-}" reply
  if [ -n "$default" ]; then
    read -r -p "$prompt [$default]: " reply || true
    printf '%s' "${reply:-$default}"
  else
    read -r -p "$prompt: " reply || true
    printf '%s' "$reply"
  fi
}

ask_secret() { # ask_secret "Prompt" -> echoes typed value (hidden)
  local prompt="$1" reply
  read -r -s -p "$prompt: " reply || true
  printf '\n' >&2
  printf '%s' "$reply"
}

yesno() { # yesno "Question" "Y" -> returns 0 for yes
  local prompt="$1" default="${2:-Y}" reply
  read -r -p "$prompt [$([ "$default" = Y ] && echo 'Y/n' || echo 'y/N')]: " reply || true
  reply="${reply:-$default}"
  case "$reply" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

gen_secret() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32
  else head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; fi
}

detect_tz() {
  if [ -f /etc/timezone ]; then cat /etc/timezone
  elif command -v timedatectl >/dev/null 2>&1; then timedatectl show -p Timezone --value 2>/dev/null
  else echo "UTC"; fi
}

bold "TV Tap List - guided installer"
echo
if [ -f "$ENV_FILE" ]; then
  warn "A .env already exists; its values are shown as defaults below."
fi
echo

# --- admin password ----------------------------------------------------------
bold "1) Admin password"
info "Protects the /admin settings page."
ADMIN_PASSWORD="$(ask_secret "  Admin password (leave blank to auto-generate)")"
if [ -z "$ADMIN_PASSWORD" ]; then
  ADMIN_PASSWORD="$(gen_secret | cut -c1-20)"
  info "Generated admin password: $ADMIN_PASSWORD   (save this!)"
fi

# --- session secret (always generated; reuse existing if present) ------------
SESSION_SECRET="$(env_get SESSION_SECRET)"
if [ -z "$SESSION_SECRET" ] || [ "$SESSION_SECRET" = "change-this-long-random-secret" ]; then
  SESSION_SECRET="$(gen_secret)"
fi

echo
bold "2) Basics"
TZ_VAL="$(ask "  Timezone (IANA name)" "$(env_get TZ || true)")"; TZ_VAL="${TZ_VAL:-$(detect_tz)}"
PORT="$(ask "  Host port for the web UI" "$(env_get PORT || echo 8080)")"
DATA_DIR_HOST="$(ask "  Data directory on this host (plain text + images live here)" "$(env_get DATA_DIR_HOST || echo ./taplist_data)")"
PUID="$(ask "  PUID (host user id that should own the data)" "$(env_get PUID || id -u)")"
PGID="$(ask "  PGID (host group id)" "$(env_get PGID || id -g)")"

echo
bold "3) Brewfather (optional - you can also add these later in /admin)"
info "Get them in Brewfather: Settings -> API -> Generate. You need the User ID and"
info "an API key with 'Read Batches' (and 'Read Recipes') scope."
BF_USER=""; BF_KEY=""
if yesno "  Enter Brewfather credentials now?" "N"; then
  BF_USER="$(ask "  Brewfather User ID" "$(env_get BREWFATHER_USER_ID || true)")"
  BF_KEY="$(ask_secret "  Brewfather API Key")"
fi
SYNC_MIN="$(ask "  Minutes between syncs" "$(env_get SYNC_INTERVAL_MINUTES || echo 15)")"

# --- reverse proxy hint ------------------------------------------------------
FORWARDED="$(env_get FORWARDED_ALLOW_IPS || echo 127.0.0.1)"

# --- write .env --------------------------------------------------------------
echo
if [ -f "$ENV_FILE" ] && ! yesno "Overwrite existing .env?" "Y"; then
  die "Aborted - left your .env untouched."
fi

umask 077  # .env holds secrets; keep it owner-only
cat > "$ENV_FILE" <<EOF
# Written by scripts/setup.sh - safe to edit by hand.
ADMIN_PASSWORD=$ADMIN_PASSWORD
SESSION_SECRET=$SESSION_SECRET
TZ=$TZ_VAL
PORT=$PORT
DATA_DIR_HOST=$DATA_DIR_HOST
PUID=$PUID
PGID=$PGID
FORWARDED_ALLOW_IPS=$FORWARDED
DEMO_MODE=false
BREWFATHER_USER_ID=$BF_USER
BREWFATHER_API_KEY=$BF_KEY
SYNC_INTERVAL_MINUTES=$SYNC_MIN
EOF
info "Wrote $ENV_FILE"

mkdir -p "$DATA_DIR_HOST"
info "Data directory ready: $DATA_DIR_HOST"

# --- start it ----------------------------------------------------------------
echo
if yesno "Build and start the container now?" "Y"; then
  bold "Starting…"
  $COMPOSE up -d
  echo
  bold "Done!"
  info "Display: http://localhost:$PORT/"
  info "Admin:   http://localhost:$PORT/admin"
  if [ -z "$BF_USER" ]; then
    info "Add your Brewfather details in /admin, set the tap count, then 'Sync now'."
  fi
else
  bold "Setup complete."
  info "Start it whenever you're ready:  $COMPOSE up -d"
fi
