from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.api.auth_dependencies import AdminUser
from bond_trading.api.schemas import (
    AdminUserCreate,
    AdminUserPatch,
    UploadedFileOut,
    UserOut,
)
from bond_trading.application.services.auth import AuthService
from bond_trading.core.config import get_settings
from bond_trading.domain.errors import DomainError
from bond_trading.infrastructure.db.models import UploadedFileModel
from bond_trading.infrastructure.db.session import get_session

router = APIRouter(prefix="/admin", tags=["admin"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/users", response_model=list[UserOut], summary="List all users")
async def list_users(admin: AdminUser, session: Session) -> list[UserOut]:
    del admin
    users = await AuthService(session, get_settings().auth).list_users()
    return [UserOut.model_validate(user) for user in users]


@router.post("/users", response_model=UserOut, status_code=201, summary="Create a user")
async def create_user(payload: AdminUserCreate, admin: AdminUser, session: Session) -> UserOut:
    del admin
    try:
        user = await AuthService(session, get_settings().auth).create_user(
            username=payload.username,
            email=payload.email,
            password=payload.password,
            role=payload.role,
        )
    except DomainError as exc:
        raise HTTPException(422, exc.message) from exc
    return UserOut.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserOut, summary="Enable or disable a user")
async def update_user(
    user_id: UUID,
    payload: AdminUserPatch,
    admin: AdminUser,
    session: Session,
) -> UserOut:
    service = AuthService(session, get_settings().auth)
    user = await service.get_user(user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if user.id == admin.id and not payload.is_active:
        raise HTTPException(422, "An administrator cannot disable the current account")
    try:
        await service.set_active(user, payload.is_active)
    except DomainError as exc:
        raise HTTPException(422, exc.message) from exc
    return UserOut.model_validate(user)


@router.get("/uploads", response_model=list[UploadedFileOut], summary="List uploads from all users")
async def list_all_uploads(admin: AdminUser, session: Session) -> list[UploadedFileOut]:
    del admin
    uploads = await session.scalars(
        select(UploadedFileModel).order_by(UploadedFileModel.created_at.desc())
    )
    return [UploadedFileOut.model_validate(upload) for upload in uploads]
