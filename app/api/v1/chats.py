"""Chat and message HTTP endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.db.models.user import User
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.schemas.project import (
    ChatCreate,
    ChatOut,
    ChatStart,
    ChatStartOut,
    ChatUpdate,
    MessageCreate,
    MessageOut,
    MessagePageOut,
    MessageResponse,
)
from app.services.project_service import ProjectService

router = APIRouter(tags=["chats"])


def get_project_service(db: Session = Depends(get_db)) -> ProjectService:
    return ProjectService(db)


def _raise(exc: AppError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/chats", response_model=list[ChatOut])
def list_all_chats(
    user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> list[ChatOut]:
    """All chats for the current user (single join query + preview query)."""
    return service.list_all_chats(user)


@router.get("/projects/{project_id}/chats", response_model=list[ChatOut])
def list_project_chats(
    project_id: UUID,
    user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> list[ChatOut]:
    try:
        return service.list_chats_for_project(user, project_id)
    except AppError as exc:
        _raise(exc)
    return []  # pragma: no cover


@router.post(
    "/projects/{project_id}/chats",
    response_model=ChatOut,
    status_code=status.HTTP_201_CREATED,
)
def create_chat(
    project_id: UUID,
    body: ChatCreate,
    user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ChatOut:
    try:
        return service.create_chat(user, project_id, title=body.title)
    except AppError as exc:
        _raise(exc)
    raise AssertionError  # pragma: no cover


@router.post(
    "/projects/{project_id}/chats/start",
    response_model=ChatStartOut,
    status_code=status.HTTP_201_CREATED,
)
def start_chat(
    project_id: UUID,
    body: ChatStart,
    user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ChatStartOut:
    try:
        chat, message = service.start_chat_with_message(
            user, project_id, content=body.content
        )
    except AppError as exc:
        _raise(exc)
    return ChatStartOut(chat=chat, message=message)


@router.get("/chats/{chat_id}", response_model=ChatOut)
def get_chat(
    chat_id: UUID,
    user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ChatOut:
    try:
        chat = service.get_chat(user, chat_id)
    except AppError as exc:
        _raise(exc)
    return ChatOut.model_validate(chat)


@router.patch("/chats/{chat_id}", response_model=ChatOut)
def rename_chat(
    chat_id: UUID,
    body: ChatUpdate,
    user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ChatOut:
    try:
        return service.rename_chat(user, chat_id, title=body.title)
    except AppError as exc:
        _raise(exc)
    raise AssertionError  # pragma: no cover


@router.delete("/chats/{chat_id}", response_model=MessageResponse)
def delete_chat(
    chat_id: UUID,
    user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> MessageResponse:
    try:
        service.delete_chat(user, chat_id)
    except AppError as exc:
        _raise(exc)
    return MessageResponse(message="Chat deleted")


@router.get("/chats/{chat_id}/messages", response_model=MessagePageOut)
def list_messages(
    chat_id: UUID,
    limit: int = Query(default=40, ge=1, le=100),
    before: str | None = Query(default=None, max_length=256),
    user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> MessagePageOut:
    try:
        return service.list_messages(user, chat_id, limit=limit, before=before)
    except AppError as exc:
        _raise(exc)
    raise AssertionError  # pragma: no cover


@router.post(
    "/chats/{chat_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
def add_message(
    chat_id: UUID,
    body: MessageCreate,
    user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> MessageOut:
    try:
        return service.add_message(
            user,
            chat_id,
            content=body.content,
            role=body.role,
            metadata=body.metadata,
        )
    except AppError as exc:
        _raise(exc)
    raise AssertionError  # pragma: no cover
