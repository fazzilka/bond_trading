from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from bond_trading.application.services.settings import SettingsService
from bond_trading.application.services.sheets import enqueue_sheet_sync
from bond_trading.domain.calculations import (
    CorporateCashFlow,
    CurrentYieldInput,
    PlannedYieldInput,
    PurchaseInput,
    TaxPolicy,
    YieldResult,
    calculate_current_yield,
    calculate_planned_yield,
)
from bond_trading.domain.errors import DomainError
from bond_trading.domain.value_objects import normalize_isin
from bond_trading.infrastructure.db.models import (
    BondInstrumentModel,
    BondLotModel,
    CorporateActionModel,
    MarketSnapshotModel,
    SheetSyncTrigger,
    YieldSnapshotModel,
)


@dataclass(frozen=True, slots=True)
class CalculationBundle:
    snapshot: YieldSnapshotModel
    planned: YieldResult
    current: YieldResult | None


class LotService:
    def __init__(
        self,
        session: AsyncSession,
        timezone: ZoneInfo,
        owner_id: UUID,
    ) -> None:
        self._session = session
        self._timezone = timezone
        self._owner_id = owner_id

    async def list_all(self) -> list[BondLotModel]:
        statement = (
            select(BondLotModel)
            .options(joinedload(BondLotModel.instrument))
            .order_by(BondLotModel.purchase_date, BondLotModel.created_at)
        )
        statement = statement.where(BondLotModel.owner_id == self._owner_id)
        result = await self._session.scalars(statement)
        return list(result.unique())

    async def get(self, lot_id: UUID) -> BondLotModel | None:
        statement = (
            select(BondLotModel)
            .options(joinedload(BondLotModel.instrument))
            .where(BondLotModel.id == lot_id)
        )
        statement = statement.where(BondLotModel.owner_id == self._owner_id)
        return cast(
            BondLotModel | None,
            await self._session.scalar(statement),
        )

    async def create(self, values: dict[str, Any]) -> BondLotModel:
        isin = normalize_isin(str(values.pop("isin")))
        source_name = values.pop("source_name", None)
        if values.get("target_redemption_price_rub_per_bond") is not None:
            values["target_redemption_override_updated_at"] = datetime.now(UTC)
        instrument = await self._session.scalar(
            select(BondInstrumentModel).where(BondInstrumentModel.isin == isin)
        )
        if instrument is None:
            instrument = BondInstrumentModel(
                isin=isin,
                secid=isin,
                short_name=str(source_name or isin),
                currency="RUB",
            )
            self._session.add(instrument)
            await self._session.flush()
        lot = BondLotModel(owner_id=self._owner_id, instrument_id=instrument.id, **values)
        self._session.add(lot)
        await enqueue_sheet_sync(self._session, self._owner_id, SheetSyncTrigger.LOT_CREATED)
        await self._session.commit()
        return await self._required(lot.id)

    async def update(self, lot_id: UUID, values: dict[str, Any]) -> BondLotModel:
        lot = await self._required(lot_id)
        if "target_redemption_price_rub_per_bond" in values:
            if values["target_redemption_price_rub_per_bond"] is None:
                values["target_redemption_override_reason"] = None
                values["target_redemption_override_updated_at"] = None
            else:
                values["target_redemption_override_updated_at"] = datetime.now(UTC)
        elif "target_redemption_override_reason" in values:
            values["target_redemption_override_updated_at"] = datetime.now(UTC)
        for field, value in values.items():
            setattr(lot, field, value)
        await enqueue_sheet_sync(self._session, self._owner_id, SheetSyncTrigger.LOT_UPDATED)
        await self._session.commit()
        return await self._required(lot_id)

    async def delete(self, lot_id: UUID) -> bool:
        lot = await self._required(lot_id)
        await self._session.delete(lot)
        await enqueue_sheet_sync(self._session, self._owner_id, SheetSyncTrigger.LOT_DELETED)
        await self._session.commit()
        return True

    async def calculate(
        self, lot_id: UUID, valuation_date: date | None = None
    ) -> CalculationBundle:
        lot = await self._required(lot_id)
        actions = list(
            await self._session.scalars(
                select(CorporateActionModel)
                .where(CorporateActionModel.instrument_id == lot.instrument_id)
                .order_by(CorporateActionModel.event_date)
            )
        )
        cashflows = tuple(
            CorporateCashFlow(
                action_type=action.action_type,
                event_date=action.event_date,
                amount_rub_per_bond=action.amount_rub_per_bond or Decimal(0),
            )
            for action in actions
            if action.amount_rub_per_bond is not None
        )
        settings = await SettingsService(self._session, self._owner_id).get()
        tax_policy = TaxPolicy(settings.tax_mode, settings.tax_rate)
        purchase = PurchaseInput(
            purchase_date=lot.purchase_date,
            quantity=lot.quantity,
            clean_price_rub_per_bond=lot.purchase_clean_price_rub_per_bond,
            accrued_interest_rub_per_bond=lot.purchase_accrued_interest_rub_per_bond,
            commission_rub_per_bond=lot.purchase_commission_rub_per_bond,
        )
        redemption = self._target_redemption(lot, actions)
        planned = calculate_planned_yield(
            PlannedYieldInput(
                purchase=purchase,
                target_date=lot.target_event_date,
                final_redemption_rub_per_bond=redemption,
                cashflows=cashflows,
                sale_commission_rub_per_bond=lot.sale_commission_rub_per_bond,
                tax_policy=tax_policy,
            )
        )
        market = await self._session.scalar(
            select(MarketSnapshotModel)
            .where(MarketSnapshotModel.instrument_id == lot.instrument_id)
            .order_by(MarketSnapshotModel.received_at.desc())
            .limit(1)
        )
        current: YieldResult | None = None
        calculation_date = valuation_date or self._valuation_date(market)
        if (
            market is not None
            and market.bid_rub_per_bond is not None
            and market.accrued_interest_rub_per_bond is not None
        ):
            current = calculate_current_yield(
                CurrentYieldInput(
                    purchase=purchase,
                    valuation_date=calculation_date,
                    bid_rub_per_bond=market.bid_rub_per_bond,
                    current_accrued_interest_rub_per_bond=(market.accrued_interest_rub_per_bond),
                    cashflows=cashflows,
                    sale_commission_rub_per_bond=lot.sale_commission_rub_per_bond,
                    tax_policy=tax_policy,
                )
            )
        yield_delta = (
            current.annual_yield_after_tax - planned.annual_yield_after_tax
            if current is not None
            else None
        )
        snapshot = YieldSnapshotModel(
            lot_id=lot.id,
            valuation_date=calculation_date,
            market_snapshot_id=market.id if market else None,
            purchase_total=planned.purchase.purchase_total,
            planned_exit_total=planned.exit_total,
            planned_profit_before_tax=planned.profit_before_tax,
            planned_profit_after_tax=planned.profit_after_tax,
            planned_annual_yield_before_tax=planned.annual_yield_before_tax,
            planned_annual_yield_after_tax=planned.annual_yield_after_tax,
            current_exit_total=current.exit_total if current else None,
            current_profit_before_tax=current.profit_before_tax if current else None,
            current_profit_after_tax=current.profit_after_tax if current else None,
            current_annual_yield_before_tax=(current.annual_yield_before_tax if current else None),
            current_annual_yield_after_tax=(current.annual_yield_after_tax if current else None),
            yield_delta_pp=yield_delta,
            calculation_version=settings.formula_version,
            calculation_details={
                "tax_mode": settings.tax_mode.value,
                "tax_rate": str(settings.tax_rate),
                "planned": _jsonable_result(planned),
                "current": _jsonable_result(current) if current else None,
            },
        )
        self._session.add(snapshot)
        await self._session.commit()
        await self._session.refresh(snapshot)
        return CalculationBundle(snapshot=snapshot, planned=planned, current=current)

    async def history(self, lot_id: UUID) -> list[YieldSnapshotModel]:
        await self._required(lot_id)
        result = await self._session.scalars(
            select(YieldSnapshotModel)
            .where(YieldSnapshotModel.lot_id == lot_id)
            .order_by(YieldSnapshotModel.created_at.desc())
        )
        return list(result)

    async def _required(self, lot_id: UUID) -> BondLotModel:
        lot = await self.get(lot_id)
        if lot is None:
            raise DomainError("Bond lot was not found", {"lot_id": str(lot_id)})
        return lot

    def _target_redemption(self, lot: BondLotModel, actions: list[CorporateActionModel]) -> Decimal:
        if lot.target_redemption_price_rub_per_bond is not None:
            return lot.target_redemption_price_rub_per_bond
        wanted_type = "maturity" if lot.target_event_type == "maturity" else "offer"
        for action in actions:
            if (
                action.action_type.value == wanted_type
                and action.event_date == lot.target_event_date
                and action.amount_rub_per_bond is not None
            ):
                return action.amount_rub_per_bond
        if lot.target_event_type == "maturity" and lot.instrument.current_face_value is not None:
            return lot.instrument.current_face_value
        raise DomainError(
            "Target redemption value is unavailable; set a manual override or refresh MOEX data",
            {"lot_id": str(lot.id)},
        )

    def _valuation_date(self, market: MarketSnapshotModel | None) -> date:
        timestamp = market.market_timestamp if market else None
        if timestamp is None:
            return datetime.now(self._timezone).date()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return timestamp.astimezone(self._timezone).date()


def _jsonable_result(result: YieldResult) -> dict[str, object]:
    def convert(value: object) -> object:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            return {str(key): convert(item) for key, item in value.items()}
        return value

    return {key: convert(value) for key, value in asdict(result).items()}
