# 模型 API 配置与 Token 预算设计

## 参考源码标注

本文件中 Provider Adapter、context overflow 识别、usage 兜底、streaming tool call delta 和消息格式校验，参考以下源码后按本项目模型配置页和 Runtime 协议重写：

| 本项目设计点 | 参考源码路径 | GitHub 地址 | 本项目落地 |
| --- | --- | --- | --- |
| 多 provider overflow 错误识别 | `.research_repos\pi\packages\ai\src\utils\overflow.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/ai/src/utils/overflow.ts` | overflow 后 compact-and-retry 最多一次 |
| Provider 消息转换 | `.research_repos\pi\packages\ai\src\providers\transform-messages.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/ai/src/providers/transform-messages.ts` | ModelRequest -> provider payload |
| OpenAI-compatible provider | `.research_repos\pi\packages\ai\src\providers\openai-completions.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/ai/src/providers/openai-completions.ts` | Chat Completions 兼容接口 |
| Responses provider / streaming tool delta | `.research_repos\pi\packages\ai\src\providers\openai-codex-responses.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/ai/src/providers/openai-codex-responses.ts` | Responses 风格流式事件、tool call delta |
| 模型能力和上下文窗口配置 | `.research_repos\aider\aider\models.py` | `https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/models.py` | 本项目模型配置保存 context_window、max_output、tool_calling、vision |
| 消息 sanity check | `.research_repos\aider\aider\sendchat.py` | `https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/sendchat.py` | 调用前校验连续角色、orphan tool result、空 tools |

## 定位

模型 API 配置负责管理所有 LLM / Embedding / Rerank 调用所需的 provider、base_url、api_key_ref、model、timeout、上下文窗口和输出上限。

Agent Runtime 不能假设 API 每次都会返回模型最大上下文窗口。模型最大上下文窗口由系统配置保存，API 返回的 usage 只用于记录本次调用实际 token 消耗和校准本地计数。

## 默认值

所有 LLM 类模型默认：

```text
context_window_tokens = 200000
max_output_tokens = 8192
```

用户可以在 API 配置页面修改。

适用范围：

- 主对话模型。
- GraphRAG LLM。
- 压缩模型。
- 备用模型。

Embedding / Rerank 不使用 `context_window_tokens` 作为核心配置，但仍需要独立 timeout、batch、top_n 等参数。

P0 默认 Embedding 配置：

```json
{
  "config_id": "embedding",
  "provider": "openai_compatible",
  "model": "text-embedding-v4",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "api_key_ref": "secret_ref_embedding_main",
  "dimension": 1024,
  "timeout_ms": 60000
}
```

Embedding API Key 必须用 `embedding_api_key` 类型单独存入 Secret Store；系统不能从主对话模型的小米 API Key 推断或复用 embedding 密钥。调用 DashScope OpenAI-compatible `/embeddings` 时请求体必须携带 `dimensions: 1024`。

P0 API 配置槽中，`graphrag_llm` 是 GraphRAG 抽取和摘要的统一模型配置。`graph_build_job` 使用它做 chunk batch 实体/关系抽取；GraphRAG 查询阶段如果需要图谱摘要，也复用同一配置，后续再拆成 `graphrag_extraction` / `graphrag_summary` 角色。

## P0 Provider 范围

P0 支持的模型 Provider：

| Provider | 说明 |
| --- | --- |
| `openai_compatible` | 兼容 OpenAI Chat Completions / Responses 风格的供应商和国产模型服务 |
| `anthropic` | Claude 类模型 |

P1 再支持：

```text
gemini
openrouter
ollama
lm_studio
local_openai_compatible
```

本地模型第一版不进入 P0，避免同时处理本地模型上下文、流式协议、工具调用兼容、资源占用和部署体验。

## Provider Adapter 架构

Runtime 不直接依赖任何供应商的原始响应格式。所有模型调用必须经过 Provider Adapter：

```text
Agent Runtime
  -> LLMConnector
    -> ModelProviderAdapter
      -> OpenAICompatibleProvider
      -> AnthropicProvider
```

Provider Adapter 负责把不同供应商的请求、流式事件、tool call、usage 和错误转换成系统内部标准结构。

内部标准结构：

```text
ModelRequest
ModelStreamEvent
ModelResult
ModelUsage
ModelError
ToolCallDelta
```

### ModelRequest

```json
{
  "request_id": "modelreq_001",
  "model_config_id": "main_chat_default",
  "provider": "openai_compatible",
  "model": "model-name",
  "messages": [],
  "tools": [],
  "tool_choice": "auto",
  "temperature": 0.7,
  "max_output_tokens": 8192,
  "stream": true,
  "metadata": {
    "workspace_id": "default",
    "thread_id": "thread_001",
    "run_id": "run_001"
  }
}
```

`api_key_ref` 指向 Secret Store 中的加密密钥对象。前端、日志、SSE 事件和 ToolResult 只能显示 `secret_ref` 或 masked 值；模型调用时由后端内部 `SecretResolver` 解密。

模型调用伪代码：

```python
class LLMConnector:
    def call(self, workspace_id: str, config: dict, request: ModelRequest) -> ModelResult:
        api_key = secret_resolver.resolve(
            workspace_id=workspace_id,
            secret_ref=config["api_key_ref"],
            purpose="model_call",
        ).plaintext
        provider = provider_factory.create(
            provider=config["provider"],
            base_url=config["base_url"],
            api_key=api_key,
        )
        return provider.call(request)
```

### ModelStreamEvent

```json
{
  "type": "content_delta",
  "request_id": "modelreq_001",
  "delta": "你好",
  "tool_call_delta": null,
  "finish_reason": null,
  "usage": null
}
```

事件类型：

```text
message_start
content_delta
tool_call_start
tool_call_delta
tool_call_completed
usage_delta
message_completed
provider_error
stream_closed
```

### ModelResult

```json
{
  "request_id": "modelreq_001",
  "assistant_message": {
    "content": "最终回答",
    "tool_calls": []
  },
  "finish_reason": "stop",
  "usage": {
    "input_tokens": 12000,
    "output_tokens": 1800,
    "total_tokens": 13800,
    "usage_estimated": false
  },
  "provider_trace": {
    "provider": "openai_compatible",
    "model": "model-name",
    "latency_ms": 3200
  }
}
```

### 模型能力字段

模型配置对象必须保存能力字段，不能完全依赖 API 自动返回：

```json
{
  "supports_streaming": true,
  "supports_tool_calling": true,
  "supports_parallel_tool_calls": false,
  "supports_vision": false,
  "supports_json_mode": true,
  "supports_prompt_cache": false,
  "supports_reasoning_tokens": false,
  "tool_call_protocol": "openai_compatible",
  "stream_protocol": "sse"
}
```

能力字段用途：

- 生成请求前裁剪不支持的参数。
- 非 vision 模型遇到图片时替换为占位文本或拒绝。
- 不支持 tool calling 的模型不能进入需要工具调用的 Agent 模式。
- 不支持 parallel tool calls 时，Runtime 把工具调用串行化。
- 不支持 JSON mode 时，结构化输出走普通文本加校验修复。

## 模型配置对象

```json
{
  "model_config_id": "main_chat_default",
  "role": "main_chat",
  "provider": "openai_compatible",
  "base_url": "https://api.example.com/v1",
  "api_key_ref": "secret_ref_main_chat",
  "model": "model-name",
  "context_window_tokens": 200000,
  "max_output_tokens": 8192,
  "temperature": 0.7,
  "timeout_ms": 120000,
  "streaming": true,
  "supports_streaming": true,
  "supports_tool_calling": true,
  "supports_parallel_tool_calls": false,
  "supports_vision": false,
  "supports_json_mode": true,
  "supports_prompt_cache": false,
  "token_counter": "local_estimator",
  "enabled": true
}
```

角色枚举：

```text
main_chat
graphrag_extraction
graphrag_summary
compression
fallback_chat
embedding
rerank
```

## API 配置页面字段

LLM 类模型页面必须提供：

- provider。
- base_url。
- api_key_ref：通过 Secret Store 创建或选择，不保存明文。
- model。
- context_window_tokens，默认 `200000`。
- max_output_tokens，默认 `8192`。
- temperature。
- timeout。
- streaming enabled。
- test connection。

压缩模型可以复用主模型配置，也可以独立配置。

当用户修改 `context_window_tokens` 后：

- Hermes 压缩阈值随之重新计算。
- session hygiene 二级保护阈值随之重新计算。
- TokenBudgetManager 使用新值做下一次调用前预算。

当用户修改 `max_output_tokens` 后：

- 模型调用请求使用新值。
- 预估 prompt 预算时需要预留新的输出空间。
- 如果 `prompt_tokens + max_output_tokens > context_window_tokens`，必须先压缩或拒绝本次调用。

## TokenBudgetManager

每次模型调用前，Runtime 需要先估算 token：

```text
system / developer messages
memory snapshot
conversation messages
tool schemas
RAG / GraphRAG evidence
pending tool results
expected max_output_tokens
```

预算判断：

```text
usable_input_budget = context_window_tokens - max_output_tokens
compression_threshold_tokens = context_window_tokens * 0.50
session_hygiene_threshold_tokens = context_window_tokens * 0.85
```

默认值下：

```text
context_window_tokens = 200000
max_output_tokens = 8192
usable_input_budget = 191808
compression_threshold_tokens = 100000
session_hygiene_threshold_tokens = 170000
```

调用前策略：

- `prompt_tokens >= compression_threshold_tokens`：触发 Hermes ContextCompressor。
- `prompt_tokens >= session_hygiene_threshold_tokens`：触发二级保护。
- `prompt_tokens + max_output_tokens > context_window_tokens`：必须压缩、裁剪可回源证据或拒绝调用。

## Context Overflow 策略

`context_overflow` 表示模型请求的输入和预留输出超过模型上下文窗口。

触发来源：

```text
1. Runtime 调用前本地预算发现 prompt_tokens + max_output_tokens > context_window_tokens。
2. Provider API 返回 context length exceeded / maximum context length / token limit exceeded 等错误。
```

P0 策略：

```text
context_overflow
  -> 触发 Hermes ContextCompressor
  -> 裁剪可回源 RAG / GraphRAG 证据
  -> 重新计算 token budget
  -> 最多重试 1 次模型调用
  -> 仍然 overflow 时返回用户可见错误
```

限制：

- 不能无限压缩重试。
- 不能静默丢弃系统规则、用户最近目标、pending approval、当前工具调用、关键证据来源。
- 如果是单次用户输入或单次文件片段过大导致 overflow，应提示用户缩小输入或减少证据范围。
- overflow 重试必须写入 `model_call_failed`、`compaction_requested`、`compaction_completed`、`model_call_retry` 事件。

LangGraph 节点伪代码：

```python
def call_model_node(state: AgentState) -> dict:
    request = model_request_builder.build(state)
    budget = token_budget_manager.estimate(request)
    if budget.prompt_tokens + request.max_output_tokens > request.context_window_tokens:
        if state.get("overflow_retry_count", 0) >= 1:
            return {"model_error": ModelError.context_overflow(final=True)}
        compacted = context_preflight_graph.invoke(
            state,
            config={"configurable": {"thread_id": state["thread_id"]}},
        )
        return {
            "messages": compacted["messages"],
            "compaction": compacted["compaction"],
            "overflow_retry_count": state.get("overflow_retry_count", 0) + 1,
            "next": "call_model",
        }

    try:
        return llm_connector.invoke(request)
    except ModelContextOverflowError as exc:
        event_store.append_event(state["run_id"], model_error_event(exc))
        if state.get("overflow_retry_count", 0) >= 1:
            return {"model_error": ModelError.context_overflow(final=True)}
        compacted = context_preflight_graph.invoke(
            state,
            config={"configurable": {"thread_id": state["thread_id"]}},
        )
        return {
            "messages": compacted["messages"],
            "compaction": compacted["compaction"],
            "overflow_retry_count": state.get("overflow_retry_count", 0) + 1,
            "next": "call_model",
        }
```

用户可见错误示例：

```json
{
  "error_type": "context_overflow",
  "message_for_user": "当前对话、工具结果或检索证据过长，系统已自动压缩并重试一次，但仍超过模型上下文上限。请减少输入内容、缩小知识库证据范围，或在 API 配置页调大 context_window_tokens。",
  "retryable": false
}
```

## API usage 回写

模型响应后，Runtime 读取 provider 返回的 usage：

```json
{
  "input_tokens": 12000,
  "output_tokens": 1800,
  "total_tokens": 13800
}
```

usage 用于：

- 记录成本。
- 记录延迟和吞吐。
- 校准本地 token 估算。
- 判断是否需要更新 ContextEngine 状态。
- 前端展示本次调用 token 消耗。

usage 不用于判断模型最大上下文窗口。Runtime 使用 `context_window_tokens` 配置计算最大上下文窗口。

## 失败策略

- provider 不返回 usage：使用本地估算值记录，并标记 `usage_estimated=true`。
- 本地 token counter 不支持某模型：使用保守估算，并提示用户确认上下文窗口配置。
- 模型返回 context length exceeded：分类为 `context_overflow`，触发 Hermes 压缩后最多重试一次。
- 用户配置的 `max_output_tokens` 大于 `context_window_tokens`：配置校验失败。
- 用户配置过小的 `context_window_tokens`：允许保存，但 API 页面需要提示会更频繁触发压缩。

## Provider 错误分类

Provider Adapter 必须把不同供应商错误统一成 `ModelError`：

| error_type | 是否重试 | 处理 |
| --- | --- | --- |
| `rate_limit` | 是 | 指数退避，受 retry_budget 限制 |
| `provider_5xx` | 是 | 重试；连续失败进入 circuit breaker |
| `timeout` | 是 | 重试；超过总延迟预算后 fallback |
| `connection_lost` | 是 | 流式调用可从 LangGraph checkpoint 重新生成 |
| `stream_ended_before_terminal` | 是 | 标记中断，必要时重新生成完整答案 |
| `context_overflow` | 特殊 | Hermes 压缩后最多重试一次 |
| `auth_failed` | 否 | 提示检查 API key |
| `model_not_found` | 否 | 提示检查模型名和 provider |
| `billing_required` | 否 | 提示检查账户余额或套餐 |
| `quota_exceeded` | 否 | 不自动重试，可切 fallback |
| `invalid_request` | 否 | 配置或请求结构错误 |
| `content_filter` | 否 | 返回安全限制说明 |
| `unsupported_feature` | 否 | 裁剪功能或提示更换模型 |
| `unknown` | 视情况 | 默认不盲目重试 |

## 流式 Tool Call Delta

不同 provider 对流式工具调用的返回格式不同，Adapter 必须统一组装：

```text
provider raw delta
  -> ToolCallDelta
  -> partial args buffer
  -> tool_call_completed
  -> Tool Executor validate args
```

规则：

- tool_call_id 缺失时由 Runtime 生成稳定 ID。
- 同一个 tool call 的 partial arguments 必须按 index / id 合并。
- JSON arguments 不完整时不能提前执行工具。
- 流结束后仍有未完成 tool call，生成 `orphan_tool_call` 错误 ToolResult，不能让下一轮 replay 崩坏。
- provider 不支持 parallel tool calls 时，同一轮只执行一个 tool call。

## Provider 兼容测试矩阵

P0 至少覆盖：

| 场景 | 预期 |
| --- | --- |
| OpenAI-compatible 非流式成功 | 生成 ModelResult 和 usage |
| OpenAI-compatible 流式 content delta | 前端收到统一 SSE token / assistant_delta |
| OpenAI-compatible tool call delta | 正确组装 ToolCallDelta |
| Anthropic 流式 content 和 tool use | 转成统一 ModelStreamEvent |
| provider 不返回 usage | 使用本地估算并标记 usage_estimated |
| context overflow | Hermes 压缩并最多重试一次 |
| auth_failed / model_not_found | 不重试，返回可见配置错误 |

P1 再补：

| 场景 | 预期 |
| --- | --- |
| Gemini 候选响应 | 转成统一 assistant message |
| OpenRouter 错误透传 | 分类成统一 ModelError |
