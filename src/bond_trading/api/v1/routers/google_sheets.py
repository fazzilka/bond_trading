from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.api.auth_dependencies import CurrentUser
from bond_trading.api.dependencies import get_google_sheets_gateway
from bond_trading.api.schemas import (
    SheetConnectionCheckOut,
    SheetConnectionOut,
    SheetConnectionUpdate,
    SheetSyncJobOut,
)
from bond_trading.application.services.sheets import SheetSyncService
from bond_trading.infrastructure.db.models import SheetSyncTrigger
from bond_trading.infrastructure.db.session import get_session
from bond_trading.infrastructure.google_sheets import GoogleSheetsError, GoogleSheetsGateway

router = APIRouter(prefix="/integrations/google-sheets", tags=["google-sheets"])
Session = Annotated[AsyncSession, Depends(get_session)]
Sheets = Annotated[GoogleSheetsGateway, Depends(get_google_sheets_gateway)]


@router.get("", response_model=SheetConnectionOut | None, summary="Получить подключение таблицы")
async def get_connection(session: Session, user: CurrentUser) -> SheetConnectionOut | None:
    connection = await SheetSyncService(session, user.id).get_connection()
    return SheetConnectionOut.model_validate(connection) if connection else None


@router.put("", response_model=SheetConnectionOut, summary="Настроить Google Таблицу")
async def update_connection(
    payload: SheetConnectionUpdate,
    session: Session,
    user: CurrentUser,
) -> SheetConnectionOut:
    try:
        connection = await SheetSyncService(session, user.id).configure(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return SheetConnectionOut.model_validate(connection)


@router.post(
    "/test",
    response_model=SheetConnectionCheckOut,
    summary="Проверить доступ к Google Таблице",
)
async def test_connection(
    session: Session,
    sheets: Sheets,
    user: CurrentUser,
) -> SheetConnectionCheckOut:
    try:
        connection, title = await SheetSyncService(session, user.id).check_connection(sheets)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except GoogleSheetsError as exc:
        raise HTTPException(503, str(exc)) from exc
    return SheetConnectionCheckOut(
        spreadsheet_title=title,
        worksheet_name=connection.worksheet_name,
    )


@router.post("/sync", response_model=SheetSyncJobOut, summary="Поставить ручное обновление")
async def sync_now(session: Session, user: CurrentUser) -> SheetSyncJobOut:
    try:
        job = await SheetSyncService(session, user.id).enqueue(SheetSyncTrigger.MANUAL)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return SheetSyncJobOut.model_validate(job)


@router.get("/jobs", response_model=list[SheetSyncJobOut], summary="История синхронизаций")
async def list_jobs(session: Session, user: CurrentUser) -> list[SheetSyncJobOut]:
    return [
        SheetSyncJobOut.model_validate(job)
        for job in await SheetSyncService(session, user.id).list_jobs()
    ]


@router.get("/jobs/{job_id}", response_model=SheetSyncJobOut, summary="Получить синхронизацию")
async def get_job(job_id: UUID, session: Session, user: CurrentUser) -> SheetSyncJobOut:
    job = await SheetSyncService(session, user.id).get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задание синхронизации не найдено")
    return SheetSyncJobOut.model_validate(job)
