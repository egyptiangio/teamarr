"""Health check endpoint."""

from fastapi import APIRouter

from teamarr.config import VERSION

router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy", "version": VERSION}
