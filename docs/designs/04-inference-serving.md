# 04 — Inference Serving

## Overview

Distinct from [02 — Retrieval Agents](02-retrieval-agents.md), which decides *what* to retrieve — this covers *how* each model call is served: which mode, what latency to expect, and how failures are handled.

**Addresses**: NFR-2 (latency is a soft target only, not a hard constraint).

## Model calls by stage

| Stage | Model | Mode | Notes |
| --- | --- | --- | --- |
| Query embedding | Titan Text/Multimodal Embeddings | Real-time InvokeModel | Single item, low latency |
| Bulk embedding | Titan Text/Multimodal Embeddings | Bedrock Batch | High throughput, no latency requirement |
| Agent tool-calling steps | Claude (via Bedrock) | Real-time, non-streaming | Needs the full tool-call decision before acting; streaming doesn't help here |
| Final answer generation | Claude (via Bedrock) | Real-time, streaming to client | Streaming improves perceived latency for a chat-style answer |

## Decisions & reasoning

**Retry/fallback**: exponential backoff with jitter on Bedrock throttling errors; a hard timeout per agent step (e.g. 10s) so one slow tool call can't hang the whole request — on timeout, the agent proceeds with whatever context it already has rather than failing the request outright.

**Latency budget** (soft target for demo usability, not a hard requirement — see [Non-Functional Requirements](../../README.md)): query embedding ~100ms, each retrieval agent call ~200–400ms, up to 2 router iterations, final generation streaming starts within ~1.5s of fused context being ready.

**Prompt/version management**: pin a specific Bedrock model version (not "latest") for both the router and the generator so behavior doesn't shift between runs; track prompt templates in version control alongside the ingestion code.

## Status

Not started — see [../PROGRESS.md](../PROGRESS.md)
