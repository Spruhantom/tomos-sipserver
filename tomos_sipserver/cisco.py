"""Cisco SCCP handset model validation for the initial TomOS inventory scope."""

from __future__ import annotations

import re

from .yealink import normalize_mac, validate_server_host

SUPPORTED_MODELS = frozenset({"7940", "7960", "7970"})


def normalize_model(value: str) -> str:
    """Return a canonical Cisco model number from a narrowly allowlisted input."""
    if not isinstance(value, str):
        raise ValueError("Unsupported Cisco SCCP model")
    candidate = re.sub(r"^Cisco\\s+", "", value.strip(), flags=re.IGNORECASE)
    candidate = candidate.upper().replace(" ", "")
    if candidate not in SUPPORTED_MODELS:
        raise ValueError("Unsupported Cisco model; supported inventory models are 7940, 7960 and 7970")
    return candidate


__all__ = ["SUPPORTED_MODELS", "normalize_mac", "normalize_model", "validate_server_host"]
