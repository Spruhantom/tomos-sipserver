# TomOS Home SIP Server

A self-hosted home telephone system for Debian 13, built around Asterisk with a web administration panel and an integrated browser softphone.

## Current status

The first Debian 13 foundation is now committed: an Asterisk installer, generated PJSIP endpoints 100/101, a basic internal dialplan, a systemd health-check unit and GitHub Actions tests. The web interface, WebRTC client, Cisco SCCP and Yealink provisioning are future stages and are not implemented yet.

## Install (Debian 13 only)

Clone this repository on a clean Debian 13 host, then run:

```bash
sudo ./install.sh
```

The installer:
- Checks for Debian 13 and root privileges.
- Installs Debian's Asterisk package and required utilities.
- Backs up existing `/etc/asterisk/pjsip.conf` and `extensions.conf` before replacing them with the initial TomOS configuration. **Do not run on an existing PBX without reviewing the backup/restore implications.**
- Creates extensions 100 and 101 with cryptographically random passwords.
- Stores the initial credentials in `/root/tomos-sip-credentials.txt` (mode 0600).
- Enables Asterisk and a TomOS systemd health-check unit.
- Verifies that Asterisk starts and both endpoints are loaded.

The initial setup listens on UDP 5060 and uses the Asterisk RTP range UDP 10000-20000. Keep this on a trusted home LAN or VPN. The installer does not configure router port forwarding or expose SIP to the public internet. It does not configure a SIP trunk, TLS, WebRTC, a firewall or external telephone-number service.

Extensions can call one another by dialling 100 or 101. Dial `*65` for the Asterisk echo test (where the demo audio prompt is installed).

## Planned technology

| Layer | Technology |
|---|---|
| OS | Debian 13 |
| PBX engine | Asterisk |
| SIP | PJSIP |
| Cisco SCCP | chan-sccp, separately built and compatibility-tested |
| API | Python / FastAPI |
| Web UI | React / TypeScript |
| Browser calling | SIP.js, WebRTC and secure WebSockets |
| Initial database | SQLite |
| Reverse proxy | Nginx |
| Remote access | WireGuard VPN (optional) |
| Service supervision | systemd |

## Planned features

- Web administration dashboard and permanent integrated webphone.
- Extension creation, editing, credential rotation and device assignment.
- Cisco SCCP phone support and model-specific provisioning.
- Yealink HTTPS provisioning.
- Voicemail, call history, forwarding, DND, ring groups, transfers and conferences.
- Optional SIP trunk connectivity for external calls.
- Backups, restore, upgrades and monitoring.

## Security principles

- No public SIP, AMI, ARI, TFTP or admin-panel exposure by default.
- Strong, unique extension credentials; no default passwords.
- Keep administration on the LAN/VPN and use HTTPS when the web UI is introduced.
- Never expose AMI/ARI directly to browsers or untrusted networks.
- Call recording disabled by default, with consent and retention controls if added.
- A private PBX does not guarantee emergency calling; verify any provider's emergency-call support and keep an independent means of calling emergency services.

## Development roadmap

1. **Foundation (current):** Debian 13 installer, Asterisk, PJSIP, internal extensions, systemd health check and CI.
2. PBX core: safe extension lifecycle, configuration validation and rollback.
3. Administration API/UI: authentication, dashboard, extension/device management and audit trail.
4. Provisioning: Yealink HTTPS profiles and Cisco SCCP model-specific provisioning.
5. Browser phone: SIP.js, WSS, SRTP and persistent in-dashboard call controls.
6. Home features: voicemail, call history, groups, forwarding, DND, backups and restore.
7. Hardening/release: security review, hardware compatibility matrix and upgrade testing.

## Tests

GitHub Actions runs Python renderer tests, Bash syntax/ShellCheck checks, and a Debian 13 job that installs Asterisk, renders the test configuration, starts Asterisk in the foreground and queries PJSIP endpoints and the internal dialplan.

Hardware tests are still required before claiming a specific Cisco or Yealink model is supported.
