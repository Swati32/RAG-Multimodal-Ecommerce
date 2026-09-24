"""Glue Python Shell job: finds the next batch of genuinely new reviews
(for products already in the catalog, not yet ingested) and upserts them
into DynamoDB, writing the same batch to S3 for the delta chunking stage.
Part of the daily refresh cadence - see docs/designs/01-ingestion-
pipeline.md, "Simulating refresh cadence": a delta run processes only
what's new, not a full reprocessing pass.

Re-streams the dataset from scratch each run (no persisted cursor) and
skips anything already loaded - simpler than tracking an offset, and the
dataset is small enough that a full stream-with-early-break stays fast.

`dataset_source`/`ingestion` provided via --extra-py-files (a zip) - see
the sys.path workaround note in load_dataset.py.
"""

import glob
import json
import sys
from collections import defaultdict

import boto3
from awsglue.utils import getResolvedOptions

for zip_path in glob.glob("/tmp/glue-python-libs-*/*.zip"):
    sys.path.insert(0, zip_path)

from dataset_source import parse_review, stream_reviews
from ingestion.delta_reviews import select_new_reviews
from ingestion.dynamo_reader import scan_all_items
from ingestion.dynamo_writer import upsert_review
from ingestion.models import Review

REGION = "us-east-2"

args = getResolvedOptions(
    sys.argv,
    ["products_table", "reviews_table", "output_bucket", "output_key", "max_new_reviews", "max_new_reviews_per_product"],
)


def main() -> None:
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    products_table = dynamodb.Table(args["products_table"])
    reviews_table = dynamodb.Table(args["reviews_table"])

    print("Loading known product ids and already-ingested review ids...")
    known_product_ids = {item["product_id"] for item in scan_all_items(products_table)}
    loaded_review_ids_by_product = defaultdict(set)
    for item in scan_all_items(reviews_table):
        loaded_review_ids_by_product[item["product_id"]].add(item["review_id"])
    already_loaded = sum(len(v) for v in loaded_review_ids_by_product.values())
    print(f"{len(known_product_ids)} known products, {already_loaded} reviews already loaded.")

    print("Streaming the dataset for reviews not yet loaded...")
    all_reviews = (Review(*parse_review(raw)) for raw in stream_reviews())
    new_reviews = select_new_reviews(
        all_reviews,
        known_product_ids,
        loaded_review_ids_by_product,
        max_total=int(args["max_new_reviews"]),
        max_per_product=int(args["max_new_reviews_per_product"]),
    )
    print(f"Found {len(new_reviews)} new reviews.")

    for review in new_reviews:
        upsert_review(reviews_table, review)

    records = [
        {"review_id": r.review_id, "product_id": r.product_id, "rating": r.rating, "text": r.text, "timestamp": r.timestamp}
        for r in new_reviews
    ]
    body = "\n".join(json.dumps(r) for r in records)
    boto3.client("s3", region_name=REGION).put_object(Bucket=args["output_bucket"], Key=args["output_key"], Body=body.encode("utf-8"))
    print(f"Wrote {len(records)} new review records to s3://{args['output_bucket']}/{args['output_key']}")


if __name__ == "__main__":
    main()
