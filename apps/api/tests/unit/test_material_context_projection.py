from __future__ import annotations

import json
from uuid import uuid4

from launchscope_api.modules.evidence.mcp_application import (
    _bounded_material_context,
    _representative_material_catalog_rows,
)


def test_material_context_projects_only_assigned_units_within_response_budget() -> None:
    scope_views: list[dict[str, object]] = []
    catalog_rows: list[dict[str, object]] = []
    for material_index in range(5):
        material_id = uuid4()
        refs: list[str] = []
        for unit_index in range(20):
            unit_id = uuid4()
            digest = f"{material_index}{unit_index}".ljust(64, "a")[:64]
            unit_ref = f"material-unit:{unit_id}@{digest}"
            refs.append(unit_ref)
            catalog_rows.append(
                {
                    "id": unit_id,
                    "material_id": material_id,
                    "parent_unit_id": None,
                    "unit_type": "SECTION",
                    "locator": {"page": unit_index + 1},
                    "tags": ["product"],
                    "confidence": 1,
                    "sha256": digest,
                    "summary": "x" * 4_000,
                    "display_name": f"material-{material_index}.pdf",
                    "coverage": {"uncovered_locators": [f"page:{value}" for value in range(100)]},
                }
            )
        scope_views.append(
            {
                "id": uuid4(),
                "material_id": material_id,
                "unit_refs": refs,
                "reason": "required material",
                "required": True,
                "scope_sha256": "b" * 64,
            }
        )

    result = _bounded_material_context({"base": "y" * 30_000}, catalog_rows, scope_views)

    encoded = json.dumps(result, default=str, ensure_ascii=False).encode("utf-8")
    projected_refs = {ref for scope in result["material_scope"] for ref in scope["unit_refs"]}
    catalog_refs = {item["unit_ref"] for item in result["material_catalog"]}
    assert len(encoded) <= 40_000
    assert len(result["material_scope"]) == 5
    assert all(1 <= len(scope["unit_refs"]) <= 8 for scope in result["material_scope"])
    assert catalog_refs <= projected_refs
    assert {item["material_id"] for item in result["material_catalog"]} == {
        str(scope["material_id"]) for scope in scope_views
    }


def test_material_context_bounds_accumulated_evidence_refs() -> None:
    result = _bounded_material_context(
        {
            "evidence_refs": [
                {"evidence_id": f"evidence-{index}", "summary": "中文证据" * 800}
                for index in range(50)
            ]
        },
        [],
        [],
    )

    encoded = json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")
    assert len(encoded) <= 40_000
    assert result["evidence_refs"]
    assert all(len(item["summary"]) <= 200 for item in result["evidence_refs"])


def test_material_context_prefers_readable_units_and_samples_the_assigned_range() -> None:
    material_id = uuid4()
    rows: list[dict[str, object]] = []
    refs: list[str] = []
    for index in range(11):
        unit_id = uuid4()
        digest = str(index).ljust(64, "a")
        refs.append(f"material-unit:{unit_id}@{digest}")
        rows.append({
            "id": unit_id,
            "material_id": material_id,
            "parent_unit_id": None if index == 0 else rows[0]["id"],
            "unit_type": "SECTION" if index == 0 else "PAGE",
            "locator": {"page": index} if index else {"page_from": 1, "page_to": 10},
            "tags": ["product"],
            "confidence": 1,
            "sha256": digest,
            "summary": "Pages 1-10" if index == 0 else f"page {index} substantive content",
            "display_name": "long.pdf",
            "coverage": {"uncovered_locators": []},
        })

    result = _bounded_material_context(
        {},
        rows,
        [{
            "id": uuid4(),
            "material_id": material_id,
            "unit_refs": refs,
            "reason": "required material",
            "required": True,
            "scope_sha256": "b" * 64,
        }],
    )

    selected = result["material_scope"][0]["unit_refs"]
    catalog = {item["unit_ref"]: item for item in result["material_catalog"]}
    assert len(selected) == 8
    assert all(catalog[ref]["unit_type"] == "PAGE" for ref in selected)
    assert selected[0] == refs[1]
    assert selected[-1] == refs[-1]


def test_planning_context_projects_selected_units_before_task_scopes_exist() -> None:
    material_id = uuid4()
    unit_id = uuid4()
    digest = "a" * 64
    result = _bounded_material_context(
        {"planning_constraints": {"evaluation_mode": "FULL_POTENTIAL"}},
        [{
            "id": unit_id,
            "material_id": material_id,
            "parent_unit_id": None,
            "unit_type": "PARAGRAPH",
            "locator": {"paragraph": 1},
            "tags": ["product"],
            "confidence": 1,
            "sha256": digest,
            "summary": "selected product material",
            "display_name": "product-intake.txt",
            "coverage": {"uncovered_locators": []},
        }],
        [],
        include_unscoped_catalog=True,
    )

    assert result["material_scope"] == []
    assert result["material_catalog"] == [{
        "material_id": str(material_id),
        "file_name": "product-intake.txt",
        "unit_ref": f"material-unit:{unit_id}@{digest}",
        "parent_unit_id": None,
        "unit_type": "PARAGRAPH",
        "locator": {"paragraph": 1},
        "tags": ["product"],
        "confidence": 1.0,
        "summary": "selected product material",
        "coverage_gaps": [],
    }]


def test_planning_catalog_samples_readable_units_across_a_long_document() -> None:
    material_id = uuid4()
    rows = [
        {
            "id": uuid4(),
            "material_id": material_id,
            "unit_type": "SECTION" if index % 11 == 0 else "PAGE",
            "summary": f"page {index}" if index % 11 else f"Pages {index + 1}-{index + 10}",
        }
        for index in range(110)
    ]

    selected = _representative_material_catalog_rows(rows)

    assert len(selected) == 8
    assert all(row["unit_type"] == "PAGE" for row in selected)
    assert selected[0]["summary"] == "page 1"
    assert selected[-1]["summary"] == "page 109"
