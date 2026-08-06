from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bond_trading.domain.calculations import ActionType
from bond_trading.infrastructure.moex.errors import MoexTemporaryError
from bond_trading.infrastructure.moex.schemas import (
    MoexCorporateActionData,
    MoexInstrumentData,
    MoexMarketData,
    MoexRefreshResult,
)


class FakeMoexClient:
    async def refresh(self, isin: str, *, force: bool = True) -> MoexRefreshResult:
        now = datetime(2026, 7, 22, 12, tzinfo=UTC)
        return MoexRefreshResult(
            instrument=MoexInstrumentData(
                isin=isin.upper(),
                secid=isin.upper(),
                short_name="ЭконЛиз1Р7",
                full_name="ЭкономЛизинг 001Р-07",
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
                    event_date=date(2026, 8, 17),
                    record_date=date(2026, 8, 14),
                    amount_rub_per_bond=Decimal("39.89"),
                    percent=Decimal("16"),
                    source_payload_hash="coupon-hash",
                ),
            ),
            market=MoexMarketData(
                board_id="TQCB",
                received_at=now,
                market_timestamp=now,
                bid_percent=Decimal("97.23"),
                bid_rub_per_bond=Decimal("972.30"),
                bid_depth_lots=Decimal("12"),
                offer_percent=Decimal("97.43"),
                offer_rub_per_bond=Decimal("974.30"),
                offer_depth_lots=Decimal("8"),
                lot_size=Decimal("1"),
                current_face_value=Decimal("1000"),
                accrued_interest_rub_per_bond=Decimal("28.93"),
                last_price_percent=Decimal("97.16"),
                status="ok",
                delayed_status="unknown",
                raw_payload={"marketdata": {"columns": ["BID"]}},
            ),
        )


class FailingMoexClient:
    async def refresh(self, isin: str, *, force: bool = True) -> MoexRefreshResult:
        raise MoexTemporaryError("MOEX is temporarily unavailable")


async def test_refresh_and_read_instrument(
    app_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], FastAPI],
) -> None:
    client, _, app = app_client
    app.state.moex_client = FakeMoexClient()

    refreshed = await client.post("/api/v1/instruments/RU000A107SX3/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert Decimal(refreshed.json()["market"]["bid_rub_per_bond"]) == Decimal("972.30")

    repeated = await client.post("/api/v1/instruments/RU000A107SX3/refresh")
    assert repeated.status_code == 200

    instrument = await client.get("/api/v1/instruments/RU000A107SX3")
    assert instrument.status_code == 200
    assert instrument.json()["short_name"] == "ЭконЛиз1Р7"

    app.state.moex_client = FailingMoexClient()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as error_client:
        failed = await error_client.post("/api/v1/instruments/RU000A107SX3/refresh")
        assert failed.status_code == 500

        status_page = await error_client.get("/data-status")
        assert "MOEX is temporarily unavailable" in status_page.text
        assert "refresh_error" in status_page.text
