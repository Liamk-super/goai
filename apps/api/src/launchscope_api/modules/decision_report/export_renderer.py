from __future__ import annotations

import hashlib
import html
import io
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

RENDERER_VERSION = "report-web-v3-r4"
_UNSAFE_ARCHIVE_CHARS = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")


@dataclass(frozen=True, slots=True)
class PrintTarget:
    run_id: str
    report_id: str
    agent_code: str | None
    source_sha256: str
    document: dict[str, object]
    view: str
    locale: str


@dataclass(frozen=True, slots=True)
class EvidenceArchiveEntry:
    evidence_id: str
    filename: str
    expected_sha256: str
    actual_sha256: str | None
    body: bytes | None
    missing_reason: str | None
    citation_ids: tuple[str, ...]


def build_print_projection(target: PrintTarget) -> dict[str, object]:
    schema_version = str(target.document.get("schema_version", ""))
    if schema_version not in {"2.0", "3.0"}:
        raise ValueError("print rendering requires a supported immutable report schema")
    return {
        "report_schema_version": schema_version,
        "document": target.document,
        "integrity": {
            "canonical_sha256": target.source_sha256,
            "source_sha256": target.document["source_sha256"],
        },
        "projection": {
            "view": "FULL",
            "created_at": "1970-01-01T00:00:00Z",
            **(
                {"supervisor_report_id": str(target.document.get("supervisor_report_id", ""))}
                if target.agent_code
                else {}
            ),
        },
    }


def report_api_marker(target: PrintTarget) -> str:
    schema_version = str(target.document.get("schema_version", ""))
    if schema_version not in {"2.0", "3.0"}:
        raise ValueError("print rendering requires a supported immutable report schema")
    major = schema_version.split(".", 1)[0]
    if target.agent_code:
        return f"/api/v1/public/demo/v{major}/agent-reports/{quote(target.agent_code, safe='')}"
    return f"/api/v1/public/demo/v{major}/reports/{quote(target.report_id, safe='')}"


class PlaywrightReportRenderer:
    version = RENDERER_VERSION

    def __init__(self, web_base_url: str, *, timeout_ms: int = 20_000) -> None:
        parsed = urlsplit(web_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("report renderer web base URL must be absolute HTTP(S)")
        if timeout_ms < 1_000 or timeout_ms > 60_000:
            raise ValueError("report renderer timeout must be between one and sixty seconds")
        self._web_base_url = web_base_url.rstrip("/")
        self._web_origin = f"{parsed.scheme}://{parsed.netloc}"
        self._timeout_ms = timeout_ms

    def render_pdf(self, target: PrintTarget) -> bytes:
        from playwright.sync_api import sync_playwright

        projection = build_print_projection(target)
        placeholder_token = "render-token-is-server-scoped-000000000000"
        if target.agent_code:
            path = (
                f"/shared/demo/{placeholder_token}/runs/{quote(target.run_id, safe='')}/agent-reports/"
                f"{quote(target.agent_code, safe='')}?print=1&view={target.view.lower()}"
            )
        else:
            path = (
                f"/shared/demo/{placeholder_token}/reports/{quote(target.report_id, safe='')}"
                f"?print=1&view={target.view.lower()}"
            )
        api_marker = report_api_marker(target)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(locale=target.locale)
                context.add_cookies(
                    [
                        {
                            "name": "launchscope.locale",
                            "value": target.locale,
                            "url": self._web_origin,
                            "sameSite": "Lax",
                        }
                    ]
                )
                page = context.new_page()

                def route_request(route: Any) -> None:
                    url = route.request.url
                    if api_marker in url:
                        route.fulfill(
                            status=200,
                            content_type="application/json; charset=utf-8",
                            body=json.dumps(projection, ensure_ascii=False, separators=(",", ":")),
                        )
                        return
                    parsed_request = urlsplit(url)
                    if f"{parsed_request.scheme}://{parsed_request.netloc}" == self._web_origin:
                        route.continue_()
                        return
                    route.abort()

                page.route("**/*", route_request)
                page.goto(f"{self._web_base_url}{path}", wait_until="domcontentloaded", timeout=self._timeout_ms)
                ready = page.locator('[data-report-ready="true"]')
                ready.wait_for(state="visible", timeout=self._timeout_ms)
                if target.agent_code and target.view == "FULL":
                    full_tab = page.locator('[data-report-view="full"]')
                    if full_tab.count():
                        full_tab.click()
                        page.locator('[data-report-view="full"][aria-selected="true"]').wait_for(
                            state="visible", timeout=self._timeout_ms
                        )
                export_details = ['details[data-export-audit="true"]']
                if target.view == "FULL":
                    export_details.extend(
                        ["details.report-v3-full-report", "details.report-v3-evidence-explainer"]
                    )
                for selector in export_details:
                    page.locator(selector).evaluate_all("elements => elements.forEach(element => element.open = true)")
                page.emulate_media(media="print")
                body = page.pdf(
                    format="A4",
                    print_background=True,
                    prefer_css_page_size=True,
                    margin={"top": "12mm", "right": "10mm", "bottom": "12mm", "left": "10mm"},
                )
            finally:
                browser.close()
        if not body.startswith(b"%PDF"):
            raise RuntimeError("Chromium did not return a PDF document")
        return body


def sanitize_archive_component(value: str, *, fallback: str) -> str:
    name = PurePosixPath(value.replace("\\", "/")).name.strip().strip(".")
    name = _UNSAFE_ARCHIVE_CHARS.sub("_", name)
    if name in {"", ".", ".."}:
        name = fallback
    return name[:180]


def assemble_report_package(
    *,
    pdfs: dict[str, bytes],
    source_directory: list[dict[str, object]],
    evidence: list[EvidenceArchiveEntry],
    include_evidence: bool,
) -> bytes:
    files: dict[str, bytes] = {}
    manifest_files: list[dict[str, object]] = []
    for requested_name, body in sorted(pdfs.items()):
        name = sanitize_archive_component(requested_name, fallback="report.pdf")
        files[name] = body
        manifest_files.append(_manifest_file(name, body, "REPORT_PDF"))

    source_json = _canonical_json({"sources": source_directory})
    source_html = _source_directory_html(source_directory)
    files["来源目录.json"] = source_json
    files["来源目录.html"] = source_html
    manifest_files.extend(
        [
            _manifest_file("来源目录.html", source_html, "SOURCE_DIRECTORY"),
            _manifest_file("来源目录.json", source_json, "SOURCE_DIRECTORY"),
        ]
    )

    evidence_index: list[dict[str, object]] = []
    if include_evidence:
        for entry in sorted(evidence, key=lambda item: item.evidence_id):
            index_item: dict[str, object] = {
                "evidence_id": entry.evidence_id,
                "expected_sha256": entry.expected_sha256,
                "actual_sha256": entry.actual_sha256,
                "citation_ids": list(entry.citation_ids),
                "missing_reason": entry.missing_reason,
                "archive_path": None,
            }
            if entry.body is not None and entry.missing_reason is None:
                safe_id = sanitize_archive_component(entry.evidence_id, fallback="evidence")
                safe_name = sanitize_archive_component(entry.filename, fallback=f"{safe_id}.bin")
                path = f"evidence/{safe_id}/{safe_name}"
                index_item["archive_path"] = path
                files[path] = entry.body
                manifest_files.append(_manifest_file(path, entry.body, "EVIDENCE_ORIGINAL"))
            evidence_index.append(index_item)
        evidence_body = _canonical_json({"evidence": evidence_index})
        files["evidence-index.json"] = evidence_body
        manifest_files.append(_manifest_file("evidence-index.json", evidence_body, "EVIDENCE_INDEX"))

    manifest = _canonical_json(
        {
            "schema_version": "ReportPackageManifestV1",
            "include_evidence": include_evidence,
            "files": sorted(manifest_files, key=lambda item: str(item["path"])),
        }
    )
    files["manifest.json"] = manifest
    stream = io.BytesIO()
    with ZipFile(stream, mode="w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path, body in sorted(files.items()):
            info = ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100600 << 16
            archive.writestr(info, body)
    return stream.getvalue()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _manifest_file(path: str, body: bytes, kind: str) -> dict[str, object]:
    return {"path": path, "kind": kind, "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}


def _source_directory_html(sources: list[dict[str, object]]) -> bytes:
    items = []
    for source in sources:
        title = html.escape(str(source.get("title", "未命名来源")))
        url = source.get("canonical_url")
        if isinstance(url, str) and url:
            safe_url = html.escape(url, quote=True)
            items.append(f'<li><a href="{safe_url}">{title}</a></li>')
        else:
            items.append(f"<li>{title}</li>")
    body = (
        "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
        "<title>来源目录</title><ol>"
        + "".join(items)
        + "</ol>"
    )
    return body.encode("utf-8")


__all__ = [
    "EvidenceArchiveEntry",
    "PlaywrightReportRenderer",
    "PrintTarget",
    "RENDERER_VERSION",
    "assemble_report_package",
    "sanitize_archive_component",
]
