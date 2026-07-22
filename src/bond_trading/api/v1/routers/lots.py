from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.api.schemas import (
    CalculateRequest,
    CalculationOut,
    LotCreate,
    LotOut,
    LotPatch,
    YieldSnapshotOut,
)
from bond_trading.application.services import LotService
from bond_trading.core.config import get_settings
from bond_trading.domain.errors import DomainError
from bond_trading.infrastructure.db.session import get_session

router = APIRouter(prefix="/lots", tags=["lots"])
Session = Annotated[AsyncSession, Depends(get_session)]


def service(session: AsyncSession) -> LotService:
    return LotService(session, get_settings().business_timezone)


@router.get("", response_model=list[LotOut], summary="List purchase lots")
async def list_lots(session: Session) -> list[LotOut]:
    return [LotOut.from_model(lot) for lot in await service(session).list_all()]


@router.post("", response_model=LotOut, status_code=201, summary="Create a purchase lot")
async def create_lot(payload: LotCreate, session: Session) -> LotOut:
    lot = await service(session).create(payload.model_dump())
    return LotOut.from_model(lot)


@router.get("/{lot_id}", response_model=LotOut, summary="Get a purchase lot")
async def get_lot(lot_id: UUID, session: Session) -> LotOut:
    lot = await service(session).get(lot_id)
    if lot is None:
        raise HTTPException(404, "Bond lot not found")
    return LotOut.from_model(lot)


@router.patch("/{lot_id}", response_model=LotOut, summary="Update a purchase lot")
async def update_lot(lot_id: UUID, payload: LotPatch, session: Session) -> LotOut:
    try:
        lot = await service(session).update(lot_id, payload.model_dump(exclude_unset=True))
    except DomainError as exc:
        raise HTTPException(404, exc.message) from exc
    return LotOut.from_model(lot)


@router.delete("/{lot_id}", status_code=204, summary="Delete a purchase lot")
async def delete_lot(lot_id: UUID, session: Session) -> Response:
    try:
        await service(session).delete(lot_id)
    except DomainError as exc:
        raise HTTPException(404, exc.message) from exc
    return Response(status_code=204)


@router.post(
    "/{lot_id}/calculate",
    response_model=CalculationOut,
    summary="Calculate planned and current annual yield",
)
async def calculate_lot(
    lot_id: UUID, payload: CalculateRequest, session: Session
) -> CalculationOut:
    try:
        result = await service(session).calculate(lot_id, payload.valuation_date)
    except DomainError as exc:
        raise HTTPException(422, exc.message) from exc
    return CalculationOut(
        snapshot=YieldSnapshotOut.model_validate(result.snapshot),
        current_available=result.current is not None,
    )


@router.get(
    "/{lot_id}/yield-history",
    response_model=list[YieldSnapshotOut],
    summary="List saved yield calculations",
)
async def yield_history(lot_id: UUID, session: Session) -> list[YieldSnapshotOut]:
    try:
        values = await service(session).history(lot_id)
    except DomainError as exc:
        raise HTTPException(404, exc.message) from exc
    return [YieldSnapshotOut.model_validate(value) for value in values]
