"""Shared access to the Amazon Reviews 2023 dataset on Hugging Face - used
by the dataset loader and the chunking/prompting experiments.
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


def fetch_long_review_sample(n: int, min_words: int) -> list[str]:
    url = hf_hub_url(DATASET_REPO, REVIEWS_FILE, repo_type="dataset")
    sample = []
    for review in stream_jsonl(url):
        text = review.get("text", "")
        if len(text.split()) >= min_words:
            sample.append(text)
        if len(sample) >= n:
            break
    return sample
