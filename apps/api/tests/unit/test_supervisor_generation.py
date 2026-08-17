from launchscope_api.modules.supervisor.generation import is_supervisor_generation


def test_supervisor_generation_family_includes_report_v22_and_material_routing() -> None:
    assert is_supervisor_generation("supervisor-1p4-v1")
    assert is_supervisor_generation("supervisor-1p4-material-routing-v2")
    assert is_supervisor_generation("supervisor-1p4-report-v22")
    assert not is_supervisor_generation("legacy-1p5")
    assert not is_supervisor_generation(None)
