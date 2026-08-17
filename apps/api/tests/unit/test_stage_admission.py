from __future__ import annotations

import pytest

from launchscope_api.modules.supervisor.stage_admission import (
    StageAdmissionError,
    evaluation_mode_for_stage,
    evaluation_route,
)


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("只有想法", "INCUBATION"),
        ("idea", "INCUBATION"),
        ("原型", "LIGHTWEIGHT_REVIEW"),
        ("PROTOTYPE", "LIGHTWEIGHT_REVIEW"),
        ("已有 Demo / MVP", "FORMAL_EVALUATION"),
        ("已有真实用户", "FORMAL_EVALUATION"),
        ("已上线运营", "FORMAL_EVALUATION"),
        (
            "截至 2026 年 8 月，已有可实际使用的 Web 端，核心链路已跑通，产品处于从功能可用走向稳定产品化的早期阶段；"
            "3 月材料所称计划 5 月上线是历史陈述，当前商业验证证据仍不足。",
            "FORMAL_EVALUATION",
        ),
        (
            "截至 2026-08-16，CreaTrades 已有可实际使用的真实 Web 端，模型调用、计费、积分/套餐、素材与生成链路已跑通，"
            "处于早期商业验证阶段；尚未形成可验证的 CAC、LTV、D7/D30 留存、复购率与稳定收入数据。",
            "FORMAL_EVALUATION",
        ),
    ],
)
def test_stage_routes_are_explicit_and_deterministic(stage: str, expected: str) -> None:
    assert evaluation_route(stage) == expected


def test_unknown_stage_fails_closed_instead_of_starting_a_formal_run() -> None:
    with pytest.raises(StageAdmissionError, match="cannot be admitted"):
        evaluation_route("还没想清楚")


def test_planned_launch_without_a_working_product_is_not_admitted() -> None:
    with pytest.raises(StageAdmissionError, match="cannot be admitted"):
        evaluation_route("尚未正式运营，计划 5 月上线")


@pytest.mark.parametrize(
    ("stage", "expected_mode"),
    [
        ("只有想法", "USER_VALIDATION"),
        ("已有原型", "USER_VALIDATION"),
        ("已有 Demo / MVP", "FULL_POTENTIAL"),
        ("已有真实用户", "FULL_POTENTIAL"),
    ],
)
def test_stage_selects_the_honest_prediction_mode(stage: str, expected_mode: str) -> None:
    assert evaluation_mode_for_stage(stage) == expected_mode


def test_idea_cannot_be_forced_into_the_full_potential_score() -> None:
    with pytest.raises(StageAdmissionError, match="USER_VALIDATION"):
        evaluation_mode_for_stage("只有想法", requested_mode="FULL_POTENTIAL")
