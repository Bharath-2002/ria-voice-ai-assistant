"""WhatsApp card service — sends product recommendations via Twilio sandbox.

Uses the Twilio REST client (synchronous) wrapped in asyncio.to_thread so
it doesn't block the FastAPI event loop. Failures are logged and swallowed —
a WhatsApp send error must never break the voice response.

Sandbox setup:
  1. User messages +1 415 523 8886 on WhatsApp
  2. User sends: join buried-audience
  3. After joining, Ria can send them product cards during the call
"""

import asyncio
from typing import Any, Dict, List, Optional

from twilio.rest import Client

from app.entities import Product
from app.shared import get_logger

logger = get_logger("whatsapp_service")

_MAX_PRODUCTS = 3  # send at most 3 cards per search result


def _to_whatsapp(caller_phone: str) -> str:
    """Normalise a phone number to Twilio WhatsApp form: whatsapp:+<E.164>."""
    number = caller_phone[len("whatsapp:"):] if caller_phone.startswith("whatsapp:") else caller_phone
    number = "".join(ch for ch in number if ch.isdigit() or ch == "+")
    if not number.startswith("+"):
        number = f"+{number}"
    return f"whatsapp:{number}"


def _format_card_body(product: Product) -> str:
    """Build a WhatsApp-friendly product card text."""
    lines = [f"*{product.name}*"]
    if product.description:
        lines.append(product.description)
    lines.append(f"💰 ₹{product.price:,.0f}")
    if product.product_url:
        lines.append(f"🔗 {product.product_url}")
    return "\n".join(lines)


def _format_store_body(store: Dict[str, Any]) -> str:
    """Build a WhatsApp-friendly text for a BlueStone store."""
    lines = [f"*BlueStone — {store.get('name','Store')}*"]
    if store.get("address"):
        lines.append(f"📍 {store['address']}")
    if store.get("timings"):
        lines.append(f"🕒 {store['timings']}")
    if store.get("phone"):
        lines.append(f"📞 {store['phone']}")
    if store.get("maps_url"):
        lines.append(f"🗺️ {store['maps_url']}")
    return "\n".join(lines)


class WhatsAppService:
    """Sends product cards to a caller's WhatsApp number."""

    def __init__(self, account_sid: str, auth_token: str, from_number: str) -> None:
        """
        Args:
            account_sid: Twilio account SID.
            auth_token: Twilio auth token.
            from_number: WhatsApp-formatted sender number, e.g. 'whatsapp:+14155238886'.
        """
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from = from_number

    def _send_sync(self, to_whatsapp: str, products: List[Product]) -> None:
        """Synchronous send — runs in a thread via asyncio.to_thread."""
        client = Client(self._account_sid, self._auth_token)
        sent = 0
        for product in products[:_MAX_PRODUCTS]:
            try:
                msg_kwargs = {
                    "from_": self._from,
                    "to": to_whatsapp,
                    "body": _format_card_body(product),
                }
                if product.image_url:
                    msg_kwargs["media_url"] = [product.image_url]

                client.messages.create(**msg_kwargs)
                sent += 1
                logger.info("WhatsApp card sent: %s → %s", product.name, to_whatsapp)
            except Exception as exc:
                logger.error("WhatsApp send failed for %s: %s", product.name, exc)

        logger.info("WhatsApp: sent %d/%d cards to %s", sent, len(products[:_MAX_PRODUCTS]), to_whatsapp)

    def _send_text_sync(self, to_whatsapp: str, body: str) -> bool:
        try:
            Client(self._account_sid, self._auth_token).messages.create(
                from_=self._from, to=to_whatsapp, body=body)
            logger.info("WhatsApp text sent → %s", to_whatsapp)
            return True
        except Exception as exc:
            logger.error("WhatsApp text send failed for %s: %s", to_whatsapp, exc)
            return False

    async def send_product_cards(self, caller_phone: str, products: List[Product]) -> None:
        """Fire-and-forget: send product cards to caller's WhatsApp. Errors are contained."""
        if not products:
            return
        try:
            await asyncio.to_thread(self._send_sync, _to_whatsapp(caller_phone), products)
        except Exception as exc:
            logger.error("WhatsApp service unexpected error: %s", exc)

    async def send_store(self, caller_phone: str, store: Dict[str, Any]) -> bool:
        """Send a single BlueStone store's details (address, timings, phone, map link) as text."""
        if not store:
            return False
        try:
            return await asyncio.to_thread(self._send_text_sync, _to_whatsapp(caller_phone), _format_store_body(store))
        except Exception as exc:
            logger.error("WhatsApp send_store unexpected error: %s", exc)
            return False

    async def send_stores(self, caller_phone: str, stores: List[Dict[str, Any]]) -> int:
        """Send each store as its own WhatsApp text. Returns the count that succeeded."""
        if not stores:
            return 0
        to = _to_whatsapp(caller_phone)

        def _send_all() -> int:
            ok = 0
            for s in stores:
                try:
                    Client(self._account_sid, self._auth_token).messages.create(
                        from_=self._from, to=to, body=_format_store_body(s))
                    ok += 1
                    logger.info("WhatsApp store sent: %s → %s", s.get("name", "?"), to)
                except Exception as exc:
                    logger.error("WhatsApp store send failed for %s: %s", s.get("name", "?"), exc)
            return ok

        try:
            return await asyncio.to_thread(_send_all)
        except Exception as exc:
            logger.error("WhatsApp send_stores unexpected error: %s", exc)
            return 0
