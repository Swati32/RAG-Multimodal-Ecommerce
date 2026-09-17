from dataclasses import dataclass, field


@dataclass
class Product:
    product_id: str
    title: str
    category: str
    category_path: list[str]
    price: float
    avg_rating: float
    review_count: int
    description: str
    image_keys: list[str] = field(default_factory=list)
    brand: str | None = None
    in_stock: bool | None = None


@dataclass
class Review:
    review_id: str
    product_id: str
    rating: float
    text: str
    timestamp: int


@dataclass
class Chunk:
    chunk_id: str
    product_id: str
    chunk_type: str
    text: str
    review_id: str | None = None
