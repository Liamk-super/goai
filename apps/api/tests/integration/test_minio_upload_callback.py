"""Opt-in live MinIO/S3 acceptance: presigned PUT followed by server-side HEAD."""

from __future__ import annotations

import os
from hashlib import sha256
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

from launchscope_api.infrastructure.object_store import S3ObjectStoreSettings, S3QuarantineObjectStore
from launchscope_api.modules.evidence.application import EvidenceCaptureApplication
from launchscope_domain import Evidence, EvidenceReview, TenantScope


def test_real_minio_presigned_upload_and_head_metadata() -> None:
    required = (
        "LAUNCHSCOPE_TEST_S3_ENDPOINT",
        "LAUNCHSCOPE_TEST_S3_BUCKET",
        "LAUNCHSCOPE_TEST_S3_ACCESS_KEY",
        "LAUNCHSCOPE_TEST_S3_SECRET_KEY",
    )
    if any(not os.getenv(name) for name in required):
        pytest.skip("set LAUNCHSCOPE_TEST_S3_* to run live MinIO/S3 upload acceptance")
    content = b"launchscope T6 live evidence"
    digest = sha256(content).hexdigest()
    store = S3QuarantineObjectStore(
        S3ObjectStoreSettings(
            endpoint=os.environ["LAUNCHSCOPE_TEST_S3_ENDPOINT"],
            bucket=os.environ["LAUNCHSCOPE_TEST_S3_BUCKET"],
            access_key_id=os.environ["LAUNCHSCOPE_TEST_S3_ACCESS_KEY"],
            secret_access_key=os.environ["LAUNCHSCOPE_TEST_S3_SECRET_KEY"],
        )
    )
    scope = TenantScope(
        uuid4(), workspace_id=uuid4(), project_id=uuid4(), product_version_id=uuid4(), run_id=uuid4()
    )
    evidence_id = uuid4()
    key = (
        f"tenant/{scope.tenant_id}/project/{scope.project_id}/version/{scope.product_version_id}/"
        f"run/{scope.run_id}/evidence/{evidence_id}/source.txt"
    )
    url = store.initiate_upload(key, "text/plain", len(content), sha256=digest)
    request = Request(
        url,
        data=content,
        method="PUT",
        headers={"Content-Type": "text/plain", "x-amz-acl": "private", "x-amz-meta-sha256": digest},
    )
    with urlopen(request, timeout=15) as response:
        assert response.status in {200, 204}
    observed = store.head(key)
    assert observed is not None
    assert (observed.sha256, observed.size_bytes, observed.mime_type) == (digest, len(content), "text/plain")
    evidence = Evidence.create(
        scope,
        evidence_id=evidence_id,
        object_key=key,
        sha256=digest,
        size_bytes=len(content),
        mime_type="text/plain",
        source_type="MATERIAL",
        trust_level="E3",
    )
    assert EvidenceCaptureApplication(store).capture(EvidenceReview(scope), evidence) == evidence
    with urlopen(store.signed_read_url(key), timeout=15) as signed_read:
        assert signed_read.status == 200
        assert signed_read.read() == content
    with pytest.raises(HTTPError) as anonymous:
        urlopen(f"{store.settings.endpoint.rstrip('/')}/{store.settings.bucket}/{key}", timeout=15)
    assert anonymous.value.code == 403
