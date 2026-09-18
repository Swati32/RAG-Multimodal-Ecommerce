import boto3
import pytest
from moto import mock_aws

from ingestion.dynamo_reader import product_from_item, review_from_item, scan_all_items
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


def test_product_from_item_round_trips(products_table):
    product = Product(
        product_id="P1",
        title="Wireless Headphones",
        category="Electronics",
        category_path=["Electronics", "Audio"],
        price=49.99,
        avg_rating=4.5,
        review_count=120,
        description="Great sound.",
        brand="Acme",
    )
    upsert_product(products_table, product)

    item = products_table.get_item(Key={"product_id": "P1"})["Item"]
    restored = product_from_item(item)

    assert restored == product


def test_review_from_item_round_trips(reviews_table):
    review = Review(review_id="R1", product_id="P1", rating=5, text="Loved it.", timestamp=1700000000)
    upsert_review(reviews_table, review)

    item = reviews_table.get_item(Key={"product_id": "P1", "review_id": "R1"})["Item"]
    restored = review_from_item(item)

    assert restored == review


def test_scan_all_items_paginates(products_table):
    for i in range(5):
        upsert_product(
            products_table,
            Product(
                product_id=f"P{i}",
                title=f"Product {i}",
                category="Electronics",
                category_path=["Electronics"],
                price=10.0,
                avg_rating=4.0,
                review_count=1,
                description="desc",
            ),
        )

    items = list(scan_all_items(products_table))

    assert {item["product_id"] for item in items} == {f"P{i}" for i in range(5)}
