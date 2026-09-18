"""Shared access to and parsing of the Amazon Reviews 2023 dataset on
Hugging Face - used by the Glue load job and the chunking/prompting
experiments. Streams rather than downloads the full ~540MB category
files.
"""

import json

import requests
from huggingface_hub import hf_hub_url

DATASET_REPO = "McAuley-Lab/Amazon-Reviews-2023"
META_FILE = "raw/meta_categories/meta_All_Beauty.jsonl"
REVIEWS_FILE = "raw/review_categories/All_Beauty.jsonl"


def stream_jsonl(url: str):
    with requests.get(url, stream=True) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line:
                yield json.loads(line)


def stream_metadata():
    return stream_jsonl(hf_hub_url(DATASET_REPO, META_FILE, repo_type="dataset"))


def stream_reviews():
    return stream_jsonl(hf_hub_url(DATASET_REPO, REVIEWS_FILE, repo_type="dataset"))


def fetch_long_review_sample(n: int, min_words: int) -> list[str]:
    sample = []
    for review in stream_reviews():
        text = review.get("text", "")
        if len(text.split()) >= min_words:
            sample.append(text)
        if len(sample) >= n:
            break
    return sample


def parse_product(meta: dict):
    """Returns a (product_id, title, category, category_path, price,
    avg_rating, review_count, description, image_keys, brand) tuple, or
    None if the record lacks a title/id. Plain tuple, not the Product
    dataclass, so this module has no dependency on src/ingestion - it
    runs standalone in the Glue Python Shell environment.

    Simplifications, given this loads a fixed subset for a portfolio
    project, not a general-purpose importer: price defaults to 0.0 when
    the source has none (common in this dataset); image URLs are kept as
    Amazon's own CDN links, not mirrored into our S3 bucket.
    """
    title = meta.get("title", "").strip()
    parent_asin = meta.get("parent_asin", "").strip()
    if not title or not parent_asin:
        return None

    description = " ".join(meta.get("description") or []).strip()
    category_path = meta.get("categories") or [meta.get("main_category", "Uncategorized")]

    return (
        parent_asin,
        title,
        category_path[-1],
        category_path,
        float(meta["price"]) if meta.get("price") is not None else 0.0,
        float(meta.get("average_rating", 0.0)),
        int(meta.get("rating_number", 0)),
        description or title,
        [img["large"] for img in meta.get("images") or [] if img.get("large")],
        meta.get("store") or (meta.get("details") or {}).get("Brand"),
    )


def parse_review(review: dict):
    """Returns a (review_id, product_id, rating, text, timestamp) tuple."""
    return (
        f"{review['timestamp']}#{review['user_id']}",
        review["parent_asin"],
        float(review.get("rating", 0.0)),
        (review.get("title", "") + ". " + review.get("text", "")).strip(". "),
        int(review["timestamp"]) // 1000,
    )
