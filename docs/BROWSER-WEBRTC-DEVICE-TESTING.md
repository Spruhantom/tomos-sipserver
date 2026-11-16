# Browser phone: real-device validation

This procedure validates the SIP.js browser phone after the PBX and dashboard have been deployed. It deliberately separates automated signalling checks from tests that require real microphones, speakers and two physical clients.

## Before testing

- Use a trusted HTTPS certificate for the dashboard hostname and a trusted TLS certificate whose SAN includes the PBX hostname used by the browser.
- Replace the installer's short-lived self-signed bootstrap certificate. Restart Asterisk after installing the certificate and key.
- Set `TOMOS_SIP_DOMAIN` and `TOMOS_WSS_URL=wss://PBX-HOST:8089/ws` in `/etc/tomos-sipserver/api.env` if the PBX hostname differs from the dashboard hostname, then restart `tomos-sipserver-api.service`.
- Keep WSS TCP 8089 and RTP UDP 10000-20000 reachable only over the trusted LAN/VPN. Do not port-forward SIP, SCCP, WSS or RTP from the public internet.
- Create two enabled SIP/PJSIP extensions and two dashboard users. Assign one unique extension to each user in User Management. Do not use a viewer account for the browser phone.
- Confirm both users can sign in to the dashboard over HTTPS. The browser phone should obtain its own extension configuration automatically; never copy WebRTC passwords into test notes.

## Automated WSS preflight

Run from a machine on the same network as the browser clients:

```bash
python3 scripts/check-webrtc-endpoint.py PBX-HOST
```

For an organisation's private CA:

```bash
python3 scripts/check-webrtc-endpoint.py PBX-HOST --ca-file /path/to/ca-chain.pem
```

A pass confirms the certificate chain and hostname are trusted and Asterisk accepts a valid WebSocket upgrade on `/ws`. It does **not** prove SIP registration, media negotiation, ICE reachability or two-way audio.

## Two-device test sequence

Use two separate devices (for example, Android Chrome and desktop Chrome/Firefox), each signed in as a different assigned dashboard user. Keep both on the same trusted LAN for the first run; repeat over the approved VPN only after LAN tests pass.

| ID | Test | Expected result | Result |
|---|---|---|---|
| WEB-01 | Open dashboard over HTTPS | No certificate warning; dashboard loads | Not run |
| WEB-02 | Sign in as assigned user A | Browser phone shows the assigned extension without manual SIP credentials | Not run |
| WEB-03 | Sign in as assigned user B on second device | Second extension provisions independently | Not run |
| WEB-04 | Connect/register both browser phones | Both show registered/connected state | Not run |
| WEB-05 | Deny microphone permission, then place a call | Browser reports the permission failure clearly; UI remains recoverable | Not run |
| WEB-06 | Grant microphone permission and call B from A | B rings; incoming caller/extension is shown | Not run |
| WEB-07 | Answer on B and speak in both directions | Clear two-way audio; no one-way or silent audio | Not run |
| WEB-08 | Mute/unmute A during the call | B hears silence while A is muted and audio resumes after unmute | Not run |
| WEB-09 | Hang up from A, then repeat and hang up from B | Both call UIs return to idle; media tracks are released | Not run |
| WEB-10 | Decline an incoming call on B | A receives call termination; both phones return to idle | Not run |
| WEB-11 | Reload one dashboard during idle state | Phone can provision/register again without stale call state | Not run |
| WEB-12 | Remove B's extension assignment and refresh | B no longer receives WebRTC credentials or registers | Not run |
| WEB-13 | Repeat with the second supported browser/device | Same call and media behaviour | Not run |

Do not test calls to public numbers, emergency numbers or external destinations: this release has no configured trunk and is for internal extension calls only.

## Record a test run

For each run, record date/time (with timezone), TomOS commit, Asterisk version, device model/OS, browser name/version, network type (LAN or VPN), test IDs passed/failed, and any browser console or Asterisk log error. Redact public IPs if not needed, usernames, extension secrets, cookies, bearer tokens, SDP containing network details, and all passwords before sharing logs.

Useful server-side checks:

```bash
sudo asterisk -rx 'pjsip show contacts'
sudo asterisk -rx 'pjsip show endpoints'
sudo asterisk -rx 'http show status'
sudo journalctl -u asterisk --since '10 minutes ago'
sudo journalctl -u tomos-sipserver-api --since '10 minutes ago'
```

## Acceptance criteria

A device/browser combination is accepted only when both phones register, incoming and outgoing internal calls complete, two-way audio works, mute/decline/hang-up work, and no stale registration or media remains after ending/reloading. Mark unsupported combinations as unverified rather than assuming compatibility.

The repository CI cannot perform physical-device microphone/audio tests. Those rows must be completed on the actual target devices and network before describing browser calling as hardware-validated.
