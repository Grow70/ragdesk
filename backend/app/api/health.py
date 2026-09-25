"""Process liveness endpoint."""

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health/live")
def live(request: Request) -> dict[str, str]:
    return {"status": "ok", "request_id": request.state.request_id}
