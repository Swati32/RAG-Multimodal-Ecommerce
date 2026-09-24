# 04 — Indexing Strategy: Hybrid Combination Method

## Question

Design doc [02](../designs/02-retrieval-agents.md#indexing-strategy) flags the hybrid combination method as a genuinely open, empirical question, deferred until now: SearchAgent's planned query is a `bool` combining a `knn` clause and a `match` clause, relying on OpenSearch's default (summed) scoring. Is that naive combination actually good, or does it need a real fusion method instead?

(The ANN engine choice — `lucene` — and the similarity metric — cosine — are explicitly *not* in scope here; design doc 02 already reasons those through as ops-simplicity calls with nothing to benchmark at this dataset's scale, not open questions.)

## Terminology

- **BM25**: OpenSearch/Lucene's default text-relevance scoring for a `match` query — scores a document higher the more query terms it contains, weighted by how rare each term is across the corpus and how concentrated it is in that document. Unbounded — scores commonly range from single digits to 20+ depending on term rarity and match strength, with no fixed ceiling.
- **k-NN / cosine similarity score**: how close a chunk's embedding vector is to the query's embedding vector. OpenSearch's `knn` query here returns cosine similarity, which is bounded to [0, 1] (or occasionally slightly related values depending on OpenSearch's exact scoring transform) — practically, in this dataset's results, scores landed in a narrow 0.6-0.7 band.
- **Naive hybrid combination**: OpenSearch's default behavior when a `bool` query's `should` clause contains both a `knn` and a `match` clause — it simply **adds** each clause's raw score. Named "naive" here (and in design doc 02) because it's a raw sum of two scores from *different, incompatible scales*, not a considered fusion method.
- **Reciprocal Rank Fusion (RRF)**: a fusion method that ignores raw scores entirely and combines *rankings*. For each method's ranked list, a document at rank `r` contributes `1 / (C + r)` (here `C = 60`, the standard damping constant from the original RRF paper); a document's final score is the sum of its contributions across all the ranked lists it appears in, and results are re-sorted by that. Because it only uses rank position, not score magnitude, it can't be dominated by one method's score scale the way naive summation can.
- **precision@k / hit rate@k**: same definitions as [Experiment 03](03-chunking-retrieval-validation.md) — precision@k is the fraction of the top-k results that are actually relevant (`relevant items in top k / k`); hit rate@k is whether *any* relevant result appears in the top-k (`1 if any relevant item in top k else 0`, averaged over queries). "Relevant" here again means: the retrieved chunk came from the same review the query was synthesized from.

## Method

Reused the same real corpus, ground truth, and synthetic-query methodology as Experiment 03 (300 real long reviews, 30 synthesized queries, precision@5 / hit-rate@5) — see that doc for why synthetic queries are used and what "relevant" means. This time, one single index was built using sentence-aware chunking (the strategy Experiment 03 confirmed), and four query methods were compared against the *same* index and *same* queries, isolating the combination method as the only variable:

1. **`knn_only`** — pure vector search
2. **`bm25_only`** — pure text match
3. **`naive_hybrid`** — `bool` query with both a `knn` and a `match` clause in `should`, OpenSearch's default summed scoring (the design doc's current plan)
4. **`rrf`** — client-side reciprocal rank fusion of the `knn_only` and `bm25_only` ranked lists (top 20 each), fused and cut to the top 5

Script: [`scripts/experiments/run_indexing_strategy_experiment.py`](../../scripts/experiments/run_indexing_strategy_experiment.py), built on shared plumbing factored out into [`retrieval_eval_utils.py`](../../scripts/experiments/retrieval_eval_utils.py) (also now used by Experiment 03's script, replacing what was duplicated code).

## Results

| Method | precision@5 | hit_rate@5 |
| --- | --- | --- |
| knn_only | 0.207 | 0.933 |
| **bm25_only** | **0.213** | **0.967** |
| naive_hybrid | 0.213 | 0.967 |
| rrf | 0.193 | 0.967 |

`naive_hybrid` and `bm25_only` are identical to three decimal places. That's not a coincidence — a direct raw-score check on one query confirms why:

| | top-5 scores |
| --- | --- |
| knn_only | 0.686, 0.647, 0.637, 0.626, 0.620 |
| bm25_only | 21.58, 10.33, 9.18, 7.73, 7.38 |
| naive_hybrid | 22.27, 10.33, 9.18, 7.73, 7.38 |

BM25 scores (single digits to 20+) are 10-30x larger than the bounded cosine-similarity scores (0.6-0.7). Summing them means the k-NN clause barely moves the total — the top hybrid score is just the top BM25 score plus a ~1-point nudge. **The naive combination isn't really combining anything; it's BM25 with noise.**

## Decision

**Do not build SearchAgent's hybrid query as a naive `bool(knn, match)` combination.** The empirical answer to the design doc's open question is no — it isn't good enough, because it doesn't do what "hybrid" implies. It silently degrades to text-only search whenever both clause types fire, which defeats the reason a vector clause was added in the first place (catching semantic matches with no keyword overlap, like "cradle cap" vs. a review that never uses that phrase).

**Also do not adopt plain RRF as tested here** — it scored worst on precision (0.193), because it fuses in `knn_only`'s comparatively weaker ranking (0.207) alongside `bm25_only`'s stronger one (0.213), and rank-only fusion has no way to weight the stronger method more. Note this isn't a fair test of "RRF is bad" — it's a test of *unweighted* RRF fusing two rankings of noticeably different quality on this dataset.

**When SearchAgent is actually built** (workflow 02, not started): use OpenSearch's native score-normalization search pipeline (referenced but not tested here — min-max or z-score normalizing each clause's scores onto a comparable range before combining), or weighted RRF favoring the stronger method, rather than either naive summation or unweighted RRF. This experiment's job was to confirm the naive default isn't safe to ship as-is, not to pick its final replacement — that decision should be made once SearchAgent exists and can be tuned against a larger query set.

**Known limitation**: n=30 queries, one synthetic set, single dataset category (All_Beauty) — the specific magnitude of BM25's score dominance will vary by query/corpus, but the *mechanism* (unbounded BM25 vs. bounded cosine scores summed directly) is a structural property of OpenSearch's default scoring, not a fluke of this sample.

## Still open

**Chunk granularity ("small-to-big")** — design doc 02's other flagged indexing question — is not addressed here. Unlike the combination method, it can't be evaluated with retrieval-only metrics: the question is whether a chunk that *matches* a query is actually *sufficient to answer from*, which requires an answer-generation step with citations to measure (the same reason citation grounding rate is still blocked — see [PROGRESS.md](../PROGRESS.md)). Revisit once workflow 02's retrieval agents (generator + verifier) exist.
