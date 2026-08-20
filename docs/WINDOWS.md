# Running TV Tap List on Windows (Docker Desktop)

Docker Desktop runs the container inside a Linux VM, so everything the Linux
instructions describe still applies - but the one thing that matters most, **where
your beers are actually stored**, works differently enough on Windows to be worth
its own guide. Get the data directory right once and the box looks after itself.

You end up at the same place as every other install: the admin at
`http://<host-ip>:8080/admin` and the TV display at `http://<host-ip>:8080/`.

**Contents**

- [Before you start](#before-you-start)
- [Step 1 - decide where the data lives](#step-1---decide-where-the-data-lives)
- [Step 2 - get the code and write .env](#step-2---get-the-code-and-write-env)
- [Step 3 - bring it up](#step-3---bring-it-up)
- [Step 4 - verify persistence before you rely on it](#step-4---verify-persistence-before-you-rely-on-it)
- [What silent data loss looks like](#what-silent-data-loss-looks-like)
- [PUID and PGID on Windows](#puid-and-pgid-on-windows)
- [File sharing and drive access](#file-sharing-and-drive-access)
- [WSL2 filesystem versus the Windows filesystem](#wsl2-filesystem-versus-the-windows-filesystem)
- [Reaching the display from the TV](#reaching-the-display-from-the-tv)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)

---

## Before you start

- **Docker Desktop for Windows**, running, with the **WSL2 backend** (the default).
  Check under **Settings -> General**; the Hyper-V backend works too and the one
  place it differs is called out in [File sharing and drive
  access](#file-sharing-and-drive-access).
- **PowerShell** for the commands below. They also work in Command Prompt. Git Bash
  works, with one caveat noted in [Step 2](#step-2---get-the-code-and-write-env).
- A folder you are happy to keep forever, for example `C:\taplist\data`. Everything
  the operator ever configures - tap count, theme, manual beers, the Brewfather key
  - lives there as plain files.

The whole install is the standard Compose path from
[INSTALLATION.md](INSTALLATION.md); this guide only differs where Windows does.

---

## Step 1 - decide where the data lives

The compose file maps one host directory to `/data` inside the container, and the
`DATA_DIR_HOST` value in `.env` chooses it. Two placements make sense on Windows,
and the choice is about where the operator wants to be able to open the files:

| Placement | `DATA_DIR_HOST` example | Trade-off |
|-----------|------------------------|-----------|
| **Windows filesystem** (recommended here) | `C:\taplist\data` | Open the mapped data directory in File Explorer, back it up with any Windows tool. Cross-filesystem access costs some speed, which this app never notices - it writes a handful of small text files per sync. |
| **Inside a WSL2 distro** | `/home/<user>/taplist/data`, with Compose run **from inside that distro** | Native Linux filesystem, fastest, and Linux ownership behaves exactly as the Linux docs describe. The files are only reachable from Windows through `\\wsl$\<distro>\...`, and they go away with the distro. |

Either is fine. What is **not** fine is a value Docker cannot resolve to a real host
directory - see [What silent data loss looks
like](#what-silent-data-loss-looks-like).

### Which `DATA_DIR_HOST` forms work

The compose file uses the value verbatim as the host side of the bind mount, so what
matters is what Docker Desktop makes of it:

| Value | Result |
|-------|--------|
| `C:\taplist\data` | **Works.** Docker Desktop converts the Windows-style path. |
| `C:/taplist/data` | **Works.** Forward slashes are accepted just the same. |
| `/c/taplist/data` | **Works.** Docker Desktop accepts the Unix-style form and converts it. Correct but easy to mistype, so prefer one of the two above. |
| `./taplist_data` (the default) | **Works.** Compose resolves a relative path against the directory holding `docker-compose.yml`, so this is the checkout's own `taplist_data\` folder. |
| `taplist_data` (no `./`) | **Fails loudly.** Without a leading `./` or drive letter, Compose reads it as the *name* of a volume, and because this project's compose file declares no such volume it refuses to start: `service "taplist" refers to undefined volume taplist_data`. Add the `./`. |
| *(empty or unset)* | **Fails loudly.** Compose reports `invalid spec: :/data: empty section between colons`. |
| `\\wsl$\<distro>\home\<user>\data` | **Not recommended.** It only starts when Docker Desktop's WSL integration is enabled for that distro; otherwise the container fails to start with `accessing specified distro mount service: ... no such file or directory`. For a WSL placement, run Compose from inside the distro with a plain Linux path instead. |

The directory does not have to exist beforehand - Docker Desktop creates a missing
bind-mount source. That is convenient and it is also why a typo produces a working
container writing to a folder nobody meant, so check the path once (Step 4).

---

## Step 2 - get the code and write .env

```powershell
git clone https://github.com/jceccato/tv-taplist.git
cd tv-taplist
copy .env.example .env
notepad .env
```

Set at least:

```ini
ADMIN_PASSWORD=your-strong-password
SESSION_SECRET=<a long random string>
TZ=Australia/Sydney
PORT=8080
DATA_DIR_HOST=C:\taplist\data
```

To generate a `SESSION_SECRET` in PowerShell without installing anything:

```powershell
-join ((1..32) | ForEach-Object { '{0:x2}' -f (Get-Random -Max 256) })
```

Write the path plainly, with no surrounding quotes - `.env` is read literally, so
quotes become part of the value.

> **Git Bash users:** Git Bash rewrites arguments that look like Unix paths, which
> mangles `-v` arguments and container paths on `docker run`. Prefix the command
> with `MSYS_NO_PATHCONV=1`, or use PowerShell. `docker compose up` reads its paths
> from `.env` and is unaffected.

---

## Step 3 - bring it up

```powershell
docker compose up -d
docker compose logs -f taplist
```

The log line to look for comes from the entrypoint:

```
[entrypoint] starting uvicorn as appuser (1000:1000), data=/data, port=8080, ...
```

Then open `http://localhost:8080/admin`, log in with `ADMIN_PASSWORD`, enter the
Brewfather **User ID** and **API key** (or set them as env vars), set the **tap
count**, and click **Sync Brewfather now**.

---

## Step 4 - verify persistence before you rely on it

Do this **before** entering real settings and manual beers. It takes a minute and it
is the difference between a box that keeps its data and one that quietly does not.

1. **Confirm Docker sees a bind mount, not a volume.**

   ```powershell
   docker inspect tv-taplist --format '{{json .Mounts}}'
   ```

   The output must say `"Type":"bind"` with `"Source"` equal to the intended folder.
   An empty list (`[]`), or `"Type":"volume"`, means the mapped data directory never
   took effect - go back to Step 1. The appliance flags this case itself, with a
   banner on `/admin` and a warning in `docker logs`, but check it here anyway: this
   step also catches a mapping that is real and points at the wrong folder, which
   nothing can detect for the operator.

2. **Confirm the files exist on the Windows side.** After the first start the
   container writes its skeleton immediately:

   ```powershell
   dir C:\taplist\data
   ```

   Expect `config.json`, `placeholder.svg`, and the `taps\` and `old_beers\`
   folders. Nothing there means the container is writing somewhere else.

3. **Make a change worth losing.** In `/admin`, set the tap count, then add one
   manual override with a recognisable beer name and save it. Confirm the file
   appears:

   ```powershell
   dir C:\taplist\data\taps
   ```

   A manual override shows up as a `custom_tap_N.md` file.

4. **Restart Docker Desktop** (right-click the tray icon -> **Restart**, or quit and
   relaunch it) and wait for the container to come back:

   ```powershell
   docker compose ps
   ```

5. **Check the board again.** The manual beer is still on tap and the tap count is
   still what the operator set. If step 2 passed and step 5 fails, the folder is
   being cleared by something else on the host - antivirus, a cleanup tool, or a
   temp directory. Move the mapped data directory somewhere permanent.

Step 2 is the load-bearing one. A restart alone proves less than it looks: data in a
Docker-managed volume also survives a Docker Desktop restart. Only files visible on
the Windows side prove the mapped data directory is real.

---

## What data loss looks like, and how the appliance flags it

When `/data` is not a bind mount to a host folder, **nothing fails**. The container
starts, the admin saves settings, the board renders, and every write really does
succeed - just into storage that is invisible from Windows and tied to the life of
that container. With no mapping, `/data` is an ordinary directory inside the
container's own writable layer: it disappears with the container, on the next
update or `docker compose down` and `up`.

The appliance checks this at startup and says so. With nothing mapped onto `/data`,
`/admin` shows a banner reading **"Data is not being saved"** and the container log
carries the same warning. It is not a dismissible banner, because it describes a
condition that is still true. A second check notices when the mapped directory is
suddenly empty or is a different directory from the one the container last used -
the shape of a host folder that was deleted, or storage that was not mounted before
Docker started - and warns once. `DEMO_MODE` suppresses both banners, since a demo
box is meant to be disposable.

Two ways to land there:

- Running the container without mapping a host directory at all - the demo
  one-liner in the README is deliberately like this, because a demo is meant to be
  disposable. It is not an install.
- Recreating the container (`docker compose down` then `up`, an update, a Docker
  Desktop reset) after having got the mapping wrong. The first container's data is
  simply not in the second one.

If the banner goes unread, the tell is a **half-empty board**. Brewfather-sourced taps rebuild
themselves within one sync interval, because the beers still live in Brewfather and
sync rewrites `bf_tap_N.md` from scratch. **Manual overrides do not** - a
`custom_tap_N.md` file exists only where it was written, and so do the settings in
`config.json`. So the symptom reads as "my manual taps vanished but the Brewfather
beers are fine", which looks like an app bug and is really a storage mapping that
was never a bind mount.

Both misconfigurations that this project's compose file can produce - a bare volume
name, or an empty `DATA_DIR_HOST` - **fail loudly at `docker compose up`** rather
than silently falling back to unmapped storage (see the table in Step 1). Between
that and the startup banner, the remaining gap is a mapping that is real but points
somewhere the operator did not intend, which is what Step 4 is for.

---

## PUID and PGID on Windows

On Linux these two must match the host user that owns the data directory, or the
non-root app cannot write. **On a Windows-filesystem bind mount they are effectively
cosmetic**, and the defaults are fine:

- Windows has no Linux uid or gid. Docker Desktop's file sharing presents the mount
  as owned by `root` with `drwxrwxrwx` permissions, so **any uid inside the
  container can write**, whatever `PUID` and `PGID` say.
- The entrypoint's `chown -R` on `/data` reports success and the ownership it sets
  is visible inside the container. It changes nothing about the file's Windows ACLs,
  which are what actually govern access from Windows.
- Leave `PUID=1000` / `PGID=1000` as shipped. Changing them to "fix" a Windows
  permission problem does not fix anything, because the permissions the operator is
  fighting are Windows ACLs on the folder.

If a write genuinely fails, the cause on Windows is the Windows side: the folder is
read-only, is under a path Docker Desktop cannot share, or a security tool is
blocking it.

The exception is the **WSL2 placement**. Files in a distro's own filesystem are real
Linux files with real ownership, so there `PUID` / `PGID` behave exactly as the
Linux instructions describe: set them to the distro user's `id -u` / `id -g`.

---

## File sharing and drive access

- **WSL2 backend (the default): nothing to configure.** Any local drive path works
  as a bind-mount source out of the box; there is no drive-sharing list to tick.
- **Hyper-V backend:** the directory must be shared first, under **Settings ->
  Resources -> File sharing**. Add the parent folder (for example `C:\taplist`)
  before bringing the container up, or the mount is refused.
- **Network drives and UNC paths** (`\\server\share\...`, or a mapped drive letter
  pointing at one) are not a good home for the mapped data directory on either
  backend. Keep it on a local disk.
- **Cloud-synced folders** (OneDrive, Dropbox, and the redirected `Documents` or
  `Desktop` folders they often own) work but are a poor fit: the sync client
  competes with the container over files that change on every sync cycle. A plain
  path such as `C:\taplist\data` avoids the whole question.

---

## WSL2 filesystem versus the Windows filesystem

Docker Desktop's WSL2 backend runs the engine in a Linux VM, so a bind mount of
`C:\taplist\data` crosses between the two filesystems on every read and write.
Microsoft's guidance is to keep files on the side you work from: files used by Linux
tooling belong in the distro's filesystem, and files used from Windows belong on the
Windows filesystem.

For this app the performance difference is not the deciding factor. It writes a few
small markdown files and images per sync and serves them from memory, so
cross-filesystem overhead is invisible next to a 15-minute sync interval. Choose on
**access and durability** instead:

- **Windows filesystem** (`C:\taplist\data`): the files are ordinary Windows files.
  File Explorer opens them, any backup tool picks them up, and they outlive Docker
  Desktop entirely. This is the recommendation for a venue box.
- **WSL2 distro** (`/home/<user>/taplist/data`, with Compose run inside the distro):
  fastest, and fully Linux in its semantics. But the files live inside the distro's
  virtual disk. Unregistering or resetting the distro takes them with it, and a
  Windows backup tool has to be pointed at `\\wsl$\<distro>\...` to see them at all.

---

## Reaching the display from the TV

The TV is a thin client - it only needs to load `/` over the LAN.

1. **Find the host's LAN address.** In PowerShell:

   ```powershell
   ipconfig | Select-String IPv4
   ```

   Use the address on the network the TV is on, typically `192.168.x.x`. Give the
   machine a DHCP reservation on the router so the address does not move under the
   TV.

2. **Allow the port through Windows Defender Firewall.** Docker Desktop publishes
   ports through `com.docker.backend.exe`, and that is what the host firewall
   filters on. The first time a container publishes a port, Windows usually prompts
   to allow it - allow it on **Private** networks and deny **Public**. If the prompt
   was dismissed, the rule can be added by hand in **Windows Defender Firewall with
   Advanced Security -> Inbound Rules**, or as an administrator:

   ```powershell
   New-NetFirewallRule -DisplayName "TV Tap List 8080" -Direction Inbound `
     -Protocol TCP -LocalPort 8080 -Profile Private -Action Allow
   ```

3. **Test from another device** on the same network: browse to
   `http://<host-ip>:8080/`. Working on `localhost` but not from the TV is a
   firewall or network-profile problem (a network marked **Public** blocks it), not
   an app problem.

4. **Point the TV at it** in kiosk / full-screen mode. For a dedicated display see
   [RASPBERRY_PI_KIOSK.md](RASPBERRY_PI_KIOSK.md) or
   [ANDROID_KIOSK.md](ANDROID_KIOSK.md).

Docker Desktop must be running for the board to be reachable, and by default it does
not start with Windows. Turn on **Settings -> General -> Start Docker Desktop when
you sign in**, keep the container's `restart: unless-stopped` policy as shipped, and
set the machine to sign in automatically if it lives in a cupboard behind the bar.
Sleep and hibernate stop the container as surely as a power cut, so set the power
plan to never sleep.

---

## Updating

```powershell
cd tv-taplist
git pull
docker compose pull
docker compose up -d
```

The mapped data directory and `.env` are untouched - `docker compose up -d`
recreates the container against the same host folder. This is exactly the moment a
wrong mapping shows itself, which is why Step 4 belongs before the first real
configuration rather than after the first update.

---

## Troubleshooting

| Symptom | Cause |
|---------|-------|
| `service "taplist" refers to undefined volume ...` | `DATA_DIR_HOST` has no `./` and no drive letter. Make it `./taplist_data` or an absolute path. |
| `invalid spec: :/data: empty section between colons` | `DATA_DIR_HOST` is empty or `.env` is missing. |
| `accessing specified distro mount service ...` | A `\\wsl$\<distro>\...` path with WSL integration disabled for that distro. Use a Windows path, or run Compose inside the distro. |
| Container runs, `C:\taplist\data` stays empty | `/data` is not bound to that folder. Run the `docker inspect` check in Step 4. |
| Manual beers gone after a recreate, Brewfather beers fine | The classic un-mapped `/data`. See [What silent data loss looks like](#what-silent-data-loss-looks-like). |
| Board loads on `localhost`, not from the TV | Firewall or network profile. See [Reaching the display from the TV](#reaching-the-display-from-the-tv). |
| Board unreachable after a reboot | Docker Desktop is not set to start at sign-in. |

For everything that is not Windows-specific - env vars, the Brewfather key,
reverse proxy and HTTPS - see [INSTALLATION.md](INSTALLATION.md), and
[FAQ.md](FAQ.md) for how the board itself works.
