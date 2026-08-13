"""Synthetic respondent collection endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.db.models.user import User
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.schemas.synthetic_collection import (
    SyntheticCollectionRunOut,
    SyntheticCollectionStartRequest,
    SyntheticCollectionStartResponse,
)
from app.services.synthetic_collection_service import SyntheticCollectionService

router = APIRouter(tags=["synthetic-collection"])
_bearer = HTTPBearer(auto_error=False)


def get_synthetic_service(db: Session = Depends(get_db)) -> SyntheticCollectionService:
    return SyntheticCollectionService(db)


def _raise(exc: AppError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _access_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


@router.post(
    "/chats/{chat_id}/synthetic-collection/start",
    response_model=SyntheticCollectionStartResponse,
    status_code=status.HTTP_201_CREATED,
)
def start_synthetic_collection(
    chat_id: UUID,
    body: SyntheticCollectionStartRequest | None = None,
    user: User = Depends(get_current_user),
    access_token: str = Depends(_access_token),
    service: SyntheticCollectionService = Depends(get_synthetic_service),
) -> SyntheticCollectionStartResponse:
    req = body or SyntheticCollectionStartRequest()
    try:
        return service.start(
            user,
            chat_id,
            access_token=access_token,
            mode=req.mode,
            randomize=req.randomize,
        )
    except AppError as exc:
        _raise(exc)
    raise AssertionError  # pragma: no cover


@router.get(
    "/chats/{chat_id}/synthetic-collection",
    response_model=SyntheticCollectionRunOut,
)
def get_synthetic_collection_status(
    chat_id: UUID,
    run_id: UUID | None = None,
    user: User = Depends(get_current_user),
    access_token: str = Depends(_access_token),
    service: SyntheticCollectionService = Depends(get_synthetic_service),
) -> SyntheticCollectionRunOut:
    try:
        return service.get_status(
            user, chat_id, access_token=access_token, run_id=run_id
        )
    except AppError as exc:
        _raise(exc)
    raise AssertionError  # pragma: no cover


@router.post(
    "/chats/{chat_id}/synthetic-collection/retry",
    response_model=SyntheticCollectionStartResponse,
)
def retry_synthetic_collection(
    chat_id: UUID,
    body: SyntheticCollectionStartRequest | None = None,
    user: User = Depends(get_current_user),
    access_token: str = Depends(_access_token),
    service: SyntheticCollectionService = Depends(get_synthetic_service),
) -> SyntheticCollectionStartResponse:
    req = body or SyntheticCollectionStartRequest()
    try:
        return service.retry(
            user,
            chat_id,
            access_token=access_token,
            mode=req.mode,
        )
    except AppError as exc:
        _raise(exc)
    raise AssertionError  # pragma: no cover
