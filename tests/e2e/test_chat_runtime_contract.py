from __future__ import annotations

import json
import re
from typing import Any

from fastapi.testclient import TestClient

from app.main import app


def _parse_sse(text: str) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\r?\n", text.strip()):
        if not block:
            continue
        item: dict[str, Any] = {}
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("id:"):
                item["id"] = line.removeprefix("id:").strip()
            elif line.startswith("event:"):
                item["event"] = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        if data_lines:
            item["data"] = json.loads("\n".join(data_lines))
        if item:
            parsed.append(item)
    return parsed


def test_chat_runtime_stream_contract_matches_frontend_flow() -> None:
    client = TestClient(app)

    thread_response = client.post(
        "/workspaces/default/threads",
        json={"title": "E2E chat contract"},
    )
    assert thread_response.status_code == 200
    thread = thread_response.json()

    run_response = client.post(
        f"/workspaces/default/threads/{thread['thread_id']}/runs",
        json={
            "idempotency_key": "e2e-chat-contract",
            "stream": True,
            "user_message": "Verify async chat contract.",
        },
    )

    assert run_response.status_code == 200
    created_run = run_response.json()
    assert created_run["status"] == "running"
    assert created_run["assistant_message_id"] is None
    assert created_run["last_event_id"]

    with client.stream(
        "GET",
        f"/workspaces/default/runs/{created_run['run_id']}/events/stream",
        params={"after_event_id": created_run["last_event_id"], "wait_ms": 5000},
    ) as stream_response:
        assert stream_response.status_code == 200
        assert stream_response.headers["content-type"].startswith("text/event-stream")
        sse_body = stream_response.read().decode("utf-8")

    parsed_events = _parse_sse(sse_body)
    event_types = [item.get("event") for item in parsed_events]
    assert "model_call_started" in event_types
    assert "assistant_message" in event_types
    assert "run_completed" in event_types
    assert event_types[-1] == "stream_closed"

    messages_response = client.get(
        f"/workspaces/default/threads/{thread['thread_id']}/messages"
    )
    assert messages_response.status_code == 200
    messages = messages_response.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "Verify async chat contract."

