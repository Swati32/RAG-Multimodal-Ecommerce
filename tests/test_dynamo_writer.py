from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

from ingestion.dynamo_writer import upsert_product, upsert_review
from ingestion.models import Product, Review


@pytest.fixture
def dynamodb():
    with mock_aws():
        yield boto3.resource("dynamodb", region_name="us-east-2")


@pytest.fixture
def products_table(dynamodb):
    return dynamodb.create_table(
        TableName="Products",
        KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def reviews_table(dynamodb):
    return dynamodb.create_table(
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


def test_upsert_product_writes_and_overwrites(products_table):
    product = Product(
        product_id="P1",
        title="Wireless Headphones",
        category="Electronics",
        category_path=["Electronics"],
        price=49.99,
        avg_rating=4.5,
        review_count=120,
        description="Great sound.",
        brand="Acme",
    )

    upsert_product(products_table, product)
    item = products_table.get_item(Key={"product_id": "P1"})["Item"]
    assert item["title"] == "Wireless Headphones"
    assert item["price"] == Decimal("49.99")

    product.price = 39.99
    upsert_product(products_table, product)
    item = products_table.get_item(Key={"product_id": "P1"})["Item"]
    assert item["price"] == Decimal("39.99")


def test_upsert_review_writes_expected_fields(reviews_table):
    review = Review(review_id="R1", product_id="P1", rating=5, text="Loved it.", timestamp=1700000000)

    upsert_review(reviews_table, review)

    item = reviews_table.get_item(Key={"product_id": "P1", "review_id": "R1"})["Item"]
    assert item["rating"] == Decimal("5")
    assert item["timestamp"] == 1700000000
