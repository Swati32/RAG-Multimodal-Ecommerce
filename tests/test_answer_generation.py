import boto3
import pytest
from moto import mock_aws

from agents.answer_generation import extract_citations, resolve_citations, stream_answer, verify_citations
from ingestion.dynamo_writer import upsert_product
from ingestion.models import Product


class StubBedrockClient:
    def __init__(self, texts: list[str] | None = None, stream_chunks: list[str] | None = None):
        self._texts = list(texts or [])
        self._stream_chunks = list(stream_chunks or [])
        self.calls = 0
        self.stream_calls: list[dict] = []

    def converse(self, **kwargs):
        self.calls += 1
        return {"output": {"message": {"content": [{"text": self._texts.pop(0)}]}}}

    def converse_stream(self, **kwargs):
        self.calls += 1
        self.stream_calls.append(kwargs)
        return {"stream": [{"contentBlockDelta": {"delta": {"text": chunk}}} for chunk in self._stream_chunks]}


def test_stream_answer_yields_text_deltas_as_they_arrive():
    bedrock = StubBedrockClient(stream_chunks=["It has ", "great battery life. [[P1]]"])

    chunks = list(stream_answer(bedrock, "model", "How's the battery?", [{"product_id": "P1", "text": "Battery lasts all day."}]))

    assert chunks == ["It has ", "great battery life. [[P1]]"]


def test_stream_answer_attaches_an_image_when_given():
    """Real bug, caught live: generation was text-only even for an
    image-driven query, so Claude refused to answer at all ("I'm unable to
    see or process images directly") despite already having the matched
    records to write from - see docs/designs/03-image-upload.md."""
    bedrock = StubBedrockClient(stream_chunks=["Similar item. [[P1]]"])

    list(stream_answer(bedrock, "model", "find similar products", [{"product_id": "P1"}], image_bytes=b"fake-jpeg-bytes"))

    content = bedrock.stream_calls[0]["messages"][0]["content"]
    assert content[0] == {"image": {"format": "jpeg", "source": {"bytes": b"fake-jpeg-bytes"}}}


def test_extract_citations_parses_marker_and_strips_it_from_the_answer():
    answer, citations = extract_citations("It has great battery life. [[P1]] It also looks nice.", [])

    assert answer == "It has great battery life. It also looks nice."
    assert citations == [{"product_id": "P1", "snippet": "It has great battery life."}]


def test_extract_citations_returns_the_answer_unchanged_when_there_are_no_markers():
    answer, citations = extract_citations("Nothing in the records answers that.", [])

    assert answer == "Nothing in the records answers that."
    assert citations == []


def test_extract_citations_handles_multiple_markers():
    answer, citations = extract_citations("Great sound. [[P1]] Long battery. [[P2]]", [])

    assert answer == "Great sound. Long battery."
    assert citations == [
        {"product_id": "P1", "snippet": "Great sound."},
        {"product_id": "P2", "snippet": "Long battery."},
    ]


def test_verify_citations_drops_ungrounded_ones():
    bedrock = StubBedrockClient(['{"verdicts": [{"product_id": "P1", "grounded": true}, {"product_id": "P2", "grounded": false}]}'])
    citations = [{"product_id": "P1", "snippet": "real claim"}, {"product_id": "P2", "snippet": "invented claim"}]
    results = [{"product_id": "P1", "text": "real claim here"}, {"product_id": "P2", "text": "unrelated content"}]

    verified = verify_citations(bedrock, "model", citations, results)

    assert verified == [{"product_id": "P1", "snippet": "real claim"}]


def test_verify_citations_skips_the_call_entirely_when_there_are_no_citations():
    bedrock = StubBedrockClient([])  # would raise IndexError if called
    assert verify_citations(bedrock, "model", [], []) == []


@pytest.fixture
def products_table():
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-2")
        table = dynamodb.create_table(
            TableName="Products",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        upsert_product(
            table,
            Product(
                product_id="P1",
                title="Wireless Headphones",
                category="Electronics",
                category_path=["Electronics"],
                price=49.99,
                avg_rating=4.5,
                review_count=1,
                description="Great sound.",
                image_keys=["https://example.com/p1.jpg"],
                brand="Acme",
            ),
        )
        yield table


def test_resolve_citations_attaches_product_fields(products_table):
    resolved = resolve_citations(products_table, [{"product_id": "P1", "snippet": "lasts all day"}])

    assert resolved == [
        {
            "product_id": "P1",
            "title": "Wireless Headphones",
            "image_url": "https://example.com/p1.jpg",
            "product_url": "/products/P1",
            "snippet": "lasts all day",
        }
    ]


def test_resolve_citations_skips_ids_with_no_matching_product(products_table):
    assert resolve_citations(products_table, [{"product_id": "MISSING", "snippet": "x"}]) == []
