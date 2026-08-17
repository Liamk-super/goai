from __future__ import annotations

import io
import zipfile

import pytest

from launchscope_worker.material_parser import MaterialParseError, parse_material


def test_text_parser_preserves_paragraph_locators() -> None:
    parsed = parse_material("第一段\n\n第二段".encode(), "text/markdown", "brief.md")

    assert [unit.locator for unit in parsed.units] == [{"paragraph": 1}, {"paragraph": 2}]
    assert parsed.parsed_count == 2
    assert parsed.partial is False


def test_docx_parser_extracts_headings_tables_and_requires_vision_for_images() -> None:
    body = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Overview</w:t></w:r></w:p>
      <w:p><w:r><w:t>Product details</w:t></w:r></w:p>
      <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Metric</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
    </w:body></w:document>"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", body)
        archive.writestr("word/media/image1.png", b"not-decoded-by-deterministic-parser")

    parsed = parse_material(
        output.getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "brief.docx",
    )

    assert {unit.unit_type for unit in parsed.units} >= {"SECTION", "PARAGRAPH", "TABLE", "IMAGE"}
    assert parsed.visual_candidates == (1,)
    assert parsed.uncovered_locators == ({"embedded_image": 1, "reason": "VISION_REQUIRED"},)


def test_legacy_doc_is_rejected_explicitly() -> None:
    with pytest.raises(MaterialParseError, match="UNSUPPORTED_LEGACY_DOC"):
        parse_material(b"legacy", "application/msword", "legacy.doc")


def test_docx_zip_slip_and_expansion_are_rejected() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("../word/document.xml", b"unsafe")

    with pytest.raises(MaterialParseError, match="DOCX_UNSAFE_PATH"):
        parse_material(
            output.getvalue(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "unsafe.docx",
        )
