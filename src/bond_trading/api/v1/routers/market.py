from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.api.dependencies import get_moex_client
from bond_trading.api.schemas import RefreshAllOut
from bond_trading.application.services import InstrumentService
from bond_trading.infrastructure.db.session import get_session
from bond_trading.infrastructure.moex import MoexIssClient

router = APIRouter(prefix="/market", tags=["market"])
Session = Annotated[AsyncSession, Depends(get_session)]
Moex = Annotated[MoexIssClient, Depends(get_moex_client)]


@router.post("/refresh-all", response_model=RefreshAllOut, summary="Refresh all instruments")
async def refresh_all(session: Session, moex: Moex) -> RefreshAllOut:
    service = InstrumentService(session)
    instruments = await service.list_all()
    refreshed: list[str] = []
    errors: dict[str, str] = {}
    for instrument in instruments:
        try:
            await service.refresh(instrument.isin, moex)
            refreshed.append(instrument.isin)
        except Exception as exc:
            errors[instrument.isin] = str(exc)
    return RefreshAllOut(refreshed=refreshed, errors=errors)
