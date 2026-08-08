from launchscope_api.modules.experience.read_model import ExperienceReadApplication


def test_action_links_keep_the_dimension_named_by_each_action() -> None:
    dimensions = {
        name: {"grade": "INSUFFICIENT_EVIDENCE", "supporting_evidence": [name.lower()]}
        for name in ("PRODUCT_IMPLEMENTATION", "USER_USAGE", "BUSINESS_INVESTMENT", "GEO_POLICY_TREND")
    }

    links = ExperienceReadApplication._action_links([
        "Collect stronger authorized evidence for Product Implementation",
        "Collect stronger authorized evidence for User Usage",
        "Collect stronger authorized evidence for Business Investment",
    ], dimensions)

    assert [item["dimension"] for item in links] == [
        "PRODUCT_IMPLEMENTATION", "USER_USAGE", "BUSINESS_INVESTMENT",
    ]
    assert links[0]["evidence_ids"] == ["product_implementation"]
