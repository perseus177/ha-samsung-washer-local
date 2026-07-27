#!/usr/bin/env python3
"""Obtain the device token from the appliance.

The flow is a callback: you ask the appliance for a token over port 8888, and it
connects back to *you* on port 8889 and posts the token there.

    $ python get_token.py 192.168.0.173 --listen-ip 192.168.0.10

Two things trip people up:

* The appliance takes the callback address from the ``Host`` header of the request,
  not from the source address. Send ``Host: <your ip>:8889`` or it will try to call
  itself and nothing will ever arrive.
* The callback is HTTPS, so the listener needs a server certificate - the same
  client_fullchain.pem/client.key pair works.

Run this on the machine that will listen (the request and the listener must share an
address), and make sure inbound 8889 is not blocked by a local firewall. Switch
Remote Control on at the appliance first, otherwise it drops off the Wi-Fi within
minutes and never answers.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import ssl
import sys
import threading
import time
from http.client import HTTPSConnection
from pathlib import Path

LISTEN_PORT = 8889
token_holder: list[str] = []


def build_client_context(cert: Path, key: Path) -> ssl.SSLContext:
    """Build a client context for the appliance (TLS 1.0, relaxed security level)."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1
    context.set_ciphers("DEFAULT@SECLEVEL=0")
    context.load_cert_chain(str(cert), str(key))
    return context


def build_server_context(cert: Path, key: Path) -> ssl.SSLContext:
    """Build the server context for the callback listener."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1
    context.set_ciphers("DEFAULT@SECLEVEL=0")
    context.load_cert_chain(str(cert), str(key))
    return context


def listen(context: ssl.SSLContext) -> None:
    """Accept the callback and pull the token out of it."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", LISTEN_PORT))
    server.listen(5)
    print(f"[listener] waiting on :{LISTEN_PORT}")
    while not token_holder:
        try:
            raw, address = server.accept()
            raw.settimeout(10)
            try:
                connection = context.wrap_socket(raw, server_side=True)
            except ssl.SSLError as err:
                print(f"[listener] TLS handshake from {address} failed: {err}")
                raw.close()
                continue
            data = b""
            while b"\r\n\r\n" not in data or len(data) < 4:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                data += chunk
            print(f"[listener] callback from {address[0]}")
            match = re.search(rb'"DeviceToken"\s*:\s*"([^"]+)"', data)
            if match:
                token_holder.append(match.group(1).decode())
            connection.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
            connection.close()
        except OSError as err:
            print(f"[listener] {err}")


def request_token(host: str, port: int, listen_ip: str, context: ssl.SSLContext) -> str:
    """Ask the appliance for a token. Returns the HTTP status line."""
    connection = HTTPSConnection(host, port, context=context, timeout=15)
    body = "{}"
    connection.putrequest("POST", "/devicetoken/request", skip_host=True,
                          skip_accept_encoding=True)
    # This Host header is the whole trick - it tells the appliance where to call back.
    connection.putheader("Host", f"{listen_ip}:{LISTEN_PORT}")
    connection.putheader("Content-Type", "application/json")
    connection.putheader("DeviceToken", "xxxxxxxxxxx")
    connection.putheader("Content-Length", str(len(body)))
    connection.endheaders(body.encode())
    response = connection.getresponse()
    response.read()
    return f"{response.status} {response.reason}"


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="IP address of the appliance")
    parser.add_argument("--listen-ip", required=True,
                        help="this machine's address on the same LAN")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--certs", default="certs", help="directory from setup_cert.py")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    certs = Path(args.certs)
    cert, key = certs / "client_fullchain.pem", certs / "client.key"
    if not cert.exists() or not key.exists():
        raise SystemExit(f"{cert} or {key} is missing - run setup_cert.py first")

    threading.Thread(
        target=listen, args=(build_server_context(cert, key),), daemon=True
    ).start()
    time.sleep(1)

    client_context = build_client_context(cert, key)
    deadline = time.time() + args.timeout
    while not token_holder and time.time() < deadline:
        try:
            status = request_token(args.host, args.port, args.listen_ip, client_context)
            print(f"[request] {status}")
        except OSError as err:
            print(f"[request] failed: {err}")
        # A pending request blocks further ones for roughly a minute; the appliance
        # answers 403 "not able to be processed until ..." until it expires.
        for _ in range(14):
            if token_holder:
                break
            time.sleep(1)

    if not token_holder:
        print("\nNo token arrived. Check that Remote Control is on at the appliance,")
        print("that --listen-ip is this machine's address, and that inbound 8889 is open.")
        return 1

    print(f"\nDevice token: {token_holder[0]}")
    print("Paste it into the Home Assistant config flow. Keep it secret - it grants")
    print("full local control of the appliance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
