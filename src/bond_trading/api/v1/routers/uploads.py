from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.api.auth_dependencies import CurrentUser
from bond_trading.api.dependencies import get_object_storage
from bond_trading.api.schemas import UploadedFileOut
from bond_trading.infrastructure.db.models import UploadedFileModel, UserRole
from bond_trading.infrastructure.db.session import get_session
from bond_trading.infrastructure.storage import ObjectStorage

router = APIRouter(prefix="/uploads", tags=["uploads"])
Session = Annotated[AsyncSession, Depends(get_session)]
Storage = Annotated[ObjectStorage, Depends(get_object_storage)]


@router.get("", response_model=list[UploadedFileOut], summary="List current user's uploads")
async def list_uploads(user: CurrentUser, session: Session) -> list[UploadedFileOut]:
    uploads = await session.scalars(
        select(UploadedFileModel)
        .where(UploadedFileModel.owner_id == user.id)
        .order_by(UploadedFileModel.created_at.desc())
    )
    return [UploadedFileOut.model_validate(upload) for upload in uploads]


@router.get("/{upload_id}/download", summary="Download an original uploaded file")
async def download_upload(
    upload_id: UUID, user: CurrentUser, session: Session, storage: Storage
) -> Response:
    upload = await session.get(UploadedFileModel, upload_id)
    if upload is None or (upload.owner_id != user.id and user.role != UserRole.ADMIN):
        raise HTTPException(404, "Uploaded file not found")
    try:
        content = await storage.get(upload.object_key)
    except Exception as exc:
        raise HTTPException(503, "Object storage is temporarily unavailable") from exc
    encoded_name = quote(upload.original_file_name)
    return Response(
        content,
        media_type=upload.content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
    )
