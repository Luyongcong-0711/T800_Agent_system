from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from app.core.ids import new_id
from app.core.time import utc_now_iso

SUMMARY_PREFIX = "[CONTEXT COMPACTION - REFERENCE ONLY]"


@dataclass
class ContextCompressionResult:
    messages: list[Any]
    compaction: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    failed_reason: str | None = None

    def __iter__(self):
        yield self.messages
        yield self.compaction or {}


class HermesStyleContextCompressor:
    def __init__(
        self,
        *,
        threshold: float = 0.50,
        target_ratio: float = 0.20,
        summary_target_ratio: float | None = None,
        protect_first_n: int = 3,
        protect_last_n: int = 20,
        session_hygiene_threshold: float = 0.85,
    ) -> None:
        self.threshold = threshold
        self.target_ratio = (
            summary_target_ratio if summary_target_ratio is not None else target_ratio
        )
        self.protect_first_n = protect_first_n
        self.protect_last_n = protect_last_n
        self.session_hygiene_threshold = session_hygiene_threshold

    def should_compress(
        self,
        prompt_tokens: int,
        usable_input_budget: int | None = None,
        *,
        model_context_limit: int | None = None,
    ) -> bool:
        budget = usable_input_budget if usable_input_budget is not None else model_context_limit
        threshold_tokens = int((budget or 200000) * self.threshold)
        return prompt_tokens >= threshold_tokens

    def should_force_compress(
        self,
        prompt_tokens: int,
        context_window_tokens: int,
        max_output_tokens: int,
    ) -> bool:
        return prompt_tokens + max_output_tokens > context_window_tokens

    def should_session_hygiene_compress(
        self,
        prompt_tokens: int,
        context_window_tokens: int,
    ) -> bool:
        return prompt_tokens >= int(context_window_tokens * self.session_hygiene_threshold)

    def compress(
        self,
        *,
        workspace_id: str = "default",
        thread_id: str = "unknown_thread",
        run_id: str = "unknown_run",
        messages: list[Any],
        current_tokens: int,
        context_window_tokens: int = 200000,
        focus_topic: str | None = None,
        previous_summary: str | None = None,
    ) -> ContextCompressionResult:
        if len(messages) <= self.protect_first_n + self.protect_last_n:
            return ContextCompressionResult(
                messages=messages,
                warnings=["compression_skipped_not_enough_middle_messages"],
            )

        head = messages[: self.protect_first_n]
        tail = self._protect_tail_pairs(messages[self.protect_first_n :])
        if len(tail) > len(messages) - self.protect_first_n:
            tail = messages[-self.protect_last_n :]
        middle_end = len(messages) - len(tail)
        middle = messages[self.protect_first_n : middle_end]
        if not middle:
            return ContextCompressionResult(
                messages=messages,
                warnings=["compression_skipped_empty_middle"],
            )

        summary = self._build_reference_summary(
            middle,
            focus_topic=focus_topic,
            previous_summary=previous_summary,
        )
        summary_message = self._summary_message(
            summary,
            compaction_id=new_id("cmpmsg"),
            like=messages[0],
        )
        compacted = [*head, summary_message, *tail]
        compaction_id = new_id("cmp")
        compaction = {
            "schema_version": 1,
            "compaction_id": compaction_id,
            "thread_id": thread_id,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "summary": summary,
            "strategy": "hermes_style_head_summary_tail",
            "source_message_ids": self._message_ids(middle),
            "head_message_ids": self._message_ids(head),
            "tail_message_ids": self._message_ids(tail),
            "open_questions": [],
            "preserved_refs": [],
            "prompt_tokens_before": current_tokens,
            "context_window_tokens": context_window_tokens,
            "created_at": utc_now_iso(),
        }
        return ContextCompressionResult(messages=compacted, compaction=compaction)

    def _protect_tail_pairs(self, messages: list[Any]) -> list[Any]:
        tail = list(messages[-self.protect_last_n :])
        if not tail:
            return tail
        first_tail = tail[0]
        if self._is_tool_message(first_tail):
            for index in range(len(messages) - len(tail) - 1, -1, -1):
                candidate = messages[index]
                if self._is_assistant_message(candidate):
                    tail.insert(0, candidate)
                    break
        tool_call_ids = {
            self._tool_result_call_id(message)
            for message in tail
            if self._is_tool_message(message)
        }
        if not tool_call_ids:
            return tail
        known_text = "\n".join(self._message_content(message) for message in tail)
        for index in range(len(messages) - len(tail) - 1, -1, -1):
            candidate = messages[index]
            candidate_call_ids = self._assistant_tool_call_ids(candidate)
            if self._is_assistant_message(candidate) and (
                candidate_call_ids & tool_call_ids
                or any(
                    tool_call_id and tool_call_id in self._message_content(candidate)
                    for tool_call_id in tool_call_ids
                )
            ):
                if self._message_content(candidate) not in known_text:
                    tail.insert(0, candidate)
                break
        return tail

    def _build_reference_summary(
        self,
        messages: list[Any],
        *,
        focus_topic: str | None,
        previous_summary: str | None,
    ) -> str:
        role_counts: dict[str, int] = {}
        tool_results = 0
        snippets: list[str] = []
        for message in messages:
            role = self._message_role(message)
            role_counts[role] = role_counts.get(role, 0) + 1
            if self._is_tool_message(message):
                tool_results += 1
            text = self._message_content(message).replace("\r", " ").replace("\n", " ").strip()
            if text:
                snippets.append(text[:180])
        sections = [
            SUMMARY_PREFIX,
            "## Active Task",
            focus_topic or "Continue the current user task.",
            "## Current State",
            f"Compressed {len(messages)} middle messages. Role counts: {role_counts}.",
        ]
        if previous_summary:
            sections.extend(["## Previous Summary", previous_summary[:1200]])
        if snippets:
            sections.extend(
                [
                    "## Completed Actions",
                    "\n".join(f"- {item}" for item in snippets[:8]),
                ]
            )
        if tool_results:
            sections.extend(
                [
                    "## Tool Results",
                    f"{tool_results} historical tool result(s) summarized.",
                ]
            )
        sections.extend(
            [
                "## Remaining Work",
                "Use the protected recent tail messages as the source of truth.",
            ]
        )
        return "\n".join(sections)

    @staticmethod
    def _message_ids(messages: list[Any]) -> list[str]:
        ids: list[str] = []
        for index, message in enumerate(messages):
            if isinstance(message, dict):
                msg_id = message.get("message_id") or message.get("id") or message.get("name")
            else:
                msg_id = getattr(message, "id", None) or getattr(message, "name", None)
            ids.append(str(msg_id or f"message_{index}"))
        return ids

    @staticmethod
    def _summary_message(summary: str, *, compaction_id: str, like: Any) -> Any:
        if isinstance(like, dict):
            return {
                "message_id": f"{compaction_id}_summary",
                "role": "system",
                "content": summary,
            }
        return SystemMessage(content=summary)

    @staticmethod
    def _message_role(message: Any) -> str:
        if isinstance(message, dict):
            return str(message.get("role") or message.get("type") or "unknown")
        return str(getattr(message, "type", "unknown"))

    @staticmethod
    def _message_content(message: Any) -> str:
        if isinstance(message, dict):
            return str(message.get("content") or "")
        return str(getattr(message, "content", ""))

    def _is_tool_message(self, message: Any) -> bool:
        if isinstance(message, dict):
            return self._message_role(message) == "tool"
        return isinstance(message, ToolMessage)

    def _is_assistant_message(self, message: Any) -> bool:
        if isinstance(message, dict):
            return self._message_role(message) in {"assistant", "ai"}
        return isinstance(message, AIMessage)

    @staticmethod
    def _tool_result_call_id(message: Any) -> str:
        if isinstance(message, dict):
            return str(message.get("tool_call_id") or "")
        return str(getattr(message, "tool_call_id", ""))

    @staticmethod
    def _assistant_tool_call_ids(message: Any) -> set[str]:
        calls: Any = []
        if isinstance(message, dict):
            calls = message.get("tool_calls") or []
        else:
            calls = getattr(message, "tool_calls", None) or getattr(
                message,
                "additional_kwargs",
                {},
            ).get("tool_calls", [])
        call_ids: set[str] = set()
        for call in calls:
            if isinstance(call, dict):
                call_id = call.get("id") or call.get("tool_call_id")
            else:
                call_id = getattr(call, "id", None) or getattr(call, "tool_call_id", None)
            if call_id:
                call_ids.add(str(call_id))
        return call_ids
