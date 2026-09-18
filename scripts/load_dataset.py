"""Streams a subset of the Amazon Reviews 2023 "All_Beauty" category from
Hugging Face and loads it into the deployed DynamoDB tables.

Streams rather than downloads the full ~540MB category files - stops
reading metadata once `--limit` products are collected, and only keeps
reviews for those products.

Simplifications, given this is loading a fixed subset for a portfolio
project, not a general-purpose importer:
- price defaults to 0.0 when the source has none (common in this dataset)
- image URLs are kept as Amazon's own CDN links, not mirrored into our S3
  bucket yet - see docs/designs/03-image-upload.md for the upload path,
  which is for user-uploaded query images, not catalog images
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

import boto3
from huggingface_hub import hf_hub_url

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dataset_source import DATASET_REPO, META_FILE, REVIEWS_FILE, stream_jsonl  # noqa: E402
from ingestion.dynamo_writer import upsert_product, upsert_review  # noqa: E402
from ingestion.models import Product, Review  # noqa: E402

REGION = "us-east-2"


def build_product(meta: dict) -> Product | None:
    title = meta.get("title", "").strip()
    parent_asin = meta.get("parent_asin", "").strip()
    if not title or not parent_asin:
        return None

    description = " ".join(meta.get("description") or []).strip()
    category_path = meta.get("categories") or [meta.get("main_category", "Uncategorized")]

    return Product(
        product_id=parent_asin,
        title=title,
        category=category_path[-1],
        category_path=category_path,
        price=float(meta["price"]) if meta.get("price") is not None else 0.0,
        avg_rating=float(meta.get("average_rating", 0.0)),
        review_count=int(meta.get("rating_number", 0)),
        description=description or title,
        image_keys=[img["large"] for img in meta.get("images") or [] if img.get("large")],
        brand=meta.get("store") or (meta.get("details") or {}).get("Brand"),
    )


def build_review(review: dict) -> Review:
    return Review(
        review_id=f"{review['timestamp']}#{review['user_id']}",
        product_id=review["parent_asin"],
        rating=float(review.get("rating", 0.0)),
        text=(review.get("title", "") + ". " + review.get("text", "")).strip(". "),
        timestamp=int(review["timestamp"]) // 1000,
    )


def resolve_table_names() -> dict[str, str]:
    cfn = boto3.client("cloudformation", region_name=REGION)
    outputs = cfn.describe_stacks(StackName="RagEcommerce-Data")["Stacks"][0]["Outputs"]
    values = {o["OutputKey"]: o["OutputValue"] for o in outputs}
    return {
        "products": values["ProductsTableName"],
        "reviews": values["ReviewsTableName"],
    }


def main(limit: int, max_reviews_per_product: int) -> None:
    table_names = resolve_table_names()
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    products_table = dynamodb.Table(table_names["products"])
    reviews_table = dynamodb.Table(table_names["reviews"])

    print(f"Streaming metadata, collecting up to {limit} products...")
    meta_url = hf_hub_url(DATASET_REPO, META_FILE, repo_type="dataset")
    products: dict[str, Product] = {}
    for raw in stream_jsonl(meta_url):
        product = build_product(raw)
        if product is None:
            continue
        products[product.product_id] = product
        if len(products) >= limit:
            break

    print(f"Writing {len(products)} products to {table_names['products']}...")
    for product in products.values():
        upsert_product(products_table, product)

    print(f"Streaming reviews for those {len(products)} products (max {max_reviews_per_product} each)...")
    reviews_url = hf_hub_url(DATASET_REPO, REVIEWS_FILE, repo_type="dataset")
    review_counts: Counter[str] = Counter()
    written = 0
    for raw in stream_jsonl(reviews_url):
        product_id = raw.get("parent_asin")
        if product_id not in products or review_counts[product_id] >= max_reviews_per_product:
            continue
        upsert_review(reviews_table, build_review(raw))
        review_counts[product_id] += 1
        written += 1
        if sum(review_counts.values()) >= len(products) * max_reviews_per_product:
            break

    print(f"Wrote {written} reviews to {table_names['reviews']}.")
    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=5000, help="Number of products to load")
    parser.add_argument("--max-reviews-per-product", type=int, default=3)
    args = parser.parse_args()
    main(args.limit, args.max_reviews_per_product)
