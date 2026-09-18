"""Glue Python Shell job: normalizes the Amazon Reviews 2023 dataset and
writes products/reviews to DynamoDB. Stage 2 (partial - chunking is a
separate job) of docs/designs/01-ingestion-pipeline.md.

`dataset_source` and `ingestion` are provided via --extra-py-files (a zip)
- see infra/stacks/glue_stack.py. Glue's Python Shell downloads that zip
into a /tmp/glue-python-libs-*/ directory and puts the *directory* on
sys.path, not the zip file itself, so it never actually becomes
importable without this: find it and add the zip itself to sys.path.
"""

import glob
import sys
from collections import Counter

import boto3
from awsglue.utils import getResolvedOptions

for zip_path in glob.glob("/tmp/glue-python-libs-*/*.zip"):
    sys.path.insert(0, zip_path)

from dataset_source import stream_metadata, stream_reviews, parse_product, parse_review
from ingestion.dynamo_writer import upsert_product, upsert_review
from ingestion.models import Product, Review

REGION = "us-east-2"

args = getResolvedOptions(
    sys.argv, ["products_table", "reviews_table", "limit", "max_reviews_per_product"]
)


def main() -> None:
    limit = int(args["limit"])
    max_reviews_per_product = int(args["max_reviews_per_product"])

    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    products_table = dynamodb.Table(args["products_table"])
    reviews_table = dynamodb.Table(args["reviews_table"])

    print(f"Streaming metadata, collecting up to {limit} products...")
    products: dict[str, Product] = {}
    for raw in stream_metadata():
        parsed = parse_product(raw)
        if parsed is None:
            continue
        product = Product(*parsed)
        products[product.product_id] = product
        if len(products) >= limit:
            break

    print(f"Writing {len(products)} products to {args['products_table']}...")
    for product in products.values():
        upsert_product(products_table, product)

    print(f"Streaming reviews for those {len(products)} products (max {max_reviews_per_product} each)...")
    review_counts: Counter[str] = Counter()
    written = 0
    for raw in stream_reviews():
        review = Review(*parse_review(raw))
        if review.product_id not in products or review_counts[review.product_id] >= max_reviews_per_product:
            continue
        upsert_review(reviews_table, review)
        review_counts[review.product_id] += 1
        written += 1
        if sum(review_counts.values()) >= len(products) * max_reviews_per_product:
            break

    print(f"Wrote {written} reviews to {args['reviews_table']}.")
    print("Done.")


if __name__ == "__main__":
    main()
