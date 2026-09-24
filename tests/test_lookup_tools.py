import boto3
import pytest
from moto import mock_aws

from agents.lookup_tools import get_product_response, get_review_response
from ingestion.dynamo_writer import upsert_product, upsert_review
from ingestion.models import Product, Review


@pytest.fixture
def dynamodb():
    with mock_aws():
        yield boto3.resource("dynamodb", region_name="us-east-2")


@pytest.fixture
def products_table(dynamodb):
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
            brand="Acme",
        ),
    )
    return table


@pytest.fixture
def reviews_table(dynamodb):
    table = dynamodb.create_table(
        TableName="Reviews",
        KeySchema=[
            {"AttributeName": "product_id", "KeyType": "HASH"},
            {"AttributeName": "review_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "product_id", "AttributeType": "S"},
            {"AttributeName": "review_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    upsert_review(table, Review(review_id="R1", product_id="P1", rating=5, text="Loved it.", timestamp=1700000000))
    return table


def test_get_product_response_found(products_table):
    result = get_product_response(products_table, "P1")
    assert result["product_id"] == "P1"
    assert result["title"] == "Wireless Headphones"


def test_get_product_response_not_found(products_table):
    assert get_product_response(products_table, "MISSING") == {"error": "No product found with product_id=MISSING"}


def test_get_product_response_missing_id(products_table):
    assert get_product_response(products_table, None) == {"error": "product_id is required"}


def test_get_review_response_found(reviews_table):
    result = get_review_response(reviews_table, "P1", "R1")
    assert result["review_id"] == "R1"
    assert result["text"] == "Loved it."


def test_get_review_response_requires_both_ids(reviews_table):
    assert get_review_response(reviews_table, "P1", None) == {"error": "both product_id and review_id are required"}
    assert get_review_response(reviews_table, None, "R1") == {"error": "both product_id and review_id are required"}
