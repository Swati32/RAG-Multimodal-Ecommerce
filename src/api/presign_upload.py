"""Presigned upload for query images - see docs/designs/03-image-upload.md,
"Pre-signed upload, not a Lambda-proxied upload". Constraints (allowed
content types, size cap) are enforced by S3 itself via the presigned
POST's policy conditions, not just app-level validation - a client can't
bypass them by skipping application code, since S3 rejects a form upload
that violates any condition regardless of what the client claims.
"""

from __future__ import annotations

import uuid

ALLOWED_CONTENT_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
QUERY_IMAGES_PREFIX = "query-images/"


class UnsupportedContentType(ValueError):
    pass


def build_presigned_post(s3_client, bucket: str, content_type: str, expires_in: int = 300) -> dict:
    """Returns `{"url": ..., "fields": {...}, "key": ...}` - the client
    POSTs a multipart form with exactly these fields plus the file itself
    directly to `url`, uploading straight to S3 without the file ever
    passing through this API."""
    extension = ALLOWED_CONTENT_TYPES.get(content_type)
    if extension is None:
        raise UnsupportedContentType(content_type)

    key = f"{QUERY_IMAGES_PREFIX}{uuid.uuid4()}.{extension}"
    presigned = s3_client.generate_presigned_post(
        Bucket=bucket,
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[
            {"Content-Type": content_type},
            ["content-length-range", 0, MAX_UPLOAD_BYTES],
        ],
        ExpiresIn=expires_in,
    )
    return {**presigned, "key": key}
