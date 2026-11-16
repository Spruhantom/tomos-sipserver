#!/usr/bin/env python3
"""Verify the trusted TLS and WebSocket upgrade used by the TomOS browser phone."""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import socket
import ssl
import sys
from datetime import datetime, timezone


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that an Asterisk WSS endpoint has a trusted TLS certificate and accepts WebSocket upgrades."
    )
    parser.add_argument("host", help="PBX hostname covered by the TLS certificate")
    parser.add_argument("--port", type=int, default=8089)
    parser.add_argument("--path", default="/ws")
    parser.add_argument("--ca-file", help="Optional PEM CA bundle for a private PKI")
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()

    if not args.path.startswith("/") or "\r" in args.path or "\n" in args.path:
        parser.error("--path must be an absolute URL path")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    context = ssl.create_default_context(cafile=args.ca_file)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    expected_accept = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
    ).decode("ascii")

    request = (
        f"GET {args.path} HTTP/1.1\r\n"
        f"Host: {args.host}:{args.port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Origin: https://{args.host}\r\n"
        "\r\n"
    ).encode("ascii")

    try:
        with socket.create_connection((args.host, args.port), timeout=args.timeout) as raw:
            with context.wrap_socket(raw, server_hostname=args.host) as tls:
                tls.settimeout(args.timeout)
                cert = tls.getpeercert()
                print(f"PASS TLS: trusted certificate and hostname match for {args.host}")
                expiry = cert.get("notAfter")
                if expiry:
                    expires = datetime.strptime(expiry, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
                    print(f"Certificate expires: {expires.isoformat()}")
                    if expires <= datetime.now(timezone.utc):
                        print("FAIL: certificate is expired", file=sys.stderr)
                        return 1
                tls.sendall(request)
                response = bytearray()
                while b"\r\n\r\n" not in response and len(response) < 16384:
                    chunk = tls.recv(4096)
                    if not chunk:
                        break
                    response.extend(chunk)
                header = bytes(response).split(b"\r\n\r\n", 1)[0].decode("iso-8859-1")
                lines = header.split("\r\n")
                if not lines or " 101 " not in f" {lines[0]} ":
                    print(f"FAIL WebSocket upgrade: {lines[0] if lines else 'no HTTP response'}", file=sys.stderr)
                    return 1
                headers = {}
                for line in lines[1:]:
                    if ":" in line:
                        name, value = line.split(":", 1)
                        headers[name.strip().lower()] = value.strip()
                if headers.get("upgrade", "").lower() != "websocket":
                    print("FAIL: server did not confirm Upgrade: websocket", file=sys.stderr)
                    return 1
                if headers.get("sec-websocket-accept") != expected_accept:
                    print("FAIL: invalid Sec-WebSocket-Accept response", file=sys.stderr)
                    return 1
                print(f"PASS WSS: {args.host}:{args.port}{args.path} accepted the WebSocket handshake")
                print("NOTE: this checks signalling TLS/upgrade only; it does not test SIP registration, ICE, microphone access or audio.")
                return 0
    except (OSError, ssl.SSLError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
