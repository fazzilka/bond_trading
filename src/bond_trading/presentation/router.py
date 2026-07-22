from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.api.dependencies import get_import_cache
from bond_trading.application.services import LotService, SettingsService
from bond_trading.application.services.imports import ImportPreviewCache, ImportService
from bond_trading.core.config import get_settings
from bond_trading.domain.calculations import calculate_purchase, evaluate_liquidity
from bond_trading.domain.calculations.models import PurchaseInput, TaxMode
from bond_trading.infrastructure.db.models import (
    BondInstrumentModel,
    MarketSnapshotModel,
    YieldSnapshotModel,
)
from bond_trading.infrastructure.db.session import get_session
from bond_trading.infrastructure.imports import (
    XlsxImportError,
    XlsxPortfolioReader,
    validate_xlsx_upload,
)

PRESENTATION_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=PRESENTATION_DIR / "templates")
router = APIRouter(include_in_schema=False)
Session = Annotated[AsyncSession, Depends(get_session)]
Cache = Annotated[ImportPreviewCache, Depends(get_import_cache)]


@router.get("/portfolio", response_class=HTMLResponse)
async def portfolio(
    request: Request,
    session: Session,
    search: Annotated[str | None, Query()] = None,
    event_type: Annotated[str | None, Query()] = None,
    comparison: Annotated[str | None, Query()] = None,
    data_status: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query()] = "delta",
) -> HTMLResponse:
    rows = await _portfolio_rows(session)
    if search:
        needle = search.strip().lower()
        rows = [
            row
            for row in rows
            if needle in row["lot"].instrument.isin.lower()
            or needle in row["lot"].instrument.short_name.lower()
        ]
    if event_type in {"maturity", "offer"}:
        rows = [row for row in rows if row["lot"].target_event_type == event_type]
    if comparison == "above":
        rows = [row for row in rows if (row["delta"] or Decimal(0)) > 0]
    elif comparison == "below":
        rows = [row for row in rows if row["delta"] is not None and row["delta"] < 0]
    if data_status == "stale":
        rows = [row for row in rows if row["freshness"] == "stale"]
    elif data_status == "no_bid":
        rows = [row for row in rows if row["market"] is None or row["market"].bid_percent is None]
    sort_keys = {
        "delta": lambda row: row["delta"] or Decimal("-999999"),
        "current_yield": lambda row: row["current_yield"] or Decimal("-999999"),
        "purchase_date": lambda row: row["lot"].purchase_date,
        "target_date": lambda row: row["lot"].target_event_date,
        "updated_at": lambda row: (
            row["market"].received_at if row["market"] else datetime.min.replace(tzinfo=UTC)
        ),
    }
    rows.sort(key=sort_keys.get(sort, sort_keys["delta"]), reverse=True)
    return templates.TemplateResponse(
        request=request,
        name="portfolio.html",
        context={"rows": rows, "filters": request.query_params},
    )


@router.get("/portfolio/{lot_id}", response_class=HTMLResponse)
async def lot_detail(request: Request, lot_id: UUID, session: Session) -> HTMLResponse:
    service = LotService(session, get_settings().business_timezone)
    lot = await service.get(lot_id)
    if lot is None:
        raise HTTPException(404, "Bond lot not found")
    history = await service.history(lot_id)
    purchase = calculate_purchase(
        PurchaseInput(
            purchase_date=lot.purchase_date,
            quantity=lot.quantity,
            clean_price_rub_per_bond=lot.purchase_clean_price_rub_per_bond,
            accrued_interest_rub_per_bond=lot.purchase_accrued_interest_rub_per_bond,
            commission_rub_per_bond=lot.purchase_commission_rub_per_bond,
        )
    )
    return templates.TemplateResponse(
        request=request,
        name="lot_detail.html",
        context={"lot": lot, "purchase": purchase, "history": history},
    )


@router.get("/import", response_class=HTMLResponse)
async def import_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="import.html", context={})


@router.post("/import/preview", response_class=HTMLResponse)
async def import_preview(
    request: Request,
    cache: Cache,
    file: Annotated[UploadFile, File()],
) -> HTMLResponse:
    error: str | None = None
    preview = None
    preview_id = None
    try:
        validate_xlsx_upload(file.filename or "", file.content_type)
        content = await file.read(get_settings().imports.max_upload_bytes + 1)
        if len(content) > get_settings().imports.max_upload_bytes:
            error = "Файл превышает допустимый размер."
        else:
            preview = XlsxPortfolioReader().preview(content, file.filename or "upload.xlsx")
            preview_id = cache.put(preview)
    except XlsxImportError as exc:
        error = str(exc)
    return templates.TemplateResponse(
        request=request,
        name="import_preview.html",
        context={"preview": preview, "preview_id": preview_id, "error": error},
    )


@router.post("/import/commit")
async def import_commit(
    preview_id: Annotated[UUID, Form()], session: Session, cache: Cache
) -> RedirectResponse:
    preview = cache.get(preview_id)
    if preview is None:
        raise HTTPException(404, "Import preview expired or was not found")
    await ImportService(session).commit(preview)
    cache.discard(preview_id)
    return RedirectResponse("/portfolio", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, session: Session) -> HTMLResponse:
    value = await SettingsService(session).get()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={"settings": value, "tax_modes": list(TaxMode)},
    )


@router.post("/settings")
async def settings_update(
    session: Session,
    market_data_ttl_seconds: Annotated[int, Form(ge=60)],
    tax_mode: Annotated[TaxMode, Form()],
    tax_rate: Annotated[Decimal, Form(ge=0, le=1)],
    default_sale_commission_rub_per_bond: Annotated[Decimal, Form(ge=0)],
) -> RedirectResponse:
    value = await SettingsService(session).get()
    value.market_data_ttl_seconds = market_data_ttl_seconds
    value.tax_mode = tax_mode
    value.tax_rate = tax_rate
    value.default_sale_commission_rub_per_bond = default_sale_commission_rub_per_bond
    await session.commit()
    return RedirectResponse("/settings", status_code=303)


@router.get("/data-status", response_class=HTMLResponse)
async def data_status(request: Request, session: Session) -> HTMLResponse:
    settings = await SettingsService(session).get()
    instruments = list(
        await session.scalars(select(BondInstrumentModel).order_by(BondInstrumentModel.isin))
    )
    rows: list[dict[str, Any]] = []
    for instrument in instruments:
        market = await _latest_market(session, instrument.id)
        rows.append(
            {
                "instrument": instrument,
                "market": market,
                "freshness": _freshness(market, settings.market_data_ttl_seconds),
            }
        )
    return templates.TemplateResponse(
        request=request,
        name="data_status.html",
        context={"rows": rows},
    )


async def _portfolio_rows(session: AsyncSession) -> list[dict[str, Any]]:
    lots = await LotService(session, get_settings().business_timezone).list_all()
    settings = await SettingsService(session).get()
    rows: list[dict[str, Any]] = []
    for lot in lots:
        market = await _latest_market(session, lot.instrument_id)
        latest_yield = await session.scalar(
            select(YieldSnapshotModel)
            .where(YieldSnapshotModel.lot_id == lot.id)
            .order_by(YieldSnapshotModel.created_at.desc())
            .limit(1)
        )
        purchase = calculate_purchase(
            PurchaseInput(
                purchase_date=lot.purchase_date,
                quantity=lot.quantity,
                clean_price_rub_per_bond=lot.purchase_clean_price_rub_per_bond,
                accrued_interest_rub_per_bond=lot.purchase_accrued_interest_rub_per_bond,
                commission_rub_per_bond=lot.purchase_commission_rub_per_bond,
            )
        )
        liquidity = evaluate_liquidity(
            quantity=lot.quantity,
            bid_present=market is not None and market.bid_rub_per_bond is not None,
            bid_depth_lots=market.bid_depth_lots if market else None,
            lot_size=market.lot_size if market else Decimal(1),
        )
        rows.append(
            {
                "lot": lot,
                "purchase_total": purchase.purchase_total,
                "market": market,
                "yield": latest_yield,
                "planned_yield": (
                    latest_yield.planned_annual_yield_after_tax if latest_yield else None
                ),
                "current_yield": (
                    latest_yield.current_annual_yield_after_tax if latest_yield else None
                ),
                "delta": latest_yield.yield_delta_pp if latest_yield else None,
                "received_coupons": _calculation_detail_decimal(
                    latest_yield, "current", "coupons_total"
                ),
                "liquidity": liquidity,
                "freshness": _freshness(market, settings.market_data_ttl_seconds),
            }
        )
    return rows


async def _latest_market(session: AsyncSession, instrument_id: UUID) -> MarketSnapshotModel | None:
    return cast(
        MarketSnapshotModel | None,
        await session.scalar(
            select(MarketSnapshotModel)
            .where(MarketSnapshotModel.instrument_id == instrument_id)
            .order_by(MarketSnapshotModel.received_at.desc())
            .limit(1)
        ),
    )


def _freshness(market: MarketSnapshotModel | None, ttl_seconds: int) -> str:
    if market is None:
        return "missing"
    received_at = market.received_at
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=UTC)
    return "fresh" if received_at >= datetime.now(UTC) - timedelta(seconds=ttl_seconds) else "stale"


def _calculation_detail_decimal(
    snapshot: YieldSnapshotModel | None, section: str, field: str
) -> Decimal | None:
    if snapshot is None:
        return None
    section_data = snapshot.calculation_details.get(section)
    if not isinstance(section_data, dict):
        return None
    value = section_data.get(field)
    return Decimal(str(value)) if value is not None else None
