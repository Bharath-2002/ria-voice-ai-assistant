"""BlueStone catalog API service.

API base: https://page.bluestone.com
Endpoints verified against BlueStone public API documentation.
"""

from typing import List, Optional

import httpx

from app.entities import Product
from app.shared import BlueStoneAPIError, get_logger

logger = get_logger("bluestone_service")

_SEARCH_BASE = "https://page.bluestone.com"
_PRODUCT_BASE = "https://page.bluestone.com"
_PRODUCT_PAGE_BASE = "https://www.bluestone.com"

# Browser-like headers — BlueStone returns 403 to bare/cloud-IP requests
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.bluestone.com/",
}

# Budget tag values exactly as the BlueStone API expects them
_BUDGET_TAG_LOW = "rs 0 to 30000"
_BUDGET_TAG_MID = "rs 10000 to 50000"


def _build_budget_tag(budget_max: int) -> Optional[str]:
    if budget_max <= 30000:
        return _BUDGET_TAG_LOW
    if budget_max <= 50000:
        return _BUDGET_TAG_MID
    return None  # no tag for budgets above 50k — let the search return all


def _parse_product(item: dict) -> Product:
    """Convert a search result item dict to a Product entity."""
    return Product(
        id=item["designId"],
        name=item.get("designName", ""),
        description=item.get("shortDesc", ""),
        price=float(item.get("defaultSkuPrice", 0)),
        metal=item.get("metalName"),
        image_url=item.get("imageUrl"),
        product_url=f"{_PRODUCT_PAGE_BASE}/{item['productPageUrl']}" if item.get("productPageUrl") else None,
    )


class BlueStoneService:
    """Client for the BlueStone jewelry catalog API."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        self._client = http_client or httpx.AsyncClient(timeout=8.0, headers=_BROWSER_HEADERS)

    async def search_products(
        self,
        query: str,
        metal: Optional[str] = None,
        budget_max: Optional[int] = None,
        extra_tags: Optional[List[str]] = None,
        limit: int = 10,
    ) -> List[Product]:
        """Search the BlueStone catalog.

        Args:
            query: Free-text search term (e.g. "diamond earrings").
            metal: Optional metal filter (e.g. "gold", "platinum").
            budget_max: Optional max price in rupees — maps to the closest API budget tag.
            extra_tags: Any additional filter tags (occasion, stone, etc.).
            limit: Max products to return (default 10, max from API is typically 24).

        Returns:
            List of matching Product entities (up to `limit`).

        Raises:
            BlueStoneAPIError: If the HTTP call fails with a non-2xx status.
        """
        tags: List[str] = []
        if metal:
            tags.append(metal.lower())
        if budget_max is not None:
            tag = _build_budget_tag(budget_max)
            if tag:
                tags.append(tag)
        if extra_tags:
            tags.extend(extra_tags)

        params: List[tuple] = [
            ("search_query", query),
            ("submit_search", "Search"),
            ("orderby", "popular"),
        ]
        for tag in tags:
            params.append(("tags", tag))

        logger.info("BlueStone search: query=%r tags=%s", query, tags)

        try:
            response = await self._client.get(
                f"{_SEARCH_BASE}/page/search",
                params=params,
                headers=_BROWSER_HEADERS,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("BlueStone search HTTP error: %s", exc)
            raise BlueStoneAPIError(f"BlueStone search failed ({exc.response.status_code})") from exc
        except httpx.RequestError as exc:
            logger.error("BlueStone search request error: %s", exc)
            raise BlueStoneAPIError("BlueStone search unreachable") from exc

        data = response.json()
        items = data.get("designItems", [])

        if not isinstance(items, list):
            logger.warning(
                "BlueStone search: unexpected response shape — 'designItems' is %s. Keys: %s",
                type(items).__name__,
                list(data.keys()),
            )
            return []

        products = [_parse_product(item) for item in items[:limit]]
        logger.info("BlueStone search returned %d products", len(products))
        return products

    async def get_product_details(self, design_id: int) -> Optional[Product]:
        """Fetch full details for a single product by designId.

        Returns None if the product is not found or the call fails (non-fatal).
        """
        logger.info("BlueStone product details: designId=%d", design_id)
        try:
            response = await self._client.get(
                f"{_PRODUCT_BASE}/page/product/{design_id}", headers=_BROWSER_HEADERS
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("BlueStone product details error for %d: %s", design_id, exc)
            return None

        data = response.json()
        return Product(
            id=data.get("designId", design_id),
            name=data.get("designName", ""),
            description=data.get("shortDesc", ""),
            price=float(str(data.get("discountedPrice", "0")).replace(",", "").replace("₹", "").strip() or 0),
            metal=data.get("metal"),
            product_url=data.get("shareUrl"),
            carat=float(data["diamondCarat"]) if data.get("diamondCarat") else None,
            collection=data.get("collectionName"),
        )

    async def get_similar_products(self, design_id: int, limit: int = 5) -> List[Product]:
        """Fetch similar designs for a given product.

        Returns empty list on any failure (non-fatal).
        """
        logger.info("BlueStone similar designs: designId=%d", design_id)
        try:
            response = await self._client.get(
                f"{_PRODUCT_PAGE_BASE}/similar-design/design-group/{design_id}",
                headers=_BROWSER_HEADERS,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("BlueStone similar designs error for %d: %s", design_id, exc)
            return []

        data = response.json()
        items = data.get("similarDesigns", {}).get("designItemList", [])
        products = []
        for entry in items[:limit]:
            item = entry.get("designItem", {})
            if not item.get("designId"):
                continue
            products.append(Product(
                id=item["designId"],
                name=item.get("designName", ""),
                description="",
                price=float(item.get("discountedPrice", 0)),
                image_url=item.get("imageUrl"),
                product_url=f"{_PRODUCT_PAGE_BASE}/{item['productPageUrl']}" if item.get("productPageUrl") else None,
            ))
        return products

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
