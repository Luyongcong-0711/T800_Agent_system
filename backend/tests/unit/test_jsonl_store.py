from __future__ import annotations

from app.storage.jsonl_store import JsonlSegmentStore
from app.storage.local_object_store import LocalObjectStore


def test_jsonl_segment_store_appends_across_segments_and_reads_in_order(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    jsonl_store = JsonlSegmentStore(
        object_store,
        "workspaces/default/runs/run_001/events",
        segment_max_events=2,
    )

    first = jsonl_store.append({"event_seq": 1, "type": "run_started"})
    second = jsonl_store.append({"event_seq": 2, "type": "assistant_delta", "text": "你好"})
    third = jsonl_store.append({"event_seq": 3, "type": "run_completed"})

    assert first.object_key.endswith("part-000001.jsonl")
    assert second.segment_no == 1
    assert third.object_key.endswith("part-000002.jsonl")
    assert jsonl_store.list_segments() == [
        first.__class__(1, "workspaces/default/runs/run_001/events/part-000001.jsonl", 2),
        first.__class__(2, "workspaces/default/runs/run_001/events/part-000002.jsonl", 1),
    ]
    assert jsonl_store.read_all() == [
        {"event_seq": 1, "type": "run_started"},
        {"event_seq": 2, "type": "assistant_delta", "text": "你好"},
        {"event_seq": 3, "type": "run_completed"},
    ]


def test_jsonl_segment_store_ignores_non_jsonl_objects(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    object_store.write_text("runs/run_001/events/readme.txt", "ignored")
    jsonl_store = JsonlSegmentStore(object_store, "runs/run_001/events", segment_max_events=1)

    segment = jsonl_store.append({"event_seq": 1})

    assert segment.object_key == "runs/run_001/events/part-000001.jsonl"
    assert jsonl_store.read_all() == [{"event_seq": 1}]
