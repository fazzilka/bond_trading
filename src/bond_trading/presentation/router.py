from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.api.auth_dependencies import CurrentWebAuth
from bond_trading.api.dependencies import get_import_cache, get_object_storage
from bond_trading.application.services import AuthService, LotService, SettingsService
from bond_trading.application.services.auth import AuthenticationError
from bond_trading.application.services.imports import ImportPreviewCache, ImportService
from bond_trading.application.services.uploads import UploadService
from bond_trading.core.config import get_settings
from bond_trading.domain.calculations import calculate_purchase, evaluate_liquidity
from bond_trading.domain.calculations.models import PurchaseInput, TaxMode
from bond_trading.infrastructure.db.models import (
    BondInstrumentModel,
    MarketSnapshotModel,
    UploadedFileModel,
    UserRole,
    YieldSnapshotModel,
)
from bond_trading.infrastructure.db.session import get_session
from bond_trading.infrastructure.imports import (
    SpreadsheetImportError,
    SpreadsheetPortfolioReader,
    validate_spreadsheet_upload,
)
from bond_trading.infrastructure.storage import ObjectStorage

PRESENTATION_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=PRESENTATION_DIR / "templates")
router = APIRouter(include_in_schema=False)
Session = Annotated[AsyncSession, Depends(get_session)]
Cache = Annotated[ImportPreviewCache, Depends(get_import_cache)]
Storage = Annotated[ObjectStorage, Depends(get_object_storage)]


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next_path: Annotated[str, Query(alias="next")] = "/portfolio",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"next_path": _safe_next(next_path), "error": None},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    session: Session,
    login: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next_path: Annotated[str, Form()] = "/portfolio",
) -> Response:
    try:
        credentials = await AuthService(session, get_settings().auth).authenticate(
            login, password, request.headers.get("user-agent")
        )
    except AuthenticationError as exc:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "next_path": _safe_next(next_path),
                "error": exc.message,
            },
            status_code=401,
        )
    response = RedirectResponse(_safe_next(next_path), status_code=303)
    _set_auth_cookies(response, credentials.token, credentials.csrf_token)
    return response


@router.post("/logout")
async def logout_submit(
    auth: CurrentWebAuth,
    session: Session,
    csrf_token: Annotated[str, Form()],
) -> RedirectResponse:
    service = AuthService(session, get_settings().auth)
    service.verify_csrf(auth.session, csrf_token)
    await service.revoke(auth.session)
    response = RedirectResponse("/login", status_code=303)
    _clear_auth_cookies(response)
    return response


@router.get("/portfolio", response_class=HTMLResponse)
async def portfolio(
    request: Request,
    session: Session,
    auth: CurrentWebAuth,
    search: Annotated[str | None, Query()] = None,
    event_type: Annotated[str | None, Query()] = None,
    comparison: Annotated[str | None, Query()] = None,
    data_status: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query()] = "delta",
) -> HTMLResponse:
    rows = await _portfolio_rows(session, auth.user.id)
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
        context=_context(
            request,
            auth,
            rows=rows,
            filters=request.query_params,
        ),
    )


@router.get("/portfolio/{lot_id}", response_class=HTMLResponse)
async def lot_detail(
    request: Request,
    lot_id: UUID,
    session: Session,
    auth: CurrentWebAuth,
) -> HTMLResponse:
    service = LotService(session, get_settings().business_timezone, auth.user.id)
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
        context=_context(
            request,
            auth,
            lot=lot,
            purchase=purchase,
            history=history,
        ),
    )


@router.get("/import", response_class=HTMLResponse)
async def import_page(request: Request, auth: CurrentWebAuth) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="import.html",
        context=_context(request, auth),
    )


@router.post("/import/preview", response_class=HTMLResponse)
async def import_preview(
    request: Request,
    auth: CurrentWebAuth,
    session: Session,
    cache: Cache,
    storage: Storage,
    csrf_token: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> HTMLResponse:
    auth_service = AuthService(session, get_settings().auth)
    auth_service.verify_csrf(auth.session, csrf_token)
    error: str | None = None
    preview = None
    preview_id = None
    upload = None
    try:
        validate_spreadsheet_upload(file.filename or "", file.content_type)
        content = await file.read(get_settings().imports.max_upload_bytes + 1)
        if len(content) > get_settings().imports.max_upload_bytes:
            error = "Файл превышает допустимый размер."
        else:
            upload_service = UploadService(session, storage, auth.user.id)
            upload = await upload_service.store(
                file_name=file.filename or "spreadsheet",
                content_type=file.content_type or "application/octet-stream",
                content=content,
            )
            try:
                preview = SpreadsheetPortfolioReader().preview(
                    content, file.filename or "spreadsheet"
                )
                preview = replace(preview, upload_id=upload.id)
                await upload_service.mark_parsed(upload)
                preview_id = cache.put(preview, auth.user.id)
            except SpreadsheetImportError as exc:
                await upload_service.mark_failed(upload, exc)
                raise
    except SpreadsheetImportError as exc:
        error = str(exc)
    except Exception:
        error = "Не удалось сохранить файл в объектное хранилище."
    return templates.TemplateResponse(
        request=request,
        name="import_preview.html",
        context=_context(
            request,
            auth,
            preview=preview,
            preview_id=preview_id,
            upload=upload,
            error=error,
        ),
    )


@router.post("/import/commit")
async def import_commit(
    preview_id: Annotated[UUID, Form()],
    csrf_token: Annotated[str, Form()],
    auth: CurrentWebAuth,
    session: Session,
    cache: Cache,
) -> RedirectResponse:
    AuthService(session, get_settings().auth).verify_csrf(auth.session, csrf_token)
    preview = cache.get(preview_id, auth.user.id)
    if preview is None:
        raise HTTPException(404, "Import preview expired or was not found")
    await ImportService(session, auth.user.id).commit(preview)
    cache.discard(preview_id)
    return RedirectResponse("/portfolio", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, session: Session, auth: CurrentWebAuth) -> HTMLResponse:
    value = await SettingsService(session, auth.user.id).get()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=_context(
            request,
            auth,
            settings=value,
            tax_modes=list(TaxMode),
        ),
    )


@router.post("/settings")
async def settings_update(
    auth: CurrentWebAuth,
    session: Session,
    csrf_token: Annotated[str, Form()],
    market_data_ttl_seconds: Annotated[int, Form(ge=60)],
    tax_mode: Annotated[TaxMode, Form()],
    tax_rate: Annotated[Decimal, Form(ge=0, le=1)],
    default_sale_commission_rub_per_bond: Annotated[Decimal, Form(ge=0)],
) -> RedirectResponse:
    AuthService(session, get_settings().auth).verify_csrf(auth.session, csrf_token)
    value = await SettingsService(session, auth.user.id).get()
    value.market_data_ttl_seconds = market_data_ttl_seconds
    value.tax_mode = tax_mode
    value.tax_rate = tax_rate
    value.default_sale_commission_rub_per_bond = default_sale_commission_rub_per_bond
    await session.commit()
    return RedirectResponse("/settings", status_code=303)


@router.get("/data-status", response_class=HTMLResponse)
async def data_status(request: Request, session: Session, auth: CurrentWebAuth) -> HTMLResponse:
    settings = await SettingsService(session, auth.user.id).get()
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
        context=_context(request, auth, rows=rows),
    )


@router.get("/uploads", response_class=HTMLResponse)
async def uploads_page(request: Request, session: Session, auth: CurrentWebAuth) -> HTMLResponse:
    uploads = list(
        await session.scalars(
            select(UploadedFileModel)
            .where(UploadedFileModel.owner_id == auth.user.id)
            .order_by(UploadedFileModel.created_at.desc())
        )
    )
    return templates.TemplateResponse(
        request=request,
        name="uploads.html",
        context=_context(request, auth, uploads=uploads),
    )


@router.get("/account", response_class=HTMLResponse)
async def account_page(request: Request, auth: CurrentWebAuth) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="account.html",
        context=_context(request, auth, error=None),
    )


@router.post("/account/password")
async def account_change_password(
    request: Request,
    auth: CurrentWebAuth,
    session: Session,
    csrf_token: Annotated[str, Form()],
    current_password: Annotated[str, Form()],
    new_password: Annotated[str, Form()],
) -> Response:
    service = AuthService(session, get_settings().auth)
    service.verify_csrf(auth.session, csrf_token)
    try:
        await service.change_password(auth.user, current_password, new_password)
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="account.html",
            context=_context(request, auth, error=str(exc)),
            status_code=422,
        )
    response = RedirectResponse("/login", status_code=303)
    _clear_auth_cookies(response)
    return response


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, session: Session, auth: CurrentWebAuth) -> HTMLResponse:
    _require_web_admin(auth)
    users = await AuthService(session, get_settings().auth).list_users()
    uploads = list(
        await session.scalars(
            select(UploadedFileModel).order_by(UploadedFileModel.created_at.desc()).limit(100)
        )
    )
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context=_context(request, auth, users=users, uploads=uploads, error=None),
    )


@router.post("/admin/users")
async def admin_create_user(
    request: Request,
    auth: CurrentWebAuth,
    session: Session,
    csrf_token: Annotated[str, Form()],
    username: Annotated[str, Form()],
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    role: Annotated[UserRole, Form()] = UserRole.USER,
) -> Response:
    _require_web_admin(auth)
    service = AuthService(session, get_settings().auth)
    service.verify_csrf(auth.session, csrf_token)
    try:
        await service.create_user(
            username=username,
            email=email,
            password=password,
            role=role,
        )
    except Exception as exc:
        users = await service.list_users()
        uploads = list(
            await session.scalars(
                select(UploadedFileModel).order_by(UploadedFileModel.created_at.desc()).limit(100)
            )
        )
        return templates.TemplateResponse(
            request=request,
            name="admin.html",
            context=_context(
                request,
                auth,
                users=users,
                uploads=uploads,
                error=str(exc),
            ),
            status_code=422,
        )
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/users/{user_id}/toggle")
async def admin_toggle_user(
    user_id: UUID,
    auth: CurrentWebAuth,
    session: Session,
    csrf_token: Annotated[str, Form()],
) -> RedirectResponse:
    _require_web_admin(auth)
    service = AuthService(session, get_settings().auth)
    service.verify_csrf(auth.session, csrf_token)
    user = await service.get_user(user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if user.id == auth.user.id:
        raise HTTPException(422, "Нельзя отключить текущую учётную запись администратора")
    await service.set_active(user, not user.is_active)
    return RedirectResponse("/admin", status_code=303)


async def _portfolio_rows(session: AsyncSession, owner_id: UUID) -> list[dict[str, Any]]:
    lots = await LotService(session, get_settings().business_timezone, owner_id).list_all()
    settings = await SettingsService(session, owner_id).get()
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


def _context(
    request: Request,
    auth: CurrentWebAuth,
    **values: object,
) -> dict[str, object]:
    return {
        "current_user": auth.user,
        "csrf_token": request.cookies.get(get_settings().auth.csrf_cookie_name, ""),
        **values,
    }


def _set_auth_cookies(
    response: RedirectResponse,
    session_token: str,
    csrf_token: str,
) -> None:
    settings = get_settings().auth
    response.set_cookie(
        settings.session_cookie_name,
        session_token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token,
        max_age=settings.session_ttl_seconds,
        httponly=False,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


def _clear_auth_cookies(response: RedirectResponse) -> None:
    response.delete_cookie(get_settings().auth.session_cookie_name, path="/")
    response.delete_cookie(get_settings().auth.csrf_cookie_name, path="/")


def _safe_next(value: str) -> str:
    return value if value.startswith("/") and not value.startswith("//") else "/portfolio"


def _require_web_admin(auth: CurrentWebAuth) -> None:
    if auth.user.role != UserRole.ADMIN:
        raise HTTPException(403, "Administrator privileges are required")
