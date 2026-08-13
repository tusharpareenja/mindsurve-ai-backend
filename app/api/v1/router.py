"""Versioned API router."""

from fastapi import APIRouter

from app.api.v1 import (
    auth,
    chats,
    projects,
    study_brief,
    study_generation,
    synthetic_collection,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(chats.router)
api_router.include_router(study_brief.router)
api_router.include_router(study_generation.router)
api_router.include_router(synthetic_collection.router)
