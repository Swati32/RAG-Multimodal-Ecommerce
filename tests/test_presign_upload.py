import pytest

from api.presign_upload import MAX_UPLOAD_BYTES, UnsupportedContentType, build_presigned_post


class StubS3Client:
    def __init__(self):
        self.calls = []

    def generate_presigned_post(self, **kwargs):
        self.calls.append(kwargs)
        return {"url": "https://example-bucket.s3.amazonaws.com/", "fields": {"key": kwargs["Key"], "Content-Type": kwargs["Fields"]["Content-Type"]}}


def test_build_presigned_post_rejects_an_unsupported_content_type():
    s3 = StubS3Client()

    with pytest.raises(UnsupportedContentType):
        build_presigned_post(s3, "my-bucket", "application/pdf")

    assert s3.calls == []  # never even asked S3 - rejected before that


def test_build_presigned_post_scopes_the_key_to_the_query_images_prefix():
    s3 = StubS3Client()

    result = build_presigned_post(s3, "my-bucket", "image/jpeg")

    assert result["key"].startswith("query-images/")
    assert result["key"].endswith(".jpg")
    assert s3.calls[0]["Bucket"] == "my-bucket"
    assert s3.calls[0]["Key"] == result["key"]


def test_build_presigned_post_enforces_the_size_cap_as_an_s3_condition():
    s3 = StubS3Client()

    build_presigned_post(s3, "my-bucket", "image/png")

    conditions = s3.calls[0]["Conditions"]
    assert ["content-length-range", 0, MAX_UPLOAD_BYTES] in conditions
