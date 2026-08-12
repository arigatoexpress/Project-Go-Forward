"""Authenticated, GET-only Paid Search admin status route."""

import logging

from fastapi import APIRouter, HTTPException

from google_ads_admin.status import build_deployment_readiness

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/google-ads", tags=["admin-google-ads"])


@router.get("/deployment-readiness")
async def deployment_readiness():
    """Return only the sanitized checked-in contract view."""
    try:
        return build_deployment_readiness()
    except Exception:  # noqa: BLE001 - fail closed without surfacing raw details
        logger.warning("Paid Search deployment readiness is unavailable")
        raise HTTPException(
            status_code=503,
            detail="Paid Search deployment readiness is unavailable.",
        ) from None
