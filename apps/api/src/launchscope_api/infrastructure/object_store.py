"""Private S3-compatible object storage adapters for MinIO and AWS S3."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from launchscope_api.modules.project_dossier.material_ingestion import ObjectMetadata, QuarantineObjectStore

_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ObjectStoreConfigurationError(RuntimeError):
    """The S3 configuration cannot provide a safe private object store."""


class ObjectStoreIntegrityError(RuntimeError):
    """An uploaded object lacks immutable metadata required for evidence."""


@dataclass(frozen=True, slots=True)
class S3ObjectStoreSettings:
    endpoint: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    region: str = "us-east-1"
    presign_ttl_seconds: int = 900

    @classmethod
    def from_env(cls) -> S3ObjectStoreSettings:
        endpoint = os.getenv("LAUNCHSCOPE_S3_ENDPOINT") or os.getenv("MINIO_ENDPOINT")
        bucket = os.getenv("LAUNCHSCOPE_EVIDENCE_BUCKET")
        access_key_id = os.getenv("LAUNCHSCOPE_S3_ACCESS_KEY") or os.getenv("MINIO_ROOT_USER")
        secret_access_key = os.getenv("LAUNCHSCOPE_S3_SECRET_KEY") or os.getenv("MINIO_ROOT_PASSWORD")
        if endpoint is None or bucket is None or access_key_id is None or secret_access_key is None:
            raise ObjectStoreConfigurationError(
                "LAUNCHSCOPE_S3_ENDPOINT, LAUNCHSCOPE_EVIDENCE_BUCKET, and S3 credentials are required"
            )
        return cls(
            endpoint=endpoint,
            bucket=bucket,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            region=os.getenv("LAUNCHSCOPE_S3_REGION", "us-east-1"),
            presign_ttl_seconds=int(os.getenv("LAUNCHSCOPE_S3_PRESIGN_TTL_SECONDS", "900")),
        )

    def __post_init__(self) -> None:
        parsed = urlsplit(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ObjectStoreConfigurationError("S3 endpoint must be an absolute HTTP(S) URL")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "minio"}:
            raise ObjectStoreConfigurationError("non-local S3 endpoints must use HTTPS")
        if _BUCKET.fullmatch(self.bucket) is None:
            raise ObjectStoreConfigurationError("evidence bucket must be a DNS-compatible private bucket name")
        if not self.access_key_id.strip() or not self.secret_access_key.strip():
            raise ObjectStoreConfigurationError("S3 credentials cannot be empty")
        if not 60 <= self.presign_ttl_seconds <= 900:
            raise ObjectStoreConfigurationError("presign TTL must be between 60 and 900 seconds")


class S3QuarantineObjectStore(QuarantineObjectStore):
    """Private ACLs, bounded PUT URLs, and completion facts from S3 HEAD."""

    def __init__(self, settings: S3ObjectStoreSettings) -> None:
        self.settings = settings
        self._bucket_ready = False

    @classmethod
    def from_env(cls) -> S3QuarantineObjectStore:
        return cls(S3ObjectStoreSettings.from_env())

    def initiate_upload(self, object_key: str, mime_type: str, size_bytes: int, *, sha256: str | None = None) -> str:
        digest = _require_sha256(sha256)
        self.ensure_private_bucket()
        return self._presign(
            "PUT",
            object_key,
            {
                "content-length": str(size_bytes),
                "content-type": mime_type,
                "x-amz-acl": "private",
                "x-amz-meta-sha256": digest,
            },
        )

    def head(self, object_key: str) -> ObjectMetadata | None:
        response = self._request("HEAD", object_key)
        if response is None:
            return None
        try:
            return ObjectMetadata(
                sha256=_require_sha256(response.headers.get("x-amz-meta-sha256")),
                size_bytes=int(response.headers["content-length"]),
                mime_type=response.headers["content-type"].split(";", 1)[0].strip(),
                etag=response.headers.get("etag", "").strip('"'),
                metadata={
                    key.removeprefix("x-amz-meta-"): value
                    for key, value in response.headers.items()
                    if key.lower().startswith("x-amz-meta-")
                },
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ObjectStoreIntegrityError("object HEAD response lacks size, MIME type, or sha256 metadata") from exc

    def delete(self, object_key: str) -> bool:
        """Delete one exact private key; never accepts a prefix or bucket target."""

        if not object_key.strip() or object_key.endswith("/"):
            raise ObjectStoreIntegrityError("retention deletion requires one exact object key")
        existed = self.head(object_key) is not None
        if existed:
            self._request("DELETE", object_key)
        return existed

    def put_private(self, object_key: str, payload: bytes, mime_type: str) -> str:
        """Write one derived Evidence/Report body through a bounded private PUT."""

        if not object_key.strip() or object_key.endswith("/") or not payload:
            raise ObjectStoreIntegrityError("private object write requires one exact key and non-empty payload")
        digest = hashlib.sha256(payload).hexdigest()
        url = self.initiate_upload(object_key, mime_type, len(payload), sha256=digest)
        headers = {
            "Content-Type": mime_type,
            "Content-Length": str(len(payload)),
            "x-amz-acl": "private",
            "x-amz-meta-sha256": digest,
        }
        try:
            with urlopen(Request(url, data=payload, headers=headers, method="PUT"), timeout=10) as response:
                if response.status not in {200, 201, 204}:
                    raise ObjectStoreConfigurationError(f"S3 PUT failed with HTTP {response.status}")
        except HTTPError as exc:
            raise ObjectStoreConfigurationError(f"S3 PUT failed with HTTP {exc.code}") from exc
        return digest

    def signed_read_url(self, object_key: str) -> str:
        """Issue a short-lived GET URL for one already-authorized private object."""

        if not object_key.strip() or object_key.endswith("/") or object_key.startswith("deleted/"):
            raise ObjectStoreIntegrityError("signed reading requires one live exact object key")
        self.ensure_private_bucket()
        return self._presign("GET", object_key, {})

    def ensure_private_bucket(self) -> None:
        if self._bucket_ready:
            return
        if self._request("HEAD", None) is None:
            self._request("PUT", None, payload=b"")
        # S3 defaults new buckets to private, every upload below has an
        # explicit private ACL, and this adapter never generates public URLs.
        self._bucket_ready = True

    def _request(self, method: str, object_key: str | None, *, payload: bytes | None = None) -> Any:
        url, headers = self._signed_request(method, object_key, payload=payload)
        try:
            return urlopen(Request(url, data=payload, headers=headers, method=method), timeout=10)
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise ObjectStoreConfigurationError(f"S3 {method} request failed with HTTP {exc.code}") from exc

    def _presign(self, method: str, object_key: str, headers: Mapping[str, str]) -> str:
        now = datetime.now(UTC)
        date_stamp, amz_date = now.strftime("%Y%m%d"), now.strftime("%Y%m%dT%H%M%SZ")
        scope = f"{date_stamp}/{self.settings.region}/s3/aws4_request"
        canonical_headers = {"host": self._host, **{key.lower(): value.strip() for key, value in headers.items()}}
        signed_headers = ";".join(sorted(canonical_headers))
        query = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.settings.access_key_id}/{scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(self.settings.presign_ttl_seconds),
            "X-Amz-SignedHeaders": signed_headers,
        }
        canonical_query = urlencode(sorted(query.items()), quote_via=quote, safe="~")
        request = "\n".join(
            (
                method,
                self._canonical_uri(object_key),
                canonical_query,
                self._canonical_headers(canonical_headers),
                signed_headers,
                "UNSIGNED-PAYLOAD",
            )
        )
        signature = self._signature(amz_date, scope, request)
        return f"{self._url(object_key)}?{canonical_query}&X-Amz-Signature={signature}"

    def _signed_request(
        self, method: str, object_key: str | None, *, payload: bytes | None
    ) -> tuple[str, dict[str, str]]:
        now = datetime.now(UTC)
        date_stamp, amz_date = now.strftime("%Y%m%d"), now.strftime("%Y%m%dT%H%M%SZ")
        scope = f"{date_stamp}/{self.settings.region}/s3/aws4_request"
        payload_hash = hashlib.sha256(payload or b"").hexdigest()
        headers = {"host": self._host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
        signed_headers = ";".join(sorted(headers))
        request = "\n".join(
            (
                method,
                self._canonical_uri(object_key),
                "",
                self._canonical_headers(headers),
                signed_headers,
                payload_hash,
            )
        )
        headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.settings.access_key_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={self._signature(amz_date, scope, request)}"
        )
        return self._url(object_key), headers

    @property
    def _host(self) -> str:
        return urlsplit(self.settings.endpoint).netloc

    def _url(self, object_key: str | None) -> str:
        suffix = quote(self.settings.bucket, safe="")
        if object_key is not None:
            suffix += "/" + quote(object_key, safe="/-_.~")
        return f"{self.settings.endpoint.rstrip('/')}/{suffix}"

    def _canonical_uri(self, object_key: str | None) -> str:
        suffix = quote(self.settings.bucket, safe="-_.~")
        if object_key is not None:
            suffix += "/" + quote(object_key, safe="/-_.~")
        return f"/{suffix}"

    @staticmethod
    def _canonical_headers(headers: Mapping[str, str]) -> str:
        return "".join(f"{key}:{headers[key].strip()}\n" for key in sorted(headers))

    def _signature(self, amz_date: str, scope: str, canonical_request: str) -> str:
        canonical_digest = hashlib.sha256(canonical_request.encode()).hexdigest()
        to_sign = "\n".join(("AWS4-HMAC-SHA256", amz_date, scope, canonical_digest))
        key = ("AWS4" + self.settings.secret_access_key).encode()
        for value in (amz_date[:8], self.settings.region, "s3", "aws4_request"):
            key = hmac.new(key, value.encode(), hashlib.sha256).digest()
        return hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()


def _require_sha256(value: str | None) -> str:
    normalized = value.strip().lower() if isinstance(value, str) else ""
    if _SHA256.fullmatch(normalized) is None:
        raise ObjectStoreIntegrityError("S3 object sha256 metadata must be a 64-character digest")
    return normalized


__all__ = [
    "ObjectStoreConfigurationError",
    "ObjectStoreIntegrityError",
    "S3ObjectStoreSettings",
    "S3QuarantineObjectStore",
]
