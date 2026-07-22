from fastapi import APIRouter

from bond_trading.api.v1.routers import imports, instruments, lots, market, settings

router = APIRouter(prefix="/api/v1")
router.include_router(instruments.router)
router.include_router(lots.router)
router.include_router(imports.router)
router.include_router(market.router)
router.include_router(settings.router)
