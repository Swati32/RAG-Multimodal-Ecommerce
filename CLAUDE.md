# CLAUDE.md

## Implementation Fidelity
- Follow the design docs (docs/designs/) as written - pipeline stages specced as Glue jobs must be implemented as real AWS Glue jobs, not local scripts standing in for them
- If an implementation must deviate from what a design doc says (scope cut, different approach, skipped step), say so explicitly rather than silently substituting - flag the deviation and why, don't let docs and reality drift apart quietly

## Environment
- AWS auth uses `aws login` (session-based, expires ~hourly) - reauthenticate with `aws login`, not `aws configure`
- `cd infra && npx cdk <synth|deploy|destroy>` - CDK CLI is a local npm devDependency, not global; `cdk.json` points the app at `../.venv/bin/python3` directly, so the venv doesn't need activating first
- Bedrock Claude models need `us.<model-id>` inference-profile IDs for `invoke_model` (e.g. `us.anthropic.claude-haiku-4-5-20251001-v1:0`), not bare model IDs
- This AWS account (953146692069) doesn't support Amazon Neptune (plan restriction, `CREATE_FAILED`) - graph storage is a DynamoDB adjacency-list table instead, see docs/designs/01-ingestion-pipeline.md
- `aws dynamodb describe-table`'s `ItemCount` is stale (~6h refresh) - use `aws dynamodb scan --select COUNT` for a live count
- Glue Python Shell only supports Python 2, 3, or exactly 3.9 (`^([2-3]|3[.]9)$`, verified via `aws glue create-job` validation) - no 3.10+, so no `X | None` union syntax in any module a Glue job imports; use `from __future__ import annotations` instead of rewriting types
- Glue Python Shell's `--extra-py-files` zip lands in `/tmp/glue-python-libs-*/` with that *directory* on `sys.path`, not the zip itself - a package inside it won't import until the script manually adds the zip file (not just its folder) to `sys.path` (see infra/glue_scripts/load_dataset.py)
- Glue Python Shell jobs don't auto-inject `--JOB_NAME` the way Spark ETL jobs do - don't request it via `getResolvedOptions` unless actually used
- Glue Python Shell bundles a 2022-era boto3/botocore that predates Bedrock's service model entirely (`UnknownServiceError: Unknown service: 'bedrock-runtime'`) - force a current one via `--additional-python-modules boto3>=1.34`
- Bedrock Batch inference (`create-model-invocation-job`) is blocked account-wide here, not per-model - confirmed via a real submission attempt with Claude: `"Your account is not authorized to perform this action. Please create a support case..."`. Needs an AWS support case to lift; use real-time calls instead (rate-limited, not just worker-count-capped - see infra/glue_scripts/embed_chunks.py)
- Titan Text Embeddings V2 on-demand quota: 600 requests/min, 300,000 tokens/min (`aws service-quotas list-service-quotas --service-code bedrock`) - check quotas before parallelizing Bedrock calls, don't guess a safe concurrency
- Glue Python Shell bundles opensearch-py 1.1.0, which predates `AWSV4SignerAuth` - force a current one via `--additional-python-modules opensearch-py>=2.4` (same category as the boto3 gotcha above)
- `opensearchpy.helpers.bulk` defaults to `chunk_size=500` and `max_retries=0` - a single-node t3.small.search domain returns `TransportError(429, 'Too Many Requests')` against that with no retry; use a smaller `chunk_size` (e.g. 100) and `max_retries>0`

## Coding Guidelines
- Clean code: direct and readable over clever; no speculative abstractions or unused flexibility
- Modular: small, single-purpose functions/modules that compose (see src/ingestion/ - models, chunking, summarization, dynamo_writer, opensearch_documents are each separate)
- Reuse existing code/modules before writing new logic that duplicates it
- No excessive logging - no debug/trace prints left in committed code; structured logs only at meaningful operational boundaries
- No excessive comments - only where the *why* isn't obvious from the code itself (a constraint, a workaround, a non-obvious invariant), never restating what the code does

## Testing
- `pip install -e ".[dev]"` then `pytest` - no AWS credentials needed; DynamoDB is mocked via `moto`, Claude/Bedrock calls via injected stub clients

## Documentation
- Any doc reporting eval results (README or docs/) must define each metric inline (precision@k, grounding rate, etc.), not just name it
- Mermaid flowcharts: always `%%{init: {'flowchart': {'curve': 'linear'}}}%%` for straight-line edges, never the default curved splines
