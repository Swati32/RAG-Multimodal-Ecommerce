from .models import Chunk, Product

EMBEDDING_DIM = 1024  # Titan Text Embeddings v2 output size

# engine: lucene, not nmslib/faiss - native to OpenSearch, no separate native
# library to manage. At this dataset's scale (~5k products, ~15k chunks) the
# HNSW-vs-exact-kNN tradeoff nmslib/faiss exist for doesn't matter; lucene
# fits the project's smallest-ops-burden stance. See docs/designs/
# 02-retrieval-agents.md, "Indexing Strategy".
INDEX_MAPPING = {
    "settings": {"index": {"knn": True}},
    "mappings": {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "product_id": {"type": "keyword"},
            "category": {"type": "keyword"},
            "category_path": {"type": "keyword"},
            "brand": {"type": "keyword"},
            "price": {"type": "float"},
            "avg_rating": {"type": "float"},
            "review_count": {"type": "integer"},
            "chunk_type": {"type": "keyword"},
            "in_stock": {"type": "boolean"},
            "text": {"type": "text"},
            "embedding": {
                "type": "knn_vector",
                "dimension": EMBEDDING_DIM,
                "method": {"engine": "lucene", "name": "hnsw", "space_type": "cosinesimil"},
            },
        }
    },
}


def build_chunk_document(chunk: Chunk, product: Product, embedding: list[float]) -> dict:
    return {
        "chunk_id": chunk.chunk_id,
        "product_id": product.product_id,
        "category": product.category,
        "category_path": product.category_path,
        "brand": product.brand,
        "price": product.price,
        "avg_rating": product.avg_rating,
        "review_count": product.review_count,
        "chunk_type": chunk.chunk_type,
        "in_stock": product.in_stock,
        "text": chunk.text,
        "embedding": embedding,
    }
