# Progress

Tracks implementation status per workflow. Update this file as each workflow moves from designed to implemented.

| Workflow | Design | Implementation | Status |
| --- | --- | --- | --- |
| Ingestion & Refresh Pipeline | [01](designs/01-ingestion-pipeline.md) | [src/ingestion](../src/ingestion) | In progress |
| Retrieval Agents & Query Orchestration | [02](designs/02-retrieval-agents.md) | — | Not started |
| Image Upload & Multimodal Query | [03](designs/03-image-upload.md) | — | Not started |
| Inference Serving | [04](designs/04-inference-serving.md) | — | Not started |
| Observability & Cost Controls | [05](designs/05-observability-cost.md) | — | Not started |
| Frontend Hosting | [06](designs/06-frontend-hosting.md) | — | Not started |

## Ingestion & Refresh Pipeline — what's done

Implemented and unit-tested (16 tests, no AWS credentials needed — DynamoDB is moto-mocked, Claude calls are stubbed):
- `src/ingestion/models.py` — `Product`, `Review`, `Chunk`
- `src/ingestion/chunking.py` — sentence-aware description/review splitting, tag-summary generation (see [01](designs/01-ingestion-pipeline.md#chunking-strategy) and [Experiment 01](experiments/01-chunking-strategy.md))
- `src/ingestion/summarization.py` — direct Claude call (via Bedrock) to summarize a long review before chunking, per the "Agents vs Direct LLM Calls" decision in [02](designs/02-retrieval-agents.md); injected as a dependency so `chunk_review` stays testable without live Bedrock access. Prompt wording still the original default — see [Experiment 02](experiments/02-summarization-prompting.md), blocked on Bedrock account setup
- `src/ingestion/dynamo_writer.py` — idempotent product/review upserts
- `src/ingestion/dynamo_reader.py` — the inverse: DynamoDB item → `Product`/`Review`, plus a paginated `scan_all_items`
- `src/ingestion/opensearch_documents.py` — chunk-to-document mapping with the filter fields from [02](designs/02-retrieval-agents.md#opensearch-metadata--filtering), plus the index mapping

**Deployed and loaded (steps 1-2 of the pipeline, done for real, as an actual Glue job)**: `RagEcommerce-Data`, `RagEcommerce-Search`, and `RagEcommerce-Glue` are all live in AWS (account 953146692069, us-east-2) — see Infrastructure below. `rag-ecommerce-load-dataset` (a Glue Python Shell job, `infra/glue_scripts/load_dataset.py`) streams the Amazon Reviews 2023 "All_Beauty" category from Hugging Face (no full download) and writes products/reviews into the real DynamoDB tables — confirmed via a real `start-job-run` (`SUCCEEDED`, 97s) and `scan`: **5,000 products and 10,313 reviews**.

This replaced an earlier local-script version (`scripts/load_dataset.py`, since deleted) that deviated from design doc 01's Glue-job spec without flagging it — corrected per the Implementation Fidelity rule in CLAUDE.md. Getting the real Glue job working surfaced three environment gotchas now recorded in CLAUDE.md: Python Shell only supports Python 3.9 (no `X | None` syntax), `--extra-py-files` zips need manual `sys.path` handling, and Python Shell doesn't auto-inject `--JOB_NAME`.

**Step 3 done for real too**: `rag-ecommerce-chunk-and-summarize` (`infra/glue_scripts/chunk_and_summarize.py`) reads every product/review from DynamoDB, chunks them (sentence-aware, summarizing long reviews via Claude Haiku first), and writes the result to `s3://.../processed/chunks.jsonl`. Run via `start-job-run`, `SUCCEEDED` in 123s: **20,338 chunk records** (5,026 description + 5,000 tag_summary + 10,313 review). New module: `src/ingestion/dynamo_reader.py` (the inverse of `dynamo_writer` — DynamoDB item → `Product`/`Review`, plus a paginated `scan_all_items`).

One more environment gotcha found and recorded in CLAUDE.md: Glue Python Shell bundles a 2022-era boto3/botocore that predates Bedrock's service model (`UnknownServiceError: Unknown service: 'bedrock-runtime'`) — fixed by forcing a current `boto3` via `--additional-python-modules`.

Not yet implemented (steps 4-5 of the pipeline, to be built as Glue jobs too):
- Bedrock Batch embedding manifest builder + invocation
- OpenSearch bulk loader script (real client calls, not just document shaping)
- DynamoDB graph-edge builder (category hierarchy, co-purchase, brand)
- Step Functions state machine wiring the stages together
- Daily EventBridge trigger for delta refreshes

## Infrastructure (`infra/`)

**Resolved: AWS CDK (Python)** — one language across app code and infra, rather than adding Terraform/HCL as a second one.

**Resolved: no Neptune.** A real deploy attempt (2026, AWS account 953146692069) failed with `CREATE_FAILED` on `AWS::Neptune::DBCluster` — *"The specified cluster engine type is not available with free plan accounts. Available engine types: [aurora-postgresql]"*. This account's plan doesn't support Neptune at all, not just a cost concern. The `RagEcommerce-Graph` stack (Neptune + its VPC) was removed; graph edges now live in a `GraphEdges` table in `RagEcommerce-Data` instead. See [01](designs/01-ingestion-pipeline.md#graph-storage-dynamodb-not-neptune).

**Deployed for real** (AWS credentials confirmed working, account 953146692069, us-east-2). This started real billing, deliberately, once the deploy was confirmed.

| Stack | Resources | Status |
| --- | --- | --- |
| `RagEcommerce-Data` | S3 bucket, DynamoDB Products (5,000 items) + Reviews (10,313 items) + GraphEdges (empty) tables | `CREATE_COMPLETE`, loaded |
| `RagEcommerce-Search` | Single-node OpenSearch domain (t3.small.search), 1 node | `CREATE_COMPLETE`, no index created yet |
| `RagEcommerce-Glue` | `rag-ecommerce-load-dataset` + `rag-ecommerce-chunk-and-summarize` Glue Python Shell jobs (1 DPU each), shared IAM role, S3 script/module assets | `CREATE_COMPLETE`, both job runs `SUCCEEDED` |

Not deployed: nothing else — `RagEcommerce-Graph` (Neptune) was deleted, see above.

Every resource uses `RemovalPolicy.DESTROY` so `cdk destroy` fully tears the stack down between work sessions, per the cost discipline in [05](designs/05-observability-cost.md).

## Open decisions (not yet resolved)

- Whether the demo needs to be always-live or can be brought up on demand (affects whether OpenSearch stays provisioned-and-idle or gets torn down between sessions — see [05](designs/05-observability-cost.md))

## Follow-ups tracked for later

- **Re-validate the chunking strategy once retrieval is live** ([experiments/01](experiments/01-chunking-strategy.md#follow-up-revisit-once-retrieval-is-live)): the sentence-aware decision currently rests on structural metrics only, not actual retrieval quality — re-run it through the real comparison harness once OpenSearch indexing + embeddings exist
- **Run the summarization prompting experiment** ([experiments/02](experiments/02-summarization-prompting.md)): blocked on a one-time Anthropic model use-case form for this Bedrock account

## Suggested build order

1. Ingestion & Refresh Pipeline — nothing else has data to work with until this exists
2. Retrieval Agents & Query Orchestration — the core of the product
3. Inference Serving details (retry/timeout/streaming) — harden alongside #2
4. Image Upload & Multimodal Query
5. Observability & Cost Controls — instrument as each piece above lands, not after the fact
6. Frontend Hosting — last, once there's an API worth putting a UI in front of
