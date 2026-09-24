import boto3
import pytest
from moto import mock_aws

from agents.answer_generation import generate_answer, resolve_citations, verify_citations
from ingestion.dynamo_writer import upsert_product
from ingestion.models import Product


class StubBedrockClient:
    def __init__(self, texts: list[str]):
        self._texts = list(texts)
        self.calls = 0

    def converse(self, **kwargs):
        self.calls += 1
        return {"output": {"message": {"content": [{"text": self._texts.pop(0)}]}}}


def test_generate_answer_parses_answer_and_citations():
    bedrock = StubBedrockClient(['{"answer": "It has great battery life.", "citations": [{"product_id": "P1", "snippet": "lasts all day"}]}'])

    result = generate_answer(bedrock, "model", "How's the battery?", [{"product_id": "P1", "text": "Battery lasts all day."}])

    assert result == {"answer": "It has great battery life.", "citations": [{"product_id": "P1", "snippet": "lasts all day"}]}


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
