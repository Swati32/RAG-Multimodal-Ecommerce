# Experiment 02 — Summarization Prompting Strategy

## Question

`src/ingestion/summarization.py` summarizes a long review with a single fixed prompt before it's read by [Experiment 01](01-chunking-strategy.md)'s chunker (design doc [02](../designs/02-retrieval-agents.md), "Agents vs Direct LLM Calls"). Is that prompt actually the best choice, or does a different framing produce better summaries?

## Method

Three candidate prompts, defined in [`scripts/experiments/prompt_candidates.py`](../../scripts/experiments/prompt_candidates.py):

- **`concise_claims`** (current default) — "summarize in under 100 words, keep every concrete claim"
- **`bullet_extraction`** — extract concrete claims as bullet points, omit generic sentiment
- **`aggressive_compression`** — summarize in under 50 words, prioritize the single most useful claim

Evaluation: **LLM-as-judge**, per the technique chosen in design doc [05](../designs/05-observability-cost.md). For each candidate prompt, summarize 10 real long reviews (≥150 words, streamed from the dataset), then have a separate, stronger model score each (review, summary) pair on:
- **Groundedness (1–5)** — does the summary avoid inventing claims not in the original
- **Coverage (1–5)** — does it retain the key concrete claims

Generator model: Claude Haiku (cheap/fast — matches the bulk, ingestion-time use case). Judge model: a stronger Claude model, deliberately different from the generator to avoid self-grading bias.

Score per prompt = mean over the 10 reviews.

Script: [`scripts/experiments/run_prompting_experiment.py`](../../scripts/experiments/run_prompting_experiment.py).

## Results

| Prompt | Groundedness (1–5) | Coverage (1–5) |
| --- | --- | --- |
| **`concise_claims` (current default)** | **4.80** | 4.30 |
| `bullet_extraction` | 4.60 | **4.40** |
| `aggressive_compression` | 4.20 | 3.70 |

## Decision

**Kept: `concise_claims`, no change to `src/ingestion/summarization.py`.**

It wins outright on groundedness — the metric that matters most for a RAG ingestion pipeline, since a hallucinated claim baked into a chunk at index time silently poisons every future retrieval of that chunk — and is within 0.1 of the best coverage score. `bullet_extraction` scores marginally higher on coverage but lower on groundedness, and produces a bullet-list format that reads less naturally as embedded prose than the other two. `aggressive_compression` loses on both axes: the 50-word budget forces it to drop concrete claims (lower coverage) without any groundedness benefit from the extra compression.

**Known limitation**: n=10 reviews, one run, no repeated sampling — scores this close (4.80 vs 4.60) could plausibly flip with a larger sample or a different judge temperature. Good enough to confirm the current default isn't leaving obvious quality on the table, not a statistically rigorous comparison.

## Status: run

Ran 2026-09-21 on account 953146692069, once Bedrock model access cleared (the two blockers below were already resolved by the time this ran):

1. **Account verification hold** (`AccessDeniedException: Your account is currently being verified`) — cleared on its own within about an hour.
2. **Anthropic model use-case form** (`ResourceNotFoundException: Model use case details have not been submitted for this account`) — a one-time form in the Bedrock console (Model access page), required before invoking Anthropic models.

Also discovered along the way: these newer Claude models require **inference profile IDs** for on-demand invocation, not bare model IDs — e.g. `us.anthropic.claude-haiku-4-5-20251001-v1:0`, not `anthropic.claude-haiku-4-5-20251001-v1:0` (`ValidationException` otherwise). And not every model listed by `aws bedrock list-foundation-models` is actually enabled for the account — `us.anthropic.claude-sonnet-5` specifically returned `AccessDeniedException: ... is not available for this account` even after the verification hold cleared; the judge model was switched to `us.anthropic.claude-sonnet-4-5-20250929-v1:0`, which passed that check.

**One more gotcha hit on the actual run**: the judge prompt asks for "ONLY this JSON, no other text," but Claude still wrapped the response in a ` ```json ... ``` ` code fence, breaking a bare `json.loads`. Fixed in the script by stripping a markdown fence before parsing (`_parse_judge_response` in `run_prompting_experiment.py`) rather than tightening the prompt further — treat judge output as needing defensive parsing generally, not just for this one prompt.
