"""Glue Python Shell job: creates the OpenSearch index (if missing) and
bulk-loads every embedded chunk, joined with its product's filter fields
from DynamoDB. Stage 5 of docs/designs/01-ingestion-pipeline.md.

`ingestion` is provided via --extra-py-files (a zip) - see the sys.path
workaround note in load_dataset.py, same Glue Python Shell quirk applies.
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
from ingestion.models import Chunk
from ingestion.opensearch_documents import INDEX_MAPPING, build_chunk_document

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
        client.indices.create(index=index_name, body=INDEX_MAPPING)
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
            chunk = Chunk(
                chunk_id=record["chunk_id"],
                product_id=record["product_id"],
                chunk_type=record["chunk_type"],
                text=record["text"],
                review_id=record.get("review_id"),
            )
            doc = build_chunk_document(chunk, product, record["embedding"])
            yield {"_index": index_name, "_id": chunk.chunk_id, "_source": doc}
        if skipped:
            print(f"Skipped {skipped} chunks with no matching product.")

    print("Bulk indexing...")
    # chunk_size well below the default 500 and max_retries>0 with backoff -
    # the single-node t3.small.search domain returned 429 (bulk queue full)
    # against the default settings on the first attempt.
    success, errors = helpers.bulk(
        client, actions(), chunk_size=100, max_retries=5, initial_backoff=2, raise_on_error=False
    )
    print(f"Indexed {success} documents, {len(errors)} errors.")
    if errors:
        print("First error:", errors[0])


if __name__ == "__main__":
    main()
