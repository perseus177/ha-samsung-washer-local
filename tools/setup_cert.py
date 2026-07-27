#!/usr/bin/env python3
"""Mint the client certificate the appliance's nginx will accept.

The appliance asks for a client certificate signed by one of Samsung's own CAs. Its
handshake lists them, and one of them - AC14K_M - is publicly mirrored together with
its private key, so a usable leaf certificate can simply be signed with it.

    $ python setup_cert.py
    -> certs/client_fullchain.pem, certs/client.key

Requires the openssl command line tool. That is not laziness: the leaf has to be
signed with SHA-1, and the cryptography library refuses to do that ("Hash algorithm
sha1 not supported for signatures"). Whether the appliance would accept SHA-256 has
not been tested, so the proven route is used.
"""

from __future__ import annotations

import argparse
import re
import shutil
import socket
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path

BUNDLE_URL = (
    "https://raw.githubusercontent.com/brayStorm/samsung-appliance-token/main/cert.pem"
)
UUID_HOST = "connect-v2.samsungiotcloud.com"
CERT_NAMES = ("ac14k_m.pem", "cert_2.pem", "cert_3.pem", "cert_4.pem")


def fetch_bundle(out_dir: Path) -> None:
    """Download the AC14K_M CA bundle and split it into key and certificates."""
    print(f"[1/4] downloading the CA bundle from {BUNDLE_URL}")
    with urllib.request.urlopen(BUNDLE_URL, timeout=30) as response:
        bundle = response.read().decode()

    key = re.search(
        r"-----BEGIN PRIVATE KEY-----.*?-----END PRIVATE KEY-----\n?", bundle, re.S
    )
    certs = re.findall(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----\n?", bundle, re.S
    )
    if key is None or len(certs) < 4:
        raise SystemExit("the bundle does not look right (expected a key and 4 certs)")

    (out_dir / "ac14k_m.key").write_text(key.group(0))
    for name, cert in zip(CERT_NAMES, certs):
        (out_dir / name).write_text(cert)
    print(f"      chain: {' <- '.join(CERT_NAMES)}")


def fetch_uuid() -> str:
    """Read the UUID out of Samsung's own server certificate.

    The leaf has to carry the same UUID, because the appliance's factory access
    control entry grants full permissions to exactly that identity.
    """
    print(f"[2/4] reading the UUID from {UUID_HOST}")
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((UUID_HOST, 443), timeout=20) as raw:
        with context.wrap_socket(raw, server_hostname=UUID_HOST) as tls:
            der = tls.getpeercert(binary_form=True)

    from cryptography import x509

    subject = x509.load_der_x509_certificate(der).subject.rfc4514_string()
    match = re.search(r"uuid:([0-9a-fA-F-]{36})", subject)
    if match is None:
        raise SystemExit(f"no UUID in the subject DN: {subject}")
    print(f"      UUID: {match.group(1)}")
    return match.group(1)


def mint(out_dir: Path, uuid: str) -> None:
    """Sign a leaf certificate for that UUID with the AC14K_M CA."""
    if shutil.which("openssl") is None:
        raise SystemExit("openssl was not found on PATH")

    print("[3/4] signing the leaf certificate (RSA-2048, SHA-1)")
    ext = out_dir / "leaf.cnf"
    ext.write_text(
        "basicConstraints = CA:FALSE\n"
        "extendedKeyUsage = clientAuth, serverAuth, 1.3.6.1.4.1.51414.0.1.2\n"
        f"subjectAltName = URI:urn:uuid:{uuid}, DNS:{uuid}\n"
        "1.3.6.1.4.1.51414.1.3 = ASN1:UTF8String:samsung.role.hub\n"
    )
    subject = (
        f"/OU=uuid:{uuid}/CN=urn:uuid:{uuid}/O=Samsung Electronics/C=KR"
    )
    run = lambda *args: subprocess.run(args, check=True, cwd=out_dir, capture_output=True)
    run("openssl", "genrsa", "-out", "client.key", "2048")
    run("openssl", "req", "-new", "-key", "client.key", "-out", "client.csr",
        "-sha1", "-subj", subject)
    # The validity deliberately mirrors Samsung's own certificates (1960-2060). An
    # appliance that lost power may come back with a reset clock, and a leaf that
    # starts "yesterday" would then be rejected as not yet valid.
    run("openssl", "x509", "-req", "-in", "client.csr", "-CA", "ac14k_m.pem",
        "-CAkey", "ac14k_m.key", "-CAcreateserial", "-out", "client.pem",
        "-sha1", "-extfile", "leaf.cnf",
        "-not_before", "19600101000000Z", "-not_after", "20600101000000Z")

    print("[4/4] assembling the full chain")
    chain = "".join(
        (out_dir / name).read_text() for name in ("client.pem", *CERT_NAMES)
    )
    (out_dir / "client_fullchain.pem").write_text(chain)


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="certs", help="output directory")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    fetch_bundle(out_dir)
    mint(out_dir, fetch_uuid())

    print()
    print("Done. Paste these two files into the Home Assistant config flow:")
    print(f"  certificate: {out_dir / 'client_fullchain.pem'}")
    print(f"  private key: {out_dir / 'client.key'}")
    print()
    print("Then run get_token.py to obtain the device token.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
