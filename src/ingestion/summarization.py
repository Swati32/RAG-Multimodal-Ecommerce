import json
from typing import Protocol

import boto3

LONG_REVIEW_WORD_THRESHOLD = 300

SUMMARIZE_PROMPT = (
    "Summarize this product review in under 100 words, keeping every concrete "
    "claim about the product (features, defects, comparisons). Do not add "
    "opinions not present in the review.\n\nReview:\n{review_text}"
)


class ClaudeClient(Protocol):
    def summarize(self, prompt: str) -> str: ...


def summarize_review_if_long(
    text: str, client: ClaudeClient, threshold: int = LONG_REVIEW_WORD_THRESHOLD
) -> str:
    if len(text.split()) <= threshold:
        return text
    return client.summarize(SUMMARIZE_PROMPT.format(review_text=text))


class BedrockClaudeClient:
    """Direct LLM call for ingestion-time summarization — see design doc 02,
    "Agents vs Direct LLM Calls": a fixed single-step task needs no tool loop.
    """

    def __init__(self, model_id: str, region: str = "us-east-2", max_tokens: int = 200):
        self._runtime = boto3.client("bedrock-runtime", region_name=region)
        self._model_id = model_id
        self._max_tokens = max_tokens

    def summarize(self, prompt: str) -> str:
        response = self._runtime.invoke_model(
            modelId=self._model_id,
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": self._max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                }
            ),
        )
        payload = json.loads(response["body"].read())
        return payload["content"][0]["text"]
