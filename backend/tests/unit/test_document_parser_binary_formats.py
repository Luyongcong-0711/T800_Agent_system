from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pytest

from app.rag_pipeline.parser import LocalDocumentParser


def test_docx_parser_extracts_document_paragraphs() -> None:
    parsed = LocalDocumentParser().parse(
        _docx_bytes(["Party A signs with Party B.", "Delivery happens in June."]),
        filename="brief.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert parsed.document_format == "docx"
    assert "Party A signs with Party B" in parsed.text
    assert parsed.metadata["paragraph_count"] == 2
    assert parsed.blocks


def test_xlsx_parser_extracts_sheet_rows() -> None:
    parsed = LocalDocumentParser().parse(
        _xlsx_bytes([["name", "role"], ["Alice", "Owner"], ["Bob", "Reviewer"]]),
        filename="sheet.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert parsed.document_format == "excel"
    assert "Alice" in parsed.text
    assert "Reviewer" in parsed.text
    assert parsed.metadata["row_count"] == 3
    assert parsed.metadata["table_count"] == 1


def test_pdf_literal_parser_extracts_text_without_optional_dependency() -> None:
    parsed = LocalDocumentParser().parse(
        b"%PDF-1.4\nBT /F1 12 Tf (Party B must deliver equipment.) Tj ET\n%%EOF",
        filename="report.pdf",
        content_type="application/pdf",
    )

    assert parsed.document_format == "pdf"
    assert "Party B must deliver equipment" in parsed.text


def test_image_parser_still_requires_ocr_backend() -> None:
    with pytest.raises(ValueError, match="image documents"):
        LocalDocumentParser().parse(
            b"\x89PNG\r\n\x1a\n",
            filename="scan.png",
            content_type="image/png",
        )


def _docx_bytes(paragraphs: list[str]) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    strings = []
    string_indexes: dict[str, int] = {}
    row_xml = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            if value not in string_indexes:
                string_indexes[value] = len(strings)
                strings.append(value)
            cell_ref = f"{chr(64 + column_index)}{row_index}"
            cells.append(f'<c r="{cell_ref}" t="s"><v>{string_indexes[value]}</v></c>')
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    shared = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f"<si><t>{value}</t></si>" for value in strings)
        + "</sst>"
    )
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData></worksheet>'
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()
