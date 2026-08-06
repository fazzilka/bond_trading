import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.application.services.instruments import InstrumentService, MoexRefreshClient
from bond_trading.application.services.sheet_calculations import (
    CALCULATION_HEADERS,
    DEFAULT_PURCHASE_COMMISSION,
    PURCHASE_ACCRUED_COLUMN,
    PURCHASE_COMMISSION_COLUMN,
    PURCHASE_DATE_COLUMN,
    SHEET_INPUT_COLUMNS,
    calculate_sheet_current,
    parse_date,
    parse_decimal,
    parse_sheet_purchase,
    summarize_paid_coupons,
)
from bond_trading.domain.calculations import CorporateCashFlow
from bond_trading.domain.value_objects import normalize_isin
from bond_trading.infrastructure.db.models import (
    BondInstrumentModel,
    CorporateActionModel,
    MarketSnapshotModel,
    SheetConnectionModel,
    SheetPriceMode,
    SheetSyncJobModel,
    SheetSyncJobStatus,
    SheetSyncTrigger,
)
from bond_trading.infrastructure.google_sheets import (
    CellUpdate,
    GoogleSheetsGateway,
    SheetDataRow,
    extract_spreadsheet_id,
    normalize_column,
)


class SheetMoexClient(MoexRefreshClient, Protocol):
    async def accrued_interest_on(
        self,
        secid: str,
        board_id: str | None,
        value_date: date,
    ) -> Decimal | None: ...


@dataclass(frozen=True, slots=True)
class _RefreshedInstrument:
    instrument: BondInstrumentModel
    market: MarketSnapshotModel
    cashflows: tuple[CorporateCashFlow, ...]
    price: Decimal | None
    market_time: datetime | None
    status: str


class SheetSyncService:
    def __init__(self, session: AsyncSession, owner_id: UUID | None = None) -> None:
        self._session = session
        self._owner_id = owner_id

    async def get_connection(self) -> SheetConnectionModel | None:
        owner_id = self._required_owner()
        return cast(
            SheetConnectionModel | None,
            await self._session.scalar(
                select(SheetConnectionModel).where(
                    SheetConnectionModel.owner_id == owner_id,
                    SheetConnectionModel.provider == "google_sheets",
                )
            ),
        )

    async def configure(self, values: dict[str, Any]) -> SheetConnectionModel:
        owner_id = self._required_owner()
        connection = await self.get_connection()
        normalized = {
            **values,
            "spreadsheet_id": extract_spreadsheet_id(str(values["spreadsheet_id"])),
            "worksheet_name": str(values["worksheet_name"]).strip(),
            "isin_column": normalize_column(str(values["isin_column"])),
            "price_column": normalize_column(str(values["price_column"])),
            "updated_at_column": normalize_column(values.get("updated_at_column"), required=False),
            "status_column": normalize_column(values.get("status_column"), required=False),
        }
        if not normalized["worksheet_name"]:
            raise ValueError("Не указано название вкладки Google Таблицы")
        columns = [
            normalized["isin_column"],
            normalized["price_column"],
            normalized["updated_at_column"],
            normalized["status_column"],
        ]
        used_columns = [column for column in columns if column is not None]
        if len(used_columns) != len(set(used_columns)):
            raise ValueError("Колонки ISIN, цены, времени и статуса не должны совпадать")
        reserved_columns = set(SHEET_INPUT_COLUMNS) | set(CALCULATION_HEADERS)
        collisions = sorted(set(used_columns) & reserved_columns)
        if collisions:
            raise ValueError(
                "Колонки подключения пересекаются с исходными или расчётными колонками: "
                + ", ".join(collisions)
            )
        if connection is None:
            connection = SheetConnectionModel(owner_id=owner_id, provider="google_sheets")
            self._session.add(connection)
        for field, value in normalized.items():
            setattr(connection, field, value)
        await self._session.commit()
        await self._session.refresh(connection)
        return connection

    async def check_connection(
        self, gateway: GoogleSheetsGateway
    ) -> tuple[SheetConnectionModel, str]:
        connection = await self._required_connection()
        check = await gateway.check_connection(connection.spreadsheet_id)
        if connection.worksheet_name not in check.worksheet_names:
            raise ValueError(f"Вкладка «{connection.worksheet_name}» не найдена в Google Таблице")
        return connection, check.spreadsheet_title

    async def enqueue(self, trigger: SheetSyncTrigger) -> SheetSyncJobModel:
        connection = await self._required_connection()
        if not connection.enabled and trigger != SheetSyncTrigger.MANUAL:
            raise ValueError("Автоматическая синхронизация Google Таблицы выключена")
        existing = await self._session.scalar(
            select(SheetSyncJobModel)
            .where(
                SheetSyncJobModel.connection_id == connection.id,
                SheetSyncJobModel.status == SheetSyncJobStatus.QUEUED,
            )
            .order_by(SheetSyncJobModel.created_at)
        )
        if existing is not None:
            return existing
        job = SheetSyncJobModel(
            connection_id=connection.id,
            trigger=trigger,
            status=SheetSyncJobStatus.QUEUED,
            next_attempt_at=datetime.now(UTC),
        )
        self._session.add(job)
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def list_jobs(self, limit: int = 20) -> list[SheetSyncJobModel]:
        connection = await self.get_connection()
        if connection is None:
            return []
        result = await self._session.scalars(
            select(SheetSyncJobModel)
            .where(SheetSyncJobModel.connection_id == connection.id)
            .order_by(SheetSyncJobModel.created_at.desc())
            .limit(limit)
        )
        return list(result)

    async def get_job(self, job_id: UUID) -> SheetSyncJobModel | None:
        connection = await self.get_connection()
        if connection is None:
            return None
        return cast(
            SheetSyncJobModel | None,
            await self._session.scalar(
                select(SheetSyncJobModel).where(
                    SheetSyncJobModel.id == job_id,
                    SheetSyncJobModel.connection_id == connection.id,
                )
            ),
        )

    async def execute(
        self,
        job: SheetSyncJobModel,
        gateway: GoogleSheetsGateway,
        moex: SheetMoexClient,
        timezone: ZoneInfo,
    ) -> SheetSyncJobModel:
        connection = await self._session.get(SheetConnectionModel, job.connection_id)
        if connection is None:
            raise ValueError("Подключение Google Таблицы удалено")
        connection.last_attempt_at = datetime.now(UTC)
        check = await gateway.check_connection(connection.spreadsheet_id)
        if connection.worksheet_name not in check.worksheet_names:
            raise ValueError(f"Вкладка «{connection.worksheet_name}» не найдена")
        rows = await gateway.read_rows(
            connection.spreadsheet_id,
            connection.worksheet_name,
            tuple(dict.fromkeys((connection.isin_column, *SHEET_INPUT_COLUMNS))),
            connection.header_row + 1,
        )
        rows = [row for row in rows if row.values.get(connection.isin_column) not in (None, "")]
        job.rows_read = len(rows)
        normalized_rows: list[tuple[SheetDataRow, str]] = []
        row_errors: list[dict[str, object]] = []
        for row in rows:
            raw_isin = str(row.values.get(connection.isin_column) or "")
            try:
                normalized_rows.append((row, normalize_isin(raw_isin)))
            except ValueError as exc:
                row_errors.append(
                    {"row_number": row.row_number, "isin": raw_isin, "message": str(exc)}
                )

        refreshed: dict[str, _RefreshedInstrument | None] = {}
        instrument_service = InstrumentService(self._session)
        for isin in dict.fromkeys(isin for _, isin in normalized_rows):
            try:
                instrument, market = await instrument_service.refresh(isin, moex)
                price = _market_price(market, connection.price_mode)
                status = (
                    "FRESH" if price is not None else _missing_quote_status(connection.price_mode)
                )
                refreshed[isin] = _RefreshedInstrument(
                    instrument=instrument,
                    market=market,
                    cashflows=await _instrument_cashflows(self._session, instrument.id),
                    price=price,
                    market_time=market.market_timestamp or market.received_at,
                    status=status,
                )
            except Exception as exc:
                refreshed[isin] = None
                row_errors.append({"isin": isin, "message": str(exc)[:1000]})

        updates = [
            CellUpdate(connection.header_row, column, header)
            for column, header in CALCULATION_HEADERS.items()
        ]
        historical_accrued: dict[tuple[str, date], Decimal | None] = {}
        for row, isin in normalized_rows:
            refreshed_value = refreshed[isin]
            if refreshed_value is None:
                updates.extend(_failed_row_updates(row.row_number, connection))
                continue

            market = refreshed_value.market
            row_updates: dict[str, Decimal | str | None] = {
                connection.price_column: refreshed_value.price,
                "AD": _money(market.accrued_interest_rub_per_bond),
            }
            if connection.updated_at_column:
                row_updates[connection.updated_at_column] = _format_timestamp(
                    refreshed_value.market_time, timezone
                )
            if connection.status_column:
                row_updates[connection.status_column] = refreshed_value.status

            raw_commission = row.values.get(PURCHASE_COMMISSION_COLUMN)
            commission_total: Decimal | None
            if raw_commission in (None, ""):
                commission_total = DEFAULT_PURCHASE_COMMISSION
                row_updates[PURCHASE_COMMISSION_COLUMN] = DEFAULT_PURCHASE_COMMISSION
            else:
                commission_total = parse_decimal(raw_commission)
            if commission_total is None:
                row_updates.update(_blank_calculation_values())
                row_updates["AL"] = "НЕКОРРЕКТНАЯ КОМИССИЯ В L"
                updates.extend(_cell_updates(row.row_number, row_updates))
                continue

            purchase_date = parse_date(row.values.get(PURCHASE_DATE_COLUMN))
            raw_purchase_accrued = row.values.get(PURCHASE_ACCRUED_COLUMN)
            purchase_accrued = parse_decimal(raw_purchase_accrued)
            if raw_purchase_accrued in (None, "") and purchase_date is not None:
                cache_key = (isin, purchase_date)
                if cache_key not in historical_accrued:
                    try:
                        historical_accrued[cache_key] = await moex.accrued_interest_on(
                            refreshed_value.instrument.secid,
                            refreshed_value.instrument.primary_board_id,
                            purchase_date,
                        )
                    except Exception as exc:
                        historical_accrued[cache_key] = None
                        row_errors.append(
                            {
                                "row_number": row.row_number,
                                "isin": isin,
                                "message": f"Исторический НКД: {str(exc)[:900]}",
                            }
                        )
                purchase_accrued = historical_accrued[cache_key]
                if purchase_accrued is not None:
                    row_updates[PURCHASE_ACCRUED_COLUMN] = _money(purchase_accrued)

            purchase, calculation_status = parse_sheet_purchase(
                row.values,
                purchase_accrued=purchase_accrued,
                commission_total=commission_total,
            )
            row_updates.update(_blank_calculation_values())
            if purchase is None:
                row_updates["AL"] = calculation_status or "НЕДОСТАТОЧНО ДАННЫХ"
                updates.extend(_cell_updates(row.row_number, row_updates))
                continue

            valuation_date = _valuation_date(market, timezone)
            coupon_count, coupon_total = summarize_paid_coupons(
                purchase_date=purchase.purchase_date,
                valuation_date=valuation_date,
                quantity=purchase.quantity,
                cashflows=refreshed_value.cashflows,
            )
            row_updates["AE"] = Decimal(coupon_count)
            row_updates["AF"] = _money(coupon_total)
            calculation_price = _market_rub_price(market, connection.price_mode)
            if calculation_price is None:
                row_updates["AL"] = _missing_quote_calculation_status(connection.price_mode)
                updates.extend(_cell_updates(row.row_number, row_updates))
                continue
            if market.accrued_interest_rub_per_bond is None:
                row_updates["AL"] = "MOEX НЕ ВЕРНУЛ ТЕКУЩИЙ НКД"
                updates.extend(_cell_updates(row.row_number, row_updates))
                continue
            try:
                calculation = calculate_sheet_current(
                    purchase,
                    valuation_date=valuation_date,
                    offer_rub_per_bond=calculation_price,
                    current_accrued_rub_per_bond=market.accrued_interest_rub_per_bond,
                    cashflows=refreshed_value.cashflows,
                )
            except Exception as exc:
                row_updates["AL"] = f"ОШИБКА РАСЧЁТА: {str(exc)[:160]}"
                row_errors.append(
                    {"row_number": row.row_number, "isin": isin, "message": str(exc)[:1000]}
                )
            else:
                row_updates.update(
                    {
                        "AE": Decimal(calculation.paid_coupon_count),
                        "AF": _money(calculation.paid_coupons_total_rub),
                        "AG": _money(calculation.current_exit_total_rub),
                        "AH": _money(calculation.profit_before_tax_rub),
                        "AI": _money(calculation.profit_after_tax_rub),
                        "AJ": _percent(calculation.annual_yield_after_tax_percent),
                        "AK": _percent(calculation.plan_delta_pp),
                        "AL": "РАССЧИТАНО",
                    }
                )
            updates.extend(_cell_updates(row.row_number, row_updates))

        for error in row_errors:
            invalid_row_number = error.get("row_number")
            if isinstance(invalid_row_number, int) and connection.status_column:
                if any(row.row_number == invalid_row_number for row, _ in normalized_rows):
                    continue
                updates.extend(
                    [
                        CellUpdate(invalid_row_number, connection.status_column, "INVALID ISIN"),
                        CellUpdate(invalid_row_number, "AL", "НЕВЕРНЫЙ ISIN"),
                    ]
                )

        await gateway.update_cells(
            connection.spreadsheet_id,
            connection.worksheet_name,
            updates,
        )
        completed_at = datetime.now(UTC)
        job.status = SheetSyncJobStatus.SUCCEEDED
        job.completed_at = completed_at
        job.rows_updated = len(normalized_rows)
        job.instruments_refreshed = sum(value is not None for value in refreshed.values())
        job.row_errors = row_errors
        job.error_message = None
        connection.last_success_at = completed_at
        connection.last_error = None
        connection.last_payload_hash = _payload_hash(updates)
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def _required_connection(self) -> SheetConnectionModel:
        connection = await self.get_connection()
        if connection is None:
            raise ValueError("Google Таблица ещё не подключена")
        return connection

    def _required_owner(self) -> UUID:
        if self._owner_id is None:
            raise ValueError("Для операции требуется владелец Google Таблицы")
        return self._owner_id


async def enqueue_sheet_sync(
    session: AsyncSession,
    owner_id: UUID,
    trigger: SheetSyncTrigger,
) -> SheetSyncJobModel | None:
    connection = await session.scalar(
        select(SheetConnectionModel).where(
            SheetConnectionModel.owner_id == owner_id,
            SheetConnectionModel.provider == "google_sheets",
            SheetConnectionModel.enabled.is_(True),
        )
    )
    if connection is None:
        return None
    existing = await session.scalar(
        select(SheetSyncJobModel).where(
            SheetSyncJobModel.connection_id == connection.id,
            SheetSyncJobModel.status == SheetSyncJobStatus.QUEUED,
        )
    )
    if existing is not None:
        return existing
    job = SheetSyncJobModel(
        connection_id=connection.id,
        trigger=trigger,
        status=SheetSyncJobStatus.QUEUED,
        next_attempt_at=datetime.now(UTC),
    )
    session.add(job)
    return job


async def enqueue_due_sheet_syncs(session: AsyncSession) -> int:
    now = datetime.now(UTC)
    connections = list(
        await session.scalars(
            select(SheetConnectionModel).where(SheetConnectionModel.enabled.is_(True))
        )
    )
    created = 0
    for connection in connections:
        reference = connection.last_attempt_at or connection.created_at
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=UTC)
        if reference + timedelta(seconds=connection.sync_interval_seconds) > now:
            continue
        existing = await session.scalar(
            select(SheetSyncJobModel).where(
                SheetSyncJobModel.connection_id == connection.id,
                SheetSyncJobModel.status.in_(
                    [SheetSyncJobStatus.QUEUED, SheetSyncJobStatus.RUNNING]
                ),
            )
        )
        if existing is not None:
            continue
        session.add(
            SheetSyncJobModel(
                connection_id=connection.id,
                trigger=SheetSyncTrigger.SCHEDULED,
                status=SheetSyncJobStatus.QUEUED,
                next_attempt_at=now,
            )
        )
        created += 1
    if created:
        await session.commit()
    return created


async def claim_next_sheet_sync_job(session: AsyncSession) -> SheetSyncJobModel | None:
    now = datetime.now(UTC)
    job = await session.scalar(
        select(SheetSyncJobModel)
        .where(
            SheetSyncJobModel.status == SheetSyncJobStatus.QUEUED,
            or_(
                SheetSyncJobModel.next_attempt_at.is_(None),
                SheetSyncJobModel.next_attempt_at <= now,
            ),
        )
        .order_by(SheetSyncJobModel.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job.status = SheetSyncJobStatus.RUNNING
    job.started_at = now
    job.attempt_count += 1
    await session.commit()
    await session.refresh(job)
    return job


async def record_sheet_sync_failure(
    session: AsyncSession,
    job: SheetSyncJobModel,
    error: Exception,
    *,
    max_attempts: int = 3,
) -> None:
    message = str(error)[:2000]
    connection = await session.get(SheetConnectionModel, job.connection_id)
    if connection is not None:
        connection.last_attempt_at = datetime.now(UTC)
        connection.last_error = message
    if job.attempt_count < max_attempts:
        job.status = SheetSyncJobStatus.QUEUED
        job.next_attempt_at = datetime.now(UTC) + timedelta(seconds=2**job.attempt_count)
    else:
        job.status = SheetSyncJobStatus.FAILED
        job.completed_at = datetime.now(UTC)
    job.error_message = message
    await session.commit()


async def _instrument_cashflows(
    session: AsyncSession,
    instrument_id: UUID,
) -> tuple[CorporateCashFlow, ...]:
    actions = list(
        await session.scalars(
            select(CorporateActionModel)
            .where(CorporateActionModel.instrument_id == instrument_id)
            .order_by(CorporateActionModel.source_updated_at.desc())
        )
    )
    seen: set[tuple[object, date]] = set()
    cashflows: list[CorporateCashFlow] = []
    for action in actions:
        identity = (action.action_type, action.event_date)
        if identity in seen or action.amount_rub_per_bond is None:
            continue
        seen.add(identity)
        cashflows.append(
            CorporateCashFlow(
                action_type=action.action_type,
                event_date=action.event_date,
                amount_rub_per_bond=action.amount_rub_per_bond,
            )
        )
    return tuple(sorted(cashflows, key=lambda value: (value.event_date, value.action_type)))


def _market_price(market: MarketSnapshotModel, mode: SheetPriceMode) -> Decimal | None:
    accrued = market.accrued_interest_rub_per_bond
    if mode == SheetPriceMode.BEST_BID_PERCENT:
        return market.bid_percent
    if mode == SheetPriceMode.BEST_BID_FULL_RUB:
        return (
            market.bid_rub_per_bond + accrued
            if market.bid_rub_per_bond is not None and accrued is not None
            else None
        )
    if mode == SheetPriceMode.BEST_BID_CLEAN_RUB:
        return market.bid_rub_per_bond
    if mode == SheetPriceMode.BEST_OFFER_PERCENT:
        return market.offer_percent
    if mode == SheetPriceMode.BEST_OFFER_FULL_RUB:
        return (
            market.offer_rub_per_bond + accrued
            if market.offer_rub_per_bond is not None and accrued is not None
            else None
        )
    return market.offer_rub_per_bond


def _market_rub_price(market: MarketSnapshotModel, mode: SheetPriceMode) -> Decimal | None:
    return (
        market.offer_rub_per_bond
        if mode
        in {
            SheetPriceMode.BEST_OFFER_PERCENT,
            SheetPriceMode.BEST_OFFER_CLEAN_RUB,
            SheetPriceMode.BEST_OFFER_FULL_RUB,
        }
        else market.bid_rub_per_bond
    )


def _missing_quote_status(mode: SheetPriceMode) -> str:
    return "NO OFFER" if mode.value.startswith("best_offer") else "NO BID"


def _missing_quote_calculation_status(mode: SheetPriceMode) -> str:
    return (
        "НЕТ ЛУЧШЕГО ПРЕДЛОЖЕНИЯ"
        if mode.value.startswith("best_offer")
        else "НЕТ ЛУЧШЕЙ ЗАЯВКИ НА ПОКУПКУ"
    )


def _valuation_date(market: MarketSnapshotModel, timezone: ZoneInfo) -> date:
    value = market.market_timestamp or market.received_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(timezone).date()


def _blank_calculation_values() -> dict[str, None]:
    return dict.fromkeys(("AE", "AF", "AG", "AH", "AI", "AJ", "AK"))


def _failed_row_updates(
    row_number: int,
    connection: SheetConnectionModel,
) -> list[CellUpdate]:
    values: dict[str, Decimal | str | None] = {
        connection.price_column: None,
        "AD": None,
        **_blank_calculation_values(),
        "AL": "ОШИБКА ОБНОВЛЕНИЯ MOEX",
    }
    if connection.updated_at_column:
        values[connection.updated_at_column] = ""
    if connection.status_column:
        values[connection.status_column] = "ERROR"
    return _cell_updates(row_number, values)


def _cell_updates(
    row_number: int,
    values: dict[str, Decimal | str | None],
) -> list[CellUpdate]:
    return [CellUpdate(row_number, column, value) for column, value in values.items()]


def _money(value: Decimal | None) -> Decimal | None:
    return value.quantize(Decimal("0.01")) if value is not None else None


def _percent(value: Decimal | None) -> Decimal | None:
    return value.quantize(Decimal("0.01")) if value is not None else None


def _format_timestamp(value: datetime | None, timezone: ZoneInfo) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(timezone).isoformat(timespec="seconds")


def _payload_hash(updates: list[CellUpdate]) -> str:
    payload = [
        {"row": update.row_number, "column": update.column, "value": str(update.value)}
        for update in updates
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
