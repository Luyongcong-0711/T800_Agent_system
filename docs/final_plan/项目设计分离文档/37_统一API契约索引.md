# 统一 API 契约索引

状态：P0 当前实现接口总表  
更新时间：2026-05-31

## 定位

本文件汇总 P0 REST、SSE 和模型可见 Tool API。详细字段以各业务设计文件为准，本文件用于开发时快速查找接口边界。

## 通用规则

- 除 `/bootstrap` 外，workspace 级接口统一放在 `/workspaces/{workspace_id}` 下。
- 写操作必须支持 `idempotency_key`。
- 错误响应统一使用 `ErrorResponse`。
- 前端不直接访问 MinIO、Milvus、Neo4j、Redis。
- 所有响应不得返回明文密钥。

错误响应：

```json
{
  "ok": false,
  "error_type": "validation_failed",
  "message_for_user": "参数校验失败。",
  "retryable": false,
  "trace_id": "trace_001",
  "details": {}
}
```

## Bootstrap

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/bootstrap` | 返回默认用户、workspace、feature flags |

返回：

```json
{
  "user": {"user_id": "default_user", "role": "owner"},
  "workspace": {"workspace_id": "default", "workspace_role": "owner"},
  "feature_flags": {
    "login_enabled": false,
    "workspace_switch_enabled": false
  }
}
```

## Thread / Message / Run

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/workspaces/{workspace_id}/threads` | 列出历史会话 |
| POST | `/workspaces/{workspace_id}/threads` | 创建新对话窗口 |
| GET | `/workspaces/{workspace_id}/threads/{thread_id}` | 读取 thread manifest |
| PATCH | `/workspaces/{workspace_id}/threads/{thread_id}` | 重命名、归档、软删除 |
| GET | `/workspaces/{workspace_id}/threads/{thread_id}/messages` | 分页读取消息 |
| POST | `/workspaces/{workspace_id}/threads/{thread_id}/runs` | 创建一次 Agent Run |
| POST | `/workspaces/{workspace_id}/runs/{run_id}/cancel` | 取消 Run |
| POST | `/workspaces/{workspace_id}/runs/recover-stale` | 恢复无有效 owner lease 且超过 stale 阈值的 running Run |
| POST | `/workspaces/{workspace_id}/runs/{run_id}/approvals/{approval_id}/approve` | 批准 Run 内等待审批的 operation_plan |
| POST | `/workspaces/{workspace_id}/runs/{run_id}/approvals/{approval_id}/reject` | 拒绝 Run 内等待审批的 operation_plan |
| POST | `/workspaces/{workspace_id}/runs/{run_id}/operations/{operation_id}/rollback` | 回滚已执行的 staged_patch operation |
| GET | `/workspaces/{workspace_id}/runs/{run_id}` | 读取 Run 状态 |
| GET | `/workspaces/{workspace_id}/runs/{run_id}/events` | REST 历史补偿 |
| GET | `/workspaces/{workspace_id}/runs/{run_id}/events/stream` | Run SSE |

创建 Run 请求：

```json
{
  "idempotency_key": "idem_001",
  "user_message": "帮我总结这个文档",
  "stream": true,
  "attachments": []
}
```

Run 状态：

```text
created
running
waiting_approval
completed
failed
cancelled
```

当 ToolResult 返回 `error_type=approval_required` 时，Runtime 必须暂停 LangGraph 继续执行，Run manifest 写入 `status=waiting_approval`，`leaf_state.requires_approval=true`，并追加 `run_waiting_approval` 事件。`waiting_approval` 不会被后台 worker 自动二次执行，但用户仍可以取消。

P0 Approve / Reject 语义：

- approve：先把对应 `operation_plan.json` 标记为 `approved_pending_execution` 并写入 `approval_approved` 事件，然后按审批类型进入受控执行路径；`tool_invocation` 会执行已批准 Tool 并继续 LangGraph，`skill_script_staged_patch` 会执行已批准脚本、提交受控 workspace 文件、写入真实 diff/result/backup/rollback metadata，再继续 Run。
- reject：把对应 `operation_plan.json` 标记为 `rejected`，写入 `approval_rejected`，并把当前 Run 结束为 `cancelled`，`leaf_state.model_error=approval_rejected`。
- 两个接口都必须通过 `run_id + approval_id` 定位 MinIO 中的 operation plan；同一个 decision 重复提交必须幂等返回。

## Job

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/workspaces/{workspace_id}/jobs` | 创建后台 Job |
| GET | `/workspaces/{workspace_id}/jobs` | 分页查询 jobs_index |
| GET | `/workspaces/{workspace_id}/jobs/{job_id}` | 读取 Job 详情 |
| POST | `/workspaces/{workspace_id}/jobs/{job_id}/cancel` | 取消 Job |
| POST | `/workspaces/{workspace_id}/jobs/{job_id}/retry` | 创建重试 Job |
| GET | `/workspaces/{workspace_id}/jobs/{job_id}/events` | Job REST 历史补偿 |
| GET | `/workspaces/{workspace_id}/jobs/{job_id}/events/stream` | Job SSE |
| GET | `/workspaces/{workspace_id}/jobs/worker/status` | 读取本地 Job worker 状态 |
| POST | `/workspaces/{workspace_id}/jobs/worker/start` | 启动本地 Job worker daemon |
| POST | `/workspaces/{workspace_id}/jobs/worker/stop` | 停止本地 Job worker daemon |
| POST | `/workspaces/{workspace_id}/jobs/process-next` | 手动处理下一个 queued Job |
| POST | `/workspaces/{workspace_id}/jobs/recover-stale` | 恢复 stale running/recovering Job |
| POST | `/workspaces/{workspace_id}/jobs/rebuild-index` | 从 Job manifest 重建 jobs_index |

创建 Job 请求：

```json
{
  "idempotency_key": "idem_job_001",
  "job_type": "document_ingestion_job",
  "target_scope": {
    "knowledge_base_id": "kb_001",
    "doc_id": "doc_001",
    "doc_version_id": "docv_001"
  },
  "input": {}
}
```

`graph_build_job` P0 请求示例：

```json
{
  "idempotency_key": "graph_doc_001_docv_001",
  "job_type": "graph_build_job",
  "target_scope": {
    "knowledge_base_id": "kb_001",
    "doc_id": "doc_001",
    "doc_version_id": "docv_001"
  },
  "input": {
    "model_config_id": "graphrag_llm",
    "graph_schema_version": 1
  }
}
```

`graph_build_job` 成功 artifact 必须包含：

```json
{
  "artifact_type": "graph_build_result",
  "artifacts": {
    "entities": "workspaces/.../graph/entities.json",
    "mentions": "workspaces/.../graph/mentions.json",
    "relation_facts": "workspaces/.../graph/relation_facts.json",
    "evidence": "workspaces/.../graph/evidence.json",
    "decisions": "workspaces/.../graph/decisions.json"
  },
  "counts": {
    "entities": 12,
    "mentions": 30,
    "relation_facts": 8,
    "evidence": 8,
    "decisions": 3
  },
  "extraction": {
    "source": "graphrag_llm",
    "fallback_used": false,
    "warnings": []
  }
}
```

## Model Config

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/workspaces/{workspace_id}/model-configs` | 读取模型配置 |
| GET | `/workspaces/{workspace_id}/model-configs/{config_id}` | 读取单个模型配置 |
| PUT | `/workspaces/{workspace_id}/model-configs/{config_id}` | 保存模型配置 |
| POST | `/workspaces/{workspace_id}/model-configs/{config_id}/test` | 测试模型调用；可携带当前表单 config override，不持久化 |

P0 config id：

```text
main_chat
graphrag_llm
embedding
rerank
compression
fallback
```

`ModelConfigResponse.source` 必须返回 `stored` 或 `default_env`。`stored` 表示 workspace 已保存配置；`default_env` 表示来自环境默认值，还没有写入 workspace。测试接口如果收到 `config` override，必须测试 override；如果未收到 override，则测试后端当前有效公开配置，不能把 fake smoke fallback 暴露给前端响应。

## Database Config

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/workspaces/{workspace_id}/database/config` | 读取脱敏数据库配置 |
| PUT | `/workspaces/{workspace_id}/database/config` | 保存数据库配置 |
| GET | `/workspaces/{workspace_id}/database/health` | 读取最近健康状态 |
| POST | `/workspaces/{workspace_id}/database/health/check` | 快速连接测试 |

P0 targets：

```text
minio
milvus
neo4j
redis
```

## Secret Store

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/workspaces/{workspace_id}/secrets` | 列出脱敏 Secret |
| POST | `/workspaces/{workspace_id}/secrets` | 创建 Secret |
| GET | `/workspaces/{workspace_id}/secrets/{secret_id}/references` | 查看引用 |
| POST | `/workspaces/{workspace_id}/secrets/{secret_id}/disable` | 禁用 Secret |

创建 Secret 请求：

```json
{
  "type": "model_api_key",
  "display_name": "main model key",
  "plaintext": "sk-...",
  "scope": "workspace"
}
```

## Knowledge Base / Document

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/workspaces/{workspace_id}/knowledge-bases` | 知识库列表 |
| POST | `/workspaces/{workspace_id}/knowledge-bases` | 创建知识库 |
| GET | `/workspaces/{workspace_id}/knowledge-bases/{kb_id}` | 读取知识库 manifest |
| GET | `/workspaces/{workspace_id}/knowledge-bases/{kb_id}/active-embedding` | 读取 active embedding |
| POST | `/workspaces/{workspace_id}/knowledge-bases/{kb_id}/documents` | 上传文档并创建入库 Job；支持 JSON 和 multipart file |
| POST | `/workspaces/{workspace_id}/documents/upload` | 兼容上传入口；默认写入 `kb_default` |
| GET | `/workspaces/{workspace_id}/knowledge-bases/{kb_id}/documents` | 指定知识库文档列表 |
| GET | `/workspaces/{workspace_id}/knowledge-bases/{kb_id}/documents/{doc_id}` | 指定知识库文档详情 |
| GET | `/workspaces/{workspace_id}/knowledge-bases/{kb_id}/documents/{doc_id}/chunks` | 指定文档 chunk 列表 |
| POST | `/workspaces/{workspace_id}/knowledge-bases/{kb_id}/embedding/reindex` | 更换 embedding 版本并创建重建 Job |
| GET | `/workspaces/{workspace_id}/chunks/{chunk_id}` | 回源 chunk 正文 |

## Graph

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/workspaces/{workspace_id}/graph/schema` | 图谱 schema 摘要 |
| POST | `/workspaces/{workspace_id}/graph/search` | GraphRAG 自然语言查询，返回 text evidence 和 graph evidence |
| POST | `/workspaces/{workspace_id}/graph/entities/search` | 搜索实体 |
| POST | `/workspaces/{workspace_id}/graph/entities/{entity_id}/expand` | 扩展实体 |
| POST | `/workspaces/{workspace_id}/graph/relationships/find` | 两实体直接关系 |
| POST | `/workspaces/{workspace_id}/graph/paths/find` | 两实体多跳路径 |
| POST | `/workspaces/{workspace_id}/graph/evidence` | 回源证据；支持 `include_chunk_text` 和 `max_chars_per_chunk` |

## MCP

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/workspaces/{workspace_id}/mcp/servers` | MCP server 列表 |
| GET | `/workspaces/{workspace_id}/mcp/servers/{server_name}` | server 详情 |
| PUT | `/workspaces/{workspace_id}/mcp/servers/{server_name}` | 保存 server transport/config；保存后旧 snapshot 标记 stale |
| GET | `/workspaces/{workspace_id}/mcp/servers/{server_name}/tools` | tool 列表 |
| GET | `/workspaces/{workspace_id}/mcp/servers/{server_name}/health` | 读取 server 健康状态和 reconnect 建议；`live_probe=true` 时真实拉取 capabilities |
| POST | `/workspaces/{workspace_id}/mcp/tools/policy` | 启用/禁用单 tool |
| POST | `/workspaces/{workspace_id}/mcp/servers/{server_name}/refresh` | 创建 capability refresh Job |
| POST | `/workspaces/{workspace_id}/mcp/servers/{server_name}/reconnect` | 创建 reconnect/capability refresh Job |
| GET | `/workspaces/{workspace_id}/tools/inventory` | 模型可见 Tool inventory |

## Skill

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/workspaces/{workspace_id}/skills` | Skill 列表 |
| GET | `/workspaces/{workspace_id}/skills/{skill_id}` | Skill 详情 |
| GET | `/workspaces/{workspace_id}/skills/{skill_id}/versions/{version}` | 指定版本详情 |
| POST | `/workspaces/{workspace_id}/skill-proposals` | 生成 Skill proposal |
| GET | `/workspaces/{workspace_id}/skill-proposals/{proposal_id}` | 创建提案详情 |
| POST | `/workspaces/{workspace_id}/skills/{skill_id}/disable` | 禁用 Skill |
| POST | `/workspaces/{workspace_id}/skills/{skill_id}/validate` | 校验脚本 Skill 的 checksum、runtime、AST policy，通过后启用 |
| POST | `/workspaces/{workspace_id}/skills/{skill_id}/activate` | 激活 Skill 到当前 running Chat run |

Skill 创建主要由 Tool 触发：`skill_propose` 和 `skill_create_from_proposal`。

Skill activation 必须绑定真实存在的 `thread_id` 和 `run_id`：thread/run 必须属于同一个 workspace，`run.thread_id` 必须等于请求中的 `thread_id`，run 状态必须是 `running`。没有当前 running Chat run 时，前端必须禁用 Activate。

脚本 Skill 创建后默认 `status=disabled`、`validation_status=pending_validation`。前端或后端管理流程必须先调用 validate：

```json
{
  "version": "0.1.0"
}
```

成功后返回 `SkillDetailResponse`，其中 `status=enabled`、`validation_status=validated`、entrypoint 带 `script_checksum` 和 `sandbox_profile`；失败时返回统一 `ErrorResponse`，并保持 Skill `disabled/failed`。

## Memory

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/workspaces/{workspace_id}/memories` | 记忆列表 |
| GET | `/workspaces/{workspace_id}/memories/{memory_id}` | 记忆详情 |
| PATCH | `/workspaces/{workspace_id}/memories/{memory_id}` | 修正、启用、禁用 |
| DELETE | `/workspaces/{workspace_id}/memories/{memory_id}` | 删除或禁用 |
| GET | `/workspaces/{workspace_id}/memory-snapshots/{snapshot_id}` | 查看 memory snapshot |

## Audit / Logs

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/workspaces/{workspace_id}/logs/system/summary` | 系统摘要日志 |
| GET | `/workspaces/{workspace_id}/logs/system/full` | 系统完整 JSONL 日志查询 |
| GET | `/workspaces/{workspace_id}/logs/system/errors` | 系统错误日志查询 |
| GET | `/workspaces/{workspace_id}/logs/components/{component}` | 组件日志查询 |
| GET | `/workspaces/{workspace_id}/logs/tail` | 最新系统日志 |
| GET | `/workspaces/{workspace_id}/logs/artifacts` | 安全读取 diagnostic bundle / log archive 工件 |
| POST | `/workspaces/{workspace_id}/logs/diagnostic-bundles` | 创建诊断包 Job |
| POST | `/workspaces/{workspace_id}/logs/archive-jobs` | 创建日志归档 Job |

## P0 Readiness / Acceptance

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 进程级健康检查 |
| GET | `/workspaces/{workspace_id}/readiness` | P0 readiness 聚合检查 |
| GET | `/workspaces/{workspace_id}/readiness/p0` | P0 readiness 兼容路径 |

Readiness 会读取 `logs/p0_acceptance_report.json`，并把 `final_handoff` 映射为 `external.final_handoff` required blocker；同时把 `runtime.model_config.main_chat_smoke`、`runtime.model_config.graphrag_llm_smoke`、`runtime.model_config.embedding_smoke`、`runtime.docker_compose_ps`、`runtime.database_live_health`、`runtime.job_worker_status`、`runtime.mcp_live_smoke`、`runtime.frontend_route_smoke`、`runtime.frontend_browser_smoke`、`runtime.p0_readiness_after_report` 映射为逐项 required acceptance blockers。缺失、失败或 skipped 都会让 P0 readiness 进入 blocked/fail。`runtime.frontend_route_smoke` 是前端 HTTP route smoke；`runtime.frontend_browser_smoke` 会尝试使用本机 Microsoft Edge、Chrome 或 Chromium 进行 headless browser DOM 渲染检查。Readiness 兼容旧报告中的 `runtime.browser_e2e_smoke`，但新报告必须使用 `runtime.frontend_route_smoke` 和 `runtime.frontend_browser_smoke`。

Readiness 的 runtime 分类必须包含 `conversation.stale_runs`。该检查会扫描 Run manifest，统计 running / non-terminal / stale running Run；如果 running Run 没有有效 owner lease 且超过 stale 阈值，会返回 `blocked`，`details.stale_run_ids` 给出最多 20 个待恢复 Run，并要求调用 `POST /workspaces/{workspace_id}/runs/recover-stale` 或前端 Chat recovery action。

## 模型可见 Tool API

| Tool | 用途 |
| --- | --- |
| `rag_search` | Milvus + MinIO 文本证据检索 |
| `document_chunk_get` | 回源读取 chunk |
| `graphrag_search` | RAG + Neo4j 图谱联合查询 |
| `graph_expand_entity` | 扩展实体关系 |
| `graph_find_relationship` | 查询两实体直接关系 |
| `graph_find_paths` | 查询两实体多跳路径 |
| `graph_get_evidence` | 回源图谱证据 |
| `database_health_check` | 读取脱敏数据库健康状态 |
| `database_health_diagnose` | 解释数据库失败 |
| `memory_search` | 搜索长期记忆摘要 |
| `memory_get` | 读取长期记忆详情 |
| `memory_upsert` | 写长期记忆 |
| `memory_delete` | 禁用或删除长期记忆 |
| `skill_search` | 搜索 Skill |
| `skill_view` | 查看 Skill 摘要 |
| `skill_activate` | 激活 Skill |
| `skill_entrypoint_call` | 调用当前 run 已激活的 Skill entrypoint dispatcher |
| `skill_propose` | 生成 Skill 创建提案 |
| `skill_create_from_proposal` | 用户批准后创建 Skill |
| `call_subagent_{agent_type}` | 调用业务 SubAgent |

## SSE 事件格式

```text
id: evt_000001
event: run_event
data: {"event_id":"evt_000001","type":"assistant_delta","payload":{}}
```

规则：

- `id` 必须等于 `event_id`。
- 客户端可传 `after_event_id` query，也可传 `Last-Event-ID` header。
- 服务端断线补偿先查 `event_index.json`，再读取 `events/part-*.jsonl`。

## P0 Acceptance Report v2

`scripts/p0_acceptance.py` writes `logs/p0_acceptance_report.json` with `schema_version=2`.
The report records `required_final_handoff_flags`, `provided_flags`, `final_handoff.required_flags`,
`final_handoff.missing_flags`, `final_handoff.required_check_ids`,
`final_handoff.missing_check_ids`, `final_handoff.non_passing_checks`, and
`final_handoff.non_passing_executed_checks`.

`final_handoff.ready=true` means:

1. every required final acceptance flag was provided;
2. every required final handoff check ID is present, including `code.backend_python_env`, `runtime.database_live_health`, `runtime.job_worker_status`, `runtime.frontend_browser_smoke`, and `runtime.p0_readiness_after_report`;
3. every executed check in the report passed.

The helper also supports `--list-final-checks`, which prints the required final flags and check IDs
without running tests or touching local services. Backend readiness treats reports older than
`schema_version=2` as blocked, so old quick-run reports cannot be mistaken for final handoff evidence.
