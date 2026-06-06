# MinIO 文件与产物存储设计

## 参考源码标注

本文件中 `JSON / JSONL 主状态 + 显式索引对象 + 可重放事件` 的设计，主要参考以下源码后改造成 MinIO 对象存储方案：

| 本项目设计点 | 参考源码路径 | GitHub 地址 | 本项目落地 |
| --- | --- | --- | --- |
| session event tree / JSONL append-only | `.research_repos\pi\packages\agent\src\harness\session\jsonl-storage.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/agent/src/harness/session/jsonl-storage.ts` | MinIO `events/part-*.jsonl` 分段事件对象 |
| session repo、读取和重放 | `.research_repos\pi\packages\agent\src\harness\session\jsonl-repo.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/agent/src/harness/session/jsonl-repo.ts` | MinIO `event_index.json`、`leaf_state.json`、REST 分页读取 |
| session header / entry / parent / leaf | `.research_repos\pi\packages\agent\src\harness\session\session.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/agent/src/harness/session/session.ts` | 本项目 session / run manifest、parent_event_id、previous_event_id、current_leaf_id |
| 有向事件记录 | `.research_repos\crewai\lib\crewai\src\crewai\state\event_record.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/state/event_record.py` | 本项目保留 parent / child / trigger / completed_by 字段 |

## 定位

MinIO 是系统的文件和运行产物仓库，负责保存可回源、可审计、可恢复的数据。

第一版同时使用 MinIO JSON / JSONL 作为主状态方案，但不把 MinIO 当成事务数据库。文档、会话、运行任务的权威状态以 JSON / JSONL 文件保存；列表查询、事件分页、断线补偿和恢复快照通过显式索引文件完成。

当前设计不使用 pgsql、PostgreSQL、MySQL 或独立 Catalog 数据库。原本 Catalog DB 负责的文档状态、版本状态、chunk 索引、run 事件索引、会话快照和记忆索引，全部映射为 MinIO 中的 manifest、index、JSONL 和 snapshot 对象。

保存内容：

- PDF、Word、Markdown、Excel、图片、压缩包等原始文件。
- 文档解析后的全文。
- chunk 列表。
- 单个 chunk JSON。
- 文档 manifest。
- 实体和关系抽取结果。
- 入库事件日志。
- Agent 运行事件日志。
- 工具调用日志。
- 错误日志。
- 操作日志。
- 页面预览图。

推荐 bucket：

```text
agent-files
```

## Object Key 设计

```text
users/{user_id}/manifest.json
users/{user_id}/profile/{memory_id}.json
users/{user_id}/preferences/global/{memory_id}.json
users/{user_id}/indexes/memory_index.json
workspaces/{workspace_id}/manifest.json
workspaces/{workspace_id}/members.json
workspaces/{workspace_id}/secrets/{secret_id}.json
workspaces/{workspace_id}/documents/{doc_id}/original/{file_name}
workspaces/{workspace_id}/documents/{doc_id}/manifest.json
workspaces/{workspace_id}/documents/{doc_id}/versions.json
workspaces/{workspace_id}/documents/{doc_id}/parsed/text.json
workspaces/{workspace_id}/documents/{doc_id}/chunks/chunks.json
workspaces/{workspace_id}/documents/{doc_id}/chunks/chunk-{chunk_index}.json
workspaces/{workspace_id}/documents/{doc_id}/entities/entities.json
workspaces/{workspace_id}/documents/{doc_id}/entities/entity_resolution_decisions.jsonl
workspaces/{workspace_id}/documents/{doc_id}/graph/relationships.json
workspaces/{workspace_id}/documents/{doc_id}/graph/relation_facts.jsonl
workspaces/{workspace_id}/documents/{doc_id}/graph/evidence.jsonl
workspaces/{workspace_id}/documents/{doc_id}/preview/page-{page}.png
workspaces/{workspace_id}/documents/{doc_id}/events/ingestion.jsonl
workspaces/{workspace_id}/indexes/workspace_index.json
workspaces/{workspace_id}/indexes/threads_index.json
workspaces/{workspace_id}/indexes/documents_index.json
workspaces/{workspace_id}/indexes/runs_index.json
workspaces/{workspace_id}/indexes/jobs_index.json
workspaces/{workspace_id}/indexes/memory_index.json
workspaces/{workspace_id}/indexes/secrets_index.json
workspaces/{workspace_id}/indexes/development_tasks_index.json
workspaces/{workspace_id}/development/tasks/{dev_task_id}/manifest.json
workspaces/{workspace_id}/development/tasks/{dev_task_id}/events/part-000001.jsonl
workspaces/{workspace_id}/development/tasks/{dev_task_id}/subagents/{subtask_id}/report.md
workspaces/{workspace_id}/development/tasks/{dev_task_id}/verification/report.md
workspaces/{workspace_id}/knowledge_bases/{knowledge_base_id}/manifest.json
workspaces/{workspace_id}/knowledge_bases/{knowledge_base_id}/active_embedding.json
workspaces/{workspace_id}/sessions/{thread_id}/manifest.json
workspaces/{workspace_id}/sessions/{thread_id}/runs_index.json
workspaces/{workspace_id}/sessions/{thread_id}/messages/message_index.json
workspaces/{workspace_id}/sessions/{thread_id}/messages/part-000001.jsonl
workspaces/{workspace_id}/runs/{run_id}/manifest.json
workspaces/{workspace_id}/runs/{run_id}/events/part-000001.jsonl
workspaces/{workspace_id}/runs/{run_id}/event_index.json
workspaces/{workspace_id}/runs/{run_id}/leaf_state.json
workspaces/{workspace_id}/runs/{run_id}/tool_inventory_snapshot.json
workspaces/{workspace_id}/runs/{run_id}/tool_calls.jsonl
workspaces/{workspace_id}/runs/{run_id}/errors.jsonl
workspaces/{workspace_id}/runs/{run_id}/operations.jsonl
workspaces/{workspace_id}/runs/{run_id}/skill_runs/{skill_run_id}/manifest.json
workspaces/{workspace_id}/runs/{run_id}/skill_runs/{skill_run_id}/args.json
workspaces/{workspace_id}/runs/{run_id}/skill_runs/{skill_run_id}/stdout.txt
workspaces/{workspace_id}/runs/{run_id}/skill_runs/{skill_run_id}/stderr.txt
workspaces/{workspace_id}/runs/{run_id}/skill_runs/{skill_run_id}/result.json
workspaces/{workspace_id}/runs/{run_id}/skill_runs/{skill_run_id}/diff.patch
workspaces/{workspace_id}/runs/{run_id}/skill_runs/{skill_run_id}/operation_plan.json
workspaces/{workspace_id}/runs/{run_id}/artifacts/{artifact_id}
workspaces/{workspace_id}/jobs/{job_id}/manifest.json
workspaces/{workspace_id}/jobs/{job_id}/events/part-000001.jsonl
workspaces/{workspace_id}/jobs/{job_id}/event_index.json
workspaces/{workspace_id}/jobs/{job_id}/leaf_state.json
workspaces/{workspace_id}/jobs/{job_id}/errors.jsonl
workspaces/{workspace_id}/jobs/{job_id}/artifacts/{artifact_id}
workspaces/{workspace_id}/memory/{memory_id}.json
workspaces/{workspace_id}/sessions/{thread_id}/memory_snapshots/{snapshot_id}.json
workspaces/{workspace_id}/sessions/{thread_id}/compactions/{compaction_id}.json
system/logs/{date}/{runtime_instance_id}/system_summary/part-000001.log
system/logs/{date}/{runtime_instance_id}/system_full/part-000001.jsonl
system/logs/{date}/{runtime_instance_id}/errors/part-000001.jsonl
system/logs/{date}/{runtime_instance_id}/components/{component}/part-000001.jsonl
system/logs/{date}/{runtime_instance_id}/diagnostic_bundles/{bundle_id}/manifest.json
system/logs/{date}/{runtime_instance_id}/diagnostic_bundles/{bundle_id}/bundle.zip
system/indexes/jobs_index.json
system/jobs/{job_id}/manifest.json
system/jobs/{job_id}/events/part-000001.jsonl
system/jobs/{job_id}/event_index.json
system/jobs/{job_id}/leaf_state.json
system/jobs/{job_id}/errors.jsonl
system/jobs/{job_id}/artifacts/{artifact_id}
```

## 第一版主状态方案

第一版确认：

```text
MinIO JSON / JSONL = 主状态和产物存储
轻量索引 JSON = 前端列表、任务列表、快速入口
Milvus = 向量检索
Neo4j = 图谱关系和多跳扩展
```

这意味着：

- `manifest.json` 是单个文档的权威状态。
- `versions.json` 是单个逻辑文档的版本历史。
- `chunks.json` 是单个文档 chunk 的权威索引。
- `runs/{run_id}/manifest.json` 是单次 Agent run 的权威状态。
- `sessions/{thread_id}/manifest.json` 是一个前端可见对话窗口的权威状态。
- `sessions/{thread_id}/messages/part-*.jsonl` 保存这个对话的持久化消息历史。
- `sessions/{thread_id}/runs_index.json` 保存这个对话下的 run 列表摘要。
- `events/part-*.jsonl` 是单次运行的分段事件流记录。
- `event_index.json` 是事件分页、SSE 断线补偿和重放的索引。
- `leaf_state.json` 是 run 当前可恢复快照。
- `tool_inventory_snapshot.json` 记录本次 run / model call 生成时模型实际可见的工具集合，用于审计、恢复和调试，不默认注入模型上下文。
- `operations.jsonl` 是副作用操作记录。
- `skill_runs/{skill_run_id}` 保存 Skill Script 每次执行的参数、日志、结果、staged patch 和 operation plan。
- `jobs/{job_id}/manifest.json` 是后台 Job 的权威状态；Job 与 Run 分开，文档入库、embedding 重建、图谱构建、日志归档、诊断包和 MCP capability refresh 都走 Job。
- `jobs/{job_id}/events/part-*.jsonl`、`event_index.json`、`leaf_state.json` 分别保存 Job 事件、事件索引和恢复快照。
- `secrets/{secret_id}.json` 保存 AES-256-GCM 加密后的模型、数据库、MCP 和代理凭据，不保存明文。
- `secrets_index.json` 保存 Secret 的脱敏摘要、状态和对象路径，用于配置页列表、引用检查和索引重建。
- `system/logs/{date}/{runtime_instance_id}` 保存系统运行日志的 MinIO 归档；本地热日志仍是故障排查第一入口。
- `development/tasks/{dev_task_id}` 保存 Codex/SubAgent 开发任务、开发事件、SubAgent 报告和验证报告。
- `threads_index.json` 只保存左侧会话列表摘要，不保存完整消息。
- `documents_index.json` 只保存文档列表摘要，不保存完整正文。
- `runs_index.json` 只保存运行任务列表摘要，不保存完整事件。
- `jobs_index.json` 只保存后台 Job 列表摘要，不保存完整事件。
- 索引文件可以从各个 manifest / JSONL 重建，因此索引损坏不是数据彻底丢失。

## MinIO 状态对象开发规格

### User / Workspace / Thread 语义

P0 默认单用户、默认单 workspace，但所有对象路径和 manifest 都保留用户和工作区边界。

| 字段 | 含义 |
| --- | --- |
| `user_id` | 当前用户 ID；P0 默认 `default_user` |
| `role` | 当前用户系统角色；P0 默认 `owner` |
| `workspace_id` | 项目空间 ID；P0 默认 `default` |
| `workspace_role` | 用户在 workspace 内的角色；P0 默认 `owner` |
| `thread_id` | workspace 内的一个前端可见对话窗口 |

规则：

- `workspace` 是项目空间，不是对话窗口。
- P0 前端不做复杂 workspace 切换，但所有 API、Tool、日志和对象路径都必须携带 `workspace_id`。
- `users/{user_id}` 保存全局用户资料和全局用户偏好。
- `workspaces/{workspace_id}` 保存项目空间内的 thread、run、文档、知识库、配置、日志和 workspace 级 memory。
- `user_profile` 写入 `users/{user_id}/profile`。
- global `user_preference` 写入 `users/{user_id}/preferences/global`。
- workspace `user_preference`、`project_fact`、`project_rule` 写入 `workspaces/{workspace_id}/memory`。

`workspaces/{workspace_id}/manifest.json`：

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "name": "Default Workspace",
  "owner_user_id": "default_user",
  "status": "active",
  "features": {
    "workspace_switch_enabled": false,
    "multi_user_enabled": false
  },
  "created_at": "2026-05-30T00:00:00+08:00",
  "updated_at": "2026-05-30T00:00:00+08:00"
}
```

`workspaces/{workspace_id}/members.json`：

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "members": [
    {
      "user_id": "default_user",
      "role": "owner",
      "status": "active",
      "created_at": "2026-05-30T00:00:00+08:00"
    }
  ]
}
```

`workspaces/{workspace_id}/secrets/{secret_id}.json`：

```json
{
  "schema_version": 1,
  "secret_id": "secret_model_main_api_key",
  "workspace_id": "default",
  "scope": "workspace",
  "type": "model_api_key",
  "display_name": "主对话模型 API Key",
  "encrypted_value": {
    "alg": "AES-256-GCM",
    "ciphertext": "base64...",
    "nonce": "base64...",
    "tag": "base64...",
    "key_version": "v1"
  },
  "masked": "sk-****abcd",
  "status": "active",
  "created_by": "default_user",
  "created_at": "2026-05-30T00:00:00+08:00",
  "updated_at": "2026-05-30T00:00:00+08:00",
  "last_used_at": null,
  "revision": 1
}
```

Secret 对象规则：

- `ciphertext`、`nonce`、`tag` 只能由后端 Secret Store 写入。
- `AGENT_MASTER_KEY` 只来自本机环境变量，不能写入 MinIO。
- 前端读取 Secret 时只能得到 `secret_id`、`type`、`display_name`、`masked`、`status` 和时间字段。
- 模型、ToolResult、SSE 事件和日志都不能包含 `encrypted_value`。
- 配置对象引用 Secret 时只保存 `secret_ref` 或 `*_ref` 字段。

### Thread / Session / Run 语义

P0 统一命名：

| 字段 | 含义 |
| --- | --- |
| `thread_id` | 一个前端可见的对话窗口，类似 ChatGPT / Gemini 左侧历史里的一条会话 |
| `session` | 存储路径命名，等价承载 `thread_id` 的对话数据，路径保留 `sessions/{thread_id}` |
| `run_id` | 一个 thread 中，用户提交一次消息后触发的一次 Agent 执行 |
| `message_id` | thread 消息流中的一条用户、assistant 或系统可见消息 |
| `event_id` | run 内部事件，用于工具状态、审批、SSE 补发和审计 |

规则：

- 用户可以创建多个 thread。
- 创建新 thread 不删除旧 thread。
- 旧 thread 默认保留在 `threads_index.json` 和 `sessions/{thread_id}` 下。
- 新 thread 不继承旧 thread 的短期上下文和 compaction。
- 长期记忆可以跨 thread 注入；global `user_profile` / `user_preference` 可跨 workspace，workspace `user_preference`、`project_fact`、`project_rule` 只在当前 `workspace_id` 内注入。
- 一个 thread 可以包含多个 run；一个 run 只能属于一个 thread。
- SSE 订阅以 run 为单位，历史消息展示以 thread 为单位。

### Run / Job 边界

Job 是后台工作单，不是 thread 中的一次 Agent 执行。

| 字段 | 含义 |
| --- | --- |
| `run_id` | 用户提交消息后触发的 Agent 执行，事件用于对话、工具状态、审批和 assistant 输出 |
| `job_id` | 后台长耗时工作单，事件用于文档入库、embedding 重建、图谱构建、日志归档、诊断包和 MCP capability refresh |

规则：

- Run 事件写入 `runs/{run_id}`，Job 事件写入 `jobs/{job_id}`。
- Run 可以创建或关联 Job，Job 通过 `related_run_id` / `related_thread_id` 回指来源。
- Job 进度不写入 thread messages；前端在任务中心或相关文档页展示进度。
- Job 使用独立 `jobs_index.json`，不复用 `runs_index.json`。

### runs/{run_id}/manifest.json

`run manifest` 是单次 Agent run 的权威元数据。它不保存完整事件，只保存当前 run 的身份、状态、所有关键对象路径和并发写入 fencing 信息。

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "thread_id": "thread_001",
  "run_id": "run_001",
  "parent_run_id": null,
  "status": "running",
  "title": "用户发起的一次 Agent 运行",
  "owner": {
    "runtime_instance_id": "rt_001",
    "fencing_token": "fence_001",
    "acquired_at": "2026-05-29T12:00:00+08:00",
    "expires_at": "2026-05-29T12:05:00+08:00"
  },
  "object_keys": {
    "event_index": "workspaces/default/runs/run_001/event_index.json",
    "leaf_state": "workspaces/default/runs/run_001/leaf_state.json",
    "tool_inventory_snapshot": "workspaces/default/runs/run_001/tool_inventory_snapshot.json",
    "operations": "workspaces/default/runs/run_001/operations.jsonl",
    "tool_calls": "workspaces/default/runs/run_001/tool_calls.jsonl",
    "errors": "workspaces/default/runs/run_001/errors.jsonl"
  },
  "last_event_seq": 12,
  "last_event_id": "evt_run_001_000000000012",
  "revision": 4,
  "created_at": "2026-05-29T12:00:00+08:00",
  "updated_at": "2026-05-29T12:01:10+08:00"
}
```

状态枚举：

```text
created
running
waiting_approval
cancel_requested
cancelling
completed
failed
cancelled
recovering
```

### runs/{run_id}/events/part-*.jsonl

事件采用分段 JSONL。每一行是一个完整 JSON。事件按 `event_seq` 严格递增。

事件 ID 规则：

```text
event_seq = run 内从 1 开始递增的整数
event_id = evt_{run_id}_{event_seq_12位补零}
```

示例：

```json
{"schema_version":1,"event_seq":1,"event_id":"evt_run_001_000000000001","run_id":"run_001","thread_id":"thread_001","type":"run_started","created_at":"2026-05-29T12:00:00+08:00"}
{"schema_version":1,"event_seq":2,"event_id":"evt_run_001_000000000002","run_id":"run_001","thread_id":"thread_001","type":"user_message","message_id":"msg_001","role":"user","content":"开始分析这个知识库","created_at":"2026-05-29T12:00:01+08:00"}
{"schema_version":1,"event_seq":3,"event_id":"evt_run_001_000000000003","run_id":"run_001","thread_id":"thread_001","type":"tool_call_started","tool_call_id":"call_001","tool_name":"rag_search","created_at":"2026-05-29T12:00:03+08:00"}
```

P0 事件类型：

```text
run_started
run_completed
run_failed
run_cancel_requested
run_cancelled
user_message
assistant_delta
assistant_message
model_call_started
model_call_completed
model_call_failed
tool_call_requested
tool_call_started
tool_call_update
tool_call_completed
tool_call_failed
tool_call_skipped
approval_requested
approval_approved
approval_rejected
operation_pending
operation_committed
operation_failed
operation_unknown_outcome
memory_snapshot_created
compaction_requested
compaction_completed
compaction_failed
subagent_started
subagent_update
subagent_completed
subagent_failed
tool_inventory_changed
skill_activated
skill_run_started
skill_run_finished
skill_run_failed
skill_patch_staged
skill_patch_committed
model_config_changed
error_recorded
```

高频 token 处理：

- Runtime 可以通过 SSE 实时发送细粒度 token。
- MinIO 不逐 token 写入。
- Runtime 将 token 聚合为 `assistant_delta` 事件，默认每 250ms 或累计 512 字符写入一次。
- assistant 完成后必须写一条 `assistant_message`，包含完整文本或完整文本对象路径。
- 断线恢复时以前端最后收到的 `event_id` 为准，通过持久化事件补齐；如果只缺细粒度 token，不影响最终 message 重建。

### runs/{run_id}/event_index.json

`event_index.json` 用于快速定位事件分段、支持分页、SSE 断线补偿和审计回放。

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "thread_id": "thread_001",
  "run_id": "run_001",
  "status": "running",
  "first_event_seq": 1,
  "last_event_seq": 120,
  "first_event_id": "evt_run_001_000000000001",
  "last_event_id": "evt_run_001_000000000120",
  "segments": [
    {
      "segment_no": 1,
      "object_key": "workspaces/default/runs/run_001/events/part-000001.jsonl",
      "first_event_seq": 1,
      "last_event_seq": 100,
      "event_count": 100,
      "sealed": true,
      "sha256": "sha256..."
    },
    {
      "segment_no": 2,
      "object_key": "workspaces/default/runs/run_001/events/part-000002.jsonl",
      "first_event_seq": 101,
      "last_event_seq": 120,
      "event_count": 20,
      "sealed": false,
      "sha256": "sha256..."
    }
  ],
  "revision": 8,
  "rebuilt_from": null,
  "updated_at": "2026-05-29T12:03:00+08:00"
}
```

分页读取规则：

- `after_event_seq` 为空时从 `first_event_seq` 开始。
- `after_event_id` 优先解析出 `event_seq`。
- 通过 `segments` 找到第一个 `last_event_seq > after_event_seq` 的分段。
- 读取分段 JSONL，过滤 `event_seq > after_event_seq`。
- 返回 `limit` 条事件和 `next_after_event_id`。

### runs/{run_id}/leaf_state.json

`leaf_state.json` 是当前 run 的可恢复快照。它由事件重放得到，也可以在运行中增量更新。

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "thread_id": "thread_001",
  "run_id": "run_001",
  "status": "waiting_approval",
  "last_event_seq": 88,
  "last_event_id": "evt_run_001_000000000088",
  "current_message_id": "msg_010",
  "active_model_call": null,
  "active_tool_calls": [
    {
      "tool_call_id": "call_008",
      "tool_name": "write_file",
      "status": "waiting_approval"
    }
  ],
  "pending_approvals": ["appr_001"],
  "active_subagents": [],
  "last_compaction_id": "cmp_001",
  "memory_snapshot_id": "memsnap_001",
  "model_config_version": "modelcfg_004",
  "tool_inventory_hash": "sha256...",
  "tool_inventory_snapshot_object_key": "workspaces/default/runs/run_001/tool_inventory_snapshot.json",
  "active_skills": [
    {
      "skill_id": "document_cleaner",
      "version": "0.1.0",
      "entrypoint_tools": [
        "skill_document_cleaner_normalize_input"
      ]
    }
  ],
  "revision": 11,
  "updated_at": "2026-05-29T12:02:00+08:00"
}
```

恢复用途：

- 前端刷新后读取 run 当前状态。
- SSE 断线重连后判断 run 是否仍在运行。
- Runtime 崩溃后判断是否有 pending approval、active tool、unknown outcome。
- 压缩后记录当前可用 compaction。
- 模型调用前后判断 tool inventory 是否漂移。
- Skill 激活后记录 active_skills，恢复时重新注册对应脚本入口。

### runs/{run_id}/tool_inventory_snapshot.json

`tool_inventory_snapshot.json` 是本次 run 或某次 model call 的工具清单快照。它不是给模型看的提示词，而是给 Runtime、审计日志和调试页面使用。

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "thread_id": "thread_001",
  "run_id": "run_001",
  "model_call_id": "modelcall_001",
  "inventory_hash": "sha256...",
  "visible_to_model": false,
  "tools": [
    {
      "name": "rag_search",
      "source": "built_in",
      "enabled": true,
      "risk_level": "low",
      "requires_approval": false,
      "schema_hash": "sha256..."
    },
    {
      "name": "mcp_github_search_issues",
      "source": "mcp",
      "server_name": "github",
      "original_tool_name": "search_issues",
      "enabled": true,
      "risk_level": "medium",
      "requires_approval": false,
      "schema_hash": "sha256..."
    },
    {
      "name": "call_subagent_code_reviewer",
      "source": "subagent",
      "agent_type": "code_reviewer",
      "enabled": true,
      "risk_level": "medium",
      "requires_approval": false,
      "schema_hash": "sha256..."
    },
    {
      "name": "skill_search",
      "source": "skill_discovery",
      "enabled": true,
      "risk_level": "low",
      "requires_approval": false,
      "schema_hash": "sha256..."
    }
  ],
  "disabled_tools": [
    {
      "name": "mcp_filesystem_delete_file",
      "source": "mcp",
      "reason": "tool_disabled_by_user"
    }
  ],
  "created_at": "2026-05-30T12:00:00+08:00"
}
```

写入规则：

- 每次 run 启动时生成一次初始快照。
- MCP capability snapshot 变化、用户启用 / 禁用 MCP tool、Skill 激活或停用、SubAgent 可用集合变化时，重新生成快照。
- 新快照写入后，更新 `leaf_state.tool_inventory_hash` 和 `leaf_state.tool_inventory_snapshot_object_key`。
- 如果 run 正在等待模型返回，不中途改变已经发给模型的工具列表；变化只影响下一次模型调用。
- 快照可由日志审计页面只读查看，但默认不进入模型上下文。

### sessions/{thread_id}/manifest.json

会话 manifest 保存一个前端可见对话窗口的权威状态、多 run 串联关系、当前 leaf 和左侧列表所需的摘要字段。

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "user_id": "user_001",
  "thread_id": "thread_001",
  "current_run_id": "run_003",
  "current_leaf_run_id": "run_003",
  "run_ids": ["run_001", "run_002", "run_003"],
  "parent_thread_id": null,
  "status": "active",
  "title": "Agent 系统设计讨论",
  "title_source": "auto",
  "pinned": false,
  "archived": false,
  "soft_deleted": false,
  "knowledge_base_id": "kb_default",
  "model_config_version": "modelcfg_004",
  "message_count": 30,
  "run_count": 3,
  "last_message_id": "msg_030",
  "last_message_role": "assistant",
  "last_message_preview": "已整理多对话窗口和历史会话的存储设计。",
  "last_message_at": "2026-05-29T12:03:00+08:00",
  "last_compaction_id": "cmp_002",
  "last_memory_snapshot_id": "memsnap_003",
  "object_keys": {
    "messages_index": "workspaces/default/sessions/thread_001/messages/message_index.json",
    "messages_first_part": "workspaces/default/sessions/thread_001/messages/part-000001.jsonl",
    "runs_index": "workspaces/default/sessions/thread_001/runs_index.json"
  },
  "revision": 9,
  "created_at": "2026-05-29T10:00:00+08:00",
  "updated_at": "2026-05-29T12:03:00+08:00",
  "archived_at": null,
  "deleted_at": null
}
```

状态枚举：

```text
active
archived
soft_deleted
```

P0 行为：

- 新建对话时创建 `manifest.json`、`messages/message_index.json`、`runs_index.json`，再更新工作区 `threads_index.json`。
- 用户重命名对话只更新 `title`、`title_source=manual`、`revision` 和 `threads_index.json` 摘要。
- 用户归档对话时设置 `status=archived`、`archived=true`、`archived_at`，默认不删除消息和 run。
- 用户删除对话时默认软删除，设置 `status=soft_deleted`、`soft_deleted=true`、`deleted_at`，从默认列表隐藏，但对象仍保留用于恢复和审计。
- 物理删除属于高风险能力，P0 不作为默认用户操作。
- P0 底层预留 `parent_thread_id`、`current_leaf_run_id`，前端分支 UI 可以后置。

### sessions/{thread_id}/messages/part-*.jsonl

thread 消息是用户在历史会话里看到的持久化消息流。它保存用户消息、最终 assistant 消息和少量系统可见状态，不保存所有工具内部事件；工具事件仍然在 `runs/{run_id}/events/part-*.jsonl`。

消息 ID 规则：

```text
message_seq = thread 内从 1 开始递增的整数
message_id = msg_{thread_id}_{message_seq_12位补零}
```

示例：

```json
{"schema_version":1,"message_seq":1,"message_id":"msg_thread_001_000000000001","thread_id":"thread_001","run_id":"run_001","role":"user","content_type":"text","content":"帮我设计 Agent 系统。","status":"completed","created_at":"2026-05-29T10:00:00+08:00"}
{"schema_version":1,"message_seq":2,"message_id":"msg_thread_001_000000000002","thread_id":"thread_001","run_id":"run_001","role":"assistant","content_type":"text","content_object_key":null,"content":"可以，我们先确认数据层和工作流。","status":"completed","source_event_id":"evt_run_001_000000000120","created_at":"2026-05-29T10:00:20+08:00"}
```

字段：

| 字段 | 说明 |
| --- | --- |
| `message_seq` | thread 内递增序号，用于分页 |
| `message_id` | 消息唯一 ID |
| `run_id` | 触发或生成该消息的 run，没有则为 null |
| `role` | user / assistant / system |
| `content_type` | text / multimodal / object_ref |
| `content` | 短文本直接保存 |
| `content_object_key` | 长文本、多模态或附件引用 |
| `status` | streaming / completed / interrupted / failed |
| `source_event_id` | assistant 完整消息对应的 run 事件 |

写入规则：

- 用户提交消息时，先追加 user message，再创建 run。
- assistant 流式输出时，前端先看 SSE；最终 `assistant_message` 事件落盘后，再追加 assistant message 到 thread 消息流。
- 如果模型流中断但恢复失败，追加一条 `assistant` 消息，`status=interrupted`，并保留可见错误摘要。
- message JSONL 分段达到阈值后封存，新的消息写入下一段。

### sessions/{thread_id}/messages/message_index.json

`message_index.json` 用于历史消息分页和打开旧对话时快速定位消息分段。

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "thread_id": "thread_001",
  "first_message_seq": 1,
  "last_message_seq": 30,
  "first_message_id": "msg_thread_001_000000000001",
  "last_message_id": "msg_thread_001_000000000030",
  "segments": [
    {
      "segment_no": 1,
      "object_key": "workspaces/default/sessions/thread_001/messages/part-000001.jsonl",
      "first_message_seq": 1,
      "last_message_seq": 30,
      "message_count": 30,
      "sealed": false,
      "sha256": "sha256..."
    }
  ],
  "revision": 5,
  "updated_at": "2026-05-29T12:03:00+08:00"
}
```

分页读取规则：

- `before_message_id` 为空时从最新消息倒序读取。
- `after_message_id` 用于继续向后拉取较新的消息。
- 前端首次打开 thread 默认取最近 50 条消息。
- 用户向上滚动时按 `before_message_id` 继续分页。

### sessions/{thread_id}/runs_index.json

thread 内 run 索引用于打开旧对话后展示每轮执行状态、恢复当前运行、跳转日志审计。

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "thread_id": "thread_001",
  "runs": [
    {
      "run_id": "run_001",
      "status": "completed",
      "user_message_id": "msg_thread_001_000000000001",
      "assistant_message_id": "msg_thread_001_000000000002",
      "manifest_object_key": "workspaces/default/runs/run_001/manifest.json",
      "leaf_state_object_key": "workspaces/default/runs/run_001/leaf_state.json",
      "event_index_object_key": "workspaces/default/runs/run_001/event_index.json",
      "last_event_id": "evt_run_001_000000000120",
      "started_at": "2026-05-29T10:00:00+08:00",
      "completed_at": "2026-05-29T10:00:20+08:00",
      "updated_at": "2026-05-29T10:00:20+08:00"
    }
  ],
  "revision": 4,
  "updated_at": "2026-05-29T12:03:00+08:00"
}
```

### operations.jsonl

`operations.jsonl` 只记录副作用操作，不记录普通 token。

```json
{
  "schema_version": 1,
  "operation_id": "op_001",
  "run_id": "run_001",
  "tool_call_id": "call_008",
  "tool_name": "write_file",
  "idempotency_key": "idem_001",
  "side_effect": true,
  "reversible": true,
  "rollback_strategy": "file_backup",
  "rollback_token": "backup_001",
  "status": "committed",
  "started_event_id": "evt_run_001_000000000080",
  "completed_event_id": "evt_run_001_000000000088",
  "created_at": "2026-05-29T12:02:00+08:00"
}
```

状态枚举：

```text
pending
committed
failed
unknown_outcome
rolled_back
compensated
```

## 写入与并发规则

### 单 run 写入器

P0 要求每个 `run_id` 在同一时间只有一个 `EventStoreWriter`：

```text
Tool / SubAgent / Connector
  -> Runtime event bus
  -> EventStoreWriter
  -> MinIO events + index + leaf_state
```

禁止：

```text
Tool 直接重写 events/part-*.jsonl
SubAgent 直接重写 event_index.json
MCP Connector 直接重写 leaf_state.json
前端直接写 MinIO 状态对象
```

### 乐观并发控制

每个可重写 JSON 索引对象必须包含：

```json
{
  "revision": 1,
  "updated_at": "2026-05-29T12:00:00+08:00"
}
```

更新流程：

```text
1. 读取对象，拿到 revision 和 etag。
2. 基于当前内容生成 next payload，revision + 1。
3. 写入对象时使用 If-Match previous etag。
4. 如果对象存储客户端不支持 If-Match，则写入前重新读取 revision。
5. revision 不一致时放弃本次写入，重新读取、合并、最多重试 3 次。
6. 仍失败时写 errors.jsonl，等待 IndexRebuilder 修复。
```

### Run ownership / fencing

多 Runtime 实例部署时，`runs/{run_id}/manifest.json` 中的 `owner.fencing_token` 用于避免两个进程同时恢复同一个 run。

规则：

- 启动 run 时写入 `runtime_instance_id`、`fencing_token`、`expires_at`。
- EventStoreWriter 每 30 秒刷新 lease。
- 其他 Runtime 发现 lease 未过期，不得接管。
- lease 过期后，新 Runtime 可以进入 `recovering`，重放事件，写入新的 fencing token。
- 旧 Runtime 如果发现自己的 fencing token 已失效，必须停止写入并上报错误。

### 分段规则

事件分段默认规则：

```text
segment_max_events = 1000
segment_max_bytes = 512KB
delta_flush_interval = 250ms
delta_flush_chars = 512
```

达到任意阈值时封存当前分段，创建下一个 `part-000002.jsonl`。封存后的分段不可修改。

## 索引重建规则

后台 `IndexRebuilder` 负责在索引缺失、索引损坏、更新失败、Runtime 崩溃恢复时重建索引。

重建输入：

- `runs/{run_id}/manifest.json`
- `runs/{run_id}/events/part-*.jsonl`
- `runs/{run_id}/operations.jsonl`
- `jobs/{job_id}/manifest.json`
- `jobs/{job_id}/events/part-*.jsonl`
- `jobs/{job_id}/leaf_state.json`
- `documents/{doc_id}/manifest.json`
- `documents/{doc_id}/versions.json`
- `documents/{doc_id}/chunks/chunks.json`
- `memory/{memory_id}.json`
- `secrets/{secret_id}.json`
- `development/tasks/{dev_task_id}/manifest.json`
- `development/tasks/{dev_task_id}/events/part-*.jsonl`

重建 run 索引流程：

```text
1. list runs/{run_id}/events/part-*.jsonl
2. 按 segment_no 排序
3. 逐行解析 JSONL
4. 校验 event_seq 连续性和 event_id 格式
5. 跳过重复 event_id，记录 duplicate_event_count
6. 遇到损坏 JSON 行，标记 segment_corrupt 并写 errors.jsonl
7. 生成新的 event_index.json
8. 从事件重放生成 leaf_state.json
9. 更新 runs_index.json 中该 run 的摘要
10. 对 Job 事件执行同样流程，生成 jobs/{job_id}/event_index.json、leaf_state.json，并更新 jobs_index.json 摘要
```

重建文档索引流程：

```text
1. list documents/{doc_id}/manifest.json
2. 读取 versions.json 和 chunks/chunks.json
3. 生成 documents_index.json 摘要
4. 校验 active embedding collection 与 knowledge_bases/{kb_id}/active_embedding.json 一致
```

重建记忆索引流程：

```text
1. list memory/*.json
2. 过滤 deleted=true 的记忆
3. 生成 memory_index.json
4. 校验 enabled_for_model_context 与 memory snapshot 构建规则一致
```

重建 Secret 索引流程：

```text
1. list secrets/*.json
2. 读取每个 Secret 对象
3. 只提取 secret_id / type / display_name / masked / status / object_key / updated_at
4. 生成 indexes/secrets_index.json
5. 校验配置对象中的 *_ref 是否仍指向 active Secret
```

重建开发任务索引流程：

```text
1. list development/tasks/*/manifest.json
2. 读取每个 dev task manifest
3. 生成 development_tasks_index.json
4. 校验 verification/report.md 是否存在
5. 按 status、priority、updated_at 生成摘要
```

## 崩溃恢复规则

Runtime 启动或接管 run 时：

```text
1. 读取 run manifest。
2. 检查 owner lease 是否过期。
3. 读取 event_index.json；不存在或损坏则重建。
4. 读取 leaf_state.json；不存在或 revision 落后则从 events 重放生成。
5. 检查 active_tool_calls。
6. 对副作用工具查 operations.jsonl。
7. pending 状态超过 timeout 的副作用操作标记 unknown_outcome。
8. 恢复 pending approval。
9. 如果 run 已 completed/failed/cancelled，只允许历史查询，不继续执行。
10. 如果 run 是 running/recovering，进入 LangGraph checkpoint 恢复或生成可见错误。
```

恢复后的前端行为：

- run 仍在运行：SSE 继续连接，并用 `after_event_id` 补齐历史。
- run 已结束：REST 拉取 leaf_state 和事件历史，SSE 不再保持。
- run 进入 unknown_outcome：前端显示需要用户确认或等待系统诊断。

## 轻量索引文件

`workspace_index.json` 记录工作区级入口信息：

```json
{
  "workspace_id": "default",
  "threads_index_object_key": "workspaces/default/indexes/threads_index.json",
  "documents_index_object_key": "workspaces/default/indexes/documents_index.json",
  "runs_index_object_key": "workspaces/default/indexes/runs_index.json",
  "jobs_index_object_key": "workspaces/default/indexes/jobs_index.json",
  "memory_index_object_key": "workspaces/default/indexes/memory_index.json",
  "secrets_index_object_key": "workspaces/default/indexes/secrets_index.json",
  "development_tasks_index_object_key": "workspaces/default/indexes/development_tasks_index.json",
  "updated_at": "2026-05-28T00:00:00+08:00"
}
```

`threads_index.json` 记录左侧历史会话列表摘要：

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "threads": [
    {
      "thread_id": "thread_001",
      "user_id": "user_001",
      "title": "Agent 系统设计讨论",
      "status": "active",
      "pinned": false,
      "archived": false,
      "soft_deleted": false,
      "manifest_object_key": "workspaces/default/sessions/thread_001/manifest.json",
      "messages_index_object_key": "workspaces/default/sessions/thread_001/messages/message_index.json",
      "runs_index_object_key": "workspaces/default/sessions/thread_001/runs_index.json",
      "current_run_id": "run_003",
      "current_run_status": "completed",
      "last_message_id": "msg_thread_001_000000000030",
      "last_message_preview": "已整理多对话窗口和历史会话的存储设计。",
      "last_message_at": "2026-05-29T12:03:00+08:00",
      "message_count": 30,
      "run_count": 3,
      "created_at": "2026-05-29T10:00:00+08:00",
      "updated_at": "2026-05-29T12:03:00+08:00"
    }
  ],
  "revision": 12,
  "updated_at": "2026-05-29T12:03:00+08:00"
}
```

列表读取规则：

- 默认只返回 `status=active` 且 `soft_deleted=false` 的 thread。
- `archived=true` 的 thread 进入归档筛选，不出现在默认列表。
- `soft_deleted=true` 的 thread 进入回收站筛选，P0 可以先只读恢复，不做物理删除。
- 排序默认 `pinned desc, last_message_at desc, updated_at desc`。
- 搜索会话时先查 `threads_index.json` 的 title 和 preview；需要全文历史搜索时走 session search 索引。

`documents_index.json` 记录文档列表摘要：

```json
{
  "workspace_id": "default",
  "documents": [
    {
      "doc_id": "doc_001",
      "current_doc_version_id": "docv_001",
      "knowledge_base_id": "kb_default",
      "file_name": "example.pdf",
      "mime_type": "application/pdf",
      "size_bytes": 123456,
      "manifest_object_key": "workspaces/default/documents/doc_001/manifest.json",
      "parse_status": "parsed",
      "embedding_status": "indexed",
      "graph_status": "indexed",
      "chunk_count": 128,
      "created_at": "2026-05-28T00:00:00+08:00",
      "updated_at": "2026-05-28T00:00:00+08:00"
    }
  ]
}
```

`runs_index.json` 记录运行任务列表摘要：

```json
{
  "workspace_id": "default",
  "runs": [
    {
      "run_id": "run_001",
      "thread_id": "thread_001",
      "status": "running",
      "title": "询问知识库入库状态",
      "manifest_object_key": "workspaces/default/runs/run_001/manifest.json",
      "event_index_object_key": "workspaces/default/runs/run_001/event_index.json",
      "leaf_state_object_key": "workspaces/default/runs/run_001/leaf_state.json",
      "operations_object_key": "workspaces/default/runs/run_001/operations.jsonl",
      "last_event_id": "evt_run_001_000000000001",
      "started_at": "2026-05-28T00:00:00+08:00",
      "updated_at": "2026-05-28T00:00:00+08:00"
    }
  ]
}
```

`jobs_index.json` 记录后台 Job 列表摘要：

```json
{
  "workspace_id": "default",
  "jobs": [
    {
      "job_id": "job_001",
      "job_type": "document_ingestion_job",
      "status": "running",
      "title": "入库 example.pdf",
      "target_scope": {
        "scope_type": "document_version",
        "knowledge_base_id": "kb_default",
        "doc_id": "doc_001",
        "doc_version_id": "docv_001"
      },
      "progress_percent": 46,
      "current_stage": "embedding_insert",
      "manifest_object_key": "workspaces/default/jobs/job_001/manifest.json",
      "event_index_object_key": "workspaces/default/jobs/job_001/event_index.json",
      "leaf_state_object_key": "workspaces/default/jobs/job_001/leaf_state.json",
      "last_event_id": "evt_job_001_000000000036",
      "created_at": "2026-05-30T11:50:00+08:00",
      "updated_at": "2026-05-30T12:01:00+08:00"
    }
  ]
}
```

`memory_index.json` 记录长期记忆列表摘要：

```json
{
  "workspace_id": "default",
  "memories": [
    {
      "memory_id": "mem_001",
      "type": "user_preference",
      "summary": "用户偏好中文回复。",
      "content_object_key": "workspaces/default/memory/mem_001.json",
      "sensitive": false,
      "frontend_visible": true,
      "enabled_for_model_context": true,
      "updated_at": "2026-05-28T00:00:00+08:00"
    }
  ]
}
```

## 索引更新规则

- 创建文档时，先写原始文件和 `manifest.json`，再更新 `documents_index.json`。
- 文档状态变化时，先更新 `manifest.json`，再更新 `documents_index.json` 的摘要字段。
- 新建对话时，创建 `sessions/{thread_id}/manifest.json`、`messages/message_index.json`、`sessions/{thread_id}/runs_index.json`，再更新 `threads_index.json`。
- 对话重命名、置顶、归档、软删除时，先更新 `sessions/{thread_id}/manifest.json`，再更新 `threads_index.json`。
- 新用户消息写入时，先追加 `sessions/{thread_id}/messages/part-*.jsonl`，再更新 `message_index.json`、thread manifest 和 `threads_index.json`。
- 新运行开始时，创建 `runs/{run_id}/manifest.json`、`event_index.json`、`leaf_state.json`，写入 `run_started` 事件，再更新 workspace `runs_index.json` 和 thread `runs_index.json`。
- 运行状态变化时，追加 `events/part-*.jsonl`，再更新 `event_index.json`、`leaf_state.json`、workspace `runs_index.json`、thread `runs_index.json` 和 `threads_index.json` 的状态摘要。
- 新建 Job 时，创建 `jobs/{job_id}/manifest.json`、`event_index.json`、`leaf_state.json`，写入 `job_created` / `job_queued` 事件，再更新 workspace 或 system `jobs_index.json`。
- Job 状态变化时，追加 `jobs/{job_id}/events/part-*.jsonl`，再更新 Job `event_index.json`、`leaf_state.json`、`manifest.json` 和 `jobs_index.json` 摘要。
- assistant 最终消息生成后，先写 `assistant_message` 事件，再追加 thread assistant message，最后更新 `message_index.json`、thread manifest 和 `threads_index.json`。
- 副作用操作先写 `operations.jsonl`，再更新必要的运行摘要。
- 长期记忆写入后，先保存 memory JSON，再更新 `memory_index.json`。
- Secret 写入后，先保存加密 Secret JSON，再更新 `secrets_index.json`；更新失败时不能回滚已加密对象，后台按 Secret 对象重建索引。
- 模型调用前生成 memory snapshot，保存本次注入模型上下文的 memory_id 列表和摘要。
- 模型调用前生成 tool inventory snapshot，保存本次模型实际可见的 Tool schema 摘要和 hash。
- 用户删除或禁用记忆后，更新 memory JSON 和 `memory_index.json`，下一次 memory snapshot 不得包含该记忆。
- 索引更新失败时，不回滚已写入的权威文件；Runtime 记录错误，并允许后台重建索引。
- 前端左侧会话列表优先读 `threads_index.json`；打开 thread 时再读取 thread manifest、message_index、messages 和必要的 run leaf_state。
- 前端文档、运行、Job、记忆列表优先读索引文件；打开详情时再读取 manifest、events、operations、Job leaf_state 或 memory 原文。

## manifest.json 是什么

`manifest.json` 是文档当前版本的权威状态文件，记录一个文档从上传、解析、切片、向量化、图谱入库到失败恢复的状态。同一逻辑文档更新时，`doc_id` 不变，新增 `doc_version_id`。

示例：

```json
{
  "doc_id": "doc_001",
  "doc_version_id": "docv_001",
  "version_no": 1,
  "workspace_id": "default",
  "knowledge_base_id": "kb_default",
  "file_name": "example.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 123456,
  "file_hash": "sha256...",
  "original_object_key": "workspaces/default/documents/doc_001/original/example.pdf",
  "parsed_text_object_key": "workspaces/default/documents/doc_001/parsed/text.json",
  "chunks_object_key": "workspaces/default/documents/doc_001/chunks/chunks.json",
  "versions_object_key": "workspaces/default/documents/doc_001/versions.json",
  "document_representation_object_key": "workspaces/default/documents/doc_001/parsed/document.json",
  "parse_status": "parsed",
  "chunk_status": "chunked",
  "embedding_status": "indexed",
  "graph_status": "indexed",
  "embedding_model": "text-embedding-v4",
  "embedding_dim": 1024,
  "chunk_count": 128,
  "created_at": "2026-05-28T00:00:00+08:00",
  "updated_at": "2026-05-28T00:00:00+08:00"
}
```

## versions.json 是什么

`versions.json` 记录同一逻辑文档的版本历史。默认查询只使用 `current_doc_version_id`，旧版本可保留、软删除或按生命周期策略清理。

```json
{
  "doc_id": "doc_001",
  "current_doc_version_id": "docv_002",
  "versions": [
    {
      "doc_version_id": "docv_001",
      "version_no": 1,
      "status": "archived",
      "manifest_object_key": "workspaces/default/documents/doc_001/versions/docv_001/manifest.json",
      "created_at": "2026-05-28T00:00:00+08:00"
    },
    {
      "doc_version_id": "docv_002",
      "version_no": 2,
      "status": "current",
      "manifest_object_key": "workspaces/default/documents/doc_001/manifest.json",
      "created_at": "2026-05-29T00:00:00+08:00"
    }
  ]
}
```

状态枚举：

```text
uploaded
parsing
parsed
chunking
chunked
embedding
indexed
graph_extracting
graph_indexed
failed
deleted
```

## chunks.json 是什么

`chunks.json` 是一个文档所有 chunk 的索引清单，用于快速知道该文档切出了哪些片段、片段位置、片段对象路径和入库状态。

示例：

```json
{
  "doc_id": "doc_001",
  "workspace_id": "default",
  "knowledge_base_id": "kb_default",
  "chunk_count": 2,
  "chunks": [
    {
      "chunk_id": "doc_001_chunk_0001",
      "chunk_index": 1,
      "page": 1,
      "token_count": 380,
      "object_key": "workspaces/default/documents/doc_001/chunks/chunk-0001.json",
      "embedding_status": "indexed",
      "graph_status": "indexed"
    },
    {
      "chunk_id": "doc_001_chunk_0002",
      "chunk_index": 2,
      "page": 2,
      "token_count": 420,
      "object_key": "workspaces/default/documents/doc_001/chunks/chunk-0002.json",
      "embedding_status": "indexed",
      "graph_status": "pending"
    }
  ]
}
```

## 单个 chunk JSON

单个 chunk 保存正文、来源、向量入库状态、图谱入库状态。

```json
{
  "chunk_id": "doc_001_chunk_0003",
  "doc_id": "doc_001",
  "doc_version_id": "docv_001",
  "workspace_id": "default",
  "knowledge_base_id": "kb_default",
  "chunk_index": 3,
  "page": 12,
  "text": "这里是 chunk 正文。",
  "token_count": 412,
  "source": {
    "file_name": "example.pdf",
    "object_key": "workspaces/default/documents/doc_001/original/example.pdf",
    "page_start": 12,
    "page_end": 12,
    "char_start": 1024,
    "char_end": 1488,
    "section_path": ["第一章", "第二节"]
  },
  "embedding": {
    "provider": "openai_compatible",
    "model": "text-embedding-v4",
    "dimension": 1024,
    "status": "indexed",
    "milvus_collection": "kb_default_text_embedding_v4_1024"
  },
  "graph": {
    "status": "indexed",
    "mentioned_entities": ["entity_001", "entity_002"]
  }
}
```

## events/part-*.jsonl 是什么

`events/part-*.jsonl` 是一次运行的分段事件流落盘文件。JSONL 表示一行一个 JSON 对象，适合按时间顺序回放、断点恢复和分页查询。

注意：MinIO 是对象存储，不是本地文件系统。P0 不依赖单个可无限追加的 `events.jsonl`，而是直接使用分段对象，例如 `events/part-000001.jsonl`、`events/part-000002.jsonl`。封存后的分段不可修改，活跃分段在小尺寸内可以重写；每个分段的范围写入 `event_index.json`。

典型事件：

```json
{"schema_version":1,"event_seq":1,"event_id":"evt_run_001_000000000001","created_at":"2026-05-28T11:10:00+08:00","trace_id":"trace_001","type":"run_started","run_id":"run_001"}
{"schema_version":1,"event_seq":2,"event_id":"evt_run_001_000000000002","created_at":"2026-05-28T11:10:01+08:00","trace_id":"trace_001","type":"assistant_delta","text_delta":"你好"}
{"schema_version":1,"event_seq":3,"event_id":"evt_run_001_000000000003","created_at":"2026-05-28T11:10:03+08:00","trace_id":"trace_001","type":"tool_call_started","tool_call_id":"call_001","tool_name":"vector_search"}
```

用途：

- 前端断线后恢复最近事件。
- 排查一次 Agent 运行发生了什么。
- 生成用户可读的任务时间线。
- 作为 LangGraph checkpoint 之外的审计记录。

## operations.jsonl 是什么

`operations.jsonl` 记录副作用操作，重点用于恢复、回滚和补偿。

`operations.jsonl` 的写入频率通常低于 token 事件，但仍按逻辑追加处理。涉及副作用的操作必须优先保证 `operation_id` 和状态记录落盘，再更新运行摘要索引。

示例：

```json
{
  "timestamp": "2026-05-28T11:20:00+08:00",
  "trace_id": "trace_001",
  "operation_id": "op_001",
  "tool_call_id": "call_010",
  "tool_name": "write_file",
  "side_effect": true,
  "reversible": true,
  "rollback_strategy": "file_backup",
  "rollback_token": "backup_001",
  "status": "committed"
}
```

`operations.jsonl` 不等同于普通事件日志。它只关心有副作用、需要审计或可能需要恢复的操作。

## 写入规则

- 写入前生成稳定 `doc_id`、`chunk_id`、`trace_id`、`operation_id`。
- 原始文件保存后先写 `manifest.json`。
- 每一步处理完成后更新 manifest 状态。
- chunk 正文优先在 MinIO 保存，Milvus 只保存检索字段和回源路径。
- JSONL 逻辑追加时必须保证单行 JSON 完整。
- 高频事件不要长期依赖重写单个大对象，应使用分段 JSONL 或运行时缓冲。
- 失败时写入 `errors.jsonl`，并在 manifest 中记录失败阶段和错误摘要。

## 不写入的内容

- API key。
- 数据库密码。
- `AGENT_MASTER_KEY`。
- Secret 明文。
- Secret 密文、nonce、tag。
- 完整用户隐私数据。
- 完整原始 headers。
- 完整 embedding 向量。
- access token。
- refresh token。
