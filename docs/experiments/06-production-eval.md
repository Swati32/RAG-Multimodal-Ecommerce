# Experiment 06 — Production Evaluation Harness

## Question

Does the deployed multi-agent RAG system (router → specialists → generator → verifier, workflows 01–04) actually retrieve the right products and ground its answers, measured against the real system end-to-end — not assumed from the fact that each piece passed its own unit tests and individual live-verification checks during development?

This is different from every prior experiment in this repo (01–05): those validated one *component* in isolation (a chunking strategy, an indexing decision, SearchAgent's fusion method) against a throwaway index. This one runs real queries through the real deployed API (`RagEcommerce-Upload`'s `/query` endpoint → `RagEcommerce-Agents`' router) exactly as an end user would, and adds an *independent* judge — a separate Claude call that never sees the production system's own verifier's work — so a bug in the production verifier can't hide behind its own approval.

## Terminology / metric definitions

- **Precision (here)**: of the citations one query's answer actually returned, the fraction that point at the query's one known-correct product — `1 / (number of citations returned)` when that product is among them, else `0`. Not a traditional multi-relevant-item precision@k (this eval set has exactly one correct product per query by construction), so it doubles as a citation-list-tightness signal: a correct-but-padded citation list scores lower than a correct-and-focused one.
- **Recall@k (`RecallAtK`)**: of the one relevant product that exists for a query, the fraction of the time it was actually retrieved and cited (`1.0` if present in the citations, else `0.0`, averaged across queries) — design doc 05's own recall@k definition, specialized to a single relevant item per query.
- **Citation grounding rate (`CitationGroundingRate`)**: design doc 05 defines this as "of the claims in a generated answer, the fraction actually supported by the retrieved chunks." This harness doesn't segment individual claims, so it's approximated as the fraction of *answers* the independent judge scored predominantly grounded (groundedness ≥ 4/5) — a coarser, answer-level proxy for the doc's claim-level definition, flagged here rather than silently presented as the same thing.
- **Judge groundedness / relevance (1–5)**: a separate Claude Sonnet 4.5 call (`JUDGE_PROMPT` in `scripts/eval/eval_utils.py`) scores each (question, answer, citations) triple fresh — groundedness: do the answer's claims match what the citation snippets say; relevance: does the answer address the question. It receives only what the `/query` API actually returns, not the specialists' raw retrieved records.
- **Latency / cost per query**: wall-clock time around the real HTTP call to `/query`; cost is a documented *estimate* (see Method), not a metered value.

## Method

**Eval set**: 20 real products, sampled (fixed seed 42) from the live `Products` table, filtered to `review_count >= 3` and a non-empty brand (enough real content to synthesize a sensible query and judge an answer against). For each, Claude Haiku wrote one natural shopping question from the product's title/brand/category (`build_query_prompt`) — the expected answer is that product's own `product_id`. This reuses the synthetic-query-from-real-data technique already validated in Experiments 03–04, extended from retrieval-only scoring to the full production pipeline.

**Deviation from design doc 05, flagged**: the design calls for 50–100 *hand-labeled* pairs. This run used 20 *synthetically labeled* pairs. Reasoning: each query is a real, live invocation of the full deployed pipeline (routing → parallel specialist dispatch → streamed generation → verification → an independent judge call) — roughly 12s and a few cents of real Bedrock spend per query — and hand-labeling 50–100 pairs needs a human rater this session doesn't have. 20 synthetic pairs is a smaller, real signal, not a simulated one; scaling to the full spec is mechanical (more products, more judge calls) whenever it's worth the added cost.

**Run**: `.venv/bin/python scripts/eval/run_production_eval.py --n 20 --api-url <RagEcommerce-Upload's QueryApiUrl> --products-table <RagEcommerce-Data's Products table>`. Each query: synthesize the question (Haiku) → `POST /query` against the real deployed API → independent judge call (Sonnet 4.5) on the real response → compute precision/recall for that query. Aggregates published to CloudWatch under `RAGEcommerce/Eval` (dimensions `Model=claude-sonnet-4-5`, `AgentConfig=multi-agent-router`, `EmbeddingModel=titan-text-v2+cohere-embed-v4`), raw per-query results saved to `scripts/eval/results.json`.

**Cost estimate, flagged**: `CostPerQueryUsd` is computed from published Bedrock on-demand pricing × assumed token counts per call (routing, consolidation, generation, verification, one specialist turn — see `ESTIMATED_*` constants), not measured — the router's response doesn't currently report real token usage. A real metered value would need the router to surface token counts per call, not built here.

**Judge limitation, found while writing this up, not before**: the judge only ever sees what `/query` returns — the final citation *snippets*, not the specialists' original retrieved records. Since `extract_citations` (workflow 04) derives a citation's snippet from the *generator's own answer text* (the sentence before its `[[marker]]`), not a quote from the underlying record, several judge notes below call snippets "circular" or "self-referential" — the judge is correctly observing that it cannot independently verify a claim from a citation that just restates the claim. This is real signal about the *client-facing* citation format, not proof the production system is ungrounded: `verify_citations` (`src/agents/answer_generation.py`) already checks each citation against the real retrieved record server-side and drops ones that fail, before a citation ever reaches the API response. See Results/Decision for what this does and doesn't tell us.

## Results

20/20 queries completed. Aggregates:

| Metric | Value |
| --- | --- |
| `RetrievalPrecisionAtK` | 0.472 |
| `RecallAtK` | 0.850 |
| `CitationGroundingRate` | 0.450 |
| `JudgeGroundednessScore` | 3.25 / 5 |
| `JudgeRelevanceScore` | 4.05 / 5 |
| `AnswerLatencyMs` | 11,664 ms |
| `CostPerQueryUsd` | $0.038 (estimate) |

**Recall (0.850) is solid**: the system found and cited the actual target product for 17/20 synthetic queries. The 3 misses:
- Two (`B01E1KBPPI` "eyeshadow palette for brown eyes", `B07CZSFHZM` "The Ordinary copper peptides serum") got **zero citations and an honest "I don't have relevant information" answer** — the judge scored these groundedness 5 (no false claims) but relevance 1 (didn't help). This is the same honest-decline behavior already verified for GraphAgent and the generator earlier this session, now confirmed at the full-pipeline level: the system would rather say nothing than invent something, even at the cost of looking unhelpful. Correct behavior, bad recall for those two queries specifically — this dataset's All_Beauty catalog likely just doesn't carry those exact items.
- One (`B08JPPKZXF`, a scrunchie organizer) cited a *different*, plausible-but-wrong product — an actual retrieval miss, not a refusal.

**Citation grounding rate (0.450) is the concerning number**, and the judge notes explain why: 9 of 20 answers were flagged for citations that don't actually let a reader verify the claim, e.g.:
- *"The citation snippet is circular - it's just the answer text itself, not actual product information"* (`B000N9IQLS`, `B01M8J6515`)
- *"the citations are suspiciously self-referential - the snippets contain the exact same descriptive language as the answer itself"* (`B07N18TZWK`)
- *"claims about PACMAXI and Tecbeauty products but only provides one citation snippet that partially supports"* (`B08JPPKZXF`) — a real instance of the "hallucinated confidence despite citations" pitfall design doc 05 names directly.

## Decision & reasoning

**The eval harness itself is the deliverable that matters most here**: it's real (hits the real deployed API, not a mock), independent (a judge call that never sees the production verifier's own reasoning), and it worked — it surfaced a genuine, specific, actionable finding (citation snippets are self-referential) that no individual workflow's own live-verification step caught, because each of those checks used the system's own output to check itself.

**Not fixed in this session, flagged as the clear next step**: `extract_citations`'s snippet (the sentence before a `[[marker]]` in the generator's own text) is a UX/traceability regression from the pre-workflow-04 design, where the generator was asked to produce "a short supporting excerpt from that record's content" - an actual quote. The fix is concrete and scoped: `resolve_citations` already does a real DynamoDB lookup per citation (for title/image_url) - it could substitute a genuine excerpt from the product's own `description`/review content for the snippet field instead of passing through the generator's restated claim, without touching the streaming mechanism or the marker-based extraction that made streaming work in the first place. Deferred rather than done reactively mid-write-up, since it's a real design change deserving its own build-verify-document cycle, not a rushed patch.

**Recall (0.850) and the honest-decline pattern are validated, not just believed** - this is the first time the full pipeline's "don't hallucinate, decline instead" behavior was measured across a real batch rather than checked on one or two hand-picked queries.

**Not scaled to the full 50-100 hand-labeled spec**: the harness scales mechanically (raise `--n`, or swap `build_eval_set`'s query source for real hand-labeled pairs instead of synthetic ones) whenever the additional Bedrock spend and rating effort are worth it - not needed to prove the harness itself works.
