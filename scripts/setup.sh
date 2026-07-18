#!/usr/bin/env bash
# TV Tap List - guided installer.                  Version: 1.4.0
#
# One-liner (from any directory):
#   bash <(curl -fsSL https://raw.githubusercontent.com/jceccato/tv-taplist/main/scripts/setup.sh)
#
# Or the long way:
#   git clone https://github.com/jceccato/tv-taplist.git
#   cd tv-taplist
#   bash scripts/setup.sh
#
# Check what version you're running:
#   curl -fsSL https://raw.githubusercontent.com/jceccato/tv-taplist/main/scripts/setup.sh | head -2
#
# Safe to re-run: loads your current .env as defaults, never starts anything
# without asking.
set -euo pipefail

# --- handle --version / -v flag ----------------------------------------------
if [ "${1:-}" = "--version" ] || [ "${1:-}" = "-v" ]; then
  echo "TV Tap List setup script v1.4.0"
  echo "Repo: https://github.com/jceccato/tv-taplist"
  exit 0
fi

# URL of the docker-compose.yml (downloaded in remote mode)
COMPOSE_RAW_URL="https://raw.githubusercontent.com/jceccato/tv-taplist/main/docker-compose.yml"

# --- terminal helpers --------------------------------------------------------
bold()   { printf '\033[1m%s\033[0m\n' "$1"; }
dim()    { printf '\033[2m%s\033[0m\n' "$1"; }
info()   { printf '  %s\n' "$1"; }
warn()   { printf '\033[33m  ! %s\033[0m\n' "$1"; }
ok()     { printf '\033[32m  \xE2\x9C\x93 %s\033[0m\n' "$1"; }
die()    { printf '\033[31mERROR: %s\033[0m\n' "$1" >&2; exit 1; }

# Box drawing characters (fall back to ASCII if UTF-8 not available)
if printf '\xe2\x94\x8c' >/dev/null 2>&1; then
  BOX_TL='\xe2\x94\x8c' BOX_TR='\xe2\x94\x90' BOX_BL='\xe2\x94\x94' BOX_BR='\xe2\x94\x98'
  BOX_H='\xe2\x94\x80'   BOX_V='\xe2\x94\x82'
else
  BOX_TL='+' BOX_TR='+' BOX_BL='+' BOX_BR='+'
  BOX_H='-'  BOX_V='|'
fi

BOX_W=55

box_top()    { printf "$BOX_TL"; printf '%*s' "$BOX_W" | tr ' ' "$BOX_H"; printf "$BOX_TR\n"; }
box_line()   { printf "$BOX_V %-*s $BOX_V\n" "$((BOX_W - 2))" "$1"; }
box_mid()    { printf "$BOX_V%*s$BOX_V\n" "$((BOX_W + 1))" | tr ' ' "$BOX_H"; }
box_bottom() { printf "$BOX_BL"; printf '%*s' "$BOX_W" | tr ' ' "$BOX_H"; printf "$BOX_BR\n"; }

# --- detect context: repo checkout vs remote one-liner -----------------------
REPO_ROOT=""
ENV_FILE=""

bootstrap_repo() {
  # Case 1: running from scripts/setup.sh inside a cloned repo
  local bs="${BASH_SOURCE[0]:-}"
  case "$bs" in
    ""|/dev/stdin|/dev/fd/*|/bin/bash|bash) ;;  # piped/remote modes
    *)
      local script_parent
      script_parent="$(cd "$(dirname "$bs")" 2>/dev/null && pwd)" || true
      if [ -n "$script_parent" ] && [ -f "$script_parent/../docker-compose.yml" ]; then
        REPO_ROOT="$(cd "$script_parent/.." && pwd)"
      fi
      ;;
  esac

  # Case 2: running from the repo root (docker-compose.yml in cwd)
  if [ -z "$REPO_ROOT" ] && [ -f "./docker-compose.yml" ]; then
    REPO_ROOT="$(pwd)"
  fi

  # Case 3: remote mode (piped from curl, or no compose file found)
  if [ -z "$REPO_ROOT" ]; then
    echo
    bold "TV Tap List - remote installer"
    echo
    info "No docker-compose.yml found nearby - switching to remote install mode."
    info "I'll download what's needed into a new directory."
    echo
    read -r -p "  Install into directory [./tv-taplist]: " dir || true
    dir="${dir:-./tv-taplist}"
    REPO_ROOT="$(cd "$(dirname "$dir")" 2>/dev/null && pwd)/$(basename "$dir")" || REPO_ROOT="$(pwd)/$(basename "$dir")"
    mkdir -p "$REPO_ROOT"

    info "Downloading docker-compose.yml..."
    if command -v curl >/dev/null 2>&1; then
      curl -fsSL "$COMPOSE_RAW_URL" -o "$REPO_ROOT/docker-compose.yml"
    elif command -v wget >/dev/null 2>&1; then
      wget -q "$COMPOSE_RAW_URL" -O "$REPO_ROOT/docker-compose.yml"
    else
      die "Need curl or wget to download files. Install one and re-run."
    fi
    ok "Ready."
    echo
  fi

  cd "$REPO_ROOT"
  ENV_FILE="$REPO_ROOT/.env"
}

# --- ensure docker & docker compose are available ----------------------------
ensure_compose() {
  # Check Docker
  if ! command -v docker >/dev/null 2>&1; then
    warn "Docker is not installed."
    if [ -f /etc/debian_version ]; then
      info "Detected Debian / Ubuntu / Raspberry Pi OS."
      echo
      if yesno "Install Docker now? (requires sudo)" "Y"; then
        info "Installing Docker..."
        sudo apt-get update -qq && sudo apt-get install -y docker.io
        sudo usermod -aG docker "$USER" 2>/dev/null || true
        ok "Docker installed. You may need to log out and back in for group changes."
      else
        die "Docker is required. See https://docs.docker.com/engine/install/"
      fi
    else
      die "Docker is required. See https://docs.docker.com/engine/install/"
    fi
  fi

  # Check Docker Compose (prefer v2 plugin, fall back to v1 standalone)
  if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
    return
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
    return
  fi

  # Neither available - offer to install
  warn "Docker Compose is not installed."
  if [ -f /etc/debian_version ]; then
    echo
    if yesno "Install Docker Compose now? (requires sudo)" "Y"; then
      info "Updating package lists..."
      sudo apt-get update -qq 2>/dev/null || true
      info "Trying docker-compose-plugin (v2)..."
      if sudo apt-get install -y docker-compose-plugin 2>/dev/null; then
        COMPOSE="docker compose"
        ok "Docker Compose v2 installed."
        return
      fi
      info "Trying docker-compose (v1 apt)..."
      if sudo apt-get install -y docker-compose 2>/dev/null; then
        COMPOSE="docker-compose"
        ok "Docker Compose v1 (apt) installed."
        return
      fi
      info "docker-compose not in apt, trying pip..."
      if command -v pip3 >/dev/null 2>&1 && sudo pip3 install docker-compose 2>/dev/null; then
        COMPOSE="docker-compose"
        ok "Docker Compose installed via pip."
        return
      fi
      die "Could not auto-install Docker Compose.\n       Manual install: sudo apt-get install -y docker-compose\n       Or see: https://docs.docker.com/compose/install/"
    else
      die "Docker Compose is required."
    fi
  else
    die "Docker Compose is required. See https://docs.docker.com/compose/install/"
  fi
}

# --- helpers -----------------------------------------------------------------
env_get() { [ -f "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | head -n1 || true; }

yesno() {
  local prompt="$1" default="${2:-Y}" reply
  read -r -p "$prompt [$([ "$default" = Y ] && echo 'Y/n' || echo 'y/N')]: " reply || true
  reply="${reply:-$default}"
  case "$reply" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

gen_secret_raw() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32
  else head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; fi
}

# --- DinoPass-style memorable password ---------------------------------------
DINO_WORDS=(blue red hot big fat but new old top fox cat dog sun fun sky sea
            map cup key pen hat bat ice ape egg ant bee owl pig cow yak rat
            hen elk cod eel fir oak bay den pod gem lug hub fog dew arc orb
            tip hop jog yam pea rye fig ply vow vex wry zap zen zip wag jig)

gen_pass() {
  local total=${#DINO_WORDS[@]}
  echo "${DINO_WORDS[$((RANDOM % total))]}-${DINO_WORDS[$((RANDOM % total))]}-$((RANDOM % 90 + 10))"
}

# --- timezone detection ------------------------------------------------------
detect_tz_system() {
  if [ -f /etc/timezone ]; then cat /etc/timezone
  elif command -v timedatectl >/dev/null 2>&1; then timedatectl show -p Timezone --value 2>/dev/null
  else echo ""; fi
}

detect_tz_ip() {
  local tz
  for url in "https://ipapi.co/timezone" "http://ip-api.com/line?fields=timezone"; do
    tz="$(curl -s --connect-timeout 3 --max-time 5 "$url" 2>/dev/null || true)"
    if [ -n "$tz" ] && echo "$tz" | grep -qE '^[A-Z][a-z]+/[A-Z][a-z]+'; then
      echo "$tz"; return
    fi
  done
  echo ""
}

COMMON_TZ=(
  "Africa/Cairo"        "Africa/Johannesburg"  "Africa/Lagos"
  "America/Anchorage"   "America/Buenos_Aires" "America/Chicago"
  "America/Denver"      "America/Halifax"      "America/Los_Angeles"
  "America/Mexico_City" "America/New_York"     "America/Phoenix"
  "America/Santiago"    "America/Sao_Paulo"    "America/Toronto"
  "America/Vancouver"   "America/Winnipeg"
  "Asia/Dubai"          "Asia/Hong_Kong"       "Asia/Kolkata"
  "Asia/Shanghai"       "Asia/Singapore"       "Asia/Tokyo"
  "Australia/Adelaide"  "Australia/Brisbane"   "Australia/Melbourne"
  "Australia/Perth"     "Australia/Sydney"
  "Europe/Amsterdam"    "Europe/Athens"        "Europe/Berlin"
  "Europe/Helsinki"     "Europe/Lisbon"        "Europe/London"
  "Europe/Madrid"       "Europe/Paris"         "Europe/Rome"
  "Europe/Stockholm"
  "Pacific/Auckland"    "Pacific/Fiji"         "Pacific/Honolulu"
  "UTC"
)

# --- current values (mutable globals) ----------------------------------------
ADMIN_PASSWORD=""
SESSION_SECRET=""
TZ_VAL=""
PORT_VAL=""
DATA_DIR=""
PUID_VAL=""
PGID_VAL=""
BF_USER=""
BF_KEY=""
SYNC_MIN=""
FORWARDED=""

# Load existing .env if present
load_env() {
  ADMIN_PASSWORD="$(env_get ADMIN_PASSWORD || true)"
  SESSION_SECRET="$(env_get SESSION_SECRET || true)"
  TZ_VAL="$(env_get TZ || true)"
  PORT_VAL="$(env_get PORT || true)"
  DATA_DIR="$(env_get DATA_DIR_HOST || true)"
  PUID_VAL="$(env_get PUID || true)"
  PGID_VAL="$(env_get PGID || true)"
  BF_USER="$(env_get BREWFATHER_USER_ID || true)"
  BF_KEY="$(env_get BREWFATHER_API_KEY || true)"
  SYNC_MIN="$(env_get SYNC_INTERVAL_MINUTES || true)"
  FORWARDED="$(env_get FORWARDED_ALLOW_IPS || true)"
}

# Apply sane defaults for anything still empty
apply_defaults() {
  [ -n "$SESSION_SECRET" ] && [ "$SESSION_SECRET" != "change-this-long-random-secret" ] || SESSION_SECRET="$(gen_secret_raw)"
  [ -n "$TZ_VAL" ] || TZ_VAL="$(detect_tz_ip || detect_tz_system || echo UTC)"
  [ -n "$PORT_VAL" ] || PORT_VAL="8080"
  [ -n "$DATA_DIR" ] || DATA_DIR="$(pwd)/taplist_data"
  [ -n "$PUID_VAL" ] || PUID_VAL="$(id -u)"
  [ -n "$PGID_VAL" ] || PGID_VAL="$(id -g)"
  [ -n "$SYNC_MIN" ] || SYNC_MIN="15"
  [ -n "$FORWARDED" ] || FORWARDED="127.0.0.1"
}

# --- display helpers for the menu --------------------------------------------
masked() { # "secret" -> "••••••••"
  if [ -z "$1" ]; then printf '(not set)'; else printf '\342\200\242\342\200\242\342\200\242\342\200\242\342\200\242\342\200\242\342\200\242\342\200\242'; fi
}

status_line() { # label value [extra]
  printf "  %-18s \033[36m%s\033[0m %s\n" "$1" "$2" "${3:-}"
}

# Fixed-width menu row (no color codes - safe for printf %-*s padding)
menu_row() {
  printf "$BOX_V %-18s %-34s $BOX_V\n" "$1" "$2"
}

# Guided-mode step tracker (set by guided_setup, empty = standalone mode)
GUIDED_STEP=""
TOTAL_STEPS=6

section_header() { # title description
  local title="$1" desc="$2"
  clear 2>/dev/null || true
  box_top
  if [ -n "$GUIDED_STEP" ]; then
    box_line "$(bold "Step $GUIDED_STEP/$TOTAL_STEPS — $title")"
  else
    box_line "$(bold "$title")"
  fi
  box_line "$(dim "$desc")"
  box_bottom
}

# --- section: Admin Password ------------------------------------------------
section_password() {
  section_header "Admin Password" "Protects the /admin settings page."
  echo
  if [ -n "$ADMIN_PASSWORD" ] && [ "$ADMIN_PASSWORD" != "changeme" ]; then
    info "Current password is set $(masked "$ADMIN_PASSWORD")"
    echo
  fi

  while true; do
    echo "  1) Generate a memorable password (recommended)"
    echo "  2) Type my own"
    echo "  3) Back"
    echo
    read -r -p "  Choose [1]: " choice || true
    choice="${choice:-1}"

    case "$choice" in
      1)
        local pw="$(gen_pass)"
        printf '\n'
        printf '  \033[1;33m%s\033[0m\n' "$pw"
        echo
        if yesno "  Keep this password?" "Y"; then
          ADMIN_PASSWORD="$pw"; ok "Password set."; sleep 1; return
        fi
        echo
        ;;
      2)
        echo
        read -r -p "  Enter password: " pw || true
        if [ -n "$pw" ]; then
          printf '  \033[1;33m%s\033[0m\n' "$pw"
          if yesno "  Keep this password?" "Y"; then
            ADMIN_PASSWORD="$pw"; ok "Password set."; sleep 1; return
          fi
        else
          warn "Password cannot be empty."
          sleep 1
        fi
        ;;
      3) return ;;
      *) warn "Pick 1-3."; sleep 1 ;;
    esac
  done
}

# --- section: Timezone ------------------------------------------------------
section_timezone() {
  section_header "Timezone" "Used for archive timestamps and daily cleanup."
  echo

  local detected_ip="$(detect_tz_ip)"
  local detected_sys="$(detect_tz_system)"
  local guess=""
  [ -n "$detected_ip" ] && guess="$detected_ip"
  [ -z "$guess" ] && [ -n "$detected_sys" ] && guess="$detected_sys"

  status_line "Detected:" "${guess:-unknown}"
  status_line "Current:" "${TZ_VAL:-not set}"
  echo

  # Build menu with detected first, then alphabetical
  local -a items=()
  [ -n "$guess" ] && items+=("$guess  (detected)")
  for tz in "${COMMON_TZ[@]}"; do
    [ "$tz" != "$guess" ] && items+=("$tz")
  done

  local per_col=$(( (${#items[@]} + 1) / 2 ))
  for ((i = 0; i < per_col; i++)); do
    local left="" right=""
    [ "$i" -lt "${#items[@]}" ] && left="$(printf '%2d) %-32s' "$((i + 1))" "${items[$i]}")"
    local ri=$((i + per_col))
    [ "$ri" -lt "${#items[@]}" ] && right="$(printf '%2d) %-32s' "$((ri + 1))" "${items[$ri]}")"
    printf '  %s%s\n' "$left" "$right"
  done

  echo
  while true; do
    local def=""
    # Default to detected if it's in the list
    if [ -n "$guess" ]; then def="1"; fi
    read -r -p "  Number, or type a timezone [${def:-}]: " choice || true
    choice="${choice:-$def}"
    if [ -z "$choice" ]; then
      continue
    elif echo "$choice" | grep -qE '^[0-9]+$'; then
      local idx=$((choice - 1))
      if [ "$idx" -ge 0 ] && [ "$idx" -lt "${#items[@]}" ]; then
        # Strip " (detected)" suffix if present
        TZ_VAL="${items[$idx]%  (detected)}"
        ok "Timezone: $TZ_VAL"; sleep 1; return
      fi
      warn "Invalid number."; sleep 1
    elif echo "$choice" | grep -qE '^[A-Z][a-z]+/[A-Z][a-z]'; then
      TZ_VAL="$choice"
      ok "Timezone: $TZ_VAL"; sleep 1; return
    else
      warn "Enter a number or IANA name like Europe/London."; sleep 1
    fi
  done
}

# --- section: Host Port ------------------------------------------------------
section_port() {
  section_header "Host Port" "The port your browser connects to (mapped to container:8080)."
  echo
  status_line "Current:" "${PORT_VAL:-8080}"
  echo
  read -r -p "  Enter port [${PORT_VAL:-8080}]: " reply || true
  reply="${reply:-${PORT_VAL:-8080}}"
  if echo "$reply" | grep -qE '^[0-9]+$' && [ "$reply" -ge 1 ] && [ "$reply" -le 65535 ]; then
    PORT_VAL="$reply"
    ok "Port: $PORT_VAL"; sleep 1
  else
    warn "Invalid port. Must be 1-65535."; sleep 1
  fi
}

# --- section: Data Directory ------------------------------------------------
section_datadir() {
  section_header "Data Directory" "Where tap data, images, and config.json live on this host."
  echo
  status_line "You are here:" "$(pwd)"
  status_line "Current:" "${DATA_DIR:-not set}"
  echo

  while true; do
    echo "  1) Use default      ($(pwd)/taplist_data)"
    echo "  2) Create new       (enter a name, created here)"
    echo "  3) Browse existing  (navigate to a directory)"
    echo "  4) Type full path"
    echo "  5) Back"
    echo
    read -r -p "  Choose [1]: " choice || true
    choice="${choice:-1}"

    case "$choice" in
      1) DATA_DIR="$(pwd)/taplist_data"; mkdir -p "$DATA_DIR"; ok "Using: $DATA_DIR"; sleep 1; return ;;
      2)
        read -r -p "  Directory name: " name || true
        [ -z "$name" ] && name="taplist_data"
        case "$name" in /*) DATA_DIR="$name" ;; *) DATA_DIR="$(pwd)/$name" ;; esac
        mkdir -p "$DATA_DIR"
        ok "Created: $DATA_DIR"; sleep 1; return
        ;;
      3)
        local cur="$(pwd)"
        while true; do
          clear 2>/dev/null || true
          box_top; box_line "$(bold 'Browse Directory')"; box_line "$(dim 'Navigate into subdirs, select with ".", go up with ".."')"; box_bottom
          echo
          status_line "Browsing:" "$cur"
          echo
          ls -1F --color=always "$cur" 2>/dev/null | head -25 || dim "(empty)"
          echo
          read -r -p "  Enter subdir, '.' to select, '..' to go up: " nav || true
          if [ "$nav" = "." ] || [ -z "$nav" ]; then
            DATA_DIR="$cur"
            ok "Selected: $DATA_DIR"; sleep 1; return
          elif [ "$nav" = ".." ]; then
            cur="$(dirname "$cur")"
          elif [ -d "$cur/$nav" ]; then
            cur="$(cd "$cur/$nav" && pwd)"
          else
            warn "Not a directory."; sleep 1
          fi
        done
        ;;
      4)
        read -r -p "  Full path: " DATA_DIR || true
        [ -z "$DATA_DIR" ] && DATA_DIR="$(pwd)/taplist_data"
        mkdir -p "$DATA_DIR"
        ok "Using: $DATA_DIR"; sleep 1; return
        ;;
      5) return ;;
      *) warn "Pick 1-5."; sleep 1 ;;
    esac
  done
}

# --- section: File Ownership ------------------------------------------------
section_ownership() {
  section_header "File Ownership (PUID / PGID)" "User & group that own the data files on the host."
  echo
  status_line "Current user:" "$(id -un) ($(id -u):$(id -g))"
  status_line "Current PUID:" "${PUID_VAL:-$(id -u)}"
  status_line "Current PGID:" "${PGID_VAL:-$(id -g)}"
  echo
  read -r -p "  PUID [${PUID_VAL:-$(id -u)}]: " puid || true
  PUID_VAL="${puid:-${PUID_VAL:-$(id -u)}}"
  read -r -p "  PGID [${PGID_VAL:-$(id -g)}]: " pgid || true
  PGID_VAL="${pgid:-${PGID_VAL:-$(id -g)}}"
  ok "Ownership: $PUID_VAL:$PGID_VAL"; sleep 1
}

# --- section: Brewfather ----------------------------------------------------
section_brewfather() {
  section_header "Brewfather Integration" "Optional. Can also be added later in the /admin UI."
  echo
  info "Get your credentials: web.brewfather.app > Settings > Integration > Generate API-Key"
  echo
  if [ -n "$BF_USER" ]; then
    status_line "User ID:" "$BF_USER"
    status_line "API Key:" "$(masked "$BF_KEY")"
  else
    status_line "Status:" "not configured"
  fi
  status_line "Sync interval:" "${SYNC_MIN:-15} min"
  echo

  echo "  1) Enter / update Brewfather credentials"
  echo "  2) Remove Brewfather credentials"
  echo "  3) Change sync interval"
  echo "  4) Back"
  echo
  read -r -p "  Choose [4]: " choice || true
  choice="${choice:-4}"

  case "$choice" in
    1)
      echo
      read -r -p "  Brewfather User ID [${BF_USER:-}]: " uid || true
      BF_USER="${uid:-$BF_USER}"
      read -r -s -p "  Brewfather API Key: " key || true; printf '\n' >&2
      [ -n "$key" ] && BF_KEY="$key"
      if [ -n "$BF_USER" ] && [ -n "$BF_KEY" ]; then
        ok "Brewfather configured."; sleep 1
      else
        warn "Both User ID and API Key are required."; sleep 1
      fi
      ;;
    2)
      BF_USER=""; BF_KEY=""
      ok "Brewfather credentials removed."; sleep 1
      ;;
    3)
      read -r -p "  Sync interval (minutes) [${SYNC_MIN:-15}]: " sync || true
      SYNC_MIN="${sync:-${SYNC_MIN:-15}}"
      ok "Sync interval: ${SYNC_MIN} min"; sleep 1
      ;;
    4) return ;;
    *) warn "Pick 1-4."; sleep 1 ;;
  esac
}

# --- section: Review & Deploy -----------------------------------------------
section_review() {
  clear 2>/dev/null || true
  box_top
  if [ -n "$GUIDED_STEP" ]; then
    box_line "$(bold "Step $GUIDED_STEP/$TOTAL_STEPS — Review & Deploy")"
  else
    box_line "$(bold 'Review & Deploy')"
  fi
  box_bottom
  echo
  status_line "Admin password:"    "$(masked "$ADMIN_PASSWORD")"
  status_line "Timezone:"          "$TZ_VAL"
  status_line "Port:"              "$PORT_VAL"
  status_line "Data directory:"    "$DATA_DIR"
  status_line "PUID / PGID:"       "$PUID_VAL / $PGID_VAL"
  if [ -n "$BF_USER" ]; then
    status_line "Brewfather:"      "$BF_USER (sync: ${SYNC_MIN}m)"
  else
    status_line "Brewfather:"      "not configured"
  fi
  echo

  if [ -z "$ADMIN_PASSWORD" ] || [ "$ADMIN_PASSWORD" = "changeme" ]; then
    warn "Admin password is not set. Please set one before deploying."
    echo
    read -r -p "  Press Enter to continue..." _ || true
    return
  fi

  echo "  D) Deploy now  - write .env and start the container"
  echo "  S) Save only    - write .env but don't start"
  echo "  B) Back"
  echo
  read -r -p "  Choose [D]: " choice || true
  choice="${choice:-D}"

  case "$(echo "$choice" | tr '[:lower:]' '[:upper:]')" in
    D|S)
      # Ensure data dir exists
      mkdir -p "$DATA_DIR"

      umask 077
      cat > "$ENV_FILE" <<EOF
# Written by scripts/setup.sh - safe to edit by hand.
ADMIN_PASSWORD=$ADMIN_PASSWORD
SESSION_SECRET=$SESSION_SECRET
TZ=$TZ_VAL
PORT=$PORT_VAL
DATA_DIR_HOST=$DATA_DIR
PUID=$PUID_VAL
PGID=$PGID_VAL
FORWARDED_ALLOW_IPS=$FORWARDED
DEMO_MODE=false
BREWFATHER_USER_ID=$BF_USER
BREWFATHER_API_KEY=$BF_KEY
SYNC_INTERVAL_MINUTES=$SYNC_MIN
EOF

      ok "Wrote $ENV_FILE"

      if [ "$choice" = "D" ] || [ "$choice" = "d" ]; then
        echo
        bold "Starting container..."
        $COMPOSE up -d
        echo
        box_top
        box_line "$(bold '  Ready!')"
        box_mid
        box_line "  Display:  http://localhost:$PORT_VAL/"
        box_line "  Admin:    http://localhost:$PORT_VAL/admin"
        box_bottom
      else
        echo
        box_top
        box_line "$(bold '  .env saved.')"
        box_mid
        box_line "  Start when ready:  $COMPOSE up -d"
        box_bottom
      fi
      exit 0
      ;;
    B) return ;;
    *) warn "Pick D, S, or B."; sleep 1 ;;
  esac
}

# --- guided setup wizard (steps through 1-6, then review) --------------------
guided_setup() {
  local steps=(section_password section_timezone section_port section_datadir section_ownership section_brewfather)

  for i in "${!steps[@]}"; do
    GUIDED_STEP=$((i + 1))
    "${steps[$i]}"
  done
  GUIDED_STEP=""

  # Show a brief "Setup complete" transition before review
  clear 2>/dev/null || true
  box_top
  box_line "$(bold '  Setup complete!')"
  box_mid
  box_line "  All settings have been configured."
  box_line "  Review them before deploying."
  box_bottom
  echo
  read -r -p "  Press Enter to review..." _ || true
  section_review
}

# --- advanced menu (individual section access) --------------------------------
advanced_menu() {
  while true; do
    clear 2>/dev/null || true
    box_top
    box_line "            TV Tap List Setup"
    box_mid
    menu_row "1) Admin password"  "$(masked "$ADMIN_PASSWORD")"
    menu_row "2) Timezone"        "$TZ_VAL"
    menu_row "3) Host port"       "$PORT_VAL"
    menu_row "4) Data directory"  "$DATA_DIR"
    menu_row "5) Ownership"       "$PUID_VAL:$PGID_VAL"
    if [ -n "$BF_USER" ]; then
      menu_row "6) Brewfather"    "$BF_USER"
    else
      menu_row "6) Brewfather"    "(not set)"
    fi
    box_mid
    box_line "  R) Review & Deploy"
    box_line "  B) Back to main menu"
    box_bottom
    echo
    read -r -p "  Choose [1-6, R, B]: " choice || true

    case "$(echo "$choice" | tr '[:lower:]' '[:upper:]')" in
      1) GUIDED_STEP=""; section_password ;;
      2) GUIDED_STEP=""; section_timezone ;;
      3) GUIDED_STEP=""; section_port ;;
      4) GUIDED_STEP=""; section_datadir ;;
      5) GUIDED_STEP=""; section_ownership ;;
      6) GUIDED_STEP=""; section_brewfather ;;
      R) section_review ;;
      B) return ;;
      *) warn "Invalid choice."; sleep 1 ;;
    esac
  done
}

# --- launch screen -----------------------------------------------------------
launch_screen() {
  while true; do
    clear 2>/dev/null || true
    box_top
    box_line "            TV Tap List Setup"
    box_line "              $(dim 'v1.4.0')"
    box_mid
    menu_row "Admin password"  "$(masked "$ADMIN_PASSWORD")"
    menu_row "Timezone"        "$TZ_VAL"
    menu_row "Host port"       "$PORT_VAL"
    menu_row "Data directory"  "$DATA_DIR"
    menu_row "Ownership"       "$PUID_VAL:$PGID_VAL"
    if [ -n "$BF_USER" ]; then
      menu_row "Brewfather"    "$BF_USER"
    else
      menu_row "Brewfather"    "(not set)"
    fi
    box_mid
    box_line "  S) Start guided setup  (walk through each step)"
    box_line "  A) Advanced menu       (jump to any setting)"
    box_line "  R) Review & Deploy     (write .env and start)"
    box_line "  Q) Quit without saving"
    box_bottom
    echo
    read -r -p "  Choose [S, A, R, Q]: " choice || true
    choice="${choice:-S}"

    case "$(echo "$choice" | tr '[:lower:]' '[:upper:]')" in
      S) guided_setup ;;
      A) advanced_menu ;;
      R) section_review ;;
      Q) echo; info "Quit without saving."; exit 0 ;;
      *) warn "Invalid choice."; sleep 1 ;;
    esac
  done
}

# --- startup -----------------------------------------------------------------
bootstrap_repo
ensure_compose
load_env
apply_defaults

# Auto-detect timezone on fresh run (no existing .env)
if [ ! -f "$ENV_FILE" ]; then
  tz="$(detect_tz_ip || detect_tz_system || echo UTC)"
  [ -n "$tz" ] && TZ_VAL="$tz"
fi

launch_screen
