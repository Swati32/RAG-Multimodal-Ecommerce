import boto3
import pytest
from moto import mock_aws

from ingestion.graph_writer import upsert_edge


@pytest.fixture
def graph_edges_table():
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-2")
        yield dynamodb.create_table(
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


def test_upsert_edge_is_idempotent(graph_edges_table):
    upsert_edge(graph_edges_table, "product#P1", "HAS_BRAND#brand#Acme")
    upsert_edge(graph_edges_table, "product#P1", "HAS_BRAND#brand#Acme")

    item = graph_edges_table.get_item(Key={"node": "product#P1", "edge": "HAS_BRAND#brand#Acme"})["Item"]
    assert item == {"node": "product#P1", "edge": "HAS_BRAND#brand#Acme"}

    response = graph_edges_table.scan()
    assert response["Count"] == 1
