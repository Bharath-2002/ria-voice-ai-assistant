"""Tool endpoints called by the ElevenLabs agent during a conversation.

ElevenLabs passes caller_phone (the customer's WhatsApp number) in each tool
body — see the agent's system prompt for how it's derived (system__caller_id
for inbound calls, outbound_customer_phone for outbound).
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from app.features import ConversationFeature
from app.shared import get_logger

logger = get_logger("tools_router")

router = APIRouter(prefix="/tools", tags=["tools"])


def _get_feature() -> ConversationFeature:
    from app.api.container import container
    return container.conversation_feature


def _int_or_none(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


@router.post("/search_products")
async def search_products(
    request: Request,
    feature: ConversationFeature = Depends(_get_feature),
) -> Dict[str, Any]:
    """Search the BlueStone catalog and return up to 3 voice-friendly picks."""
    try:
        body = await request.json()
        logger.info("Tool call search_products: %s", body)
        return await feature.handle_search_products(
            conversation_id=body.get("conversation_id", "unknown"),
            search_query=body.get("search_query", "jewelry"),
            metal_preference=body.get("metal_preference"),
            budget_min=_int_or_none(body.get("budget_min")),
            budget_max=_int_or_none(body.get("budget_max")),
            occasion=body.get("occasion"),
            caller_phone=body.get("caller_phone"),
        )
    except Exception as exc:
        logger.error("search_products tool error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/get_product_details")
async def get_product_details(
    request: Request,
    feature: ConversationFeature = Depends(_get_feature),
) -> Dict[str, Any]:
    """Fetch full details for a single product by design ID."""
    try:
        body = await request.json()
        logger.info("Tool call get_product_details: %s", body)
        design_id = _int_or_none(body.get("design_id"))
        if design_id is None:
            raise HTTPException(status_code=422, detail="design_id is required")
        return await feature.handle_get_product_details(
            conversation_id=body.get("conversation_id", "unknown"),
            design_id=design_id,
            caller_phone=body.get("caller_phone"),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_product_details tool error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/find_similar")
async def find_similar(
    request: Request,
    feature: ConversationFeature = Depends(_get_feature),
) -> Dict[str, Any]:
    """Return designs similar to a given product (by design ID)."""
    try:
        body = await request.json()
        logger.info("Tool call find_similar: %s", body)
        design_id = _int_or_none(body.get("design_id"))
        if design_id is None:
            raise HTTPException(status_code=422, detail="design_id is required")
        return await feature.handle_find_similar(
            conversation_id=body.get("conversation_id", "unknown"),
            design_id=design_id,
            caller_phone=body.get("caller_phone"),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("find_similar tool error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/send_to_whatsapp")
async def send_to_whatsapp(
    request: Request,
    feature: ConversationFeature = Depends(_get_feature),
) -> Dict[str, Any]:
    """Send product cards to the customer's WhatsApp.

    Body: { caller_phone, conversation_id, design_ids? }
    If design_ids is omitted, sends whatever was last recommended this session.
    """
    try:
        body = await request.json()
        logger.info("Tool call send_to_whatsapp: %s", body)
        raw_ids = body.get("design_ids")
        design_ids: Optional[List[int]] = None
        if isinstance(raw_ids, list):
            design_ids = [i for i in (_int_or_none(x) for x in raw_ids) if i is not None]
            design_ids = design_ids or None
        return await feature.handle_send_to_whatsapp(
            conversation_id=body.get("conversation_id", "unknown"),
            caller_phone=body.get("caller_phone"),
            design_ids=design_ids,
        )
    except Exception as exc:
        logger.error("send_to_whatsapp tool error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/find_nearest_store")
async def find_nearest_store(
    request: Request,
    feature: ConversationFeature = Depends(_get_feature),
) -> Dict[str, Any]:
    """Find BlueStone stores near a pincode or place name.

    Body: { location, conversation_id }  — location may be "560034" or "Koramangala".
    """
    try:
        body = await request.json()
        logger.info("Tool call find_nearest_store: %s", body)
        return await feature.handle_find_nearest_store(
            conversation_id=body.get("conversation_id", "unknown"),
            location=body.get("location", ""),
        )
    except Exception as exc:
        logger.error("find_nearest_store tool error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
