from ingestion.models import Chunk, Product
from ingestion.opensearch_documents import EMBEDDING_DIM, build_chunk_document


def test_build_chunk_document_carries_filter_fields():
    product = Product(
        product_id="P1",
        title="Wireless Headphones",
        category="Electronics/Audio",
        category_path=["Electronics", "Audio"],
        price=49.99,
        avg_rating=4.5,
        review_count=120,
        description="Great sound.",
        brand="Acme",
        in_stock=True,
    )
    chunk = Chunk(chunk_id="P1#desc0", product_id="P1", chunk_type="description", text="Great sound.")
    embedding = [0.1] * EMBEDDING_DIM

    doc = build_chunk_document(chunk, product, embedding)

    assert doc["chunk_id"] == "P1#desc0"
    assert doc["product_id"] == "P1"
    assert doc["category"] == "Electronics/Audio"
    assert doc["brand"] == "Acme"
    assert doc["price"] == 49.99
    assert doc["chunk_type"] == "description"
    assert doc["in_stock"] is True
    assert len(doc["embedding"]) == EMBEDDING_DIM
