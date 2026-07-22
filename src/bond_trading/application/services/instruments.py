from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.domain.value_objects import normalize_isin
from bond_trading.infrastructure.db.models import (
    BondInstrumentModel,
    CorporateActionModel,
    MarketSnapshotModel,
)
from bond_trading.infrastructure.moex import MoexIssClient


class InstrumentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[BondInstrumentModel]:
        result = await self._session.scalars(
            select(BondInstrumentModel).order_by(BondInstrumentModel.isin)
        )
        return list(result)

    async def get_by_isin(self, isin: str) -> BondInstrumentModel | None:
        normalized = normalize_isin(isin)
        return cast(
            BondInstrumentModel | None,
            await self._session.scalar(
                select(BondInstrumentModel).where(BondInstrumentModel.isin == normalized)
            ),
        )

    async def get_by_id(self, instrument_id: UUID) -> BondInstrumentModel | None:
        return await self._session.get(BondInstrumentModel, instrument_id)

    async def refresh(
        self, isin: str, client: MoexIssClient
    ) -> tuple[BondInstrumentModel, MarketSnapshotModel]:
        normalized_isin = normalize_isin(isin)
        instrument = await self.get_by_isin(normalized_isin)
        try:
            result = await client.refresh(normalized_isin)
        except Exception as exc:
            await self._record_refresh_error(instrument, exc)
            raise
        if instrument is None:
            instrument = BondInstrumentModel(
                isin=result.instrument.isin,
                secid=result.instrument.secid,
                short_name=result.instrument.short_name,
            )
            self._session.add(instrument)
        for field in (
            "secid",
            "short_name",
            "full_name",
            "primary_board_id",
            "currency",
            "initial_face_value",
            "current_face_value",
            "maturity_date",
            "offer_date",
            "coupon_period_days",
            "is_amortizing",
            "is_floating_coupon",
            "is_active",
            "source_updated_at",
        ):
            setattr(instrument, field, getattr(result.instrument, field))
        await self._session.flush()

        existing_hashes = set(
            await self._session.scalars(
                select(CorporateActionModel.source_payload_hash).where(
                    CorporateActionModel.instrument_id == instrument.id
                )
            )
        )
        source_updated_at = datetime.now(UTC)
        for action in result.actions:
            if action.source_payload_hash in existing_hashes:
                continue
            self._session.add(
                CorporateActionModel(
                    instrument_id=instrument.id,
                    action_type=action.action_type,
                    event_date=action.event_date,
                    record_date=action.record_date,
                    amount_rub_per_bond=action.amount_rub_per_bond,
                    percent=action.percent,
                    source="MOEX ISS",
                    source_payload_hash=action.source_payload_hash,
                    source_updated_at=source_updated_at,
                )
            )

        snapshot = MarketSnapshotModel(
            instrument_id=instrument.id,
            board_id=result.market.board_id,
            received_at=result.market.received_at,
            market_timestamp=result.market.market_timestamp,
            bid_percent=result.market.bid_percent,
            bid_rub_per_bond=result.market.bid_rub_per_bond,
            bid_depth_lots=result.market.bid_depth_lots,
            lot_size=result.market.lot_size,
            current_face_value=result.market.current_face_value,
            accrued_interest_rub_per_bond=result.market.accrued_interest_rub_per_bond,
            last_price_percent=result.market.last_price_percent,
            status=result.market.status,
            delayed_status=result.market.delayed_status,
            raw_payload=result.market.raw_payload,
        )
        self._session.add(snapshot)
        await self._session.commit()
        await self._session.refresh(instrument)
        await self._session.refresh(snapshot)
        return instrument, snapshot

    async def _record_refresh_error(
        self, instrument: BondInstrumentModel | None, error: Exception
    ) -> None:
        if instrument is None:
            return
        latest = await self._session.scalar(
            select(MarketSnapshotModel)
            .where(MarketSnapshotModel.instrument_id == instrument.id)
            .order_by(MarketSnapshotModel.received_at.desc())
            .limit(1)
        )
        if latest is None:
            return
        latest.status = "refresh_error"
        latest.error_message = str(error)[:2000]
        await self._session.commit()
