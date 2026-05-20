"""健康检查API。"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
@router.get("/api/health")
async def health():
    return {"status": "healthy"}
