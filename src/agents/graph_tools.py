"""GraphAgent's tool implementation - DynamoDB adjacency-list traversal
over GraphEdges. See docs/designs/02-retrieval-agents.md, GraphAgent, and
docs/designs/01-ingestion-pipeline.md for the edge types this reads
(BELONGS_TO/HAS_BRAND/HAS_PRODUCT/CO_REVIEWED_WITH, written by
infra/glue_scripts/build_graph_edges.py / src/ingestion/graph_edges.py).

`co_reviewed` is a 1-hop lookup - CO_REVIEWED_WITH edges are already
product-to-product, written both directions at ingestion time.
`same_brand`/`same_category` are 2-hop: first find the product's own
brand/category node, then query *that* node for sibling products - each
hop is a single-partition Query, the whole point of the adjacency-list
design (see docs/designs/01-ingestion-pipeline.md).
"""

from __future__ import annotations

from boto3.dynamodb.conditions import Key

RELATION_EDGE_TYPES = {
    "co_reviewed": "CO_REVIEWED_WITH",
    "same_brand": "HAS_BRAND",
    "same_category": "BELONGS_TO",
}


def _query_edges(table, node: str, edge_prefix: str) -> list[str]:
    response = table.query(KeyConditionExpression=Key("node").eq(node) & Key("edge").begins_with(edge_prefix))
    return [item["edge"] for item in response["Items"]]


def find_related_products(table, product_id: str | None, relation: str | None) -> list[dict]:
    if not product_id or relation not in RELATION_EDGE_TYPES:
        return [{"error": f"product_id and a valid relation ({', '.join(RELATION_EDGE_TYPES)}) are required"}]

    edge_type = RELATION_EDGE_TYPES[relation]
    product_node = f"product#{product_id}"

    if edge_type == "CO_REVIEWED_WITH":
        edges = _query_edges(table, product_node, f"{edge_type}#product#")
        related_ids = [edge.rsplit("#", 1)[-1] for edge in edges]
    else:
        own_edges = _query_edges(table, product_node, f"{edge_type}#")
        if not own_edges:
            return []
        target_node = own_edges[0].split("#", 1)[1]  # e.g. "BELONGS_TO#category#X" -> "category#X"
        sibling_edges = _query_edges(table, target_node, "HAS_PRODUCT#product#")
        related_ids = [edge.rsplit("#", 1)[-1] for edge in sibling_edges if edge.rsplit("#", 1)[-1] != product_id]

    return [{"product_id": related_id, "relation": relation} for related_id in related_ids]
