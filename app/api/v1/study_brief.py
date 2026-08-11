"""Study brief AI + upload + confirm endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.azure.blob_storage import get_blob_storage
from app.core.exceptions import AppError
from app.db.models.user import User
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.schemas.study_brief import (
    AiContinueEmptyResponse,
    AiTurnRequest,
    AiTurnResponse,
    AttachmentBrief,
    StudyBriefOut,
    StudyBriefUpdate,
    StudyConfirmResponse,
    UploadOut,
)
from app.services.study_brief_service import StudyBriefService

router = APIRouter(tags=["study-brief"])


def get_brief_service(db: Session = Depends(get_db)) -> StudyBriefService:
    return StudyBriefService(db)


def _raise(exc: AppError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/chats/{chat_id}/study-brief", response_model=StudyBriefOut)
def get_study_brief(
    chat_id: UUID,
    user: User = Depends(get_current_user),
    service: StudyBriefService = Depends(get_brief_service),
) -> StudyBriefOut:
    try:
        phase, brief = service.get_brief(user, chat_id)
    except AppError as exc:
        _raise(exc)
    return StudyBriefOut(phase=phase, study_brief=brief)


@router.patch("/chats/{chat_id}/study-brief", response_model=StudyBriefOut)
def patch_study_brief(
    chat_id: UUID,
    body: StudyBriefUpdate,
    user: User = Depends(get_current_user),
    service: StudyBriefService = Depends(get_brief_service),
) -> StudyBriefOut:
    try:
        phase, brief = service.update_brief(user, chat_id, body)
    except AppError as exc:
        _raise(exc)
    return StudyBriefOut(phase=phase, study_brief=brief)


@router.post(
    "/chats/{chat_id}/ai-turn",
    response_model=AiTurnResponse,
    status_code=status.HTTP_201_CREATED,
)
def ai_turn(
    chat_id: UUID,
    body: AiTurnRequest,
    user: User = Depends(get_current_user),
    service: StudyBriefService = Depends(get_brief_service),
) -> AiTurnResponse:
    attachments = list(body.attachments)
    seen = {a.url for a in attachments if a.url}
    for url in body.attachment_urls:
        cleaned = url.strip()
        if cleaned and cleaned not in seen:
            attachments.append(
                AttachmentBrief(
                    url=cleaned,
                    filename=cleaned.rsplit("/", 1)[-1],
                    content_type="",
                )
            )
            seen.add(cleaned)
    try:
        user_msg, assistant_msg, phase, brief, suggested = service.run_ai_turn(
            user,
            chat_id,
            content=body.content,
            attachments=attachments,
        )
    except AppError as exc:
        _raise(exc)
    return AiTurnResponse(
        user_message=user_msg.model_dump(mode="json"),
        assistant_message=assistant_msg.model_dump(mode="json"),
        phase=phase,
        study_brief=brief,
        suggested_chat_title=suggested,
    )


@router.post(
    "/chats/{chat_id}/ai-continue",
    response_model=AiTurnResponse | AiContinueEmptyResponse,
)
def ai_continue(
    chat_id: UUID,
    user: User = Depends(get_current_user),
    service: StudyBriefService = Depends(get_brief_service),
) -> AiTurnResponse | AiContinueEmptyResponse:
    """Generate an assistant reply when the chat ends on an unanswered user message."""
    try:
        result = service.continue_ai_if_needed(user, chat_id)
    except AppError as exc:
        _raise(exc)
    if result is None:
        return AiContinueEmptyResponse()
    assistant_msg, phase, brief, suggested = result
    return AiTurnResponse(
        user_message=None,
        assistant_message=assistant_msg.model_dump(mode="json"),
        phase=phase,
        study_brief=brief,
        suggested_chat_title=suggested,
        continued=True,
    )


@router.post(
    "/chats/{chat_id}/study-brief/confirm",
    response_model=StudyConfirmResponse,
)
def confirm_study_brief(
    chat_id: UUID,
    user: User = Depends(get_current_user),
    service: StudyBriefService = Depends(get_brief_service),
) -> StudyConfirmResponse:
    try:
        return service.confirm_brief(user, chat_id)
    except AppError as exc:
        _raise(exc)
    raise AssertionError  # pragma: no cover


@router.post(
    "/chats/{chat_id}/uploads",
    response_model=UploadOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_chat_file(
    chat_id: UUID,
    file: UploadFile = File(...),
    category: str | None = Form(default=None),
    relative_path: str | None = Form(default=None),
    user: User = Depends(get_current_user),
    service: StudyBriefService = Depends(get_brief_service),
) -> UploadOut:
    # Ownership check
    try:
        service.get_brief(user, chat_id)
    except AppError as exc:
        _raise(exc)

    data = await file.read()
    storage = get_blob_storage()
    try:
        uploaded = storage.upload_bytes(
            data=data,
            filename=file.filename or "upload.bin",
            content_type=file.content_type or "application/octet-stream",
            folder=f"mindsurve/chats/{chat_id}",
        )
    except AppError as exc:
        _raise(exc)

    cleaned_category = category.strip()[:100] if category and category.strip() else None
    cleaned_path = (
        relative_path.strip()[:500] if relative_path and relative_path.strip() else None
    )
    return UploadOut(
        url=uploaded.url,
        filename=uploaded.filename,
        content_type=uploaded.content_type,
        size_bytes=uploaded.size_bytes,
        category=cleaned_category,
        relative_path=cleaned_path,
    )
