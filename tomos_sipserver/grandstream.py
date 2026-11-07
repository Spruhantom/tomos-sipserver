"""Grandstream GRP260x XML provisioning renderer.

Uses Grandstream's documented XML alias names and MAC-specific cfg<mac>.xml
filename convention. Confirm exact firmware behavior on physical hardware.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .yealink import validate_server_host

SUPPORTED_MODELS = frozenset({"GRP2601", "GRP2602", "GRP2603", "GRP2604"})


def normalize_model(value: str) -> str:
    model = value.strip().upper()
    if model not in SUPPORTED_MODELS:
        raise ValueError("Unsupported Grandstream model; initially supported models are GRP2601/2602/2603/2604")
    return model


def render_grandstream_xml(
    *, mac: str, extension: str, display_name: str, sip_password: str,
    server_host: str, server_port: int = 5060,
) -> str:
    """Render a single-account Grandstream XML provisioning document."""
    canonical_mac = re.sub(r"[:-]", "", mac.strip()).lower()
    if not re.fullmatch(r"[0-9a-f]{12}", canonical_mac):
        raise ValueError("MAC address must contain exactly 12 hexadecimal digits")
    if not re.fullmatch(r"[1-9][0-9]{2,5}", extension):
        raise ValueError("Invalid SIP extension")
    if not sip_password or len(sip_password) > 128 or any(ord(c) < 32 or ord(c) == 127 for c in sip_password):
        raise ValueError("Invalid SIP password")
    if not 1 <= server_port <= 65535:
        raise ValueError("Invalid SIP server port")
    host = validate_server_host(server_host)
    label = display_name.strip() or f"Extension {extension}"
    if any(ord(c) < 32 or ord(c) == 127 for c in label):
        raise ValueError("Display name must not contain control characters")

    root = ET.Element("gs_provision", {"version": "1"})
    ET.SubElement(root, "mac").text = canonical_mac
    config = ET.SubElement(root, "config", {"version": "2"})
    values = (
        ("account.1.enable", "Yes"),
        ("account.1.name", label),
        ("account.1.sip.server.1.address", f"{host}:{server_port}"),
        ("account.1.sip.userid", extension),
        ("account.1.sip.subscriber.userId", extension),
        ("account.1.sip.subscriber.password", sip_password),
        ("account.1.sip.subscriber.name", label),
    )
    for key, value in values:
        ET.SubElement(config, "item", {"name": key}).text = value
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
