# TomOS Home SIP Server

A self-hosted home telephone system for Debian 13, built around Asterisk with a browser-based administration dashboard.

## Current status

The Debian 13 Asterisk foundation passes CI; CI now also builds and integration-checks the initial Cisco SCCP driver. The installer builds Asterisk 22 LTS and the pinned AI3I/chan_sccp driver, while retaining PJSIP for ordinary SIP phones. The repository includes an authenticated FastAPI extension-management API, SQLite persistence, a privileged configuration-apply helper and systemd units. The dashboard provides extension management, live registration status, call history and Yealink, Grandstream and Polycom provisioning workflows. SCCP is currently at driver-foundation stage: the module is loaded and its CLI is checked, but Cisco handset inventory is now being added for the initial 7940, 7960 and 7970 target models; model-specific SEP XML generation and controlled TFTP delivery remain subsequent stages. Physical Cisco handset and firmware compatibility remain unverified. Asterisk writes call-detail records to a separate local SQLite database; call audio is not recorded.

## Install (Debian 13 only)

**Quick install — one command** on a clean Debian 13 host:

```bash
curl -fsSL https://raw.githubusercontent.com/Spruhantom/tomos-sipserver/main/install.sh | sudo bash
```

Alternatively, clone the repository and run the installer locally:

```bash
git clone https://github.com/Spruhantom/tomos-sipserver.git
cd tomos-sipserver
sudo ./install.sh
```

The one-line command downloads the current installer from the repository's `main` branch and runs it with root privileges. Review the installer before running it, and use a clean Debian 13 machine or VM.

The installer builds the pinned Asterisk 22.12.0 LTS source release because Debian 13 stable does not currently publish the Asterisk PBX package. It verifies the source archive against the SHA-256 digest published with the official Asterisk 22.12.0 GitHub release before compiling.

It then:
- Installs build dependencies and compiles Asterisk with bundled PJProject, then builds and installs the pinned AI3I/chan_sccp channel driver against those Asterisk headers.
- Creates the `asterisk` and unprivileged `tomos` service accounts.
- Backs up existing Asterisk configuration files before installing the TomOS base configuration.
- Creates extensions 100 and 101 with cryptographically random passwords.
- Stores initial SIP credentials in `/root/tomos-sip-credentials.txt` (mode 0600).
- Installs FastAPI and initializes the SQLite extension database at `/var/lib/tomos-sipserver/extensions.db` (mode 0600).
- Enables Asterisk CDR with the `cdr_sqlite3_custom` backend and stores call metadata in `/var/lib/tomos-sipserver/cdr/cdr.db`; no audio is recorded.
- Preserves existing extension 100/101 credentials when the installer is rerun.
- Generates an API bearer token in `/root/tomos-sip-api-token.txt` and stores the dashboard administrator username and password in `/root/tomos-sip-dashboard-username.txt` and `/root/tomos-sip-dashboard-password.txt` (mode 0600). On an interactive first install, the installer prompts for both dashboard credentials; passwords are hidden while typed and must be at least 24 characters. On upgrades, existing credentials are preserved when the password prompt is left blank. In non-interactive installs, a strong random password is generated.
- Serves the dashboard and API on `127.0.0.1:8000`; it is not exposed directly to the LAN.
- Installs a narrowly-scoped sudo helper for validated PJSIP configuration generation and reload.
- Loads `chan_sccp.so`, disables the conflicting built-in `chan_skinny.so`, and installs the initial SCCP listener configuration in `/etc/asterisk/sccp.conf`.
- Enables the Asterisk, health-check and API systemd units.

**Important:** run the installer on a clean Debian 13 machine or VM. Existing Asterisk configuration files are backed up, but the installer replaces the active base configuration files and should not be run over another live PBX without reviewing the migration first.

The PBX listens on UDP 5060 for SIP, TCP 2000 for SCCP, and UDP 10000-20000 for RTP. Keep all voice traffic on a trusted home LAN or VPN. Do not configure router port forwarding or expose SIP/SCCP publicly. Cisco SCCP device provisioning and TFTP are not enabled yet; the driver is installed and its CLI is checked by CI. Extensions 100 and 101 can call one another. Dial `*65` for the Asterisk echo test.

## Extension management API

The dashboard is served at `http://127.0.0.1:8000/`. On the first install, sign in using the bootstrap administrator username and password saved in `/root/tomos-sip-dashboard-username.txt` and `/root/tomos-sip-dashboard-password.txt`. The password file records the installer bootstrap credential only; changing a password later in the dashboard does not update that file. On upgrades, explicitly entered installer credential changes are synchronised with the original bootstrap administrator account. The dashboard uses short-lived HttpOnly, SameSite=Strict session cookies plus a CSRF header for mutations; the browser never receives the API bearer token. Dashboard accounts are stored in SQLite using PBKDF2-HMAC-SHA256 password hashes. The dashboard includes an administrator-only account management panel for creating accounts, changing roles, enabling/disabling users, deleting accounts and resetting passwords. The API also keeps an append-only account audit trail, available to administrators at `GET /api/v1/audit`. All signed-in roles can change their own password; viewer accounts are shown a read-only interface. The installer credentials bootstrap the first administrator only; subsequent logins use the account database. Roles are `admin` (full access), `operator` (PBX and device operations) and `viewer` (read-only). The API remains localhost-only; never expose port 8000 directly to an untrusted network. For remote access, place it behind a trusted HTTPS reverse proxy and enable `TOMOS_COOKIE_SECURE=true` in `/etc/tomos-sipserver/api.env` before restarting the API.

- `GET /api/v1/users` — list dashboard accounts (administrators only; password hashes are never returned).
- `POST /api/v1/users` — create an `admin`, `operator` or `viewer` account with a password of at least 12 characters.
- `PATCH /api/v1/users/{id}` — change a user's role or enable/disable the account. The last active administrator cannot be demoted or disabled.
- `DELETE /api/v1/users/{id}` — delete an account. The last active administrator and the currently signed-in account are protected.
- `POST /api/v1/users/{id}/password` — reset a user's password (administrators only).
- `POST /api/v1/auth/password` — change the signed-in user's password after verifying the current password.
- `GET /api/v1/audit?limit=50&offset=0` — list newest account audit events (admins only), with an optional exact `action` filter. Records account creation, role changes, enable/disable, deletion, administrator password resets and self-service password changes. Passwords and password hashes are never written to audit events.
- `GET /api/v1/extensions` — list extensions without exposing SIP passwords.
- `POST /api/v1/extensions` — create an extension; a generated SIP password is returned once. Body: `{"extension":"120","display_name":"Office"}`.
- `PATCH /api/v1/extensions/{extension}` — update `display_name` or `enabled`.
- `POST /api/v1/extensions/{extension}/password` — rotate the SIP password and return it once.
- `POST /api/v1/extensions/{extension}/enable` and `/disable` — enable or disable registration.
- `DELETE /api/v1/extensions/{extension}` — delete an extension.
- `POST /api/v1/devices/cisco` — add a Cisco SCCP handset and apply its chan_sccp device/line configuration. Initial model allowlist: 7940, 7960 and 7970. Body: `{"extension":"100","mac":"001122AABBCC","model":"7940","server_host":"192.168.1.10"}`. The root configuration helper generates `/etc/asterisk/tomos-sccp-devices.conf`, reloads chan_sccp, and writes `SEP<MAC>.cnf.xml` profiles under `/var/lib/tftpboot`. The XML contains the CallManager connection settings; the extension-to-line mapping is held in chan_sccp configuration. No SIP password is copied into the phone profile or managed SCCP file. TFTP serving must be configured separately on the trusted voice LAN; physical handset/firmware compatibility remains unverified.
- `GET /api/v1/devices/cisco` — list Cisco handset inventory with separate SCCP configuration, TFTP profile and hardware-test states. `sccp_config_status` is `generated` for enabled assignments, `tftp_profile_status` is `generated` (local profile generated; TFTP serving is a separate deployment step), and `hardware_test_status` remains `not_tested` until validated.
- `DELETE /api/v1/devices/cisco/{mac}` — remove a Cisco handset from inventory.
- `POST /api/v1/devices/yealink` — register a Yealink phone against an enabled extension. Supply `extension`, `mac`, `model` and `server_host`. The response displays a unique HTTP Basic provisioning password once.
- `GET /api/v1/devices/yealink` — list registered Yealink devices without provisioning secrets.
- `POST /api/v1/devices/yealink/{mac}/credentials` — rotate a device's provisioning password and immediately revoke its previous password. The replacement is returned once.
- `DELETE /api/v1/devices/yealink/{mac}` — revoke a phone's provisioning access.
- `POST /api/v1/devices/grandstream` — register a Grandstream GRP2601/2602/2603/2604 against an enabled extension and issue a one-time per-device HTTP Basic password.
- `GET /api/v1/devices/grandstream` — list registered Grandstream devices without provisioning secrets.
- `POST /api/v1/devices/grandstream/{mac}/credentials` — rotate the device provisioning password and immediately revoke the old one.
- `DELETE /api/v1/devices/grandstream/{mac}` — revoke a phone's provisioning access.
- `POST /api/v1/devices/polycom` — register a Polycom VVX SIP phone (initial allowlist: VVX150/250/300/310/350/410/450/500/501/600/601/1500) and issue a one-time per-device HTTP Basic password. Optional SIP port and UDP/TCP/TLS transport are configurable.
- `GET /api/v1/devices/polycom` — list Polycom devices without provisioning secrets.
- `POST /api/v1/devices/polycom/{mac}/credentials` — rotate a Polycom device provisioning password.
- `DELETE /api/v1/devices/polycom/{mac}` — revoke Polycom provisioning access.
- `GET /provisioning/polycom/{mac}.cfg` — serve a MAC-specific Polycom VVX XML profile over HTTPS using per-device Basic authentication.
- `GET /provisioning/grandstream/cfg{mac}.xml` — serve the Grandstream MAC-specific XML profile (lowercase MAC filename), authenticated with per-device Basic credentials over HTTPS. The XML uses Grandstream account aliases for SIP server, user ID, authentication ID/password and display name.
- `GET /provisioning/yealink/{MAC}.cfg` — serve the generated Yealink CFG file. The phone must authenticate with its per-device HTTP Basic credentials over HTTPS. Configure the phone with the provisioning directory URL (`https://HOST/provisioning/yealink/`); Yealink then requests its lowercase MAC-specific CFG filename. The generated file starts with the required `#!version:1.0.0.1` header. The initial supported model allowlist covers selected SIP-T30/T31/T33/T40/T41/T42/T43/T46/T48/T53/T54/T57/T58 models; unsupported models are rejected rather than silently receiving an unverified profile.
- `GET /api/v1/calls?limit=50&offset=0&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&number=100&disposition=ANSWERED` — read filtered call-detail records (maximum 100 per page). Date bounds are inclusive; number matches caller ID, source or destination.
- `GET /api/v1/calls/export.csv` — export matching call history as CSV (maximum 5,000 records per export). Spreadsheet formula-leading values are neutralised.

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
- The API requires a per-installation bearer token and listens only on loopback. Dashboard sessions use HttpOnly/SameSite cookies and CSRF validation; the browser does not store the API token. Dashboard login is rate-limited to five failed attempts per client address in a rolling five-minute window; a successful login clears that address's failure counter.
- SIP passwords are not returned by list or update operations; they are returned only at creation/reset.
- Keep administration on the LAN/VPN and use HTTPS with a trusted reverse proxy for dashboard and phone provisioning access; enable secure cookies when TLS is configured. Configure the reverse proxy to forward the `/provisioning/` path to the API and set `X-Forwarded-Proto: https`.
- Never expose AMI/ARI directly to browsers or untrusted networks.
- Call audio recording is disabled; only CDR metadata (caller ID, extension numbers, timestamps, durations and outcomes) is stored. Set an appropriate retention period before production use.
- A private PBX does not guarantee emergency calling; verify provider emergency-call support and keep an independent means of calling emergency services.

## Development roadmap

1. **Foundation (passed CI):** Debian 13 source-build installer, Asterisk, PJSIP, internal extensions, systemd and CI.
2. **Extension API (current):** authenticated FastAPI CRUD, SQLite persistence, password rotation, enable/disable and configuration rollback.
3. **Dashboard (initial release):** password login, session/CSRF protection, extension listing, create, enable/disable, delete and one-time password reset display. Multi-user backend now supports administrator, operator and read-only roles, PBKDF2 password hashes, account enable/disable, password resets and last-admin safeguards; dashboard account-management screens remain to be added. Device registration, generic SIP setup notes and searchable call history, date/number/result filters and CSV export are now included.
4. **Vendor provisioning (in progress):** Yealink, Grandstream and Polycom MAC-specific provisioning workflows are implemented with per-device credentials. Cisco SCCP driver, device inventory, chan_sccp device/line sections and conservative `SEP<MAC>.cnf.xml` profile generation are integrated. Configure a restricted TFTP service on the voice LAN and verify the exact firmware on physical handsets before claiming device compatibility.
5. Browser phone: SIP.js, WSS, SRTP and persistent in-dashboard call controls.
6. Home features: voicemail, call history, groups, forwarding, DND, backups and restore.
7. Hardening/release: security review, hardware compatibility matrix and upgrade testing.

## Tests

GitHub Actions runs Python renderer and API tests, Bash syntax/ShellCheck checks, builds the pinned AI3I/chan_sccp driver against Asterisk 22, and runs a Debian 13 integration job that starts Asterisk, verifies PJSIP endpoints and dialplan, confirms `chan_sccp.so` loads and exposes `sccp show devices`, checks the SQLite CDR backend records a generated answered call, and verifies that record through the authenticated call-history API. This does not yet prove two-way audio between physical phones or call records from physical handsets.

