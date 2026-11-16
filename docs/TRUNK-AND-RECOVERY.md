# TomOS SIP Server: trunk and recovery development

This work is being delivered in stages because external calling changes the PBX's security boundary.

## Reliability and recovery

The first recovery utility is `scripts/tomos-sipserver-backup`:

```bash
sudo python3 scripts/tomos-sipserver-backup create
sudo python3 scripts/tomos-sipserver-backup verify /var/backups/tomos-sipserver/FILE.tar.gz
```

It creates root-only archives, uses SQLite's online backup API for the extension and CDR databases, records SHA-256 checksums, and validates archive member names before accepting an archive. A daily systemd timer is installed by the package. Backups contain SIP credentials, dashboard secrets and call metadata. They are **not encrypted** by this utility; move them only to encrypted storage and keep off-host copies protected. Restore automation is deliberately not enabled until a separately tested restore procedure can stop services, preserve rollback copies and validate the restored database/configuration as a unit.

## SIP trunk foundation

`tomos_sipserver/trunk.py` adds strict validation and PJSIP registration/authentication rendering for one provider trunk. The default is disabled. Hostnames, ports, transport, credentials and optional E.164 caller ID are validated; CR/LF configuration injection is rejected.

This first stage does not activate external routing or expose trunk credentials in the dashboard. A provider-specific profile and administrator-controlled secret storage/rotation flow must be completed and tested before routing is enabled. UK 999/112 handling must remain an explicit emergency policy, never a generic outbound pattern. External calling must remain disabled until provider details, caller-ID authorisation, inbound routing, firewall/media policy and emergency-call arrangements are reviewed.

## Validation boundary

CI can validate Python logic, generated configuration and archive integrity. It cannot validate a real provider's registration, caller-ID acceptance, inbound DID delivery, NAT/media path, billing or emergency-service handling. Those require a test account and a controlled deployment.


## Root-only trunk credential storage (foundation)

The current development branch also includes `tomos_sipserver.trunk_store`.
It stores a validated provider hostname, account name, secret, port, transport
and optional E.164 caller ID in `/etc/tomos-sipserver/trunk.json` using an
atomic replacement, root-only access and mode 0600. Secret rotation replaces
the stored secret without exposing it through the status summary. The status
view intentionally omits the secret.

**Important:** saving or rotating credentials always writes the trunk as
disabled. This storage layer does not yet activate provider registration,
outbound routes or inbound DID handling. The live apply path still needs
provider transport definitions, certificate validation for TLS, provider
interop tests and a reviewed emergency-call policy before activation can be
implemented. Existing internal calling is unchanged.


## Dashboard trunk credential management

The administrator dashboard now provides a trunk settings panel backed by four admin-only API operations: read safe status, save credentials, rotate the provider secret, and disable the trunk. The API calls a fixed-purpose root helper through a narrowly scoped sudoers entry; secrets are sent on standard input rather than command-line arguments and are never included in API responses or helper errors. The root helper stores credentials through the atomic root-only trunk store. The password field is never pre-populated, and a saved trunk remains disabled.

This is credential management only. It does not yet create provider transport definitions, validate provider TLS certificates, register to a live provider, install outbound routes, or enable external calls. Selecting TLS in the dashboard records the preference only; it is not an indication that TLS is configured or active. Provider-specific interoperability still requires the provider's documented registrar, proxy, authentication and certificate requirements.
