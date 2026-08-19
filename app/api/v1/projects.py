"""Project HTTP endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.db.models.user import User
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.schemas.project import (
    CollaboratorInvite,
    CollaboratorInviteResult,
    CollaboratorOut,
    MessageResponse,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
)
from app.services.collaborator_service import CollaboratorService
from app.services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


def get_project_service(db: Session = Depends(get_db)) -> ProjectService:
    return ProjectService(db)


def get_collaborator_service(db: Session = Depends(get_db)) -> CollaboratorService:
    return CollaboratorService(db)


def _raise(exc: AppError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("", response_model=list[ProjectOut])
def list_projects(
    user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> list[ProjectOut]:
    projects = service.list_projects(user)
    return [ProjectOut.from_project(p, viewer_id=user.id) for p in projects]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreate,
    user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectOut:
    try:
        project = service.create_project(user, title=body.title)
    except AppError as exc:
        _raise(exc)
    return ProjectOut.from_project(project, viewer_id=user.id)


@router.get("/inbox", response_model=ProjectOut)
def get_or_create_inbox(
    user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectOut:
    project = service.ensure_inbox(user)
    return ProjectOut.from_project(project, viewer_id=user.id)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: UUID,
    user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectOut:
    try:
        project = service.get_project(user, project_id)
    except AppError as exc:
        _raise(exc)
    return ProjectOut.from_project(project, viewer_id=user.id)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: UUID,
    body: ProjectUpdate,
    user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectOut:
    try:
        project = service.rename_project(user, project_id, title=body.title)
    except AppError as exc:
        _raise(exc)
    return ProjectOut.from_project(project, viewer_id=user.id)


@router.delete("/{project_id}", response_model=MessageResponse)
def delete_project(
    project_id: UUID,
    user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> MessageResponse:
    try:
        service.delete_project(user, project_id)
    except AppError as exc:
        _raise(exc)
    return MessageResponse(message="Project deleted")


@router.get("/{project_id}/collaborators", response_model=list[CollaboratorOut])
def list_collaborators(
    project_id: UUID,
    user: User = Depends(get_current_user),
    service: CollaboratorService = Depends(get_collaborator_service),
) -> list[CollaboratorOut]:
    try:
        rows = service.list_collaborators(user, project_id)
    except AppError as exc:
        _raise(exc)
    return [CollaboratorOut.model_validate(r) for r in rows]


@router.post(
    "/{project_id}/collaborators",
    response_model=CollaboratorInviteResult,
    status_code=status.HTTP_201_CREATED,
)
def invite_collaborator(
    project_id: UUID,
    body: CollaboratorInvite,
    user: User = Depends(get_current_user),
    service: CollaboratorService = Depends(get_collaborator_service),
) -> CollaboratorInviteResult:
    try:
        member = service.invite(user, project_id, email=body.email)
    except AppError as exc:
        _raise(exc)
    pending = member.user_id is None
    return CollaboratorInviteResult(
        id=str(member.id),
        email=member.invited_email,
        status="pending" if pending else "active",
        project_id=project_id,
        message=(
            "Invitation sent. They’ll see this project after signing in."
            if pending
            else "Collaborator added. They can open this project now."
        ),
    )


@router.delete(
    "/{project_id}/collaborators/{member_id}",
    response_model=MessageResponse,
)
def remove_collaborator(
    project_id: UUID,
    member_id: UUID,
    user: User = Depends(get_current_user),
    service: CollaboratorService = Depends(get_collaborator_service),
) -> MessageResponse:
    try:
        service.remove(user, project_id, member_id)
    except AppError as exc:
        _raise(exc)
    return MessageResponse(message="Collaborator removed")
