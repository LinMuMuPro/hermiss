from fastapi import APIRouter, Depends

from routers.auth import get_current_user
from services.runtime_status_service import build_runtime_summary

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("/summary")
def diagnostics_summary(user=Depends(get_current_user)):
    return build_runtime_summary(user, include_recent_errors=True)
