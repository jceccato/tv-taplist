# Raspberry Pi Kiosk Setup

How to turn a Raspberry Pi running Raspberry Pi OS Desktop into a dedicated
full-screen display for TV Tap List. On boot, it launches Chromium pointing at
the locally hosted container on `http://localhost:8080`.

Supports **Bookworm** (labwc / wayfire on Wayland) and **Bullseye** (LXDE on
X11). The script detects your desktop environment automatically.

The setup has two parts: a few manual steps you do once (flash the OS, enable
SSH and auto-login), then a script that does everything else automatically.

For installing the TV Tap List container itself, start with
[INSTALLATION.md](INSTALLATION.md). Come back here once the container is running.

> If you're using an Android tablet, Android TV, or Chromecast instead of a Pi,
> see the [Android Kiosk guide](ANDROID_KIOSK.md).

> **Note:** The Pi kiosk setup is currently in **beta** and has not been fully
> tested across all Pi models and OS versions. If you run into any issues, please
> report them so we can improve the guide and script.

**Contents**

- [What you need](#what-you-need)
- [Step 1 — Flash the OS and boot](#step-1--flash-the-os-and-boot)
- [Step 2 — Enable SSH and auto-login](#step-2--enable-ssh-and-auto-login)
- [Step 3 — Install Docker and the tap list container](#step-3--install-docker-and-the-tap-list-container)
- [Step 4 — Run the kiosk script](#step-4--run-the-kiosk-script)
- [Kiosk modes](#kiosk-modes)
- [What the script does](#what-the-script-does)
- [Troubleshooting](#troubleshooting)

---

## What you need

- **A Raspberry Pi** (Pi 3, 4, or 5 — any model that runs the desktop OS
  comfortably). A Pi 4 with 2 GB or more is a sweet spot.
- **Raspberry Pi OS Desktop** (64-bit, Bookworm or Bullseye). The _Desktop_ image
  is required — the Lite image has no GUI and cannot run a kiosk browser.
  Download it from [raspberrypi.com/software](https://www.raspberrypi.com/software/).
- **A microSD card** (16 GB or larger; the Desktop image needs about 5 GB plus
  room for Docker images).
- **Power, HDMI cable, and a TV or monitor** the Pi will drive.
- **A network connection** — Ethernet is recommended for reliability, but Wi-Fi
  works too. The Pi must be on the same network as you when you SSH in for
  setup.
- **The TV Tap List container already running** on this Pi (or another Docker
  host reachable at `localhost:8080`). If you have not installed it yet, follow
  [INSTALLATION.md](INSTALLATION.md) first.

---

## Step 1 — Flash the OS and boot

1. **Download** the latest Raspberry Pi OS Desktop (64-bit) image from
   [raspberrypi.com/software](https://www.raspberrypi.com/software/).

2. **Flash it** to your microSD card with
   [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Before
   writing, click the gear icon and pre-configure:

   | Setting | Value |
   |---------|-------|
   | Hostname | `taplist` (or any name you like) |
   | Username | `pi` (the default) |
   | Password | Choose a strong one — you will need it for SSH |
   | Wi-Fi | Enter your SSID and password if using Wi-Fi |
   | Enable SSH | **Yes** — choose password authentication |
   | Locale / timezone / keyboard | Set to your region |

   > If you skip the Imager's pre-configuration step, you can still enable SSH
   > later by connecting a keyboard and monitor, but pre-configuring saves you a
   > trip to the TV.

3. **Insert the card, connect Ethernet (if using it), and power on.** Give it a
   minute to finish its first-boot resize and reboot.

---

## Step 2 — Enable SSH and auto-login

These steps configure the Pi so it boots straight into the desktop without
waiting for a password, and so you can reach it over SSH without a keyboard
attached.

> If you pre-configured SSH in Raspberry Pi Imager (Step 1), skip the SSH part
> and start at "Enable desktop auto-login".

### Enable SSH (if not already done)

Plug in a keyboard and monitor temporarily, or add an empty file named `ssh` to
the boot partition of the SD card before first boot.

From the desktop, open a terminal and run:

```bash
sudo raspi-config
```

Navigate to **Interface Options → SSH → Enable**, then **Finish**.

### Enable desktop auto-login

Still in `raspi-config` (run it if you closed it):

1. Go to **System Options → Boot / Auto Login**.
2. Choose **Desktop Autologin** (the one that auto-logs into the graphical
   desktop, not the CLI).
3. Select **Finish** and **reboot** when prompted.

After reboot the Pi logs in and starts the desktop automatically. The
display shows the default Raspberry Pi OS desktop — that's fine; the kiosk
script takes over after we run it.

### Verify SSH access

From your laptop or desktop on the same network:

```bash
ssh pi@taplist.local
```

(Use the hostname you set, or the Pi's IP address. Find the IP with
`ping taplist.local` or by checking your router's DHCP table.)

---

## Step 3 — Install Docker and the tap list container

> Skip ahead if you have already deployed the container and verified it at
> `http://<pi-ip>:8080`.

The quickest way: the guided installer pulls the prebuilt image, writes your
environment, and starts the container in one pass.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/jceccato/tv-taplist/main/setup)
```

Follow the prompts. At minimum, set an **admin password** and choose a
**timezone**. You can skip Brewfather credentials for now and add them later in
the admin UI.

When it finishes, verify the container is up:

```bash
docker ps | grep tv-taplist
curl -s http://localhost:8080/healthz
```

Both should succeed. If the health check returns `{"status":"ok"}` you are ready
for the kiosk script.

Manual Docker Compose instructions and environment variable details are in
[INSTALLATION.md](INSTALLATION.md).

---

## Step 4 — Run the kiosk script

SSH into the Pi and run:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/jceccato/tv-taplist/main/scripts/pi-kiosk.sh)
```

Or, if you cloned the repo:

```bash
cd tv-taplist
bash scripts/pi-kiosk.sh
```

The script is **interactive** — it asks before overwriting existing
configuration, so it's safe to re-run if you change anything later.

It installs Chromium (if missing), detects your desktop environment (labwc,
wayfire, or LXDE), writes a launch script, wires it into the autostart, and
disables screen blanking and power saving.

### Choosing a kiosk mode

By default the script sets up **escapable fullscreen** — Chromium fills the
screen but you can press **F11** or **ESC** to exit and use the Pi normally.
This is ideal for a home bar where the Pi doubles as a general-purpose machine.

For a public venue (bar, taproom, brewery) where you want to lock the display
down, pass the `KIOSK_MODE` environment variable:

```bash
KIOSK_MODE=true bash scripts/pi-kiosk.sh
```

This enables true locked kiosk mode: Chromium has no window decorations, no UI,
and can only be exited with **Alt+F4** on an attached keyboard (or by killing
the process over SSH).

When the script finishes, it prints a summary and a reminder to **reboot**:

```bash
sudo reboot
```

After reboot the Pi auto-logs into the desktop, waits for the tap list
container to become reachable, and launches Chromium in full-screen. You should
see the tap list board fill the screen.

---

## Kiosk modes

| Mode | Flag | Exit key | Use case |
|------|------|----------|----------|
| **Escapable fullscreen** (default) | `--start-fullscreen` | F11 or ESC | Home bar, shared Pi |
| **Locked kiosk** | `--kiosk` | Alt+F4 | Public venue, taproom |

Set the mode when running the script:

```bash
# Default (escapable, for home use)
bash scripts/pi-kiosk.sh

# Locked down (for public venues)
KIOSK_MODE=true bash scripts/pi-kiosk.sh
```

To switch an existing setup from one mode to the other, just re-run the script
with the desired setting — it will overwrite the launch script and you choose
whether to keep or replace the autostart entry.

---

## What the script does

Here is exactly what `pi-kiosk.sh` configures, so you know what changed on your
system.

| Component | What it changes |
|-----------|----------------|
| **Chromium** | Installed via `apt` if not already present. Falls back to `chromium` if `chromium-browser` is not available (Bullseye). |
| **Launch script** | Written to `~/.config/tv-taplist-kiosk.sh`. Polls `http://localhost:8080/healthz` for up to 2 minutes, then launches Chromium with `--start-fullscreen` (default) or `--kiosk` (if `KIOSK_MODE=true`). |
| **Autostart (labwc)** | Adds the launch script to `~/.config/labwc/autostart` (a shell script sourced at login). |
| **Autostart (wayfire)** | Adds the launch script to the `[autostart]` section of `~/.config/wayfire.ini`. |
| **Autostart (LXDE)** | Seeds `~/.config/lxsession/<profile>/autostart` from the global one, then appends the launch script. Preserves existing entries (panel, desktop, screensaver). |
| **Screen blanking** | Disabled via `raspi-config nonint do_blanking 1` and `consoleblank=0` on the kernel command line (`/boot/firmware/cmdline.txt`). |
| **DPMS (wayfire)** | Sets `dpms_timeout = -1` in `~/.config/wayfire.ini` so the compositor never turns the display off. |
| **Systemd sleep** | Masks `sleep.target`, `suspend.target`, `hibernate.target`, and `hybrid-sleep.target` so the Pi never suspends. |
| **Existing config** | Never overwritten without asking. Re-running the script is safe. |

### Network resilience

The kiosk launch script (`~/.config/tv-taplist-kiosk.sh`) polls the tap list
health endpoint for up to 2 minutes before launching Chromium. This handles the
common Docker-on-boot race: `dockerd` and the container take a few seconds to
start after the desktop is ready. If the container is still not up after 2
minutes, Chromium launches anyway (pointing at the URL) — it will show the
board as soon as the container comes online. The display page itself is
self-refreshing, so no manual reload is needed.

---

## Troubleshooting

### The screen is blank or shows the desktop instead of Chromium

- **Make sure the container is running:** SSH in and run
  `docker ps | grep tv-taplist`. If it's not listed, start it:
  `cd tv-taplist && docker compose up -d`.
- **Check that auto-login is set correctly:** `sudo raspi-config` →
  System Options → Boot / Auto Login → **Desktop Autologin**. If it's set to
  CLI or "wait for login", the desktop environment never starts.
- **Run the launch script manually** to see errors:
  ```bash
  bash ~/.config/tv-taplist-kiosk.sh
  ```
  This starts Chromium on whatever display the Pi is currently using. If it
  works manually but not on boot, the compositor probably hasn't finished
  starting before the script runs — check the autostart entry for your
  desktop environment (see below).

### The screen still blanks after a while

- Re-run `sudo raspi-config nonint do_blanking 1`.
- Verify `consoleblank=0` is present in `/boot/firmware/cmdline.txt`.
- If using wayfire, check `~/.config/wayfire.ini` contains
  `dpms_timeout = -1` under `[core]`.
- Some TVs have their own sleep timer — check the TV's on-screen settings menu.

### Chromium complains about "managed by your organization" or shows a restore bubble

The `--disable-infobars` and `--disable-session-crashed-bubble` flags suppress
these. If Chromium's behaviour changes in a future version and these flags stop
working, edit `~/.config/tv-taplist-kiosk.sh` — it is plain Bash and safe to
tweak.

### I want to use a different URL (e.g., the container is on another host)

Edit `~/.config/tv-taplist-kiosk.sh` and change the `DISPLAY_URL` variable near
the top. Or re-run the script with the environment variable set:

```bash
DISPLAY_URL=http://192.168.1.50:8080 bash scripts/pi-kiosk.sh
```

### I switched from wayfire to labwc (or vice versa)

Re-run the script — it detects the current desktop environment and updates the
right autostart file automatically. It asks before overwriting anything.

### I want to exit fullscreen but F11 / ESC does nothing

You are probably in locked kiosk mode (`KIOSK_MODE=true`). In that mode only
**Alt+F4** works. If you want to switch to escapable mode, re-run the script
without `KIOSK_MODE`:

```bash
bash scripts/pi-kiosk.sh
```

It will overwrite the launch script with `--start-fullscreen` instead of
`--kiosk`.

### I want to uninstall the kiosk setup

Remove the files the script created and reboot:

```bash
rm ~/.config/tv-taplist-kiosk.sh
sed -i '/tv-taplist-kiosk/d' ~/.config/labwc/autostart          # if using labwc
sed -i '/tv-taplist-kiosk/d' ~/.config/wayfire.ini               # if using wayfire
sed -i '/tv-taplist-kiosk/d' ~/.config/lxsession/*/autostart     # if using LXDE
```

The screen blanking and power management changes are harmless to leave in
place. If you want to revert them, run `sudo raspi-config nonint do_blanking 0`
and `sudo systemctl unmask sleep.target suspend.target hibernate.target
hybrid-sleep.target`.
