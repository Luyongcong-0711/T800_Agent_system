from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.storage.object_store import ObjectStore


@dataclass(frozen=True)
class JsonlSegment:
    segment_no: int
    object_key: str
    event_count: int


@dataclass(frozen=True)
class JsonlPage:
    records: list[dict[str, Any]]
    next_cursor: str | None


class JsonlSegmentStore:
    def __init__(
        self,
        object_store: ObjectStore,
        base_prefix: str,
        segment_max_events: int = 1000,
    ) -> None:
        if segment_max_events < 1:
            raise ValueError("segment_max_events must be positive.")
        self.object_store = object_store
        self.base_prefix = base_prefix.rstrip("/")
        self.segment_max_events = segment_max_events

    def _segment_key(self, segment_no: int) -> str:
        return f"{self.base_prefix}/part-{segment_no:06d}.jsonl"

    def append(self, record: dict[str, Any]) -> JsonlSegment:
        segments = self.list_segments()
        current = segments[-1] if segments else JsonlSegment(1, self._segment_key(1), 0)
        if current.event_count >= self.segment_max_events:
            next_segment_no = current.segment_no + 1
            current = JsonlSegment(next_segment_no, self._segment_key(next_segment_no), 0)

        existing = (
            self.object_store.read_text(current.object_key)
            if self.object_store.exists(current.object_key)
            else ""
        )
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        self.object_store.write_text(current.object_key, f"{existing}{line}\n")
        return JsonlSegment(current.segment_no, current.object_key, current.event_count + 1)

    def read_all(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for segment in self.list_segments():
            records.extend(self._read_segment_records(segment.object_key))
        return records

    def read_page(self, cursor: str | None = None, limit: int = 100) -> JsonlPage:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000.")
        start_segment, start_offset = self._decode_cursor(cursor)
        page: list[dict[str, Any]] = []
        next_cursor: str | None = None

        for segment in self.list_segments():
            if segment.segment_no < start_segment:
                continue
            records = self._read_segment_records(segment.object_key)
            offset = start_offset if segment.segment_no == start_segment else 0
            for record_offset, record in enumerate(records[offset:], start=offset):
                if len(page) >= limit:
                    next_cursor = self._encode_cursor(segment.segment_no, record_offset)
                    return JsonlPage(records=page, next_cursor=next_cursor)
                page.append(record)
        return JsonlPage(records=page, next_cursor=next_cursor)

    def rebuild_event_index(
        self,
        index_object_key: str,
        stream_id: str,
        event_id_field: str = "event_id",
        event_seq_field: str = "event_seq",
    ) -> dict[str, Any]:
        segments = self.list_segments()
        indexed_segments: list[dict[str, Any]] = []
        last_event_id: str | None = None
        last_event_seq = 0
        event_count = 0
        duplicate_event_count = 0
        seen_event_ids: set[str] = set()

        for segment in segments:
            records = self._read_segment_records(segment.object_key)
            segment_first_seq: int | None = None
            segment_last_seq: int | None = None
            for record in records:
                event_id = str(record[event_id_field])
                event_seq = int(record[event_seq_field])
                if event_id in seen_event_ids:
                    duplicate_event_count += 1
                    continue
                seen_event_ids.add(event_id)
                if event_seq != last_event_seq + 1:
                    raise ValueError(
                        f"Non-contiguous event_seq in {segment.object_key}: "
                        f"expected {last_event_seq + 1}, got {event_seq}"
                    )
                segment_first_seq = event_seq if segment_first_seq is None else segment_first_seq
                segment_last_seq = event_seq
                last_event_seq = event_seq
                last_event_id = event_id
                event_count += 1
            indexed_segments.append(
                {
                    "segment_no": segment.segment_no,
                    "object_key": segment.object_key,
                    "event_count": len(records),
                    "first_event_seq": segment_first_seq,
                    "last_event_seq": segment_last_seq,
                }
            )

        previous_etag = None
        previous_revision = 0
        if self.object_store.exists(index_object_key):
            previous_metadata = self.object_store.stat(index_object_key)
            previous_etag = previous_metadata.etag
            previous_index = json.loads(self.object_store.read_text(index_object_key))
            previous_revision = int(previous_index.get("revision", 0))

        index = {
            "schema_version": 1,
            "stream_id": stream_id,
            "segments": indexed_segments,
            "event_count": event_count,
            "duplicate_event_count": duplicate_event_count,
            "last_event_seq": last_event_seq,
            "last_event_id": last_event_id,
            "revision": previous_revision + 1,
        }
        self.object_store.write_text(
            index_object_key,
            json.dumps(index, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            expected_etag=previous_etag,
        )
        return index

    def list_segments(self) -> list[JsonlSegment]:
        keys = sorted(self.object_store.list_keys(self.base_prefix))
        segments: list[JsonlSegment] = []
        for key in keys:
            if not key.endswith(".jsonl") or "part-" not in key:
                continue
            text = self.object_store.read_text(key)
            count = len([line for line in text.splitlines() if line.strip()])
            segment_no = int(key.rsplit("part-", 1)[1].removesuffix(".jsonl"))
            segments.append(JsonlSegment(segment_no, key, count))
        return segments

    def _read_segment_records(self, object_key: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        text = self.object_store.read_text(object_key)
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {object_key}:{line_no}") from exc
        return records

    @staticmethod
    def _encode_cursor(segment_no: int, offset: int) -> str:
        return f"{segment_no}:{offset}"

    @staticmethod
    def _decode_cursor(cursor: str | None) -> tuple[int, int]:
        if cursor is None:
            return 1, 0
        try:
            raw_segment_no, raw_offset = cursor.split(":", 1)
            segment_no = int(raw_segment_no)
            offset = int(raw_offset)
        except ValueError as exc:
            raise ValueError("Invalid JSONL cursor.") from exc
        if segment_no < 1 or offset < 0:
            raise ValueError("Invalid JSONL cursor.")
        return segment_no, offset
