from __future__ import annotations

import json
import os
from typing import Any

import pytest
from playwright.sync_api import Page, Route, expect, sync_playwright

RUN_ID = "11111111-1111-4111-8111-111111111111"
PROJECT_ID = "22222222-2222-4222-8222-222222222222"
VERSION_ID = "33333333-3333-4333-8333-333333333333"
REPORT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


@pytest.fixture
def recorded_page() -> Any:
    url = os.getenv("LAUNCHSCOPE_WEB_E2E_URL")
    if not url:
        pytest.skip("LAUNCHSCOPE_WEB_E2E_URL is required for Recorded browser acceptance")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(locale="zh-CN")
        context.add_cookies([{"name": "launchscope.locale", "value": "zh-CN", "url": url}])
        context.add_init_script(
            "localStorage.setItem('launchscope.demo.session.v1', "
            + json.dumps(
                json.dumps(
                    {
                        "schemaVersion": "launchscope.demo.session.v1",
                        "tenantId": "44444444-4444-4444-8444-444444444444",
                        "workspaceId": "55555555-5555-4555-8555-555555555555",
                        "actorId": "recorded-browser",
                        "displayName": "Recorded Browser",
                        "createdAt": "2026-08-11T00:00:00Z",
                    }
                )
            )
            + "); localStorage.setItem('launchscope.locale', 'zh-CN'); "
            + "document.cookie = 'launchscope.locale=zh-CN; Path=/';"
        )
        page = context.new_page()
        page.route("**/api/v1/demo/session", lambda route: route.fulfill(json={"status": "ok"}))
        yield page, url
        context.close()
        browser.close()


def _run(values: dict[str, object] | None = None) -> dict[str, object]:
    document: dict[str, object] = {
        "run_id": RUN_ID,
        "project_id": PROJECT_ID,
        "product_version_id": VERSION_ID,
        "status": "PLANNED",
        "standard_version": "1.0",
        "current_cursor": "event.initial",
        "correlation_id": "66666666-6666-4666-8666-666666666666",
        "current_stage": None,
        "attention_reason": None,
        "updated_at": "2026-08-11T00:00:00Z",
        "ui_mode": "SUPERVISOR_1P4",
        "architecture_generation": "supervisor-1p4-v1",
        "experience_stage": {
            "ordinal": 1,
            "code": "UNDERSTANDING",
            "label": "正在理解需求",
            "exception": None,
            "exception_label": None,
        },
    }
    document.update(values or {})
    return document


def _install_run(page: Page, document: dict[str, object], *, with_tasks: bool = False) -> list[dict[str, object]]:
    page.route(f"**/api/v1/runs/{RUN_ID}", lambda route: route.fulfill(json=document))
    page.route(f"**/api/v1/runs/{RUN_ID}/events**", lambda route: route.abort())
    page.route(
        f"**/api/v1/runs/{RUN_ID}/clarifications",
        lambda route: route.fulfill(json={"run_id": RUN_ID, "items": []}),
    )
    tasks = []
    if with_tasks:
        tasks = [
            {
                "id": "77777777-7777-4777-8777-777777777777",
                "stage_code": "DOMAIN_REVIEW",
                "agent_identity_ref": "user-evidence@4.0",
                "status": "RUNNING",
                "tool_allowlist": [],
                "evidence_count": 2,
            }
        ]
    page.route(
        f"**/api/v1/experience/runs/{RUN_ID}/agentteams",
        lambda route: route.fulfill(
            json={
                "run_id": RUN_ID,
                "team": {
                    "name": "launchscope-potential-review-v4",
                    "agentteams_version": "v1.2.0",
                    "binding_status": "RECORDED",
                },
                "stages": [],
                "tasks": tasks,
                "handoff_count": 0,
                "matrix_event_count": 0,
            }
        ),
    )
    messages: list[dict[str, object]] = []
    channels = [
        {
            "channel": channel,
            "status": "RUNNING" if with_tasks else str(document["status"]),
            "evidence_count": 2 if with_tasks and channel in {"supervisor", "user-evidence"} else 0,
            "pending_count": 0,
            "summary": "",
        }
        for channel in ("supervisor", "user-evidence", "product-engineering", "business-investment")
    ]

    def conversations(route: Route) -> None:
        route.fulfill(json={"run_id": RUN_ID, "channels": channels, "messages": messages, "next_cursor": None})

    def submit_conversation(route: Route) -> None:
        channel = route.request.url.split("/conversations/", 1)[1].split("/", 1)[0]
        payload = route.request.post_data_json
        text = str(payload["message"])
        waiting = "另一个想法" in text
        message_id = f"88888888-8888-4888-8888-{len(messages) + 1:012d}"
        messages.append(
            {
                "message_id": message_id,
                "channel": channel,
                "role": "USER",
                "kind": "MESSAGE",
                "text": text,
                "route_state": "WAITING_FOR_USER" if waiting else "ROUTED",
                "affected_task_ids": [],
                "created_at": "2026-08-11T00:00:00Z",
            }
        )
        questions = ["主要目标用户是谁？", "本轮要支持什么决定？"] if waiting else []
        if questions:
            messages.append(
                {
                    "message_id": f"99999999-9999-4999-8999-{len(messages) + 1:012d}",
                    "channel": channel,
                    "role": "SUPERVISOR",
                    "kind": "QUESTION",
                    "text": "\n".join(questions),
                    "route_state": "WAITING_FOR_USER",
                    "affected_task_ids": [],
                    "created_at": "2026-08-11T00:00:01Z",
                }
            )
        route.fulfill(
            json={
                "message_id": message_id,
                "run_id": RUN_ID,
                "channel": channel,
                "route_state": "WAITING_FOR_USER" if waiting else "ROUTED",
                "affected_task_ids": [],
                "questions": questions,
                "duplicate": False,
            }
        )

    page.route(f"**/api/v1/runs/{RUN_ID}/conversations?*", conversations)
    page.route(f"**/api/v1/runs/{RUN_ID}/conversations/*/messages", submit_conversation)
    return messages


def _submit_chat(page: Page, message: str) -> None:
    if page.locator(".conversation-drawer").count() == 0:
        page.get_by_role("tab", name="主管").click()
    drawer = page.locator(".conversation-drawer")
    drawer.get_by_label("发给主管的消息").fill(message)
    drawer.get_by_role("checkbox").check()
    drawer.get_by_role("button", name="发送", exact=True).click()


def test_recorded_route_roles_converge_on_one_intake(recorded_page: Any) -> None:
    page, url = recorded_page
    page.set_viewport_size({"width": 1440, "height": 900})
    page.set_default_navigation_timeout(60_000)
    projects = [
        {
            "project_id": PROJECT_ID,
            "workspace_id": "55555555-5555-4555-8555-555555555555",
            "name": "尚未评审的草稿项目",
            "status": "ACTIVE",
        }
    ]
    create_requests: list[dict[str, object]] = []

    def project_api(route: Route) -> None:
        if route.request.method == "POST":
            create_requests.append(route.request.post_data_json)
            route.fulfill(json={"project_id": PROJECT_ID})
            return
        route.fulfill(json={"items": projects})

    page.route("**/api/v1/projects", project_api)
    history = [
        {
            "run_id": f"11111111-1111-4111-8111-{index:012d}",
            "project_id": PROJECT_ID,
            "project_name": "CreaTrades clarification acceptance",
            "product_version_label": f"V{12 - index}",
            "product_version_number": 12 - index,
            "status": "COMPLETED" if index % 2 else "PLANNED",
            "updated_at": "2026-08-13T00:00:00Z",
        }
        for index in range(6)
    ]
    page.route(
        "**/api/v1/experience/history?*",
        lambda route: route.fulfill(json={"items": history, "has_more": False, "total": len(history)}),
    )

    for path in ("/", f"/projects/{PROJECT_ID}/new-evaluation"):
        page.goto(f"{url}{path}", wait_until="domcontentloaded")
        expect(page.locator(".evaluation-wheel")).to_have_count(1)
        expect(page.locator(".evaluation-wheel .wheel-dimension")).to_have_count(3)

    page.goto(f"{url}/")
    expect(page.locator("a.landing-brand")).to_have_attribute("href", "/")
    expect(page.locator('.landing-menu-list a[href="/projects"]')).to_have_count(1)
    expect(page.locator(".bead-side-title")).to_have_count(2)
    expect(page.locator(".bead-side .history-bead")).to_have_count(6)
    history_action = page.locator(".landing-history-action")
    expect(history_action).to_be_visible()
    action_box = history_action.bounding_box()
    right_title_box = page.locator(".bead-side-right .bead-side-title").bounding_box()
    assert action_box is not None and right_title_box is not None and action_box["y"] < right_title_box["y"]
    page.set_viewport_size({"width": 1366, "height": 768})
    assert page.evaluate("document.documentElement.scrollHeight <= window.innerHeight + 1")

    page.locator(".landing-menu summary").click()
    page.get_by_label("语言").select_option("en")
    expect(page.locator('.wheel-sector[data-wrapped="true"]')).to_have_count(2)
    core_box = page.locator(".wheel-core-button").bounding_box()
    right_label_box = page.locator(".wheel-sector").nth(1).locator(".wheel-sector-name").bounding_box()
    left_label_box = page.locator(".wheel-sector").nth(3).locator(".wheel-sector-name").bounding_box()
    assert core_box is not None and right_label_box is not None and left_label_box is not None
    assert right_label_box["x"] >= core_box["x"] + core_box["width"]
    assert left_label_box["x"] + left_label_box["width"] <= core_box["x"]
    page.get_by_label("Language").select_option("zh-CN")

    page.goto(f"{url}/projects")
    expect(page).to_have_url(f"{url}/projects")
    expect(page.locator(".evaluation-wheel")).to_have_count(0)
    expect(page.locator(".project-archive")).to_be_visible()
    expect(page.get_by_text("尚未评审的草稿项目")).to_be_visible()
    expect(page.locator("a.brand")).to_have_attribute("href", "/")
    expect(page.locator('.topnav a[href="/projects"]')).to_be_visible()
    expect(page.locator('.topnav a[href="/?start=1"]')).to_be_visible()
    page.get_by_label("语言").select_option("en")
    expect(page.get_by_role("heading", name="Projects in motion.")).to_be_visible()
    page.get_by_label("Language").select_option("zh-CN")
    expect(page.get_by_role("heading", name="推进中的项目。")).to_be_visible()

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{url}/")
    expect(page.locator(".evaluation-wheel")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.goto(f"{url}/projects")
    expect(page.locator(".project-archive")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.set_viewport_size({"width": 1440, "height": 900})

    page.goto(f"{url}/projects/new")
    expect(page).to_have_url(f"{url}/?start=1")
    expect(page.locator(".evaluation-wheel")).to_have_count(1)
    expect(page.locator(".dock-panel")).to_be_visible()
    project_name = page.get_by_label("产品名称")
    expect(project_name).to_be_focused()
    page.get_by_role("button", name="返回").click()
    expect(page).to_have_url(f"{url}/")
    expect(page.locator(".dock-panel")).to_have_count(0)

    page.goto(f"{url}/?start=1")
    project_name.fill("统一入口测试项目")
    page.get_by_role("button", name="下一步：填资料").click()
    expect(page).to_have_url(f"{url}/projects/{PROJECT_ID}/new-evaluation")
    assert len(create_requests) == 1


def test_recorded_product_flow_keeps_one_v4_wheel_until_the_report(recorded_page: Any) -> None:
    page, url = recorded_page
    page.set_viewport_size({"width": 1440, "height": 900})
    _install_run(
        page,
        _run(
            {
                "status": "RUNNING",
                "current_stage": "DOMAIN_REVIEW",
                "experience_stage": {
                    "ordinal": 2,
                    "code": "MULTI_REVIEW",
                    "label": "多维评审",
                    "exception": None,
                    "exception_label": None,
                },
            }
        ),
        with_tasks=True,
    )
    page.goto(f"{url}/runs/{RUN_ID}")
    command_bar = page.locator(".supervisor-command-bar").bounding_box()
    assert command_bar is not None and command_bar["height"] <= 104
    expect(page.locator(".evaluation-wheel")).to_have_count(1)
    expect(page.locator(".evaluation-wheel .wheel-dimension")).to_have_count(3)
    expect(page.locator(".conversation-tab")).to_have_count(4)
    expect(page.get_by_role("tab", name="主管")).to_be_visible()
    expect(page.get_by_role("tab", name="用户")).to_be_visible()
    expect(page.get_by_role("tab", name="产品")).to_be_visible()
    expect(page.get_by_role("tab", name="商业")).to_be_visible()
    page.get_by_role("tab", name="主管").focus()
    page.keyboard.press("ArrowRight")
    expect(page.get_by_role("tab", name="用户")).to_be_focused()
    before = page.locator(".evaluation-wheel").bounding_box()
    page.get_by_role("tab", name="产品").click()
    expect(page.locator(".conversation-drawer")).to_be_visible()
    after = page.locator(".evaluation-wheel").bounding_box()
    assert before == after
    page.keyboard.press("Escape")
    expect(page.locator(".conversation-drawer")).to_have_count(0)
    expect(page.get_by_role("tab", name="产品")).to_be_focused()
    page.set_viewport_size({"width": 2048, "height": 1152})
    command_bar_wide = page.locator(".supervisor-command-bar").bounding_box()
    assert command_bar_wide is not None and command_bar_wide["height"] <= 104
    page.set_viewport_size({"width": 390, "height": 844})
    mobile_tab = page.get_by_role("tab", name="商业").bounding_box()
    assert mobile_tab is not None and abs(mobile_tab["y"] + mobile_tab["height"] - 844) < 1
    page.emulate_media(reduced_motion="reduce")
    page.get_by_role("tab", name="商业").click()
    sheet = page.locator(".conversation-drawer")
    expect(sheet).to_be_visible()
    sheet_box = sheet.bounding_box()
    assert sheet_box is not None and sheet_box["width"] == 390 and sheet_box["y"] > 0
    assert sheet.evaluate("element => getComputedStyle(element).animationName") == "none"


def test_recorded_clear_and_ambiguous_supervisor_intake(recorded_page: Any) -> None:
    page, url = recorded_page
    _install_run(page, _run())
    calls = {"dispatch": 0}

    def dispatch(route: Route) -> None:
        calls["dispatch"] += 1
        route.fulfill(
            json={"run_id": RUN_ID, "status": "RUNNING", "manifest_sha256": "a" * 64, "task_count": 1}
        )

    page.route(f"**/api/v1/runs/{RUN_ID}/dispatch", dispatch)
    page.goto(f"{url}/runs/{RUN_ID}")
    expect(page.locator(".evaluation-wheel")).to_have_attribute("data-motion-state", "PLANNED")
    expect(page.get_by_text("四阶段预测", exact=True)).to_be_visible()
    for label in ("正在了解项目", "多维预测", "核对证据并生成结果", "预测已完成"):
        expect(page.locator(".supervisor-progress").get_by_text(label, exact=True)).to_be_visible()
    expect(page.locator(".agent-rail")).to_have_count(0)
    expect(page.locator(".conversation-tab")).to_have_count(4)
    assert page.locator(".supervisor-process-details").get_attribute("open") is None
    _submit_chat(page, "评估香港大学生的校园工具，判断是否继续投入")
    expect(page.get_by_text("已记录，并路由到相关的待执行任务")).to_be_visible()
    assert calls["dispatch"] == 0

    _submit_chat(page, "再帮我看看另一个想法")
    expect(page.get_by_text("主要目标用户是谁？", exact=False)).to_be_visible()


def test_recorded_runtime_supplement_and_needs_attention(recorded_page: Any) -> None:
    page, url = recorded_page
    running = _run(
        {
            "status": "RUNNING",
            "current_stage": "DOMAIN_REVIEW",
            "experience_stage": {
                "ordinal": 2,
                "code": "MULTI_REVIEW",
                "label": "多维评审",
                "exception": None,
                "exception_label": None,
            },
        }
    )
    _install_run(page, running, with_tasks=True)
    dispatch_calls = {"count": 0}
    page.route(
        f"**/api/v1/runs/{RUN_ID}/dispatch",
        lambda route: (dispatch_calls.__setitem__("count", dispatch_calls["count"] + 1), route.fulfill(json={})),
    )
    page.goto(f"{url}/runs/{RUN_ID}")
    expect(page.locator(".evaluation-wheel")).to_have_attribute("data-motion-state", "RUNNING")
    expect(page.locator(".evaluation-wheel .rete")).to_have_attribute("transform", "rotate(22.5)")
    _submit_chat(page, "补充：我们已经有 12 名香港学生访谈记录")
    expect(page.get_by_text("已记录，并路由到相关的待执行任务")).to_be_visible()
    assert dispatch_calls["count"] == 0

    page.unroute(f"**/api/v1/runs/{RUN_ID}")
    attention = _run(
        {
            "status": "NEEDS_ATTENTION",
            "current_stage": "NEEDS_ATTENTION",
            "experience_stage": {
                "ordinal": 2,
                "code": "MULTI_REVIEW",
                "label": "多维评审",
                "exception": "NEEDS_CONFIRMATION",
                "exception_label": "需要确认",
            },
        }
    )
    page.route(f"**/api/v1/runs/{RUN_ID}", lambda route: route.fulfill(json=attention))
    page.reload()
    expect(page.locator(".evaluation-wheel")).to_have_attribute("data-motion-state", "ATTENTION")
    expect(page.get_by_role("heading", name="需要确认")).to_be_visible()
    expect(page.get_by_text("预测已安全暂停", exact=False)).to_be_visible()


def test_recorded_layered_report_and_legacy_compatibility(recorded_page: Any) -> None:
    page, url = recorded_page
    report_calls = {"catalog": 0, "body": 0}
    page.route(
        f"**/api/v1/experience/reports/{REPORT_ID}",
        lambda route: route.fulfill(
            json={
                "report_id": REPORT_ID,
                "run_id": RUN_ID,
                "project_id": PROJECT_ID,
                "decision_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "recommendation": "VALIDATE_FURTHER",
                "standard_version": "1.0",
                "dimension_grades": {},
                "blocking_reasons": [],
                "action_items": ["完成 12 次目标用户访谈"],
                "created_at": "2026-08-11T00:00:00Z",
                "evidence_chain": [],
                "calibration_results": [],
                "architecture_generation": "supervisor-1p4-v1",
                "deterministic_score": {
                    "score": 68.5,
                    "coverage": 0.67,
                    "recommendation": "VALIDATE_FURTHER",
                    "dimension_scores": {"user": 75, "evidence_quality": 80},
                    "caps_applied": ["low_coverage:VALIDATE_FURTHER"],
                    "missing_agents": ["business-investment"],
                },
                "layered_report": {
                    "summary": "用户痛点明确，但商业证据仍需补齐。",
                    "actions": ["完成 12 次目标用户访谈"],
                    "largest_opportunity": "校园协作场景频率高。",
                    "largest_risk": "缺少付费意愿证据。",
                    "coverage": 0.67,
                    "confidence": 0.8,
                    "information_gaps": ["business-investment"],
                    "conflicts": [],
                    "cross_domain_analysis": ["用户需求与产品可行性一致。"],
                    "citations": [],
                    "version_changes": {},
                    "decision_conflict": False,
                    "synthesis_status": "ACCEPTED",
                },
            }
        ),
    )
    page.route("**/api/v1/experience/projects/**/compare/**", lambda route: route.fulfill(json={}))
    def report_catalog(route: Route) -> None:
        report_calls["catalog"] += 1
        route.fulfill(
            json={
                "run_id": RUN_ID,
                "reports": [
                    {
                        "agent_code": code,
                        "title": title,
                        "kind": "AUDIT" if code == "evidence-auditor" else "DOMAIN",
                        "status": "AVAILABLE" if code == "user-evidence" else "PENDING",
                        "sha256": "c" * 64 if code == "user-evidence" else None,
                        "created_at": "2026-08-11T00:00:00Z" if code == "user-evidence" else None,
                        "revision": 0 if code == "user-evidence" else None,
                        "failure_reason": None,
                    }
                    for code, title in (
                        ("user-evidence", "User evidence report"),
                        ("product-engineering", "Product engineering report"),
                        ("business-investment", "Business investment report"),
                        ("evidence-auditor", "Evidence audit report"),
                    )
                ],
            }
        )

    def report_body(route: Route) -> None:
        report_calls["body"] += 1
        route.fulfill(
            json={
                "run_id": RUN_ID,
                "agent_code": "user-evidence",
                "title": "User evidence report",
                "kind": "DOMAIN",
                "sha256": "c" * 64,
                "mime_type": "application/json",
                "created_at": "2026-08-11T00:00:00Z",
                "audit_round": None,
                "format": "json",
                "content": '{"finding":"recorded and traceable"}',
            }
        )

    page.route(f"**/api/v1/experience/runs/{RUN_ID}/agent-reports", report_catalog)
    page.route(f"**/api/v1/experience/runs/{RUN_ID}/agent-reports/user-evidence", report_body)
    page.goto(f"{url}/reports/{REPORT_ID}")
    expect(page.get_by_role("heading", name="这个产品，有机会成为爆款吗？")).to_be_visible()
    expect(page.get_by_text("校园协作场景频率高。")).to_be_visible()
    expect(page.get_by_text("缺少付费意愿证据。")).to_be_visible()
    expect(page.locator(".evaluation-wheel")).to_have_count(0)
    assert page.locator(".supervisor-report-process").get_attribute("open") is None
    assert report_calls == {"catalog": 0, "body": 0}
    page.locator(".agent-reports-panel > summary").click()
    expect(page.locator(".agent-report-list")).to_be_visible()
    assert report_calls == {"catalog": 1, "body": 0}
    page.locator(".agent-report-list button").first.click()
    expect(page.locator(".agent-report-drawer")).to_contain_text("recorded and traceable")
    assert report_calls == {"catalog": 1, "body": 1}

    legacy = _run(
        {
            "status": "RUNNING",
            "current_stage": "DOMAIN_REVIEW",
            "ui_mode": "LEGACY",
            "architecture_generation": "legacy-1p5",
            "experience_stage": {
                "ordinal": 2,
                "code": "MULTI_REVIEW",
                "label": "多维评审",
                "exception": None,
                "exception_label": None,
            },
        }
    )
    _install_run(page, legacy, with_tasks=True)
    page.goto(f"{url}/runs/{RUN_ID}")
    expect(page.locator(".evaluation-wheel")).to_be_visible()
    expect(page.locator(".agent-rail")).to_be_visible()
    expect(page.locator(".supervisor-chat")).to_have_count(0)

    page.unroute(f"**/api/v1/runs/{RUN_ID}")
    page.route(
        f"**/api/v1/runs/{RUN_ID}",
        lambda route: route.fulfill(json={**legacy, "status": "PLANNED", "current_stage": None}),
    )
    page.reload()
    expect(page.get_by_role("button", name="派发真实 AgentTeam")).to_have_count(0)
    expect(page.get_by_role("link", name="创建新的 1+4 预测")).to_be_visible()
