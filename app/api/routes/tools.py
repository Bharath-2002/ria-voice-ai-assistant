"""Tool endpoints called by the ElevenLabs agent during a conversation."""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from app.features import ConversationFeature
from app.shared import get_logger

logger = get_logger("tools_router")

router = APIRouter(prefix="/tools", tags=["tools"])


def _get_feature() -> ConversationFeature:
    from app.api.container import container
    return container.conversation_feature


@router.post("/search_products")
async def search_products(
    request: Request,
    feature: ConversationFeature = Depends(_get_feature),
) -> Dict[str, Any]:
    """ElevenLabs server tool: search BlueStone catalog and return top picks.

    ElevenLabs passes caller_phone (set as a Stream Parameter in the TwiML)
    so WhatsApp cards can be sent during the call.
    """
    try:
        body = await request.json()
        logger.info("Tool call search_products: %s", body)
        return await feature.handle_search_products(
            conversation_id=body.get("conversation_id", "unknown"),
            search_query=body.get("search_query", "jewelry"),
            metal_preference=body.get("metal_preference"),
            budget_max=body.get("budget_max"),
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
    """ElevenLabs server tool: fetch full details for a single product."""
    try:
        body = await request.json()
        logger.info("Tool call get_product_details: %s", body)
        return await feature.handle_get_product_details(
            conversation_id=body.get("conversation_id", "unknown"),
            design_id=int(body["design_id"]),
            caller_phone=body.get("caller_phone"),
        )
    except KeyError:
        raise HTTPException(status_code=422, detail="design_id is required")
    except Exception as exc:
        logger.error("get_product_details tool error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
