#!/usr/bin/env bash
# TV Tap List - Raspberry Pi Kiosk Setup.                Version: 1.1.0
#
# Configures a Raspberry Pi OS Desktop installation to boot directly into a
# full-screen Chromium pointing at the locally hosted TV Tap List container
# on http://localhost:8080.
#
# Supports Bookworm (labwc/wayfire) and Bullseye (LXDE).
#
# Safe to re-run: detects existing config and asks before overwriting.
#
# Usage:
#   bash scripts/pi-kiosk.sh                    # default: escapable fullscreen
#   KIOSK_MODE=true bash scripts/pi-kiosk.sh    # locked-down kiosk for bars
#
# Or one-liner from a fresh Pi:
#   bash <(curl -fsSL https://raw.githubusercontent.com/jceccato/tv-taplist/main/scripts/pi-kiosk.sh)
#
# BETA: This script is in beta and has not been fully tested across all
#       Pi models and OS versions. Please report any issues you encounter.
set -euo pipefail

# --- terminal helpers --------------------------------------------------------
bold()   { printf '\033[1m%s\033[0m\n' "$1"; }
dim()    { printf '\033[2m%s\033[0m\n' "$1"; }
info()   { printf '  %s\n' "$1"; }
warn()   { printf '\033[33m  ! %s\033[0m\n' "$1"; }
ok()     { printf '\033[32m  \xE2\x9C\x93 %s\033[0m\n' "$1"; }
die()    { printf '\033[31mERROR: %s\033[0m\n' "$1" >&2; exit 1; }

HEADER() {
  echo
  bold "TV Tap List -- Raspberry Pi Kiosk Setup"
  echo
}

# --- helpers -----------------------------------------------------------------
yesno() {
  local prompt="$1" default="${2:-Y}" reply
  read -r -p "$prompt [$([ "$default" = Y ] && echo 'Y/n' || echo 'y/N')]: " reply || true
  reply="${reply:-$default}"
  case "$reply" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

ensure_cmd() {
  # Ensure a command is installed, with an apt fallback on Debian-family systems.
  local cmd="$1" pkg="${2:-$cmd}"
  if command -v "$cmd" >/dev/null 2>&1; then return 0; fi
  warn "$pkg is not installed."
  if [ -f /etc/debian_version ]; then
    if yesno "Install $pkg now? (requires sudo)" "Y"; then
      sudo apt-get update -qq 2>/dev/null || true
      sudo apt-get install -y "$pkg"
      ok "$pkg installed."
    else
      die "$pkg is required. Install it manually and re-run."
    fi
  else
    die "$pkg is required. Install it and re-run."
  fi
}

# --- guard: Raspberry Pi + desktop check -------------------------------------
check_environment() {
  info "Checking environment..."

  # Must be running on a Raspberry Pi (or at least a Debian-family Linux).
  if [ ! -f /etc/debian_version ]; then
    warn "This script is designed for Raspberry Pi OS (Debian)."
    warn "Your system may not be compatible."
    if ! yesno "Continue anyway?" "N"; then
      die "Aborted."
    fi
  fi

  # Check for Raspberry Pi model info.
  if [ -f /proc/device-tree/model ]; then
    local model
    model="$(tr -d '\0' < /proc/device-tree/model || true)"
    if echo "$model" | grep -qi 'raspberry'; then
      info "Detected: $model"
    else
      warn "Could not confirm this is a Raspberry Pi."
    fi
  fi

  # Must be running as a user who can sudo (pi user has passwordless sudo by default on RPi OS).
  if ! sudo -n true 2>/dev/null; then
    warn "This script needs passwordless sudo access."
    if ! yesno "Continue? (sudo commands will prompt for a password)" "Y"; then
      die "Aborted."
    fi
  fi

  ok "Environment check passed."
  echo
}

# --- package installation ----------------------------------------------------
install_packages() {
  info "Checking required packages..."

  # Chromium: the package name varies by OS version.
  # Bookworm and later use 'chromium-browser'; Bullseye and earlier use
  # just 'chromium'.  Detect which one (if any) is available.
  local chrome_bin="chromium-browser" chrome_pkg=""

  if command -v "$chrome_bin" >/dev/null 2>&1; then
    ok "Chromium is already installed."
  elif command -v chromium >/dev/null 2>&1; then
    ok "Chromium is installed (as 'chromium')."
    # Ensure the binary name the kiosk script expects exists.
    sudo ln -sf "$(command -v chromium)" "/usr/local/bin/$chrome_bin" 2>/dev/null || true
  else
    # Neither binary is present -- work out which package to install.
    if apt-cache show chromium-browser >/dev/null 2>&1; then
      chrome_pkg="chromium-browser"
    elif apt-cache show chromium >/dev/null 2>&1; then
      chrome_pkg="chromium"
    fi

    if [ -n "$chrome_pkg" ]; then
      warn "Chromium is not installed."
      if yesno "Install $chrome_pkg now? (requires sudo)" "Y"; then
        sudo apt-get update -qq 2>/dev/null || true
        sudo apt-get install -y "$chrome_pkg"
        ok "$chrome_pkg installed."
        # If we installed 'chromium' (not 'chromium-browser'), create a
        # symlink so the kiosk script finds it at the expected name.
        if [ "$chrome_pkg" != "chromium-browser" ]; then
          sudo ln -sf "$(command -v chromium)" "/usr/local/bin/$chrome_bin" 2>/dev/null || true
        fi
      else
        die "Chromium is required. Install it manually and re-run."
      fi
    else
      die "Could not find a chromium-browser or chromium package to install."
    fi
  fi

  ensure_cmd curl curl
  ok "All required packages are installed."
  echo
}

# --- detect desktop environment / compositor ----------------------------------
# Returns one of: labwc, wayfire, lxde, unknown
detect_compositor() {
  # Check running processes first (most reliable on a live session).
  if pgrep -x labwc >/dev/null 2>&1; then
    echo "labwc"; return
  fi
  if pgrep -x wayfire >/dev/null 2>&1; then
    echo "wayfire"; return
  fi
  if pgrep -x lxsession >/dev/null 2>&1; then
    echo "lxde"; return
  fi

  # If not running (e.g. we're over SSH before the desktop starts), check
  # which config directories exist.
  if [ -d "$HOME/.config/labwc" ]; then
    echo "labwc"; return
  fi
  if [ -d "$HOME/.config/wayfire" ]; then
    echo "wayfire"; return
  fi
  if [ -d "$HOME/.config/lxsession" ]; then
    echo "lxde"; return
  fi

  # Check the XDG_CURRENT_DESKTOP or DESKTOP_SESSION env vars.
  local d="${XDG_CURRENT_DESKTOP:-${DESKTOP_SESSION:-}}"
  case "$(echo "$d" | tr '[:upper:]' '[:lower:]')" in
    *labwc*)     echo "labwc";   return ;;
    *wayfire*)   echo "wayfire"; return ;;
    *lxde*|*lxsession*) echo "lxde"; return ;;
  esac

  # Check for /etc/xdg/lxsession (present on Raspberry Pi OS with LXDE).
  if [ -d "/etc/xdg/lxsession" ]; then
    echo "lxde"; return
  fi

  # Default: assume labwc (Bookworm default on Pi 3 and older; also used on
  # Pi 4/5 since late 2024 updates).
  echo "labwc"
}

# --- create the kiosk launch script ------------------------------------------
write_kiosk_script() {
  local script_path="$HOME/.config/tv-taplist-kiosk.sh"
  local display_url="${DISPLAY_URL:-http://localhost:8080}"
  local wait_secs="${WAIT_SECS:-120}"
  local poll_secs="${POLL_SECS:-2}"
  local kiosk_mode="${KIOSK_MODE:-false}"

  # Build the Chromium flags based on mode.
  # Default: fullscreen but escapable (F11 / ESC work).
  # KIOSK_MODE=true: locked-down kiosk (no UI, Alt+F4 only).
  local chrome_flags mode_label
  if [ "$kiosk_mode" = "true" ] || [ "$kiosk_mode" = "1" ]; then
    chrome_flags='--kiosk'
    mode_label='locked kiosk (Alt+F4 to exit)'
  else
    chrome_flags='--start-fullscreen'
    mode_label='fullscreen (F11 / ESC to exit)'
  fi

  if [ -f "$script_path" ]; then
    info "Kiosk launch script already exists at:"
    info "  $script_path"
    if ! yesno "Overwrite it?" "N"; then
      ok "Kept existing kiosk script."
      return
    fi
  fi

  info "Writing kiosk launch script to $script_path ..."
  info "  Mode: $mode_label"

  mkdir -p "$(dirname "$script_path")"

  cat > "$script_path" << KIOSKEOF
#!/bin/bash
# Auto-generated by TV Tap List pi-kiosk.sh.  Safe to edit.
#
# Waits for the TV Tap List container to be reachable, then launches Chromium
# in full-screen mode.

DISPLAY_URL="$display_url"
WAIT_SECS=$wait_secs
POLL_SECS=$poll_secs
MODE="$kiosk_mode"

log()  { printf '[tv-taplist-kiosk] %s %s\n' "\$(date '+%H:%M:%S')" "\$*"; }
[ "\$MODE" = "true" ] && log "Mode: locked kiosk (Alt+F4 to exit)" \\
                     || log "Mode: fullscreen (F11 / ESC to exit)"

log "Waiting for \$DISPLAY_URL (up to \${WAIT_SECS}s) ..."

elapsed=0
while [ \$elapsed -lt \$WAIT_SECS ]; do
  if curl -sf --max-time 3 "\$DISPLAY_URL/healthz" >/dev/null 2>&1; then
    log "Health check passed -- launching Chromium."
    break
  fi
  sleep "\$POLL_SECS"
  elapsed=\$((elapsed + POLL_SECS))
done

# Launch even if the health check timed out (container might be slow to start
# but still respond to HTTP -- or the admin might fix things later).
log "Starting Chromium ($(if [ "\$MODE" = "true" ]; then echo locked; else echo escapable; fi) fullscreen) ..."
exec chromium-browser \\
  $chrome_flags \\
  --noerrdialogs \\
  --disable-infobars \\
  --disable-session-crashed-bubble \\
  --disable-restore-session-state \\
  --disable-features=TranslateUI \\
  --disable-pinch \\
  --overscroll-history-navigation=0 \\
  "\$DISPLAY_URL"
KIOSKEOF

  chmod +x "$script_path"
  ok "Kiosk launch script written."
}

# --- configure the compositor to start the kiosk on login --------------------
configure_autostart() {
  local compositor="$1"
  local kiosk_script="$HOME/.config/tv-taplist-kiosk.sh"

  case "$compositor" in
    labwc)
      local autostart_file="$HOME/.config/labwc/autostart"

      # labwc autostart is a shell script that gets sourced at session start.
      # The directory must exist (labwc creates it on first run).
      mkdir -p "$(dirname "$autostart_file")"

      if [ -f "$autostart_file" ] && grep -qF 'tv-taplist-kiosk' "$autostart_file" 2>/dev/null; then
        info "labwc autostart already references the kiosk script."
        if ! yesno "Re-apply the autostart entry?" "N"; then
          ok "Kept existing autostart."
          return
        fi
        # Remove any existing kiosk line so we can append a clean one.
        sed -i '/tv-taplist-kiosk/d' "$autostart_file"
      fi

      # Ensure the file starts with a shebang if it doesn't exist yet.
      if [ ! -f "$autostart_file" ]; then
        printf '#!/bin/sh\n# labwc autostart -- managed by TV Tap List kiosk setup\n\n' > "$autostart_file"
      elif ! head -1 "$autostart_file" | grep -q '^#!/' 2>/dev/null; then
        # Add shebang at the top if missing.
        sed -i '1i#!/bin/sh' "$autostart_file"
      fi

      printf '\n# TV Tap List -- fullscreen kiosk (added by pi-kiosk.sh)\n%s &\n' \
        "$kiosk_script" >> "$autostart_file"

      ok "Added kiosk to labwc autostart."
      ;;

    wayfire)
      local ini_file="$HOME/.config/wayfire.ini"

      # wayfire autostart lives in the [autostart] section of wayfire.ini.
      mkdir -p "$(dirname "$ini_file")"

      if [ -f "$ini_file" ] && grep -qF 'tv-taplist-kiosk' "$ini_file" 2>/dev/null; then
        info "wayfire.ini already references the kiosk script."
        if ! yesno "Re-apply the autostart entry?" "N"; then
          ok "Kept existing autostart."
          return
        fi
        sed -i '/tv-taplist-kiosk/d' "$ini_file"
      fi

      # Ensure the [autostart] section exists.
      if [ ! -f "$ini_file" ]; then
        echo "# wayfire config -- managed by TV Tap List kiosk setup" > "$ini_file"
      fi
      if ! grep -q '^\[autostart\]' "$ini_file" 2>/dev/null; then
        printf '\n[autostart]\n' >> "$ini_file"
      fi

      # Add the kiosk entry to the [autostart] section.
      # wayfire expects "key = value" pairs; the key is just a label.
      printf 'taplist_kiosk = %s\n' "$kiosk_script" >> "$ini_file"

      ok "Added kiosk to wayfire.ini [autostart]."
      ;;

    lxde)
      # LXDE (X11) uses lxsession.  The autostart file lives under
      # ~/.config/lxsession/<profile>/autostart where <profile> is the
      # session name from the `lxsession -s <profile>` command line.
      # Common values: LXDE-pi (Raspberry Pi OS), LXDE (standard).
      local lxde_profile="" autostart_file=""

      # Try to extract the profile from a running lxsession process.
      lxde_profile="$(ps aux | grep '[l]xsession.*-s ' | sed -n 's/.*-s \([^ ]*\).*/\1/p' | head -1 || true)"

      # Fallback: look for an existing config directory.
      if [ -z "$lxde_profile" ]; then
        for d in "$HOME/.config/lxsession"/*/; do
          [ -d "$d" ] || continue
          lxde_profile="$(basename "$d")"
          break
        done
      fi

      # Last resort: try common profile names.
      if [ -z "$lxde_profile" ]; then
        if [ -f "/etc/xdg/lxsession/LXDE-pi/autostart" ]; then
          lxde_profile="LXDE-pi"
        elif [ -f "/etc/xdg/lxsession/LXDE/autostart" ]; then
          lxde_profile="LXDE"
        else
          lxde_profile="LXDE-pi"
        fi
      fi

      autostart_file="$HOME/.config/lxsession/$lxde_profile/autostart"
      info "LXDE session profile: $lxde_profile"

      # If the user doesn't have a local autostart yet, seed it from the
      # global one so we don't lose the default entries (panel, desktop, etc.).
      local global_auto="/etc/xdg/lxsession/$lxde_profile/autostart"
      if [ ! -f "$autostart_file" ]; then
        mkdir -p "$(dirname "$autostart_file")"
        if [ -f "$global_auto" ]; then
          cp "$global_auto" "$autostart_file"
          info "Seeded local autostart from $global_auto"
        else
          touch "$autostart_file"
        fi
      fi

      # Check for existing kiosk entry.
      if grep -qF 'tv-taplist-kiosk' "$autostart_file" 2>/dev/null; then
        info "LXDE autostart already references the kiosk script."
        if ! yesno "Re-apply the autostart entry?" "N"; then
          ok "Kept existing autostart."
          return
        fi
        sed -i '/tv-taplist-kiosk/d' "$autostart_file"
      fi

      # LXDE autostart uses @-prefixed commands to run in the background.
      printf '\n# TV Tap List -- fullscreen kiosk (added by pi-kiosk.sh)\n@%s\n' \
        "$kiosk_script" >> "$autostart_file"

      ok "Added kiosk to LXDE autostart ($lxde_profile)."
      ;;

    *)
      warn "Unknown compositor. Could not configure autostart."
      warn "Please manually start the kiosk script on login:"
      info "  $kiosk_script"
      return
      ;;
  esac
}

# --- disable screen blanking, screen savers, and system sleep ----------------
disable_power_management() {
  info "Disabling screen blanking and power management..."

  # 1. raspi-config: disable screen blanking (handles kernel + X11 + Wayland
  #    via the compositor's DPMS/idle settings).
  if command -v raspi-config >/dev/null 2>&1; then
    sudo raspi-config nonint do_blanking 1 2>/dev/null && \
      ok "Screen blanking disabled (raspi-config)." || \
      warn "raspi-config do_blanking failed -- continuing."
  else
    info "raspi-config not found; skipping kernel blanking tweak."
    info "  (If the screen still blanks, run: sudo raspi-config -> Display -> Screen Blanking)"
  fi

  # 2. Kernel cmdline: append consoleblank=0 if not already present.
  local cmdline_file=""
  if [ -f /boot/firmware/cmdline.txt ]; then
    cmdline_file="/boot/firmware/cmdline.txt"       # Bookworm
  elif [ -f /boot/cmdline.txt ]; then
    cmdline_file="/boot/cmdline.txt"                 # older (Bullseye)
  fi

  if [ -n "$cmdline_file" ]; then
    if grep -q 'consoleblank=' "$cmdline_file" 2>/dev/null; then
      # Update any existing consoleblank=N to consoleblank=0.
      sudo sed -i 's/consoleblank=[^ ]*/consoleblank=0/' "$cmdline_file"
      ok "Kernel console blanking set to 0."
    else
      info "Adding consoleblank=0 to $cmdline_file ..."
      sudo sed -i '$ s/$/ consoleblank=0/' "$cmdline_file"
      # Fix up any doubled spaces from repeated runs.
      sudo sed -i 's/  \+/ /g' "$cmdline_file"
      ok "Kernel console blanking disabled."
    fi
  else
    warn "Could not find /boot/firmware/cmdline.txt -- skipping kernel blanking tweak."
  fi

  # 3. Disable systemd sleep/resume targets so the Pi never suspends.
  if command -v systemctl >/dev/null 2>&1; then
    for target in sleep.target suspend.target hibernate.target hybrid-sleep.target; do
      if systemctl is-enabled "$target" >/dev/null 2>&1; then
        sudo systemctl mask "$target" 2>/dev/null && \
          info "Masked $target." || true
      fi
    done
    ok "Systemd sleep targets disabled."
  fi

  # 4. If wayfire is detected (or its config is present), set DPMS timeout to
  #    never (-1) as a belt-and-suspenders measure.
  if [ -d "$HOME/.config/wayfire" ] || pgrep -x wayfire >/dev/null 2>&1; then
    local wf_file="$HOME/.config/wayfire.ini"
    mkdir -p "$(dirname "$wf_file")"
    touch "$wf_file"

    if ! grep -q '^dpms_timeout' "$wf_file" 2>/dev/null; then
      # Ensure [core] section exists.
      if ! grep -q '^\[core\]' "$wf_file" 2>/dev/null; then
        printf '\n[core]\n' >> "$wf_file"
      fi
      # Insert dpms_timeout after the [core] section header.
      sed -i '/^\[core\]/a dpms_timeout = -1' "$wf_file"
      ok "wayfire DPMS timeout set to never (-1)."
    else
      # Update any existing dpms_timeout line.
      sed -i 's/^dpms_timeout *=.*/dpms_timeout = -1/' "$wf_file"
      ok "wayfire DPMS timeout confirmed at -1."
    fi
  fi

  echo
}

# --- summary -----------------------------------------------------------------
print_summary() {
  local kiosk_mode="${KIOSK_MODE:-false}"
  local exit_hint="F11 / ESC"
  [ "$kiosk_mode" = "true" ] || [ "$kiosk_mode" = "1" ] && exit_hint="Alt+F4"

  echo
  bold "Kiosk setup complete."
  echo
  info "What was configured:"
  info "  • Chromium launch script at ~/.config/tv-taplist-kiosk.sh"
  info "  • Autostart entry for the detected desktop environment"
  info "  • Screen blanking disabled (raspi-config + kernel cmdline + systemd)"
  info "  • Power management / sleep targets masked"
  info "  • Mode: $(if [ "$kiosk_mode" = "true" ] || [ "$kiosk_mode" = "1" ]; then echo 'locked kiosk'; else echo 'escapable fullscreen'; fi)"
  echo
  info "Manual checks before rebooting:"
  info "  1. Run 'sudo raspi-config' -> System -> Boot -> Desktop Autologin"
  info "     (Choose 'Desktop' or the GUI autologin option so the compositor starts.)"
  info "  2. Make sure the TV Tap List container is running:"
  info "       docker ps | grep tv-taplist"
  info "     If not: cd tv-taplist && docker compose up -d"
  echo
  bold "When ready, reboot:  sudo reboot"
  echo
  info "After reboot the Pi will auto-login, start the desktop, and launch"
  info "Chromium in full-screen pointing at http://localhost:8080."
  info "To exit, press $exit_hint."
  echo
}

# --- main --------------------------------------------------------------------
main() {
  HEADER

  local kiosk_mode="${KIOSK_MODE:-false}"
  if [ "$kiosk_mode" = "true" ] || [ "$kiosk_mode" = "1" ]; then
    info "Mode: locked kiosk (Alt+F4 to exit)"
  else
    info "Mode: escapable fullscreen (F11 / ESC to exit)"
  fi

  check_environment
  install_packages

  local compositor
  compositor="$(detect_compositor)"
  info "Detected compositor: $(bold "$compositor")"

  write_kiosk_script
  configure_autostart "$compositor"
  disable_power_management
  print_summary
}

main "$@"
