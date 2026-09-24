"""Glue Python Shell job: creates the product-images OpenSearch index (if
missing) and bulk-loads every embedded image, joined with its product's
filter fields from DynamoDB. Mirrors load_opensearch.py for the image path
of docs/designs/01-ingestion-pipeline.md - see also ImageAgent in
docs/designs/02-retrieval-agents.md.

`ingestion` is provided via --extra-py-files (a zip) - see the sys.path
workaround note in load_dataset.py.
"""

import glob
import json
import sys

import boto3
from awsglue.utils import getResolvedOptions
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection, helpers

for zip_path in glob.glob("/tmp/glue-python-libs-*/*.zip"):
    sys.path.insert(0, zip_path)

from ingestion.dynamo_reader import product_from_item, scan_all_items
from ingestion.opensearch_documents import IMAGE_INDEX_MAPPING, build_image_document

REGION = "us-east-2"

args = getResolvedOptions(
    sys.argv, ["input_bucket", "input_key", "products_table", "opensearch_endpoint", "index_name"]
)


def opensearch_client() -> OpenSearch:
    credentials = boto3.Session().get_credentials()
    auth = AWSV4SignerAuth(credentials, REGION, "es")
    return OpenSearch(
        hosts=[{"host": args["opensearch_endpoint"], "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
    )


def load_products() -> dict:
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(args["products_table"])
    return {item["product_id"]: product_from_item(item) for item in scan_all_items(table)}


def main() -> None:
    client = opensearch_client()
    index_name = args["index_name"]
    if not client.indices.exists(index=index_name):
        client.indices.create(index=index_name, body=IMAGE_INDEX_MAPPING)
        print(f"Created index {index_name}.")

    print("Loading product metadata from DynamoDB...")
    products = load_products()

    s3 = boto3.client("s3", region_name=REGION)
    body = s3.get_object(Bucket=args["input_bucket"], Key=args["input_key"])["Body"].read().decode("utf-8")

    def actions():
        skipped = 0
        for line in body.splitlines():
            if not line:
                continue
            record = json.loads(line)
            product = products.get(record["product_id"])
            if product is None:
                skipped += 1
                continue
            doc = build_image_document(product, record["image_url"], record["embedding"])
            yield {"_index": index_name, "_id": f"{product.product_id}#image0", "_source": doc}
        if skipped:
            print(f"Skipped {skipped} images with no matching product.")

    print("Bulk indexing...")
    success, errors = helpers.bulk(
        client, actions(), chunk_size=100, max_retries=5, initial_backoff=2, raise_on_error=False
    )
    print(f"Indexed {success} documents, {len(errors)} errors.")
    if errors:
        print("First error:", errors[0])


if __name__ == "__main__":
    main()
