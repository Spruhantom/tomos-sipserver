"""Polycom VVX SIP provisioning configuration renderer."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .yealink import validate_server_host

# VVX SIP-family models using the Polycom UC Software reg.* configuration schema.
SUPPORTED_MODELS = frozenset({
    "VVX150", "VVX250", "VVX300", "VVX301", "VVX310", "VVX311",
    "VVX350", "VVX401", "VVX410", "VVX411", "VVX450",
    "VVX500", "VVX501", "VVX600", "VVX601", "VVX1500",
})


def normalize_model(value: str) -> str:
    model = re.sub(r"[ -]", "", value.strip().upper())
    if model not in SUPPORTED_MODELS:
        raise ValueError("Unsupported Polycom model; initial support is limited to listed VVX SIP phones")
    return model


def render_polycom_cfg(
    *, mac: str, extension: str, display_name: str, sip_password: str,
    server_host: str, server_port: int = 5060, transport: str = "UDP",
) -> str:
    """Render a per-phone Polycom VVX XML .cfg file."""
    canonical_mac = re.sub(r"[:-]", "", mac.strip()).lower()
    if not re.fullmatch(r"[0-9a-f]{12}", canonical_mac):
        raise ValueError("MAC address must contain exactly 12 hexadecimal digits")
    if not re.fullmatch(r"[1-9][0-9]{2,5}", extension):
        raise ValueError("Invalid SIP extension")
    if not sip_password or len(sip_password) > 128 or any(ord(c) < 32 or ord(c) == 127 for c in sip_password):
        raise ValueError("Invalid SIP password")
    if not 1 <= server_port <= 65535:
        raise ValueError("Invalid SIP server port")
    protocol = transport.strip().upper()
    if protocol not in {"UDP", "TCP", "TLS"}:
        raise ValueError("Transport must be UDP, TCP or TLS")
    host = validate_server_host(server_host)
    label = display_name.strip() or f"Extension {extension}"
    if any(ord(c) < 32 or ord(c) == 127 for c in label):
        raise ValueError("Display name must not contain control characters")

    root = ET.Element("polycomConfig")
    registration = ET.SubElement(root, "reg", {
        "reg.1.address": extension,
        "reg.1.label": label,
        "reg.1.displayName": label,
        "reg.1.auth.userId": extension,
        "reg.1.auth.password": sip_password,
        "reg.1.type": "private",
        "reg.1.server.1.address": host,
        "reg.1.server.1.port": str(server_port),
        "reg.1.server.1.transport": protocol,
    })
    # A second element is not required; the named registration element is the
    # Polycom UC Software configuration syntax used by phone1.cfg.
    del registration
    return '<?xml version="1.0" standalone="yes"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
