from fastapi import APIRouter

from bond_trading.api.v1.router import router as v1_router

router = APIRouter()
router.include_router(v1_router)


@router.get("/health", tags=["operations"], summary="Application health")
async def health() -> dict[str, str]:
    return {"status": "success"}
