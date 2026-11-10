#!/usr/bin/env python3
"""
Windows System Tray interface for Home VPN Server.
Allows starting, stopping, and inspecting connection metrics from the taskbar.
"""

import sys
import threading
import asyncio
import urllib.request
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    print("[!] GUI dependencies missing.")
    print("    Install them using: pip install pystray pillow")
    sys.exit(1)

from server.server import VPNServer


def create_tray_image(color: str = "green") -> Image.Image:
    """Renders a dynamic circular status icon for the system tray."""
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    colors = {
        "green": (46, 204, 113, 255),
        "red": (231, 76, 60, 255),
        "blue": (52, 152, 219, 255)
    }
    fill = colors.get(color, (127, 140, 141, 255))
    draw.ellipse((4, 4, size - 4, size - 4), fill=fill, outline=(255, 255, 255, 220), width=3)
    return image


class TrayApp:
    def __init__(self):
        self.server: Optional[VPNServer] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.server_thread: Optional[threading.Thread] = None
        self.is_running = False
        self.public_ip = "Unknown"
        self.icon: Optional[pystray.Icon] = None

    def fetch_public_ip(self):
        try:
            req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": "HomeVPN/1.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                self.public_ip = r.read().decode("utf-8").strip()
        except Exception:
            self.public_ip = "Failed to fetch"

    def _run_async_server(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.server = VPNServer()
        self.is_running = True
        self._update_tray()

        try:
            self.loop.run_until_complete(self.server.start())
        except Exception:
            pass
        finally:
            self.is_running = False
            self._update_tray()

    def start_server(self, icon=None, item=None):
        if self.is_running:
            return
        threading.Thread(target=self.fetch_public_ip, daemon=True).start()
        self.server_thread = threading.Thread(target=self._run_async_server, daemon=True)
        self.server_thread.start()

    def stop_server(self, icon=None, item=None):
        if not self.is_running or not self.loop or not self.server:
            return
        asyncio.run_coroutine_threadsafe(self.server.stop(), self.loop)
        self.is_running = False
        self._update_tray()

    def _update_tray(self):
        if self.icon:
            self.icon.icon = create_tray_image("green" if self.is_running else "red")
            self.icon.title = f"Home VPN: {'Running' if self.is_running else 'Stopped'}"

    def quit_app(self, icon, item):
        self.stop_server()
        icon.stop()

    def build_menu(self):
        return pystray.Menu(
            pystray.MenuItem(
                lambda text: f"Status: {'🟢 Running' if self.is_running else '🔴 Stopped'}",
                None,
                enabled=False
            ),
            pystray.MenuItem(
                lambda text: f"Public IP: {self.public_ip}",
                None,
                enabled=False
            ),
            pystray.MenuItem(
                lambda text: f"Active Clients: {self.server.active_connections if self.server else 0}",
                None,
                enabled=False
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start Server", self.start_server, enabled=lambda item: not self.is_running),
            pystray.MenuItem("Stop Server", self.stop_server, enabled=lambda item: self.is_running),
            pystray.MenuItem("Refresh IP", lambda icon, item: self.fetch_public_ip()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self.quit_app)
        )

    def run(self):
        threading.Thread(target=self.fetch_public_ip, daemon=True).start()
        self.start_server()
        self.icon = pystray.Icon(
            "home_vpn_server",
            create_tray_image("green"),
            "Home VPN Server",
            self.build_menu()
        )
        self.icon.run()


if __name__ == "__main__":
    app = TrayApp()
    app.run()
