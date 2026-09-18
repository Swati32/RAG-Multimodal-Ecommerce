from decimal import Decimal

from .models import Product, Review


def _to_float(value) -> float:
    return float(value) if isinstance(value, Decimal) else value


def product_from_item(item: dict) -> Product:
    return Product(
        product_id=item["product_id"],
        title=item["title"],
        category=item["category"],
        category_path=list(item["category_path"]),
        price=_to_float(item["price"]),
        avg_rating=_to_float(item["avg_rating"]),
        review_count=int(item["review_count"]),
        description=item["description"],
        image_keys=list(item.get("image_keys") or []),
        brand=item.get("brand"),
        in_stock=item.get("in_stock"),
    )


def review_from_item(item: dict) -> Review:
    return Review(
        review_id=item["review_id"],
        product_id=item["product_id"],
        rating=_to_float(item["rating"]),
        text=item["text"],
        timestamp=int(item["timestamp"]),
    )


def scan_all_items(table):
    response = table.scan()
    yield from response["Items"]
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        yield from response["Items"]
