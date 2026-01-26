"""Health check endpoint."""

from fastapi import APIRouter

from teamarr.api.startup_state import get_startup_state
from teamarr.config import VERSION

router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    """Health check endpoint with startup status."""
    startup_state = get_startup_state()
    startup_info = startup_state.to_dict()

    return {
        "status": "healthy" if startup_state.is_ready else "starting",
        "version": VERSION,
        "startup": startup_info,
    }
