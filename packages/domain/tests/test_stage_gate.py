from __future__ import annotations

import pytest

from launchscope_domain import InvalidTransitionError, StageCode, StageGate


def test_stage_gate_is_fixed_and_cannot_skip() -> None:
    assert StageGate.next_stage(None) is StageCode.INTAKE
    assert StageGate.next_stage(StageCode.INTAKE) is StageCode.GAP_ANALYSIS
    with pytest.raises(InvalidTransitionError):
        StageGate.assert_entry(None, StageCode.PLANNING)
