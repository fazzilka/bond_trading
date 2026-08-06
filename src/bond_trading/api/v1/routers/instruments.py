from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.api.auth_dependencies import CurrentUser
from bond_trading.api.dependencies import get_moex_client
from bond_trading.api.schemas import InstrumentOut, InstrumentRefreshOut, MarketSnapshotOut
from bond_trading.application.services import InstrumentService
from bond_trading.application.services.sheets import enqueue_sheet_sync
from bond_trading.infrastructure.db.models import SheetSyncTrigger
from bond_trading.infrastructure.db.session import get_session
from bond_trading.infrastructure.moex import MoexIssClient, MoexNotFoundError

router = APIRouter(prefix="/instruments", tags=["instruments"])
Session = Annotated[AsyncSession, Depends(get_session)]
Moex = Annotated[MoexIssClient, Depends(get_moex_client)]


@router.get("", response_model=list[InstrumentOut], summary="List bond instruments")
async def list_instruments(session: Session) -> list[InstrumentOut]:
    return [
        InstrumentOut.model_validate(item) for item in await InstrumentService(session).list_all()
    ]


@router.get("/{isin}", response_model=InstrumentOut, summary="Get an instrument by ISIN")
async def get_instrument(isin: str, session: Session) -> InstrumentOut:
    instrument = await InstrumentService(session).get_by_isin(isin)
    if instrument is None:
        raise HTTPException(404, "Instrument not found")
    return InstrumentOut.model_validate(instrument)


@router.post(
    "/{isin}/refresh",
    response_model=InstrumentRefreshOut,
    summary="Refresh instrument and market data from MOEX ISS",
)
async def refresh_instrument(
    isin: str, session: Session, moex: Moex, user: CurrentUser
) -> InstrumentRefreshOut:
    try:
        instrument, market = await InstrumentService(session).refresh(isin, moex)
    except MoexNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    await enqueue_sheet_sync(session, user.id, SheetSyncTrigger.MOEX_REFRESHED)
    await session.commit()
    return InstrumentRefreshOut(
        instrument=InstrumentOut.model_validate(instrument),
        market=MarketSnapshotOut.model_validate(market),
    )
