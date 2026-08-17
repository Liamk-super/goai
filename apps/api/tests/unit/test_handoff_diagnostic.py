from launchscope_api.modules.evaluation.handoff_application import _persisted_diagnostic


def test_matrix_diagnostic_is_bounded_for_legacy_summary_columns() -> None:
    value = "x" * 2000

    result = _persisted_diagnostic(value)

    assert len(result) == 1000
    assert result.endswith("...")
    assert _persisted_diagnostic("short") == "short"
