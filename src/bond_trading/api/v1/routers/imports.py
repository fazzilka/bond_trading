from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.api.dependencies import get_import_cache
from bond_trading.api.schemas import (
    ImportBatchOut,
    ImportCommitRequest,
    ImportErrorOut,
    ImportPreviewOut,
    ImportRowOut,
)
from bond_trading.application.services.imports import ImportPreviewCache, ImportService
from bond_trading.core.config import get_settings
from bond_trading.infrastructure.db.models import ImportBatchModel
from bond_trading.infrastructure.db.session import get_session
from bond_trading.infrastructure.imports import (
    XlsxImportError,
    XlsxPortfolioReader,
    validate_xlsx_upload,
)

router = APIRouter(prefix="/imports", tags=["imports"])
Session = Annotated[AsyncSession, Depends(get_session)]
Cache = Annotated[ImportPreviewCache, Depends(get_import_cache)]


@router.post("/preview", response_model=ImportPreviewOut, summary="Preview an XLSX import")
async def preview_import(
    cache: Cache,
    file: Annotated[UploadFile, File()],
) -> ImportPreviewOut:
    try:
        validate_xlsx_upload(file.filename or "", file.content_type)
    except XlsxImportError as exc:
        raise HTTPException(422, str(exc)) from exc
    content = await file.read(get_settings().imports.max_upload_bytes + 1)
    if len(content) > get_settings().imports.max_upload_bytes:
        raise HTTPException(413, "Uploaded XLSX file is too large")
    try:
        preview = XlsxPortfolioReader().preview(content, file.filename or "upload.xlsx")
    except XlsxImportError as exc:
        raise HTTPException(422, str(exc)) from exc
    preview_id = cache.put(preview)
    return ImportPreviewOut(
        preview_id=preview_id,
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
    payload: ImportCommitRequest, session: Session, cache: Cache
) -> ImportBatchOut:
    preview = cache.get(payload.preview_id)
    if preview is None:
        raise HTTPException(404, "Import preview expired or was not found")
    batch, idempotent = await ImportService(session).commit(preview)
    cache.discard(payload.preview_id)
    output = ImportBatchOut.model_validate(batch)
    return output.model_copy(update={"idempotent_replay": idempotent})


@router.get("/{batch_id}", response_model=ImportBatchOut, summary="Get import batch details")
async def get_import_batch(batch_id: UUID, session: Session) -> ImportBatchOut:
    batch = await session.get(ImportBatchModel, batch_id)
    if batch is None:
        raise HTTPException(404, "Import batch not found")
    return ImportBatchOut.model_validate(batch)
