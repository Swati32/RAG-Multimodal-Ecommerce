"""Submits a shopper's query (optionally with an uploaded image) to the
router's AgentCore Runtime and returns its final answer - see docs/designs/
03-image-upload.md. The router streams its answer internally as
server-sent events (docs/designs/04-inference-serving.md), but this API
collapses that stream into one response rather than relaying it further -
see design doc 03's "Implementation notes" for why (no frontend exists yet
to consume a second hop of streaming, and Lambda-to-API-Gateway response
streaming is a separate, heavier integration this workflow doesn't need
yet).
"""

from __future__ import annotations

import base64
import json

# Converse's image content block and Cohere Embed v4's data-URI both need
# to know the actual format - the upload allowlist (see presign_upload.py)
# accepts jpg/png/webp, not jpeg only, so a hardcoded "jpeg" downstream
# would mislabel a real PNG/WEBP upload (a real, if narrower, version of
# the same "the model wasn't actually given accurate information" class of
# bug documented in docs/designs/03-image-upload.md).
CONTENT_TYPE_TO_FORMAT = {"image/jpeg": "jpeg", "image/png": "png", "image/webp": "webp"}


def build_router_payload(prompt: str, image_bytes: bytes | None, content_type: str | None = None) -> dict:
    payload = {"prompt": prompt}
    if image_bytes is not None:
        payload["image_base64"] = base64.b64encode(image_bytes).decode()
        payload["image_format"] = CONTENT_TYPE_TO_FORMAT.get(content_type, "jpeg")
    return payload


def collect_final_response(raw_body: bytes) -> dict:
    """Parses the router's `data: {...}\\n\\n` SSE stream (see
    router_runtime.py's `invoke`) and returns its one `{"type": "final",
    ...}` event. Intermediate `answer_chunk` events are dropped - this API
    waits for the complete answer rather than relaying the stream."""
    text = raw_body.decode("utf-8")
    for block in text.split("\n\n"):
        block = block.strip()
        if not block.startswith("data:"):
            continue
        event = json.loads(block[len("data:") :].strip())
        if event.get("type") == "final":
            return {
                "answer": event["answer"],
                "citations": event["citations"],
                "dispatched": event["dispatched"],
                "trace": event.get("trace"),
            }
    raise ValueError("Router response stream ended without a final event")
