# TomOS Home SIP Server

A self-hosted home telephone system for Debian 13, built around Asterisk with a web administration panel and an integrated browser softphone.

## Current status

The Debian 13 foundation is under development. The repository contains a source-build installer for Asterisk 22 LTS, generated PJSIP endpoints 100/101, a basic internal dialplan, systemd units and CI that compiles Asterisk and checks configuration loading. The web interface, WebRTC client, Cisco SCCP and Yealink provisioning are future stages.

## Install (Debian 13 only)

Clone this repository on a clean Debian 13 host, then run:

```bash
sudo ./install.sh
```

The installer builds the pinned Asterisk 22.12.0 LTS source release because Debian 13 stable does not currently publish the Asterisk PBX package. It verifies the source archive against the SHA-256 file from the official Asterisk download site before compiling.

It then:
- Installs build dependencies and compiles Asterisk with bundled PJProject.
- Creates the `asterisk` service account and required runtime directories.
- Backs up existing Asterisk configuration files before installing the TomOS base configuration.
- Creates extensions 100 and 101 with cryptographically random passwords.
- Stores initial credentials in `/root/tomos-sip-credentials.txt` (mode 0600).
- Installs and enables the Asterisk and TomOS systemd units.
- Checks that Asterisk starts and both PJSIP endpoints load.

**Important:** this is an initial PBX foundation, not yet a finished home PBX. Run it on a clean Debian 13 machine or VM. Existing Asterisk configuration files are backed up, but the installer replaces the active base configuration files and should not be run over another live PBX without reviewing the migration first.

The initial setup listens on UDP 5060 and uses RTP UDP 10000-20000. Keep this on a trusted home LAN or VPN. No router port-forwarding, public SIP exposure, firewall rules, SIP trunk, TLS, WebRTC or web interface is configured yet. Extensions 100 and 101 can call one another. Dial `*65` for the Asterisk echo test.

## Technology

| Layer | Technology |
|---|---|
| OS | Debian 13 |
| PBX engine | Asterisk 22 LTS (source build) |
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

## Security defaults

- No public SIP, AMI, ARI, TFTP or admin-panel exposure by default.
- Strong, unique extension credentials; no default passwords.
- Keep administration on the LAN/VPN and use HTTPS when the web UI is introduced.
- Never expose AMI/ARI directly to browsers or untrusted networks.
- Call recording disabled by default, with consent and retention controls if added.
- A private PBX does not guarantee emergency calling; verify any provider's emergency-call support and keep an independent means of calling emergency services.

## Development roadmap

1. **Foundation (current):** Debian 13 source-build installer, Asterisk, PJSIP, internal extensions, systemd and CI.
2. PBX core: safe extension lifecycle, configuration validation and rollback.
3. Administration API/UI: authentication, dashboard, extension/device management and audit trail.
4. Provisioning: Yealink HTTPS profiles and Cisco SCCP model-specific provisioning.
5. Browser phone: SIP.js, WSS, SRTP and persistent in-dashboard call controls.
6. Home features: voicemail, call history, groups, forwarding, DND, backups and restore.
7. Hardening/release: security review, hardware compatibility matrix and upgrade testing.

## Tests

GitHub Actions runs Python renderer tests, Bash syntax/ShellCheck checks, and a Debian 13 job that downloads and verifies the pinned Asterisk source, compiles it, starts the PBX process and checks that PJSIP endpoints 100/101 and the internal dialplan load. This verifies the PBX process and configuration; it does not yet prove two-way audio between physical phones.

Hardware tests are still required before claiming a specific Cisco or Yealink model is supported.
