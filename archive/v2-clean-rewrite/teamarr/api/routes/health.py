"""Health check endpoint."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy"}
