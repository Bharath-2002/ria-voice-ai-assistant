"""Store locator service — BlueStone physical stores + India pincode lookup.

A customer can give either a 6-digit pincode or a place name (e.g. "Koramangala").
Place names are resolved to a pincode via api.postalpincode.in, then the BlueStone
store-locator API returns nearby stores.
"""

import re
from typing import Any, Dict, List, Optional

import httpx

from app.shared import get_logger

logger = get_logger("store_service")

_STORE_URL = "https://www.bluestone.com/physical-store/store-details-for-product-page"
_PINCODE_LOOKUP_URL = "https://api.postalpincode.in/postoffice"

_PINCODE_RE = re.compile(r"^\d{6}$")

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


def _maps_url(lat: str, lng: str) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"


class StoreService:
    """Finds BlueStone physical stores near a pincode or place name."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        self._client = http_client or httpx.AsyncClient(timeout=8.0)

    async def resolve_pincode(self, location: str) -> Optional[str]:
        """Return a 6-digit pincode for `location` (already a pincode → returned as-is)."""
        location = location.strip()
        if _PINCODE_RE.match(location):
            return location
        try:
            resp = await self._client.get(
                f"{_PINCODE_LOOKUP_URL}/{location}", headers=_BROWSER_HEADERS
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            logger.error("Pincode lookup failed for %r: %s", location, exc)
            return None

        if not isinstance(data, list) or not data:
            return None
        first = data[0]
        if first.get("Status") != "Success":
            return None
        offices = first.get("PostOffice") or []
        if not offices:
            return None
        pincode = offices[0].get("Pincode")
        logger.info("Resolved location %r -> pincode %s", location, pincode)
        return pincode

    async def find_stores(self, location: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Return up to `limit` BlueStone stores near `location` (pincode or place name).

        Returns [] on any failure or no match (non-fatal — the agent offers alternatives).
        """
        pincode = await self.resolve_pincode(location)
        if not pincode:
            logger.info("Could not resolve a pincode for %r", location)
            return []

        try:
            resp = await self._client.get(
                _STORE_URL, params={"pincode": pincode}, headers=_BROWSER_HEADERS
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            logger.error("Store lookup failed for pincode %s: %s", pincode, exc)
            return []

        details = data.get("storeDetails") if isinstance(data, dict) else None
        if not isinstance(details, list):
            return []

        stores: List[Dict[str, Any]] = []
        for s in details[:limit]:
            lat, lng = s.get("latitude", ""), s.get("longitude", "")
            stores.append({
                "name": s.get("storeName", ""),
                "city": s.get("cityName", ""),
                "address": s.get("storeAddress", ""),
                "phone": s.get("contactNumber", ""),
                "whatsapp": s.get("whatsappNumber", ""),
                "timings": s.get("storeTimings", ""),
                "maps_url": _maps_url(lat, lng) if lat and lng else None,
            })
        logger.info("Found %d stores near pincode %s", len(stores), pincode)
        return stores

    async def close(self) -> None:
        await self._client.aclose()
