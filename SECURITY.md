# Security Policy

## Reporting a Vulnerability

**Do not open a public issue** for security vulnerabilities.

To report a security issue, please use one of these methods:

- **Preferred:** Use GitHub's [private vulnerability reporting](https://github.com/jceccato/tv-taplist/security/advisories/new) feature. This allows coordinated disclosure and keeps the conversation private.
- **Alternative:** Email the project maintainer at **[PROJECT MAINTAINER EMAIL]**. Include "TV TAP LIST SECURITY" in the subject line.

You should receive an acknowledgment within **5 business days**. If you don't
hear back, please follow up.

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest `main` branch | Yes |
| Latest GHCR image (`:latest` tag) | Yes |
| Tagged releases (`v*`) | Yes |
| Older releases | No |

Security patches are applied to the `main` branch and released as a new tag.
Users running the `:latest` Docker image will receive the fix on their next pull.

## Disclosure Timeline

When a vulnerability is reported, we aim to:

1. **Acknowledge** within 5 business days.
2. **Validate and assess severity** within 10 business days.
3. **Develop and test a fix** — timeline varies by complexity.
4. **Coordinate disclosure** with the reporter. We prefer to publish the fix
   and the advisory simultaneously.
5. **Credit the reporter** in the advisory (with permission).

## Scope

### In scope

- The web application (`app/` — FastAPI routes, auth, API endpoints)
- The Docker image build and entrypoint
- The admin interface and its authentication
- Data handling and file I/O in the mapped `/data` directory
- The Brewfather sync and API client

### Out of scope

- TLS/HTTPS configuration on the user's reverse proxy (the container serves
  plain HTTP; users are responsible for TLS termination)
- Physical access to the host machine or TV
- Social engineering attacks
- Vulnerabilities in the user's own Brewfather account or API key management
- Denial of service from an attacker with network access to the container

## Security Model

TV Tap List is an **appliance**: a single Docker container intended to run on a
local network behind a reverse proxy. Key security properties:

- **Admin authentication** uses signed, `HttpOnly`, `SameSite=Strict` cookies
  (`Secure` when served over HTTPS). Login is rate-limited (5 failures per 5
  minutes per IP).
- **The display endpoint (`/`) and `/api/board` are public** — no authentication
  required. The board API deliberately omits sync-status and error details.
- **Upload validation happens before any filesystem change.** Rejected uploads
  never delete existing data. Image responses carry `Content-Security-Policy:
  script-src 'none'; sandbox` + `nosniff` so an uploaded SVG cannot execute
  script if opened directly.
- **Secrets** (session key, admin password) are environment variables. The
  Brewfather API key can be supplied via environment variables
  (`BREWFATHER_USER_ID` / `BREWFATHER_API_KEY`) or stored in
  `/data/config.json`. Never commit `.env` files or the `taplist_data/`
  directory.
- **The container runs non-root** at runtime via `gosu` with configurable
  `PUID`/`PGID`.
- **No outbound requests are made from the display page.** The frontend loads
  zero CDN resources, ensuring the board stays safe even on an isolated network.

## Reporting a vulnerability in a dependency

If a vulnerability is found in a third-party dependency (Python packages,
the base Docker image, etc.), please report it the same way. We track
dependency updates and will ship a patched image promptly.

---

**Thank you for helping keep TV Tap List and its users safe.**
