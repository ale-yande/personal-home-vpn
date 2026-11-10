#!/usr/bin/env python3
"""
Certificate generation utility for Home VPN.
Generates self-signed TLS certificates (server.crt and server.key)
compatible with modern TLS 1.2 and TLS 1.3 clients.
"""

import os
import sys
import datetime
import ipaddress
import subprocess
from pathlib import Path

CERTS_DIR = Path(__file__).resolve().parent
KEY_PATH = CERTS_DIR / "server.key"
CERT_PATH = CERTS_DIR / "server.crt"


def generate_with_cryptography(common_name: str = "home-vpn"):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "HomeVPN"),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])

    alt_names = [
        x509.DNSName(common_name),
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))  # 10 years
        .add_extension(
            x509.SubjectAlternativeName(alt_names),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )

    with open(KEY_PATH, "wb") as f:
        f.write(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    with open(CERT_PATH, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    print(f"[+] TLS certificates generated successfully:\n    - {KEY_PATH}\n    - {CERT_PATH}")


def generate_with_openssl(common_name: str = "home-vpn"):
    print("[*] Generating certificates using system OpenSSL binary...")
    cmd = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", str(KEY_PATH),
        "-out", str(CERT_PATH),
        "-days", "3650",
        "-nodes",
        "-subj", f"/CN={common_name}"
    ]
    subprocess.check_call(cmd)
    print(f"[+] Certificates generated successfully using OpenSSL.")


def main():
    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    if KEY_PATH.exists() and CERT_PATH.exists():
        print(f"[i] Certificates already exist in {CERTS_DIR}.")
        ans = input("Overwrite existing certificates? (y/N): ").strip().lower()
        if ans != "y":
            print("Operation aborted. Keeping existing certificates.")
            return

    try:
        generate_with_cryptography()
    except ImportError:
        try:
            generate_with_openssl()
        except Exception as e:
            print(f"[!] Error: Could not generate certificates: {e}")
            print("    Please install cryptography: pip install cryptography")
            sys.exit(1)


if __name__ == "__main__":
    main()
