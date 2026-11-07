"""Yealink SIP-T series provisioning configuration renderer.

The generated per-device CFG contains only the account settings required for
registration. It deliberately does not configure firmware, remote management,
or a provisioning URL of its own.
"""
from __future__ import annotations

import re

MAC_RE = re.compile(r"^[0-9A-F]{12}$")
HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def normalize_mac(value: str) -> str:
    """Return a canonical uppercase 12-hex-digit MAC address."""
    mac = re.sub(r"[:-]", "", value.strip()).upper()
    if not MAC_RE.fullmatch(mac):
        raise ValueError("MAC address must contain exactly 12 hexadecimal digits")
    return mac


def validate_server_host(value: str) -> str:
    """Validate a DNS name or IPv4 address without accepting a URL or port."""
    host = value.strip().rstrip(".")
    if not host or len(host) > 253 or any(not HOST_LABEL_RE.fullmatch(part) for part in host.split(".")):
        raise ValueError("PBX server must be a DNS hostname or IPv4 address without a scheme or port")
    return host


def _cfg_value(value: str) -> str:
    """Reject control characters and CFG line injection."""
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Configuration values must not contain control characters")
    return value.replace("\\", "\\\\")


def render_yealink_cfg(
    *,
    extension: str,
    display_name: str,
    sip_password: str,
    server_host: str,
    server_port: int = 5060,
    transport: str = "UDP",
) -> str:
    """Render a conservative Yealink T-series single-account CFG file."""
    if not re.fullmatch(r"[1-9][0-9]{2,5}", extension):
        raise ValueError("Invalid SIP extension")
    if not sip_password or len(sip_password) > 128:
        raise ValueError("Invalid SIP password")
    if not 1 <= server_port <= 65535:
        raise ValueError("Invalid SIP server port")
    if transport not in {"UDP", "TCP", "TLS"}:
        raise ValueError("Unsupported SIP transport")
    host = validate_server_host(server_host)
    label = _cfg_value(display_name.strip())
    if not label:
        label = f"Extension {extension}"
    transport_value = {"UDP": 0, "TCP": 1, "TLS": 2}[transport]
    values = [
        ("account.1.enable", "1"),
        ("account.1.label", label),
        ("account.1.display_name", label),
        ("account.1.auth_name", extension),
        ("account.1.user_name", extension),
        ("account.1.password", _cfg_value(sip_password)),
        ("account.1.sip_server.1.address", host),
        ("account.1.sip_server.1.port", str(server_port)),
        ("account.1.transport_type", str(transport_value)),
        ("account.1.codec.pcmu.priority", "1"),
        ("account.1.codec.pcma.priority", "2"),
        ("account.1.codec.g722.priority", "0"),
        ("account.1.codec.opus.priority", "0"),
    ]
    return "# TomOS SIP Server - generated Yealink account profile\n" + "".join(
        f"{key} = {value}\n" for key, value in values
    )
