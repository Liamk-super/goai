from launchscope_api.modules.project_dossier.material_analysis import _analysis_can_start


def test_only_queued_analysis_may_start_work() -> None:
    assert _analysis_can_start("QUEUED") is True
    assert _analysis_can_start("PARSING") is False
    assert _analysis_can_start("READY") is False
    assert _analysis_can_start("PARTIAL") is False
