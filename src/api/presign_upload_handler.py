"""Lambda entrypoint for POST /upload-url - see presign_upload.py for the
actual logic; this just adapts an API Gateway HTTP API event to it."""

from __future__ import annotations

import json
import os

import boto3

from api.presign_upload import UnsupportedContentType, build_presigned_post

_s3 = boto3.client("s3")

_HEADERS = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}


def handler(event, context):
    body = json.loads(event.get("body") or "{}")
    content_type = body.get("content_type", "")

    try:
        presigned = build_presigned_post(_s3, os.environ["UPLOAD_BUCKET"], content_type)
    except UnsupportedContentType:
        return {"statusCode": 400, "headers": _HEADERS, "body": json.dumps({"error": f"Unsupported content type: {content_type}"})}

    return {"statusCode": 200, "headers": _HEADERS, "body": json.dumps(presigned)}
