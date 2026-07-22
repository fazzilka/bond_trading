from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["operations"], summary="Application health")
async def health() -> dict[str, str]:
    return {"status": "success"}
