from __future__ import annotations

import re
from typing import Literal

EvaluationRoute = Literal["INCUBATION", "LIGHTWEIGHT_REVIEW", "FORMAL_EVALUATION"]
EvaluationMode = Literal["FULL_POTENTIAL", "USER_VALIDATION"]


class StageAdmissionError(ValueError):
    pass


def evaluation_route(stage: str) -> EvaluationRoute:
    normalized = re.sub(r"[\s_\-/]+", " ", stage.strip().lower())
    if any(value in normalized for value in ("只有想法", "想法阶段", "idea", "concept")):
        return "INCUBATION"
    if any(value in normalized for value in ("原型", "prototype", "线框", "wireframe")):
        return "LIGHTWEIGHT_REVIEW"
    if re.search(
        r"可实际使用的.{0,8}web\s*端|web\s*端.{0,40}(?:可用|已跑通)|"
        r"(?:核心|素材|生成|模型|计费).{0,24}链路.{0,12}已跑通|功能可用",
        normalized,
    ):
        return "FORMAL_EVALUATION"
    if any(
        value in normalized
        for value in (
            "demo",
            "mvp",
            "真实用户",
            "real user",
            "已上线",
            "上线运营",
            "launched",
            "production",
        )
    ):
        return "FORMAL_EVALUATION"
    raise StageAdmissionError("the product stage cannot be admitted to a formal evaluation")


def evaluation_mode_for_stage(stage: str, *, requested_mode: str | None = None) -> EvaluationMode:
    route = evaluation_route(stage)
    if route == "FORMAL_EVALUATION":
        if requested_mode not in {None, "FULL_POTENTIAL"}:
            raise StageAdmissionError("Demo / MVP or later must use FULL_POTENTIAL")
        return "FULL_POTENTIAL"
    if requested_mode not in {None, "USER_VALIDATION"}:
        raise StageAdmissionError(f"{route} must use USER_VALIDATION instead of FULL_POTENTIAL")
    return "USER_VALIDATION"


__all__ = ["EvaluationMode", "EvaluationRoute", "StageAdmissionError", "evaluation_mode_for_stage", "evaluation_route"]
