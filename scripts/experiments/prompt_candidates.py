"""Candidate prompts for summarizing a long review before chunking - see
docs/experiments/02-summarization-prompting.md. Compared via LLM-as-judge
(see run_prompting_experiment.py); blocked until Bedrock model access
clears on this account.
"""

CONCISE_CLAIMS = (
    "Summarize this product review in under 100 words, keeping every concrete "
    "claim about the product (features, defects, comparisons). Do not add "
    "opinions not present in the review.\n\nReview:\n{review_text}"
)

BULLET_EXTRACTION = (
    "Extract the concrete claims from this product review as short bullet "
    "points (one claim per bullet: a feature, defect, or comparison the "
    "reviewer made). Omit generic sentiment with no concrete claim behind it. "
    "Output only the bullets.\n\nReview:\n{review_text}"
)

AGGRESSIVE_COMPRESSION = (
    "Summarize this product review in under 50 words. Prioritize the single "
    "most important concrete claim (the one most useful for someone deciding "
    "whether to buy this product) over completeness.\n\nReview:\n{review_text}"
)

CANDIDATES = {
    "concise_claims (current default)": CONCISE_CLAIMS,
    "bullet_extraction": BULLET_EXTRACTION,
    "aggressive_compression": AGGRESSIVE_COMPRESSION,
}
