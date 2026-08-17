from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal
from xml.etree import ElementTree

MAX_PDF_PAGES = 500
MAX_DOCX_ENTRIES = 10_000
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000

UnitType = Literal["DOCUMENT", "SECTION", "PAGE", "PARAGRAPH", "TABLE", "IMAGE"]


class MaterialParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedUnit:
    ordinal: int
    unit_type: UnitType
    locator: dict[str, object]
    content: str
    summary: str
    tags: tuple[str, ...]
    confidence: float
    contains_sensitive_data: bool
    parent_ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class MaterialParseResult:
    units: tuple[ParsedUnit, ...]
    page_count: int
    parsed_count: int
    visual_candidates: tuple[int, ...]
    uncovered_locators: tuple[dict[str, object], ...]
    partial: bool


_TAG_RULES = {
    "users": re.compile(r"用户|客户|受众|访谈|留存|user|customer|audience|retention", re.I),
    "product": re.compile(r"产品|功能|架构|技术|流程|界面|product|feature|architecture|workflow", re.I),
    "business": re.compile(r"商业|收入|成本|定价|市场|竞争|融资|revenue|cost|pricing|market|compet", re.I),
    "evidence": re.compile(r"调研|数据|样本|证据|research|data|sample|evidence", re.I),
}
_SENSITIVE = re.compile(
    r"(?:[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)\d{17}[\dXx](?!\d))",
    re.I,
)


def parse_material(payload: bytes, mime_type: str, display_name: str) -> MaterialParseResult:
    normalized = mime_type.split(";", 1)[0].strip().lower()
    if normalized == "application/pdf":
        return _parse_pdf(payload)
    if normalized == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return _parse_docx(payload)
    if normalized in {"text/plain", "text/markdown"}:
        return _parse_text(payload)
    if normalized in {"image/jpeg", "image/png", "image/webp"}:
        return _parse_image(payload, display_name)
    if normalized in {"application/msword", "application/vnd.ms-word"} or display_name.lower().endswith(".doc"):
        raise MaterialParseError("UNSUPPORTED_LEGACY_DOC: convert the file to DOCX before uploading")
    raise MaterialParseError(f"UNSUPPORTED_MATERIAL_TYPE: {normalized or 'application/octet-stream'}")


def render_pdf_page_jpeg(payload: bytes, page_number: int) -> bytes:
    import pypdfium2 as pdfium  # type: ignore[import-untyped]

    document = pdfium.PdfDocument(payload)
    if page_number < 1 or page_number > len(document):
        raise MaterialParseError("PDF page is outside the document")
    bitmap = document[page_number - 1].render(scale=1.15)
    image = bitmap.to_pil().convert("RGB")
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=72, optimize=True)
    return output.getvalue()


def _parse_pdf(payload: bytes) -> MaterialParseResult:
    import pdfplumber

    units: list[ParsedUnit] = []
    visual_candidates: list[int] = []
    uncovered: list[dict[str, object]] = []
    with pdfplumber.open(io.BytesIO(payload)) as document:
        actual_pages = len(document.pages)
        page_count = min(actual_pages, MAX_PDF_PAGES)
        ordinal = 1
        for block_start in range(1, page_count + 1, 10):
            block_end = min(page_count, block_start + 9)
            section_ordinal = ordinal
            units.append(
                _unit(
                    ordinal,
                    "SECTION",
                    {"page_from": block_start, "page_to": block_end},
                    f"Pages {block_start}-{block_end}",
                    1.0,
                )
            )
            ordinal += 1
            for page_number in range(block_start, block_end + 1):
                page = document.pages[page_number - 1]
                text = (page.extract_text(x_tolerance=2, y_tolerance=3) or "").strip()
                tables = page.extract_tables() or []
                table_text = "\n\n".join(_table_text(table) for table in tables if table)
                content = "\n\n".join(value for value in (text, table_text) if value).strip()
                if len(text) < 120 or bool(page.images) or tables:
                    visual_candidates.append(page_number)
                if not content:
                    uncovered.append({"page": page_number, "reason": "NO_TEXT_LAYER"})
                units.append(
                    _unit(
                        ordinal,
                        "PAGE",
                        {"page": page_number},
                        content,
                        0.98 if content else 0.2,
                        parent_ordinal=section_ordinal,
                    )
                )
                ordinal += 1
        if actual_pages > MAX_PDF_PAGES:
            uncovered.append({"page_from": MAX_PDF_PAGES + 1, "page_to": actual_pages, "reason": "PAGE_LIMIT"})
    return MaterialParseResult(
        tuple(units),
        page_count,
        page_count - sum(1 for item in uncovered if item.get("reason") == "NO_TEXT_LAYER"),
        tuple(dict.fromkeys(visual_candidates[:24])),
        tuple(uncovered),
        bool(uncovered),
    )


def _parse_docx(payload: bytes) -> MaterialParseResult:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise MaterialParseError("DOCX_INVALID_ARCHIVE") from exc
    infos = archive.infolist()
    if len(infos) > MAX_DOCX_ENTRIES or sum(item.file_size for item in infos) > MAX_DOCX_UNCOMPRESSED_BYTES:
        raise MaterialParseError("DOCX_EXPANSION_LIMIT")
    for item in infos:
        path = PurePosixPath(item.filename)
        if path.is_absolute() or ".." in path.parts:
            raise MaterialParseError("DOCX_UNSAFE_PATH")
    try:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (KeyError, ElementTree.ParseError) as exc:
        raise MaterialParseError("DOCX_DOCUMENT_XML_INVALID") from exc
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    units: list[ParsedUnit] = []
    ordinal = 1
    current_section: int | None = None
    paragraph_index = 0
    for paragraph in root.findall(".//w:body/w:p", namespace):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace)).strip()
        if not text:
            continue
        paragraph_index += 1
        style = paragraph.find("./w:pPr/w:pStyle", namespace)
        style_name = "" if style is None else style.attrib.get(f"{{{namespace['w']}}}val", "")
        if style_name.lower().startswith("heading"):
            current_section = ordinal
            units.append(_unit(ordinal, "SECTION", {"paragraph": paragraph_index, "style": style_name}, text, 0.99))
        else:
            units.append(
                _unit(ordinal, "PARAGRAPH", {"paragraph": paragraph_index}, text, 0.99, parent_ordinal=current_section)
            )
        ordinal += 1
    for table_index, table in enumerate(root.findall(".//w:body/w:tbl", namespace), start=1):
        rows: list[str] = []
        for row in table.findall("./w:tr", namespace):
            cells = [
                "".join(node.text or "" for node in cell.findall(".//w:t", namespace)).strip()
                for cell in row.findall("./w:tc", namespace)
            ]
            rows.append(" | ".join(cells))
        content = "\n".join(rows).strip()
        if content:
            units.append(_unit(ordinal, "TABLE", {"table": table_index}, content, 0.97, parent_ordinal=current_section))
            ordinal += 1
    media = [item for item in infos if item.filename.startswith("word/media/") and not item.is_dir()]
    for image_index, item in enumerate(media[:24], 1):
        units.append(
            _unit(
                ordinal,
                "IMAGE",
                {"embedded_image": image_index, "name": PurePosixPath(item.filename).name},
                "",
                0.2,
                parent_ordinal=current_section,
            )
        )
        ordinal += 1
    uncovered = tuple({"embedded_image": index, "reason": "VISION_REQUIRED"} for index in range(1, len(media[:24]) + 1))
    return MaterialParseResult(
        tuple(units),
        1,
        len(units) - len(uncovered),
        tuple(range(1, len(media[:24]) + 1)),
        uncovered,
        bool(uncovered),
    )


def _parse_text(payload: bytes) -> MaterialParseResult:
    text = _decode_text(payload).replace("\x00", "").strip()
    if not text:
        raise MaterialParseError("TEXT_EMPTY")
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
    units = tuple(_unit(index, "PARAGRAPH", {"paragraph": index}, chunk, 1.0) for index, chunk in enumerate(chunks, 1))
    return MaterialParseResult(units, 1, len(units), (), (), False)


def _parse_image(payload: bytes, display_name: str) -> MaterialParseResult:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(payload)) as image:
            width, height = image.size
            image.verify()
    except Exception as exc:
        raise MaterialParseError("IMAGE_INVALID") from exc
    if width * height > MAX_IMAGE_PIXELS:
        raise MaterialParseError("IMAGE_PIXEL_LIMIT")
    unit = _unit(1, "IMAGE", {"name": display_name, "width": width, "height": height}, "", 0.2)
    return MaterialParseResult((unit,), 1, 0, (1,), ({"image": 1, "reason": "VISION_REQUIRED"},), True)


def _unit(
    ordinal: int,
    unit_type: UnitType,
    locator: dict[str, object],
    content: str,
    confidence: float,
    *,
    parent_ordinal: int | None = None,
) -> ParsedUnit:
    normalized = content.strip()
    summary = re.sub(r"\s+", " ", normalized)[:500]
    tags = tuple(name for name, rule in _TAG_RULES.items() if rule.search(normalized))
    return ParsedUnit(
        ordinal,
        unit_type,
        locator,
        normalized,
        summary,
        tags,
        confidence,
        bool(_SENSITIVE.search(normalized)),
        parent_ordinal,
    )


def _table_text(table: list[list[str | None]]) -> str:
    return "\n".join(" | ".join(str(cell or "").strip() for cell in row) for row in table)


def _decode_text(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise MaterialParseError("TEXT_ENCODING_UNSUPPORTED")


__all__ = [
    "MaterialParseError",
    "MaterialParseResult",
    "ParsedUnit",
    "parse_material",
    "render_pdf_page_jpeg",
]
