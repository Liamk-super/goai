"""HTTPS-only public research with DNS, redirect, and response-budget checks."""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from ..tool_gateway.contract import AdapterResult, ToolContract, ToolGatewayError


class PublicResearchPolicyError(ToolGatewayError):
    """A public URL is outside the strict, read-only research policy."""


Resolver = Callable[[str], list[str]]


def system_resolver(host: str) -> list[str]:
    return sorted({str(item[4][0]) for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


def validate_public_https_url(url: str, allowed_domains: tuple[str, ...], resolver: Resolver = system_resolver) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise PublicResearchPolicyError("public research permits only absolute HTTPS URLs without userinfo")
    if parsed.port not in {None, 443}:
        raise PublicResearchPolicyError("public research permits only default HTTPS port")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "metadata.google.internal", "metadata.azure.internal"}:
        raise PublicResearchPolicyError("loopback and cloud metadata hosts are prohibited")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise PublicResearchPolicyError("private, loopback, and reserved IP addresses are prohibited")
    normalized_domains = tuple(domain.rstrip(".").lower() for domain in allowed_domains)
    if not any(host == domain or host.endswith(f".{domain}") for domain in normalized_domains):
        raise PublicResearchPolicyError("domain is not in the frozen Tool Contract allowlist")
    try:
        addresses = [ipaddress.ip_address(value) for value in resolver(host)]
    except (OSError, ValueError) as exc:
        raise PublicResearchPolicyError("domain resolution failed") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise PublicResearchPolicyError("DNS result includes private, loopback, metadata, or reserved address")
    return url


@dataclass(frozen=True, slots=True)
class HttpResponse:
    url: str
    status: int
    headers: Mapping[str, str]
    body: bytes


HttpTransport = Callable[[str, str, int, int], HttpResponse]


def urllib_transport(url: str, method: str, timeout_seconds: int, max_response_bytes: int) -> HttpResponse:
    request = urllib.request.Request(url, method=method, headers={"User-Agent": "LaunchScope/0.1 read-only"})
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            body = response.read(max_response_bytes + 1)
            return HttpResponse(response.url, response.status, dict(response.headers.items()), body)
    except urllib.error.HTTPError as exc:
        return HttpResponse(
            exc.url, exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read(max_response_bytes + 1)
        )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PublicResearchClient:
    def __init__(self, *, transport: HttpTransport = urllib_transport, resolver: Resolver = system_resolver) -> None:
        self.transport = transport
        self.resolver = resolver

    def fetch(self, parameters: Mapping[str, object], contract: ToolContract) -> AdapterResult:
        url = parameters.get("url")
        method = parameters.get("method", "GET")
        if not isinstance(url, str) or method not in {"GET", "HEAD"}:
            raise PublicResearchPolicyError("public research requires a URL and HTTPS GET or HEAD")
        if method not in {"GET", "HEAD"}:
            raise PublicResearchPolicyError("HTTP method is not allowed")
        current = validate_public_https_url(url, contract.allowed_domains, self.resolver)
        for redirect_count in range(contract.max_redirects + 1):
            response = self.transport(current, method, contract.timeout_seconds, contract.max_response_bytes)
            if len(response.body) > contract.max_response_bytes:
                raise PublicResearchPolicyError("response exceeds frozen byte budget")
            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location") or response.headers.get("location")
                if not location or redirect_count == contract.max_redirects:
                    raise PublicResearchPolicyError("redirect is missing or exceeds frozen redirect budget")
                # Relative redirect is resolved against an already validated HTTPS origin.
                from urllib.parse import urljoin

                current = validate_public_https_url(urljoin(current, location), contract.allowed_domains, self.resolver)
                continue
            if response.status < 200 or response.status >= 300:
                raise PublicResearchPolicyError(f"public source returned HTTP {response.status}")
            fetched_at = datetime.now(UTC).isoformat()
            digest = hashlib.sha256(response.body).hexdigest()
            result = {
                "source_url": current,
                "fetched_at": fetched_at,
                "content_sha256": digest,
                "content_type": response.headers.get(
                    "Content-Type", response.headers.get("content-type", "application/octet-stream")
                ),
            }
            evidence = {
                "source_url": current,
                "fetched_at": fetched_at,
                "sha256": digest,
                "bytes": len(response.body),
                "source_type": "PUBLIC_RESEARCH",
            }
            return AdapterResult(result, evidence)
        raise PublicResearchPolicyError("redirect handling exhausted")
