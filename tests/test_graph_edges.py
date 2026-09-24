from ingestion.graph_edges import brand_edges, category_edges, co_reviewed_edges
from ingestion.models import Product, Review


def _product(product_id: str, category: str = "Electronics", brand: str | None = "Acme") -> Product:
    return Product(
        product_id=product_id,
        title="Widget",
        category=category,
        category_path=[category],
        price=9.99,
        avg_rating=4.0,
        review_count=1,
        description="A widget.",
        brand=brand,
    )


def test_category_edges_are_written_both_directions():
    edges = category_edges(_product("P1", category="Electronics"))
    assert ("product#P1", "BELONGS_TO#category#Electronics") in edges
    assert ("category#Electronics", "HAS_PRODUCT#product#P1") in edges


def test_brand_edges_skip_products_with_no_brand():
    assert brand_edges(_product("P1", brand=None)) == []

    edges = brand_edges(_product("P1", brand="Acme"))
    assert ("product#P1", "HAS_BRAND#brand#Acme") in edges
    assert ("brand#Acme", "HAS_PRODUCT#product#P1") in edges


def test_co_reviewed_edges_link_products_from_the_same_reviewer():
    reviews = [
        Review(review_id="1700000000#user-A", product_id="P1", rating=5, text="", timestamp=1700000000),
        Review(review_id="1700000001#user-A", product_id="P2", rating=4, text="", timestamp=1700000001),
        Review(review_id="1700000002#user-B", product_id="P3", rating=3, text="", timestamp=1700000002),
    ]

    edges = co_reviewed_edges(reviews)

    assert ("product#P1", "CO_REVIEWED_WITH#product#P2") in edges
    assert ("product#P2", "CO_REVIEWED_WITH#product#P1") in edges
    assert not any("P3" in node or "P3" in edge for node, edge in edges)


def test_co_reviewed_edges_ignore_reviewers_with_one_product():
    reviews = [Review(review_id="1700000000#user-A", product_id="P1", rating=5, text="", timestamp=1700000000)]
    assert co_reviewed_edges(reviews) == []
