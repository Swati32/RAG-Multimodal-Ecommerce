import base64

import pytest

from api.submit_query import build_router_payload, collect_final_response


def test_build_router_payload_text_only():
    assert build_router_payload("find a moisturizer", None) == {"prompt": "find a moisturizer"}


def test_build_router_payload_includes_base64_image_when_given():
    payload = build_router_payload("what does this look like", b"fake-image-bytes", content_type="image/jpeg")

    assert payload["prompt"] == "what does this look like"
    assert payload["image_base64"] == base64.b64encode(b"fake-image-bytes").decode()
    assert payload["image_format"] == "jpeg"


def test_build_router_payload_maps_content_type_to_the_converse_image_format():
    """The upload allowlist accepts jpg/png/webp, not jpeg only (see
    presign_upload.py) - downstream Converse/Cohere calls need to know
    which one a given upload actually is."""
    assert build_router_payload("q", b"bytes", content_type="image/png")["image_format"] == "png"
    assert build_router_payload("q", b"bytes", content_type="image/webp")["image_format"] == "webp"


def test_build_router_payload_defaults_to_jpeg_for_an_unknown_or_missing_content_type():
    assert build_router_payload("q", b"bytes", content_type=None)["image_format"] == "jpeg"
    assert build_router_payload("q", b"bytes", content_type="application/octet-stream")["image_format"] == "jpeg"


def test_collect_final_response_parses_the_terminal_event_from_a_real_shaped_sse_stream():
    """Matches the real SSE format observed from a live router invocation -
    see docs/designs/04-inference-serving.md."""
    raw = (
        b'data: {"type": "answer_chunk", "text": "It has "}\n\n'
        b'data: {"type": "answer_chunk", "text": "great sound."}\n\n'
        b'data: {"type": "final", "answer": "It has great sound.", "citations": [{"product_id": "P1"}], "dispatched": ["search_agent"]}\n\n'
    )

    result = collect_final_response(raw)

    assert result == {"answer": "It has great sound.", "citations": [{"product_id": "P1"}], "dispatched": ["search_agent"]}


def test_collect_final_response_raises_if_the_stream_never_produces_a_final_event():
    raw = b'data: {"type": "answer_chunk", "text": "partial"}\n\n'

    with pytest.raises(ValueError):
        collect_final_response(raw)
