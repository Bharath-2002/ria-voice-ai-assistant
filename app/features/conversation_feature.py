"""Conversation feature — orchestrates session, product search, and WhatsApp delivery."""

import asyncio
from typing import Any, Dict, List, Optional

from app.entities import Product
from app.services import BlueStoneService, SessionService
from app.services.whatsapp_service import WhatsAppService
from app.shared import get_logger

logger = get_logger("conversation_feature")


def _format_price(price: float) -> str:
    return f"₹{int(price):,}"


def _narrate_products(products: List[Product]) -> str:
    """Build a voice-friendly sentence listing product names and prices."""
    if not products:
        return ""
    parts = [f"{p.name} at {_format_price(p.price)}" for p in products]
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


class ConversationFeature:
    """Orchestrates the full conversation workflow with DI."""

    def __init__(
        self,
        session_service: SessionService,
        bluestone_service: BlueStoneService,
        whatsapp_service: Optional[WhatsAppService] = None,
    ) -> None:
        self._session = session_service
        self._bluestone = bluestone_service
        self._whatsapp = whatsapp_service

    async def handle_search_products(
        self,
        conversation_id: str,
        search_query: str,
        metal_preference: Optional[str] = None,
        budget_max: Optional[int] = None,
        occasion: Optional[str] = None,
        caller_phone: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle ElevenLabs search_products tool call.

        Searches BlueStone, updates session context, fires WhatsApp cards
        asynchronously (non-blocking), and returns the voice response.
        """
        logger.info(
            "search_products: conv=%s query=%r metal=%s budget=%s occasion=%s phone=%s",
            conversation_id, search_query, metal_preference, budget_max, occasion, caller_phone,
        )

        context_updates: Dict[str, Any] = {}
        if metal_preference:
            context_updates["metal_preference"] = metal_preference
        if budget_max is not None:
            context_updates["budget_max"] = budget_max
        if occasion:
            context_updates["occasion"] = occasion
        if caller_phone:
            context_updates["user_phone"] = caller_phone
        if context_updates:
            await self._session.update_context(conversation_id, context_updates)

        extra_tags = [occasion] if occasion else None

        try:
            products = await self._bluestone.search_products(
                query=search_query,
                metal=metal_preference,
                budget_max=budget_max,
                extra_tags=extra_tags,
            )
        except Exception as exc:
            logger.error("Product search failed: %s", exc)
            return {
                "say": "I ran into a problem searching the catalog just now. "
                       "Could you repeat your preferences and I'll try again?",
                "data": {"products": [], "error": str(exc)},
            }

        if not products:
            budget_hint = f" under {_format_price(budget_max)}" if budget_max else ""
            return {
                "say": (
                    f"I couldn't find {search_query}{budget_hint} right now. "
                    "Would you like me to broaden the budget, try a different style, "
                    "or explore another metal?"
                ),
                "data": {"products": []},
            }

        top = products[:3]

        await self._session.update_context(
            conversation_id,
            {"recommended_products": [str(p.id) for p in top]},
        )

        # Fire-and-forget WhatsApp cards — does not delay the voice response
        if self._whatsapp and caller_phone:
            asyncio.create_task(
                self._whatsapp.send_product_cards(caller_phone=caller_phone, products=top)
            )
            whatsapp_line = "I'm sending the details to your WhatsApp right now. "
        else:
            whatsapp_line = ""

        narration = _narrate_products(top)
        count_phrase = f"{len(products)} design{'s' if len(products) != 1 else ''}"

        return {
            "say": (
                f"I found {count_phrase} for you. "
                f"My top picks are {narration}. "
                f"{whatsapp_line}"
                "Which one would you like to know more about?"
            ),
            "data": {
                "action": "display_products",
                "products": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "description": p.description,
                        "price": p.price,
                        "metal": p.metal,
                        "image_url": p.image_url,
                        "product_url": p.product_url,
                    }
                    for p in top
                ],
            },
        }

    async def handle_get_product_details(
        self,
        conversation_id: str,
        design_id: int,
        caller_phone: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle ElevenLabs get_product_details tool call."""
        logger.info("get_product_details: conv=%s designId=%d", conversation_id, design_id)

        product = await self._bluestone.get_product_details(design_id)
        if not product:
            return {
                "say": "I couldn't pull up the details for that piece right now. "
                       "Would you like me to search for something similar?",
                "data": {"product": None},
            }

        # Fire-and-forget single product card to WhatsApp
        if self._whatsapp and caller_phone:
            asyncio.create_task(
                self._whatsapp.send_product_cards(caller_phone=caller_phone, products=[product])
            )

        detail_parts = []
        if product.metal:
            detail_parts.append(f"it's crafted in {product.metal}")
        if product.carat:
            detail_parts.append(f"set with {product.carat} carats of diamonds")
        if product.collection:
            detail_parts.append(f"part of the {product.collection}")

        detail_sentence = ", ".join(detail_parts) + "." if detail_parts else ""

        return {
            "say": (
                f"{product.name} is priced at {_format_price(product.price)}. "
                f"{detail_sentence} "
                "Shall I send the link to your WhatsApp, or would you like to explore similar designs?"
            ),
            "data": {
                "product": {
                    "id": product.id,
                    "name": product.name,
                    "description": product.description,
                    "price": product.price,
                    "metal": product.metal,
                    "carat": product.carat,
                    "collection": product.collection,
                    "image_url": product.image_url,
                    "product_url": product.product_url,
                }
            },
        }
