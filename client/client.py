#!/usr/bin/env python3
"""
Home VPN Client for macOS / Linux.
Binds a local SOCKS5 proxy server on 127.0.0.1:1080, tunnels connections
through an authenticated TLS gateway, and automates macOS system proxying.
"""

import os
import sys
import ssl
import json
import time
import shutil
import socket
import struct
import signal
import asyncio
import logging
import argparse
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from common import protocol

try:
    from client import macos_proxy
except ImportError:
    import macos_proxy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("VPN-Client")


class SOCKS5TunnelClient:
    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            config_path = Path(__file__).resolve().parent / "config.json"
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self.server: Optional[asyncio.Server] = None
        self.running = False

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
        verify = self.config.get("verify_ssl", False)
        if not verify:
            ctx = ssl._create_unverified_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx

        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        ca_cert = (self.config_path.parent / self.config["ca_cert_file"]).resolve()
        if ca_cert.exists():
            ctx.load_verify_locations(cafile=str(ca_cert))
        ctx.check_hostname = False
        return ctx

    async def _open_tunnel_connection(self) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Establishes an encrypted TLS stream to the gateway server."""
        ssl_ctx = self._create_ssl_context()
        host = self.config["server_host"]
        port = int(self.config["server_port"])
        return await asyncio.open_connection(host, port, ssl=ssl_ctx)

    async def _handle_socks5_client(self, local_reader: asyncio.StreamReader, local_writer: asyncio.StreamWriter):
        """Processes an RFC 1928 SOCKS5 client connection."""
        tunnel_writer = None
        try:
            # SOCKS5 Authentication negotiation
            ver = await local_reader.readexactly(1)
            if ver != b"\x05":
                local_writer.close()
                return

            nmethods_bytes = await local_reader.readexactly(1)
            nmethods = nmethods_bytes[0]
            methods = await local_reader.readexactly(nmethods)

            # Accept NO AUTHENTICATION REQUIRED (0x00)
            local_writer.write(b"\x05\x00")
            await local_writer.drain()

            # SOCKS5 Request Details
            header = await local_reader.readexactly(4)
            s_ver, s_cmd, s_rsv, s_atyp = header[0], header[1], header[2], header[3]

            if s_cmd != 0x01:  # 0x01 = CONNECT
                local_writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")  # Command not supported
                await local_writer.drain()
                local_writer.close()
                return

            target_host = ""
            if s_atyp == 0x01:  # IPv4
                ip_bytes = await local_reader.readexactly(4)
                target_host = socket.inet_ntop(socket.AF_INET, ip_bytes)
            elif s_atyp == 0x03:  # Domain name
                domain_len = (await local_reader.readexactly(1))[0]
                domain_bytes = await local_reader.readexactly(domain_len)
                target_host = domain_bytes.decode("utf-8", errors="ignore")
            elif s_atyp == 0x04:  # IPv6
                ip6_bytes = await local_reader.readexactly(16)
                target_host = socket.inet_ntop(socket.AF_INET6, ip6_bytes)
            else:
                local_writer.close()
                return

            port_bytes = await local_reader.readexactly(2)
            target_port = struct.unpack("!H", port_bytes)[0]

            logger.debug(f"[SOCKS5] Request for {target_host}:{target_port}")

            try:
                tunnel_reader, tunnel_writer = await asyncio.wait_for(
                    self._open_tunnel_connection(),
                    timeout=10.0
                )
            except Exception as e:
                logger.error(f"[!] Gateway connection error: {e}")
                local_writer.write(b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00")  # Network unreachable
                await local_writer.drain()
                return

            await protocol.send_msg(tunnel_writer, {
                "version": protocol.PROTOCOL_VERSION,
                "token": self.config["auth_token"],
                "command": "CONNECT",
                "target_host": target_host,
                "target_port": target_port
            })

            resp = await asyncio.wait_for(protocol.read_msg(tunnel_reader), timeout=12.0)
            if resp.get("status") != protocol.STATUS_OK:
                logger.warning(f"[-] Gateway rejected destination {target_host}:{target_port}: {resp.get('message')}")
                local_writer.write(b"\x05\x04\x00\x01\x00\x00\x00\x00\x00\x00")
                await local_writer.drain()
                return

            # Reply SUCCESS (0x00) to local client
            local_writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            await local_writer.drain()

            await protocol.pipe_bidirectional(
                local_reader, local_writer,
                tunnel_reader, tunnel_writer
            )

        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.debug(f"SOCKS5 handler detail: {e}")
        finally:
            try:
                if not local_writer.is_closing():
                    local_writer.close()
                    await local_writer.wait_closed()
            except Exception:
                pass
            try:
                if tunnel_writer and not tunnel_writer.is_closing():
                    tunnel_writer.close()
                    await tunnel_writer.wait_closed()
            except Exception:
                pass

    async def ping_test(self) -> bool:
        """Pings the TLS gateway to measure latency and test authentication."""
        print(f"[*] Connecting to {self.config['server_host']}:{self.config['server_port']} via TLS...")
        t0 = time.time()
        try:
            reader, writer = await self._open_tunnel_connection()
            await protocol.send_msg(writer, {
                "version": protocol.PROTOCOL_VERSION,
                "token": self.config["auth_token"],
                "command": "PING"
            })
            resp = await asyncio.wait_for(protocol.read_msg(reader), timeout=6.0)
            latency = (time.time() - t0) * 1000
            writer.close()
            await writer.wait_closed()

            if resp.get("status") == protocol.STATUS_OK:
                print(f"[+] Success! Server answered PONG in {latency:.1f} ms.")
                print(f"    Active clients on server: {resp.get('connections', 1)}")
                return True
            else:
                print(f"[!] Server returned error: {resp.get('message')}")
                return False
        except Exception as e:
            print(f"[!] Connection to gateway failed: {e}")
            return False

    async def start(self):
        self.running = True
        local_host = self.config.get("local_socks_host", "127.0.0.1")
        local_port = int(self.config.get("local_socks_port", 1080))

        self.server = await asyncio.start_server(
            self._handle_socks5_client,
            host=local_host,
            port=local_port
        )

        print("\n========================================================")
        print(f" 🟢 Home VPN Client Active")
        print(f"    - Local SOCKS5 Proxy: {local_host}:{local_port}")
        print(f"    - Gateway Endpoint:   {self.config['server_host']}:{self.config['server_port']}")
        print("========================================================")

        if self.config.get("auto_configure_macos_proxy", True) and sys.platform == "darwin":
            print("[*] Configuring macOS system proxy...")
            macos_proxy.enable_socks_proxy(host=local_host, port=local_port)
            print("[+] System traffic redirected through remote gateway.")
            print("    (Press Ctrl+C to disconnect and restore network)\n")

        try:
            async with self.server:
                await self.server.serve_forever()
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            if self.server:
                self.server.close()
                try:
                    await self.server.wait_closed()
                except Exception:
                    pass

    def cleanup(self):
        """Restores network proxy settings on macOS."""
        if self.config.get("auto_configure_macos_proxy", True) and sys.platform == "darwin":
            print("\n[*] Restoring macOS system proxy settings...")
            macos_proxy.disable_socks_proxy()
            print("[+] System proxy disabled. Direct network restored.")


def query_current_ip():
    """Queries and displays external IP and ISP information."""
    try:
        req = urllib.request.Request("https://ipinfo.io/json", headers={"User-Agent": "HomeVPN/1.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
            print(f"\n📡 Current External IP:")
            print(f"   IP:        {data.get('ip')}")
            print(f"   Org:       {data.get('org')}")
            print(f"   Location:  {data.get('city')}, {data.get('country')}")
    except Exception as e:
        print(f"[!] Failed to fetch external IP: {e}")


def cmd_connect():
    client = SOCKS5TunnelClient()
    try:
        asyncio.run(client.start())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.debug(f"Client execution terminated: {e}")
    finally:
        client.cleanup()


def cmd_disconnect():
    if sys.platform == "darwin":
        print("[*] Disabling system SOCKS5 proxy on macOS...")
        macos_proxy.disable_socks_proxy()
        print("[+] System proxy disabled.")
    else:
        print("[i] Automatic proxy teardown is currently supported on macOS.")


def cmd_status():
    if sys.platform == "darwin":
        status = macos_proxy.get_socks_proxy_status()
        print("\n🔍 macOS System Proxy Status:")
        print(f"   Service:   {status.get('service')}")
        print(f"   Enabled:   {'🟢 YES' if status.get('enabled') else '⚪ NO'}")
        if status.get("enabled"):
            print(f"   Server:    {status.get('server')}:{status.get('port')}")
    query_current_ip()


def cmd_test():
    client = SOCKS5TunnelClient()
    print("--- 1. Gateway Connectivity & Handshake Test ---")
    ok = asyncio.run(client.ping_test())
    if ok:
        print("\n--- 2. Direct External IP ---")
        query_current_ip()
    else:
        print("\n[!] Ensure the remote server is active and the port forwarding is properly set.")


def main():
    parser = argparse.ArgumentParser(description="Home VPN Client for macOS / Linux")
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    subparsers.add_parser("connect", help="Start the tunnel and configure system proxy")
    subparsers.add_parser("disconnect", help="Disable system proxy and restore network")
    subparsers.add_parser("status", help="Inspect proxy status and current external IP")
    subparsers.add_parser("test", help="Test TLS handshake with the remote gateway")

    args = parser.parse_args()
    if args.command == "connect":
        cmd_connect()
    elif args.command == "disconnect":
        cmd_disconnect()
    elif args.command == "status":
        cmd_status()
    elif args.command == "test":
        cmd_test()
    else:
        cmd_connect()


if __name__ == "__main__":
    main()
