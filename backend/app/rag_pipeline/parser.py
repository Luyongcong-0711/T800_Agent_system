from __future__ import annotations

import hashlib
import html as html_lib
import re
import zipfile
from csv import reader as csv_reader
from io import BytesIO, StringIO
from pathlib import PurePath
from typing import Any
from xml.etree import ElementTree

from app.rag_pipeline.models import DocumentFormat, ParsedDocument

PARSER_VERSION = "local-document-parser-v2"
MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mdown", ".mkd"}
TEXT_EXTENSIONS = {".txt", ".text", ".log"}
CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yaml", ".yml", ".toml"}
HTML_EXTENSIONS = {".html", ".htm"}
CSV_EXTENSIONS = {".csv"}
P0_BINARY_EXTENSIONS = {
    ".docx": "docx",
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tif": "image",
    ".tiff": "image",
    ".webp": "image",
    ".xls": "excel",
    ".xlsx": "excel",
}
SUPPORTED_CONTENT_TYPES = {
    "text/plain": "text",
    "text/markdown": "markdown",
    "text/x-markdown": "markdown",
    "text/csv": "csv",
    "text/html": "html",
    "application/markdown": "markdown",
    "application/csv": "csv",
    "application/json": "code",
}
P0_BINARY_CONTENT_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.ms-excel": "excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "excel",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "image/jpeg": "image",
    "image/png": "image",
    "image/tiff": "image",
    "image/webp": "image",
}
ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>",
    flags=re.IGNORECASE | re.DOTALL,
)


class LocalDocumentParser:
    """Deterministic parser for local text and Markdown payloads."""

    version = PARSER_VERSION

    def parse(
        self,
        content: bytes | str,
        *,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> ParsedDocument:
        raw_bytes = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        document_format = self._detect_format(filename=filename, content_type=content_type)
        metadata: dict[str, Any] = {
            "parser_version": self.version,
            "filename": filename,
            "content_type": content_type,
        }
        if document_format == "image":
            raise ValueError("Parser backend is not configured for image documents.")
        if document_format == "docx":
            normalized, extracted = self._docx_to_text(raw_bytes)
            metadata.update(extracted)
        elif document_format == "excel":
            normalized, extracted = self._xlsx_to_text(raw_bytes)
            metadata.update(extracted)
        elif document_format == "pdf":
            normalized, extracted = self._pdf_to_text(raw_bytes)
            metadata.update(extracted)
        else:
            text = self._decode_utf8(raw_bytes)
            normalized = self._normalize_text(
                self._html_to_text(text) if document_format == "html" else text
            )
            if document_format == "csv":
                normalized, csv_metadata = self._csv_to_text(text)
                metadata.update(csv_metadata)
        if document_format == "markdown":
            metadata["headings"] = self._extract_markdown_headings(normalized)
        if not normalized.strip():
            raise ValueError(f"No extractable text found in {document_format} document.")
        blocks = self._build_blocks(normalized, document_format, metadata)
        return ParsedDocument(
            text=normalized,
            document_format=document_format,
            source_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            byte_size=len(raw_bytes),
            blocks=blocks,
            metadata={key: value for key, value in metadata.items() if value is not None},
        )

    def _decode_utf8(self, raw_bytes: bytes) -> str:
        try:
            return raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Document parser only accepts deterministic UTF-8 text.") from exc

    def _detect_format(
        self,
        *,
        filename: str | None,
        content_type: str | None,
    ) -> DocumentFormat:
        normalized_content_type = (content_type or "").split(";")[0].strip().lower()
        if normalized_content_type:
            detected = SUPPORTED_CONTENT_TYPES.get(normalized_content_type)
            detected = detected or P0_BINARY_CONTENT_TYPES.get(normalized_content_type)
            if detected is not None:
                return detected
            if normalized_content_type.startswith("text/"):
                suffix = PurePath(filename or "").suffix.lower()
                if suffix in CODE_EXTENSIONS:
                    return "code"
            raise ValueError(f"Unsupported document content type: {content_type}")

        suffix = PurePath(filename or "").suffix.lower()
        if suffix in MARKDOWN_EXTENSIONS:
            return "markdown"
        if suffix in HTML_EXTENSIONS:
            return "html"
        if suffix in CSV_EXTENSIONS:
            return "csv"
        if suffix in CODE_EXTENSIONS:
            return "code"
        if suffix in TEXT_EXTENSIONS or not suffix:
            return "text"
        if suffix in P0_BINARY_EXTENSIONS:
            return P0_BINARY_EXTENSIONS[suffix]
        raise ValueError(f"Unsupported document extension: {suffix}")

    @staticmethod
    def _normalize_text(text: str) -> str:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
        lines = [line.rstrip() for line in normalized.split("\n")]
        return "\n".join(lines).strip() + ("\n" if any(line.strip() for line in lines) else "")

    @staticmethod
    def _extract_markdown_headings(text: str) -> list[dict[str, Any]]:
        headings: list[dict[str, Any]] = []
        offset = 0
        for line in text.splitlines(keepends=True):
            match = ATX_HEADING_RE.match(line.strip())
            if match:
                headings.append(
                    {
                        "level": len(match.group(1)),
                        "text": match.group(2).strip(),
                        "offset": offset,
                    }
                )
            offset += len(line)
        return headings

    @staticmethod
    def _html_to_text(text: str) -> str:
        without_scripts = SCRIPT_STYLE_RE.sub(" ", text)
        with_breaks = re.sub(r"</(p|div|section|article|h[1-6]|li|tr)>", "\n", without_scripts)
        without_tags = TAG_RE.sub(" ", with_breaks)
        return html_lib.unescape(without_tags)

    @staticmethod
    def _csv_to_text(text: str) -> tuple[str, dict[str, Any]]:
        rows = [row for row in csv_reader(StringIO(text)) if any(cell.strip() for cell in row)]
        if not rows:
            return "", {"row_count": 0, "column_count": 0, "table_count": 0}
        header = [cell.strip() or f"column_{index + 1}" for index, cell in enumerate(rows[0])]
        records: list[str] = []
        for row_index, row in enumerate(rows[1:], start=1):
            pairs = []
            for column_index, name in enumerate(header):
                value = row[column_index].strip() if column_index < len(row) else ""
                pairs.append(f"{name}: {value}")
            records.append(f"Row {row_index}: " + "; ".join(pairs))
        if not records:
            records.append("; ".join(header))
        return "\n".join(records) + "\n", {
            "row_count": max(0, len(rows) - 1),
            "column_count": len(header),
            "table_count": 1,
        }

    def _docx_to_text(self, raw_bytes: bytes) -> tuple[str, dict[str, Any]]:
        try:
            with zipfile.ZipFile(BytesIO(raw_bytes)) as archive:
                document_xml = archive.read("word/document.xml")
        except Exception as exc:  # noqa: BLE001 - parser boundary returns deterministic failure.
            raise ValueError("Unable to parse DOCX document text.") from exc

        root = ElementTree.fromstring(document_xml)
        namespaces = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for paragraph in root.findall(".//w:p", namespaces):
            text = "".join(
                node.text or "" for node in paragraph.findall(".//w:t", namespaces)
            ).strip()
            if text:
                paragraphs.append(text)
        return self._normalize_text("\n\n".join(paragraphs)), {
            "paragraph_count": len(paragraphs),
            "parser_backend": "zip_xml_docx",
        }

    def _xlsx_to_text(self, raw_bytes: bytes) -> tuple[str, dict[str, Any]]:
        if not raw_bytes.startswith(b"PK"):
            raise ValueError("Parser backend is not configured for legacy XLS documents.")
        try:
            with zipfile.ZipFile(BytesIO(raw_bytes)) as archive:
                shared_strings = self._read_xlsx_shared_strings(archive)
                sheet_names = sorted(
                    name
                    for name in archive.namelist()
                    if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
                )
                rows_by_sheet = [
                    self._read_xlsx_sheet(archive, name, shared_strings) for name in sheet_names
                ]
        except Exception as exc:  # noqa: BLE001 - parser boundary returns deterministic failure.
            raise ValueError("Unable to parse XLSX document text.") from exc

        lines: list[str] = []
        row_count = 0
        column_count = 0
        for sheet_index, rows in enumerate(rows_by_sheet, start=1):
            for row_index, row in enumerate(rows, start=1):
                if not any(cell.strip() for cell in row):
                    continue
                row_count += 1
                column_count = max(column_count, len(row))
                values = "; ".join(
                    f"column_{index + 1}: {cell}" for index, cell in enumerate(row)
                )
                lines.append(f"Sheet {sheet_index} Row {row_index}: {values}")
        return self._normalize_text("\n".join(lines)), {
            "row_count": row_count,
            "column_count": column_count,
            "table_count": len(rows_by_sheet),
            "parser_backend": "zip_xml_xlsx",
        }

    @staticmethod
    def _read_xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
        try:
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        except KeyError:
            return []
        namespaces = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        strings = []
        for item in root.findall(".//s:si", namespaces):
            strings.append("".join(node.text or "" for node in item.findall(".//s:t", namespaces)))
        return strings

    @staticmethod
    def _read_xlsx_sheet(
        archive: zipfile.ZipFile,
        name: str,
        shared_strings: list[str],
    ) -> list[list[str]]:
        root = ElementTree.fromstring(archive.read(name))
        namespaces = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        rows: list[list[str]] = []
        for row in root.findall(".//s:row", namespaces):
            values = []
            for cell in row.findall("s:c", namespaces):
                values.append(_xlsx_cell_text(cell, namespaces, shared_strings))
            rows.append(values)
        return rows

    def _pdf_to_text(self, raw_bytes: bytes) -> tuple[str, dict[str, Any]]:
        pypdf_text = self._try_pypdf_text(raw_bytes)
        if pypdf_text.strip():
            return self._normalize_text(pypdf_text), {"parser_backend": "pypdf"}

        decoded = raw_bytes.decode("latin-1", errors="ignore")
        literals = [_decode_pdf_literal(match) for match in re.findall(r"\((.*?)\)", decoded)]
        text = "\n".join(item for item in literals if item.strip())
        return self._normalize_text(text), {"parser_backend": "pdf_literal_extractor"}

    @staticmethod
    def _try_pypdf_text(raw_bytes: bytes) -> str:
        try:
            from pypdf import PdfReader
        except Exception:  # noqa: BLE001 - optional dependency may be absent in local tests.
            return ""
        try:
            reader = PdfReader(BytesIO(raw_bytes))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:  # noqa: BLE001 - fall back to deterministic literal extraction.
            return ""

    @staticmethod
    def _build_blocks(
        text: str,
        document_format: DocumentFormat,
        metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not text.strip():
            return []

        blocks: list[dict[str, Any]] = []
        offset = 0
        current_section: list[str] = []
        block_index = 1
        for paragraph in re.split(r"(\n{2,})", text):
            if not paragraph or paragraph.startswith("\n"):
                offset += len(paragraph)
                continue
            stripped = paragraph.strip()
            if not stripped:
                offset += len(paragraph)
                continue

            block_type = "paragraph"
            level = None
            if document_format == "markdown":
                first_line = stripped.splitlines()[0].strip()
                heading_match = ATX_HEADING_RE.match(first_line)
                if heading_match:
                    block_type = "heading"
                    level = len(heading_match.group(1))
                    heading_text = heading_match.group(2).strip()
                    current_section = current_section[: level - 1]
                    current_section.append(heading_text)
            elif document_format == "code":
                block_type = "code"
            elif document_format in {"csv", "excel"}:
                block_type = "table"

            char_start = text.find(paragraph, offset)
            if char_start < 0:
                char_start = offset
            char_end = char_start + len(paragraph)
            block: dict[str, Any] = {
                "block_id": f"blk_{block_index:06d}",
                "type": block_type,
                "text": stripped,
                "page_start": 1,
                "page_end": 1,
                "char_start": char_start,
                "char_end": char_end,
                "section_path": list(current_section)
                or [str(metadata.get("filename") or "Untitled")],
            }
            if level is not None:
                block["level"] = level
            blocks.append(block)
            block_index += 1
            offset = char_end
        return blocks


def _xlsx_cell_text(
    cell: ElementTree.Element,
    namespaces: dict[str, str],
    shared_strings: list[str],
) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//s:t", namespaces)).strip()
    value = cell.find("s:v", namespaces)
    raw = value.text if value is not None and value.text is not None else ""
    if cell_type == "s":
        try:
            return shared_strings[int(raw)].strip()
        except (IndexError, ValueError):
            return ""
    return raw.strip()


def _decode_pdf_literal(value: str) -> str:
    replacements = {
        r"\n": "\n",
        r"\r": "\n",
        r"\t": "\t",
        r"\(": "(",
        r"\)": ")",
        r"\\": "\\",
    }
    decoded = value
    for source, target in replacements.items():
        decoded = decoded.replace(source, target)
    decoded = re.sub(r"\\[0-7]{1,3}", " ", decoded)
    return decoded
