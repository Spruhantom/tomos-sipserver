# TomOS Home SIP Server

A self-hosted home telephone system for Debian 13, built around Asterisk with a web administration panel and an integrated browser softphone.

> Status: planning / foundation. This repository currently contains the project specification and scaffolding plan; do not describe the PBX as production-ready until the implementation and integration tests are complete.

## Goals

- Run on Debian 13 (amd64 initially).
- Provide SIP calling through Asterisk PJSIP.
- Provide Cisco SCCP support through a separately packaged and tested SCCP channel driver (evaluate AI3I/chan-sccp compatibility against the selected Asterisk release).
- Provision supported Yealink SIP phones over authenticated HTTPS.
- Provision compatible Cisco SCCP phones using model-appropriate provisioning (typically TFTP/HTTP, depending on model and firmware).
- Create and manage extensions from the web interface.
- Provide an integrated browser SIP client using SIP.js and WebRTC.
- Keep the management interface and telephony services private to the LAN/VPN by default.
- Support optional SIP trunk connectivity without requiring it for internal calls.

## Planned stack

| Layer | Technology |
|---|---|
| OS | Debian 13 |
| PBX engine | Asterisk (pin and test a supported release) |
| SIP | PJSIP |
| Cisco SCCP | chan-sccp, built against the selected Asterisk ABI |
| API | Python 3 / FastAPI |
| Web UI | React / TypeScript |
| Browser calling | SIP.js, WebRTC, secure WebSocket signalling |
| Database | SQLite for initial single-home-server deployment |
| Reverse proxy | Nginx |
| Remote access | WireGuard VPN (optional; not an internet-exposed SIP listener) |
| Service supervision | systemd |

Asterisk manages the call engine. The API is the control plane and must not expose AMI/ARI directly to browsers or untrusted networks. Use a narrow local service adapter with least-privilege credentials.

## Target architecture

- Browser -> HTTPS -> Nginx -> FastAPI.
- Browser softphone -> WSS/SIP.js -> Asterisk PJSIP WebRTC endpoint; media uses WebRTC/SRTP.
- SIP phones -> PJSIP -> shared Asterisk dialplan.
- Cisco SCCP phones -> chan-sccp -> shared Asterisk dialplan.
- API -> validated provisioning templates and controlled Asterisk configuration/reload operations.
- Yealink devices -> authenticated HTTPS provisioning.
- Cisco device provisioning -> per-model template and documented protocol/firmware requirements.
- SQLite stores users, extension metadata, device inventory, call metadata and provisioning audit events. Secrets must be hashed/encrypted as appropriate and never logged.

## Planned features

### PBX
- Internal extension-to-extension calling.
- PJSIP registrations and endpoint authentication.
- Optional SIP trunk configuration, incoming/outgoing routes and explicit outbound permissions.
- Voicemail, forwarding, DND, ring groups, call pickup, transfers and conferences.
- Call detail records and configurable retention.
- Optional call recording disabled by default; provide consent, retention and deletion controls.

### Extensions
- Create, edit, disable, delete and bulk-create extensions.
- Allocate unique extension numbers and validate collisions.
- Generate high-entropy credentials; show secrets only once and support rotation.
- Assign one or more devices and a supported protocol.
- Apply permissions, voicemail and forwarding settings.
- Provisioning must be transactional: validate desired state, back up current configuration, apply, validate/reload, and roll back on failure.
- Never interpolate untrusted values directly into Asterisk config or shell commands.

### Device provisioning
- Device discovery is advisory only; require an administrator to approve and bind a MAC address before provisioning.
- Yealink model profiles, account settings, time/NTP, programmable keys, BLF and firmware metadata.
- Cisco SCCP model profiles, MAC binding, line assignment and compatible provisioning files.
- Firmware is not redistributed by this project. Users supply firmware they are entitled to use.
- Use per-device tokens or equivalent authentication, HTTPS where supported, and LAN/VPN access controls.
- Validate MAC address, model and template schema; audit configuration downloads and administrative changes.

### Web softphone
- Permanent telephone entry in the dashboard navigation.
- SIP.js registration against a dedicated PJSIP WebRTC endpoint.
- Incoming/outgoing calls, answer/reject, mute, hold, transfer, contacts and call history.
- Persistent compact active-call panel while navigating dashboard pages.
- Desktop notifications only after permission; microphone selection and browser permissions.
- Require HTTPS and secure signalling/media. Browser sessions must be revocable.
- Do not store plaintext SIP passwords in localStorage; use short-lived session-bound credentials or a secure provisioning flow.

## Network and security defaults

- No public SIP, AMI, ARI, TFTP or admin-panel exposure by default.
- Admin UI bound to LAN/VPN behind HTTPS; initial setup must create an administrator securely.
- Firewall allowlist for necessary LAN/VPN ports; document SIP/RTP/WebRTC port ranges.
- Strong per-extension credentials, rate limiting, audit logs, least privilege and automatic backup.
- No default credentials or universal sample secrets.
- WireGuard is the recommended remote-access path.
- SIP trunk outbound routes and premium-rate/international calling are disabled until explicitly configured.
- Emergency calling is not guaranteed by a private PBX; document provider requirements and provide an independent means of reaching emergency services.
- Call recording is off by default and must have configurable consent/retention controls.

## Repository layout (target)

```text
backend/                 FastAPI, auth, extension and device APIs
frontend/                React dashboard and integrated webphone
asterisk/                PJSIP, dialplan and WebRTC templates
sccp/                    chan-sccp build/integration metadata
provisioning/            Yealink and Cisco model templates
installer/               Debian 13 install, upgrade and uninstall
packaging/               Debian package/systemd/Nginx definitions
docs/                    Architecture, operations, security and device guides
tests/                   Unit, integration and hardware-compatibility tests
.github/workflows/       CI workflows
```

## Development roadmap

1. **Foundation:** Debian 13 dependency checks, pinned Asterisk build/install strategy, systemd units, secure first-run setup, packaging and CI.
2. **PBX core:** PJSIP endpoints, dialplan, extension lifecycle, internal call tests and configuration rollback.
3. **Administration API/UI:** authentication, dashboard, extension/device management and audit trail.
4. **Provisioning:** Yealink HTTPS provisioning; Cisco SCCP driver build and model-specific provisioning; validate against actual supported phone models.
5. **Browser phone:** dedicated WebRTC endpoint, SIP.js, WSS, SRTP, persistent call controls and browser integration tests.
6. **Home features:** voicemail, call history, groups, forwarding, DND, backups and restore tests.
7. **Hardening/release:** security review, Debian 13 clean-install tests, upgrade/rollback tests, documented supported hardware matrix and release process.

## Minimum acceptance criteria

- Clean Debian 13 installation completes without interactive package-manager surprises.
- A generated extension can register from a test SIP client and place a two-way-audio internal call.
- Invalid extension/device inputs cannot alter generated configuration.
- A failed configuration apply restores the last known good configuration.
- Yealink provisioning is verified against named models and firmware versions.
- SCCP is only declared supported after the selected chan-sccp/Asterisk combination builds and registers named Cisco models.
- Browser softphone can register, call, receive calls and pass two-way audio over HTTPS/WSS/WebRTC.
- AMI/ARI and management endpoints are not reachable from untrusted networks.
- Backup/restore and upgrade/rollback have automated tests.
- CI reports actual test outcomes; documentation distinguishes mocked tests from real hardware tests.

## Development status

No PBX implementation has been completed merely by adding this specification. The repository should grow incrementally with tested code and CI evidence. Model-specific Cisco and Yealink compatibility must be reported explicitly rather than assumed.
