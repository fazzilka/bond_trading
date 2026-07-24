import hashlib
import re
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.infrastructure.db.models import UploadedFileModel
from bond_trading.infrastructure.storage import ObjectStorage


class UploadService:
    def __init__(
        self,
        session: AsyncSession,
        storage: ObjectStorage,
        owner_id: UUID,
    ) -> None:
        self._session = session
        self._storage = storage
        self._owner_id = owner_id

    async def store(
        self,
        *,
        file_name: str,
        content_type: str,
        content: bytes,
    ) -> UploadedFileModel:
        upload_id = uuid4()
        safe_name = _safe_name(file_name)
        object_key = f"users/{self._owner_id}/uploads/{upload_id}/{safe_name}"
        checksum = hashlib.sha256(content).hexdigest()
        await self._storage.put(object_key, content, content_type)
        upload = UploadedFileModel(
            id=upload_id,
            owner_id=self._owner_id,
            original_file_name=Path(file_name).name,
            object_key=object_key,
            content_type=content_type,
            file_format=Path(file_name).suffix.lower().lstrip("."),
            size_bytes=len(content),
            checksum=checksum,
            status="uploaded",
        )
        self._session.add(upload)
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            await self._storage.delete(object_key)
            raise
        await self._session.refresh(upload)
        return upload

    async def get(self, upload_id: UUID) -> UploadedFileModel | None:
        return cast(
            UploadedFileModel | None,
            await self._session.scalar(
                select(UploadedFileModel).where(
                    UploadedFileModel.id == upload_id,
                    UploadedFileModel.owner_id == self._owner_id,
                )
            ),
        )

    async def mark_parsed(self, upload: UploadedFileModel) -> None:
        upload.status = "parsed"
        upload.parse_error = None
        await self._session.commit()

    async def mark_failed(self, upload: UploadedFileModel, error: Exception) -> None:
        upload.status = "failed"
        upload.parse_error = str(error)[:4000]
        await self._session.commit()


def _safe_name(file_name: str) -> str:
    name = Path(file_name).name
    sanitized = re.sub(r"[^A-Za-zА-Яа-яЁё0-9._ -]+", "_", name).strip(" .")
    return sanitized[:255] or "spreadsheet"
