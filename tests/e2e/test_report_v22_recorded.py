from __future__ import annotations

import io
import json
import os
import time
from base64 import b64encode
from pathlib import Path
from typing import Any
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from playwright.sync_api import Page, Route, expect, sync_playwright

RUN_ID = "12121212-1212-4212-8212-121212121212"
PROJECT_ID = "23232323-2323-4232-8232-232323232323"
VERSION_ID = "34343434-3434-4343-8343-343434343434"
REPORT_ID = "45454545-4545-4454-8454-454545454545"
EVIDENCE_ID = "56565656-5656-4565-8565-565656565656"
LOCATOR_ID = "67676767-6767-4676-8676-676767676767"
TOKEN = "recorded-v22-public-token-000000000000000000000000"
AGENTS = (
    ("user-evidence", "用户报告"),
    ("product-engineering", "产品经理报告"),
    ("business-investment", "投资人报告"),
    ("evidence-auditor", "证据校准报告"),
)


def _wait_until(page: Page, predicate, *, timeout_seconds: float = 5) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        page.wait_for_timeout(50)
    raise AssertionError("Recorded browser condition did not become true before the timeout")


def _capture_browser_errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
    page.on("console", lambda message: errors.append(f"console: {message.text}") if message.type == "error" else None)
    return errors


@pytest.fixture
def recorded_page() -> Any:
    url = os.getenv("LAUNCHSCOPE_WEB_E2E_URL")
    if not url:
        pytest.skip("LAUNCHSCOPE_WEB_E2E_URL is required for Recorded browser acceptance")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(locale="zh-CN", accept_downloads=True)
        context.add_cookies([{"name": "launchscope.locale", "value": "zh-CN", "url": url}])
        context.add_init_script(
            "localStorage.setItem('launchscope.demo.session.v1', "
            + json.dumps(
                json.dumps(
                    {
                        "schemaVersion": "launchscope.demo.session.v1",
                        "tenantId": "78787878-7878-4787-8787-787878787878",
                        "workspaceId": "89898989-8989-4898-8898-898989898989",
                        "actorId": "recorded-v22-browser",
                        "displayName": "Recorded v2.2 Browser",
                        "createdAt": "2026-08-13T00:00:00Z",
                    }
                )
            )
            + "); localStorage.setItem('launchscope.locale', 'zh-CN');"
            + "for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {"
            + "const key = sessionStorage.key(index);"
            + "if (key && key.includes('evaluation')) sessionStorage.removeItem(key); }"
        )
        page = context.new_page()
        page.set_default_timeout(15_000)
        page.set_default_navigation_timeout(60_000)
        yield page, url
        context.close()
        browser.close()


def _citation() -> dict[str, object]:
    return {
        "citation_id": "citation-internal-1",
        "claim_id": "claim-summary",
        "evidence_id": EVIDENCE_ID,
        "source_locator_id": LOCATOR_ID,
        "support_role": "SUPPORT",
        "audit_status": "VERIFIED",
        "label": 1,
    }


def _source() -> dict[str, object]:
    return {
        "source_locator_id": LOCATOR_ID,
        "evidence_id": EVIDENCE_ID,
        "source_kind": "INTERNAL_MATERIAL",
        "title": "已上传的访谈记录",
        "publisher": None,
        "published_at": None,
        "fetched_at": "2026-08-13T00:00:00Z",
        "locator": {"page": 1},
        "region": "HK",
        "independence_group": "recorded-interviews",
        "content_sha256": "a" * 64,
    }


def _claim(claim_id: str, text: str, *, cited: bool = True) -> dict[str, object]:
    return {
        "claim_id": claim_id,
        "section": "CONCLUSION",
        "text": text,
        "status": "VERIFIED" if cited else "PENDING_VALIDATION",
        "decision_relevance": "CRITICAL" if cited else "CONTEXT",
        "citation_ids": ["citation-internal-1"] if cited else [],
        "score_bearing": cited,
    }


def _supervisor_document(comparison: dict[str, object] | None = None) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "2.0",
        "report_id": REPORT_ID,
        "run_id": RUN_ID,
        "project_id": PROJECT_ID,
        "product_version_id": VERSION_ID,
        "product_title": "校园证据助手",
        "source_sha256": "b" * 64,
        "top_card": {
            "potential_index": 68,
            "stage": "早期验证",
            "confidence_band": "MEDIUM",
            "evidence_coverage": 0.62,
            "recommendation": "VALIDATE_FURTHER",
        },
        "summary_claim_id": "claim-summary",
        "claims": [
            _claim("claim-summary", "目标学生的资料核对痛点真实存在。"),
            _claim("claim-pending", "校外市场规模仍待验证。", cited=False),
        ],
        "highlights": ["claim-summary"],
        "critical_issues": ["claim-pending"],
        "role_summaries": {"user": ["claim-summary"], "product": [], "investment": []},
        "cross_domain_claims": ["claim-summary"],
        "actions": [
            {
                "action_id": "action-1",
                "title": "补充续费验证",
                "owner": "项目负责人",
                "deadline_days": 14,
                "success_criteria": ["取得可核对的续费记录"],
                "failure_triggers": ["无续费"],
                "required_evidence": ["订单记录"],
                "related_claim_ids": ["claim-summary"],
            }
        ],
        "confidence_breakdown": {
            "profile_ref": "confidence:full-potential@2.0",
            "audited_evidence_quality": 0.65,
            "evidence_coverage": 0.62,
            "independent_source_support": 0.5,
            "freshness": 0.9,
            "cross_domain_agreement": 0.7,
            "unresolved_conflict_penalty": 0.1,
            "score": 0.64,
            "band": "MEDIUM",
        },
        "agent_report_cards": [
            {
                "agent_code": code,
                "report_id": f"{index:08d}-1111-4111-8111-111111111111",
                "title": title,
                "summary_claim_ids": ["claim-summary"],
                "source_sha256": "c" * 64,
            }
            for index, (code, title) in enumerate(AGENTS, start=1)
        ],
        "citations": [_citation()],
        "source_directory": [_source()],
        "audit_detail_ref": "evidence-auditor",
    }
    if comparison is not None:
        document["comparison"] = comparison
    return document


def _specialist_document(agent_code: str) -> dict[str, object]:
    report_id = next(
        card["report_id"]
        for card in _supervisor_document()["agent_report_cards"]
        if card["agent_code"] == agent_code
    )
    return {
        "schema_version": "2.0",
        "report_id": report_id,
        "run_id": RUN_ID,
        "project_id": PROJECT_ID,
        "product_version_id": VERSION_ID,
        "product_title": "校园证据助手",
        "agent_code": agent_code,
        "source_sha256": "c" * 64,
        "executive_summary": ["claim-summary"],
        "metrics": [{"key": "coverage", "label": "证据覆盖", "value": 62, "claim_ids": ["claim-summary"]}],
        "claims": [
            _claim("claim-summary", f"{agent_code} 已核对核心判断。"),
            _claim("claim-pending", "这一项仍需补充材料。", cited=False),
        ],
        "domain_payload": {"recorded": True},
        "risks": ["claim-pending"],
        "actions": [],
        "citations": [_citation()],
        "source_directory": [_source()],
        "audit_summary": {"verified": 1, "insufficient": 0, "needs_more": 1, "conflicted": 0},
        "raw_audit_refs": ["audit-recorded-v22"],
    }


def _supervisor_v3_document(comparison: dict[str, object] | None = None) -> dict[str, object]:
    document = _supervisor_document(comparison)
    document["schema_version"] = "3.0"
    document["locale"] = "zh-CN"
    document["dimension_scores"] = {
        "user_value": {
            "value": 72,
            "strength": "MODERATE",
            "evidence_level": "MEDIUM",
            "positive_driver_claim_ids": ["claim-summary"],
            "negative_driver_claim_ids": [],
            "pending_validation_claim_ids": ["claim-pending"],
        },
        "product_capability": {
            "value": 68,
            "strength": "MODERATE",
            "evidence_level": "MEDIUM",
            "positive_driver_claim_ids": ["claim-summary"],
            "negative_driver_claim_ids": [],
            "pending_validation_claim_ids": [],
        },
        "investment_potential": {
            "value": 54,
            "strength": "WEAK",
            "evidence_level": "LOW",
            "positive_driver_claim_ids": [],
            "negative_driver_claim_ids": ["claim-pending"],
            "pending_validation_claim_ids": ["claim-pending"],
        },
        "evidence_quality": {
            "value": 62,
            "strength": "MODERATE",
            "evidence_level": "MEDIUM",
            "positive_driver_claim_ids": ["claim-summary"],
            "negative_driver_claim_ids": [],
            "pending_validation_claim_ids": ["claim-pending"],
        },
    }
    document["evidence_coverage_profile"] = {
        "definition_version": "recorded-v3",
        "label": "EVIDENCE_COVERAGE",
        "required_dimensions": 4,
        "covered_dimensions": 3,
        "quality_note": "记录样例：仅验证确定性投影，不构成 Live 证据。",
        "independent_support_note": "记录样例：保留独立性与待验证边界。",
    }
    document["issue_priorities"] = [
        {"priority": "P0", "claim_id": "claim-pending", "decision_impact": "补充市场验证后复评"},
    ]
    return document


def _specialist_v3_document(agent_code: str) -> dict[str, object]:
    document = _specialist_document(agent_code)
    document["schema_version"] = "3.0"
    document["locale"] = "zh-CN"
    payloads = {
        "user-evidence": {
            "kind": "USER_EVIDENCE",
            "target_segments": ["学生与求职辅导用户"],
            "jobs_and_scenarios": ["核对申请材料"],
            "behavioral_evidence": ["Recorded fixture only"],
            "retention_and_payment": ["仍待验证"],
            "validation_plan": ["补充可审计续费记录"],
        },
        "product-engineering": {
            "kind": "PRODUCT_ENGINEERING",
            "stage_gate": ["早期验证"],
            "core_flows": ["材料核对流程"],
            "delivery_and_reliability": ["Recorded fixture only"],
            "dependencies_and_security": ["待审计"],
            "retest_gates": ["提供运行证据"],
        },
        "business-investment": {
            "kind": "BUSINESS_INVESTMENT",
            "business_model": ["待验证"],
            "unit_economics": ["待验证"],
            "competition_and_market": ["待验证"],
            "investment_gates": ["补充市场验证"],
            "compliance_scope": ["待审计"],
        },
        "evidence-auditor": {
            "kind": "EVIDENCE_AUDIT",
            "coverage_by_dimension": ["3/4 维度已覆盖"],
            "source_independence": ["Recorded fixture only"],
            "conflicts": ["无已审定冲突"],
            "calibration_decisions": ["待验证项不计分"],
            "evidence_gaps": ["市场规模"],
        },
    }
    document["domain_payload"] = payloads[agent_code]
    return document


def _projection(document: dict[str, object], *, specialist: bool = False) -> dict[str, object]:
    projection: dict[str, object] = {"view": "FULL", "created_at": "2026-08-13T00:00:00Z"}
    if specialist:
        projection["supervisor_report_id"] = REPORT_ID
    return {
        "report_schema_version": document["schema_version"],
        "document": document,
        "integrity": {"canonical_sha256": "d" * 64, "source_sha256": document["source_sha256"]},
        "projection": projection,
    }


def _install_report_routes(page: Page, state: dict[str, object]) -> None:
    context = page.context
    context.route("**/api/v1/demo/session", lambda route: route.fulfill(status=200, json={"valid": True}))
    def specialist_handler(agent_code: str):
        def fulfill(route: Route) -> None:
            route.fulfill(json=_projection(_specialist_document(agent_code), specialist=True))

        return fulfill

    context.route(
        f"**/api/v1/experience/v2/reports/{REPORT_ID}",
        lambda route: route.fulfill(json=_projection(_supervisor_document(state.get("comparison")))),
    )
    context.route(
        f"**/api/v1/public/demo/v2/reports/{REPORT_ID}?*",
        lambda route: route.fulfill(json=_projection(_supervisor_document(state.get("comparison")))),
    )
    context.route(f"**/api/v1/experience/v3/reports/{REPORT_ID}", lambda route: route.fulfill(status=404))
    context.route(f"**/api/v1/public/demo/v3/reports/{REPORT_ID}?*", lambda route: route.fulfill(status=404))
    for agent_code, _title in AGENTS:
        context.route(
            f"**/api/v1/experience/v3/runs/{RUN_ID}/agent-reports/{agent_code}",
            lambda route: route.fulfill(status=404),
        )
        context.route(
            f"**/api/v1/public/demo/v3/agent-reports/{agent_code}?*",
            lambda route: route.fulfill(status=404),
        )
        context.route(
            f"**/api/v1/experience/v2/runs/{RUN_ID}/agent-reports/{agent_code}",
            specialist_handler(agent_code),
        )
        context.route(
            f"**/api/v1/public/demo/v2/agent-reports/{agent_code}?*",
            specialist_handler(agent_code),
        )
    context.route(
        f"**/api/v1/public/demo/v2/evidence/{EVIDENCE_ID}/read-url?*",
        lambda route: route.fulfill(
            json={
                "evidence_id": EVIDENCE_ID,
                "run_id": RUN_ID,
                "sha256": "e" * 64,
                "mime_type": "text/plain",
                "read_url": f"{state['url']}/recorded-evidence/{EVIDENCE_ID}.txt",
                "expires_in_seconds": 900,
            }
        ),
    )
    context.route(f"**/recorded-evidence/{EVIDENCE_ID}.txt", lambda route: route.fulfill(body="recorded evidence"))


def _install_v3_report_routes(page: Page, state: dict[str, object]) -> None:
    context = page.context
    context.route("**/api/v1/demo/session", lambda route: route.fulfill(status=200, json={"valid": True}))
    def specialist_handler(agent_code: str):
        def fulfill(route: Route) -> None:
            route.fulfill(json=_projection(_specialist_v3_document(agent_code), specialist=True))

        return fulfill

    context.route(
        f"**/api/v1/experience/v3/reports/{REPORT_ID}",
        lambda route: route.fulfill(json=_projection(_supervisor_v3_document(state.get("comparison")))),
    )
    context.route(
        f"**/api/v1/public/demo/v3/reports/{REPORT_ID}?*",
        lambda route: route.fulfill(json=_projection(_supervisor_v3_document(state.get("comparison")))),
    )
    for agent_code, _title in AGENTS:
        context.route(
            f"**/api/v1/experience/v3/runs/{RUN_ID}/agent-reports/{agent_code}",
            specialist_handler(agent_code),
        )
        context.route(
            f"**/api/v1/public/demo/v3/agent-reports/{agent_code}?*",
            specialist_handler(agent_code),
        )


def _package_bytes() -> bytes:
    stream = io.BytesIO()
    names = ["项目负责人综合报告.pdf", *(title + ".pdf" for _code, title in AGENTS)]
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for name in names:
            archive.writestr(name, b"%PDF-recorded-v22")
        archive.writestr("来源目录.html", "<ol><li>已上传的访谈记录</li></ol>")
        archive.writestr("来源目录.json", json.dumps({"sources": [_source()]}, ensure_ascii=False))
        archive.writestr("manifest.json", json.dumps({"schema_version": "ReportPackageManifestV1"}))
    return stream.getvalue()


def _install_export_routes(page: Page, state: dict[str, object]) -> None:
    package = _package_bytes()

    def create_export(route: Route) -> None:
        request = route.request.post_data_json
        kind = request["kind"]
        state["last_export_request"] = request
        export_id = (
            "90909090-9090-4909-8909-909090909090"
            if kind == "PACKAGE"
            else "91919191-9191-4919-8919-919191919191"
        )
        route.fulfill(
            status=201,
            json={
                "export_id": export_id,
                "report_id": REPORT_ID,
                "run_id": RUN_ID,
                "kind": kind,
                "agent_code": request["agent_code"],
                "view": request["view"],
                "locale": request["locale"],
                "include_evidence": request["include_evidence"],
                "source_sha256": "d" * 64,
                "status": "COMPLETED",
                "object_key": "recorded/export",
                "sha256": "f" * 64,
                "size_bytes": len(package) if kind == "PACKAGE" else 20,
                "error_code": None,
            },
        )

    def read_url(route: Route) -> None:
        package_export = "90909090" in route.request.url
        payload = package if package_export else b"%PDF-recorded-v22"
        mime_type = "application/zip" if package_export else "application/pdf"
        route.fulfill(
            json={
                "export_id": route.request.url.split("/")[-2],
                "sha256": "f" * 64,
                "size_bytes": len(package) if package_export else 20,
                "read_url": f"data:{mime_type};base64,{b64encode(payload).decode()}",
            }
        )

    page.route(f"**/api/v1/experience/reports/{REPORT_ID}/exports", create_export)
    page.route("**/api/v1/experience/report-exports/*/read-url", read_url)
    page.route(
        "**/recorded-export/report.pdf",
        lambda route: route.fulfill(
            content_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="report.pdf"'},
            body=b"%PDF-recorded-v22",
        ),
    )
    page.route(
        "**/recorded-export/package.zip",
        lambda route: route.fulfill(
            content_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="package.zip"'},
            body=package,
        ),
    )


def test_recorded_v22_comparison_reports_public_evidence_and_exports(recorded_page: Any, tmp_path) -> None:
    page, url = recorded_page
    browser_errors = _capture_browser_errors(page)
    state: dict[str, object] = {"url": url, "comparison": None}
    page.route("**/api/v1/demo/session", lambda route: route.fulfill(status=200, json={"valid": True}))
    _install_report_routes(page, state)
    _install_export_routes(page, state)
    page.set_viewport_size({"width": 1440, "height": 900})

    page.goto(f"{url}/reports/{REPORT_ID}")
    expect(page.get_by_text("爆款潜力指数", exact=True)).to_be_visible()
    expect(page.get_by_text("相比上一次", exact=True)).to_have_count(0)
    expect(page.get_by_text("待验证 · 不参与评分，也不支撑主要建议", exact=False)).to_be_visible()
    expect(page.locator(".report-v22-agent-reports li")).to_have_count(4)

    state["comparison"] = {
        "schema_version": "1.0",
        "status": "COMPARABLE",
        "index_before": 61,
        "index_after": 68,
        "index_delta": 7,
        "dimension_deltas": [{"dimension": "user_value", "before": 58, "after": 70, "delta": 12}],
        "resolved_issues": [],
        "unchanged_issues": [],
        "new_risks": [],
        "evidence_upgrades": [],
        "evidence_downgrades": [],
        "change_reason_claim_ids": ["claim-summary"],
    }
    page.reload()
    compared = page.locator(".report-v22-comparison")
    expect(compared).to_contain_text("61 → 68")
    expect(compared).to_contain_text("+7")
    expect(page.get_by_text("证据支持", exact=True)).to_be_visible()
    expect(page.get_by_text("关键判断", exact=True)).to_be_visible()
    expect(page.get_by_text("背景信息", exact=True)).to_be_visible()
    expect(page.get_by_text("用户价值", exact=True)).to_have_count(2)
    for raw_label in ("VERIFIED", "CRITICAL", "PENDING VALIDATION", "CONTEXT", "user_value"):
        expect(page.get_by_text(raw_label, exact=True)).to_have_count(0)
    stage_box = page.get_by_text("阶段", exact=True).bounding_box()
    comparison_box = page.get_by_text("相比上一次", exact=True).bounding_box()
    confidence_box = page.get_by_text("可信度", exact=True).bounding_box()
    assert stage_box and comparison_box and confidence_box
    assert stage_box["y"] < comparison_box["y"] < confidence_box["y"]
    artifact_dir = Path("output/playwright")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=artifact_dir / "report-v22-comparison.png", full_page=True)

    state["comparison"] = {
        "schema_version": "1.0",
        "status": "STANDARD_CHANGED",
        "resolved_issues": [],
        "unchanged_issues": [],
        "new_risks": [],
        "evidence_upgrades": [],
        "evidence_downgrades": [],
        "change_reason_claim_ids": [],
    }
    page.reload()
    expect(page.get_by_text("评估标准已变化，因此不展示指数差值。", exact=True)).to_be_visible()
    expect(page.locator(".report-v22-comparison")).not_to_contain_text("→")

    state["comparison"] = None
    page.reload()
    expect(page.get_by_text("相比上一次", exact=True)).to_have_count(0)

    first_card = page.locator(".report-v22-agent-reports a").first
    with page.expect_popup() as specialist_popup:
        first_card.click()
    specialist_page = specialist_popup.value
    expect(specialist_page).to_have_url(f"{url}/runs/{RUN_ID}/agent-reports/user-evidence")
    checksum = specialist_page.locator(".specialist-v22-document").get_attribute("data-content-sha256")
    expect(specialist_page.locator(".specialist-v22-claim")).to_have_count(1)
    specialist_page.locator('[data-report-view="full"]').click()
    expect(specialist_page.locator(".specialist-v22-claim")).to_have_count(2)
    assert specialist_page.locator(".specialist-v22-document").get_attribute("data-content-sha256") == checksum
    specialist_page.locator(f'a[href="/reports/{REPORT_ID}#agent-reports"]').click()
    expect(specialist_page).to_have_url(f"{url}/reports/{REPORT_ID}#agent-reports")
    specialist_page.close()

    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.set_viewport_size({"width": 1440, "height": 900})

    page.goto(f"{url}/shared/demo/{TOKEN}/reports/{REPORT_ID}")
    expect(page.locator(".report-v22-agent-reports li")).to_have_count(4)
    with page.expect_popup() as public_specialist_popup:
        page.locator(".report-v22-agent-reports a").first.click()
    public_specialist_page = public_specialist_popup.value
    expect(public_specialist_page).to_have_url(f"{url}/shared/demo/{TOKEN}/runs/{RUN_ID}/agent-reports/user-evidence")
    public_specialist_page.close()
    page.locator(".inline-citation summary").first.click()
    page.get_by_role("link", name="查看证据").first.click()
    expect(page).to_have_url(f"{url}/shared/demo/{TOKEN}/runs/{RUN_ID}/evidence/{EVIDENCE_ID}")
    expect(page.get_by_role("link", name="下载证据原件")).to_be_visible()
    page.screenshot(path=artifact_dir / "report-v22-public-evidence.png", full_page=True)
    page.goto(f"{url}/shared/demo/{TOKEN}/runs/{RUN_ID}/agent-reports/evidence-auditor")
    expect(page.locator(".specialist-v22-document")).to_have_attribute("data-content-sha256", "d" * 64)

    page.goto(f"{url}/reports/{REPORT_ID}")
    with page.expect_download() as pdf_download:
        page.get_by_role("button", name="导出 PDF").click()
    assert pdf_download.value.suggested_filename.endswith(".pdf")
    page.get_by_role("checkbox", name="同时下载证据原件").check()
    with page.expect_download() as package_download:
        page.get_by_role("button", name="一键下载完整报告包").click()
    package_path = tmp_path / "report-package.zip"
    package_download.value.save_as(package_path)
    with ZipFile(package_path) as archive:
        names = set(archive.namelist())
    assert len([name for name in names if name.endswith(".pdf")]) == 5
    assert {"来源目录.html", "来源目录.json", "manifest.json"} <= names
    assert state["last_export_request"]["include_evidence"] is True
    unexpected_browser_errors = [
        error
        for error in browser_errors
        if "Failed to load resource: the server responded with a status of 404" not in error
    ]
    assert unexpected_browser_errors == []


def test_recorded_v3_institutional_reports_are_responsive_printable_and_run_scoped(recorded_page: Any) -> None:
    page, url = recorded_page
    browser_errors = _capture_browser_errors(page)
    state: dict[str, object] = {
        "comparison": {
            "schema_version": "1.0",
            "status": "COMPARABLE",
            "index_before": 61,
            "index_after": 68,
            "index_delta": 7,
            "dimension_deltas": [{"dimension": "user_value", "before": 58, "after": 72, "delta": 14}],
            "resolved_issues": ["记录样例行动已清偿"],
            "unchanged_issues": ["市场规模仍待验证"],
            "new_risks": ["Recorded 样例不可作为 Live 证明"],
            "evidence_upgrades": [],
            "evidence_downgrades": [],
            "change_reason_claim_ids": ["claim-summary"],
        }
    }
    page.route("**/api/v1/demo/session", lambda route: route.fulfill(status=200, json={"valid": True}))
    _install_v3_report_routes(page, state)
    page.set_viewport_size({"width": 1440, "height": 900})

    page.goto(f"{url}/reports/{REPORT_ID}")
    expect(page.locator(".institutional-report")).to_be_visible()
    expect(page.get_by_text("投资决策尽调报告", exact=True)).to_be_visible()
    expect(page.locator(".institutional-table")).to_have_count(1)
    expect(page.locator(".institutional-delta")).to_contain_text("61 → 68")
    expect(page.locator(".institutional-action-card")).to_have_count(1)
    first_card = page.locator(".report-v22-agent-reports a").first
    assert first_card.get_attribute("target") == "_blank"
    assert first_card.get_attribute("rel") == "noopener noreferrer"
    artifact_dir = Path("output/playwright")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=artifact_dir / "report-v3-institutional-desktop.png", full_page=True)

    with page.expect_popup() as specialist_popup:
        first_card.click()
    specialist_page = specialist_popup.value
    expect(specialist_page).to_have_url(f"{url}/runs/{RUN_ID}/agent-reports/user-evidence")
    expect(specialist_page.locator(".institutional-specialist-document")).to_be_visible()
    expect(specialist_page.locator(".institutional-table")).to_have_count(1)
    specialist_page.close()

    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=artifact_dir / "report-v3-institutional-mobile.png", full_page=True)
    page.emulate_media(media="print")
    institutional_actions_display = page.locator(".institutional-report-actions").evaluate(
        "element => getComputedStyle(element).display"
    )
    assert institutional_actions_display == "none"
    assert page.locator(".topbar").evaluate("element => getComputedStyle(element).display") == "none"
    page.screenshot(path=artifact_dir / "report-v3-institutional-print.png", full_page=True)
    page.emulate_media(media="screen")

    page.goto(f"{url}/shared/demo/{TOKEN}/reports/{REPORT_ID}")
    expect(page.locator(".institutional-report")).to_be_visible()
    with page.expect_popup() as public_specialist_popup:
        page.locator(".report-v22-agent-reports a").first.click()
    public_specialist_page = public_specialist_popup.value
    expect(public_specialist_page).to_have_url(f"{url}/shared/demo/{TOKEN}/runs/{RUN_ID}/agent-reports/user-evidence")
    expect(public_specialist_page.locator(".institutional-specialist-document")).to_be_visible()
    public_specialist_page.close()
    assert browser_errors == []


def test_recorded_v22_disclosure_is_committed_once_before_upload(recorded_page: Any) -> None:
    page, url = recorded_page
    browser_errors = _capture_browser_errors(page)
    state = {"accepted": False, "accept_calls": 0, "upload_init_calls": 0}
    page.route("**/api/v1/demo/session", lambda route: route.fulfill(status=200, json={"valid": True}))
    page.route(
        "**/api/v1/projects",
        lambda route: route.fulfill(
            json={
                "items": [
                    {
                        "project_id": PROJECT_ID,
                        "workspace_id": "89898989-8989-4898-8898-898989898989",
                        "name": "校园证据助手",
                        "status": "ACTIVE",
                    }
                ]
            }
        ),
    )
    page.route(f"**/api/v1/projects/{PROJECT_ID}/runs", lambda route: route.fulfill(json={"items": []}))
    page.route(
        f"**/api/v1/projects/{PROJECT_ID}/versions",
        lambda route: route.fulfill(status=201, json={"product_version_id": VERSION_ID}),
    )

    def disclosure(route: Route) -> None:
        if route.request.method == "POST":
            state["accepted"] = True
            state["accept_calls"] += 1
            route.fulfill(
                json={
                    "product_version_id": VERSION_ID,
                    "policy_version": "public-demo-evidence-v1",
                    "accepted": True,
                    "acceptance_id": str(uuid4()),
                    "accepted_at": "2026-08-13T00:00:00Z",
                }
            )
            return
        route.fulfill(
            json={
                "product_version_id": VERSION_ID,
                "policy_version": "public-demo-evidence-v1",
                "accepted": state["accepted"],
                "acceptance_id": str(uuid4()) if state["accepted"] else None,
                "accepted_at": "2026-08-13T00:00:00Z" if state["accepted"] else None,
            }
        )

    page.route(f"**/api/v1/product-versions/{VERSION_ID}/public-demo-disclosure*", disclosure)

    def initiate(route: Route) -> None:
        state["upload_init_calls"] += 1
        material_id = str(uuid4())
        route.fulfill(
            status=201,
            json={
                "material_id": material_id,
                "upload_url": f"{url}/recorded-upload/{material_id}",
            },
        )

    page.route(f"**/api/v1/product-versions/{VERSION_ID}/materials:initiate*", initiate)
    page.route("**/recorded-upload/*", lambda route: route.fulfill(status=200))
    def complete(route: Route) -> None:
        material_id = route.request.url.split("/materials/")[1].split("/")[0]
        route.fulfill(
            json={
                "material_id": material_id,
                "status": "AVAILABLE",
                "object_key": "recorded/material.txt",
                "sha256": "a" * 64,
            }
        )

    page.route("**/api/v1/materials/*/complete*", complete)
    page.route(
        f"**/api/v1/product-versions/{VERSION_ID}/material-analyses",
        lambda route: route.fulfill(json={"product_version_id": VERSION_ID, "items": []}),
    )

    page.goto(f"{url}/projects/{PROJECT_ID}/new-evaluation")
    chooser = page.locator('input[type="file"]').first
    expect(chooser).to_be_attached()
    chooser.set_input_files({"name": "first.txt", "mimeType": "text/plain", "buffer": b"first"})
    expect(page.get_by_role("dialog")).to_be_visible()
    artifact_dir = Path("output/playwright")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=artifact_dir / "report-v22-public-disclosure.png", full_page=True)
    assert state["upload_init_calls"] == 0
    page.get_by_role("button", name="我已了解，继续上传").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    _wait_until(page, lambda: state["upload_init_calls"] == 1)
    expect(page.get_by_text("first.txt", exact=True)).to_be_visible()
    expect(page.get_by_text("已上传", exact=True)).to_be_visible()
    chooser = page.locator('input[type="file"]').first
    expect(chooser).to_be_attached()
    chooser.set_input_files({"name": "second.txt", "mimeType": "text/plain", "buffer": b"second"})
    expect(page.get_by_text("second.txt", exact=True)).to_be_visible()
    _wait_until(page, lambda: state["upload_init_calls"] == 2)
    expect(page.get_by_role("dialog")).to_have_count(0)
    assert state["accept_calls"] == 1
    page.screenshot(path=artifact_dir / "report-v22-disclosure-uploaded.png", full_page=True)
    assert browser_errors == []
