# TomOS Home SIP Server

A self-hosted home telephone system for Debian 13, built around Asterisk with a web administration panel and an integrated browser softphone.

## Current status

The Debian 13 Asterisk foundation passes CI. The repository now includes a source-build installer for Asterisk 22 LTS, PJSIP endpoints, an authenticated FastAPI extension-management API, SQLite persistence, a privileged configuration-apply helper and systemd units. The React dashboard, WebRTC client, Cisco SCCP and Yealink provisioning remain future stages.

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
- Generates an API bearer token in `/root/tomos-sip-api-token.txt` (mode 0600).
- Binds the API to `127.0.0.1:8000`; it is not exposed directly to the LAN.
- Installs a narrowly-scoped sudo helper for validated PJSIP configuration generation and reload.
- Enables the Asterisk, health-check and API systemd units.

**Important:** run the installer on a clean Debian 13 machine or VM. Existing Asterisk configuration files are backed up, but the installer replaces the active base configuration files and should not be run over another live PBX without reviewing the migration first.

The PBX listens on UDP 5060 and uses RTP UDP 10000-20000. Keep SIP/RTP on a trusted home LAN or VPN. Do not configure router port forwarding or expose SIP publicly. No SIP trunk, TLS, WebRTC, dashboard or firewall rules are configured yet. Extensions 100 and 101 can call one another. Dial `*65` for the Asterisk echo test.

## Extension management API

The API listens on `http://127.0.0.1:8000`. It exposes public `GET /health` and bearer-authenticated `/api/v1/extensions` routes. Read the token from `/root/tomos-sip-api-token.txt` and send it as `Authorization: Bearer TOKEN`. The API is localhost-only; never expose port 8000 directly to an untrusted network.

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
- The API requires a per-installation bearer token and listens only on loopback.
- SIP passwords are not returned by list or update operations; they are returned only at creation/reset.
- Keep administration on the LAN/VPN and use HTTPS when a reverse proxy/dashboard is introduced.
- Never expose AMI/ARI directly to browsers or untrusted networks.
- Call recording disabled by default, with consent and retention controls if added.
- A private PBX does not guarantee emergency calling; verify provider emergency-call support and keep an independent means of calling emergency services.

## Development roadmap

1. **Foundation (passed CI):** Debian 13 source-build installer, Asterisk, PJSIP, internal extensions, systemd and CI.
2. **Extension API (current):** authenticated FastAPI CRUD, SQLite persistence, password rotation, enable/disable and configuration rollback.
3. Administration dashboard: server status, extension/device management and audit trail.
4. Provisioning: Yealink HTTPS profiles and Cisco SCCP model-specific provisioning.
5. Browser phone: SIP.js, WSS, SRTP and persistent in-dashboard call controls.
6. Home features: voicemail, call history, groups, forwarding, DND, backups and restore.
7. Hardening/release: security review, hardware compatibility matrix and upgrade testing.

## Tests

GitHub Actions runs Python renderer and API tests, Bash syntax/ShellCheck checks, and a Debian 13 job that downloads and verifies the pinned Asterisk source, compiles it, starts the PBX process and checks that PJSIP endpoints 100/101 and the internal dialplan load. This does not yet prove two-way audio between physical phones or full API operation against a production Asterisk host.

