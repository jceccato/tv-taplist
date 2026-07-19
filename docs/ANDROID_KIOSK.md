# Android Kiosk Display Setup

How to turn an Android device - phone, tablet, Android TV, Chromecast with
Google TV, or Amazon Fire Stick - into a dedicated tap-list display using
**[Screenlite Web Kiosk](https://github.com/screenlite/android-web-kiosk)**.
The app launches your board in full-screen on boot and keeps it on top, so the
device behaves like a dedicated display with no interaction needed.

This is the Android equivalent of the [Raspberry Pi kiosk](RASPBERRY_PI_KIOSK.md)
and is ideal when the TV is separate from the Docker host - point the device at
the container's IP and it stays on the board indefinitely.

> If you have a Raspberry Pi plugged directly into the TV via HDMI, the
> [Raspberry Pi Kiosk guide](RASPBERRY_PI_KIOSK.md) may be a better fit.

For installing the TV Tap List container itself, start with
[INSTALLATION.md](INSTALLATION.md). Come back here once the container is running
and reachable on your network.

**Contents**

- [What you need](#what-you-need)
- [Step 1 - Install Screenlite Web Kiosk](#step-1---install-screenlite-web-kiosk)
- [Step 2 - Grant permissions](#step-2---grant-permissions)
- [Step 3 - Configure for TV Tap List](#step-3---configure-for-tv-tap-list)
- [Device-specific tips](#device-specific-tips)
- [Troubleshooting](#troubleshooting)

---

## What you need

- **An Android device** running **Android 8.0 (API 26) or newer**. Android 5.1+
  (API 22) is supported experimentally but may be less stable.
- **The TV Tap List container running** on a Docker host that the Android device
  can reach over the network (e.g. `http://192.168.1.50:8080`).
- **The device must be on the same network** as the container (or be able to
  reach its IP/hostname). Wi-Fi works, but Ethernet or a strong Wi-Fi signal is
  better for a device that runs 24/7.

### Device options

| Device | How it works | Best for |
|--------|-------------|----------|
| **Android tablet** | Mounted near the bar; connects to the container over Wi-Fi. Browser runs full-screen on the tablet's own screen. | A small, self-contained display near the taps. |
| **Android TV / smart TV** | Kiosk app runs natively on the TV's Android OS. No extra hardware needed. | TVs with built-in Android TV (Sony, Philips, TCL, etc.). |
| **Chromecast with Google TV** | Small HDMI dongle. Install the kiosk APK directly on it. The TV becomes a dumb display. | Any TV with an HDMI port. Cheap, compact. |
| **Android phone** | Same as a tablet, but smaller. Can be useful for a single-tap mini display. | Testing, or a compact single-screen setup. |
| **Fire TV Stick** | Amazon's Fire Stick (Fire OS). Sideload the APK via ADB. Works the same as Android TV once installed. | Any TV with an HDMI port. Inexpensive, widely available. |
| **Android TV box / stick** | Generic Android boxes (e.g. Xiaomi Mi Box, NVIDIA Shield). Install the APK, point at the container. | TVs without built-in Android. |

---

## Step 1 - Install Screenlite Web Kiosk

Download and install the **latest APK** from the
[releases page](https://github.com/screenlite/android-web-kiosk/releases/latest)
(`screenlite-web-kiosk-v*-*-*.apk`).

### On a phone or tablet

1. Download the APK using the device's browser.
2. Open the downloaded file. Android will prompt you to **allow installing from
   unknown sources** - enable it for the browser (or file manager) you used.
3. Install the app.

### On an Android TV or Chromecast with Google TV

Sideloading is slightly different because these devices lack a built-in browser
with download support:

1. On the TV device, install **Send files to TV** from the Play Store.
2. On your phone or laptop, also install **Send files to TV** (or use the
   `adb` method below).
3. Download the APK on your phone/laptop, then transfer it to the TV device
   using Send files to TV.
4. On the TV device, open the transferred APK with a file manager (install one
   from the Play Store if needed - **X-plore File Manager** or **FX File
   Explorer** work well).
5. When prompted, allow installing from unknown sources for the file manager.

**Alternative - ADB (advanced):**

```bash
adb connect <tv-ip>:5555
adb install screenlite-web-kiosk-v*-*-*.apk
```

> On Chromecast with Google TV, enable Developer Options first: go to
> **Settings → System → About → Android TV OS build** and tap it 7 times.
> Then go to **Settings → System → Developer options → Enable USB debugging**.

### On a Fire TV Stick

Fire Sticks run Fire OS, an Android fork. Sideloading works but the setup path
differs slightly from stock Android TV:

1. **Enable ADB debugging** on the Fire Stick:
   - Go to **Settings → My Fire TV → About → Fire TV Stick** and tap the
     **device name** 7 times to unlock Developer Options.
   - Go back to **Settings → My Fire TV → Developer Options → ADB debugging → ON**.
   - Also enable **Apps from Unknown Sources** here (may be listed as **Install
     unknown apps**).

2. **Find the Fire Stick's IP address:**
   - **Settings → My Fire TV → About → Network**. Note the IP.

3. **Install the APK via ADB** from your laptop:

   ```bash
   adb connect <firestick-ip>:5555
   adb install screenlite-web-kiosk-v*-*-*.apk
   ```

   If you see an `INSTALL_FAILED_UPDATE_INCOMPATIBLE` error, the APK is already
   installed but a different version exists. Uninstall first:
   ```bash
   adb uninstall org.screenlite.webkiosk
   adb install screenlite-web-kiosk-v*-*-*.apk
   ```

4. The app appears in the Fire Stick's **Apps** row. If it doesn't, go to
   **Settings → Applications → Manage Installed Applications** and launch it
   from there.

> On newer Fire Sticks (Fire OS 7/8, based on Android 9/11), the Screenlite
> app is fully compatible. Older Fire Sticks running Fire OS 5/6 (Android 5/7)
> may work with the experimental support but could be less stable. Fire OS 7+
> is recommended.

---

## Step 2 - Grant permissions

Screenlite Web Kiosk needs two permissions to function:

### Display over other apps

This lets the kiosk draw on top of everything and stay in the foreground.

- **Phone / tablet:** Go to **Settings → Apps → Screenlite Web Kiosk → Advanced
  → Display over other apps → Allow**.
- **Android TV / Chromecast:** Go to **Settings → Apps → See all apps →
  Screenlite Web Kiosk → Permissions → Display over other apps → Allow**.

If you skip this, the app will prompt you on first launch.

### Disable battery optimisation (phone / tablet only)

Phones and tablets may suspend background services to save battery:

1. Go to **Settings → Apps → Screenlite Web Kiosk → Battery → Unrestricted**
   (or **Don't optimise** on older Android versions).
2. This ensures the kiosk service keeps running when the screen is on.

Android TV devices do not apply battery optimisation to foreground services,
so this step is not needed there.

---

## Step 3 - Configure for TV Tap List

### Launch the app

Open Screenlite Web Kiosk from the app drawer. On the first launch it shows a
default web page. Now open the settings:

- **Phone / tablet:** Tap the **bottom-left corner of the screen 5 times
  quickly** (within about 2 seconds).
- **Android TV / remote:** Press the **center / OK button on the remote 5 times
  quickly**.

### Settings to configure

| Setting | Value | Notes |
|---------|-------|-------|
| **Kiosk URL** | `http://<container-ip>:8080/` | The address of your tap list container. Use the Docker host's LAN IP (e.g. `http://192.168.1.50:8080`), not `localhost`. |
| **Check Interval** | `10` (seconds) | How often the app checks it is still in the foreground. Keep it at 10 unless you need more time for config tasks - longer intervals give you more time to access other apps or settings before the kiosk re-takes focus. |
| **Screen Rotation** | `0°` (default) | Most TVs are landscape already. Set to `90°` or `270°` for a portrait-mounted tablet or a vertically oriented display. |

Tap **Save**. The app restarts and loads your tap list in full-screen kiosk mode.

### Verify it works

- On boot (or reboot), the device should launch straight into the full-screen
  board with no interaction needed.
- If the container is temporarily unreachable during boot, the app retries
  automatically with exponential backoff. Once the container comes online the
  board appears.
- Pressing the remote's **back** button or swiping up from the bottom (on a
  tablet) will switch away from the kiosk, but the app's foreground service
  brings it back at the next check interval. To exit the kiosk properly for
  maintenance, open the Android **app switcher** and swipe the kiosk away, or
  restart the device.

---

## Device-specific tips

### Android TV

- Connect Ethernet if possible. Android TV's Wi-Fi power-saving behaviour can
  occasionally drop the connection when the device is idle.
- Disable the TV's built-in screen saver / ambient mode:
  **Settings → Device Preferences → Screen saver → Turn off**.
- Disable the TV's sleep timer:
  **Settings → Device Preferences → Power → Turn off display → Never**.

### Chromecast with Google TV

- The Chromecast is powered via USB. Use the **supplied power adapter**, not the
  TV's USB port - TV USB ports often provide insufficient power and can cause
  reboots under sustained use.
- Disable the Chromecast's ambient mode:
  **Settings → System → Ambient mode → Off**.
- Disable the sleep timer:
  **Settings → System → Power & energy → When inactive → Never**.

### Fire TV Stick

- Use the **supplied power adapter**, not the TV's USB port - Fire Sticks are
  sensitive to low power and will reboot if under-supplied.
- Disable the screen saver:
  **Settings → Display & Sounds → Screen saver → Start time → Never**.
- Disable the sleep timer:
  **Settings → Display & Sounds → Sleep → Never** (or the longest available).
- Fire Sticks show **Amazon ads on the home screen**. These do not affect the
  kiosk once it is running, but the app may take a few seconds to launch on
  boot because Fire OS loads the launcher first. The Screenlite app's
  foreground service re-takes over once started.
- The **Fire TV remote** works the same as other Android TV remotes: press the
  center / select button 5 times quickly to access the kiosk settings.

### Tablet

- Keep the tablet plugged in. A tablet running full-screen, full-brightness 24/7
  will drain the battery in hours otherwise.
- Consider a **tablet wall mount** or **tablet stand** near the bar area.
- Disable screen timeout:
  **Settings → Display → Screen timeout → Never** (or the longest available
  option; Screenlite's wake lock will keep it on, but Android's own timeout can
  still dim the screen on some devices).
- If the tablet supports it, enable **Developer Options → Stay awake** (keeps
  the screen on while charging).

---

## Troubleshooting

### The app doesn't start on boot

- Make sure the **Display over other apps** permission is granted (see
  [Step 2](#step-2---grant-permissions)).
- On some devices (especially Xiaomi, Huawei, OnePlus), the manufacturer's
  power-saving features can block apps from auto-starting. Check:
  - **Settings → Apps → Screenlite Web Kiosk → Autostart** → Enable.
  - **Settings → Battery → App launch** → Set Screenlite to **Manage manually**
    with auto-launch enabled.

### The screen goes blank or shows the Android home screen

1. Verify the kiosk URL is correct - if the app cannot load the page it will show
   an error screen between retries. Test the URL in a normal browser on the same
   device first.
2. Check the container is reachable from the device:
   ```bash
   # On a laptop/phone on the same network, test:
   curl http://<container-ip>:8080/healthz
   ```
   It should return `{"status":"ok"}`.

### The kiosk works, but the board content looks small or off

TV Tap List's display is designed for 16:9 screens. If the layout looks wrong:

- On a tablet in portrait mode, set **Screen Rotation** to `90°` or `270°` in
  the kiosk settings.
- On a 4:3 tablet or screen, the board will letterbox - this is normal.
- Ensure **Screen Rotation** in the kiosk settings is `0°` for a standard
  landscape TV. The app's rotation setting overrides the device's physical
  orientation.

### I can't access the settings (5-tap gesture not working)

- **On a touchscreen:** Make sure you are tapping the **very bottom-left corner**
  of the screen, right at the edge, 5 times within 2 seconds. Try restarting the
  app and tapping before the web page loads.
- **On a TV with remote:** Press the **center / OK / select button** 5 times
  quickly - do not navigate to a different element between presses. The D-pad
  center button, not the back or home button.

If the gesture still fails, clear the app's data to reset it to its setup state:
**Settings → Apps → Screenlite Web Kiosk → Storage → Clear data**, then re-open
the app.

### The board flickers or reloads periodically

The kiosk's foreground check does not reload the page - it just re-shows the
same WebView. If you see the board reloading:

- Make sure the board's rotation timer (`rotation_seconds` in the admin) is not
  set too low, which can look like flickering.
- On older or low-RAM devices, Android may kill background processes
  aggressively. Set the **Check Interval** in the kiosk settings to a lower
  value (e.g. 5 seconds) so the service reasserts more aggressively.
