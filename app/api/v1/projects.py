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
    MessageResponse,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
)
from app.services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


def get_project_service(db: Session = Depends(get_db)) -> ProjectService:
    return ProjectService(db)


def _raise(exc: AppError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("", response_model=list[ProjectOut])
def list_projects(
    user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> list[ProjectOut]:
    projects = service.list_projects(user)
    return [ProjectOut.from_project(p) for p in projects]


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
    return ProjectOut.from_project(project)


@router.get("/inbox", response_model=ProjectOut)
def get_or_create_inbox(
    user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectOut:
    project = service.ensure_inbox(user)
    return ProjectOut.from_project(project)


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
    return ProjectOut.from_project(project)


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
    return ProjectOut.from_project(project)


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
