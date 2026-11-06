#!/usr/bin/env bash
set -Eeuo pipefail
log() { printf '[TomOS SIP] %s\n' "$*"; }
die() { printf '[TomOS SIP] ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${EUID}" -eq 0 ]] || die "Run this installer as root (for example: sudo ./install.sh)."
[[ -r /etc/os-release ]] || die "Cannot identify operating system."
. /etc/os-release
[[ "${ID:-}" == "debian" && "${VERSION_ID:-}" == "13" ]] || die "This installer supports Debian 13 only; detected ${PRETTY_NAME:-unknown}."
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
RENDERER="$REPO_ROOT/scripts/render-config.py"
CHECKER="$REPO_ROOT/scripts/tomos-sipserver-check"
UNIT="$REPO_ROOT/systemd/tomos-sipserver.service"
[[ -f "$RENDERER" && -f "$CHECKER" && -f "$UNIT" ]] || die "Run install.sh from a complete TomOS repository checkout."
command -v apt-get >/dev/null || die "apt-get is required."
export DEBIAN_FRONTEND=noninteractive
log "Installing Asterisk and required tools."
apt-get update
apt-get install -y --no-install-recommends asterisk python3 openssl ca-certificates
ASTERISK_DIR=/etc/asterisk
BACKUP_DIR="$ASTERISK_DIR/tomos-backups/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0750 "$BACKUP_DIR"
for name in pjsip.conf extensions.conf; do
  if [[ -f "$ASTERISK_DIR/$name" ]]; then cp -a "$ASTERISK_DIR/$name" "$BACKUP_DIR/$name"; fi
done
log "Existing Asterisk configuration backed up to $BACKUP_DIR."
EXT100_SECRET="$(openssl rand -hex 24)"
EXT101_SECRET="$(openssl rand -hex 24)"
install -d -m 0750 "$ASTERISK_DIR"
python3 "$RENDERER" --output-dir "$ASTERISK_DIR" \
  --extension "100:$EXT100_SECRET" --extension "101:$EXT101_SECRET"
install -D -m 0755 "$CHECKER" /usr/local/sbin/tomos-sipserver-check
install -D -m 0644 "$UNIT" /etc/systemd/system/tomos-sipserver.service
if id asterisk >/dev/null 2>&1; then
  chown root:asterisk "$ASTERISK_DIR/pjsip.conf" "$ASTERISK_DIR/extensions.conf"
  chmod 0640 "$ASTERISK_DIR/pjsip.conf" "$ASTERISK_DIR/extensions.conf"
fi
systemctl daemon-reload
systemctl enable asterisk.service
systemctl restart asterisk.service
if ! systemctl is-active --quiet asterisk.service; then
  log "Asterisk failed to start; restoring previous configuration."
  for name in pjsip.conf extensions.conf; do
    if [[ -f "$BACKUP_DIR/$name" ]]; then cp -a "$BACKUP_DIR/$name" "$ASTERISK_DIR/$name"; fi
  done
  systemctl restart asterisk.service || true
  die "Asterisk did not start. Previous configuration was restored where available."
fi
systemctl enable --now tomos-sipserver.service
if ! /usr/local/sbin/tomos-sipserver-check; then
  log "TomOS validation failed; restoring previous configuration."
  for name in pjsip.conf extensions.conf; do
    if [[ -f "$BACKUP_DIR/$name" ]]; then cp -a "$BACKUP_DIR/$name" "$ASTERISK_DIR/$name"; fi
  done
  systemctl restart asterisk.service || true
  systemctl disable --now tomos-sipserver.service || true
  die "Asterisk started, but TomOS endpoint validation failed. Previous configuration was restored where available."
fi
CREDENTIAL_FILE=/root/tomos-sip-credentials.txt
umask 077
{
  printf 'TomOS Home SIP Server initial extensions\n'
  printf 'Generated: %s\n\n' "$(date -u +%FT%TZ)"
  printf 'Extension 100 password: %s\n' "$EXT100_SECRET"
  printf 'Extension 101 password: %s\n' "$EXT101_SECRET"
  printf '\nStore these credentials securely. This file is readable by root only.\n'
} > "$CREDENTIAL_FILE"
chmod 0600 "$CREDENTIAL_FILE"
log "Installation completed."
log "Asterisk: $(systemctl is-active asterisk.service)"
log "TomOS check service: $(systemctl is-active tomos-sipserver.service)"
log "Initial SIP credentials saved to $CREDENTIAL_FILE (root-only)."
log "SIP listens on UDP 5060; RTP uses UDP 10000-20000. Keep these ports on the trusted LAN/VPN only."
log "No router port-forwarding or public internet exposure has been configured."
