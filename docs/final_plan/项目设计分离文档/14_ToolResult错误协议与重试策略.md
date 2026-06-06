# ToolResult、错误协议与重试策略

## 参考源码标注

本文件中统一 ToolResult、工具调用事件、错误分类、重试和 provider/tool 边界，参考以下源码后按本项目协议重写：

| 本项目设计点 | 参考源码路径 | GitHub 地址 | 本项目落地 |
| --- | --- | --- | --- |
| tool usage lifecycle | `.research_repos\crewai\lib\crewai\src\crewai\tools\tool_usage.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/tools/tool_usage.py` | ToolResult、tool_call_start/update/end、重复调用检测 |
| tool caller loop | `.research_repos\autogen\python\packages\autogen-core\src\autogen_core\tool_agent\_caller_loop.py` | `https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/python/packages/autogen-core/src/autogen_core/tool_agent/_caller_loop.py` | 工具调用循环、结果回传、max iterations |
| provider overflow 错误识别 | `.research_repos\pi\packages\ai\src\utils\overflow.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/ai/src/utils/overflow.ts` | context_overflow 分类，触发 compact-and-retry |
| 消息和工具结果 sanity check | `.research_repos\aider\aider\sendchat.py` | `https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/sendchat.py` | orphan tool result、连续角色、空 tools 检查 |

## ToolResult 最小字段

所有工具必须返回统一 JSON。

最小字段：

```text
ok
tool
data 或 error_type
retryable
recoverable_by_model
next_action
trace.tool_call_id
```

## 成功结果

```json
{
  "ok": true,
  "tool": "vector_search",
  "stage": "retrieval",
  "data": {
    "hits": []
  },
  "warnings": [],
  "trace": {
    "trace_id": "trace_001",
    "tool_call_id": "call_001",
    "attempt": 1,
    "latency_ms": 120
  }
}
```

空结果：

```json
{
  "ok": true,
  "tool": "vector_search",
  "stage": "retrieval",
  "data": [],
  "warnings": ["没有找到匹配结果"],
  "next_action": "try_graph_or_answer_with_limitation"
}
```

空结果不是错误。

## 可重试失败

```json
{
  "ok": false,
  "tool": "milvus_search",
  "stage": "retrieval",
  "error_type": "transient_connection_error",
  "retryable": true,
  "recoverable_by_model": false,
  "attempt": 1,
  "max_attempts": 3,
  "message_for_model": "Milvus connection timeout. Runtime will retry.",
  "message_for_user": "向量检索连接超时，系统正在重试。",
  "next_action": "retry_same_tool"
}
```

## 模型可修复失败

```json
{
  "ok": false,
  "tool": "graph_find_paths",
  "stage": "graph_query",
  "error_type": "invalid_entity_reference",
  "retryable": false,
  "recoverable_by_model": true,
  "message_for_model": "The entity name is ambiguous. Choose one candidate entity_id or ask the user to clarify.",
  "message_for_user": "图谱中找到了多个可能实体，需要确认具体对象。",
  "next_action": "repair_tool_args_or_ask_user"
}
```

## 安全拦截

```json
{
  "ok": false,
  "tool": "write_file",
  "stage": "safety_validation",
  "error_type": "approval_required",
  "retryable": false,
  "recoverable_by_model": false,
  "message_for_model": "This action requires user approval.",
  "message_for_user": "写入文件需要用户确认。",
  "next_action": "request_approval",
  "risk_level": "high"
}
```

## 副作用成功

```json
{
  "ok": true,
  "tool": "write_file",
  "side_effect": true,
  "operation_id": "op_001",
  "idempotency_key": "write_file_doc_001",
  "reversible": true,
  "rollback_action": "restore_file_backup",
  "rollback_token": "backup_001",
  "data": {
    "path": "design.md"
  }
}
```

## Connector

Connector 是 Runtime 和外部服务之间的适配层。

典型 Connector：

```text
LLMConnector
EmbeddingConnector
MilvusConnector
Neo4jConnector
MCPConnector
HTTPAPIConnector
FileSystemConnector
MinIOConnector
```

Connector 负责：

- 连接管理。
- timeout。
- retry。
- 错误分类。
- 熔断。
- fallback。
- 脱敏日志。
- 返回 ToolResult。

## 错误分类

| 类型 | 含义 | 处理 |
| --- | --- | --- |
| transient_error | 临时错误 | 自动重试 |
| permanent_error | 永久错误 | 停止或提示用户 |
| model_repairable_error | 模型参数、查询可修复 | 返回 ToolMessage 修复 |
| user_action_required | 缺少信息、权限、配置 | 询问用户 |
| safety_error | 安全边界违规 | 拒绝或审批 |
| empty_result | 正常执行但无结果 | ok=true |
| unknown_outcome | 不确定副作用是否生效 | 先查状态，不能盲目重试 |
| rate_limit | Provider 或外部服务限流 | 指数退避，受 retry_budget 限制 |
| provider_5xx | 模型供应商 5xx | 自动重试，连续失败进入熔断 |
| timeout | 请求超时 | 自动重试，受总延迟预算限制 |
| connection_lost | 网络或流式连接中断 | 可从 checkpoint 恢复 |
| stream_ended_before_terminal | 模型流未返回终止事件就结束 | 标记中断后恢复或重新生成 |
| context_overflow | 上下文超过模型窗口 | Hermes 压缩后最多重试一次 |
| auth_failed | API key、token、权限错误 | 不重试，提示配置错误 |
| model_not_found | 模型名不存在或 provider 不支持 | 不重试 |
| billing_required | 账户未开通或余额不足 | 不重试 |
| quota_exceeded | 配额耗尽 | 不自动重试，可切 fallback |
| unsupported_feature | 模型不支持工具、视觉、JSON mode 等能力 | 裁剪能力或换模型 |
| skill_dependency_missing | Skill 脚本依赖缺失 | 不在运行时安装，提示配置或禁用该 entrypoint |
| skill_write_scope_violation | Skill 脚本尝试写出声明范围 | 安全拒绝，不重试 |
| skill_script_timeout | Skill 脚本执行超时 | kill 沙盒进程树，必要时当前 run 临时禁用 entrypoint |
| skill_script_failed | Skill 脚本异常退出 | 返回摘要，模型可换方案或询问用户 |
| skill_script_output_too_large | Skill 脚本输出过大 | 截断模型可见内容，完整日志按上限落 MinIO |
| skill_sandbox_violation | Skill 脚本违反沙盒策略 | 安全拒绝并记录审计 |

## 重试规则

可重试：

```text
DNS 临时失败
connection reset
timeout
HTTP 408
HTTP 429
HTTP 500 / 502 / 503 / 504
provider_5xx
rate_limit
stream_ended_before_terminal
Neo4j TransientError
MCP 断线
流式输出中途断开
```

不可重试：

```text
HTTP 400
HTTP 401
HTTP 403
HTTP 404
API key 无效
模型名不存在
billing_required
quota_exceeded
content_filter
工具 schema 不匹配
管理员诊断 Cypher 语法错误
向量维度不匹配
collection 不存在
Skill 依赖缺失
Skill 写入范围违规
Skill 沙盒策略违规
```

特殊重试：

```text
context_overflow
  -> Hermes 压缩
  -> 最多重试 1 次模型调用
  -> 仍失败则返回用户可见错误
```

不算错误：

```text
向量检索无命中
图谱路径为空
MCP 工具正常返回空列表
```

## 三层重试

```text
第 1 层：SDK / Connector 重试
  处理网络抖动、429、短暂 5xx。

第 2 层：Tool Executor 重试
  处理单个工具执行失败。

第 3 层：LangGraph 工作流恢复
  多次失败后换工具、换服务、问用户。
```

必须有：

```text
retry_budget
max_external_calls
max_total_latency
max_total_cost
```

避免重试相乘。

## Circuit Breaker

状态：

```text
closed：正常调用
open：服务暂时不可用，直接 fallback 或 ask_user
half_open：过一段时间试探性调用一次
```

适用：

- LLM 供应商持续 5xx。
- MCP server 持续断线。
- Neo4j 持续失败。
- Milvus 持续超时。

## Fallback

Fallback 必须预先设计。

示例：

```text
主 LLM 失败 -> 备用 LLM
Milvus 无结果 -> Neo4j 图谱扩展
Graph 查询失败 -> 文本 chunk 检索结果回答并说明限制
MCP 工具失败 -> 本地缓存或 ask_user
Embedding API 失败 -> 暂停索引构建
```

## Skill Script ToolResult

Skill Script 结果必须使用统一 ToolResult，不允许把原始 stdout、stderr、异常堆栈直接塞给模型。

只读脚本成功：

```json
{
  "ok": true,
  "tool": "skill_document_cleaner_normalize_input",
  "stage": "skill_script",
  "data": {
    "metadata": {
      "title": "文档标题"
    }
  },
  "warnings": [],
  "skill": {
    "skill_id": "document_cleaner",
    "skill_version": "0.1.0",
    "entrypoint": "normalize_input",
    "skill_run_id": "skillrun_001"
  },
  "artifacts": {
    "result_object_key": "workspaces/default/runs/run_001/skill_runs/skillrun_001/result.json",
    "stdout_object_key": "workspaces/default/runs/run_001/skill_runs/skillrun_001/stdout.txt",
    "stderr_object_key": "workspaces/default/runs/run_001/skill_runs/skillrun_001/stderr.txt"
  },
  "trace": {
    "trace_id": "trace_001",
    "tool_call_id": "call_001",
    "attempt": 1,
    "latency_ms": 1200
  }
}
```

写入型脚本生成 staged patch 后：

```json
{
  "ok": false,
  "tool": "skill_document_cleaner_generate_report_patch",
  "stage": "skill_script",
  "error_type": "approval_required",
  "retryable": false,
  "recoverable_by_model": false,
  "message_for_model": "A staged patch was generated and requires user approval before commit.",
  "message_for_user": "Skill 脚本生成了文件修改，需要确认后写入。",
  "next_action": "request_approval",
  "skill": {
    "skill_id": "document_cleaner",
    "skill_version": "0.1.0",
    "entrypoint": "generate_report_patch",
    "skill_run_id": "skillrun_002"
  },
  "diff_summary": {
    "files_changed": 1,
    "insertions": 80,
    "deletions": 10
  },
  "artifacts": {
    "diff_object_key": "workspaces/default/runs/run_001/skill_runs/skillrun_002/diff.patch",
    "operation_plan_object_key": "workspaces/default/runs/run_001/skill_runs/skillrun_002/operation_plan.json"
  }
}
```

写入范围违规：

```json
{
  "ok": false,
  "tool": "skill_document_cleaner_generate_report_patch",
  "stage": "safety_validation",
  "error_type": "skill_write_scope_violation",
  "retryable": false,
  "recoverable_by_model": true,
  "message_for_model": "The script attempted to write outside its declared file_write scope. Choose another tool or ask the user to enable a broader scope.",
  "message_for_user": "Skill 脚本尝试写入未授权路径，已被拦截。",
  "next_action": "repair_tool_args_or_ask_user"
}
```
