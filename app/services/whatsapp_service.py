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
from typing import List

from twilio.rest import Client

from app.entities import Product
from app.shared import get_logger

logger = get_logger("whatsapp_service")

_MAX_PRODUCTS = 3  # send at most 3 cards per search result


def _format_card_body(product: Product) -> str:
    """Build a WhatsApp-friendly product card text."""
    lines = [f"*{product.name}*"]
    if product.description:
        lines.append(product.description)
    lines.append(f"💰 ₹{product.price:,.0f}")
    if product.product_url:
        lines.append(f"🔗 {product.product_url}")
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

    async def send_product_cards(self, caller_phone: str, products: List[Product]) -> None:
        """Fire-and-forget: send product cards to caller's WhatsApp.

        Converts caller_phone (e.g. '+919876543210') to WhatsApp format,
        then dispatches the synchronous Twilio client in a thread.
        Errors are fully contained here.
        """
        if not products:
            return

        # Normalize to WhatsApp E.164 format: whatsapp:+<digits>
        if caller_phone.startswith("whatsapp:"):
            number = caller_phone[len("whatsapp:"):]
        else:
            number = caller_phone
        if not number.startswith("+"):
            number = f"+{number}"
        to_whatsapp = f"whatsapp:{number}"

        try:
            await asyncio.to_thread(self._send_sync, to_whatsapp, products)
        except Exception as exc:
            logger.error("WhatsApp service unexpected error: %s", exc)
