from __future__ import annotations

from hashlib import sha256
from urllib.parse import parse_qs, urlsplit

from launchscope_api.infrastructure.object_store import S3ObjectStoreSettings, S3QuarantineObjectStore


def test_private_presigned_upload_binds_content_metadata_and_has_short_ttl(monkeypatch) -> None:
    settings = S3ObjectStoreSettings(
        endpoint="http://minio:9000",
        bucket="launchscope-evidence",
        access_key_id="test-access",
        secret_access_key="test-secret",
        presign_ttl_seconds=300,
    )
    store = S3QuarantineObjectStore(settings)
    calls: list[tuple[str, str | None]] = []

    def request(method: str, object_key: str | None, *, payload: bytes | None = None):
        calls.append((method, object_key))
        return object()

    monkeypatch.setattr(store, "_request", request)
    digest = sha256(b"evidence").hexdigest()
    url = store.initiate_upload("tenant/a/evidence/1.txt", "text/plain", 8, sha256=digest)

    query = parse_qs(urlsplit(url).query)
    assert calls == [("HEAD", None)]
    assert urlsplit(url).path == "/launchscope-evidence/tenant/a/evidence/1.txt"
    assert query["X-Amz-Expires"] == ["300"]
    assert "x-amz-acl;" in query["X-Amz-SignedHeaders"][0]
    assert "x-amz-meta-sha256" in query["X-Amz-SignedHeaders"][0]
    assert "X-Amz-Signature" in query


def test_private_presigned_read_has_short_ttl_and_no_write_headers(monkeypatch) -> None:
    settings = S3ObjectStoreSettings(
        endpoint="http://minio:9000",
        bucket="launchscope-evidence",
        access_key_id="test-access",
        secret_access_key="test-secret",
        presign_ttl_seconds=180,
    )
    store = S3QuarantineObjectStore(settings)
    monkeypatch.setattr(store, "ensure_private_bucket", lambda: None)

    url = store.signed_read_url("tenant/a/evidence/1.txt")

    query = parse_qs(urlsplit(url).query)
    assert query["X-Amz-Expires"] == ["180"]
    assert query["X-Amz-SignedHeaders"] == ["host"]
    assert "X-Amz-Signature" in query
