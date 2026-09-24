import boto3
import pytest
from moto import mock_aws

from agents.graph_tools import find_related_products
from ingestion.graph_edges import brand_edges, category_edges, co_reviewed_edges
from ingestion.graph_writer import upsert_edge
from ingestion.models import Product, Review


@pytest.fixture
def graph_edges_table():
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-2")
        table = dynamodb.create_table(
            TableName="GraphEdges",
            KeySchema=[
                {"AttributeName": "node", "KeyType": "HASH"},
                {"AttributeName": "edge", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "node", "AttributeType": "S"},
                {"AttributeName": "edge", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        products = [
            Product(product_id="P1", title="A", category="Electronics", category_path=["Electronics"], price=10, avg_rating=4, review_count=1, description="d", brand="Acme"),
            Product(product_id="P2", title="B", category="Electronics", category_path=["Electronics"], price=10, avg_rating=4, review_count=1, description="d", brand="Acme"),
            Product(product_id="P3", title="C", category="Home", category_path=["Home"], price=10, avg_rating=4, review_count=1, description="d", brand="Other"),
        ]
        for product in products:
            for node, edge in category_edges(product) + brand_edges(product):
                upsert_edge(table, node, edge)

        reviews = [
            Review(review_id="1700000000#user-A", product_id="P1", rating=5, text="", timestamp=1700000000),
            Review(review_id="1700000001#user-A", product_id="P3", rating=4, text="", timestamp=1700000001),
        ]
        for node, edge in co_reviewed_edges(reviews):
            upsert_edge(table, node, edge)

        yield table


def test_same_brand_is_a_two_hop_lookup_excluding_self(graph_edges_table):
    results = find_related_products(graph_edges_table, "P1", "same_brand")
    assert results == [{"product_id": "P2", "relation": "same_brand"}]


def test_same_category_excludes_products_in_other_categories(graph_edges_table):
    results = find_related_products(graph_edges_table, "P1", "same_category")
    assert results == [{"product_id": "P2", "relation": "same_category"}]

    results = find_related_products(graph_edges_table, "P3", "same_category")
    assert results == []


def test_co_reviewed_is_a_one_hop_lookup(graph_edges_table):
    results = find_related_products(graph_edges_table, "P1", "co_reviewed")
    assert results == [{"product_id": "P3", "relation": "co_reviewed"}]


def test_no_relations_returns_empty_list_not_error(graph_edges_table):
    assert find_related_products(graph_edges_table, "P2", "co_reviewed") == []


def test_missing_or_invalid_inputs_return_an_error_entry(graph_edges_table):
    assert find_related_products(graph_edges_table, None, "co_reviewed") == [
        {"error": "product_id and a valid relation (co_reviewed, same_brand, same_category) are required"}
    ]
    assert find_related_products(graph_edges_table, "P1", "bought_together") == [
        {"error": "product_id and a valid relation (co_reviewed, same_brand, same_category) are required"}
    ]
