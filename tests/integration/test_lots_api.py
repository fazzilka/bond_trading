from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bond_trading.domain.calculations import ActionType
from bond_trading.infrastructure.db.models import CorporateActionModel, MarketSnapshotModel


def lot_payload(quantity: str = "40") -> dict[str, object]:
    return {
        "isin": "RU000A107SX3",
        "source_name": "ЭкономЛизинг 1р-07",
        "purchase_date": "2026-05-25",
        "quantity": quantity,
        "purchase_clean_price_rub_per_bond": "962.90",
        "purchase_accrued_interest_rub_per_bond": "3.51",
        "purchase_commission_rub_per_bond": "0.39",
        "target_event_type": "maturity",
        "target_event_date": "2027-02-15",
        "target_redemption_price_rub_per_bond": "1000",
        "target_redemption_override_reason": "Control scenario redemption value",
    }


async def test_lot_crud_and_calculation(
    app_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], object],
) -> None:
    client, session_factory, _ = app_client
    created = await client.post("/api/v1/lots", json=lot_payload())
    assert created.status_code == 201, created.text
    lot = created.json()
    assert lot["target_redemption_override_reason"] == "Control scenario redemption value"
    assert lot["target_redemption_override_updated_at"] is not None

    duplicate = await client.post("/api/v1/lots", json=lot_payload("2"))
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] != lot["id"]

    settings = await client.patch("/api/v1/settings", json={"tax_mode": "legacy_divide_1_13"})
    assert settings.status_code == 200

    async with session_factory() as session:
        instrument_id = UUID(lot["instrument_id"])
        session.add(
            CorporateActionModel(
                instrument_id=instrument_id,
                action_type=ActionType.COUPON,
                event_date=date(2026, 8, 17),
                amount_rub_per_bond=Decimal("39.89"),
                source="test",
                source_payload_hash="coupon-1",
                source_updated_at=datetime.now(UTC),
            )
        )
        session.add(
            MarketSnapshotModel(
                instrument_id=instrument_id,
                board_id="TQCB",
                received_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
                market_timestamp=datetime(2026, 9, 1, 12, tzinfo=UTC),
                bid_percent=Decimal("98.2"),
                bid_rub_per_bond=Decimal("982"),
                bid_depth_lots=Decimal("50"),
                lot_size=Decimal("1"),
                current_face_value=Decimal("1000"),
                accrued_interest_rub_per_bond=Decimal("3.2"),
                status="ok",
                raw_payload={},
            )
        )
        await session.commit()

    calculated = await client.post(
        f"/api/v1/lots/{lot['id']}/calculate", json={"valuation_date": "2026-09-01"}
    )
    assert calculated.status_code == 200, calculated.text
    snapshot = calculated.json()["snapshot"]
    assert Decimal(snapshot["purchase_total"]) == Decimal("38672")
    assert Decimal(snapshot["current_exit_total"]) == Decimal("41003.6")
    assert Decimal(snapshot["current_profit_before_tax"]) == Decimal("2331.6")
    assert Decimal(snapshot["current_annual_yield_after_tax"]) == pytest.approx(
        Decimal("19.6715"), abs=Decimal("0.0001")
    )

    history = await client.get(f"/api/v1/lots/{lot['id']}/yield-history")
    assert history.status_code == 200
    assert len(history.json()) == 1

    listed = await client.get("/api/v1/lots")
    assert len(listed.json()) == 2

    deleted = await client.delete(f"/api/v1/lots/{lot['id']}")
    assert deleted.status_code == 204


async def test_manual_override_requires_reason(
    app_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], object],
) -> None:
    client, _, _ = app_client
    payload = lot_payload()
    payload.pop("target_redemption_override_reason")

    rejected = await client.post("/api/v1/lots", json=payload)
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "validation_error"
