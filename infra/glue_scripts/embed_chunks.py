"""Glue Python Shell job: embeds every chunk from processed/chunks.jsonl via
Bedrock Titan, real-time - not Batch, see docs/designs/01-ingestion-pipeline.md,
"Embedding generation: real-time Titan calls, not Bedrock Batch", for why.
Parallelized with a thread pool since ~20k sequential network calls would
risk the Glue job's timeout - but rate-limited to stay under Titan Text
Embeddings V2's on-demand quota (600 requests/min, confirmed via
`aws service-quotas list-service-quotas --service-code bedrock`), which a
worker-count cap alone can't guarantee since actual throughput depends on
per-call latency. Stage 4 (text only - images not yet handled) of
docs/designs/01-ingestion-pipeline.md.

`ingestion` is provided via --extra-py-files (a zip) - see the sys.path
workaround note in load_dataset.py, same Glue Python Shell quirk applies.
"""

import glob
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from awsglue.utils import getResolvedOptions
from botocore.exceptions import ClientError

for zip_path in glob.glob("/tmp/glue-python-libs-*/*.zip"):
    sys.path.insert(0, zip_path)

REGION = "us-east-2"
MAX_WORKERS = 10
MAX_RETRIES = 5
MAX_REQUESTS_PER_MINUTE = 540  # 90% of the 600/min quota, margin for retries/jitter

args = getResolvedOptions(
    sys.argv, ["input_bucket", "input_key", "output_bucket", "output_key", "embedding_model_id"]
)


class RateLimiter:
    """Paces calls to a fixed rate across all threads, rather than relying
    on worker count to indirectly stay under a requests-per-minute quota."""

    def __init__(self, max_per_minute: int):
        self._interval = 60.0 / max_per_minute
        self._lock = threading.Lock()
        self._next_time = time.monotonic()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait_time = max(0.0, self._next_time - now)
            self._next_time = max(now, self._next_time) + self._interval
        if wait_time > 0:
            time.sleep(wait_time)


def embed_with_retry(client, model_id: str, text: str, limiter: RateLimiter) -> list:
    for attempt in range(MAX_RETRIES):
        limiter.wait()
        try:
            response = client.invoke_model(modelId=model_id, body=json.dumps({"inputText": text}))
            return json.loads(response["body"].read())["embedding"]
        except ClientError as e:
            if e.response["Error"]["Code"] != "ThrottlingException" or attempt == MAX_RETRIES - 1:
                raise
            time.sleep(2**attempt + 0.1 * attempt)


def embed_record(client, model_id: str, record: dict, limiter: RateLimiter) -> dict:
    record["embedding"] = embed_with_retry(client, model_id, record["text"], limiter)
    return record


def main() -> None:
    s3 = boto3.client("s3", region_name=REGION)
    body = s3.get_object(Bucket=args["input_bucket"], Key=args["input_key"])["Body"].read().decode("utf-8")
    records = [json.loads(line) for line in body.splitlines() if line]
    print(f"Embedding {len(records)} chunks with {args['embedding_model_id']} ({MAX_WORKERS} workers)...")

    client = boto3.client("bedrock-runtime", region_name=REGION)
    limiter = RateLimiter(MAX_REQUESTS_PER_MINUTE)
    embedded = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(embed_record, client, args["embedding_model_id"], r, limiter) for r in records]
        for i, future in enumerate(as_completed(futures), 1):
            embedded.append(future.result())
            if i % 1000 == 0:
                print(f"{i}/{len(records)} embedded...")

    out_body = "\n".join(json.dumps(r) for r in embedded)
    s3.put_object(Bucket=args["output_bucket"], Key=args["output_key"], Body=out_body.encode("utf-8"))
    print(f"Wrote {len(embedded)} embedded chunk records to s3://{args['output_bucket']}/{args['output_key']}")


if __name__ == "__main__":
    main()
