from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import pytest

from launchscope_api.infrastructure.object_store import (
    ObjectStoreIntegrityError,
    S3ObjectStoreSettings,
    S3QuarantineObjectStore,
)
from launchscope_api.modules.project_dossier.material_ingestion import ObjectMetadata


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


def test_private_read_is_bounded_and_verifies_the_content_digest(monkeypatch) -> None:
    settings = S3ObjectStoreSettings(
        endpoint="http://minio:9000",
        bucket="launchscope-evidence",
        access_key_id="test-access",
        secret_access_key="test-secret",
    )
    store = S3QuarantineObjectStore(settings)
    payload = b"aggregate evidence"
    monkeypatch.setattr(
        store,
        "head",
        lambda _key: ObjectMetadata(sha256(payload).hexdigest(), len(payload), "text/plain"),
    )
    monkeypatch.setattr(
        "launchscope_api.infrastructure.object_store.urlopen",
        lambda *_args, **_kwargs: BytesIO(payload),
    )

    assert store.get_private("tenant/a/evidence/1.txt", max_bytes=100) == payload


def test_private_read_rejects_a_payload_that_differs_from_immutable_metadata(monkeypatch) -> None:
    settings = S3ObjectStoreSettings(
        endpoint="http://minio:9000",
        bucket="launchscope-evidence",
        access_key_id="test-access",
        secret_access_key="test-secret",
    )
    store = S3QuarantineObjectStore(settings)
    payload = b"tampered evidence"
    monkeypatch.setattr(
        store,
        "head",
        lambda _key: ObjectMetadata("a" * 64, len(payload), "text/plain"),
    )
    monkeypatch.setattr(
        "launchscope_api.infrastructure.object_store.urlopen",
        lambda *_args, **_kwargs: BytesIO(payload),
    )

    with pytest.raises(ObjectStoreIntegrityError, match="sha256"):
        store.get_private("tenant/a/evidence/1.txt", max_bytes=100)
