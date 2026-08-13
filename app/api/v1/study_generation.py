"""Study task-generation orchestration endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.db.models.user import User
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.schemas.study_brief import (
    AudienceBrief,
    CategoryBrief,
    ClassificationQuestionBrief,
    RatingScaleBrief,
    StudyBriefUpdate,
)
from app.schemas.study_generation import (
    BriefChangePreview,
    BriefRegenerateRequest,
    GenerationLaunchResponse,
    GenerationRunOut,
    GenerationStartResponse,
)
from app.services.study_generation_service import StudyGenerationService

router = APIRouter(tags=["study-generation"])
_bearer = HTTPBearer(auto_error=False)


def get_generation_service(db: Session = Depends(get_db)) -> StudyGenerationService:
    return StudyGenerationService(db)


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
    "/chats/{chat_id}/study-generation/start",
    response_model=GenerationStartResponse,
    status_code=status.HTTP_201_CREATED,
)
def start_generation(
    chat_id: UUID,
    user: User = Depends(get_current_user),
    access_token: str = Depends(_access_token),
    service: StudyGenerationService = Depends(get_generation_service),
) -> GenerationStartResponse:
    try:
        return service.start(user, chat_id, access_token=access_token)
    except AppError as exc:
        _raise(exc)
    raise AssertionError  # pragma: no cover


@router.get(
    "/chats/{chat_id}/study-generation",
    response_model=GenerationRunOut,
)
def get_generation_status(
    chat_id: UUID,
    run_id: UUID | None = None,
    user: User = Depends(get_current_user),
    access_token: str = Depends(_access_token),
    service: StudyGenerationService = Depends(get_generation_service),
) -> GenerationRunOut:
    try:
        return service.get_status(
            user, chat_id, access_token=access_token, run_id=run_id
        )
    except AppError as exc:
        _raise(exc)
    raise AssertionError  # pragma: no cover


@router.post(
    "/chats/{chat_id}/study-generation/retry",
    response_model=GenerationStartResponse,
)
def retry_generation(
    chat_id: UUID,
    user: User = Depends(get_current_user),
    access_token: str = Depends(_access_token),
    service: StudyGenerationService = Depends(get_generation_service),
) -> GenerationStartResponse:
    try:
        return service.retry(user, chat_id, access_token=access_token)
    except AppError as exc:
        _raise(exc)
    raise AssertionError  # pragma: no cover


@router.post(
    "/chats/{chat_id}/study-generation/preview-changes",
    response_model=BriefChangePreview,
)
def preview_brief_changes(
    chat_id: UUID,
    body: StudyBriefUpdate,
    user: User = Depends(get_current_user),
    service: StudyGenerationService = Depends(get_generation_service),
) -> BriefChangePreview:
    try:
        return service.preview_brief_changes(user, chat_id, body)
    except AppError as exc:
        _raise(exc)
    raise AssertionError  # pragma: no cover


@router.post(
    "/chats/{chat_id}/study-generation/regenerate",
    response_model=GenerationStartResponse,
)
def regenerate_after_edit(
    chat_id: UUID,
    body: BriefRegenerateRequest,
    user: User = Depends(get_current_user),
    access_token: str = Depends(_access_token),
    service: StudyGenerationService = Depends(get_generation_service),
) -> GenerationStartResponse:
    patch_data: dict = body.model_dump(exclude_unset=True, exclude={"confirm_regeneration"})
    # Drop Nones so StudyBriefUpdate treats them as unset.
    patch_data = {k: v for k, v in patch_data.items() if v is not None}
    if "rating_scale" in patch_data and isinstance(patch_data["rating_scale"], dict):
        patch_data["rating_scale"] = RatingScaleBrief.model_validate(patch_data["rating_scale"])
    if "categories" in patch_data and isinstance(patch_data["categories"], list):
        patch_data["categories"] = [
            CategoryBrief.model_validate(item) for item in patch_data["categories"]
        ]
    if "classification_questions" in patch_data and isinstance(
        patch_data["classification_questions"], list
    ):
        patch_data["classification_questions"] = [
            ClassificationQuestionBrief.model_validate(item)
            for item in patch_data["classification_questions"]
        ]
    if "audience" in patch_data and isinstance(patch_data["audience"], dict):
        patch_data["audience"] = AudienceBrief.model_validate(patch_data["audience"])

    try:
        patch = StudyBriefUpdate.model_validate(patch_data)
        return service.apply_brief_and_regenerate(
            user,
            chat_id,
            patch,
            access_token=access_token,
            confirm_regeneration=body.confirm_regeneration,
        )
    except AppError as exc:
        _raise(exc)
    raise AssertionError  # pragma: no cover


@router.post(
    "/chats/{chat_id}/study-generation/launch",
    response_model=GenerationLaunchResponse,
)
def launch_study(
    chat_id: UUID,
    user: User = Depends(get_current_user),
    access_token: str = Depends(_access_token),
    service: StudyGenerationService = Depends(get_generation_service),
) -> GenerationLaunchResponse:
    try:
        return service.launch(user, chat_id, access_token=access_token)
    except AppError as exc:
        _raise(exc)
    raise AssertionError  # pragma: no cover
