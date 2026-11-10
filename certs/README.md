# TLS Certificates Directory

This directory stores the TLS certificates used to encrypt traffic between the client and server.

## Generating Certificates
Run the generation script:
```bash
python generate_certs.py
```
This produces:
- `server.crt`: The public X.509 certificate.
- `server.key`: The private RSA 2048-bit key (**Never share or commit to Git**).

> [!NOTE]
> The `.gitignore` file is configured to exclude `server.key` and `server.crt` from version control.
