from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.api.schemas import SettingsOut, SettingsPatch
from bond_trading.application.services import SettingsService
from bond_trading.infrastructure.db.session import get_session

router = APIRouter(prefix="/settings", tags=["settings"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=SettingsOut, summary="Get calculation settings")
async def get_app_settings(session: Session) -> SettingsOut:
    value = await SettingsService(session).get()
    return SettingsOut.model_validate(value)


@router.patch("", response_model=SettingsOut, summary="Update calculation settings")
async def update_app_settings(payload: SettingsPatch, session: Session) -> SettingsOut:
    value = await SettingsService(session).get()
    for field, field_value in payload.model_dump(exclude_unset=True).items():
        setattr(value, field, field_value)
    await session.commit()
    await session.refresh(value)
    return SettingsOut.model_validate(value)
