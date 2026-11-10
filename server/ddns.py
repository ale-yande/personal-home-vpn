"""
Dynamic DNS updater for DuckDNS.
Periodically updates the configured domain with the current public IP.
"""

import urllib.request
import urllib.error
import asyncio
import logging
from typing import Optional

logger = logging.getLogger("DuckDNS")


def update_duckdns(domain: str, token: str) -> bool:
    """Sends an update request to DuckDNS."""
    clean_domain = domain.replace(".duckdns.org", "").strip()
    url = f"https://www.duckdns.org/update?domains={clean_domain}&token={token}&ip="

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "HomeVPN-DuckDNS-Updater/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8").strip()
            if content == "OK":
                logger.info(f"[DuckDNS] Domain '{clean_domain}.duckdns.org' updated successfully.")
                return True
            else:
                logger.error(f"[DuckDNS] DuckDNS update failed with response: {content}")
                return False
    except Exception as e:
        logger.error(f"[DuckDNS] Connection error updating DuckDNS: {e}")
        return False


async def duckdns_worker(domain: str, token: str, interval_seconds: int = 600) -> None:
    """Background asyncio worker periodically triggering DuckDNS updates."""
    if not domain or not token or domain.startswith("your-subdomain"):
        logger.info("[DuckDNS] DuckDNS not configured. Skipping background updates.")
        return

    logger.info(f"[DuckDNS] Starting DDNS updater for '{domain}' (every {interval_seconds // 60} min)...")
    while True:
        try:
            await asyncio.to_thread(update_duckdns, domain, token)
        except Exception as e:
            logger.error(f"[DuckDNS] Exception in updater task: {e}")
        await asyncio.sleep(interval_seconds)
