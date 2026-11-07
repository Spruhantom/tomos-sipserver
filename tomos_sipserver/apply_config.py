"""Privileged, fixed-purpose configuration renderer and reload helper."""
from __future__ import annotations

import grp
import importlib.util
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from .cisco import render_phone_xml, render_sccp_config

APP_DIR = Path(__file__).resolve().parent.parent
RENDERER_PATH = APP_DIR / "render-config.py"
DB_PATH = Path("/var/lib/tomos-sipserver/extensions.db")
CONFIG_DIR = Path("/etc/asterisk")
SCCP_CONFIG_PATH = CONFIG_DIR / "tomos-devices.conf"
TFTP_DIR = Path("/var/lib/tftpboot")


def load_renderer():
    spec = importlib.util.spec_from_file_location("tomos_render_config", RENDERER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load TomOS configuration renderer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cli(command: str) -> None:
    result = subprocess.run(["/usr/sbin/asterisk", "-rx", command], capture_output=True, text=True, timeout=20)
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or "Unable to connect" in output or "No such command" in output:
        raise RuntimeError(f"Asterisk command failed ({command}): {output[-1000:]}")


def apply(reload_asterisk: bool = True) -> None:
    if os.geteuid() != 0:
        raise PermissionError("Configuration helper must run as root")
    with closing(sqlite3.connect(DB_PATH)) as connection:
        rows = connection.execute("SELECT extension, password FROM extensions WHERE enabled = 1 ORDER BY extension").fetchall()
        cisco_rows = connection.execute(
            """SELECT d.mac, d.model, d.server_host, e.extension, e.display_name
               FROM cisco_devices d JOIN extensions e ON e.extension = d.extension
               WHERE e.enabled = 1 ORDER BY d.mac"""
        ).fetchall()
    extensions = [(str(number), str(password)) for number, password in rows]
    cisco_configs = {
        str(mac): (
            render_sccp_config(
                mac=str(mac), model=str(model), extension=str(extension),
                display_name=str(display_name), server_host=str(server_host),
            ),
            render_phone_xml(mac=str(mac), server_host=str(server_host)),
        )
        for mac, model, server_host, extension, display_name in cisco_rows
    }
    renderer = load_renderer()
    paths = [CONFIG_DIR / "pjsip.conf", CONFIG_DIR / "extensions.conf", SCCP_CONFIG_PATH]
    asterisk_gid = grp.getgrnam("asterisk").gr_gid
    previous = {path: path.read_bytes() if path.exists() else None for path in paths}
    previous_modes = {path: path.stat().st_mode & 0o777 if path.exists() else 0o640 for path in paths}

    TFTP_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)
    os.chown(TFTP_DIR, 0, 0)
    os.chmod(TFTP_DIR, 0o755)
    previous_tftp = {
        path: (path.read_bytes(), path.stat().st_mode & 0o777)
        for path in TFTP_DIR.glob("SEP*.cnf.xml") if path.is_file()
    }
    try:
        renderer.atomic_write(paths[0], renderer.render_pjsip(extensions))
        renderer.atomic_write(paths[1], renderer.render_dialplan(extensions))
        renderer.atomic_write(paths[2], "".join(config for config, _ in cisco_configs.values()))
        for path in paths:
            os.chown(path, 0, asterisk_gid)
            os.chmod(path, 0o640)

        wanted = {f"SEP{mac.upper()}.cnf.xml": xml for mac, (_, xml) in cisco_configs.items()}
        for path in TFTP_DIR.glob("SEP*.cnf.xml"):
            if path.is_file() and path.name not in wanted:
                path.unlink()
        for filename, content in wanted.items():
            target = TFTP_DIR / filename
            renderer.atomic_write(target, content)
            os.chown(target, 0, 0)
            os.chmod(target, 0o644)

        if reload_asterisk:
            cli("pjsip reload")
            cli("dialplan reload")
            cli("sccp reload")
    except Exception:
        for path in paths:
            old = previous[path]
            if old is None:
                path.unlink(missing_ok=True)
            else:
                renderer.atomic_write(path, old.decode("utf-8"))
                os.chown(path, 0, asterisk_gid)
                os.chmod(path, previous_modes[path])
        for path in TFTP_DIR.glob("SEP*.cnf.xml"):
            if path.is_file() and path not in previous_tftp:
                path.unlink()
        for path, (old, mode) in previous_tftp.items():
            renderer.atomic_write(path, old.decode("utf-8"))
            os.chown(path, 0, 0)
            os.chmod(path, mode)
        if reload_asterisk:
            try:
                cli("pjsip reload")
                cli("dialplan reload")
                cli("sccp reload")
            except Exception:
                pass
        raise


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-reload", action="store_true", help="Render config without contacting a running Asterisk service")
    args = parser.parse_args()
    try:
        apply(reload_asterisk=not args.no_reload)
    except Exception as error:
        print(f"TomOS config apply failed: {error}", file=sys.stderr)
        raise SystemExit(1)
