from agents.image_tools import search_similar_images


class StubOpenSearchClient:
    def __init__(self, hits: list[dict]):
        self._hits = hits
        self.last_search_kwargs = None

    def search(self, **kwargs):
        self.last_search_kwargs = kwargs
        return {"hits": {"hits": self._hits}}


def _hit(product_id: str, price: float = 19.99) -> dict:
    return {
        "_source": {
            "image_id": f"{product_id}#image0",
            "product_id": product_id,
            "image_url": f"https://example.com/{product_id}.jpg",
            "category": "All Beauty",
            "brand": "Acme",
            "price": price,
            "avg_rating": 4.3,
        }
    }


def test_search_similar_images_maps_hits_to_lean_dicts():
    client = StubOpenSearchClient([_hit("P1")])

    results = search_similar_images(client, "product_images", [0.1] * 1024)

    assert results == [
        {
            "image_id": "P1#image0",
            "product_id": "P1",
            "image_url": "https://example.com/P1.jpg",
            "category": "All Beauty",
            "brand": "Acme",
            "price": 19.99,
            "avg_rating": 4.3,
        }
    ]


def test_search_similar_images_without_filters_sends_a_bare_knn_query():
    client = StubOpenSearchClient([])

    search_similar_images(client, "product_images", [0.1] * 1024)

    assert client.last_search_kwargs["body"]["query"] == {"knn": {"embedding": {"vector": [0.1] * 1024, "k": 5}}}


def test_search_similar_images_applies_filters():
    client = StubOpenSearchClient([])

    search_similar_images(client, "product_images", [0.1] * 1024, max_price=25, brand="Acme")

    query = client.last_search_kwargs["body"]["query"]
    assert query["bool"]["filter"] == [{"term": {"brand": "Acme"}}, {"range": {"price": {"lte": 25}}}]
    assert query["bool"]["must"] == [{"knn": {"embedding": {"vector": [0.1] * 1024, "k": 5}}}]
