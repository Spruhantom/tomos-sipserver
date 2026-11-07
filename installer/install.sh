#!/usr/bin/env bash
set -Eeuo pipefail
log() { printf '[TomOS SIP] %s\n' "$*"; }
die() { printf '[TomOS SIP] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || die "Run as root: sudo ./install.sh"
[[ -r /etc/os-release ]] || die "Cannot identify operating system."
. /etc/os-release
[[ "${ID:-}" == "debian" && "${VERSION_ID:-}" == "13" ]] || die "Debian 13 only. Detected: ${PRETTY_NAME:-unknown}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
for required in scripts/render-config.py scripts/tomos-sipserver-check scripts/tomos-sipserver-apply-config tomos_sipserver/api.py tomos_sipserver/db.py dashboard/index.html dashboard/app.js dashboard/style.css requirements.txt systemd/asterisk.service systemd/tomos-sipserver.service systemd/tomos-sipserver-api.service sudoers/tomos-sipserver-api asterisk/asterisk.conf asterisk/modules.conf asterisk/logger.conf asterisk/rtp.conf asterisk/cdr.conf asterisk/cdr_sqlite3_custom.conf asterisk/sccp.conf; do
  [[ -f "$REPO_ROOT/$required" ]] || die "Missing project file: $required (use a complete repository checkout)."
done

export DEBIAN_FRONTEND=noninteractive
ASTERISK_VERSION=22.12.0
SCCP_COMMIT=9591e4583c0c3b682d30d253ce77d596f040acf8
SCCP_SOURCE_DIR=/usr/local/src/tomos-chan-sccp
SOURCE_URL="https://github.com/asterisk/asterisk/releases/download/${ASTERISK_VERSION}/asterisk-${ASTERISK_VERSION}.tar.gz"
EXPECTED_SHA256=f8b2ba98aac6c4ff3376bc0a2868bea6ed577ca684efb7ec739ae6e8ce6e35e2
BUILD_ROOT=/usr/local/src/tomos-asterisk
ARCHIVE="$BUILD_ROOT/asterisk-${ASTERISK_VERSION}.tar.gz"
SOURCE_DIR="$BUILD_ROOT/asterisk-${ASTERISK_VERSION}"
BUILD_JOBS="${TOMOS_BUILD_JOBS:-2}"
[[ "$BUILD_JOBS" =~ ^[1-9][0-9]?$ ]] || die "TOMOS_BUILD_JOBS must be a positive integer from 1 to 99."

log "Installing Asterisk and API dependencies."
apt-get update
apt-get install -y --no-install-recommends build-essential git wget curl ca-certificates pkg-config python3 python3-venv sudo openssl \
  libxml2-dev libxslt1-dev gettext libncurses-dev uuid-dev libsqlite3-dev libssl-dev libedit-dev libjansson-dev \
  libcurl4-openssl-dev libnewt-dev libspandsp-dev libsrtp2-dev

install -d -m 0755 "$BUILD_ROOT"
if [[ ! -s "$ARCHIVE" ]]; then
  log "Downloading Asterisk ${ASTERISK_VERSION} LTS source."
  curl --fail --location --retry 3 --retry-delay 2 "$SOURCE_URL" --output "$ARCHIVE"
fi
printf '%s  %s\n' "$EXPECTED_SHA256" "$ARCHIVE" | sha256sum --check --status || die "Asterisk source checksum verification failed."

if [[ ! -d "$SOURCE_DIR" ]]; then
  tar -xzf "$ARCHIVE" -C "$BUILD_ROOT"
fi
cd "$SOURCE_DIR"
log "Installing any additional Asterisk prerequisites."
./contrib/scripts/install_prereq install
log "Configuring and compiling Asterisk ${ASTERISK_VERSION} with bundled PJProject."
./configure --with-pjproject-bundled --with-jansson-bundled
make menuselect.makeopts
make -j"$BUILD_JOBS"
make install

log "Building pinned chan_sccp driver for Asterisk ${ASTERISK_VERSION}."
if [[ ! -d "$SCCP_SOURCE_DIR/.git" ]]; then
  git clone --no-checkout https://github.com/AI3I/chan_sccp.git "$SCCP_SOURCE_DIR"
fi
git -C "$SCCP_SOURCE_DIR" fetch --depth 1 origin "$SCCP_COMMIT"
git -C "$SCCP_SOURCE_DIR" checkout --detach "$SCCP_COMMIT"
cd "$SCCP_SOURCE_DIR"
./configure --with-asterisk=/usr
make -j"$BUILD_JOBS"
make install
cd "$SOURCE_DIR"
ldconfig

install -d -m 0750 /etc/asterisk/tomos-backups
BACKUP_DIR="/etc/asterisk/tomos-backups/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0750 "$BACKUP_DIR"
for name in asterisk.conf modules.conf logger.conf rtp.conf cdr.conf cdr_sqlite3_custom.conf pjsip.conf extensions.conf sccp.conf; do
  if [[ -f "/etc/asterisk/$name" ]]; then cp -a "/etc/asterisk/$name" "$BACKUP_DIR/$name"; fi
done
log "Existing Asterisk configuration backed up to $BACKUP_DIR."

make samples
make config
ldconfig

if ! getent group asterisk >/dev/null; then groupadd --system asterisk; fi
if ! id asterisk >/dev/null 2>&1; then
  useradd --system --gid asterisk --home-dir /var/lib/asterisk --create-home --shell /usr/sbin/nologin asterisk
fi
install -d -o asterisk -g asterisk -m 0750 /var/lib/asterisk /var/log/asterisk /var/spool/asterisk /var/spool/asterisk/voicemail /var/lib/asterisk/agi-bin
install -d -o asterisk -g asterisk -m 0755 /run/asterisk

# Install the unprivileged API and its fixed-purpose root configuration helper.
if ! id tomos >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/tomos-sipserver --create-home --shell /usr/sbin/nologin tomos
fi
install -d -o tomos -g tomos -m 0700 /var/lib/tomos-sipserver
getent group tomos-cdr >/dev/null || groupadd --system tomos-cdr
gpasswd --delete asterisk tomos >/dev/null 2>&1 || true
usermod --append --groups tomos-cdr asterisk
usermod --append --groups tomos-cdr tomos
chgrp tomos-cdr /var/lib/tomos-sipserver
chmod 0710 /var/lib/tomos-sipserver
install -d -o root -g tomos-cdr -m 2770 /var/lib/tomos-sipserver/cdr
install -d -o root -g root -m 0755 /opt/tomos-sipserver/app
rm -rf /opt/tomos-sipserver/app/tomos_sipserver
cp -a "$REPO_ROOT/tomos_sipserver" /opt/tomos-sipserver/app/
install -m 0644 "$REPO_ROOT/scripts/render-config.py" /opt/tomos-sipserver/app/render-config.py
install -d -m 0755 /opt/tomos-sipserver/app/dashboard
cp -a "$REPO_ROOT/dashboard/." /opt/tomos-sipserver/app/dashboard/
chown -R root:root /opt/tomos-sipserver
python3 -m venv /opt/tomos-sipserver/venv
/opt/tomos-sipserver/venv/bin/pip install --disable-pip-version-check --requirement "$REPO_ROOT/requirements.txt"

EXT100_SECRET="$(openssl rand -hex 24)"
EXT101_SECRET="$(openssl rand -hex 24)"
PYTHONPATH=/opt/tomos-sipserver/app /opt/tomos-sipserver/venv/bin/python -m tomos_sipserver.db --database /var/lib/tomos-sipserver/extensions.db \
  --seed-extension "100:$EXT100_SECRET" --seed-extension "101:$EXT101_SECRET"
# Keep existing credentials on upgrades so the database and active SIP config stay aligned.
EXT100_SECRET="$(/opt/tomos-sipserver/venv/bin/python -c "import sqlite3; print(sqlite3.connect('/var/lib/tomos-sipserver/extensions.db').execute('SELECT password FROM extensions WHERE extension=?', ('100',)).fetchone()[0])")"
EXT101_SECRET="$(/opt/tomos-sipserver/venv/bin/python -c "import sqlite3; print(sqlite3.connect('/var/lib/tomos-sipserver/extensions.db').execute('SELECT password FROM extensions WHERE extension=?', ('101',)).fetchone()[0])")"
chown tomos:tomos /var/lib/tomos-sipserver/extensions.db
chmod 0600 /var/lib/tomos-sipserver/extensions.db

install -d -o root -g tomos -m 0750 /etc/tomos-sipserver
API_TOKEN="$(openssl rand -hex 32)"
ADMIN_PASSWORD="$(openssl rand -hex 24)"
printf 'TOMOS_API_TOKEN=%s\nTOMOS_ADMIN_PASSWORD=%s\n' "$API_TOKEN" "$ADMIN_PASSWORD" > /etc/tomos-sipserver/api.env
chown root:tomos /etc/tomos-sipserver/api.env
chmod 0640 /etc/tomos-sipserver/api.env
printf '%s\n' "$API_TOKEN" > /root/tomos-sip-api-token.txt
chmod 0600 /root/tomos-sip-api-token.txt
printf '%s\n' "$ADMIN_PASSWORD" > /root/tomos-sip-dashboard-password.txt
chmod 0600 /root/tomos-sip-dashboard-password.txt
install -D -o root -g root -m 0755 "$REPO_ROOT/scripts/tomos-sipserver-apply-config" /usr/local/sbin/tomos-sipserver-apply-config
install -D -o root -g root -m 0755 "$REPO_ROOT/scripts/tomos-sipserver-status" /usr/local/sbin/tomos-sipserver-status
install -D -o root -g root -m 0440 "$REPO_ROOT/sudoers/tomos-sipserver-api" /etc/sudoers.d/tomos-sipserver-api
visudo -cf /etc/sudoers.d/tomos-sipserver-api
install -D -m 0644 "$REPO_ROOT/systemd/tomos-sipserver-api.service" /etc/systemd/system/tomos-sipserver-api.service

install -m 0644 "$REPO_ROOT/asterisk/asterisk.conf" /etc/asterisk/asterisk.conf
install -m 0644 "$REPO_ROOT/asterisk/modules.conf" /etc/asterisk/modules.conf
install -m 0640 "$REPO_ROOT/asterisk/sccp.conf" /etc/asterisk/sccp.conf
install -m 0644 "$REPO_ROOT/asterisk/logger.conf" /etc/asterisk/logger.conf
install -m 0644 "$REPO_ROOT/asterisk/rtp.conf" /etc/asterisk/rtp.conf
install -m 0644 "$REPO_ROOT/asterisk/cdr.conf" /etc/asterisk/cdr.conf
install -m 0644 "$REPO_ROOT/asterisk/cdr_sqlite3_custom.conf" /etc/asterisk/cdr_sqlite3_custom.conf
PYTHONPATH=/opt/tomos-sipserver/app /opt/tomos-sipserver/venv/bin/python -m tomos_sipserver.apply_config --no-reload
chown root:asterisk /etc/asterisk/pjsip.conf /etc/asterisk/extensions.conf /etc/asterisk/sccp.conf
chmod 0640 /etc/asterisk/pjsip.conf /etc/asterisk/extensions.conf /etc/asterisk/sccp.conf
chown -R asterisk:asterisk /var/lib/asterisk /var/log/asterisk /var/spool/asterisk
chown asterisk:asterisk /run/asterisk

install -D -m 0644 "$REPO_ROOT/systemd/asterisk.service" /etc/systemd/system/asterisk.service
install -D -m 0755 "$REPO_ROOT/scripts/tomos-sipserver-check" /usr/local/sbin/tomos-sipserver-check
install -D -m 0644 "$REPO_ROOT/systemd/tomos-sipserver.service" /etc/systemd/system/tomos-sipserver.service
systemctl daemon-reload
systemctl enable asterisk.service
systemctl restart asterisk.service

if ! systemctl is-active --quiet asterisk.service; then
  journalctl -u asterisk.service -n 80 --no-pager >&2 || true
  die "Asterisk failed to start. Review the backup in $BACKUP_DIR and service logs."
fi
systemctl enable --now tomos-sipserver.service
/usr/local/sbin/tomos-sipserver-check
systemctl enable --now tomos-sipserver-api.service

CREDENTIAL_FILE=/root/tomos-sip-credentials.txt
umask 077
{
  printf 'TomOS Home SIP Server initial extensions\n'
  printf 'Asterisk: %s\n' "$ASTERISK_VERSION"
  printf 'Generated: %s\n\n' "$(date -u +%FT%TZ)"
  printf 'Extension 100 password: %s\n' "$EXT100_SECRET"
  printf 'Extension 101 password: %s\n' "$EXT101_SECRET"
  printf '\nStore these credentials securely. This file is readable by root only.\n'
} > "$CREDENTIAL_FILE"
chmod 0600 "$CREDENTIAL_FILE"

log "Installation completed. Asterisk ${ASTERISK_VERSION}, extension API and call history are running."
log "Initial SIP credentials saved to $CREDENTIAL_FILE (root-only)."
log "API bearer token saved to /root/tomos-sip-api-token.txt (root-only)."
log "Dashboard password saved to /root/tomos-sip-dashboard-password.txt (root-only); open http://127.0.0.1:8000/ on the server or use a trusted HTTPS reverse proxy."
log "Extension API listens on 127.0.0.1:8000; use a trusted HTTPS reverse proxy for remote administration."
log "SIP: UDP 5060; SCCP: TCP 2000; RTP: UDP 10000-20000. Restrict access to your trusted LAN/VPN."
log "No router port forwarding, public SIP exposure or SIP trunk has been configured."
