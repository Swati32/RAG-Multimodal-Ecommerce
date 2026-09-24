# Experiment 01 — Chunking Strategy

## Question

Design doc [01](../designs/01-ingestion-pipeline.md) originally specced fixed-size word-count chunking with overlap (300 words, 50-word overlap) for long reviews/descriptions. Does that actually produce good chunks, or does a sentence-aware alternative do better at the same granularity?

## Method

Streamed 40 real reviews (≥150 words) from the Amazon Reviews 2023 "All_Beauty" category. Compared three strategies, no LLM involved — purely deterministic metrics:

- **`word_count(300w/50 overlap)`** — the original production strategy: fixed-size word windows with overlap
- **`word_count(150w/30 overlap)`** — a smaller/more aggressive variant of the same approach
- **`sentence_aware(300w budget)`** — candidate strategy: pack whole sentences into a chunk up to a word budget, never splitting a sentence across two chunks, no overlap needed

Script: [`scripts/experiments/run_chunking_experiment.py`](../../scripts/experiments/run_chunking_experiment.py) (strategies defined in [`chunking_strategies.py`](../../scripts/experiments/chunking_strategies.py)).

## Results

| Strategy | Chunks/text | Words/chunk | Stdev | % clean sentence boundary |
| --- | --- | --- | --- | --- |
| `word_count(300w/50 overlap)` (original default) | 1.1 | 196.0 | 57.5 | 90.9% |
| `word_count(150w/30 overlap)` | 2.15 | 114.0 | 44.0 | 51.2% |
| **`sentence_aware(300w budget)`** | 1.1 | 191.4 | 60.7 | **100.0%** |

"% clean sentence boundary" = fraction of chunk endings that land on a sentence boundary (`.`/`!`/`?`) rather than mid-sentence — a proxy for whether a chunk reads as a coherent, self-contained unit for retrieval.

## Decision

**Adopted: sentence-aware chunking with a 300-word budget, replacing word-count+overlap.**

At the *same* chunk density as the original default (1.1 chunks/review), sentence-aware splitting is strictly better: 100% clean boundaries vs. 90.9%, and it needs no overlap at all — overlap exists specifically to avoid losing context across a mid-sentence cut, which doesn't happen here in the first place. The smaller word-count variant (150w/30) confirms the problem gets worse, not better, the more aggressively naive word-count splitting is applied (51.2% clean boundaries).

`src/ingestion/chunking.py` updated accordingly; `tests/test_chunking.py` updated to match.

**Known limitation, not handled**: a single sentence longer than the word budget becomes its own oversized chunk (no fallback split within a sentence). Not worth the complexity for this dataset — real product reviews essentially never have a single 300+ word sentence.

## Follow-up: revisit once retrieval is live

This decision rests on structural proxy metrics only (boundary cleanliness, chunk density) — the cheapest of the three validation tiers described in design doc [05](../designs/05-observability-cost.md)'s Evaluation Framework, not the ground-truth one. It says nothing about actual retrieval quality on its own.

**Resolved in [Experiment 03](03-chunking-retrieval-validation.md)**: now that OpenSearch indexing and embeddings are live, sentence-aware was re-validated against real precision@k and hit-rate@k on a synthetic labeled query set, and confirmed as the better strategy on both. See that doc for method, terminology, and results.
