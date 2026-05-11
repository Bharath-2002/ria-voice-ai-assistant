"""Conversation feature — orchestrates session, product search, WhatsApp, and stores."""

from typing import Any, Dict, List, Optional

from app.entities import Product
from app.services import BlueStoneService, SessionService, StoreService
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


def _product_dict(p: Product) -> Dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "price": p.price,
        "metal": p.metal,
        "carat": p.carat,
        "collection": p.collection,
        "image_url": p.image_url,
        "product_url": p.product_url,
    }


def _product_from_dict(d: Dict[str, Any]) -> Product:
    return Product(
        id=d.get("id", 0),
        name=d.get("name", ""),
        description=d.get("description", ""),
        price=float(d.get("price", 0) or 0),
        metal=d.get("metal"),
        image_url=d.get("image_url"),
        product_url=d.get("product_url"),
        carat=d.get("carat"),
        collection=d.get("collection"),
    )


class ConversationFeature:
    """Orchestrates the full conversation workflow with DI."""

    def __init__(
        self,
        session_service: SessionService,
        bluestone_service: BlueStoneService,
        whatsapp_service: Optional[WhatsAppService] = None,
        store_service: Optional[StoreService] = None,
    ) -> None:
        self._session = session_service
        self._bluestone = bluestone_service
        self._whatsapp = whatsapp_service
        self._store = store_service

    # ------------------------------------------------------------------ search

    async def handle_search_products(
        self,
        conversation_id: str,
        search_query: str,
        metal_preference: Optional[str] = None,
        budget_max: Optional[int] = None,
        occasion: Optional[str] = None,
        caller_phone: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search BlueStone, update session context, return a voice-friendly response.

        Does NOT send WhatsApp cards — Ria calls send_to_whatsapp explicitly once
        the customer confirms they want them.
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
        await self._session.update_context(conversation_id, {
            "recommended_products": [str(p.id) for p in top],
            "recommended_products_full": [_product_dict(p) for p in top],
        })

        narration = _narrate_products(top)
        count_phrase = f"{len(products)} design{'s' if len(products) != 1 else ''}"
        return {
            "say": (
                f"I found {count_phrase} for you. My top picks are {narration}. "
                "Would you like me to send these to your WhatsApp with photos and links, "
                "or shall I tell you more about one of them?"
            ),
            "data": {
                "action": "display_products",
                "products": [_product_dict(p) for p in top],
            },
        }

    # ------------------------------------------------------------ product detail

    async def handle_get_product_details(
        self,
        conversation_id: str,
        design_id: int,
        caller_phone: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch full details for one product. Does not auto-send to WhatsApp."""
        logger.info("get_product_details: conv=%s designId=%d", conversation_id, design_id)

        product = await self._bluestone.get_product_details(design_id)
        if not product:
            return {
                "say": "I couldn't pull up the details for that piece right now. "
                       "Would you like me to search for something similar?",
                "data": {"product": None},
            }

        # Remember it (so send_to_whatsapp / find_similar can act on it)
        await self._session.update_context(conversation_id, {
            "recommended_products": [str(product.id)],
            "recommended_products_full": [_product_dict(product)],
        })

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
                "Shall I send the link to your WhatsApp, or would you like to see similar designs?"
            ),
            "data": {"product": _product_dict(product)},
        }

    # ---------------------------------------------------------------- similar

    async def handle_find_similar(
        self,
        conversation_id: str,
        design_id: int,
        caller_phone: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return designs similar to `design_id`. Does not auto-send to WhatsApp."""
        logger.info("find_similar: conv=%s designId=%d", conversation_id, design_id)

        products = await self._bluestone.get_similar_products(design_id, limit=3)
        if not products:
            return {
                "say": "I couldn't find similar designs for that piece right now. "
                       "Would you like me to run a fresh search instead?",
                "data": {"products": []},
            }

        await self._session.update_context(conversation_id, {
            "recommended_products": [str(p.id) for p in products],
            "recommended_products_full": [_product_dict(p) for p in products],
        })

        narration = _narrate_products(products)
        return {
            "say": (
                f"Here are some similar designs: {narration}. "
                "Want me to send these to your WhatsApp, or hear more about one?"
            ),
            "data": {"action": "display_products", "products": [_product_dict(p) for p in products]},
        }

    # ------------------------------------------------------------- send to WA

    async def handle_send_to_whatsapp(
        self,
        conversation_id: str,
        caller_phone: Optional[str] = None,
        design_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Send product cards to the customer's WhatsApp.

        Uses `design_ids` if provided; otherwise sends whatever is currently
        recommended in the session. caller_phone is the customer's number.
        """
        logger.info("send_to_whatsapp: conv=%s phone=%s design_ids=%s",
                    conversation_id, caller_phone, design_ids)

        if not self._whatsapp:
            return {"say": "WhatsApp isn't available right now, but I can read the details out to you.",
                    "data": {"sent": False}}
        if not caller_phone:
            return {"say": "I'd love to send those over — what's your WhatsApp number with the country code?",
                    "data": {"sent": False, "need": "caller_phone"}}

        # Resolve which products to send
        products: List[Product] = []
        if design_ids:
            for did in design_ids[:3]:
                p = await self._bluestone.get_product_details(int(did))
                if p:
                    products.append(p)
        else:
            ctx_session = await self._session.get_raw_session(conversation_id)
            full = ctx_session.get("recommended_products_full") or []
            products = [_product_from_dict(d) for d in full[:3]]

        if not products:
            return {"say": "I don't have anything to send yet — let me find some pieces for you first.",
                    "data": {"sent": False}}

        await self._whatsapp.send_product_cards(caller_phone=caller_phone, products=products)
        names = ", ".join(p.name for p in products)
        return {
            "say": f"Done! I've sent {names} to your WhatsApp — check the photos and prices there.",
            "data": {"sent": True, "count": len(products)},
        }

    # ----------------------------------------------------------- nearest store

    async def handle_find_nearest_store(
        self,
        conversation_id: str,
        location: str,
    ) -> Dict[str, Any]:
        """Find BlueStone stores near a pincode or place name."""
        logger.info("find_nearest_store: conv=%s location=%r", conversation_id, location)

        if not self._store:
            return {"say": "Store lookup isn't available right now.", "data": {"stores": []}}

        location = (location or "").strip()
        if not location:
            return {"say": "Sure — what's your area or pincode? I'll find the nearest BlueStone store.",
                    "data": {"stores": [], "need": "location"}}

        stores = await self._store.find_stores(location, limit=3)
        if not stores:
            return {
                "say": (
                    f"I couldn't find a BlueStone store near {location}. "
                    "You can shop everything online at bluestone.com — would you like me to "
                    "send some pieces to your WhatsApp instead?"
                ),
                "data": {"stores": []},
            }

        nearest = stores[0]
        line = f"The nearest one is {nearest['name']}"
        if nearest.get("address"):
            line += f", at {nearest['address']}"
        if nearest.get("timings"):
            line += f". It's open {nearest['timings']}"
        if len(stores) > 1:
            line += f". I found {len(stores)} stores in the area"
        line += ". Want me to send the address and map link to your WhatsApp?"

        return {"say": line, "data": {"stores": stores}}
