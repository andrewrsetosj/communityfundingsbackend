"""
Upload routes — generate S3 presigned URLs for campaign image uploads
Frontend uploads directly to S3 (no file passes through our server)
"""

from fastapi import APIRouter, Depends, HTTPException
import boto3
import os
import uuid

from app.auth import get_current_user
from app.models.models import User

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

S3_BUCKET = os.getenv("AWS_S3_BUCKET", "community-fundings-uploads")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")


def get_s3_client():
    return boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )


@router.post("/presigned-url")
async def get_presigned_upload_url(
    filename: str,
    content_type: str = "image/jpeg",
    user: User = Depends(get_current_user),
):
    """
    Generate a presigned S3 URL for direct browser upload.
    Frontend uses this URL to PUT the image directly to S3.
    Returns the presigned URL and the final public URL of the uploaded file.
    """
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
            ExpiresIn=300,  # 5 minutes
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate upload URL: {str(e)}")

    public_url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{key}"

    return {
        "upload_url": presigned_url,
        "public_url": public_url,
        "key": key,
    }