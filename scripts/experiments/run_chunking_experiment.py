"""Compares chunking strategies on a sample of real long reviews from the
Amazon Reviews 2023 dataset. No LLM calls - deterministic metrics only.
Results feed docs/experiments/01-chunking-strategy.md.
"""

import json
import statistics

import requests
from huggingface_hub import hf_hub_url

from chunking_strategies import sentence_aware_split, word_count_split

DATASET_REPO = "McAuley-Lab/Amazon-Reviews-2023"
REVIEWS_FILE = "raw/review_categories/All_Beauty.jsonl"
SAMPLE_SIZE = 40
MIN_WORDS = 150  # only long reviews are interesting for a chunking comparison


def fetch_long_review_sample(n: int, min_words: int) -> list[str]:
    url = hf_hub_url(DATASET_REPO, REVIEWS_FILE, repo_type="dataset")
    sample = []
    with requests.get(url, stream=True) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            text = json.loads(line).get("text", "")
            if len(text.split()) >= min_words:
                sample.append(text)
            if len(sample) >= n:
                break
    return sample


def ends_cleanly(chunk: str) -> bool:
    return chunk.rstrip().endswith((".", "!", "?"))


def score_strategy(name: str, chunks_per_text: list[list[str]]) -> dict:
    chunk_counts = [len(chunks) for chunks in chunks_per_text]
    all_chunks = [c for chunks in chunks_per_text for c in chunks]
    word_counts = [len(c.split()) for c in all_chunks]
    clean_boundaries = sum(1 for c in all_chunks if ends_cleanly(c))

    return {
        "strategy": name,
        "avg_chunks_per_text": round(statistics.mean(chunk_counts), 2),
        "avg_words_per_chunk": round(statistics.mean(word_counts), 1),
        "stdev_words_per_chunk": round(statistics.stdev(word_counts), 1) if len(word_counts) > 1 else 0.0,
        "pct_clean_sentence_boundary": round(100 * clean_boundaries / len(all_chunks), 1),
    }


def main() -> None:
    print(f"Fetching {SAMPLE_SIZE} reviews with >= {MIN_WORDS} words...")
    sample = fetch_long_review_sample(SAMPLE_SIZE, MIN_WORDS)
    print(f"Got {len(sample)} reviews.\n")

    strategies = {
        "word_count(300w/50 overlap) - current default": lambda t: word_count_split(t, 300, 50),
        "word_count(150w/30 overlap)": lambda t: word_count_split(t, 150, 30),
        "sentence_aware(300w budget)": lambda t: sentence_aware_split(t, 300),
    }

    results = []
    for name, fn in strategies.items():
        chunks_per_text = [fn(text) for text in sample]
        results.append(score_strategy(name, chunks_per_text))

    header = f"{'strategy':<45} {'chunks/text':>12} {'words/chunk':>12} {'stdev':>8} {'%clean end':>11}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['strategy']:<45} {r['avg_chunks_per_text']:>12} "
            f"{r['avg_words_per_chunk']:>12} {r['stdev_words_per_chunk']:>8} "
            f"{r['pct_clean_sentence_boundary']:>10}%"
        )


if __name__ == "__main__":
    main()
