def upsert_edge(table, node: str, edge: str) -> None:
    table.put_item(Item={"node": node, "edge": edge})
