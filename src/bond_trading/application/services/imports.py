import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.application.services.sheets import enqueue_sheet_sync
from bond_trading.infrastructure.db.models import (
    BondInstrumentModel,
    BondLotModel,
    ImportBatchModel,
    SheetSyncTrigger,
    UploadedFileModel,
)
from bond_trading.infrastructure.imports import ImportPreview


@dataclass(frozen=True, slots=True)
class CachedPreview:
    preview: ImportPreview
    expires_at: float
    owner_id: UUID | None


class ImportPreviewCache:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = ttl_seconds
        self._values: dict[UUID, CachedPreview] = {}

    def put(self, preview: ImportPreview, owner_id: UUID | None = None) -> UUID:
        preview_id = uuid4()
        self._values[preview_id] = CachedPreview(
            preview=preview,
            expires_at=time.monotonic() + self._ttl_seconds,
            owner_id=owner_id,
        )
        return preview_id

    def get(self, preview_id: UUID, owner_id: UUID | None = None) -> ImportPreview | None:
        value = self._values.get(preview_id)
        if value is None:
            return None
        if owner_id is not None and value.owner_id != owner_id:
            return None
        if value.expires_at <= time.monotonic():
            self._values.pop(preview_id, None)
            return None
        return value.preview

    def discard(self, preview_id: UUID) -> None:
        self._values.pop(preview_id, None)


class ImportService:
    def __init__(self, session: AsyncSession, owner_id: UUID) -> None:
        self._session = session
        self._owner_id = owner_id

    async def commit(self, preview: ImportPreview) -> tuple[ImportBatchModel, bool]:
        existing = await self._session.scalar(
            select(ImportBatchModel).where(
                ImportBatchModel.checksum == preview.checksum,
                ImportBatchModel.sheet_name == preview.sheet_name,
                ImportBatchModel.owner_id == self._owner_id,
            )
        )
        if existing is not None:
            if preview.upload_id is not None:
                uploaded_file = await self._session.get(UploadedFileModel, preview.upload_id)
                if uploaded_file is not None:
                    uploaded_file.status = "duplicate"
                    uploaded_file.parse_error = f"Content already imported in batch {existing.id}"
                    await self._session.commit()
            return existing, True

        batch = ImportBatchModel(
            owner_id=self._owner_id,
            uploaded_file_id=preview.upload_id,
            file_name=preview.file_name,
            sheet_name=preview.sheet_name,
            rows_read=preview.rows_read,
            lots_created=0,
            instruments_updated=0,
            row_errors=[asdict(error) for error in preview.errors],
            checksum=preview.checksum,
        )
        self._session.add(batch)
        await self._session.flush()
        isins = {row.normalized_isin for row in preview.rows}
        existing_instruments = {
            instrument.isin: instrument
            for instrument in await self._session.scalars(
                select(BondInstrumentModel).where(BondInstrumentModel.isin.in_(isins))
            )
        }
        instruments_updated = 0
        for row in preview.rows:
            instrument = existing_instruments.get(row.normalized_isin)
            if instrument is None:
                instrument = BondInstrumentModel(
                    isin=row.normalized_isin,
                    secid=row.normalized_isin,
                    short_name=row.source_name or row.normalized_isin,
                    currency="RUB",
                )
                self._session.add(instrument)
                await self._session.flush()
                existing_instruments[row.normalized_isin] = instrument
                instruments_updated += 1
            elif row.source_name and instrument.short_name == instrument.isin:
                instrument.short_name = row.source_name
                instruments_updated += 1
            self._session.add(
                BondLotModel(
                    owner_id=self._owner_id,
                    instrument_id=instrument.id,
                    purchase_date=row.purchase_date,
                    quantity=row.quantity,
                    purchase_clean_price_rub_per_bond=(row.purchase_clean_price_rub_per_bond),
                    purchase_accrued_interest_rub_per_bond=(
                        row.purchase_accrued_interest_rub_per_bond
                    ),
                    purchase_commission_rub_per_bond=(row.purchase_commission_rub_per_bond),
                    target_event_type=row.target_event_type,
                    target_event_date=row.target_event_date,
                    target_redemption_price_rub_per_bond=(row.target_redemption_price_rub_per_bond),
                    target_redemption_override_reason=(
                        "Imported from source column N"
                        if row.target_redemption_price_rub_per_bond is not None
                        else None
                    ),
                    target_redemption_override_updated_at=(
                        datetime.now(UTC)
                        if row.target_redemption_price_rub_per_bond is not None
                        else None
                    ),
                    sale_commission_rub_per_bond=row.sale_commission_rub_per_bond,
                    planned_yield_manual_reference=(row.planned_yield_manual_reference),
                    source_row_number=row.row_number,
                    source_sheet_name=preview.sheet_name,
                    notes=(
                        f"Offer submission period: {row.offer_submission_period}"
                        if row.offer_submission_period
                        else None
                    ),
                    import_batch_id=batch.id,
                )
            )
        batch.lots_created = len(preview.rows)
        batch.instruments_updated = instruments_updated
        if preview.upload_id is not None:
            uploaded_file = await self._session.get(UploadedFileModel, preview.upload_id)
            if uploaded_file is not None:
                uploaded_file.status = "imported"
        await enqueue_sheet_sync(
            self._session,
            self._owner_id,
            SheetSyncTrigger.IMPORT_COMMITTED,
        )
        await self._session.commit()
        await self._session.refresh(batch)
        return batch, False
