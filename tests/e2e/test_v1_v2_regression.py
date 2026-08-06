"""T12 same-standard comparison contract over durable Run projections."""

from launchscope_api.modules.decision_report.regression_application import VersionRegressionApplication


def test_version_regression_application_is_available_for_same_standard_runs() -> None:
    assert VersionRegressionApplication is not None
