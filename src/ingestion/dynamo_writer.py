from decimal import Decimal

from .models import Product, Review


def _product_item(product: Product) -> dict:
    return {
        "product_id": product.product_id,
        "title": product.title,
        "category": product.category,
        "category_path": product.category_path,
        "price": Decimal(str(product.price)),
        "avg_rating": Decimal(str(product.avg_rating)),
        "review_count": product.review_count,
        "description": product.description,
        "image_keys": product.image_keys,
        "brand": product.brand,
        "in_stock": product.in_stock,
    }


def _review_item(review: Review) -> dict:
    return {
        "product_id": review.product_id,
        "review_id": review.review_id,
        "rating": Decimal(str(review.rating)),
        "text": review.text,
        "timestamp": review.timestamp,
    }


def upsert_product(table, product: Product) -> None:
    table.put_item(Item=_product_item(product))


def upsert_review(table, review: Review) -> None:
    table.put_item(Item=_review_item(review))
