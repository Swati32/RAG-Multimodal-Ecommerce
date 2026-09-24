"""Glue Python Shell job: chunks products/reviews (sentence-aware, see
docs/experiments/01-chunking-strategy.md), summarizing long reviews via
Claude first (design doc 02, "Agents vs Direct LLM Calls"). Writes the
resulting chunk records as JSONL to S3, ready for the embedding job.
Stage 3 of docs/designs/01-ingestion-pipeline.md.

Two modes, both real usages of this same job:
- Full (default, `--reviews_input_key` empty): scans all products/reviews
  from DynamoDB - the initial catalog load.
- Delta (`--reviews_input_key` set): chunks only the reviews in that S3
  file - written by load_delta_reviews.py for the daily refresh cadence
  (see docs/designs/01-ingestion-pipeline.md, "Simulating refresh
  cadence"). Products are never chunked in delta mode - the catalog itself
  doesn't change, only new reviews arrive.

`ingestion` is provided via --extra-py-files (a zip) - see the sys.path
workaround note in load_dataset.py, same Glue Python Shell quirk applies
here.
"""

import glob
import json
import sys

import boto3
from awsglue.utils import getResolvedOptions

for zip_path in glob.glob("/tmp/glue-python-libs-*/*.zip"):
    sys.path.insert(0, zip_path)

from ingestion.chunking import chunk_product, chunk_review
from ingestion.dynamo_reader import product_from_item, review_from_item, scan_all_items
from ingestion.models import Chunk, Review
from ingestion.summarization import BedrockClaudeClient

REGION = "us-east-2"

args = getResolvedOptions(
    sys.argv,
    ["products_table", "reviews_table", "output_bucket", "output_key", "claude_model_id", "reviews_input_key"],
)


def chunk_to_record(chunk: Chunk) -> dict:
    return {
        "chunk_id": chunk.chunk_id,
        "product_id": chunk.product_id,
        "chunk_type": chunk.chunk_type,
        "text": chunk.text,
        "review_id": chunk.review_id,
    }


def _load_delta_reviews(bucket: str, key: str) -> list[Review]:
    body = boto3.client("s3", region_name=REGION).get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    return [Review(**json.loads(line)) for line in body.splitlines() if line]


def main() -> None:
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    products_table = dynamodb.Table(args["products_table"])
    reviews_table = dynamodb.Table(args["reviews_table"])
    claude_client = BedrockClaudeClient(args["claude_model_id"], region=REGION)

    records = []

    if args["reviews_input_key"]:
        print(f"Delta mode: chunking new reviews from s3://{args['output_bucket']}/{args['reviews_input_key']}...")
        delta_reviews = _load_delta_reviews(args["output_bucket"], args["reviews_input_key"])
        for review in delta_reviews:
            for chunk in chunk_review(review, claude_client=claude_client):
                records.append(chunk_to_record(chunk))
        print(f"{len(records)} chunks from {len(delta_reviews)} new reviews.")
    else:
        print("Full mode: chunking all products and reviews...")
        for item in scan_all_items(products_table):
            for chunk in chunk_product(product_from_item(item)):
                records.append(chunk_to_record(chunk))
        product_chunk_count = len(records)
        print(f"{product_chunk_count} chunks from products.")

        print("Chunking reviews (summarizing long ones via Claude first)...")
        for item in scan_all_items(reviews_table):
            for chunk in chunk_review(review_from_item(item), claude_client=claude_client):
                records.append(chunk_to_record(chunk))
        print(f"{len(records) - product_chunk_count} chunks from reviews. {len(records)} total.")

    body = "\n".join(json.dumps(r) for r in records)
    boto3.client("s3", region_name=REGION).put_object(
        Bucket=args["output_bucket"], Key=args["output_key"], Body=body.encode("utf-8")
    )
    print(f"Wrote {len(records)} chunk records to s3://{args['output_bucket']}/{args['output_key']}")


if __name__ == "__main__":
    main()
