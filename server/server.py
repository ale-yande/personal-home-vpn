#!/usr/bin/env python3
"""
Home VPN TLS Gateway Server.
Listens for authenticated TLS connections from remote clients and forwards
traffic to destination endpoints on the Internet.
"""

import os
import sys
import ssl
import json
import shutil
import asyncio
import logging
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from common import protocol

try:
    from server.ddns import duckdns_worker
except ImportError:
    from ddns import duckdns_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("VPN-Server")


class VPNServer:
    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            config_path = Path(__file__).resolve().parent / "config.json"
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self.server: Optional[asyncio.Server] = None
        self.ddns_task: Optional[asyncio.Task] = None
        self.active_connections = 0

    def _load_config(self) -> dict:
        if not self.config_path.exists():
            example_path = self.config_path.parent / "config.example.json"
            if example_path.exists():
                logger.warning(f"Config '{self.config_path.name}' not found. Initializing from config.example.json.")
                shutil.copyfile(example_path, self.config_path)
            else:
                raise FileNotFoundError(f"Missing configuration file at: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _create_ssl_context(self) -> ssl.SSLContext:
        cert_path = (self.config_path.parent / self.config["cert_file"]).resolve()
        key_path = (self.config_path.parent / self.config["key_file"]).resolve()

        if not cert_path.exists() or not key_path.exists():
            raise FileNotFoundError(
                f"Certificates missing:\n  {cert_path}\n  {key_path}\n"
                "Please run first: python certs/generate_certs.py"
            )

        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ssl_ctx

    async def _handle_client(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter):
        peer = client_writer.get_extra_info("peername")
        self.active_connections += 1
        logger.info(f"[+] Inbound connection from {peer} (Active: {self.active_connections})")

        remote_writer = None
        try:
            handshake = await asyncio.wait_for(protocol.read_msg(client_reader), timeout=10.0)

            auth_token = handshake.get("token")
            if auth_token != self.config["auth_token"]:
                logger.warning(f"[-] Unauthorized access attempt from {peer} (invalid token)")
                await protocol.send_msg(client_writer, {
                    "status": protocol.STATUS_ERROR,
                    "message": "Unauthorized"
                })
                return

            cmd = handshake.get("command")

            if cmd == "PING":
                await protocol.send_msg(client_writer, {
                    "status": protocol.STATUS_OK,
                    "message": "PONG",
                    "connections": self.active_connections
                })
                return

            if cmd == "CONNECT":
                target_host = handshake.get("target_host")
                target_port = handshake.get("target_port")

                if not target_host or not target_port:
                    await protocol.send_msg(client_writer, {
                        "status": protocol.STATUS_ERROR,
                        "message": "Missing target_host or target_port"
                    })
                    return

                try:
                    remote_reader, remote_writer = await asyncio.wait_for(
                        asyncio.open_connection(target_host, int(target_port)),
                        timeout=12.0
                    )
                except Exception as e:
                    logger.warning(f"[-] Destination unreachable {target_host}:{target_port} -> {e}")
                    await protocol.send_msg(client_writer, {
                        "status": protocol.STATUS_ERROR,
                        "message": f"Connection failed: {e}"
                    })
                    return

                await protocol.send_msg(client_writer, {
                    "status": protocol.STATUS_OK,
                    "message": "Connected"
                })

                await protocol.pipe_bidirectional(
                    client_reader, client_writer,
                    remote_reader, remote_writer
                )

        except asyncio.TimeoutError:
            logger.warning(f"[-] Handshake timeout from {peer}")
        except Exception as e:
            logger.debug(f"Connection closed {peer}: {e}")
        finally:
            self.active_connections = max(0, self.active_connections - 1)
            try:
                if not client_writer.is_closing():
                    client_writer.close()
                    await client_writer.wait_closed()
            except Exception:
                pass
            try:
                if remote_writer and not remote_writer.is_closing():
                    remote_writer.close()
                    await remote_writer.wait_closed()
            except Exception:
                pass
            logger.info(f"[-] Connection closed with {peer} (Active: {self.active_connections})")

    async def start(self):
        ssl_ctx = self._create_ssl_context()
        host = self.config.get("listen_host", "0.0.0.0")
        port = int(self.config.get("listen_port", 8443))

        self.server = await asyncio.start_server(
            self._handle_client,
            host=host,
            port=port,
            ssl=ssl_ctx
        )

        logger.info(f"==================================================")
        logger.info(f" Home VPN Server running on {host}:{port}")
        logger.info(f"==================================================")

        ddns_cfg = self.config.get("duckdns", {})
        if ddns_cfg.get("enabled"):
            self.ddns_task = asyncio.create_task(
                duckdns_worker(
                    domain=ddns_cfg.get("domain", ""),
                    token=ddns_cfg.get("token", ""),
                    interval_seconds=int(ddns_cfg.get("interval_seconds", 600))
                )
            )

        async with self.server:
            await self.server.serve_forever()

    async def stop(self):
        logger.info("[*] Shutting down server...")
        if self.ddns_task:
            self.ddns_task.cancel()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        logger.info("[+] Server stopped.")


def main():
    server = VPNServer()
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        print("\nServer terminated by user.")


if __name__ == "__main__":
    main()
