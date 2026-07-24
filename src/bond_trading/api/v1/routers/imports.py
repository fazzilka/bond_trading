from dataclasses import asdict, replace
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.api.auth_dependencies import CurrentUser
from bond_trading.api.dependencies import get_import_cache, get_object_storage
from bond_trading.api.schemas import (
    ImportBatchOut,
    ImportCommitRequest,
    ImportErrorOut,
    ImportPreviewOut,
    ImportRowOut,
)
from bond_trading.application.services.imports import ImportPreviewCache, ImportService
from bond_trading.application.services.uploads import UploadService
from bond_trading.core.config import get_settings
from bond_trading.infrastructure.db.models import ImportBatchModel, UserRole
from bond_trading.infrastructure.db.session import get_session
from bond_trading.infrastructure.imports import (
    SpreadsheetImportError,
    SpreadsheetPortfolioReader,
    validate_spreadsheet_upload,
)
from bond_trading.infrastructure.storage import ObjectStorage

router = APIRouter(prefix="/imports", tags=["imports"])
Session = Annotated[AsyncSession, Depends(get_session)]
Cache = Annotated[ImportPreviewCache, Depends(get_import_cache)]
Storage = Annotated[ObjectStorage, Depends(get_object_storage)]


@router.post(
    "/preview", response_model=ImportPreviewOut, summary="Store and preview a spreadsheet import"
)
async def preview_import(
    cache: Cache,
    storage: Storage,
    user: CurrentUser,
    session: Session,
    file: Annotated[UploadFile, File()],
) -> ImportPreviewOut:
    try:
        validate_spreadsheet_upload(file.filename or "", file.content_type)
    except SpreadsheetImportError as exc:
        raise HTTPException(422, str(exc)) from exc
    content = await file.read(get_settings().imports.max_upload_bytes + 1)
    if len(content) > get_settings().imports.max_upload_bytes:
        raise HTTPException(413, "Uploaded spreadsheet is too large")
    content_type = file.content_type or "application/octet-stream"
    upload_service = UploadService(session, storage, user.id)
    try:
        upload = await upload_service.store(
            file_name=file.filename or "spreadsheet",
            content_type=content_type,
            content=content,
        )
    except Exception as exc:
        raise HTTPException(503, "Object storage is temporarily unavailable") from exc
    try:
        preview = SpreadsheetPortfolioReader().preview(content, file.filename or "spreadsheet")
        preview = replace(preview, upload_id=upload.id)
        await upload_service.mark_parsed(upload)
    except SpreadsheetImportError as exc:
        await upload_service.mark_failed(upload, exc)
        raise HTTPException(422, str(exc)) from exc
    preview_id = cache.put(preview, user.id)
    return ImportPreviewOut(
        preview_id=preview_id,
        upload_id=upload.id,
        file_name=preview.file_name,
        sheet_name=preview.sheet_name,
        checksum=preview.checksum,
        header_row_number=preview.header_row_number,
        rows_read=preview.rows_read,
        rows=[ImportRowOut(**asdict(row)) for row in preview.rows],
        errors=[ImportErrorOut(**asdict(error)) for error in preview.errors],
    )


@router.post("/commit", response_model=ImportBatchOut, summary="Commit a previewed import")
async def commit_import(
    payload: ImportCommitRequest, user: CurrentUser, session: Session, cache: Cache
) -> ImportBatchOut:
    preview = cache.get(payload.preview_id, user.id)
    if preview is None:
        raise HTTPException(404, "Import preview expired or was not found")
    batch, idempotent = await ImportService(session, user.id).commit(preview)
    cache.discard(payload.preview_id)
    output = ImportBatchOut.model_validate(batch)
    return output.model_copy(update={"idempotent_replay": idempotent})


@router.get("/{batch_id}", response_model=ImportBatchOut, summary="Get import batch details")
async def get_import_batch(batch_id: UUID, user: CurrentUser, session: Session) -> ImportBatchOut:
    batch = await session.get(ImportBatchModel, batch_id)
    if batch is None or (batch.owner_id != user.id and user.role != UserRole.ADMIN):
        raise HTTPException(404, "Import batch not found")
    return ImportBatchOut.model_validate(batch)
