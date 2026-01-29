"""API route modules."""

from fastapi import APIRouter

from .regular_tv import router as regular_tv_router
from .settings import router as settings_router

api_router = APIRouter()

api_router.include_router(settings_router)
api_router.include_router(regular_tv_router)
