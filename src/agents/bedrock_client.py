"""Shared boto3 client configuration for Bedrock/AgentCore calls - see
docs/designs/04-inference-serving.md, "Retry/fallback". Centralized so
every agent runtime gets the same retry/timeout behavior instead of each
one hand-rolling its own Config.
"""

from __future__ import annotations

import boto3
from botocore.config import Config

# "standard" retry mode implements exponential backoff with jitter for
# retryable errors (including Bedrock's ThrottlingException) out of the
# box - no need to hand-roll it, matching design doc 04's "exponential
# backoff with jitter on Bedrock throttling errors". "adaptive" mode adds
# client-side rate limiting built for sustained bulk throughput (already
# handled by the ingestion pipeline's own RateLimiter, see
# src/rate_limiter.py) and isn't a fit for these low-volume, real-time,
# single-call agent steps.
_RETRY_CONFIG = Config(retries={"mode": "standard", "max_attempts": 3})


def bedrock_runtime_client(region: str):
    """For Claude Converse calls (specialist tool-use turns, router phases,
    generator/verifier) - a 10s read timeout per call, the "hard timeout
    per agent step (e.g. 10s)" from design doc 04, so one slow call can't
    hang the whole request."""
    return boto3.client("bedrock-runtime", region_name=region, config=_RETRY_CONFIG.merge(Config(read_timeout=10, connect_timeout=5)))


def agentcore_client(region: str):
    """For the router's specialist dispatch (bedrock-agentcore:InvokeAgentRuntime).
    A single dispatch call can run several Converse turns internally inside
    the specialist (see MAX_TURNS in each *_agent_runtime.py), so this gets
    a longer timeout than one bare Converse call - long enough for a normal
    multi-turn specialist run, still bounded so one hung specialist can't
    block the whole router request indefinitely."""
    return boto3.client("bedrock-agentcore", region_name=region, config=_RETRY_CONFIG.merge(Config(read_timeout=20, connect_timeout=5)))
