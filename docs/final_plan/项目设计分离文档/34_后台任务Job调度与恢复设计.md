# 后台任务 Job 调度与恢复设计

状态：P0 最终技术设计  
更新时间：2026-05-30

## 参考源码标注

本文件的 Job 事件流、恢复、分页和前端实时进度设计，参考以下源码后改造成 MinIO 权威状态方案：

| 本项目设计点 | 参考源码路径 | GitHub 地址 | 本项目落地 |
| --- | --- | --- | --- |
| JSONL append-only event storage | `.research_repos\pi\packages\agent\src\harness\session\jsonl-storage.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/agent/src/harness/session/jsonl-storage.ts` | `jobs/{job_id}/events/part-*.jsonl` 分段事件对象 |
| JSONL repo / replay / leaf | `.research_repos\pi\packages\agent\src\harness\session\jsonl-repo.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/agent/src/harness/session/jsonl-repo.ts` | `event_index.json`、`leaf_state.json`、事件重放 |
| OpenHands event service | `.research_repos\openhands\openhands\app_server\event\event_service.py` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/openhands/app_server/event/event_service.py` | REST 历史事件分页、count/search 形态 |
| OpenHands event router | `.research_repos\openhands\openhands\app_server\event\event_router.py` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/openhands/app_server/event/event_router.py` | `GET /jobs/{job_id}/events` 与 SSE 断线补偿 |
| CrewAI event bus / replay | `.research_repos\crewai\lib\crewai\src\crewai\events\event_bus.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/events/event_bus.py` | 内部 JobEventBus handler 必须幂等，replay 不重复副作用 |
| CrewAI event record | `.research_repos\crewai\lib\crewai\src\crewai\state\event_record.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/state/event_record.py` | job event 保留 parent / trigger / completed_by 字段 |
| OpenClaw session lock / metrics | `.research_repos\openclaw\src\agents\subagent-spawn.ts` | `https://github.com/openclaw/openclaw/blob/f2ba23424eacc7f567018e5d8862bdeddfa9c740/src/agents/subagent-spawn.ts` | 后台任务 owner、timeout、metrics、隔离执行边界 |

## 定位

Job 是系统后台工作单，不是 Agent Run，也不是 Redis 队列。

```text
Run = 某个 thread 中一次用户消息触发的 Agent 执行。
Job = 后台长耗时任务，例如文档入库、embedding 重建、图谱构建、日志归档、诊断包生成、MCP capability refresh。
```

P0 已确认：

```text
Run 和 Job 分开。
不用 Redis 队列。
Job 权威状态放 MinIO。
Job 进度用 SSE，不用 WebSocket。
Job 状态机包含 unknown_outcome / recovering。
前端增加任务中心 / Jobs 页面。
同文档或同知识库关键任务互斥，不同文档可以并发。
```

## Run 与 Job 边界

| 维度 | Run | Job |
| --- | --- | --- |
| 触发来源 | 用户在 thread 中提交消息 | 用户操作、系统定时、Run 内部请求、管理员维护 |
| 主要目标 | 生成一次 assistant 回答 | 完成一个后台工作单 |
| 前端位置 | 主对话页、日志审计页 | 任务中心 / Jobs 页面，也可在相关文档或配置页显示摘要 |
| 事件路径 | `workspaces/{workspace_id}/runs/{run_id}` | `workspaces/{workspace_id}/jobs/{job_id}` 或 `system/jobs/{job_id}` |
| 权威状态 | run manifest / events / leaf_state | job manifest / events / leaf_state |
| 是否进入对话历史 | 是，最终 assistant message 进入 thread messages | 否，只有摘要可作为系统提示或卡片显示 |
| 是否可关联 | 可通过 `related_job_ids` 关联 Job | 可通过 `related_run_id` / `related_thread_id` 关联 Run |
| 是否由模型直接控制 | 主 Agent 通过 LangGraph 控制 | 普通模型不直接写 Job 状态，只能通过受控 Tool 查询或触发允许的 Job |

常见关系：

```text
用户在主对话页说“把这批文件入库”
  -> Run 创建
  -> Agent 调用受控 tool: create_document_ingestion_job
  -> Job 创建并后台执行
  -> Run 可以回复“已创建入库任务 job_xxx，可在任务中心查看”
  -> Job 后续进度不塞进当前 thread 的完整消息流
```

## P0 Job 类型

| job_type | P0 | target_scope | 说明 |
| --- | --- | --- | --- |
| `document_ingestion_job` | 是 | `document_version` | 单文档解析、切块、embedding、图谱入库、索引更新 |
| `embedding_reindex_job` | 是 | `knowledge_base` | 更换 embedding 模型后新建 collection 并重新入库 |
| `graph_build_job` | 是 | `document_version` | 对单文档或一批文档构建图谱 |
| `graph_rebuild_job` | 是 | `knowledge_base` | 知识库级图谱重建 |
| `index_rebuild_job` | 是 | `workspace` / `system` | 重建 `jobs_index.json`、`runs_index.json`、`documents_index.json` 等轻量索引 |
| `log_shipper_job` | 是 | `system` | 本地热日志异步归档到 MinIO |
| `diagnostic_bundle_job` | 是 | `workspace` / `system` | 生成脱敏诊断包 |
| `mcp_capability_refresh_job` | 是 | `mcp_server` | 重新 initialize / list tools/resources/prompts，更新 capability snapshot |
| `database_health_check_job` | 是 | `workspace` | 周期性连接健康检查；按钮即时测试仍可走同步 REST |
| `cleanup_retention_job` | P1 | `workspace` / `system` | 日志、临时产物、诊断包保留期清理 |

## MinIO Object Key

Workspace 级 Job：

```text
workspaces/{workspace_id}/indexes/jobs_index.json
workspaces/{workspace_id}/jobs/{job_id}/manifest.json
workspaces/{workspace_id}/jobs/{job_id}/events/part-000001.jsonl
workspaces/{workspace_id}/jobs/{job_id}/event_index.json
workspaces/{workspace_id}/jobs/{job_id}/leaf_state.json
workspaces/{workspace_id}/jobs/{job_id}/artifacts/{artifact_id}
workspaces/{workspace_id}/jobs/{job_id}/errors.jsonl
```

System 级 Job：

```text
system/indexes/jobs_index.json
system/jobs/{job_id}/manifest.json
system/jobs/{job_id}/events/part-000001.jsonl
system/jobs/{job_id}/event_index.json
system/jobs/{job_id}/leaf_state.json
system/jobs/{job_id}/artifacts/{artifact_id}
system/jobs/{job_id}/errors.jsonl
```

规则：

- workspace 级任务必须带 `workspace_id`。
- system 级任务不属于某个 workspace，但如果由某个用户在某个 workspace 页面触发，仍记录 `created_by` 和 `source_workspace_id`。
- `manifest.json` 是 Job 权威状态。
- `events/part-*.jsonl` 是 Job 事件权威日志。
- `event_index.json` 用于分页、SSE 断线补偿和重放。
- `leaf_state.json` 是 Job 当前可恢复快照。
- `jobs_index.json` 是前端列表和调度扫描入口，可以从 manifest 与 event 重建。

## Job 状态机

状态枚举：

```text
created
queued
running
waiting_retry
succeeded
failed
cancelled
unknown_outcome
recovering
```

状态流转：

```text
created -> queued -> running -> succeeded
created -> queued -> running -> failed
created -> queued -> running -> cancelled

running -> waiting_retry -> queued
running -> unknown_outcome -> recovering -> running
running -> unknown_outcome -> recovering -> succeeded
running -> unknown_outcome -> recovering -> failed

queued -> cancelled
waiting_retry -> cancelled
recovering -> cancelled
```

终态：

```text
succeeded
failed
cancelled
```

`unknown_outcome` 表示 Worker 不确定副作用是否已经完成，例如 Milvus 批量写入超时、Neo4j 事务提交后连接断开、MinIO object 已写但 manifest 更新失败。进入该状态后不能盲目重复执行，必须先执行恢复探测。

`recovering` 表示 Scheduler / Worker 正在重放事件、检查外部状态、重建 leaf_state 或接管过期 owner。

## manifest.json

示例：

```json
{
  "schema_version": 1,
  "job_id": "job_01JABC",
  "job_type": "document_ingestion_job",
  "workspace_id": "default",
  "created_by": "default_user",
  "role": "owner",
  "status": "running",
  "priority": "normal",
  "target_scope": {
    "scope_type": "document_version",
    "knowledge_base_id": "kb_default",
    "doc_id": "doc_001",
    "doc_version_id": "docv_001"
  },
  "idempotency_key": "sha256:workspace-default:doc-doc_001:docv-docv_001:pipeline-v1",
  "operation_id": "op_job_01JABC",
  "related_run_id": "run_001",
  "related_thread_id": "thread_001",
  "trace_id": "trace_001",
  "owner": {
    "runtime_instance_id": "rt_001",
    "fencing_token": "fence_01JABC_0003",
    "acquired_at": "2026-05-30T12:00:00+08:00",
    "expires_at": "2026-05-30T12:05:00+08:00"
  },
  "stages": [
    {"name": "parse_document", "status": "succeeded", "progress": 1.0},
    {"name": "chunk_document", "status": "succeeded", "progress": 1.0},
    {"name": "embedding_insert", "status": "running", "progress": 0.42},
    {"name": "graph_extract", "status": "pending", "progress": 0.0},
    {"name": "graph_write", "status": "pending", "progress": 0.0},
    {"name": "finalize_indexes", "status": "pending", "progress": 0.0}
  ],
  "progress": {
    "current_stage": "embedding_insert",
    "percent": 46,
    "done_units": 420,
    "total_units": 1000,
    "message": "正在写入 Milvus 向量"
  },
  "retry_policy": {
    "max_attempts": 3,
    "attempt": 1,
    "backoff": "exponential_jitter",
    "next_retry_at": null
  },
  "object_keys": {
    "manifest": "workspaces/default/jobs/job_01JABC/manifest.json",
    "events_prefix": "workspaces/default/jobs/job_01JABC/events/",
    "event_index": "workspaces/default/jobs/job_01JABC/event_index.json",
    "leaf_state": "workspaces/default/jobs/job_01JABC/leaf_state.json",
    "errors": "workspaces/default/jobs/job_01JABC/errors.jsonl"
  },
  "revision": 7,
  "created_at": "2026-05-30T11:50:00+08:00",
  "updated_at": "2026-05-30T12:01:00+08:00",
  "started_at": "2026-05-30T11:51:00+08:00",
  "finished_at": null
}
```

字段要求：

- `idempotency_key` 必须稳定，防止重复创建同一任务。
- `target_scope` 必须足够具体，用于并发互斥。
- `owner.fencing_token` 是写入令牌，Worker 每次写 manifest / event / leaf_state 前都要校验。
- `revision` 每次更新递增，写入时使用 etag 或 revision 乐观并发控制。
- `related_run_id` 可为空；后台定时任务通常没有 related run。

## Job Event JSONL

事件 ID：

```text
job event_id = evt_{job_id}_{event_seq_12位补零}
```

典型事件类型：

```text
job_created
job_queued
job_started
job_stage_started
job_stage_progress
job_stage_completed
job_waiting_retry
job_recovering
job_unknown_outcome
job_succeeded
job_failed
job_cancelled
job_artifact_created
job_owner_acquired
job_owner_released
```

事件示例：

```json
{"schema_version":1,"event_seq":1,"event_id":"evt_job_01JABC_000000000001","type":"job_created","job_id":"job_01JABC","workspace_id":"default","created_at":"2026-05-30T11:50:00+08:00","trace_id":"trace_001"}
{"schema_version":1,"event_seq":2,"event_id":"evt_job_01JABC_000000000002","type":"job_started","job_id":"job_01JABC","stage":"parse_document","created_at":"2026-05-30T11:51:00+08:00","trace_id":"trace_001"}
{"schema_version":1,"event_seq":3,"event_id":"evt_job_01JABC_000000000003","type":"job_stage_progress","job_id":"job_01JABC","stage":"embedding_insert","done_units":420,"total_units":1000,"percent":46,"message":"正在写入 Milvus 向量","created_at":"2026-05-30T12:01:00+08:00","trace_id":"trace_001"}
```

事件写入顺序：

```text
1. Worker 校验 owner.fencing_token。
2. 生成 event_seq / event_id。
3. 写入当前 events/part-*.jsonl 分段。
4. 更新 event_index.json。
5. 更新 leaf_state.json。
6. 更新 manifest.json。
7. 更新 jobs_index.json 摘要。
8. 通过 SSE 推送。
```

如果第 7 步失败，不回滚前面的权威事件；`index_rebuild_job` 后续重建 `jobs_index.json`。

## leaf_state.json

示例：

```json
{
  "schema_version": 1,
  "job_id": "job_01JABC",
  "workspace_id": "default",
  "status": "running",
  "current_stage": "embedding_insert",
  "last_event_seq": 36,
  "last_event_id": "evt_job_01JABC_000000000036",
  "progress": {
    "percent": 46,
    "done_units": 420,
    "total_units": 1000,
    "message": "正在写入 Milvus 向量"
  },
  "stage_state": {
    "embedding_insert": {
      "last_chunk_id": "chk_0420",
      "batch_no": 14,
      "committed_batches": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13],
      "pending_batch_operation_id": "op_job_01JABC_embedding_batch_0014"
    }
  },
  "unknown_outcome": null,
  "artifacts": [],
  "updated_at": "2026-05-30T12:01:00+08:00"
}
```

`leaf_state.json` 用于：

- 前端刷新后恢复 Job 当前状态。
- SSE 断线后先读快照，再用 `after_event_id` 补事件。
- Worker 崩溃后从最近 stage_state 继续。
- `unknown_outcome` 时保存待探测对象和 operation_id。

## event_index.json

示例：

```json
{
  "schema_version": 1,
  "job_id": "job_01JABC",
  "workspace_id": "default",
  "last_event_seq": 36,
  "last_event_id": "evt_job_01JABC_000000000036",
  "segments": [
    {
      "object_key": "workspaces/default/jobs/job_01JABC/events/part-000001.jsonl",
      "from_event_seq": 1,
      "to_event_seq": 36,
      "event_count": 36,
      "sealed": false,
      "sha256": "sha256..."
    }
  ],
  "revision": 4,
  "updated_at": "2026-05-30T12:01:00+08:00"
}
```

P0 分段规则：

- 单个 active part 默认小于 2 MB。
- 达到阈值后封存为 sealed，不再修改。
- active part 可以在小尺寸内重写。
- `event_index.json` 缺失时，扫描 `events/part-*.jsonl` 重建。

## jobs_index.json

Workspace 级示例：

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "revision": 12,
  "updated_at": "2026-05-30T12:01:00+08:00",
  "jobs": [
    {
      "job_id": "job_01JABC",
      "job_type": "document_ingestion_job",
      "status": "running",
      "priority": "normal",
      "title": "入库 example.pdf",
      "target_scope": {
        "scope_type": "document_version",
        "knowledge_base_id": "kb_default",
        "doc_id": "doc_001",
        "doc_version_id": "docv_001"
      },
      "progress_percent": 46,
      "current_stage": "embedding_insert",
      "manifest_object_key": "workspaces/default/jobs/job_01JABC/manifest.json",
      "event_index_object_key": "workspaces/default/jobs/job_01JABC/event_index.json",
      "leaf_state_object_key": "workspaces/default/jobs/job_01JABC/leaf_state.json",
      "last_event_id": "evt_job_01JABC_000000000036",
      "created_at": "2026-05-30T11:50:00+08:00",
      "updated_at": "2026-05-30T12:01:00+08:00"
    }
  ]
}
```

索引规则：

- `jobs_index.json` 只保存列表摘要，不保存完整事件。
- 列表默认按 `updated_at desc` 排序。
- 可按 `status`、`job_type`、`target_scope`、`created_by`、`related_run_id` 过滤。
- 索引可由 `jobs/*/manifest.json` + `jobs/*/leaf_state.json` 重建。

## 幂等键

| job_type | idempotency_key |
| --- | --- |
| `document_ingestion_job` | `workspace_id + doc_id + doc_version_id + pipeline_version` |
| `embedding_reindex_job` | `workspace_id + knowledge_base_id + embedding_config_version` |
| `graph_build_job` | `workspace_id + knowledge_base_id + doc_id + doc_version_id + graph_schema_version` |
| `graph_rebuild_job` | `workspace_id + knowledge_base_id + graph_schema_version` |
| `index_rebuild_job` | `scope + index_name + rebuild_reason + date_bucket` |
| `log_shipper_job` | `runtime_instance_id + date + log_file + file_sha256` |
| `diagnostic_bundle_job` | 默认允许重复；如果用户传 `request_id`，用 `workspace_id + request_id` |
| `mcp_capability_refresh_job` | `workspace_id + server_name + config_version + refresh_reason` |
| `database_health_check_job` | `workspace_id + connector_type + config_version + minute_bucket` |

创建 Job 时：

```text
1. 根据请求计算 idempotency_key。
2. 查询 jobs_index.json 中是否已有非终态同 key job。
3. 如果存在，直接返回已有 job。
4. 如果不存在，创建新 job manifest。
5. 如果 jobs_index 更新冲突，重新读取并再次检查 idempotency_key。
```

## 并发策略

P0 互斥规则：

| 任务 | 互斥范围 |
| --- | --- |
| 同一 `doc_id + doc_version_id` 的 `document_ingestion_job` | 互斥 |
| 同一 `knowledge_base_id` 的 `embedding_reindex_job` | 互斥 |
| 同一 `knowledge_base_id` 的 `graph_rebuild_job` | 互斥 |
| 同一 `doc_id + doc_version_id` 的 `graph_build_job` | 互斥 |
| `index_rebuild_job` 与正在重建的同一 index | 互斥 |
| `mcp_capability_refresh_job` 同一 server | 互斥 |
| `log_shipper_job` 同一 runtime/date/file | 互斥 |
| `diagnostic_bundle_job` | 默认可并发，但每个 bundle_id 独立 |

允许并发：

- 不同文档的 `document_ingestion_job` 可以并发。
- 不同文档的 `graph_build_job` 可以并发。
- 不同 MCP server 的 capability refresh 可以并发。
- 查询类 Run 不被文档入库 Job 阻塞，但可能看到旧 active embedding 或 GraphRAG 降级提示。

并发不使用 Redis lock。P0 使用 MinIO owner + fencing：

```text
1. Worker 读取 manifest 和 revision/etag。
2. 如果 owner 为空或 expires_at 已过期，生成新的 fencing_token。
3. 使用 conditional put 或 revision compare 写回 owner。
4. 写回成功才算获得执行权。
5. 每次写事件、leaf_state、manifest 前校验 fencing_token。
6. Worker 定期续租 owner.expires_at。
7. 旧 Worker 发现 fencing_token 失效，必须停止写入。
```

## REST API

### 创建 Job

```http
POST /workspaces/{workspace_id}/jobs
```

请求：

```json
{
  "job_type": "document_ingestion_job",
  "priority": "normal",
  "target_scope": {
    "scope_type": "document_version",
    "knowledge_base_id": "kb_default",
    "doc_id": "doc_001",
    "doc_version_id": "docv_001"
  },
  "params": {
    "pipeline_version": "rag_ingestion_v1",
    "run_graph_build": true
  },
  "idempotency_key": null,
  "related_run_id": "run_001",
  "related_thread_id": "thread_001"
}
```

返回：

```json
{
  "job_id": "job_01JABC",
  "status": "queued",
  "deduped": false,
  "manifest_object_key": "workspaces/default/jobs/job_01JABC/manifest.json",
  "events_stream_url": "/workspaces/default/jobs/job_01JABC/events/stream"
}
```

### 查询 Job 列表

```http
GET /workspaces/{workspace_id}/jobs?status=running&job_type=document_ingestion_job&limit=50&cursor=...
```

返回 `jobs_index.json` 摘要分页。

### 查询 Job 快照

```http
GET /workspaces/{workspace_id}/jobs/{job_id}
```

返回：

```json
{
  "job": {
    "job_id": "job_01JABC",
    "job_type": "document_ingestion_job",
    "status": "running",
    "progress": {"percent": 46, "current_stage": "embedding_insert"}
  },
  "leaf_state": {
    "last_event_id": "evt_job_01JABC_000000000036",
    "current_stage": "embedding_insert"
  }
}
```

### 取消 Job

```http
POST /workspaces/{workspace_id}/jobs/{job_id}/cancel
```

规则：

- `created` / `queued` 可直接取消。
- `running` 写入 `cancel_requested=true`，Worker 到安全检查点后退出并写 `job_cancelled`。
- 正在执行不可中断外部写操作时，先进入 `unknown_outcome`，恢复探测后再决定 cancelled / failed / succeeded。

### 重试 Job

```http
POST /workspaces/{workspace_id}/jobs/{job_id}/retry
```

规则：

- 只允许 `failed` 或 `unknown_outcome` 经过诊断后的可重试任务。
- 重试不会复用旧 job_id；P0 创建新 Job，并在新 manifest 中记录 `retry_of_job_id`。
- 如果同一 idempotency_key 已有非终态 Job，返回已有 Job。

## SSE API

```http
GET /workspaces/{workspace_id}/jobs/{job_id}/events/stream
GET /workspaces/{workspace_id}/jobs/{job_id}/events?after_event_id=evt_job_...&limit=200
```

SSE wire 格式：

```text
id: evt_job_01JABC_000000000036
event: job_stage_progress
data: {"event_id":"evt_job_01JABC_000000000036","job_id":"job_01JABC","type":"job_stage_progress","stage":"embedding_insert","percent":46}
```

断线恢复：

```text
1. 前端记录每个 job_id 最后成功处理的 last_event_id。
2. 断线后重新连接，带 Last-Event-ID header 或 after_event_id query。
3. Server 读取 job event_index.json。
4. 从 events/part-*.jsonl 补发缺失事件。
5. 如果 job.status 是 running / waiting_retry / recovering，补发后继续挂实时流。
6. 如果 job.status 是 succeeded / failed / cancelled，补发后发送 stream_closed 并关闭。
```

前端同一个任务中心页面可以同时订阅多个 active Job，但 P0 建议：

- 当前打开的 Job 详情保持 SSE。
- 列表页对 active jobs 使用批量摘要轮询或少量 SSE。
- 同时 SSE 连接数限制为 5，超出后用 `GET /jobs` 摘要刷新。

## Scheduler 伪代码

```python
from dataclasses import dataclass
from datetime import datetime, timedelta

@dataclass
class JobCandidate:
    job_id: str
    workspace_id: str | None
    object_key: str
    priority: str
    updated_at: datetime

class JobScheduler:
    def tick(self) -> None:
        candidates = self.load_candidates()
        for candidate in sort_by_priority(candidates):
            manifest = self.job_store.get_manifest(candidate)

            if manifest.status not in {"queued", "waiting_retry", "recovering", "unknown_outcome"}:
                continue

            if self.conflict_detector.has_conflict(manifest):
                continue

            lease = self.job_store.try_acquire_owner(
                manifest=manifest,
                runtime_instance_id=self.runtime_instance_id,
                ttl=timedelta(minutes=5),
            )
            if not lease.acquired:
                continue

            self.worker_pool.submit(manifest.job_id)

    def load_candidates(self) -> list[JobCandidate]:
        workspace_jobs = self.job_store.scan_workspace_jobs_index()
        system_jobs = self.job_store.scan_system_jobs_index()
        return workspace_jobs + system_jobs
```

说明：

- Scheduler 可以多实例运行。
- 执行权靠 manifest owner/fencing，不靠 Redis。
- `conflict_detector` 读取 active jobs 的 `target_scope`，判断同文档/同知识库关键任务互斥。

## Worker Loop 伪代码

```python
class JobWorker:
    def run(self, job_id: str) -> None:
        manifest = self.job_store.get_manifest_by_id(job_id)
        self.job_store.assert_owner(manifest, self.runtime_instance_id)

        try:
            self.job_events.append(job_id, "job_started")
            self.job_store.update_status(job_id, "running")

            handler = self.handlers[manifest.job_type]
            handler.run(JobContext(
                job_id=job_id,
                manifest=manifest,
                events=self.job_events,
                store=self.job_store,
                heartbeat=self.heartbeat,
                cancellation=self.cancellation,
            ))

            self.job_events.append(job_id, "job_succeeded")
            self.job_store.mark_succeeded(job_id)

        except UnknownOutcomeError as exc:
            self.job_events.append(job_id, "job_unknown_outcome", error=redact(exc))
            self.job_store.mark_unknown_outcome(job_id, exc.probe_hint)

        except RetryableJobError as exc:
            self.job_events.append(job_id, "job_waiting_retry", error=redact(exc))
            self.job_store.schedule_retry(job_id, exc)

        except CancellationRequested:
            self.job_events.append(job_id, "job_cancelled")
            self.job_store.mark_cancelled(job_id)

        except Exception as exc:
            self.job_events.append(job_id, "job_failed", error=redact(exc))
            self.job_store.mark_failed(job_id, exc)

        finally:
            self.job_store.release_owner(job_id, self.runtime_instance_id)
```

## Recovery 伪代码

```python
class JobRecovery:
    def recover_job(self, job_id: str) -> None:
        manifest = self.job_store.get_manifest_by_id(job_id)

        lease = self.job_store.try_acquire_owner(
            manifest=manifest,
            runtime_instance_id=self.runtime_instance_id,
            ttl=timedelta(minutes=5),
            force_if_expired=True,
        )
        if not lease.acquired:
            return

        self.job_events.append(job_id, "job_recovering")
        self.job_store.update_status(job_id, "recovering")

        event_index = self.job_store.ensure_event_index(job_id)
        leaf_state = self.job_store.rebuild_leaf_state(job_id, event_index)

        if manifest.status == "unknown_outcome":
            outcome = self.probe_external_outcome(manifest, leaf_state)
            if outcome == "committed":
                self.job_events.append(job_id, "job_succeeded", recovered=True)
                self.job_store.mark_succeeded(job_id)
            elif outcome == "not_committed_retryable":
                self.job_store.requeue(job_id)
            else:
                self.job_events.append(job_id, "job_failed", recovered=True)
                self.job_store.mark_failed(job_id, outcome.error)
            return

        if manifest.status in {"running", "recovering"}:
            next_step = self.resume_plan(manifest, leaf_state)
            self.worker_pool.submit(job_id, resume_from=next_step)
```

外部结果探测：

| 阶段 | 探测方式 |
| --- | --- |
| MinIO object 写入 | 检查 object 是否存在、sha256、manifest 引用是否一致 |
| Milvus batch insert | 用 `operation_id` 或 `chunk_id` 查询是否已写入 active/new collection |
| Neo4j batch write | 用 `operation_id`、`doc_version_id`、`fact_id` 查询是否已提交 |
| active embedding 切换 | 检查 `active_embedding.json` 的 version、collection 和 revision |
| MCP capability refresh | 检查 capability snapshot hash 与 tools/resources/prompts 产物 |
| log shipper | 检查目标归档 object_key 和 sha256 |

## document_ingestion_job

阶段：

```text
validate_source
parse_document
extract_document_metadata
chunk_document
embedding_insert
graph_extract
graph_write
finalize_indexes
```

LangGraph 伪代码：

```python
from typing import TypedDict, Literal
from langgraph.graph import StateGraph, START, END

class IngestionJobState(TypedDict):
    job_id: str
    workspace_id: str
    knowledge_base_id: str
    doc_id: str
    doc_version_id: str
    current_stage: str
    parsed_object_key: str | None
    chunks_object_key: str | None
    chunk_ids: list[str]
    graph_artifacts: dict
    errors: list[dict]

def emit_stage(stage: str, status: str, state: IngestionJobState) -> None:
    job_events.append(
        state["job_id"],
        f"job_stage_{status}",
        stage=stage,
        workspace_id=state["workspace_id"],
    )

def parse_document_node(state: IngestionJobState) -> IngestionJobState:
    emit_stage("parse_document", "started", state)
    parsed = parser_router.parse_from_minio(state["workspace_id"], state["doc_id"])
    minio.put_json(parsed.object_key, parsed.to_dict())
    emit_stage("parse_document", "completed", state)
    return {**state, "parsed_object_key": parsed.object_key, "current_stage": "chunk_document"}

def chunk_document_node(state: IngestionJobState) -> IngestionJobState:
    emit_stage("chunk_document", "started", state)
    chunks = chunker.chunk_document(state["parsed_object_key"])
    minio.put_json(chunks.object_key, chunks.to_dict())
    emit_stage("chunk_document", "completed", state)
    return {**state, "chunks_object_key": chunks.object_key, "chunk_ids": chunks.chunk_ids}

def embedding_insert_node(state: IngestionJobState) -> IngestionJobState:
    active_embedding = kb_store.get_active_embedding(state["knowledge_base_id"])
    for batch in iter_chunk_batches(state["chunk_ids"]):
        cancellation.throw_if_requested(state["job_id"])
        operation_id = stable_operation_id(state["job_id"], "embedding", batch.no)
        vectors = embedding_client.embed_documents(batch.texts, model=active_embedding.model)
        milvus.upsert_chunks(
            collection=active_embedding.collection,
            vectors=vectors,
            metadata=batch.metadata,
            operation_id=operation_id,
        )
        job_events.progress(state["job_id"], "embedding_insert", batch.done, batch.total)
    return {**state, "current_stage": "graph_extract"}

def graph_extract_node(state: IngestionJobState) -> IngestionJobState:
    fragments = graph_extractor.extract_from_chunks(state["chunks_object_key"])
    minio.put_json(fragments.object_key, fragments.to_dict())
    return {**state, "graph_artifacts": {"fragments": fragments.object_key}}

def graph_write_node(state: IngestionJobState) -> IngestionJobState:
    graph_write_batch_internal({
        "caller_type": "ingestion_pipeline",
        "operation_id": stable_operation_id(state["job_id"], "graph_write", state["doc_version_id"]),
        "workspace_id": state["workspace_id"],
        "knowledge_base_id": state["knowledge_base_id"],
        "doc_id": state["doc_id"],
        "doc_version_id": state["doc_version_id"],
        "staging_object_key": state["graph_artifacts"]["fragments"],
    })
    return {**state, "current_stage": "finalize_indexes"}

def finalize_indexes_node(state: IngestionJobState) -> IngestionJobState:
    document_store.mark_ingested(state["workspace_id"], state["doc_id"], state["doc_version_id"])
    index_writer.update_documents_index(state["workspace_id"], state["doc_id"])
    return state

graph = StateGraph(IngestionJobState)
graph.add_node("parse_document", parse_document_node)
graph.add_node("chunk_document", chunk_document_node)
graph.add_node("embedding_insert", embedding_insert_node)
graph.add_node("graph_extract", graph_extract_node)
graph.add_node("graph_write", graph_write_node)
graph.add_node("finalize_indexes", finalize_indexes_node)
graph.add_edge(START, "parse_document")
graph.add_edge("parse_document", "chunk_document")
graph.add_edge("chunk_document", "embedding_insert")
graph.add_edge("embedding_insert", "graph_extract")
graph.add_edge("graph_extract", "graph_write")
graph.add_edge("graph_write", "finalize_indexes")
graph.add_edge("finalize_indexes", END)
ingestion_job_graph = graph.compile()
```

失败策略：

- 解析失败：manifest 标记 `parse_status=failed`，Job failed，可重试。
- chunk 失败：保留 parsed 产物，可从 `chunk_document` 重试。
- embedding 失败：保留 chunk JSON，可重试 batch。
- Neo4j 写入失败：基础 RAG 可用，GraphRAG 标记降级；Job 可进入 waiting_retry。
- 写入结果未知：进入 `unknown_outcome`，先查 Milvus / Neo4j / MinIO 状态。

## embedding_reindex_job

阶段：

```text
create_embedding_version
create_milvus_collection
reembed_documents
validate_collection
switch_active_embedding
mark_old_collection_readonly
invalidate_cache
```

关键规则：

- 同一 knowledge_base 只允许一个 active embedding 版本。
- 更换 embedding 模型必须新建 collection。
- 新 collection 校验通过后才更新 `active_embedding.json`。
- 旧 collection 保留只读用于回滚和审计。
- 普通 RAG / GraphRAG 在切换前继续查询旧 active collection。

伪代码：

```python
class EmbeddingReindexJob:
    def run(self, ctx: JobContext) -> None:
        kb = ctx.manifest.target_scope["knowledge_base_id"]
        new_version = embedding_versions.create_from_config(kb, ctx.params["embedding_config"])
        milvus.create_collection(new_version.collection, dimension=new_version.dimension)

        for doc in document_store.iter_documents(ctx.workspace_id, kb):
            child_job = ctx.create_or_inline_stage(
                stage="reembed_document",
                idempotency_key=f"{ctx.job_id}:{doc.doc_version_id}:{new_version.version}",
            )
            self.reembed_document(ctx, doc, new_version)

        validation = milvus.validate_collection(new_version.collection)
        if not validation.ok:
            raise RetryableJobError(validation.message)

        operation_id = stable_operation_id(ctx.job_id, "switch_active_embedding", kb)
        kb_store.switch_active_embedding(
            workspace_id=ctx.workspace_id,
            knowledge_base_id=kb,
            new_version=new_version,
            operation_id=operation_id,
        )
        cache.invalidate_prefix(f"rag:{ctx.workspace_id}:{kb}:")
```

## graph_build_job / graph_rebuild_job

`graph_build_job` 适合单文档，`graph_rebuild_job` 适合知识库级重建。

阶段：

```text
load_chunks
load_model_config(graphrag_llm)
extract_mentions_relations_with_graphrag_llm
merge_batch_extraction_results
resolve_entities
normalize_relations
write_graph_staging
graph_write_batch_internal
validate_graph
finalize_graph_status
```

规则：

- 普通模型不能调用图谱写入接口。
- 图谱写入只允许 Job Worker / 入库 pipeline 调 `graph_write_batch_internal`。
- `graph_build_job` 优先使用 `graphrag_llm` 配置做 chunk batch 结构化抽取；默认每批最多 24 个 chunk。
- GraphRAG LLM 只返回候选 `entities / mentions / relation_facts / evidence / decisions`，不能直接写 Neo4j。
- LLM JSON 解析失败、模型调用失败或 schema 校验失败时，Job 降级到 rule-based extractor，并把 `fallback_used=true`、`failed_source=graphrag_llm`、`error_type` 写入 job artifact 和 graph decisions。
- 关系事实、evidence、entity resolution decision 必须先落 MinIO staging，再写 Neo4j。
- Neo4j 写入必须带 `operation_id`，用于 unknown_outcome 探测和幂等重试。

## log_shipper_job

阶段：

```text
scan_rotated_logs
redact_and_checksum
upload_to_minio
write_shipper_manifest
update_system_log_index
```

规则：

- MinIO 不可用时，本地热日志继续写。
- LogShipper Job 失败不阻断主系统。
- 上传成功后记录 sha256，重复执行时按 sha256 去重。
- 日志归档本身写 system runtime log，但不能递归产生无限日志。

## diagnostic_bundle_job

阶段：

```text
collect_scope
read_filtered_logs
redact_sensitive_fields
pack_bundle
upload_bundle
write_bundle_manifest
```

规则：

- 诊断包默认脱敏。
- 完整 prompt / response 不进入普通诊断包。
- 如果用户显式选择包含敏感调试材料，manifest 必须标记 `sensitive=true`，前端普通日志页默认不展示。
- 生成结果写入 `system/logs/{date}/{runtime_instance_id}/diagnostic_bundles/{bundle_id}`。

## mcp_capability_refresh_job

阶段：

```text
load_mcp_config
resolve_secret_refs
connect_or_restart
initialize
list_tools_resources_prompts
normalize_schema
detect_name_conflicts
write_capability_snapshot
update_tool_registry
invalidate_mcp_cache
```

规则：

- 同一 server 的 refresh 互斥。
- refresh 失败不删除旧 snapshot；旧 snapshot 标记 `stale=true`。
- tool schema hash 变化时，保留原 enabled 状态，但标记 `schema_changed`。
- 名称冲突时禁用冲突 tool，并在 MCP 配置页面显示 `name_conflict`。

伪代码：

```python
class McpCapabilityRefreshJob:
    def run(self, ctx: JobContext) -> None:
        server_name = ctx.manifest.target_scope["server_name"]
        config = mcp_config_store.get(server_name)
        client = mcp_client_factory.create(config, secret_resolver)

        client.connect()
        server_info = client.initialize()
        tools = client.list_tools()
        resources = client.list_resources()
        prompts = client.list_prompts()

        snapshot = capability_builder.build(
            server_name=server_name,
            server_info=server_info,
            tools=tools,
            resources=resources,
            prompts=prompts,
        )
        policy_result = mcp_policy.apply_existing_tool_policy(snapshot)
        tool_registry.update_from_mcp_snapshot(policy_result)
        mcp_snapshot_store.save(server_name, policy_result)
        cache.invalidate(f"mcp:{ctx.workspace_id}:{server_name}:capability")
```

## JobStore 接口

```python
class JobStore:
    def create_job(self, req: CreateJobRequest) -> JobManifest: ...
    def get_manifest(self, workspace_id: str | None, job_id: str) -> JobManifest: ...
    def update_manifest(self, manifest: JobManifest, expected_revision: int) -> JobManifest: ...
    def append_event(self, job_id: str, event: JobEvent, fencing_token: str) -> JobEvent: ...
    def update_leaf_state(self, job_id: str, leaf: JobLeafState, fencing_token: str) -> None: ...
    def update_jobs_index(self, summary: JobSummary) -> None: ...
    def try_acquire_owner(self, manifest: JobManifest, runtime_instance_id: str, ttl: timedelta) -> LeaseResult: ...
    def release_owner(self, job_id: str, runtime_instance_id: str) -> None: ...
    def rebuild_event_index(self, job_id: str) -> EventIndex: ...
    def rebuild_leaf_state(self, job_id: str, event_index: EventIndex) -> JobLeafState: ...
    def rebuild_jobs_index(self, workspace_id: str | None) -> JobsIndex: ...
```

MinIO 写入要求：

- 所有 `put_json` 带 `expected_revision` 或 `expected_etag`。
- SDK 支持 conditional put 时使用 `If-Match`。
- 不支持时读取对象、比较 revision、合并、重试。
- 重试仍冲突时返回 `concurrency_conflict`，由 Scheduler 下次 tick 处理。

## 前端任务中心 / Jobs 页面

P0 页面能力：

- 查看 Job 列表。
- 按状态、类型、知识库、文档、创建时间过滤。
- 查看 Job 当前进度、阶段、错误摘要。
- 打开 Job 详情，显示事件时间线。
- 对 `queued` / `running` / `waiting_retry` Job 发起取消。
- 对可重试失败 Job 发起重试。
- 跳转关联文档、知识库、Run、系统日志 trace。
- 订阅当前 Job SSE，断线后按 `last_event_id` 补发。

UI 建议：

| 区域 | 内容 |
| --- | --- |
| 顶部筛选条 | status、job_type、knowledge_base、doc、time range、search |
| 左侧或主表格 | job title、status tag、progress、current_stage、updated_at |
| 详情抽屉 | manifest 摘要、target_scope、owner、retry、related links |
| Timeline | job_created、stage_started、progress、stage_completed、failed 等事件 |
| Error Panel | 错误类型、是否可重试、建议动作、trace_id |
| Actions | cancel、retry、open logs、open document、open knowledge base |

前端 Adapter 伪代码：

```ts
export type JobSummary = {
  job_id: string;
  job_type: string;
  status: 'created' | 'queued' | 'running' | 'waiting_retry' | 'succeeded' | 'failed' | 'cancelled' | 'unknown_outcome' | 'recovering';
  title: string;
  progress_percent: number;
  current_stage?: string;
  target_scope: Record<string, string>;
  updated_at: string;
};

export class AgentApiClient {
  async createJob(workspaceId: string, input: CreateJobInput): Promise<JobDetail> {
    return this.request(`/workspaces/${workspaceId}/jobs`, { method: 'POST', body: input });
  }

  async listJobs(workspaceId: string, params: JobListParams): Promise<JobPage> {
    return this.request(`/workspaces/${workspaceId}/jobs?${toQuery(params)}`);
  }

  async getJob(workspaceId: string, jobId: string): Promise<JobDetail> {
    return this.request(`/workspaces/${workspaceId}/jobs/${jobId}`);
  }

  async cancelJob(workspaceId: string, jobId: string): Promise<void> {
    return this.request(`/workspaces/${workspaceId}/jobs/${jobId}/cancel`, { method: 'POST' });
  }

  async retryJob(workspaceId: string, jobId: string): Promise<JobDetail> {
    return this.request(`/workspaces/${workspaceId}/jobs/${jobId}/retry`, { method: 'POST' });
  }
}
```

SSE client 伪代码：

```ts
export async function connectJobEventStream(options: {
  workspaceId: string;
  jobId: string;
  afterEventId?: string;
  signal: AbortSignal;
  onEvent: (event: JobEvent) => void;
}) {
  const url = `/workspaces/${options.workspaceId}/jobs/${options.jobId}/events/stream`
    + (options.afterEventId ? `?after_event_id=${encodeURIComponent(options.afterEventId)}` : '');

  const response = await fetch(url, {
    headers: options.afterEventId ? { 'Last-Event-ID': options.afterEventId } : {},
    signal: options.signal,
  });

  for await (const frame of parseSseFrames(response.body)) {
    const event = JSON.parse(frame.data) as JobEvent;
    options.onEvent(event);
  }
}
```

Zustand store 伪代码：

```ts
type JobState = {
  jobsById: Record<string, JobSummary>;
  activeJobId?: string;
  lastEventIdByJob: Record<string, string>;
  streamStatusByJob: Record<string, 'idle' | 'streaming' | 'error' | 'closed'>;
  applyJobEvent: (event: JobEvent) => void;
};

export const useJobStore = create<JobState>((set) => ({
  jobsById: {},
  lastEventIdByJob: {},
  streamStatusByJob: {},
  applyJobEvent: (event) => set((state) => ({
    lastEventIdByJob: {
      ...state.lastEventIdByJob,
      [event.job_id]: event.event_id,
    },
    jobsById: {
      ...state.jobsById,
      [event.job_id]: reduceJobSummary(state.jobsById[event.job_id], event),
    },
  })),
}));
```

## 系统日志联动

每个 Job 必须写系统运行日志：

```json
{
  "component": "job_scheduler",
  "event": "job_stage_progress",
  "trace_id": "trace_001",
  "job_id": "job_01JABC",
  "job_type": "document_ingestion_job",
  "workspace_id": "default",
  "stage": "embedding_insert",
  "status": "running",
  "progress_percent": 46
}
```

日志关系：

- Job event 是业务进度权威日志。
- system runtime log 是排障日志。
- operation audit 记录有副作用写入、回滚和 unknown_outcome。
- 诊断包 Job 会把相关 Job event、system log、operation log 的脱敏片段打包。

## 测试清单

P0 必测：

| 测试 | 期望 |
| --- | --- |
| 重复创建同一 document ingestion job | 返回同一非终态 Job，不重复入库 |
| 不同文档同时入库 | 可以并发执行 |
| 同一文档版本重复入库 | 互斥或幂等返回已有 Job |
| 同一知识库 embedding reindex 并发 | 只允许一个 running |
| Worker 崩溃后恢复 | 进入 recovering，重放事件，继续或给出明确失败 |
| Milvus 写入超时 | 进入 unknown_outcome，先查 chunk 是否已写入 |
| Neo4j 提交后断线 | 进入 unknown_outcome，通过 operation_id 探测 |
| Job SSE 断线 | 按 last_event_id 补发，不重复渲染 |
| jobs_index 损坏 | `index_rebuild_job` 从 manifest / leaf_state 重建 |
| MinIO index 更新失败 | 权威 event / manifest 保留，索引稍后重建 |
| 取消 queued job | 状态变 cancelled，不执行副作用 |
| 取消 running job | 到安全检查点后 cancelled 或 unknown_outcome |
| MCP capability refresh 失败 | 旧 snapshot 保留并标记 stale |
| LogShipper 上传失败 | 本地热日志继续写，Job waiting_retry |
| 诊断包生成 | bundle redacted=true，敏感字段不泄露 |

## 开发顺序

P0 建议先后：

1. 实现 `JobStore`：manifest、event、event_index、leaf_state、jobs_index。
2. 实现 `JobEventWriter` 和 `JobEventReader`。
3. 实现 `JobScheduler`、owner/fencing、conflict detector。
4. 实现基础 Worker loop、cancel、retry、recovery。
5. 接入 `document_ingestion_job`。
6. 接入 `embedding_reindex_job` 和 `graph_build_job`。
7. 接入 `log_shipper_job`、`diagnostic_bundle_job`、`mcp_capability_refresh_job`。
8. 实现 REST + SSE API。
9. 实现前端任务中心 / Jobs 页面。
10. 补充恢复、断线、幂等、互斥和 unknown_outcome 测试。
