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
- Claude via Bedrock ignores "respond with ONLY JSON, no other text" often enough to plan for it - it still wraps output in a ` ```json ` code fence sometimes; strip a markdown fence before `json.loads` on any prompted-JSON response instead of trusting the instruction
- `amazon.titan-embed-image-v1` (Titan Multimodal Embeddings) isn't in `aws bedrock list-foundation-models` on this account in us-east-2 - only `amazon.titan-embed-text-v2:0` and `cohere.embed-v4:0` show up for embeddings; use Cohere Embed v4 (`us.cohere.embed-v4:0` inference profile) for image embeddings instead
- Cohere Embed v4's real request/response shape (verified against a real image, not assumed from docs): request `{"images": ["data:image/jpeg;base64,<...>"], "input_type": "image", "embedding_types": ["float"]}`, response embedding at `embeddings.float[0]` (not a flat `embedding` key like Titan)
- Cohere Embed v4 defaults to 1536-dim output, but OpenSearch's `lucene` k-NN engine caps vector dimension at 1024 - a real `400 mapper_parsing_exception: "Dimension value cannot be greater than 1024 for vector"` on first attempt. Fix: pass `"output_dimension": 1024` in the Cohere request (it's a supported param) rather than switching ANN engines just for one index
- Cohere Embed v4's on-demand quota is 200 requests/min (`aws service-quotas list-service-quotas --service-code bedrock`) - lower than Titan Text V2's 600/min, so rate-limit lower (e.g. 180/min) when parallelizing calls to it
- A CLI subcommand existing (e.g. `aws bedrock-agent create-agent --help` works) does NOT mean the account/service will accept the call - AWS Bedrock Agents (classic) is in maintenance mode and rejects `CreateAgent` for any account with no prior usage (`AccessDeniedException: Bedrock Agents is in Maintenance Mode...`), confirmed only by a real (if minimal/throwaway) API call, not by CLI help text. Always make the real call to check availability, the same discipline as Neptune/Bedrock Batch
- Bedrock AgentCore (`bedrock-agentcore`/`bedrock-agentcore-control`) is NOT a like-for-like replacement for classic Bedrock Agents' multi-agent collaboration - it's a hosting platform for arbitrary agent code (plus gateways/memory/identity/browser/payment features), not a declarative supervisor-decides-who-to-call orchestrator. Router/dispatch logic must be hand-written regardless of which one is used
- AgentCore Runtime requires `platform=LINUX_ARM64` (via `aws_ecr_assets.Platform`) and the `bedrock_agentcore` Python SDK's `BedrockAgentCoreApp` (`@app.entrypoint`, listens on port 8080 - the SDK provides the HTTP/health-check contract, don't hand-roll it)
- `aws bedrock-agentcore invoke-agent-runtime --payload` needs base64-encoded JSON, not raw JSON text (`Invalid base64` otherwise) - `echo -n '{"prompt": "..."}' | base64`
- A `DockerImageAsset`/`AgentRuntimeArtifact.from_asset` build context rooted at the repo root (needed so a Dockerfile can `COPY src/`) will stage `tests/`, `docs/`, etc. into `infra/cdk.out/asset.*/` unless explicitly excluded - besides bloating the build, a leftover staged `tests/` collides with pytest's real `tests/` (`import file mismatch`); exclude `tests`, `docs`, `scripts`, `.venv`, `infra/cdk.out` from the asset, and add `testpaths = ["tests"]` under `[tool.pytest.ini_options]` in pyproject.toml as a permanent guard
- An OpenSearch domain's account-wide resource policy (`es:ESHttp*` to `AccountPrincipal`) is NOT sufficient by itself for a role to call the API - OpenSearch also requires the calling role's own IAM identity policy to explicitly allow the action (a real 403 each time: `"no identity-based policy allows the es:ESHttpPut/ESHttpPost action"` even though the domain's resource policy already allowed the account). CDK's `Domain.grant_read` covers `ESHttpGet`/`ESHttpHead` only - **not** `ESHttpPost`, and opensearch-py sends an ordinary `_search` with a request body as a POST, not a GET, so `grant_read` alone can't even run a plain query, let alone a `PUT` (e.g. creating a search pipeline). In practice, use `grant_read_write` on any role that actually queries OpenSearch, not `grant_read`
- Bedrock Converse API's `toolResult.content[0].json` field must be a JSON **object**, not a bare array - a real `ValidationException: The format of the value at messages.N.content.0.toolResult.content.0.json is invalid` otherwise. A tool that naturally returns a list (e.g. multiple search results) must wrap it (`{"items": [...]}`) before sending it back as a toolResult, even though the same list is fine to use directly in your own app-level response

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
- Every experiment (chunking, prompting, indexing, etc.) gets its own doc under docs/experiments/ - not a summary bolted onto a design doc. Each doc must cover, in order: the question being asked, terminology/metric definitions (plain-English, not just a name), method, results, and the decision with reasoning - explain *why* a metric or method was chosen, not just what the numbers were
- Mermaid flowcharts: always `%%{init: {'flowchart': {'curve': 'linear'}}}%%` for straight-line edges, never the default curved splines
