"""Lambda entrypoint for POST /query - see submit_query.py for the actual
logic; this just adapts an API Gateway HTTP API event to it, fetching the
uploaded image from S3 (if any) and invoking the router's AgentCore
Runtime."""

from __future__ import annotations

import json
import os
import uuid

import boto3

from agents.bedrock_client import agentcore_client
from api.submit_query import build_router_payload, collect_final_response

_s3 = boto3.client("s3")
# This calls the *router*, not a specialist - its full pipeline (routing +
# parallel dispatch + consolidation + streaming generation + verification)
# needs more than the 20s default meant for one specialist's own dispatch
# (see agentcore_client's docstring); kept a few seconds under the
# Lambda's own function timeout (see upload_stack.py) so a real timeout
# surfaces as this client's own error, not an ungraceful Lambda kill.
_agentcore = agentcore_client(os.environ["AWS_REGION"], read_timeout=55)

_HEADERS = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}


def handler(event, context):
    body = json.loads(event.get("body") or "{}")
    prompt = body.get("prompt", "")
    object_key = body.get("object_key")

    if not prompt and not object_key:
        return {"statusCode": 400, "headers": _HEADERS, "body": json.dumps({"error": "Provide a prompt, an uploaded image's object_key, or both."})}

    image_bytes = None
    content_type = None
    if object_key:
        obj = _s3.get_object(Bucket=os.environ["UPLOAD_BUCKET"], Key=object_key)
        image_bytes = obj["Body"].read()
        content_type = obj.get("ContentType")  # set by the presigned POST's own Content-Type field - see presign_upload.py

    payload = build_router_payload(prompt, image_bytes, content_type=content_type)
    response = _agentcore.invoke_agent_runtime(
        agentRuntimeArn=os.environ["ROUTER_ARN"],
        runtimeSessionId=f"api-{uuid.uuid4()}",
        payload=json.dumps(payload).encode(),
    )
    final = collect_final_response(response["response"].read())

    return {"statusCode": 200, "headers": _HEADERS, "body": json.dumps(final)}
