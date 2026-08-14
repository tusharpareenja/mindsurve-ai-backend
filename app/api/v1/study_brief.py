"""Study brief AI + upload + confirm endpoints."""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.azure.blob_storage import get_blob_storage
from app.services.document_extract import extract_document_text
from app.core.exceptions import AppError
from app.db.models.user import User
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.schemas.study_brief import (
    AiContinueEmptyResponse,
    AiTurnRequest,
    AiTurnResponse,
    AttachmentBrief,
    BriefVersionListOut,
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
    return StudyBriefOut(
        phase=phase,
        study_brief=brief,
        version=service.version_meta(chat_id),
    )


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
    return StudyBriefOut(
        phase=phase,
        study_brief=brief,
        version=service.version_meta(chat_id),
    )


@router.get(
    "/chats/{chat_id}/study-brief/versions",
    response_model=BriefVersionListOut,
)
def list_study_brief_versions(
    chat_id: UUID,
    user: User = Depends(get_current_user),
    service: StudyBriefService = Depends(get_brief_service),
) -> BriefVersionListOut:
    try:
        return service.list_versions(user, chat_id)
    except AppError as exc:
        _raise(exc)
    raise AssertionError  # pragma: no cover


@router.post(
    "/chats/{chat_id}/study-brief/versions/{version}/restore",
    response_model=StudyBriefOut,
)
def restore_study_brief_version(
    chat_id: UUID,
    version: int,
    user: User = Depends(get_current_user),
    service: StudyBriefService = Depends(get_brief_service),
) -> StudyBriefOut:
    try:
        phase, brief, _changed = service.restore_version(user, chat_id, version)
    except AppError as exc:
        _raise(exc)
    return StudyBriefOut(
        phase=phase,
        study_brief=brief,
        version=service.version_meta(chat_id),
    )


@router.post("/chats/{chat_id}/ai-think-stream")
def ai_think_stream(
    chat_id: UUID,
    body: AiTurnRequest,
    user: User = Depends(get_current_user),
    service: StudyBriefService = Depends(get_brief_service),
) -> StreamingResponse:
    """Stream the model's live thinking for this turn (does not save the brief)."""
    attachments = list(body.attachments)

    def events():
        try:
            for piece in service.iter_thinking_tokens(
                user,
                chat_id,
                content=body.content,
                attachments=attachments,
            ):
                yield f"data: {json.dumps({'text': piece}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except AppError as exc:
            yield f"data: {json.dumps({'error': exc.message})}\n\n"
        except Exception:
            yield f"data: {json.dumps({'error': 'Thinking stream stopped.'})}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
    changed = []
    if isinstance(assistant_msg.metadata, dict):
        raw = assistant_msg.metadata.get("changed_fields")
        if isinstance(raw, list):
            changed = [str(item) for item in raw]
    return AiTurnResponse(
        user_message=user_msg.model_dump(mode="json"),
        assistant_message=assistant_msg.model_dump(mode="json"),
        phase=phase,
        study_brief=brief,
        suggested_chat_title=suggested,
        version=service.version_meta(chat_id),
        changed_fields=changed,
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
    changed = []
    if isinstance(assistant_msg.metadata, dict):
        raw = assistant_msg.metadata.get("changed_fields")
        if isinstance(raw, list):
            changed = [str(item) for item in raw]
    return AiTurnResponse(
        user_message=None,
        assistant_message=assistant_msg.model_dump(mode="json"),
        phase=phase,
        study_brief=brief,
        suggested_chat_title=suggested,
        continued=True,
        version=service.version_meta(chat_id),
        changed_fields=changed,
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
    # Prefer the browser's original name/type for extraction — blob storage may
    # sanitize the stored filename.
    original_name = (file.filename or "upload.bin").strip() or "upload.bin"
    original_type = (file.content_type or "").strip() or "application/octet-stream"

    storage = get_blob_storage()
    try:
        uploaded = storage.upload_bytes(
            data=data,
            filename=original_name,
            content_type=original_type,
            folder=f"mindsurve/chats/{chat_id}",
        )
    except AppError as exc:
        _raise(exc)

    cleaned_category = category.strip()[:100] if category and category.strip() else None
    cleaned_path = (
        relative_path.strip()[:500] if relative_path and relative_path.strip() else None
    )
    extracted = extract_document_text(
        filename=original_name,
        content_type=uploaded.content_type or original_type,
        data=data,
    )
    return UploadOut(
        url=uploaded.url,
        filename=uploaded.filename or original_name,
        content_type=uploaded.content_type,
        size_bytes=uploaded.size_bytes,
        category=cleaned_category,
        relative_path=cleaned_path,
        extracted_text=extracted,
    )
