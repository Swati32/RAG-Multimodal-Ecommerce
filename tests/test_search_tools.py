from agents.search_tools import search_products


class StubOpenSearchClient:
    def __init__(self, hits: list[dict]):
        self._hits = hits
        self.last_search_kwargs = None

    def search(self, **kwargs):
        self.last_search_kwargs = kwargs
        return {"hits": {"hits": self._hits}}


def _hit(product_id: str, price: float = 9.99) -> dict:
    return {
        "_source": {
            "chunk_id": f"{product_id}#desc0",
            "product_id": product_id,
            "category": "Electronics",
            "brand": "Acme",
            "price": price,
            "avg_rating": 4.5,
            "chunk_type": "description",
            "text": "Great sound quality.",
        }
    }


def test_search_products_maps_hits_to_lean_dicts():
    client = StubOpenSearchClient([_hit("P1")])

    results = search_products(client, "chunks", "good bass", [0.1] * 1024)

    assert results == [
        {
            "chunk_id": "P1#desc0",
            "product_id": "P1",
            "category": "Electronics",
            "brand": "Acme",
            "price": 9.99,
            "avg_rating": 4.5,
            "chunk_type": "description",
            "text": "Great sound quality.",
        }
    ]


def test_search_products_without_filters_sends_bare_clauses():
    client = StubOpenSearchClient([])

    search_products(client, "chunks", "good bass", [0.1] * 1024)

    queries = client.last_search_kwargs["body"]["query"]["hybrid"]["queries"]
    assert queries[0] == {"match": {"text": "good bass"}}
    assert queries[1] == {"knn": {"embedding": {"vector": [0.1] * 1024, "k": 5}}}


def test_search_products_applies_filters_to_both_hybrid_clauses():
    client = StubOpenSearchClient([])

    search_products(client, "chunks", "good bass", [0.1] * 1024, max_price=50, min_rating=4)

    queries = client.last_search_kwargs["body"]["query"]["hybrid"]["queries"]
    expected_filters = [{"range": {"price": {"lte": 50}}}, {"range": {"avg_rating": {"gte": 4}}}]
    assert queries[0]["bool"]["filter"] == expected_filters
    assert queries[1]["bool"]["filter"] == expected_filters
    assert queries[0]["bool"]["must"] == [{"match": {"text": "good bass"}}]


def test_search_products_uses_the_hybrid_pipeline():
    client = StubOpenSearchClient([])

    search_products(client, "chunks", "good bass", [0.1] * 1024)

    assert client.last_search_kwargs["params"] == {"search_pipeline": "searchagent-hybrid-pipeline"}
