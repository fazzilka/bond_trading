from fastapi import APIRouter, Depends

from bond_trading.api.auth_dependencies import get_authenticated_session
from bond_trading.api.v1.routers import (
    admin,
    auth,
    google_sheets,
    imports,
    instruments,
    lots,
    market,
    settings,
    uploads,
)

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
protected = [Depends(get_authenticated_session)]
router.include_router(instruments.router, dependencies=protected)
router.include_router(lots.router, dependencies=protected)
router.include_router(imports.router, dependencies=protected)
router.include_router(market.router, dependencies=protected)
router.include_router(settings.router, dependencies=protected)
router.include_router(uploads.router, dependencies=protected)
router.include_router(admin.router, dependencies=protected)
router.include_router(google_sheets.router, dependencies=protected)
