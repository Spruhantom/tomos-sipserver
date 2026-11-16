"""Validation and rendering primitives for a single outbound SIP trunk.

The trunk remains disabled unless an administrator explicitly provisions it.
Emergency destinations are never routed through this generic trunk.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9.-]+$")
USER_RE = re.compile(r"^[A-Za-z0-9_.+~-]{1,64}$")
SECRET_RE = re.compile(r"^[^\r\n\x00]{1,256}$")


@dataclass(frozen=True)
class Trunk:
    host: str
    username: str
    password: str
    port: int = 5060
    transport: str = "udp"
    enabled: bool = False
    outbound_caller_id: str = ""


def validate_trunk(trunk: Trunk) -> None:
    if not HOST_RE.fullmatch(trunk.host):
        raise ValueError("Trunk host must be a hostname or IP address")
    try:
        ipaddress.ip_address(trunk.host)
    except ValueError:
        if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
               for label in trunk.host.rstrip(".").split(".")):
            raise ValueError("Invalid trunk hostname")
    if not USER_RE.fullmatch(trunk.username):
        raise ValueError("Trunk username contains unsupported characters")
    if not SECRET_RE.fullmatch(trunk.password):
        raise ValueError("Trunk password must be 1-256 characters without control characters")
    if not 1 <= trunk.port <= 65535:
        raise ValueError("Trunk port must be between 1 and 65535")
    if trunk.transport not in {"udp", "tcp", "tls"}:
        raise ValueError("Trunk transport must be udp, tcp or tls")
    if trunk.outbound_caller_id and not re.fullmatch(r"\+[1-9][0-9]{7,14}", trunk.outbound_caller_id):
        raise ValueError("Outbound caller ID must be an E.164 number")


def render_trunk_pjsip(trunk: Trunk) -> str:
    validate_trunk(trunk)
    if not trunk.enabled:
        return "; Outbound SIP trunk is not configured or is disabled.\n"
    # Provider-controlled values are validated above and cannot inject PJSIP
    # directives. The secret is held in the root-only trunk configuration file.
    transport = {"udp": "transport-udp", "tcp": "transport-tcp", "tls": "transport-tls"}[trunk.transport]
    lines = [
        "[tomos-provider-auth]", "type=auth", "auth_type=userpass",
        f"username={trunk.username}", f"password={trunk.password}", "",
        "[tomos-provider-aor]", "type=aor",
        f"contact=sip:{trunk.host}:{trunk.port};transport={trunk.transport}", "",
        "[tomos-provider-registration]", "type=registration",
        f"transport={transport}", "outbound_auth=tomos-provider-auth",
        "server_uri=sip:" + trunk.host + ":" + str(trunk.port),
        "client_uri=sip:" + trunk.username + "@" + trunk.host,
        "retry_interval=60", "forbidden_retry_interval=600", "expiration=300", "",
        "[tomos-provider-endpoint]", "type=endpoint",
        f"transport={transport}", "outbound_auth=tomos-provider-auth",
        "aors=tomos-provider-aor", "context=from-tomos-provider",
        "disallow=all", "allow=alaw,ulaw", "direct_media=no",
        "rtp_symmetric=yes", "force_rport=yes", "rewrite_contact=yes", "",
    ]
    if trunk.outbound_caller_id:
        lines.insert(-1, f"from_user={trunk.username}")
    return "\n".join(lines)
