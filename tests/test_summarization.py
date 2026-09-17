from ingestion.summarization import summarize_review_if_long


class StubClaudeClient:
    def __init__(self, summary: str):
        self.summary = summary
        self.calls = 0

    def summarize(self, prompt: str) -> str:
        self.calls += 1
        return self.summary


def test_short_text_is_returned_unchanged_without_calling_claude():
    client = StubClaudeClient(summary="unused")

    result = summarize_review_if_long("a short review", client)

    assert result == "a short review"
    assert client.calls == 0


def test_long_text_is_summarized_via_claude():
    client = StubClaudeClient(summary="Concise summary.")
    long_text = " ".join(["word"] * 400)

    result = summarize_review_if_long(long_text, client)

    assert result == "Concise summary."
    assert client.calls == 1
