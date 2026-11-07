"""Cisco SCCP handset validation and SEP<MAC>.cnf.xml rendering."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .yealink import normalize_mac, validate_server_host

SUPPORTED_MODELS = frozenset({"7940", "7960", "7970"})
EXTENSION_PATTERN = re.compile(r"^[1-9][0-9]{2,5}$")
SECRET_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{24,128}$")


def normalize_model(value: str) -> str:
    """Return a canonical Cisco model number from a narrowly allowlisted input."""
    if not isinstance(value, str):
        raise ValueError("Unsupported Cisco SCCP model")
    candidate = re.sub(r"^Cisco\\s+", "", value.strip(), flags=re.IGNORECASE)
    candidate = candidate.upper().replace(" ", "")
    if candidate not in SUPPORTED_MODELS:
        raise ValueError("Unsupported Cisco model; supported inventory models are 7940, 7960 and 7970")
    return candidate


def render_sccp_config(
    *, mac: str, model: str, extension: str, display_name: str,
    server_host: str, sip_password: str, sccp_port: int = 2000,
) -> str:
    """Render a per-device Cisco SCCP SEP XML profile for TFTP delivery.

    Cisco SCCP configuration is unauthenticated when fetched over TFTP. The
    resulting file contains the extension secret and must only be served on a
    trusted, isolated voice LAN.
    """
    canonical_mac = normalize_mac(mac)
    canonical_model = normalize_model(model)
    host = validate_server_host(server_host)
    if not isinstance(extension, str) or not EXTENSION_PATTERN.fullmatch(extension):
        raise ValueError("Invalid extension number")
    if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 64:
        raise ValueError("Invalid display name")
    if not isinstance(sip_password, str) or not SECRET_PATTERN.fullmatch(sip_password):
        raise ValueError("Invalid extension password")
    if isinstance(sccp_port, bool) or not isinstance(sccp_port, int) or not 1 <= sccp_port <= 65535:
        raise ValueError("Invalid SCCP port")

    device = ET.Element("device")
    ET.SubElement(device, "deviceProtocol").text = "SCCP"
    pool = ET.SubElement(device, "devicePool")
    manager_group = ET.SubElement(pool, "callManagerGroup")
    members = ET.SubElement(manager_group, "members")
    member = ET.SubElement(members, "member", {"priority": "0"})
    manager = ET.SubElement(member, "callManager")
    ET.SubElement(manager, "ports")
    ET.SubElement(manager.find("ports"), "ethernetPhonePort").text = str(sccp_port)
    ET.SubElement(manager, "processNodeName").text = host
    ET.SubElement(device, "loadInformation").text = ""
    ET.SubElement(device, "vendorConfig")
    ET.SubElement(device, "versionStamp").text = "tomos-sccp-1"
    ET.SubElement(device, "userLocale").text = "English_United_Kingdom"
    ET.SubElement(device, "networkLocale").text = "United_Kingdom"
    ET.SubElement(device, "deviceSecurityMode").text = "1"
    ET.SubElement(device, "authenticationURL").text = ""
    ET.SubElement(device, "directoryURL").text = ""
    ET.SubElement(device, "servicesURL").text = ""
    ET.SubElement(device, "transportLayerProtocol").text = "4"

    lines = ET.SubElement(device, "lines")
    line = ET.SubElement(lines, "line", {"button": "1"})
    ET.SubElement(line, "featureID").text = "9"
    ET.SubElement(line, "featureLabel").text = display_name.strip()
    ET.SubElement(line, "proxy").text = "USECALLMANAGER"
    ET.SubElement(line, "port").text = str(sccp_port)
    ET.SubElement(line, "name").text = extension
    ET.SubElement(line, "displayName").text = display_name.strip()
    ET.SubElement(line, "autoAnswer").text = "2"
    ET.SubElement(line, "callWaiting").text = "3"
    ET.SubElement(line, "authName").text = extension
    ET.SubElement(line, "authPassword").text = sip_password
    ET.SubElement(line, "sharedLine").text = "false"
    ET.SubElement(line, "messageWaitingLampPolicy").text = "1"

    ET.indent(device, space="  ")
    return ET.tostring(device, encoding="utf-8", xml_declaration=True).decode("utf-8") + "\\n"


__all__ = [
    "SUPPORTED_MODELS", "normalize_mac", "normalize_model",
    "render_sccp_config", "validate_server_host",
]
