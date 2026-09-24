# 03 — Chunking Strategy: Real Retrieval Validation

## Question

[Experiment 01](01-chunking-strategy.md) picked sentence-aware chunking over fixed-size word-count-with-overlap, but only on structural proxy metrics (chunk-boundary cleanliness) — before OpenSearch indexing and embeddings existed. Now that real retrieval is live, does sentence-aware still win once actually measured by whether it retrieves the right content, not just how clean its chunk boundaries look?

## Terminology

- **Chunking strategy**: how a long piece of text (here, a product review) gets split into smaller pieces ("chunks") before being embedded and indexed, since embedding an entire long document as one vector loses fine-grained detail. The two strategies compared: **sentence-aware** (pack whole sentences into a ~300-word budget, never cut mid-sentence — the current production default, see [chunking.py](../../src/ingestion/chunking.py)) and **word-count** (fixed 300-word windows with 50-word overlap, no regard for sentence boundaries — the original design-doc default before Experiment 01).
- **Corpus**: the full set of documents available to be retrieved from. Here, 300 real long reviews (≥150 words) streamed from the dataset, chunked under each strategy into its own throwaway OpenSearch index (~390 chunks per strategy) — kept separate from the production `chunks` index so this experiment can't corrupt real data.
- **Query**: a short natural-language question a user might type. Since there's no real query log for this dataset, queries are **synthetic**: for 30 of the 300 pool reviews (the "target" reviews), Claude Haiku is prompted to write a short shopping question that review's content would answer, without quoting the review — e.g. a review that mentions a shaver's battery dying mid-use might produce "does this razor's battery last through a full shave?" This gives a natural-language query while still knowing, by construction, which single review is "correct" for it.
- **Relevant** (ground truth): for a synthesized query, a chunk is relevant if and only if it was cut from the same review the query was generated from. The other 270 pool reviews exist purely as distractor content — plausible-looking but wrong results a bad chunking strategy might surface instead.
- **k**: the number of top-ranked results examined per query. Here, k=5 — roughly how many chunks a downstream generator would realistically be given as context.
- **Precision@k**: of the top-k retrieved chunks for a query, the fraction that are actually relevant (`relevant chunks in top k / k`). Penalizes a strategy whose chunk splits push the right content out of the top-k results, or whose split pieces read poorly enough to embed further from the query than they should.
- **Hit rate@k**: whether *any* chunk from the correct review appears in the top-k, regardless of how many (`1 if any relevant chunk in top k else 0`, averaged over all queries). A softer, complementary metric to precision@k — a review chunked into 2 pieces can only ever contribute 2/5 to precision@5 even in a perfect world, so hit rate answers "did we find the right source at all" independent of how many of its pieces made the cut.

(Citation grounding rate — the other metric named in design doc [05](../designs/05-observability-cost.md) — isn't measurable yet: it requires an actual answer-generation step with citations, which is workflow [02](../designs/02-retrieval-agents.md)'s retrieval agents, not yet built. This experiment covers retrieval quality only.)

## Method

1. Stream 300 real long reviews (≥150 words) from the dataset — the retrieval corpus.
2. Pick 30 of them (fixed random seed) as **target** reviews; synthesize one query per target via Claude Haiku.
3. Chunk all 300 pool reviews under each strategy separately, embed every chunk with Titan Text Embeddings V2 (real-time, matching production), and bulk-index into two isolated, throwaway OpenSearch indices — `eval-chunks-sentence-aware` and `eval-chunks-word-count` — so corpus size and content are identical between the two arms, isolating chunking strategy as the only variable.
4. Embed each of the 30 queries, run a k-NN search (k=5) against both indices, and score precision@5 / hit_rate@5 per strategy using the ground truth above.
5. Delete both eval indices at the end — this is a one-off analysis, not part of the production index.

Script: [`scripts/experiments/run_chunking_retrieval_experiment.py`](../../scripts/experiments/run_chunking_retrieval_experiment.py).

## Results

| Strategy | precision@5 | hit_rate@5 |
| --- | --- | --- |
| **sentence_aware (current default)** | **0.200** | **0.867** |
| word_count (300w/50 overlap) | 0.180 | 0.800 |

n=30 queries, corpus=300 reviews (~390 chunks/strategy).

## Decision

**Confirmed: sentence-aware chunking stays the production strategy.** It wins on both metrics — retrieves the correct review in the top 5 for 86.7% of queries, versus 80.0% for word-count, and does so with a higher share of the top-5 actually being correct (0.200 vs 0.180 precision). This validates the Experiment 01 decision on the metric that actually matters (does it retrieve the right thing), not just the structural proxy (are boundaries clean) it was originally chosen on.

**Caveats**:
- n=30 queries is small; a ~2-point precision gap (0.200 vs 0.180) is directionally consistent with Experiment 01 but not large enough to treat as a precise measurement — it's a confirmation, not a tight bound.
- Absolute precision@5 (~0.2) looks low, but is expected by construction: most reviews split into only 1-2 chunks, so even a perfect strategy can't exceed ~0.2-0.4 precision@5 for those queries. Hit rate@5 (~0.87 vs ~0.80) is the more informative number here.
- Queries are synthetic (LLM-generated from the target review itself), not real user queries — they're a reasonable proxy for "does this review's content get found," not a substitute for real query logs, which don't exist for this project.
