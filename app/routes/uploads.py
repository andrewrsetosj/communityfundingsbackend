"""
S3 uploads: server-side multipart (recommended) and optional presigned PUT.
"""

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
import boto3
import os
import uuid

import db as rds_db
from app.auth import get_current_user
from app.models.models import User

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

S3_BUCKET = os.getenv("AWS_S3_BUCKET", "community-fundings-uploads")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
# Object key prefix inside the bucket, e.g. "Campaigns" -> Campaigns/{campaign_id}/{uuid}.jpg
S3_KEY_PREFIX = (os.getenv("S3_KEY_PREFIX") or "Campaigns").strip().strip("/")

MAX_IMAGE_BYTES = 12 * 1024 * 1024


def get_s3_client():
    # Strip: trailing spaces/newlines in .env break the key id string.
    key_id = (os.getenv("AWS_ACCESS_KEY_ID") or "").strip()
    secret = (os.getenv("AWS_SECRET_ACCESS_KEY") or "").strip()
    session_token = (os.getenv("AWS_SESSION_TOKEN") or "").strip() or None

    if not key_id or not secret:
        raise RuntimeError(
            "AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY missing in environment"
        )

    kwargs: dict = {
        "region_name": AWS_REGION,
        "aws_access_key_id": key_id,
        "aws_secret_access_key": secret,
    }
    if session_token:
        kwargs["aws_session_token"] = session_token

    return boto3.client("s3", **kwargs)


def _build_campaign_object_key(campaign_id: int, ext: str) -> str:
    stem = uuid.uuid4().hex
    if S3_KEY_PREFIX:
        return f"{S3_KEY_PREFIX}/{campaign_id}/{stem}.{ext}"
    return f"campaigns/{campaign_id}/{stem}.{ext}"


async def _assert_draft_owned(campaign_id: int, user_id: str) -> None:
    pool = await rds_db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT campaign_id FROM public.campaigns
            WHERE campaign_id = $1 AND creator_id = $2 AND status = 'draft'
            LIMIT 1
            """,
            campaign_id,
            user_id,
        )
    if not row:
        raise HTTPException(
            status_code=403,
            detail="Invalid campaign or you can only upload to your own drafts",
        )


@router.post("/campaign-file")
async def upload_campaign_file(
    campaign_id: int = Query(..., description="Draft campaign id"),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Upload one image via API → S3 (avoids browser CORS to S3)."""
    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    content_type = (file.content_type or "").strip() or "application/octet-stream"
    if content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed: {content_type}",
        )

    await _assert_draft_owned(campaign_id, user.id)

    raw = await file.read()
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 12MB)")

    filename = file.filename or "image.jpg"
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "jpg"
    if ext.lower() not in {"jpg", "jpeg", "png", "webp", "gif"}:
        ext = "jpg"
    key = _build_campaign_object_key(campaign_id, ext)

    try:
        s3 = get_s3_client()
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=raw,
            ContentType=content_type,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"S3 upload failed: {str(e)}")

    public_url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{key}"

    return {
        "public_url": public_url,
        "key": key,
        "bucket": S3_BUCKET,
        "region": AWS_REGION,
    }


@router.post("/presigned-url")
async def get_presigned_upload_url(
    filename: str = Query(..., min_length=1),
    content_type: str = Query("image/jpeg"),
    user: User = Depends(get_current_user),
):
    """Optional: presigned PUT for direct browser → S3 (requires S3 CORS)."""
    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"File type {content_type} not allowed")

    ext = filename.rsplit(".", 1)[-1] if "." in filename else "jpg"
    key = f"campaigns/{user.id}/{uuid.uuid4().hex}.{ext}"

    try:
        s3 = get_s3_client()
        presigned_url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": S3_BUCKET,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=300,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate upload URL: {str(e)}")

    public_url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{key}"

    return {
        "upload_url": presigned_url,
        "public_url": public_url,
        "key": key,
        "bucket": S3_BUCKET,
        "region": AWS_REGION,
    }
