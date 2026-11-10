"""
macOS Network Proxy Automation using the native 'networksetup' utility.
"""

import subprocess
import re
import logging
from typing import Optional, List

logger = logging.getLogger("macOS-Proxy")


def get_network_services() -> List[str]:
    """Retrieves list of active network services on macOS."""
    try:
        output = subprocess.check_output(
            ["networksetup", "-listallnetworkservices"],
            text=True
        )
        services = []
        for line in output.splitlines():
            line = line.strip()
            if line and not line.startswith("An asterisk") and not line.startswith("*"):
                services.append(line)
        return services
    except Exception as e:
        logger.error(f"Error enumerating network services: {e}")
        return ["Wi-Fi"]


def get_active_service() -> str:
    """Returns primary network service (defaults to Wi-Fi)."""
    services = get_network_services()
    if "Wi-Fi" in services:
        return "Wi-Fi"
    if services:
        return services[0]
    return "Wi-Fi"


def enable_socks_proxy(host: str = "127.0.0.1", port: int = 1080, service: Optional[str] = None) -> bool:
    """Configures and activates system SOCKS5 proxy on macOS."""
    target_service = service or get_active_service()
    try:
        subprocess.check_call([
            "networksetup", "-setsocksfirewallproxy", target_service, host, str(port)
        ])
        subprocess.check_call([
            "networksetup", "-setsocksfirewallproxystate", target_service, "on"
        ])
        logger.info(f"[+] SOCKS5 proxy enabled on '{target_service}' ({host}:{port})")
        return True
    except Exception as e:
        logger.error(f"[!] Failed to enable SOCKS proxy on '{target_service}': {e}")
        return False


def disable_socks_proxy(service: Optional[str] = None) -> bool:
    """Disables system SOCKS5 proxy on macOS and restores direct networking."""
    target_service = service or get_active_service()
    try:
        subprocess.check_call([
            "networksetup", "-setsocksfirewallproxystate", target_service, "off"
        ])
        logger.info(f"[+] SOCKS5 proxy disabled on '{target_service}'")
        return True
    except Exception as e:
        logger.error(f"[!] Failed to disable SOCKS proxy on '{target_service}': {e}")
        return False


def get_socks_proxy_status(service: Optional[str] = None) -> dict:
    """Queries current SOCKS proxy configuration."""
    target_service = service or get_active_service()
    try:
        output = subprocess.check_output(
            ["networksetup", "-getsocksfirewallproxy", target_service],
            text=True
        )
        enabled = "Enabled: Yes" in output
        server_match = re.search(r"Server:\s*(.+)", output)
        port_match = re.search(r"Port:\s*(\d+)", output)
        return {
            "service": target_service,
            "enabled": enabled,
            "server": server_match.group(1).strip() if server_match else None,
            "port": int(port_match.group(1)) if port_match else None
        }
    except Exception as e:
        return {"service": target_service, "enabled": False, "error": str(e)}
