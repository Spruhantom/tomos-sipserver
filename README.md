# TomOS Home SIP Server

A self-hosted home telephone system for Debian 13, built around Asterisk with a browser-based administration dashboard.

## Current status

The Debian 13 Asterisk foundation passes CI. The repository now includes a source-build installer for Asterisk 22 LTS, PJSIP endpoints, an authenticated FastAPI extension-management API, SQLite persistence, a privileged configuration-apply helper and systemd units. The first dashboard release is a lightweight static interface served by FastAPI; WebRTC, Cisco SCCP and Yealink provisioning remain future stages.

## Install (Debian 13 only)

Clone this repository on a clean Debian 13 host, then run:

```bash
sudo ./install.sh
```

The installer builds the pinned Asterisk 22.12.0 LTS source release because Debian 13 stable does not currently publish the Asterisk PBX package. It verifies the source archive against the SHA-256 digest published with the official Asterisk 22.12.0 GitHub release before compiling.

It then:
- Installs build dependencies and compiles Asterisk with bundled PJProject.
- Creates the `asterisk` and unprivileged `tomos` service accounts.
- Backs up existing Asterisk configuration files before installing the TomOS base configuration.
- Creates extensions 100 and 101 with cryptographically random passwords.
- Stores initial SIP credentials in `/root/tomos-sip-credentials.txt` (mode 0600).
- Installs FastAPI and initializes the SQLite extension database at `/var/lib/tomos-sipserver/extensions.db` (mode 0600).
- Preserves existing extension 100/101 credentials when the installer is rerun.
- Generates an API bearer token in `/root/tomos-sip-api-token.txt` and a separate dashboard password in `/root/tomos-sip-dashboard-password.txt` (both mode 0600).
- Serves the dashboard and API on `127.0.0.1:8000`; it is not exposed directly to the LAN.
- Installs a narrowly-scoped sudo helper for validated PJSIP configuration generation and reload.
- Enables the Asterisk, health-check and API systemd units.

**Important:** run the installer on a clean Debian 13 machine or VM. Existing Asterisk configuration files are backed up, but the installer replaces the active base configuration files and should not be run over another live PBX without reviewing the migration first.

The PBX listens on UDP 5060 and uses RTP UDP 10000-20000. Keep SIP/RTP on a trusted home LAN or VPN. Do not configure router port forwarding or expose SIP publicly. No SIP trunk, TLS, WebRTC, dashboard or firewall rules are configured yet. Extensions 100 and 101 can call one another. Dial `*65` for the Asterisk echo test.

## Extension management API

The dashboard is served at `http://127.0.0.1:8000/`. Sign in using the one-time generated password in `/root/tomos-sip-dashboard-password.txt`. The dashboard uses short-lived HttpOnly, SameSite=Strict session cookies plus a CSRF header for mutations; the browser never receives the API bearer token. The API remains localhost-only; never expose port 8000 directly to an untrusted network. For remote access, place it behind a trusted HTTPS reverse proxy and enable `TOMOS_COOKIE_SECURE=true` in `/etc/tomos-sipserver/api.env` before restarting the API.

- `GET /api/v1/extensions` — list extensions without exposing SIP passwords.
- `POST /api/v1/extensions` — create an extension; a generated SIP password is returned once. Body: `{"extension":"120","display_name":"Office"}`.
- `PATCH /api/v1/extensions/{extension}` — update `display_name` or `enabled`.
- `POST /api/v1/extensions/{extension}/password` — rotate the SIP password and return it once.
- `POST /api/v1/extensions/{extension}/enable` and `/disable` — enable or disable registration.
- `DELETE /api/v1/extensions/{extension}` — delete an extension.

Each mutation regenerates PJSIP and dialplan configuration and requests an Asterisk reload. If configuration application fails, the API restores the previous database state and returns an error. The API runs as the unprivileged `tomos` user; a fixed-purpose root helper is allowed through one sudoers command only.

## Technology

| Layer | Technology |
|---|---|
| OS | Debian 13 |
| PBX engine | Asterisk 22 LTS (source build) |
| SIP | PJSIP |
| API | Python / FastAPI |
| Database | SQLite |
| Planned web UI | React / TypeScript |
| Planned browser calling | SIP.js, WebRTC and secure WebSockets |
| Service supervision | systemd |

## Security defaults

- No public SIP, AMI, ARI, TFTP or admin-panel exposure by default.
- Unique extension passwords; no default SIP passwords.
- The API requires a per-installation bearer token and listens only on loopback. Dashboard sessions use HttpOnly/SameSite cookies and CSRF validation; the browser does not store the API token.
- SIP passwords are not returned by list or update operations; they are returned only at creation/reset.
- Keep administration on the LAN/VPN and use HTTPS with a trusted reverse proxy for remote dashboard access; enable secure cookies when TLS is configured.
- Never expose AMI/ARI directly to browsers or untrusted networks.
- Call recording disabled by default, with consent and retention controls if added.
- A private PBX does not guarantee emergency calling; verify provider emergency-call support and keep an independent means of calling emergency services.

## Development roadmap

1. **Foundation (passed CI):** Debian 13 source-build installer, Asterisk, PJSIP, internal extensions, systemd and CI.
2. **Extension API (current):** authenticated FastAPI CRUD, SQLite persistence, password rotation, enable/disable and configuration rollback.
3. **Dashboard (initial release):** password login, session/CSRF protection, extension listing, create, enable/disable, delete and one-time password reset display. Device registration and call history remain planned.
4. Provisioning: Yealink HTTPS profiles and Cisco SCCP model-specific provisioning.
5. Browser phone: SIP.js, WSS, SRTP and persistent in-dashboard call controls.
6. Home features: voicemail, call history, groups, forwarding, DND, backups and restore.
7. Hardening/release: security review, hardware compatibility matrix and upgrade testing.

## Tests

GitHub Actions runs Python renderer and API tests, Bash syntax/ShellCheck checks, and a Debian 13 job that downloads and verifies the pinned Asterisk source, compiles it, starts the PBX process and checks that PJSIP endpoints 100/101 and the internal dialplan load. This does not yet prove two-way audio between physical phones or full API operation against a production Asterisk host.

