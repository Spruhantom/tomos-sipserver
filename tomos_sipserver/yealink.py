"""Yealink SIP-T series provisioning configuration renderer.

The output follows Yealink's MAC-oriented CFG format. Hardware compatibility
still needs confirmation against the exact handset firmware in a real lab.
"""
from __future__ import annotations

import re

MAC_RE = re.compile(r"^[0-9A-F]{12}$")
HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

# Supported wired SIP-T models documented in Yealink's provisioning guides.
SUPPORTED_MODELS = frozenset({
    "SIP-T30", "SIP-T30P", "SIP-T31", "SIP-T31G", "SIP-T31P",
    "SIP-T33G", "SIP-T33P", "SIP-T40G", "SIP-T40P",
    "SIP-T41P", "SIP-T41S", "SIP-T42G", "SIP-T42S", "SIP-T42U",
    "SIP-T43U", "SIP-T46G", "SIP-T46S", "SIP-T46U",
    "SIP-T48G", "SIP-T48S", "SIP-T48U",
    "SIP-T53", "SIP-T53W", "SIP-T54W", "SIP-T57W", "SIP-T58W",
})


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


def normalize_model(value: str) -> str:
    """Normalize a model identifier and reject models without a verified template."""
    model = value.strip().upper()
    if model not in SUPPORTED_MODELS:
        raise ValueError("Unsupported Yealink model; choose a supported SIP-T series model")
    return model


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
    """Render a conservative Yealink SIP-T series single-account MAC CFG file."""
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
        ("account.1.sip_server.1.transport_type", str(transport_value)),
        ("account.1.codec.1.enable", "1"),
        ("account.1.codec.1.payload_type", "PCMU"),
        ("account.1.codec.1.priority", "1"),
        ("account.1.codec.2.enable", "1"),
        ("account.1.codec.2.payload_type", "PCMA"),
        ("account.1.codec.2.priority", "2"),
    ]
    return "#!version:1.0.0.1\n# TomOS SIP Server - generated Yealink MAC-oriented profile\n" + "".join(
        f"{key} = {value}\n" for key, value in values
    )
