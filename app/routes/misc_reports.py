from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user
from app.db import get_pool
from app.models.models import User

router = APIRouter(prefix="/api/misc-reports", tags=["misc-reports"])


class CreateMiscReportRequest(BaseModel):
    report_description: str
    report_reason: str
    reported_content_url: str | None = None


def _is_valid_optional_url(value: str | None) -> bool:
    if not value:
        return True

    try:
        parsed = urlparse(value)
        return bool(parsed.scheme and parsed.netloc)
    except Exception:
        return False


@router.post("")
async def create_misc_report(
    payload: CreateMiscReportRequest,
    current_user: User = Depends(get_current_user),
):
    report_description = payload.report_description.strip()
    report_reason = payload.report_reason.strip()
    reported_content_url = (payload.reported_content_url or "").strip() or None

    if not report_description:
        raise HTTPException(status_code=400, detail="report_description is required")

    if not report_reason:
        raise HTTPException(status_code=400, detail="report_reason is required")

    if reported_content_url and not _is_valid_optional_url(reported_content_url):
        raise HTTPException(status_code=400, detail="reported_content_url must be a valid URL")

    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO misc_reports (
                creator_id,
                report_description,
                report_reason,
                reported_content_url
            )
            VALUES ($1, $2, $3, $4)
            RETURNING report_id, creator_id, report_description, report_reason, reported_content_url, status, time_created
            """,
            current_user.id,
            report_description,
            report_reason,
            reported_content_url,
        )

    return {
        "ok": True,
        "report": dict(row),
    }