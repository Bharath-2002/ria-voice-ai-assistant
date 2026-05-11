"""BlueStone catalog API service.

API base: https://page.bluestone.com
Endpoints verified against BlueStone public API documentation.
"""

import asyncio
from typing import Any, List, Optional

import httpx

from app.entities import Product
from app.shared import BlueStoneAPIError, get_logger

logger = get_logger("bluestone_service")

# Retry transient failures: connection/timeout errors and 429/5xx/403 responses.
# BlueStone intermittently 403s requests from cloud IPs, so it's worth retrying.
_RETRY_STATUSES = {403, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECS = 0.5  # 0.5s, 1s, 2s ...

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



# Practical ceiling for "above X" searches — higher than any BlueStone piece.
_BUDGET_CEILING = 100_000_000  # ₹10 crore


def _build_budget_tag(budget_min: Optional[int], budget_max: Optional[int]) -> Optional[str]:
    """Build the BlueStone budget tag 'rs <from> to <to>'.

    The API accepts arbitrary ranges (the two values in the docs are just examples):
      - "under X"        -> budget_max=X            -> 'rs 0 to X'
      - "above X"        -> budget_min=X            -> 'rs X to <ceiling>'
      - "between X and Y" -> budget_min=X, budget_max=Y -> 'rs X to Y'
      - no preference    -> neither                 -> no tag (full price range)
    """
    if budget_max is None and budget_min is None:
        return None
    lo = int(budget_min) if budget_min is not None else 0
    hi = int(budget_max) if budget_max is not None else _BUDGET_CEILING
    if lo < 0:
        lo = 0
    if hi < lo:
        lo, hi = 0, hi  # nonsense range -> treat as "up to hi"
    return f"rs {lo} to {hi}"


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

    async def _get_with_retry(self, url: str, **kwargs: Any) -> httpx.Response:
        """GET with exponential-backoff retry on transient errors.

        Retries connection/timeout errors and 403/429/5xx responses up to
        _MAX_ATTEMPTS times. Raises the last error if all attempts fail.
        """
        kwargs.setdefault("headers", _BROWSER_HEADERS)
        last_exc: Optional[Exception] = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = await self._client.get(url, **kwargs)
            except httpx.RequestError as exc:
                last_exc = exc
                logger.warning("BlueStone GET %s attempt %d/%d failed: %s",
                               url, attempt, _MAX_ATTEMPTS, exc)
            else:
                if response.status_code not in _RETRY_STATUSES:
                    return response
                last_exc = httpx.HTTPStatusError(
                    f"retryable status {response.status_code}", request=response.request, response=response
                )
                logger.warning("BlueStone GET %s attempt %d/%d -> HTTP %d",
                               url, attempt, _MAX_ATTEMPTS, response.status_code)
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(_BACKOFF_BASE_SECS * (2 ** (attempt - 1)))
        assert last_exc is not None
        raise last_exc

    async def search_products(
        self,
        query: str,
        metal: Optional[str] = None,
        budget_min: Optional[int] = None,
        budget_max: Optional[int] = None,
        extra_tags: Optional[List[str]] = None,
        limit: int = 10,
    ) -> List[Product]:
        """Search the BlueStone catalog.

        Quirk worked around here: the BlueStone budget tag ('rs <from> to <to>')
        only filters when it is the *only* tag — any metal/occasion/stone tag
        alongside it makes the API ignore the price range. So we fold metal,
        occasion and stone words into the `search_query` text and pass ONLY the
        budget tag as a tag. (When no budget is given, metal/occasion are still
        folded into the query text for consistency.)

        Args:
            query: Item / free-text search term (e.g. "diamond earrings").
            metal: Optional metal preference (e.g. "gold", "white gold", "platinum").
            budget_min: Optional lower price bound in rupees (defaults to 0 when only max is given).
            budget_max: Optional upper price bound in rupees. If omitted, no price filter is applied.
            extra_tags: Extra descriptive words to add to the query (occasion, stone, etc.).
            limit: Max products to return (default 10, max from API is typically 24).

        Returns:
            List of matching Product entities (up to `limit`).

        Raises:
            BlueStoneAPIError: If the HTTP call fails with a non-2xx status.
        """
        # Compose the full search text: "<metal> <extra words> <item>", de-duplicated.
        words: List[str] = []
        seen: set[str] = set()
        for chunk in [metal] + list(extra_tags or []) + [query]:
            if not chunk:
                continue
            for w in str(chunk).split():
                lw = w.lower()
                if lw not in seen:
                    seen.add(lw)
                    words.append(w)
        search_query = " ".join(words) or query

        budget_tag = _build_budget_tag(budget_min, budget_max)
        tags: List[str] = [budget_tag] if budget_tag else []

        params: List[tuple] = [
            ("search_query", search_query),
            ("submit_search", "Search"),
            ("orderby", "popular"),
        ]
        for tag in tags:
            params.append(("tags", tag))

        logger.info("BlueStone search: query=%r tags=%s", search_query, tags)

        try:
            response = await self._get_with_retry(
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
            response = await self._get_with_retry(
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
            response = await self._get_with_retry(
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
