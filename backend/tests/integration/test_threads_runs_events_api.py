from __future__ import annotations

# ruff: noqa: E402,I001

import json
import re
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

pytest.importorskip("app.api.threads", reason="Phase D thread API has not landed yet.")

from app.api.dependencies import get_conversation_service, get_object_store
from app.conversation.service import ConversationService
from app.main import app
from app.runtime.runner import RuntimeRunner
from app.runtime.tools import ToolRegistry
from app.schemas.conversation import CreateRunRequest, CreateThreadRequest
from app.schemas.identity import RuntimeIdentity
from app.schemas.model import (
    ModelConfig,
    ModelRequest,
    ModelResult,
    ModelStreamEvent,
    ModelToolCall,
    ModelUsage,
    ToolCallDelta,
)
from app.schemas.runtime import RuntimeSmokeResponse
from app.storage.local_object_store import LocalObjectStore
from app.storage.path_builder import run_operations_prefix, run_skill_run_artifact_key
from app.storage.path_builder import workspace_file_object_key


SENSITIVE_TERMS = [
    "api_key",
    "password",
    "plaintext",
    "ciphertext",
    "nonce",
    "tag",
    "agent_master_key",
    "sk-test-secret",
]


@pytest.fixture()
def client(tmp_path) -> Iterator[TestClient]:
    object_store = LocalObjectStore(tmp_path / "objects")
    app.dependency_overrides[get_object_store] = lambda: object_store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        get_object_store.cache_clear()


def _assert_no_secret_material(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False, default=str).lower()
    for term in SENSITIVE_TERMS:
        assert term not in serialized


def _items(body: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return body
    assert isinstance(body, dict)
    value = body.get(key)
    assert isinstance(value, list)
    return value


def _post_thread(client: TestClient, title: str) -> dict[str, Any]:
    response = client.post("/workspaces/default/threads", json={"title": title})
    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"]
    assert body["status"] == "active"
    _assert_no_secret_material(body)
    return body


def _post_run(
    client: TestClient,
    thread_id: str,
    user_message: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"user_message": user_message}
    if idempotency_key is not None:
        payload["idempotency_key"] = idempotency_key
    response = client.post(f"/workspaces/default/threads/{thread_id}/runs", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"]
    _assert_no_secret_material(body)
    return body


def _get_events(
    client: TestClient,
    run_id: str,
    after_event_id: str | None = None,
) -> list[dict[str, Any]]:
    params = {"after_event_id": after_event_id} if after_event_id else None
    response = client.get(f"/workspaces/default/runs/{run_id}/events", params=params)
    assert response.status_code == 200
    events = _items(response.json(), "events")
    _assert_no_secret_material(events)
    return events


def _parse_sse(text: str) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\r?\n", text.strip()):
        if not block:
            continue
        event: dict[str, Any] = {}
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("id:"):
                event["id"] = line.removeprefix("id:").strip()
            elif line.startswith("event:"):
                event["event"] = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        if data_lines:
            event["data"] = json.loads("\n".join(data_lines))
        parsed.append(event)
    return parsed


def test_create_thread_lists_active_threads_without_deleting_old_threads(
    client: TestClient,
) -> None:
    first = _post_thread(client, "First thread")
    second = _post_thread(client, "Second thread")

    list_response = client.get("/workspaces/default/threads")
    assert list_response.status_code == 200
    threads = _items(list_response.json(), "threads")
    listed_ids = {thread["thread_id"] for thread in threads}

    assert {first["thread_id"], second["thread_id"]} <= listed_ids
    assert all(thread["status"] == "active" for thread in threads)
    detail_response = client.get(f"/workspaces/default/threads/{first['thread_id']}")
    assert detail_response.status_code == 200
    assert detail_response.json()["thread_id"] == first["thread_id"]
    _assert_no_secret_material(list_response.json())
    _assert_no_secret_material(detail_response.json())


def test_create_thread_idempotency_key_with_different_payload_returns_conflict(
    client: TestClient,
) -> None:
    first = client.post(
        "/workspaces/default/threads",
        json={"title": "Original title", "idempotency_key": "thread-idem"},
    )
    conflict = client.post(
        "/workspaces/default/threads",
        json={"title": "Changed title", "idempotency_key": "thread-idem"},
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["error_type"] == "idempotency_conflict"
    _assert_no_secret_material(conflict.json())


def test_creating_run_persists_user_and_assistant_messages(client: TestClient) -> None:
    thread = _post_thread(client, "Message persistence")
    run = _post_run(client, thread["thread_id"], "Hello from the user")

    messages_response = client.get(
        f"/workspaces/default/threads/{thread['thread_id']}/messages"
    )
    assert messages_response.status_code == 200
    messages = _items(messages_response.json(), "messages")
    roles = [message["role"] for message in messages]

    assert run["run_id"]
    assert "user" in roles
    assert "assistant" in roles
    assert any(message["content"] == "Hello from the user" for message in messages)
    _assert_no_secret_material(messages)


def test_stream_run_response_is_running_before_background_completion(
    client: TestClient,
) -> None:
    thread = _post_thread(client, "Async stream run")

    response = client.post(
        f"/workspaces/default/threads/{thread['thread_id']}/runs",
        json={"stream": True, "user_message": "Start asynchronously"},
    )

    assert response.status_code == 200
    created = response.json()
    assert created["status"] == "running"
    assert created["assistant_message_id"] is None

    run_response = client.get(f"/workspaces/default/runs/{created['run_id']}")
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "completed"

    messages_response = client.get(
        f"/workspaces/default/threads/{thread['thread_id']}/messages"
    )
    messages = _items(messages_response.json(), "messages")
    assert [message["role"] for message in messages] == ["user", "assistant"]
    _assert_no_secret_material([created, run_response.json(), messages])


def test_run_events_are_sequential_and_after_event_id_filters_later_events(
    client: TestClient,
) -> None:
    thread = _post_thread(client, "Events")
    run = _post_run(client, thread["thread_id"], "Create event history")

    events = _get_events(client, run["run_id"])
    assert len(events) >= 3
    event_ids = [event["event_id"] for event in events]
    event_seqs = [event["event_seq"] for event in events]

    assert event_seqs == list(range(1, len(events) + 1))
    assert len(event_ids) == len(set(event_ids))
    assert all(
        re.fullmatch(rf"evt_{re.escape(run['run_id'])}_[0-9]{{12}}", event_id)
        for event_id in event_ids
    )

    later_events = _get_events(client, run["run_id"], after_event_id=event_ids[0])
    assert [event["event_id"] for event in later_events] == event_ids[1:]


def test_subagent_tool_result_emits_run_events(tmp_path) -> None:
    class SubAgentRuntimeRunner:
        def invoke_for_run(self, **kwargs: Any) -> RuntimeSmokeResponse:
            return RuntimeSmokeResponse(
                run_id=kwargs["run_id"],
                thread_id=kwargs["thread_id"],
                workspace_id=kwargs["workspace_id"],
                status="completed",
                model_error=None,
                requires_approval=False,
                context_usage={
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                    "usage_estimated": True,
                },
                messages=[{"type": "ai", "content": "Main Agent reviewed SubAgent output."}],
                tool_results=[
                    {
                        "tool_call_id": "call_subagent_001",
                        "name": "call_subagent_log_analyst",
                        "ok": True,
                        "error_type": None,
                        "content": {
                            "ok": True,
                            "needs_main_review": True,
                            "can_directly_finalize": False,
                            "data": {
                                "task_id": "subtask_001",
                                "parent_run_id": kwargs["run_id"],
                                "agent_type": "log_analyst",
                                "status": "completed",
                                "summary": "Checked run logs and found no fatal error.",
                                "changed_files": [],
                                "needs_main_review": True,
                                "can_directly_finalize": False,
                            },
                        },
                    }
                ],
                tool_specs=[],
            )

    object_store = LocalObjectStore(tmp_path / "objects")
    service = ConversationService(
        object_store,
        SubAgentRuntimeRunner(),  # type: ignore[arg-type]
    )
    identity = RuntimeIdentity()
    thread = service.create_thread(
        "default",
        identity,
        CreateThreadRequest(title="SubAgent events"),
    )
    run = service.create_run(
        "default",
        thread["thread_id"],
        identity,
        CreateRunRequest(user_message="Use a subagent."),
    )

    events, _ = service.list_run_events("default", run["run_id"])
    by_type = {event["type"]: event for event in events}

    assert "subagent_task_created" in by_type
    assert "subagent_task_completed" in by_type
    assert by_type["subagent_task_created"]["payload"]["task_id"] == "subtask_001"
    assert by_type["subagent_task_completed"]["payload"]["needs_main_review"] is True
    assert by_type["subagent_task_completed"]["payload"]["can_directly_finalize"] is False
    _assert_no_secret_material(events)


def test_provider_stream_content_delta_is_written_to_run_events(tmp_path) -> None:
    class StreamOnlyConnector:
        def call(
            self,
            workspace_id: str,
            config: ModelConfig,
            request: ModelRequest,
        ) -> ModelResult:
            _ = workspace_id, config, request
            raise AssertionError("streaming run should not use non-stream call")

        def stream(
            self,
            workspace_id: str,
            config: ModelConfig,
            request: ModelRequest,
        ):
            _ = workspace_id, config, request
            yield ModelStreamEvent(type="message_start", request_id="run_stream")
            yield ModelStreamEvent(
                type="content_delta",
                request_id="run_stream",
                delta="hel",
            )
            yield ModelStreamEvent(
                type="content_delta",
                request_id="run_stream",
                delta="lo",
            )
            yield ModelStreamEvent(
                type="usage_delta",
                request_id="run_stream",
                usage=ModelUsage(input_tokens=2, output_tokens=1, total_tokens=3),
            )
            yield ModelStreamEvent(type="message_completed", request_id="run_stream")
            yield ModelStreamEvent(type="stream_closed", request_id="run_stream")

    object_store = LocalObjectStore(tmp_path / "objects")
    service = ConversationService(
        object_store,
        RuntimeRunner(
            llm_connector=StreamOnlyConnector(),  # type: ignore[arg-type]
            model_config=ModelConfig(provider="fake", model="stream-test"),
            object_store=object_store,
        ),
    )
    identity = RuntimeIdentity()
    thread = service.create_thread(
        "default",
        identity,
        CreateThreadRequest(title="Provider stream"),
    )
    run = service.create_run(
        "default",
        thread["thread_id"],
        identity,
        CreateRunRequest(user_message="Stream response"),
    )

    events, _ = service.list_run_events("default", run["run_id"])
    assistant_deltas = [
        event["payload"]["delta"] for event in events if event["type"] == "assistant_delta"
    ]
    messages = service.list_messages("default", thread["thread_id"])

    assert assistant_deltas == ["hel", "lo"]
    assert any(event["type"] == "model_usage_delta" for event in events)
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "hello"
    _assert_no_secret_material(events)


def test_second_run_reuses_same_thread_history_without_copying_other_threads(tmp_path) -> None:
    class CapturingConnector:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def call(
            self,
            workspace_id: str,
            config: ModelConfig,
            request: ModelRequest,
        ) -> ModelResult:
            _ = workspace_id, config
            self.requests.append(request)
            request_text = "\n".join(message.content for message in request.messages)
            content = (
                "I can see the previous answer about MinIO."
                if "What did we say about storage?" in request_text
                else "First answer mentions MinIO."
                if "Remember that storage uses MinIO." in request_text
                else "First answer mentions MinIO."
            )
            return ModelResult(
                content=content,
                tool_calls=[],
                usage=ModelUsage(input_tokens=3, output_tokens=3, total_tokens=6),
            )

        def stream(
            self,
            workspace_id: str,
            config: ModelConfig,
            request: ModelRequest,
        ):
            result = self.call(workspace_id, config, request)
            yield ModelStreamEvent(type="message_start", request_id=request.request_id)
            yield ModelStreamEvent(
                type="content_delta",
                request_id=request.request_id,
                delta=result.content,
            )
            yield ModelStreamEvent(
                type="usage_delta",
                request_id=request.request_id,
                usage=result.usage,
            )
            yield ModelStreamEvent(type="message_completed", request_id=request.request_id)
            yield ModelStreamEvent(type="stream_closed", request_id=request.request_id)

    object_store = LocalObjectStore(tmp_path / "objects")
    connector = CapturingConnector()
    service = ConversationService(
        object_store,
        RuntimeRunner(
            llm_connector=connector,  # type: ignore[arg-type]
            model_config=ModelConfig(provider="fake", model="history-test"),
            object_store=object_store,
        ),
    )
    identity = RuntimeIdentity()
    first_thread = service.create_thread(
        "default",
        identity,
        CreateThreadRequest(title="Thread memory boundary"),
    )
    other_thread = service.create_thread(
        "default",
        identity,
        CreateThreadRequest(title="Other thread"),
    )

    service.create_run(
        "default",
        other_thread["thread_id"],
        identity,
        CreateRunRequest(user_message="This other thread should not appear."),
    )
    service.create_run(
        "default",
        first_thread["thread_id"],
        identity,
        CreateRunRequest(user_message="Remember that storage uses MinIO."),
    )
    service.create_run(
        "default",
        first_thread["thread_id"],
        identity,
        CreateRunRequest(user_message="What did we say about storage?"),
    )

    second_request = connector.requests[-1]
    request_text = "\n".join(message.content for message in second_request.messages)
    roles = [message.role for message in second_request.messages]

    assert "Remember that storage uses MinIO." in request_text
    assert "First answer mentions MinIO." in request_text
    assert "What did we say about storage?" in request_text
    assert "This other thread should not appear." not in request_text
    assert roles.count("user") >= 2
    assert roles.count("assistant") >= 1
    _assert_no_secret_material(second_request.model_dump())


def test_duplicate_idempotency_key_returns_same_run_and_user_message(
    client: TestClient,
) -> None:
    thread = _post_thread(client, "Idempotency")

    first = _post_run(
        client,
        thread["thread_id"],
        "Only create this once",
        idempotency_key="idem-001",
    )
    second = _post_run(
        client,
        thread["thread_id"],
        "Only create this once",
        idempotency_key="idem-001",
    )

    assert second["run_id"] == first["run_id"]
    assert second["user_message_id"] == first["user_message_id"]

    messages_response = client.get(
        f"/workspaces/default/threads/{thread['thread_id']}/messages"
    )
    messages = _items(messages_response.json(), "messages")
    user_messages = [
        message for message in messages if message.get("message_id") == first["user_message_id"]
    ]
    assert len(user_messages) == 1
    _assert_no_secret_material(messages)


def test_duplicate_idempotency_key_with_different_payload_returns_conflict(
    client: TestClient,
) -> None:
    thread = _post_thread(client, "Idempotency conflict")
    first = _post_run(
        client,
        thread["thread_id"],
        "Payload A",
        idempotency_key="idem-conflict",
    )

    conflict = client.post(
        f"/workspaces/default/threads/{thread['thread_id']}/runs",
        json={"user_message": "Payload B", "idempotency_key": "idem-conflict"},
    )

    assert first["run_id"]
    assert conflict.status_code == 409
    assert conflict.json()["error_type"] == "idempotency_conflict"
    _assert_no_secret_material(conflict.json())


def test_event_cursor_must_belong_to_same_run(client: TestClient) -> None:
    first_thread = _post_thread(client, "Cursor A")
    second_thread = _post_thread(client, "Cursor B")
    first_run = _post_run(client, first_thread["thread_id"], "First run")
    second_run = _post_run(client, second_thread["thread_id"], "Second run")
    first_events = _get_events(client, first_run["run_id"])

    response = client.get(
        f"/workspaces/default/runs/{second_run['run_id']}/events",
        params={"after_event_id": first_events[0]["event_id"]},
    )

    assert response.status_code == 400
    assert response.json()["error_type"] == "invalid_event_cursor"
    _assert_no_secret_material(response.json())


def test_sse_replays_later_events_and_closes_completed_run(client: TestClient) -> None:
    thread = _post_thread(client, "SSE")
    run = _post_run(client, thread["thread_id"], "Stream completed events")
    events = _get_events(client, run["run_id"])

    with client.stream(
        "GET",
        f"/workspaces/default/runs/{run['run_id']}/events/stream",
        headers={"Last-Event-ID": events[0]["event_id"]},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = response.read().decode("utf-8")

    parsed = _parse_sse(body)
    replayed_event_ids = [
        event.get("id")
        for event in parsed
        if event.get("event") != "stream_closed" and event.get("id")
    ]

    assert replayed_event_ids == [event["event_id"] for event in events[1:]]
    assert parsed[-1]["event"] == "stream_closed"
    assert parsed[-1]["data"]["run_id"] == run["run_id"]
    assert parsed[-1]["data"]["status"] == "completed"
    _assert_no_secret_material(parsed)


def test_sse_accepts_stream_closed_last_event_id_on_browser_reconnect(
    client: TestClient,
) -> None:
    thread = _post_thread(client, "SSE stream closed reconnect")
    run = _post_run(client, thread["thread_id"], "Stream completed events")

    with client.stream(
        "GET",
        f"/workspaces/default/runs/{run['run_id']}/events/stream",
    ) as response:
        assert response.status_code == 200
        body = response.read().decode("utf-8")

    parsed = _parse_sse(body)
    stream_closed_id = parsed[-1]["id"]
    assert parsed[-1]["event"] == "stream_closed"
    assert re.fullmatch(rf"evt_{re.escape(run['run_id'])}_[0-9]{{12}}", stream_closed_id)

    with client.stream(
        "GET",
        f"/workspaces/default/runs/{run['run_id']}/events/stream",
        headers={"Last-Event-ID": stream_closed_id},
    ) as response:
        assert response.status_code == 200
        reconnect_body = response.read().decode("utf-8")

    reconnect_events = _parse_sse(reconnect_body)
    assert reconnect_events[-1]["event"] == "stream_closed"
    assert reconnect_events[-1]["id"] == stream_closed_id
    _assert_no_secret_material(reconnect_events)


def test_sse_waits_for_new_events_after_cursor_until_run_closes(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    service = ConversationService(object_store, RuntimeRunner(object_store=object_store))
    identity = RuntimeIdentity()
    thread = service.create_thread(
        "default",
        identity,
        CreateThreadRequest(title="Live SSE"),
    )
    run = service.create_run(
        "default",
        thread["thread_id"],
        identity,
        CreateRunRequest(user_message="Wait for live events", stream=True),
        execute_inline=False,
    )
    initial_events, _ = service.list_run_events("default", run["run_id"])
    assert [event["type"] for event in initial_events] == ["run_started", "user_message"]

    def execute_later() -> None:
        time.sleep(0.2)
        service.execute_run(
            workspace_id="default",
            thread_id=thread["thread_id"],
            identity=identity,
            run_id=run["run_id"],
            user_message="Wait for live events",
        )

    worker = Thread(target=execute_later)
    worker.start()
    app.dependency_overrides[get_object_store] = lambda: object_store
    app.dependency_overrides[get_conversation_service] = lambda: service
    try:
        client = TestClient(app)
        with client.stream(
            "GET",
            f"/workspaces/default/runs/{run['run_id']}/events/stream",
            params={"after_event_id": initial_events[-1]["event_id"], "wait_ms": 5000},
        ) as response:
            assert response.status_code == 200
            body = response.read().decode("utf-8")
    finally:
        worker.join(timeout=5)
        app.dependency_overrides.clear()
        get_object_store.cache_clear()

    parsed = _parse_sse(body)
    event_types = [event.get("event") for event in parsed]
    assert "model_call_started" in event_types
    assert "run_completed" in event_types
    assert event_types[-1] == "stream_closed"
    _assert_no_secret_material(parsed)


def test_cancelled_run_is_not_overwritten_by_late_background_execution(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    service = ConversationService(object_store, RuntimeRunner(object_store=object_store))
    identity = RuntimeIdentity()
    thread = service.create_thread(
        "default",
        identity,
        CreateThreadRequest(title="Cancel race"),
    )
    run = service.create_run(
        "default",
        thread["thread_id"],
        identity,
        CreateRunRequest(user_message="Cancel before execution", stream=True),
        execute_inline=False,
    )

    cancelled = service.cancel_run("default", run["run_id"])
    late_result = service.execute_run(
        workspace_id="default",
        thread_id=thread["thread_id"],
        identity=identity,
        run_id=run["run_id"],
        user_message="Cancel before execution",
    )

    assert cancelled["status"] == "cancelled"
    assert late_result["status"] == "cancelled"
    assert service.get_thread("default", thread["thread_id"])["current_run_status"] == "cancelled"
    events, _ = service.list_run_events("default", run["run_id"])
    assert [event["type"] for event in events][-1] == "run_cancelled"
    assert "run_completed" not in [event["type"] for event in events]
    _assert_no_secret_material([cancelled, late_result, events])


def test_run_execution_lease_prevents_duplicate_runtime_invocation(tmp_path) -> None:
    class SlowRuntimeRunner:
        def __init__(self) -> None:
            self.started = Event()
            self.calls = 0

        def invoke_for_run(self, **kwargs: Any) -> RuntimeSmokeResponse:
            self.calls += 1
            self.started.set()
            time.sleep(0.3)
            return RuntimeSmokeResponse(
                run_id=kwargs["run_id"],
                thread_id=kwargs["thread_id"],
                workspace_id=kwargs["workspace_id"],
                status="completed",
                model_error=None,
                requires_approval=False,
                context_usage={
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                    "usage_estimated": True,
                },
                messages=[{"type": "ai", "content": "Lease protected answer."}],
                tool_results=[],
                tool_specs=[],
            )

    object_store = LocalObjectStore(tmp_path / "objects")
    runner = SlowRuntimeRunner()
    service = ConversationService(
        object_store,
        runner,  # type: ignore[arg-type]
        runtime_instance_id="rt_test",
        run_lease_ttl_seconds=60,
    )
    identity = RuntimeIdentity()
    thread = service.create_thread(
        "default",
        identity,
        CreateThreadRequest(title="Lease"),
    )
    run = service.create_run(
        "default",
        thread["thread_id"],
        identity,
        CreateRunRequest(user_message="Only one worker should execute.", stream=True),
        execute_inline=False,
    )

    first_result: dict[str, Any] = {}

    def first_execute() -> None:
        first_result.update(
            service.execute_run(
                workspace_id="default",
                thread_id=thread["thread_id"],
                identity=identity,
                run_id=run["run_id"],
                user_message="Only one worker should execute.",
            )
        )

    worker = Thread(target=first_execute)
    worker.start()
    assert runner.started.wait(timeout=3)

    second = service.execute_run(
        workspace_id="default",
        thread_id=thread["thread_id"],
        identity=identity,
        run_id=run["run_id"],
        user_message="Only one worker should execute.",
    )
    worker.join(timeout=5)

    assert second["status"] == "running"
    assert first_result["status"] == "completed"
    assert runner.calls == 1
    events, _ = service.list_run_events("default", run["run_id"])
    assert [event["type"] for event in events].count("model_call_started") == 1
    assert [event["type"] for event in events].count("run_completed") == 1


def test_recover_stale_running_run_marks_failed_and_updates_indexes(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    service = ConversationService(object_store, RuntimeRunner(object_store=object_store))
    identity = RuntimeIdentity()
    thread = service.create_thread(
        "default",
        identity,
        CreateThreadRequest(title="Stale recovery"),
    )
    run = service.create_run(
        "default",
        thread["thread_id"],
        identity,
        CreateRunRequest(user_message="This run will become stale", stream=True),
        execute_inline=False,
    )
    manifest = service._run_manifest("default", run["run_id"])  # noqa: SLF001
    manifest["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    service._write_run_manifest("default", run["run_id"], manifest)  # noqa: SLF001

    result = service.recover_stale_running_runs("default", stale_after_seconds=60)

    assert result["recovered_count"] == 1
    recovered = result["recovered_runs"][0]
    assert recovered["status"] == "failed"
    assert recovered["model_error"] == "stale_running_recovered"
    assert recovered["leaf_state"]["status"] == "failed"
    assert recovered["leaf_state"]["model_error"] == "stale_running_recovered"
    assert service.get_thread("default", thread["thread_id"])["current_run_status"] == "failed"

    events, _ = service.list_run_events("default", run["run_id"])
    event_types = [event["type"] for event in events]
    assert "run_recovery_started" in event_types
    assert event_types[-1] == "run_failed"
    _assert_no_secret_material(result)


def test_recover_stale_running_run_skips_active_owner_lease(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    service = ConversationService(
        object_store,
        RuntimeRunner(object_store=object_store),
        runtime_instance_id="rt_test",
        run_lease_ttl_seconds=300,
    )
    identity = RuntimeIdentity()
    thread = service.create_thread(
        "default",
        identity,
        CreateThreadRequest(title="Active lease recovery"),
    )
    run = service.create_run(
        "default",
        thread["thread_id"],
        identity,
        CreateRunRequest(user_message="This run has an active owner", stream=True),
        execute_inline=False,
    )
    manifest = service._run_manifest("default", run["run_id"])  # noqa: SLF001
    manifest["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    manifest["owner"] = {
        "runtime_instance_id": "rt_test",
        "fencing_token": "fence_active",
        "acquired_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }
    service._write_run_manifest("default", run["run_id"], manifest)  # noqa: SLF001

    result = service.recover_stale_running_runs("default", stale_after_seconds=60)

    assert result["recovered_count"] == 0
    assert service.get_run("default", run["run_id"])["status"] == "running"
    assert service.get_thread("default", thread["thread_id"])["current_run_status"] == "running"
    _assert_no_secret_material(result)


def test_recover_stale_runs_endpoint_uses_workspace_store(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    service = ConversationService(object_store, RuntimeRunner(object_store=object_store))
    identity = RuntimeIdentity()
    thread = service.create_thread(
        "default",
        identity,
        CreateThreadRequest(title="Recover endpoint"),
    )
    run = service.create_run(
        "default",
        thread["thread_id"],
        identity,
        CreateRunRequest(user_message="Endpoint recovery", stream=True),
        execute_inline=False,
    )
    manifest = service._run_manifest("default", run["run_id"])  # noqa: SLF001
    manifest["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    service._write_run_manifest("default", run["run_id"], manifest)  # noqa: SLF001
    app.dependency_overrides[get_object_store] = lambda: object_store
    app.dependency_overrides[get_conversation_service] = lambda: service
    try:
        client = TestClient(app)
        response = client.post(
            "/workspaces/default/runs/recover-stale",
            params={"stale_after_seconds": 60},
        )
    finally:
        app.dependency_overrides.clear()
        get_object_store.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["recovered_count"] == 1
    assert body["recovered_runs"][0]["run_id"] == run["run_id"]
    assert body["recovered_runs"][0]["status"] == "failed"
    _assert_no_secret_material(body)


def test_sse_rejects_conflicting_cursor_sources(client: TestClient) -> None:
    thread = _post_thread(client, "SSE cursor conflict")
    run = _post_run(client, thread["thread_id"], "Stream completed events")
    events = _get_events(client, run["run_id"])

    response = client.get(
        f"/workspaces/default/runs/{run['run_id']}/events/stream",
        params={"after_event_id": events[0]["event_id"]},
        headers={"Last-Event-ID": events[1]["event_id"]},
    )

    assert response.status_code == 400
    assert response.json()["error_type"] == "invalid_event_cursor"
    _assert_no_secret_material(response.json())


def test_run_detail_and_event_responses_do_not_leak_secret_material(
    client: TestClient,
) -> None:
    thread = _post_thread(client, "Redaction")
    run = _post_run(
        client,
        thread["thread_id"],
        "Never echo sk-test-secret api_key password plaintext ciphertext nonce tag",
    )

    run_response = client.get(f"/workspaces/default/runs/{run['run_id']}")
    events_response = client.get(f"/workspaces/default/runs/{run['run_id']}/events")

    assert run_response.status_code == 200
    assert events_response.status_code == 200
    _assert_no_secret_material(run_response.json())
    _assert_no_secret_material(events_response.json())


def test_message_response_redacts_sensitive_label_value_text(client: TestClient) -> None:
    thread = _post_thread(client, "Message redaction")
    _post_run(
        client,
        thread["thread_id"],
        (
            "api_key=abc password=hunter plaintext: clear ciphertext: enc "
            "nonce: n tag: t master key raw payload provider raw payload"
        ),
    )

    response = client.get(f"/workspaces/default/threads/{thread['thread_id']}/messages")

    assert response.status_code == 200
    _assert_no_secret_material(response.json())


def test_invalid_identifier_returns_400(client: TestClient) -> None:
    response = client.get("/workspaces/default/runs/bad$run/events")

    assert response.status_code == 400
    assert response.json()["error_type"] == "invalid_identifier"


def test_runtime_exception_marks_run_failed_without_stale_running(tmp_path) -> None:
    class ExplodingRuntimeRunner:
        def invoke_for_run(self, **kwargs: Any) -> None:
            _ = kwargs
            raise RuntimeError("runtime exploded")

    object_store = LocalObjectStore(tmp_path / "objects")
    app.dependency_overrides[get_object_store] = lambda: object_store
    app.dependency_overrides[get_conversation_service] = lambda: ConversationService(
        object_store,
        ExplodingRuntimeRunner(),  # type: ignore[arg-type]
    )
    try:
        client = TestClient(app)
        thread = _post_thread(client, "Runtime failure")

        response = client.post(
            f"/workspaces/default/threads/{thread['thread_id']}/runs",
            json={"user_message": "This runtime will fail"},
        )

        assert response.status_code == 200
        run = response.json()
        assert run["status"] == "failed"
        assert run["model_error"] == "RuntimeError"
        assert run["leaf_state"]["status"] == "failed"
        assert run["leaf_state"]["model_error"] == "RuntimeError"

        thread_response = client.get(f"/workspaces/default/threads/{thread['thread_id']}")
        assert thread_response.status_code == 200
        assert thread_response.json()["current_run_status"] == "failed"

        events = _get_events(client, run["run_id"])
        assert [event["type"] for event in events][-2:] == ["model_call_failed", "run_failed"]
        _assert_no_secret_material([run, thread_response.json(), events])
    finally:
        app.dependency_overrides.clear()
        get_object_store.cache_clear()


def test_approval_required_runtime_pauses_run_without_assistant_message(tmp_path) -> None:
    class ApprovalRuntimeRunner:
        def __init__(self) -> None:
            self.calls = 0

        def invoke_for_run(self, **kwargs: Any) -> RuntimeSmokeResponse:
            self.calls += 1
            return RuntimeSmokeResponse(
                run_id=kwargs["run_id"],
                thread_id=kwargs["thread_id"],
                workspace_id=kwargs["workspace_id"],
                status="waiting_approval",
                model_error=None,
                requires_approval=True,
                context_usage={
                    "input_tokens": 3,
                    "output_tokens": 2,
                    "total_tokens": 5,
                    "usage_estimated": True,
                },
                messages=[{"type": "ai", "content": "Waiting for user approval."}],
                tool_results=[
                    {
                        "tool_call_id": "call_approval",
                        "name": "skill_entrypoint_call",
                        "ok": False,
                        "content": {
                            "ok": False,
                            "error_type": "approval_required",
                            "data": {
                                "approval_id": "approval_001",
                                "operation_plan_object_key": (
                                    "workspaces/default/runs/run_approval/"
                                    "skill_runs/skillrun_001/operation_plan.json"
                                ),
                            },
                        },
                        "error_type": "approval_required",
                    }
                ],
                tool_specs=[],
            )

    object_store = LocalObjectStore(tmp_path / "objects")
    runner = ApprovalRuntimeRunner()
    service = ConversationService(
        object_store,
        runner,  # type: ignore[arg-type]
        runtime_instance_id="rt_approval",
    )
    identity = RuntimeIdentity()
    thread = service.create_thread(
        "default",
        identity,
        CreateThreadRequest(title="Approval pause"),
    )
    run = service.create_run(
        "default",
        thread["thread_id"],
        identity,
        CreateRunRequest(user_message="Run approval patch"),
    )

    assert run["status"] == "waiting_approval"
    assert run["assistant_message_id"] is None
    assert run["leaf_state"]["status"] == "waiting_approval"
    assert run["leaf_state"]["requires_approval"] is True
    assert run["leaf_state"]["tool_results"][0]["error_type"] == "approval_required"
    assert service.get_thread("default", thread["thread_id"])["current_run_status"] == (
        "waiting_approval"
    )

    second_execute = service.execute_run(
        workspace_id="default",
        thread_id=thread["thread_id"],
        identity=identity,
        run_id=run["run_id"],
        user_message="Run approval patch",
    )

    assert second_execute["status"] == "waiting_approval"
    assert runner.calls == 1

    messages = service.list_messages("default", thread["thread_id"])
    assert [message["role"] for message in messages] == ["user"]
    events, _ = service.list_run_events("default", run["run_id"])
    event_types = [event["type"] for event in events]
    assert "tool_call_failed" in event_types
    assert event_types[-1] == "run_waiting_approval"
    _assert_no_secret_material([run, second_execute, messages, events])


def test_run_approval_approve_executes_skill_staged_patch_and_resumes_run(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")

    class ApprovalRuntimeRunner:
        def __init__(self) -> None:
            self.calls = 0
            self.approval_id: str | None = None
            self.operation_plan_key: str | None = None

        def invoke_for_run(self, **kwargs: Any) -> RuntimeSmokeResponse:
            self.calls += 1
            if self.calls > 1:
                return RuntimeSmokeResponse(
                    run_id=kwargs["run_id"],
                    thread_id=kwargs["thread_id"],
                    workspace_id=kwargs["workspace_id"],
                    status="completed",
                    model_error=None,
                    requires_approval=False,
                    context_usage={"usage_estimated": True},
                    messages=[
                        {
                            "type": "ai",
                            "content": "Approved Skill patch execution finished.",
                        }
                    ],
                    tool_results=[],
                    tool_specs=[],
                )

            from app.schemas.skill import (
                SkillActivateRequest,
                SkillCreateFromProposalRequest,
                SkillEntrypointSpec,
                SkillPermissions,
                SkillProposalRequest,
                SkillSource,
                SkillValidateRequest,
            )
            from app.skills.service import SkillService

            identity = RuntimeIdentity()
            skill_service = SkillService(object_store)
            script_content = (
                "def main(args):\n"
                "    print('approved skill patch')\n"
                "    return {'files': [{'path': 'workspace/reports/summary.md', "
                "'content': '# Summary\\n' + args['document_id'] + '\\n'}]}\n"
            )
            proposal = skill_service.create_proposal(
                kwargs["workspace_id"],
                identity,
                SkillProposalRequest(
                    display_name="Contract patch skill",
                    description="Generate an approved report patch.",
                    when_to_use=["When a report patch is required."],
                    workflow_steps=["Generate approved patch."],
                    knowledge_notes=[],
                    entrypoints=[
                        SkillEntrypointSpec(
                            name="normalize_contract",
                            type="script",
                            runtime="python",
                            args_schema={
                                "type": "object",
                                "required": ["document_id"],
                                "properties": {"document_id": {"type": "string"}},
                            },
                            risk_level="medium",
                            script_required=True,
                            write_mode="staged_patch",
                            file_write=["workspace/reports/**"],
                            script_content=script_content,
                        )
                    ],
                    permissions=SkillPermissions(),
                    script_required=True,
                    source=SkillSource(created_by="agent"),
                ),
            )
            detail = skill_service.materialize_proposal(
                kwargs["workspace_id"],
                identity,
                SkillCreateFromProposalRequest(
                    proposal_id=proposal["proposal_id"],
                    approval_id=proposal["approval_id"],
                    skill_id="contract_patch_skill",
                    version="0.1.0",
                ),
            )
            skill_service.validate_skill_scripts(
                kwargs["workspace_id"],
                detail["skill_id"],
                SkillValidateRequest(version="0.1.0"),
            )
            activation = skill_service.activate_skill(
                kwargs["workspace_id"],
                detail["skill_id"],
                SkillActivateRequest(
                    run_id=kwargs["run_id"],
                    thread_id=kwargs["thread_id"],
                    reason="Need an approved report patch.",
                ),
            )
            result = skill_service.execute_activated_entrypoint(
                workspace_id=kwargs["workspace_id"],
                run_id=kwargs["run_id"],
                thread_id=kwargs["thread_id"],
                entrypoint_tool_name=activation["activated_entrypoint_tools"][0],
                args={"document_id": "doc_001"},
                tool_call_id="call_skill_001",
            )
            self.approval_id = result["data"]["approval_id"]
            self.operation_plan_key = result["artifacts"]["operation_plan_object_key"]
            return RuntimeSmokeResponse(
                run_id=kwargs["run_id"],
                thread_id=kwargs["thread_id"],
                workspace_id=kwargs["workspace_id"],
                status="waiting_approval",
                model_error=None,
                requires_approval=True,
                context_usage={"usage_estimated": True},
                messages=[{"type": "ai", "content": "Waiting for user approval."}],
                tool_results=[
                    {
                        "tool_call_id": "call_skill_001",
                        "name": "skill_entrypoint_call",
                        "ok": False,
                        "content": result,
                        "error_type": "approval_required",
                    }
                ],
                tool_specs=[],
            )

    runner = ApprovalRuntimeRunner()
    service = ConversationService(
        object_store,
        runner,  # type: ignore[arg-type]
    )
    identity = RuntimeIdentity()
    thread = service.create_thread(
        "default",
        identity,
        CreateThreadRequest(title="Approve plan"),
    )
    run = service.create_run(
        "default",
        thread["thread_id"],
        identity,
        CreateRunRequest(user_message="Need approval"),
    )
    app.dependency_overrides[get_conversation_service] = lambda: service
    try:
        client = TestClient(app)
        response = client.post(
            f"/workspaces/default/runs/{run['run_id']}/approvals/{runner.approval_id}/approve",
            json={"reason": "Looks good."},
        )
        repeated = client.post(
            f"/workspaces/default/runs/{run['run_id']}/approvals/{runner.approval_id}/approve",
            json={"reason": "Looks good."},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "approved"
    assert body["status"] == "executed", body
    assert body["run_status"] == "completed"
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "executed"

    operation_plan = json.loads(object_store.read_text(body["operation_plan_object_key"]))
    assert operation_plan["status"] == "executed"
    assert operation_plan["decision"]["decision"] == "approved"
    assert operation_plan["script_execution"] == "executed"
    assert operation_plan["overlay_diff_status"] == "generated"
    assert operation_plan["changed_files"] == ["workspace/reports/summary.md"]
    assert operation_plan["diff_summary"]["files_changed"] == 1
    diff_text = object_store.read_text(operation_plan["artifacts"]["diff_object_key"])
    assert "doc_001" in diff_text
    workspace_file_key = workspace_file_object_key("default", "workspace/reports/summary.md")
    assert object_store.exists(workspace_file_key)
    assert object_store.read_text(workspace_file_key) == "# Summary\ndoc_001\n"
    operation_keys = object_store.list_keys(
        run_operations_prefix("default", run["run_id"])
    )
    assert operation_keys
    operations_text = "\n".join(object_store.read_text(key) for key in operation_keys)
    assert operation_plan["operation_id"] in operations_text
    assert '"workspace_commit_status":"committed"' in operations_text

    app.dependency_overrides[get_conversation_service] = lambda: service
    try:
        client = TestClient(app)
        rollback_response = client.post(
            (
                f"/workspaces/default/runs/{run['run_id']}/operations/"
                f"{operation_plan['operation_id']}/rollback"
            ),
            json={
                "idempotency_key": "rollback_contract_patch",
                "reason": "Undo generated report.",
                "rollback_token": operation_plan["rollback_token"],
            },
        )
        repeated_rollback = client.post(
            (
                f"/workspaces/default/runs/{run['run_id']}/operations/"
                f"{operation_plan['operation_id']}/rollback"
            ),
            json={
                "idempotency_key": "rollback_contract_patch_repeat",
                "reason": "Repeat rollback.",
                "rollback_token": operation_plan["rollback_token"],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert rollback_response.status_code == 200
    rollback_body = rollback_response.json()
    assert rollback_body["status"] == "rolled_back"
    assert rollback_body["operation_id"] == operation_plan["operation_id"]
    assert rollback_body["restored_files"][0]["rollback_action"] == "delete_created_file"
    assert repeated_rollback.status_code == 200
    assert repeated_rollback.json()["status"] == "rolled_back"
    assert not object_store.exists(workspace_file_key)

    run_detail = service.get_run("default", run["run_id"])
    assert run_detail["status"] == "completed"
    assert run_detail["leaf_state"]["approval_execution_pending"] is False
    assert run_detail["leaf_state"]["approved_skill_result"]["ok"] is True
    events, _ = service.list_run_events("default", run["run_id"])
    event_types = [event["type"] for event in events]
    assert "approval_approved" in event_types
    assert "approval_execution_completed" in event_types
    assert "skill_entrypoint_approval_execution_completed" in event_types
    assert "operation_rolled_back" in event_types
    assert "run_completed" in event_types
    assert event_types[-1] == "operation_rolled_back"
    _assert_no_secret_material([body, repeated.json(), rollback_body, run_detail, events])


def test_tool_invocation_approval_executes_tool_and_resumes_run(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    invoked: list[str] = []

    class DeleteArgs(BaseModel):
        target_id: str

    def delete_remote_record(target_id: str) -> dict[str, Any]:
        invoked.append(target_id)
        return {"ok": True, "deleted": target_id}

    class ApprovalResumeConnector:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def call(
            self,
            workspace_id: str,
            config: ModelConfig,
            request: ModelRequest,
        ) -> ModelResult:
            _ = workspace_id, config
            self.requests.append(request)
            if len(self.requests) == 1:
                return ModelResult(
                    content="I need approval before deleting the remote record.",
                    tool_calls=[
                        ModelToolCall(
                            tool_call_id="call_delete_001",
                            name="delete_remote_record",
                            args={"target_id": "doc_001"},
                        )
                    ],
                    usage=ModelUsage(input_tokens=3, output_tokens=3, total_tokens=6),
                )
            request_text = json.dumps(
                [message.model_dump() for message in request.messages],
                ensure_ascii=False,
                default=str,
            )
            assert "doc_001" in request_text
            return ModelResult(
                content="Approved tool execution finished for doc_001.",
                tool_calls=[],
                usage=ModelUsage(input_tokens=5, output_tokens=5, total_tokens=10),
            )

        def stream(
            self,
            workspace_id: str,
            config: ModelConfig,
            request: ModelRequest,
        ):
            result = self.call(workspace_id, config, request)
            yield ModelStreamEvent(type="message_start", request_id=request.request_id)
            if result.tool_calls:
                for index, call in enumerate(result.tool_calls):
                    yield ModelStreamEvent(
                        type="tool_call_delta",
                        request_id=request.request_id,
                        tool_call_delta=ToolCallDelta(
                            index=index,
                            tool_call_id=call.tool_call_id,
                            name=call.name,
                            args=call.args,
                        ),
                    )
            if result.content:
                yield ModelStreamEvent(
                    type="content_delta",
                    request_id=request.request_id,
                    delta=result.content,
                )
            yield ModelStreamEvent(
                type="usage_delta",
                request_id=request.request_id,
                usage=result.usage,
            )
            yield ModelStreamEvent(type="message_completed", request_id=request.request_id)
            yield ModelStreamEvent(type="stream_closed", request_id=request.request_id)

    connector = ApprovalResumeConnector()
    registry = ToolRegistry(
        [
            StructuredTool.from_function(
                func=delete_remote_record,
                name="delete_remote_record",
                description="Delete a remote record after explicit user approval.",
                args_schema=DeleteArgs,
                metadata={
                    "source": "mcp",
                    "risk_level": "high",
                    "requires_approval": True,
                },
            )
        ],
        object_store=object_store,
    )
    service = ConversationService(
        object_store,
        RuntimeRunner(
            llm_connector=connector,  # type: ignore[arg-type]
            model_config=ModelConfig(provider="fake", model="approval-resume-test"),
            object_store=object_store,
            tool_registry=registry,
        ),
    )
    identity = RuntimeIdentity()
    thread = service.create_thread(
        "default",
        identity,
        CreateThreadRequest(title="Tool approval resume"),
    )
    run = service.create_run(
        "default",
        thread["thread_id"],
        identity,
        CreateRunRequest(user_message="Delete the remote record if approved."),
    )
    approval_id = run["leaf_state"]["tool_results"][0]["content"]["data"]["approval_id"]

    approved = service.resolve_run_approval(
        workspace_id="default",
        run_id=run["run_id"],
        approval_id=approval_id,
        decision="approved",
        identity=identity,
        reason="User confirmed the destructive action.",
    )
    repeated = service.resolve_run_approval(
        workspace_id="default",
        run_id=run["run_id"],
        approval_id=approval_id,
        decision="approved",
        identity=identity,
        reason="Repeated confirmation should be idempotent.",
    )

    operation_plan = json.loads(object_store.read_text(approved["operation_plan_object_key"]))
    run_detail = service.get_run("default", run["run_id"])
    messages = service.list_messages("default", thread["thread_id"])
    events, _ = service.list_run_events("default", run["run_id"])
    event_types = [event["type"] for event in events]

    assert invoked == ["doc_001"]
    assert len(connector.requests) == 2
    assert approved["status"] == "executed"
    assert approved["run_status"] == "completed"
    assert repeated["status"] == "executed"
    assert repeated["run_status"] == "completed"
    assert operation_plan["status"] == "executed"
    assert operation_plan["tool_call_id"] == "call_delete_001"
    assert operation_plan["execution_result"]["ok"] is True
    assert run_detail["status"] == "completed"
    assert run_detail["leaf_state"]["approved_tool_result"]["ok"] is True
    assert [message["role"] for message in messages][-2:] == ["tool", "assistant"]
    assert messages[-1]["content"] == "Approved tool execution finished for doc_001."
    assert "approval_execution_started" in event_types
    assert "approval_execution_completed" in event_types
    assert event_types[-1] == "run_completed"
    _assert_no_secret_material([approved, repeated, operation_plan, run_detail, messages, events])


def test_run_approval_reject_marks_plan_and_cancels_run(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")

    class ApprovalRuntimeRunner:
        def invoke_for_run(self, **kwargs: Any) -> RuntimeSmokeResponse:
            operation_plan_key = run_skill_run_artifact_key(
                kwargs["workspace_id"],
                kwargs["run_id"],
                "skillrun_001",
                "operation_plan.json",
            )
            object_store.write_text(
                operation_plan_key,
                json.dumps(
                    {
                        "schema_version": 1,
                        "approval_id": "approval_001",
                        "approval_kind": "skill_script_staged_patch",
                        "status": "waiting_approval",
                        "stage": "approval_required",
                        "approval_ready": True,
                        "workspace_id": kwargs["workspace_id"],
                        "thread_id": kwargs["thread_id"],
                        "run_id": kwargs["run_id"],
                        "skill_run_id": "skillrun_001",
                        "artifacts": {"operation_plan_object_key": operation_plan_key},
                        "revision": 1,
                    },
                    ensure_ascii=False,
                ),
            )
            return RuntimeSmokeResponse(
                run_id=kwargs["run_id"],
                thread_id=kwargs["thread_id"],
                workspace_id=kwargs["workspace_id"],
                status="waiting_approval",
                model_error=None,
                requires_approval=True,
                context_usage={"usage_estimated": True},
                messages=[{"type": "ai", "content": "Waiting for user approval."}],
                tool_results=[
                    {
                        "tool_call_id": "call_approval",
                        "name": "skill_entrypoint_call",
                        "ok": False,
                        "content": {
                            "ok": False,
                            "error_type": "approval_required",
                            "data": {"approval_id": "approval_001"},
                        },
                        "error_type": "approval_required",
                    }
                ],
                tool_specs=[],
            )

    service = ConversationService(
        object_store,
        ApprovalRuntimeRunner(),  # type: ignore[arg-type]
    )
    identity = RuntimeIdentity()
    thread = service.create_thread(
        "default",
        identity,
        CreateThreadRequest(title="Reject plan"),
    )
    run = service.create_run(
        "default",
        thread["thread_id"],
        identity,
        CreateRunRequest(user_message="Need approval"),
    )
    app.dependency_overrides[get_conversation_service] = lambda: service
    try:
        client = TestClient(app)
        response = client.post(
            f"/workspaces/default/runs/{run['run_id']}/approvals/approval_001/reject",
            json={"reason": "Do not apply this change."},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "rejected"
    assert body["status"] == "rejected"
    assert body["run_status"] == "cancelled"

    operation_plan = json.loads(object_store.read_text(body["operation_plan_object_key"]))
    assert operation_plan["status"] == "rejected"
    assert operation_plan["decision"]["decision"] == "rejected"

    run_detail = service.get_run("default", run["run_id"])
    assert run_detail["status"] == "cancelled"
    assert run_detail["leaf_state"]["requires_approval"] is False
    assert run_detail["leaf_state"]["model_error"] == "approval_rejected"
    events, _ = service.list_run_events("default", run["run_id"])
    event_types = [event["type"] for event in events]
    assert "approval_rejected" in event_types
    assert event_types[-1] == "run_cancelled"
    _assert_no_secret_material([body, run_detail, events])
