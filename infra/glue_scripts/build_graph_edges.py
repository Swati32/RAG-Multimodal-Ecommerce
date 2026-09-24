"""Glue Python Shell job: derives DynamoDB adjacency-list graph edges from
products/reviews already loaded. Stage 5 (graph builder) of docs/designs/
01-ingestion-pipeline.md - deviates from the design's planned
CO_PURCHASED_WITH edges, see docs/designs/01-ingestion-pipeline.md
("Graph edges: CO_REVIEWED_WITH, not CO_PURCHASED_WITH") for why.

`ingestion` is provided via --extra-py-files (a zip) - see the sys.path
workaround note in load_dataset.py, same Glue Python Shell quirk applies.
"""

import glob
import sys

import boto3
from awsglue.utils import getResolvedOptions

for zip_path in glob.glob("/tmp/glue-python-libs-*/*.zip"):
    sys.path.insert(0, zip_path)

from ingestion.dynamo_reader import product_from_item, review_from_item, scan_all_items
from ingestion.graph_edges import brand_edges, category_edges, co_reviewed_edges
from ingestion.graph_writer import upsert_edge

REGION = "us-east-2"

args = getResolvedOptions(sys.argv, ["products_table", "reviews_table", "graph_edges_table"])


def main() -> None:
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    products_table = dynamodb.Table(args["products_table"])
    reviews_table = dynamodb.Table(args["reviews_table"])
    graph_table = dynamodb.Table(args["graph_edges_table"])

    print("Loading products and reviews from DynamoDB...")
    products = [product_from_item(item) for item in scan_all_items(products_table)]
    reviews = [review_from_item(item) for item in scan_all_items(reviews_table)]
    print(f"{len(products)} products, {len(reviews)} reviews.")

    edges = []
    for product in products:
        edges.extend(category_edges(product))
        edges.extend(brand_edges(product))
    edges.extend(co_reviewed_edges(reviews))

    print(f"Writing {len(edges)} edges to {args['graph_edges_table']}...")
    for node, edge in edges:
        upsert_edge(graph_table, node, edge)
    print("Done.")


if __name__ == "__main__":
    main()
