from __future__ import annotations

from decimal import Decimal

import pytest

from launchscope_api.modules.evaluation.agentteams_usage import (
    AgentUsageSnapshot,
    usage_delta,
)


def test_usage_delta_is_task_scoped_and_uses_frozen_prices() -> None:
    baseline = AgentUsageSnapshot(1000, 200, 3)
    terminal = AgentUsageSnapshot(1600, 350, 5)

    receipt = usage_delta(
        baseline,
        terminal,
        task_key="task-1:0",
        input_usd_per_million=Decimal("2"),
        output_usd_per_million=Decimal("8"),
    )

    assert receipt.input_tokens == 600
    assert receipt.output_tokens == 150
    assert receipt.call_count == 2
    assert receipt.cost_usd == Decimal("0.002400")
    assert len(receipt.receipt_id) == 64


def test_usage_delta_token_only_preserves_usage_without_inventing_cost() -> None:
    receipt = usage_delta(
        AgentUsageSnapshot(1000, 200, 3),
        AgentUsageSnapshot(1600, 350, 5),
        task_key="task-1:0",
    )

    assert (receipt.input_tokens, receipt.output_tokens, receipt.call_count) == (600, 150, 2)
    assert receipt.cost_usd is None
    assert len(receipt.receipt_id) == 64


def test_usage_delta_rejects_a_reset_or_unattributable_interval() -> None:
    with pytest.raises(ValueError, match="counter moved backwards"):
        usage_delta(
            AgentUsageSnapshot(100, 20, 2),
            AgentUsageSnapshot(90, 30, 3),
            task_key="task-1:0",
            input_usd_per_million=Decimal("1"),
            output_usd_per_million=Decimal("1"),
        )
    with pytest.raises(ValueError, match="no model call"):
        usage_delta(
            AgentUsageSnapshot(100, 20, 2),
            AgentUsageSnapshot(100, 20, 2),
            task_key="task-1:0",
            input_usd_per_million=Decimal("1"),
            output_usd_per_million=Decimal("1"),
        )
