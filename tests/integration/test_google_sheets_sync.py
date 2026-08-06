from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bond_trading.application.services.sheet_calculations import CALCULATION_HEADERS
from bond_trading.application.services.sheets import (
    SheetSyncService,
    claim_next_sheet_sync_job,
    enqueue_due_sheet_syncs,
    record_sheet_sync_failure,
)
from bond_trading.core.config import AppSettings, GoogleSheetsSettings
from bond_trading.domain.calculations import ActionType
from bond_trading.infrastructure.db.models import (
    SheetConnectionModel,
    SheetSyncJobModel,
    SheetSyncJobStatus,
)
from bond_trading.infrastructure.google_sheets import MemoryGoogleSheetsGateway
from bond_trading.infrastructure.moex.schemas import (
    MoexCorporateActionData,
    MoexInstrumentData,
    MoexMarketData,
    MoexRefreshResult,
)
from bond_trading.workers.sheet_sync import process_one_job

OWNER_ID = UUID("11111111-1111-1111-1111-111111111111")
SPREADSHEET_ID = "spreadsheet-test-004"
WORKSHEET = "Доход счёт 2026"


class FakeMoexClient:
    async def accrued_interest_on(
        self,
        secid: str,
        board_id: str | None,
        value_date: date,
    ) -> Decimal | None:
        del secid, board_id, value_date
        return Decimal("3.07")

    async def refresh(self, isin: str, *, force: bool = True) -> MoexRefreshResult:
        del force
        now = datetime(2026, 8, 1, 9, 30, tzinfo=UTC)
        prices = {
            "RU000A107SX3": (Decimal("94.83"), Decimal("948.30")),
            "RU000A106CJ8": (Decimal("98.21"), Decimal("982.10")),
        }
        bid_percent, bid_rub = prices[isin]
        return MoexRefreshResult(
            instrument=MoexInstrumentData(
                isin=isin,
                secid=isin,
                short_name="Тестовая облигация",
                full_name="Тестовая облигация для синхронизации",
                primary_board_id="TQCB",
                currency="RUB",
                initial_face_value=Decimal("1000"),
                current_face_value=Decimal("1000"),
                maturity_date=date(2027, 2, 15),
                offer_date=None,
                coupon_period_days=91,
                coupon_value=Decimal("39.89"),
                is_amortizing=False,
                is_floating_coupon=False,
                is_active=True,
                source_updated_at=now,
            ),
            actions=(
                MoexCorporateActionData(
                    action_type=ActionType.COUPON,
                    event_date=date(2026, 7, 1),
                    record_date=date(2026, 6, 28),
                    amount_rub_per_bond=Decimal("39.89"),
                    percent=Decimal("16"),
                    source_payload_hash=f"paid-coupon-{isin}",
                ),
                MoexCorporateActionData(
                    action_type=ActionType.COUPON,
                    event_date=date(2026, 8, 17),
                    record_date=date(2026, 8, 14),
                    amount_rub_per_bond=Decimal("39.89"),
                    percent=Decimal("16"),
                    source_payload_hash=f"coupon-{isin}",
                ),
            ),
            market=MoexMarketData(
                board_id="TQCB",
                received_at=now,
                market_timestamp=now,
                bid_percent=bid_percent,
                bid_rub_per_bond=bid_rub,
                bid_depth_lots=Decimal("12"),
                offer_percent=bid_percent + Decimal("0.20"),
                offer_rub_per_bond=bid_rub + Decimal("2.00"),
                offer_depth_lots=Decimal("8"),
                lot_size=Decimal("1"),
                current_face_value=Decimal("1000"),
                accrued_interest_rub_per_bond=Decimal("28.93"),
                last_price_percent=bid_percent,
                status="ok",
                delayed_status="unknown",
                raw_payload={"test": True},
            ),
        )


class NoBidMoexClient(FakeMoexClient):
    async def refresh(self, isin: str, *, force: bool = True) -> MoexRefreshResult:
        result = await super().refresh(isin, force=force)
        return replace(
            result,
            market=replace(
                result.market,
                bid_percent=None,
                bid_rub_per_bond=None,
                bid_depth_lots=None,
            ),
        )


async def configure_connection(
    client: httpx.AsyncClient,
    *,
    price_mode: str = "best_bid_clean_rub",
    price_column: str = "X",
    updated_at_column: str = "Y",
    status_column: str = "Z",
) -> None:
    response = await client.put(
        "/api/v1/integrations/google-sheets",
        json={
            "spreadsheet_id": f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit",
            "worksheet_name": WORKSHEET,
            "header_row": 2,
            "isin_column": "B",
            "price_column": price_column,
            "updated_at_column": updated_at_column,
            "status_column": status_column,
            "price_mode": price_mode,
            "enabled": True,
            "sync_interval_seconds": 300,
        },
    )
    assert response.status_code == 200, response.text


async def test_in_place_sync_updates_price_and_preserves_customer_formulas(
    app_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], FastAPI],
) -> None:
    client, session_factory, app = app_client
    gateway = app.state.google_sheets_gateway
    assert isinstance(gateway, MemoryGoogleSheetsGateway)
    gateway.add_spreadsheet(SPREADSHEET_ID, title="Портфель заказчика", worksheets=(WORKSHEET,))
    for row, isin, formula in (
        (3, "RU000A107SX3", "=(X3-I3)*H3"),
        (4, "RU000A107SX3", "=(X4-I4)*H4"),
        (5, "RU000A106CJ8", "=(X5-I5)*H5"),
    ):
        gateway.set_cell(SPREADSHEET_ID, WORKSHEET, row, "B", isin)
        gateway.set_cell(SPREADSHEET_ID, WORKSHEET, row, "AA", formula)

    await configure_connection(client)
    checked = await client.post("/api/v1/integrations/google-sheets/test")
    assert checked.status_code == 200
    assert checked.json()["spreadsheet_title"] == "Портфель заказчика"
    queued = await client.post("/api/v1/integrations/google-sheets/sync")
    assert queued.status_code == 200

    settings = AppSettings(google_sheets=GoogleSheetsSettings(enabled=True))
    processed = await process_one_job(
        session_factory,
        gateway,
        FakeMoexClient(),  # type: ignore[arg-type]
        settings,
    )
    assert processed is True

    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "X")] == Decimal("948.30")
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 4, "X")] == Decimal("948.30")
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 5, "X")] == Decimal("982.10")
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "Z")] == "FRESH"
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "Y")] == "2026-08-01T12:30:00+03:00"
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "AA")] == "=(X3-I3)*H3"
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 4, "AA")] == "=(X4-I4)*H4"
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 5, "AA")] == "=(X5-I5)*H5"
    assert all(
        update.column in {"L", "X", "Y", "Z", *CALCULATION_HEADERS}
        for update in gateway.update_batches[0]
    )
    assert not any(update.column == "AA" for update in gateway.update_batches[0])

    async with session_factory() as session:
        job = await session.scalar(select(SheetSyncJobModel))
        assert job is not None
        assert job.status == SheetSyncJobStatus.SUCCEEDED
        assert job.rows_read == 3
        assert job.rows_updated == 3
    assert job.instruments_refreshed == 2


async def test_offer_sync_calculates_coupons_historical_aci_and_current_yield(
    app_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], FastAPI],
) -> None:
    client, session_factory, app = app_client
    gateway = app.state.google_sheets_gateway
    assert isinstance(gateway, MemoryGoogleSheetsGateway)
    gateway.add_spreadsheet(SPREADSHEET_ID, title="Портфель заказчика", worksheets=(WORKSHEET,))
    for column, value in {
        "B": "RU000A107SX3",
        "E": "25/05/26",
        "H": Decimal("2"),
        "I": Decimal("962.90"),
        "M": "=I3*H3+J3*H3+L3",
        "V": Decimal("19.3"),
    }.items():
        gateway.set_cell(SPREADSHEET_ID, WORKSHEET, 3, column, value)

    await configure_connection(
        client,
        price_mode="best_offer_clean_rub",
        price_column="AA",
        updated_at_column="AB",
        status_column="AC",
    )
    await client.post("/api/v1/integrations/google-sheets/sync")
    assert await process_one_job(
        session_factory,
        gateway,
        FakeMoexClient(),  # type: ignore[arg-type]
        AppSettings(google_sheets=GoogleSheetsSettings(enabled=True)),
    )

    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "J")] == Decimal("3.07")
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "L")] == Decimal("0.4")
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "M")] == "=I3*H3+J3*H3+L3"
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "AA")] == Decimal("950.30")
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "AC")] == "FRESH"
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "AD")] == Decimal("28.93")
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "AE")] == Decimal("1")
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "AF")] == Decimal("79.78")
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "AG")] == Decimal("2038.24")
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "AH")] == Decimal("105.90")
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "AI")] == Decimal("93.72")
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "AJ")] == Decimal("26.03")
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "AK")] == Decimal("6.73")
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "AL")] == "РАССЧИТАНО"
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 2, "AD")] == CALCULATION_HEADERS["AD"]


async def test_lot_change_enqueues_sync_and_user_cannot_read_foreign_job(
    app_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], FastAPI],
) -> None:
    client, session_factory, app = app_client
    gateway = app.state.google_sheets_gateway
    assert isinstance(gateway, MemoryGoogleSheetsGateway)
    gateway.add_spreadsheet(SPREADSHEET_ID, title="Портфель", worksheets=(WORKSHEET,))
    await configure_connection(client)

    created = await client.post(
        "/api/v1/lots",
        json={
            "isin": "RU000A107SX3",
            "purchase_date": "2026-05-25",
            "quantity": "40",
            "purchase_clean_price_rub_per_bond": "962.90",
            "purchase_accrued_interest_rub_per_bond": "3.51",
            "purchase_commission_rub_per_bond": "0.39",
            "target_event_type": "maturity",
            "target_event_date": "2027-02-15",
        },
    )
    assert created.status_code == 201
    jobs = await client.get("/api/v1/integrations/google-sheets/jobs")
    assert jobs.status_code == 200
    assert jobs.json()[0]["trigger"] == "lot_created"

    async with session_factory() as session:
        own_job = await session.scalar(select(SheetSyncJobModel))
        assert own_job is not None
        assert await SheetSyncService(session, UUID(int=999)).get_job(own_job.id) is None


async def test_google_sheets_page_is_in_russian(
    app_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], FastAPI],
) -> None:
    client, _, _ = app_client
    response = await client.get("/integrations/google-sheets")

    assert response.status_code == 200
    assert "Google Таблица заказчика" in response.text
    assert "Колонка текущей цены" in response.text
    assert "Остальные формулы не перезаписываются" in response.text
    assert 'value="best_offer_clean_rub" selected' in response.text
    assert "Лучшее предложение (OFFER)" in response.text


async def test_sync_clears_price_without_bid_and_marks_invalid_isin(
    app_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], FastAPI],
) -> None:
    client, session_factory, app = app_client
    gateway = app.state.google_sheets_gateway
    assert isinstance(gateway, MemoryGoogleSheetsGateway)
    gateway.add_spreadsheet(SPREADSHEET_ID, title="Портфель", worksheets=(WORKSHEET,))
    gateway.set_cell(SPREADSHEET_ID, WORKSHEET, 3, "B", "RU000A107SX3")
    gateway.set_cell(SPREADSHEET_ID, WORKSHEET, 3, "X", Decimal("999.99"))
    gateway.set_cell(SPREADSHEET_ID, WORKSHEET, 4, "B", "неверный-isin")
    gateway.set_cell(SPREADSHEET_ID, WORKSHEET, 4, "X", Decimal("777.77"))
    gateway.set_cell(SPREADSHEET_ID, WORKSHEET, 4, "AA", "=X4*2")

    await configure_connection(client)
    queued = await client.post("/api/v1/integrations/google-sheets/sync")
    assert queued.status_code == 200
    processed = await process_one_job(
        session_factory,
        gateway,
        NoBidMoexClient(),  # type: ignore[arg-type]
        AppSettings(google_sheets=GoogleSheetsSettings(enabled=True)),
    )
    assert processed is True

    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "X")] == ""
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 3, "Z")] == "NO BID"
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 4, "X")] == Decimal("777.77")
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 4, "Z")] == "INVALID ISIN"
    assert gateway.cells[(SPREADSHEET_ID, WORKSHEET, 4, "AA")] == "=X4*2"


async def test_configuration_rejects_overlapping_columns_and_settings_enqueue_once(
    app_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], FastAPI],
) -> None:
    client, _, app = app_client
    gateway = app.state.google_sheets_gateway
    assert isinstance(gateway, MemoryGoogleSheetsGateway)
    gateway.add_spreadsheet(SPREADSHEET_ID, title="Портфель", worksheets=(WORKSHEET,))
    await configure_connection(client)

    invalid = await client.put(
        "/api/v1/integrations/google-sheets",
        json={
            "spreadsheet_id": SPREADSHEET_ID,
            "worksheet_name": WORKSHEET,
            "header_row": 2,
            "isin_column": "B",
            "price_column": "B",
            "price_mode": "best_bid_clean_rub",
            "enabled": True,
            "sync_interval_seconds": 300,
        },
    )
    assert invalid.status_code == 422
    assert "не должны совпадать" in invalid.json()["message"]

    first = await client.patch("/api/v1/settings", json={"tax_rate": "0.12"})
    second = await client.patch("/api/v1/settings", json={"tax_rate": "0.13"})
    assert first.status_code == 200
    assert second.status_code == 200
    jobs = (await client.get("/api/v1/integrations/google-sheets/jobs")).json()
    assert len(jobs) == 1
    assert jobs[0]["trigger"] == "settings_changed"


async def test_failed_job_is_retried_then_marked_failed(
    app_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], FastAPI],
) -> None:
    client, session_factory, app = app_client
    gateway = app.state.google_sheets_gateway
    assert isinstance(gateway, MemoryGoogleSheetsGateway)
    gateway.add_spreadsheet(SPREADSHEET_ID, title="Портфель", worksheets=(WORKSHEET,))
    await configure_connection(client)
    await client.post("/api/v1/integrations/google-sheets/sync")

    async with session_factory() as session:
        job = await claim_next_sheet_sync_job(session)
        assert job is not None
        await record_sheet_sync_failure(session, job, RuntimeError("Google временно недоступен"))
        assert job.status == SheetSyncJobStatus.QUEUED
        assert job.attempt_count == 1
        assert job.error_message == "Google временно недоступен"

        job.attempt_count = 3
        await record_sheet_sync_failure(
            session,
            job,
            RuntimeError("Google всё ещё недоступен"),
        )
        assert job.status == SheetSyncJobStatus.FAILED
        assert job.completed_at is not None


async def test_scheduler_enqueues_only_one_due_job(
    app_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], FastAPI],
) -> None:
    client, session_factory, app = app_client
    gateway = app.state.google_sheets_gateway
    assert isinstance(gateway, MemoryGoogleSheetsGateway)
    gateway.add_spreadsheet(SPREADSHEET_ID, title="Портфель", worksheets=(WORKSHEET,))
    await configure_connection(client)

    async with session_factory() as session:
        connection = await session.scalar(select(SheetConnectionModel))
        assert connection is not None
        connection.created_at = datetime.now(UTC) - timedelta(seconds=301)
        await session.commit()
        assert await enqueue_due_sheet_syncs(session) == 1
        assert await enqueue_due_sheet_syncs(session) == 0

    jobs = (await client.get("/api/v1/integrations/google-sheets/jobs")).json()
    assert len(jobs) == 1
    assert jobs[0]["trigger"] == "scheduled"


async def test_event_during_running_job_enqueues_one_follow_up(
    app_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], FastAPI],
) -> None:
    client, session_factory, app = app_client
    gateway = app.state.google_sheets_gateway
    assert isinstance(gateway, MemoryGoogleSheetsGateway)
    gateway.add_spreadsheet(SPREADSHEET_ID, title="Портфель", worksheets=(WORKSHEET,))
    await configure_connection(client)
    await client.post("/api/v1/integrations/google-sheets/sync")

    async with session_factory() as session:
        running = await claim_next_sheet_sync_job(session)
        assert running is not None
        assert running.status == SheetSyncJobStatus.RUNNING

    await client.patch("/api/v1/settings", json={"tax_rate": "0.12"})
    await client.patch("/api/v1/settings", json={"tax_rate": "0.13"})
    jobs = (await client.get("/api/v1/integrations/google-sheets/jobs")).json()
    assert len(jobs) == 2
    assert [job["status"] for job in jobs].count("queued") == 1
    assert [job["status"] for job in jobs].count("running") == 1
