from __future__ import annotations

from app.schemas.model import ModelConfig, ModelError, ModelMessage, ModelUsage


class TokenBudgetManager:
    def estimate_prompt_tokens(self, messages: list[ModelMessage]) -> int:
        total = 0
        for message in messages:
            # Conservative offline estimate: chars/3 plus role and message framing overhead.
            total += max(1, (len(message.content) + 2) // 3) + 8
        return total

    def enforce(
        self,
        config: ModelConfig,
        messages: list[ModelMessage],
        max_output_tokens: int,
    ) -> ModelUsage:
        prompt_tokens = self.estimate_prompt_tokens(messages)
        total_tokens = prompt_tokens + max_output_tokens
        if total_tokens > config.context_window_tokens:
            raise ModelError(
                "context_overflow",
                "Prompt and max output exceed model context window.",
                retryable=False,
            )
        return ModelUsage(
            input_tokens=prompt_tokens,
            output_tokens=0,
            total_tokens=prompt_tokens,
            usage_estimated=True,
        )

