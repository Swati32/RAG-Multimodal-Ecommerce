"""Glue Python Shell job: downloads each product's primary image and embeds
it via Bedrock Cohere Embed v4 (real-time), writing vectors to S3. Mirrors
embed_chunks.py's structure but for images - stage 4/ImageAgent support of
docs/designs/01-ingestion-pipeline.md.

Scoped to one (the first) image per product, not every image - see
docs/designs/01-ingestion-pipeline.md ("Image embeddings: one per product,
not one per image") for why.

`ingestion`/`rate_limiter` provided via --extra-py-files (a zip) - see the
sys.path workaround note in load_dataset.py.
"""

import base64
import glob
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
import requests
from awsglue.utils import getResolvedOptions
from botocore.exceptions import ClientError

for zip_path in glob.glob("/tmp/glue-python-libs-*/*.zip"):
    sys.path.insert(0, zip_path)

from ingestion.dynamo_reader import product_from_item, scan_all_items
from rate_limiter import RateLimiter

REGION = "us-east-2"
MAX_WORKERS = 8
MAX_RETRIES = 5
MAX_REQUESTS_PER_MINUTE = 180  # 90% of Cohere Embed v4's 200/min quota

args = getResolvedOptions(
    sys.argv, ["products_table", "output_bucket", "output_key", "embedding_model_id"]
)


def embed_with_retry(client, model_id: str, image_bytes: bytes, limiter: RateLimiter) -> list:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    # output_dimension=1024, not Cohere's 1536 default - OpenSearch's lucene
    # k-NN engine caps vector dimension at 1024 (confirmed by a real 400:
    # "Dimension value cannot be greater than 1024 for vector"), and staying
    # on lucene everywhere avoids a second ANN engine just for this index.
    body = json.dumps(
        {
            "images": [f"data:image/jpeg;base64,{b64}"],
            "input_type": "image",
            "embedding_types": ["float"],
            "output_dimension": 1024,
        }
    )
    for attempt in range(MAX_RETRIES):
        limiter.wait()
        try:
            response = client.invoke_model(modelId=model_id, body=body)
            return json.loads(response["body"].read())["embeddings"]["float"][0]
        except ClientError as e:
            if e.response["Error"]["Code"] != "ThrottlingException" or attempt == MAX_RETRIES - 1:
                raise
            time.sleep(2**attempt + 0.1 * attempt)


def embed_product_image(client, model_id: str, product, limiter: RateLimiter) -> dict:
    image_url = product.image_keys[0]
    image_bytes = requests.get(image_url, timeout=15).content
    embedding = embed_with_retry(client, model_id, image_bytes, limiter)
    return {"product_id": product.product_id, "image_url": image_url, "embedding": embedding}


def main() -> None:
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    products_table = dynamodb.Table(args["products_table"])
    products = [p for p in (product_from_item(i) for i in scan_all_items(products_table)) if p.image_keys]
    print(f"Embedding the primary image for {len(products)} products with {args['embedding_model_id']}...")

    client = boto3.client("bedrock-runtime", region_name=REGION)
    limiter = RateLimiter(MAX_REQUESTS_PER_MINUTE)
    embedded = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(embed_product_image, client, args["embedding_model_id"], p, limiter) for p in products]
        for i, future in enumerate(as_completed(futures), 1):
            try:
                embedded.append(future.result())
            except Exception as e:  # noqa: BLE001 - one bad image URL shouldn't fail the whole job
                print(f"Skipping an image due to error: {e}")
            if i % 500 == 0:
                print(f"{i}/{len(products)} processed...")

    s3 = boto3.client("s3", region_name=REGION)
    out_body = "\n".join(json.dumps(r) for r in embedded)
    s3.put_object(Bucket=args["output_bucket"], Key=args["output_key"], Body=out_body.encode("utf-8"))
    print(f"Wrote {len(embedded)} embedded image records to s3://{args['output_bucket']}/{args['output_key']}")


if __name__ == "__main__":
    main()
