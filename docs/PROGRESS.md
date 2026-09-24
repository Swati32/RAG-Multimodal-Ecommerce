# Progress

Tracks implementation status per workflow. Update this file as each workflow moves from designed to implemented.

| Workflow | Design | Implementation | Status |
| --- | --- | --- | --- |
| Ingestion & Refresh Pipeline | [01](designs/01-ingestion-pipeline.md) | [src/ingestion](../src/ingestion) | Complete |
| Retrieval Agents & Query Orchestration | [02](designs/02-retrieval-agents.md) | [src/agents](../src/agents) | Core pipeline complete |
| Image Upload & Multimodal Query | [03](designs/03-image-upload.md) | — | Not started |
| Inference Serving | [04](designs/04-inference-serving.md) | [src/agents/bedrock_client.py](../src/agents/bedrock_client.py) | Complete |
| Observability & Cost Controls | [05](designs/05-observability-cost.md) | — | Not started |
| Frontend Hosting | [06](designs/06-frontend-hosting.md) | — | Not started |

## Ingestion & Refresh Pipeline — what's done

Implemented and unit-tested (16 tests, no AWS credentials needed — DynamoDB is moto-mocked, Claude calls are stubbed):
- `src/ingestion/models.py` — `Product`, `Review`, `Chunk`
- `src/ingestion/chunking.py` — sentence-aware description/review splitting, tag-summary generation (see [01](designs/01-ingestion-pipeline.md#chunking-strategy), [Experiment 01](experiments/01-chunking-strategy.md), and [Experiment 03](experiments/03-chunking-retrieval-validation.md) validating it against real retrieval)
- `src/ingestion/summarization.py` — direct Claude call (via Bedrock) to summarize a long review before chunking, per the "Agents vs Direct LLM Calls" decision in [02](designs/02-retrieval-agents.md); injected as a dependency so `chunk_review` stays testable without live Bedrock access. Prompt wording confirmed as the best of three candidates by LLM-as-judge — see [Experiment 02](experiments/02-summarization-prompting.md)
- `src/ingestion/dynamo_writer.py` — idempotent product/review upserts
- `src/ingestion/dynamo_reader.py` — the inverse: DynamoDB item → `Product`/`Review`, plus a paginated `scan_all_items`
- `src/ingestion/opensearch_documents.py` — chunk-to-document mapping with the filter fields from [02](designs/02-retrieval-agents.md#opensearch-metadata--filtering), plus the index mapping

**Deployed and loaded (steps 1-2 of the pipeline, done for real, as an actual Glue job)**: `RagEcommerce-Data`, `RagEcommerce-Search`, and `RagEcommerce-Glue` are all live in AWS (account 953146692069, us-east-2) — see Infrastructure below. `rag-ecommerce-load-dataset` (a Glue Python Shell job, `infra/glue_scripts/load_dataset.py`) streams the Amazon Reviews 2023 "All_Beauty" category from Hugging Face (no full download) and writes products/reviews into the real DynamoDB tables — confirmed via a real `start-job-run` (`SUCCEEDED`, 97s) and `scan`: **5,000 products and 10,313 reviews**.

This replaced an earlier local-script version (`scripts/load_dataset.py`, since deleted) that deviated from design doc 01's Glue-job spec without flagging it — corrected per the Implementation Fidelity rule in CLAUDE.md. Getting the real Glue job working surfaced three environment gotchas now recorded in CLAUDE.md: Python Shell only supports Python 3.9 (no `X | None` syntax), `--extra-py-files` zips need manual `sys.path` handling, and Python Shell doesn't auto-inject `--JOB_NAME`.

**Step 3 done for real too**: `rag-ecommerce-chunk-and-summarize` (`infra/glue_scripts/chunk_and_summarize.py`) reads every product/review from DynamoDB, chunks them (sentence-aware, summarizing long reviews via Claude Haiku first), and writes the result to `s3://.../processed/chunks.jsonl`. Run via `start-job-run`, `SUCCEEDED` in 123s: **20,339 chunk records** (5,026 description + 5,000 tag_summary + 10,313 review). New module: `src/ingestion/dynamo_reader.py` (the inverse of `dynamo_writer` — DynamoDB item → `Product`/`Review`, plus a paginated `scan_all_items`).

One more environment gotcha found and recorded in CLAUDE.md: Glue Python Shell bundles a 2022-era boto3/botocore that predates Bedrock's service model (`UnknownServiceError: Unknown service: 'bedrock-runtime'`) — fixed by forcing a current `boto3` via `--additional-python-modules`.

**Step 4 done for real too, but not as originally planned**: `rag-ecommerce-embed-chunks` (`infra/glue_scripts/embed_chunks.py`) embeds every chunk via Bedrock Titan Text Embeddings V2, **real-time, not Batch** — Batch inference turned out to be blocked for this whole account (confirmed against every model, not just Titan; see [01](designs/01-ingestion-pipeline.md#embedding-generation-real-time-titan-calls-not-bedrock-batch) for the deviation and why). Rate-limited to 540 req/min (90% of Titan's confirmed 600/min on-demand quota) via a thread-safe `RateLimiter`, not just a worker-count cap, since throughput depends on latency. Run via `start-job-run`, `SUCCEEDED` in 2302s (~38 min): **20,339 chunks, each with a real 1024-dim embedding vector**.

**Step 5 done for real too — the core pipeline is now end-to-end**: `rag-ecommerce-load-opensearch` (`infra/glue_scripts/load_opensearch.py`) creates the `chunks` index (via `INDEX_MAPPING`, engine `lucene`) if missing, joins every embedded chunk with its product's filter fields from DynamoDB, and bulk-loads the lot. First attempt hit `TransportError(429, 'Too Many Requests')` — the single-node t3.small.search domain's bulk queue filled up against `helpers.bulk`'s default `chunk_size=500` with no retry (`max_retries=0` by default); fixed with `chunk_size=100, max_retries=5`. Re-run `SUCCEEDED` in 191s: **20,339 documents, 0 errors**, confirmed via `client.count` and a real search query returning relevant hits with correct filter fields.

**Graph edges done for real, but not as originally planned**: `rag-ecommerce-build-graph-edges` (`infra/glue_scripts/build_graph_edges.py`) derives `BELONGS_TO`/`HAS_PRODUCT` (category), `HAS_BRAND`/`HAS_PRODUCT` (brand), and `CO_REVIEWED_WITH` (same-reviewer product pairs, substituting for the dataset's empty `bought_together` field) — see [01](designs/01-ingestion-pipeline.md#graph-edges-co_reviewed_with-not-co_purchased_with) for the deviation and why. Run via `start-job-run`, `SUCCEEDED` in 104s: **19,458 edges** in the real `GraphEdges` table, verified via a full scan and cross-checked internally consistent (5,000 `BELONGS_TO` + 9,505 `HAS_PRODUCT` + 4,505 `HAS_BRAND` + 448 `CO_REVIEWED_WITH`). New modules: `src/ingestion/graph_edges.py` (pure edge-derivation functions, unit-tested) and `src/ingestion/graph_writer.py` (idempotent edge upsert, mirrors `dynamo_writer`).

**Image embeddings done for real too — ImageAgent has real data to query**: `rag-ecommerce-embed-images` (`infra/glue_scripts/embed_images.py`) downloads each product's primary image and embeds it via Bedrock Cohere Embed v4 (`us.cohere.embed-v4:0`) — Titan Multimodal Embeddings isn't available on this account, see [01](designs/01-ingestion-pipeline.md#image-embeddings-cohere-embed-v4-one-per-product). Rate-limited to 180 req/min (Cohere's 200/min quota). First run wrote 1536-dim vectors and `rag-ecommerce-load-images` failed with a real OpenSearch `400`: `lucene`'s k-NN engine caps dimension at 1024. Fixed by requesting `output_dimension: 1024` from Cohere directly (re-ran the embed job) rather than switching ANN engines. `rag-ecommerce-load-images` (`infra/glue_scripts/load_images.py`) then created the `product_images` index and bulk-loaded it: `SUCCEEDED` in 49s, **5,000 documents**, confirmed via `client.count`, mapping inspection, and a real k-NN search (query = an indexed doc's own vector, correctly returned itself as the top hit, score 1.0). New module additions: `src/rate_limiter.py` (extracted from `embed_chunks.py` so both embedding jobs share it), `IMAGE_INDEX_MAPPING`/`build_image_document` in `opensearch_documents.py`.

Deliberately scoped to one (the first) image per product, not all 24,062 images across the catalog — see [01](designs/01-ingestion-pipeline.md#image-embeddings-cohere-embed-v4-one-per-product) for why.

**Step Functions orchestration + daily EventBridge trigger done for real, deployed and verified — workflow 01 is now fully complete.** A real delta-refresh mechanism, not just orchestration wiring around the existing full-load jobs:
- `src/ingestion/delta_reviews.py` — `select_new_reviews`, a pure function picking up to a bounded cap of not-yet-loaded reviews per product, unit-tested
- `infra/glue_scripts/load_delta_reviews.py` — new Glue job, re-streams the dataset each run (no persisted cursor - simpler, and fast enough at this dataset's scale) and upserts only what's genuinely new
- `infra/glue_scripts/chunk_and_summarize.py` extended with a delta mode (`--reviews_input_key`) that chunks only the new reviews, not the whole catalog — full mode (the existing initial-load usage) is unchanged and still the default
- `infra/stacks/refresh_stack.py` (new `RagEcommerce-Refresh` stack) — a Step Functions state machine (`aws_stepfunctions_tasks.GlueStartJobRun`, `.sync` integration so each stage really waits for the previous one) chaining `LoadDeltaReviews → ChunkDeltaReviews → EmbedDeltaChunks → LoadDeltaIntoOpenSearch → RebuildGraphEdges`, each Glue task's `Arguments` pointing the reused job definitions at delta-specific S3 keys instead of the full-corpus ones; an EventBridge `rate(1 day)` rule targets it directly, no Lambda glue code needed

Deviates from the original plan in two deliberate ways (both in [01](designs/01-ingestion-pipeline.md#simulating-refresh-cadence)): delta batches are "next N not-yet-loaded reviews per product" rather than calendar week/month slices (simpler, same effect, correctness doesn't depend on the calendar framing), and graph edges are fully rebuilt every run rather than incrementally diffed (the rebuild is cheap enough — ~100s — that incremental edge logic isn't worth the complexity).

**Real, unprompted verification**: EventBridge's `rate()` schedule fires once immediately on rule creation, not only on the recurring interval — so the very first execution ran automatically within seconds of deploying, not a manually-triggered or mocked test. It `SUCCEEDED` in ~6 minutes. Checked before vs. after, all real counts: **DynamoDB reviews 10,313 → 10,513** (+200, matching the S3 delta batch), **OpenSearch `chunks` index 20,339 → 20,539** (+200, real new embeddings, not placeholders), **`GraphEdges` 19,458 → 19,476** (+18 = 9 new `CO_REVIEWED_WITH` pairs × 2 directions, from reviewers whose newly-added review now overlaps with an existing one) — exactly the staleness test the design doc's follow-up called for ("confirm the graph and search index both reflect the newer state"), demonstrated in one real run rather than simulated.

## Retrieval Agents & Query Orchestration — what's done

**Runtime pivoted twice before landing — see [02](designs/02-retrieval-agents.md#agent-implementation-hand-rolled-claude-tool-use-loop-on-bedrock-agentcore-runtime) for the full story.** AWS Bedrock Agents (classic, multi-agent collaboration) was the original plan; a real deploy attempt hit `AccessDeniedException: Bedrock Agents is in Maintenance Mode` — closed to any account with no prior usage. Bedrock AgentCore was considered as a replacement but turned out to be a hosting platform, not a declarative orchestrator — the router/dispatch logic has to be hand-written regardless. Landed on: a hand-rolled Claude tool-use loop (Bedrock Converse API) per agent, hosted on AgentCore Runtime.

**LookupAgent done for real, deployed and verified** — the first agent built, chosen to prove the Dockerfile/CDK/IAM/AgentCore wiring before the harder ones:
- `src/agents/lookup_tools.py` — `get_product_response`/`get_review_response`, pure DynamoDB lookups, unit-tested with moto
- `src/agents/lookup_agent_runtime.py` — the Claude tool-use loop (`bedrock_agentcore`'s `BedrockAgentCoreApp`, `@app.entrypoint`), unit-tested with a stub Bedrock client (same injected-stub pattern as `summarization.py`)
- `infra/agent_runtimes/lookup_agent/Dockerfile` + `infra/stacks/agents_stack.py` (`AgentsStack`, `bedrockagentcore.Runtime` L2 construct, `platform=LINUX_ARM64`, CDK auto-builds/pushes the image)

Deployed via `cdk deploy RagEcommerce-Agents`: runtime status `READY`. Verified via three real `invoke-agent-runtime` calls (not mocked) — a valid product id, a nonexistent id (correctly reported not found), and an out-of-scope request (correctly declined). All three passed as designed.

**Contract correction, applied to both agents**: LookupAgent initially returned a paraphrased prose answer, which didn't match the design's own sequence diagram (specialists return structured `results` to the router; a separate generator produces the user-facing answer). Fixed by factoring the loop into `src/agents/tool_loop.py`, shared by both agents — see [02](designs/02-retrieval-agents.md#agent-implementation-hand-rolled-claude-tool-use-loop-on-bedrock-agentcore-runtime) for the full contract (`{"results": [...], "message": str|None}`).

**SearchAgent done for real, deployed and verified** — hybrid (BM25 + k-NN) search over the `chunks` index with structured filters:
- `src/agents/search_tools.py` — `search_products` (native OpenSearch `hybrid` query + normalization pipeline, see [Experiment 05](experiments/05-searchagent-fusion-method.md)), unit-tested with a stub OpenSearch client
- `src/agents/search_agent_runtime.py` — embeds the query text (Titan, real-time) inside the tool call once Claude has decided what to search for, then runs the hybrid query
- `infra/agent_runtimes/search_agent/Dockerfile` + `AgentsStack` additions (shared `_build_runtime` helper, `search_domain.grant_read_write` — not `grant_read`, see below)

Two real deploy-time bugs, both fixed and recorded in [CLAUDE.md](../CLAUDE.md): (1) a `403` on the search-pipeline setup PUT — the OpenSearch domain's account-wide resource policy alone wasn't sufficient; needed an explicit `grant_read_write` on the runtime's role; (2) a `ValidationException` from Bedrock Converse — `toolResult.content[0].json` rejected `search_products`' list return value, fixed by wrapping list results as `{"items": [...]}` on the wire (in the shared `tool_loop.py`, so every future agent gets this for free).

Verified via three real `invoke-agent-runtime` calls: a filtered semantic query ("gentle moisturizer for sensitive skin under $15" → 5 results, all price-filtered correctly), a nonexistent-brand filter (0 results, no fabrication), and an off-category query ("headphones with good bass" — this dataset is All_Beauty only) — the tool returned lexical noise, but Claude's own message correctly recognized and flagged the results as not actually relevant rather than presenting them as a real answer.

**GraphAgent done for real, deployed and verified** — DynamoDB adjacency-list traversal over `GraphEdges`, three relation types (`co_reviewed` 1-hop, `same_brand`/`same_category` 2-hop):
- `src/agents/graph_tools.py` — `find_related_products`, pure DynamoDB `Query` calls, unit-tested with moto against real edge-derivation functions from `graph_edges.py`
- `src/agents/graph_agent_runtime.py` — same shared `tool_loop.py` pattern as the other two agents
- `infra/agent_runtimes/graph_agent/Dockerfile` + `AgentsStack` addition (`graph_edges_table.grant_read_data`)

First agent to deploy and work correctly on the first real attempt — no new gotchas, benefiting from every fix the prior two agents already found. Verified against real edges from the actual `build_graph_edges.py` output (not synthetic test data): a real 3-product brand (`Plant Therapy`) correctly returned its other 2 products, a real `CO_REVIEWED_WITH` pair from the sparse 448-edge co-reviewed graph correctly traversed, and a no-product-given request correctly declined rather than guessing.

**ImageAgent done for real, deployed and verified — all four retrieval specialists are now built.** k-NN over `product_images`, driven by an uploaded photo rather than text:
- `src/agents/image_tools.py` — `search_similar_images`, pure k-NN (no hybrid text component), reuses `search_filters.py` (extracted from `search_tools.py` once a second agent needed the identical filter logic)
- `src/agents/image_agent_runtime.py` — embeds the image *eagerly*, before the loop starts (unlike SearchAgent's lazy embed — a photo has no "extraction" step to wait on); `run_tool` is a per-request closure, not a fixed module function, since it needs that request's embedding, which never round-trips through a tool call's arguments
- `src/agents/tool_loop.py` generalized to accept multimodal content (`str | list[dict]`), so an agent can pass Claude an image content block directly — verified Claude Haiku 4.5 actually accepts image input via Converse with a real call before building around it
- `infra/agent_runtimes/image_agent/Dockerfile` + `AgentsStack` addition

One real deploy-time bug, now in [CLAUDE.md](../CLAUDE.md) and a broadening of SearchAgent's earlier finding: `grant_read` doesn't cover an ordinary `_search` query at all — a real `403` (`es:ESHttpPost` denied) showed opensearch-py sends search bodies as POST, not GET. `grant_read_write` is what actually works for any role that queries OpenSearch, not just ones that also write.

Verified against a real product image (not a synthetic fixture): downloaded an actual indexed product's own image and queried with it — top result was that exact product (self-match), plus 4 other genuinely similar items across different brands, with Claude correctly describing what was in the photo unprompted; a second call with "only from the brand kapoua" in the accompanying text correctly narrowed the same visual matches down to just that brand.

**Router done for real, deployed and verified — workflow 02's core retrieval loop (routing → parallel dispatch → LLM-judged consolidation → deterministic dedupe) is complete.** Built last, once all four specialists existed to dispatch to:
- `src/agents/router_tools.py` — `dispatch_specialist` (calls another agent's AgentCore runtime via `bedrock-agentcore:InvokeAgentRuntime`), `dedupe_and_rank` (deterministic post-processing, unit-tested with pure dicts)
- `src/agents/router_runtime.py` — two focused single-turn JSON-output Claude calls (routing decision, then consolidation) instead of `tool_loop.py`'s Claude-tool-use loop, since the router's two-phase shape doesn't fit that pattern the way the specialists' single-phase loops do; real parallel dispatch via `ThreadPoolExecutor`, not just Converse tool_use batching
- `infra/agent_runtimes/router/Dockerfile` + `AgentsStack` addition (`grant_invoke_runtime` from each specialist's runtime onto the router's role)

No new gotchas — clean synth, clean deploy, all three real verification calls passed without a fix cycle, the first agent this workflow to go start-to-finish with zero issues.

Verified against the real deployed runtime: a single-specialist question (routed to `search_agent` only, correctly), and — the case that actually matters — "Tell me about product B01A5YXRX2, and what other products does the same brand make?", which correctly routed to **both** `graph_agent` and `lookup_agent` in parallel and consolidated their results into one deduplicated list (the named product's full record plus its two real same-brand siblings, no duplicates); a genuinely out-of-scope question ("What is your return policy?") correctly dispatched nothing rather than forcing a guess.

**Generator + verifier done for real, deployed and verified — workflow 02's core request pipeline is now complete end-to-end**, routing through to a grounded, cited, verified answer:
- `src/agents/answer_generation.py` — `generate_answer` (Sonnet 4.5, drafts the answer + citations from the router's consolidated records), `verify_citations` (**Haiku, not Sonnet** — corrected while implementing, from an earlier design-doc draft that said both would be Sonnet, back to the original "a second, *cheaper* Claude call" reasoning), `resolve_citations` (plain DynamoDB lookup, no model call, attaches title/image_url/a placeholder product_url — no real frontend exists yet to link to)
- `src/agents/router_runtime.py` extended: after consolidation, hands off internally to generate → verify → resolve, returning the design's actual Answer Format (`{"answer": ..., "citations": [...]}`) instead of stopping at raw `results`
- `AgentsStack` addition: `products_table.grant_read_data` on the router's role (citation resolution needs a DynamoDB read)

No new gotchas — clean synth, clean deploy, all real verification calls passed on the first try.

Verified against the real deployed pipeline: the same multi-specialist question as the router's own test ("Tell me about product B01A5YXRX2, and what other products does the same brand make?") produced 4 correctly-grounded citations for the named product (one per distinct fact, each with a real quoted snippet) and **correctly declined to cite** the two same-brand sibling products at all, since GraphAgent's records for them had no real text content to ground a citation in — the generator's own judgment caught this rather than fabricating a snippet, and said so honestly in the answer text instead. A search-only question and an out-of-scope question both also completed the full pipeline correctly.

Workflow 02 (Retrieval Agents & Query Orchestration) is now functionally complete — real routing, real parallel multi-agent dispatch, real LLM-judged consolidation, real deterministic dedup, real grounded generation, real citation verification, all deployed and verified against live AWS, not mocks.

## Inference Serving — what's done

**Retry/timeout/graceful-degradation done for real, deployed and verified** — see [04](designs/04-inference-serving.md#implementation-notes) for the full config reasoning. `src/agents/bedrock_client.py` centralizes client construction for every agent: `Config(retries={"mode": "standard", "max_attempts": 3})` (boto3's built-in exponential backoff + jitter, no hand-rolled retry loop needed) plus a per-call `read_timeout` (10s for Converse calls, 20s for the router's specialist-dispatch calls, since one dispatch is a whole multi-turn specialist run, not one model call). On a timeout, both `tool_loop.py` (a specialist's own loop) and `router_runtime.py` (the router's dispatch to a specialist) degrade gracefully — return/proceed with whatever was already collected, rather than failing the whole request. All five `*_agent_runtime.py`/`router_runtime.py` modules switched from ad hoc `boto3.client(...)` calls to the shared factories. Unit-tested against the real `botocore.exceptions.ReadTimeoutError` type (forcing a genuine live Bedrock timeout isn't practical); redeployed `RagEcommerce-Agents` and re-ran a real router invocation end-to-end to confirm the client config change caused no regression.

**Streaming final-answer generation done for real, deployed and verified too — workflow 04 is now fully complete.** Replaced the JSON-blob answer format with plain-text streaming + inline `[[product_id]]` citation markers (a deliberate, flagged deviation from design doc 02's original Answer Format — JSON doesn't stream usefully). `stream_answer` (Bedrock `converse_stream`) yields text deltas; `extract_citations` parses markers out of the reassembled full text once streaming finishes (verified live that a marker can split across chunk boundaries — `[[B07968N5GC]]` arrived as two separate chunks in a real response — and reassembly handles it correctly). The router's AgentCore entrypoint is now a generator, auto-streamed by the SDK as real SSE. One new real deploy-time gotcha: `converse_stream` needs `bedrock:InvokeModelWithResponseStream`, a separate IAM action from `bedrock:InvokeModel` — a real `AccessDeniedException` confirmed it wasn't implied, fixed in `agents_stack.py`. Verified live: `invoke-agent-runtime` against the deployed router returned `contentType: text/event-stream`, dozens of real `answer_chunk` SSE events, and one correctly-deduplicated `final` event with clean per-product citation snippets. See [04](designs/04-inference-serving.md#implementation-notes) for the full reasoning.

## Infrastructure (`infra/`)

**Resolved: AWS CDK (Python)** — one language across app code and infra, rather than adding Terraform/HCL as a second one.

**Resolved: no Neptune.** A real deploy attempt (2026, AWS account 953146692069) failed with `CREATE_FAILED` on `AWS::Neptune::DBCluster` — *"The specified cluster engine type is not available with free plan accounts. Available engine types: [aurora-postgresql]"*. This account's plan doesn't support Neptune at all, not just a cost concern. The `RagEcommerce-Graph` stack (Neptune + its VPC) was removed; graph edges now live in a `GraphEdges` table in `RagEcommerce-Data` instead. See [01](designs/01-ingestion-pipeline.md#graph-storage-dynamodb-not-neptune).

**Deployed for real** (AWS credentials confirmed working, account 953146692069, us-east-2). This started real billing, deliberately, once the deploy was confirmed.

| Stack | Resources | Status |
| --- | --- | --- |
| `RagEcommerce-Data` | S3 bucket, DynamoDB Products (5,000 items) + Reviews (10,513 items) + GraphEdges (19,476 items) tables | `CREATE_COMPLETE`, loaded |
| `RagEcommerce-Search` | Single-node OpenSearch domain (t3.small.search), 1 node | `CREATE_COMPLETE`, `chunks` index (20,539 docs) + `product_images` index (5,000 docs) |
| `RagEcommerce-Glue` | `rag-ecommerce-load-dataset` + `-load-delta-reviews` + `-chunk-and-summarize` + `-embed-chunks` + `-load-opensearch` + `-build-graph-edges` + `-embed-images` + `-load-images` Glue Python Shell jobs (1 DPU each), shared IAM role, S3 script/module assets | `CREATE_COMPLETE`, all eight job runs `SUCCEEDED` |
| `RagEcommerce-Refresh` | Step Functions state machine (`rag-ecommerce-daily-refresh`) + EventBridge daily schedule | `CREATE_COMPLETE`, first (automatic) execution `SUCCEEDED` in ~6 min |
| `RagEcommerce-Agents` | LookupAgent + SearchAgent + GraphAgent + ImageAgent + Router (Bedrock AgentCore Runtime, ARM64 containers, CDK-built/pushed images) | `CREATE_COMPLETE`, all five runtimes `READY`, each verified via real invocations |

Not deployed: nothing else — `RagEcommerce-Graph` (Neptune) was deleted, see above.

Every resource uses `RemovalPolicy.DESTROY` so `cdk destroy` fully tears the stack down between work sessions, per the cost discipline in [05](designs/05-observability-cost.md).

## Open decisions (not yet resolved)

- Whether the demo needs to be always-live or can be brought up on demand (affects whether OpenSearch stays provisioned-and-idle or gets torn down between sessions — see [05](designs/05-observability-cost.md))

## Follow-ups tracked for later

- **Citation grounding rate**: [Experiment 03](experiments/03-chunking-retrieval-validation.md) and [Experiment 04](experiments/04-indexing-strategy.md) validated chunking and hybrid combination against real precision@k / hit-rate@k, but grounding rate needs an actual answer-generation step with citations — revisit once workflow [02](designs/02-retrieval-agents.md)'s retrieval agents exist
- **Chunk granularity ("small-to-big")** ([02](designs/02-retrieval-agents.md#indexing-strategy)): still an open, reasoned-but-unvalidated decision — like grounding rate, needs the generator/verifier step to measure meaningfully, not just retrieval-only metrics

## Suggested build order

1. Ingestion & Refresh Pipeline — nothing else has data to work with until this exists
2. Retrieval Agents & Query Orchestration — the core of the product
3. Inference Serving details (retry/timeout/streaming) — harden alongside #2
4. Image Upload & Multimodal Query
5. Observability & Cost Controls — instrument as each piece above lands, not after the fact
6. Frontend Hosting — last, once there's an API worth putting a UI in front of
