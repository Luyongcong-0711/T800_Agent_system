# Agent 系统数据库与存储架构设计文档

## 1. 总体目标

本系统的数据底座支持 RAG、GraphRAG、Agent 工具调用、长期记忆、会话恢复、审计回放和知识库管理。

当前数据层固定为：

```text
MinIO + Milvus + Neo4j
```

明确不引入：

```text
pgsql
PostgreSQL
MySQL
独立 Catalog 数据库
```

原本关系型 Catalog 可能承担的文档状态、版本状态、chunk 索引、run 事件索引、会话快照、任务列表和记忆列表，全部改为 MinIO 中的显式 JSON / JSONL / index 对象。

核心原则：

```text
MinIO 管“原文、产物、状态、事件、索引和可恢复快照”
Milvus 管“语义向量召回”
Neo4j 管“实体关系、多跳路径和 GraphRAG provenance”
Agent Runtime 管“写入顺序、恢复、权限、工具治理和失败策略”
Redis 只管“可重建热缓存”
```

## 2. 总体架构

```text
用户 / 前端 / CLI / 桌面端
        ↓
Agent Server
  - REST 控制接口
  - SSE 事件流
  - 上传、配置、审批、取消
        ↓
Agent Runtime
  - LangGraph Workflow
  - Tool Executor
  - EventStoreWriter
  - IndexRebuilder
  - Memory Manager
  - RAG / GraphRAG Pipeline
        ↓
┌────────────────────────────────────┐
│ MinIO                               │
│ - 原始文件                           │
│ - Document Representation            │
│ - chunks.json / chunk JSON           │
│ - run manifest / events / indexes    │
│ - operations / errors / artifacts    │
│ - long-term memory canonical JSON    │
└────────────────────────────────────┘
        ↓
┌───────────────┬────────────────────┐
│ Milvus         │ Neo4j              │
│ 向量索引        │ 实体关系和多跳图谱   │
└───────────────┴────────────────────┘
```

## 3. MinIO 职责

MinIO 不只是原始文件仓库，也是第一版的状态账本和轻量索引存储。

MinIO 保存：

- 原始文件。
- 解析后的 Document Representation。
- chunk 清单和单个 chunk JSON。
- 文档 manifest 和版本历史。
- 知识库 active embedding 配置。
- run manifest、事件分段、事件索引、leaf_state。
- 副作用 operations 日志。
- 工具调用结果、错误日志、产物。
- 长期记忆 canonical JSON。
- memory snapshot。
- compaction summary。

MinIO 不负责：

- 向量相似度检索。
- 图关系遍历。
- 高并发事务。
- 任意复杂条件查询。
- 模型直接访问数据库凭证。

## 4. Object Key 设计

推荐统一 bucket：

```text
agent-files
```

核心路径：

```text
workspaces/{workspace_id}/indexes/workspace_index.json
workspaces/{workspace_id}/indexes/documents_index.json
workspaces/{workspace_id}/indexes/runs_index.json
workspaces/{workspace_id}/indexes/memory_index.json

workspaces/{workspace_id}/knowledge_bases/{knowledge_base_id}/manifest.json
workspaces/{workspace_id}/knowledge_bases/{knowledge_base_id}/active_embedding.json

workspaces/{workspace_id}/documents/{doc_id}/original/{file_name}
workspaces/{workspace_id}/documents/{doc_id}/manifest.json
workspaces/{workspace_id}/documents/{doc_id}/versions.json
workspaces/{workspace_id}/documents/{doc_id}/parsed/document.json
workspaces/{workspace_id}/documents/{doc_id}/parsed/text.json
workspaces/{workspace_id}/documents/{doc_id}/chunks/chunks.json
workspaces/{workspace_id}/documents/{doc_id}/chunks/chunk-{chunk_index}.json
workspaces/{workspace_id}/documents/{doc_id}/entities/entities.json
workspaces/{workspace_id}/documents/{doc_id}/entities/entity_resolution_decisions.jsonl
workspaces/{workspace_id}/documents/{doc_id}/graph/relation_facts.jsonl
workspaces/{workspace_id}/documents/{doc_id}/graph/evidence.jsonl
workspaces/{workspace_id}/documents/{doc_id}/events/ingestion.jsonl

workspaces/{workspace_id}/sessions/{thread_id}/manifest.json
workspaces/{workspace_id}/sessions/{thread_id}/messages/part-000001.jsonl
workspaces/{workspace_id}/sessions/{thread_id}/memory_snapshots/{snapshot_id}.json
workspaces/{workspace_id}/sessions/{thread_id}/compactions/{compaction_id}.json

workspaces/{workspace_id}/runs/{run_id}/manifest.json
workspaces/{workspace_id}/runs/{run_id}/events/part-000001.jsonl
workspaces/{workspace_id}/runs/{run_id}/event_index.json
workspaces/{workspace_id}/runs/{run_id}/leaf_state.json
workspaces/{workspace_id}/runs/{run_id}/operations.jsonl
workspaces/{workspace_id}/runs/{run_id}/tool_calls.jsonl
workspaces/{workspace_id}/runs/{run_id}/errors.jsonl
workspaces/{workspace_id}/runs/{run_id}/artifacts/{artifact_id}

workspaces/{workspace_id}/memory/{memory_id}.json
```

## 5. ID 设计

使用业务前缀 + ULID / UUIDv7 / 稳定 hash。

```text
workspace_id       = ws_01J...
knowledge_base_id = kb_01J...
doc_id            = doc_01J...
doc_version_id    = docv_01J...
chunk_id          = chk_01J...
mention_id        = men_01J...
entity_id         = ent_01J...
fact_id           = fact_01J...
evidence_id       = ev_01J...
thread_id         = thread_01J...
run_id            = run_01J...
event_id          = evt_{run_id}_{event_seq_12位补零}
message_id        = msg_01J...
tool_call_id      = call_01J...
operation_id      = op_01J...
approval_id       = appr_01J...
memory_id         = mem_01J...
compaction_id     = cmp_01J...
```

稳定 ID 规则：

```text
chunk_id = chk_ + hash(doc_version_id + chunk_index + chunk_text_sha256)
mention_id = men_ + hash(chunk_id + start_offset + end_offset + surface)
fact_id = fact_ + hash(subject_entity_id + predicate + object_entity_id + scope_id)
```

`event_seq` 是 run 内递增整数，从 1 开始。`event_id` 必须可从字符串解析出 run 和 sequence。

## 6. 文档状态对象

### documents/{doc_id}/manifest.json

```json
{
  "schema_version": 1,
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
  "document_representation_object_key": "workspaces/default/documents/doc_001/parsed/document.json",
  "parsed_text_object_key": "workspaces/default/documents/doc_001/parsed/text.json",
  "chunks_object_key": "workspaces/default/documents/doc_001/chunks/chunks.json",
  "versions_object_key": "workspaces/default/documents/doc_001/versions.json",
  "parse_status": "parsed",
  "chunk_status": "chunked",
  "embedding_status": "indexed",
  "graph_status": "indexed",
  "embedding_provider": "openai_compatible",
  "embedding_model": "text-embedding-v4",
  "embedding_dim": 1024,
  "milvus_collection": "kb_default_text_embedding_v4_1024",
  "chunk_count": 128,
  "revision": 5,
  "created_at": "2026-05-29T12:00:00+08:00",
  "updated_at": "2026-05-29T12:05:00+08:00"
}
```

### documents/{doc_id}/versions.json

```json
{
  "schema_version": 1,
  "doc_id": "doc_001",
  "current_doc_version_id": "docv_002",
  "versions": [
    {
      "doc_version_id": "docv_001",
      "version_no": 1,
      "status": "archived",
      "manifest_object_key": "workspaces/default/documents/doc_001/versions/docv_001/manifest.json",
      "created_at": "2026-05-28T10:00:00+08:00"
    },
    {
      "doc_version_id": "docv_002",
      "version_no": 2,
      "status": "current",
      "manifest_object_key": "workspaces/default/documents/doc_001/manifest.json",
      "created_at": "2026-05-29T10:00:00+08:00"
    }
  ],
  "revision": 2,
  "updated_at": "2026-05-29T10:00:00+08:00"
}
```

### chunks/chunks.json

```json
{
  "schema_version": 1,
  "doc_id": "doc_001",
  "doc_version_id": "docv_001",
  "workspace_id": "default",
  "knowledge_base_id": "kb_default",
  "chunk_count": 2,
  "chunks": [
    {
      "chunk_id": "chk_001",
      "chunk_index": 1,
      "parent_chunk_id": null,
      "section_title": "第二条 合同金额",
      "section_path": ["采购合同", "第二条 合同金额"],
      "page_start": 2,
      "page_end": 2,
      "char_start": 1024,
      "char_end": 1098,
      "token_count": 420,
      "object_key": "workspaces/default/documents/doc_001/chunks/chunk-0001.json",
      "embedding_status": "indexed",
      "graph_status": "indexed"
    }
  ],
  "revision": 1,
  "updated_at": "2026-05-29T12:00:00+08:00"
}
```

## 7. Run 状态对象

### runs/{run_id}/manifest.json

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "thread_id": "thread_001",
  "run_id": "run_001",
  "parent_run_id": null,
  "status": "running",
  "title": "一次 Agent 运行",
  "owner": {
    "runtime_instance_id": "rt_001",
    "fencing_token": "fence_001",
    "acquired_at": "2026-05-29T12:00:00+08:00",
    "expires_at": "2026-05-29T12:05:00+08:00"
  },
  "object_keys": {
    "event_index": "workspaces/default/runs/run_001/event_index.json",
    "leaf_state": "workspaces/default/runs/run_001/leaf_state.json",
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

### runs/{run_id}/events/part-*.jsonl

```json
{"schema_version":1,"event_seq":1,"event_id":"evt_run_001_000000000001","run_id":"run_001","thread_id":"thread_001","type":"run_started","created_at":"2026-05-29T12:00:00+08:00"}
{"schema_version":1,"event_seq":2,"event_id":"evt_run_001_000000000002","run_id":"run_001","thread_id":"thread_001","type":"user_message","message_id":"msg_001","role":"user","content":"分析这个知识库","created_at":"2026-05-29T12:00:01+08:00"}
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
model_config_changed
error_recorded
```

### runs/{run_id}/event_index.json

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
  "updated_at": "2026-05-29T12:03:00+08:00"
}
```

### runs/{run_id}/leaf_state.json

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
  "revision": 11,
  "updated_at": "2026-05-29T12:02:00+08:00"
}
```

## 8. 显式索引对象

### workspace_index.json

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "documents_index_object_key": "workspaces/default/indexes/documents_index.json",
  "runs_index_object_key": "workspaces/default/indexes/runs_index.json",
  "memory_index_object_key": "workspaces/default/indexes/memory_index.json",
  "revision": 1,
  "updated_at": "2026-05-29T12:00:00+08:00"
}
```

### documents_index.json

只保存列表摘要，不保存完整正文。

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "documents": [
    {
      "doc_id": "doc_001",
      "current_doc_version_id": "docv_001",
      "knowledge_base_id": "kb_default",
      "file_name": "example.pdf",
      "mime_type": "application/pdf",
      "manifest_object_key": "workspaces/default/documents/doc_001/manifest.json",
      "parse_status": "parsed",
      "embedding_status": "indexed",
      "graph_status": "indexed",
      "chunk_count": 128,
      "updated_at": "2026-05-29T12:00:00+08:00"
    }
  ],
  "revision": 3,
  "updated_at": "2026-05-29T12:00:00+08:00"
}
```

### runs_index.json

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "runs": [
    {
      "run_id": "run_001",
      "thread_id": "thread_001",
      "status": "running",
      "title": "一次 Agent 运行",
      "manifest_object_key": "workspaces/default/runs/run_001/manifest.json",
      "event_index_object_key": "workspaces/default/runs/run_001/event_index.json",
      "leaf_state_object_key": "workspaces/default/runs/run_001/leaf_state.json",
      "last_event_id": "evt_run_001_000000000120",
      "started_at": "2026-05-29T12:00:00+08:00",
      "updated_at": "2026-05-29T12:03:00+08:00"
    }
  ],
  "revision": 7,
  "updated_at": "2026-05-29T12:03:00+08:00"
}
```

### memory_index.json

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "memories": [
    {
      "memory_id": "mem_001",
      "type": "user_preference",
      "summary": "用户偏好中文回复。",
      "content_object_key": "workspaces/default/memory/mem_001.json",
      "frontend_visible": true,
      "enabled_for_model_context": true,
      "updated_at": "2026-05-29T12:00:00+08:00"
    }
  ],
  "revision": 2,
  "updated_at": "2026-05-29T12:00:00+08:00"
}
```

## 9. 写入流程

### 新增文档

```text
1. API 鉴权。
2. 生成 doc_id / doc_version_id / job_id。
3. 计算文件 sha256。
4. 写原始文件到 MinIO。
5. 写 documents/{doc_id}/manifest.json，状态 uploaded。
6. 写 versions.json。
7. 更新 documents_index.json。
8. Ingestion Worker 读取 manifest。
9. Parser Router 生成 Document Representation。
10. 写 parsed/document.json 和 parsed/text.json。
11. 更新 manifest parse_status。
12. Chunker 生成 chunks.json 和 chunk JSON。
13. 更新 manifest chunk_status。
14. Embedding 写 Milvus active collection。
15. 更新 manifest embedding_status。
16. KG pipeline 抽取实体关系并写 Neo4j。
17. 写 entity_resolution_decisions.jsonl、relation_facts.jsonl、evidence.jsonl。
18. 更新 manifest graph_status。
19. 更新 documents_index.json。
```

### 新增 run

```text
1. POST /runs 创建 run_id。
2. 写 runs/{run_id}/manifest.json，状态 created/running。
3. 初始化 event_index.json。
4. 初始化 leaf_state.json。
5. 更新 runs_index.json。
6. EventStoreWriter 写 run_started。
7. SSE 开始推送事件。
```

### 写 run 事件

```text
1. EventStoreWriter 生成 event_seq / event_id。
2. 写入当前 events/part-*.jsonl。
3. 更新 event_index.json。
4. 重放或增量更新 leaf_state.json。
5. 如果状态变化，更新 runs_index.json。
6. 通过 SSE 发送事件。
```

高频 token：

- SSE 可以实时发 token。
- MinIO 按 250ms 或 512 字符聚合写 `assistant_delta`。
- 完成时写 `assistant_message`。

### 副作用操作

```text
1. 生成 operation_id 和 idempotency_key。
2. 写 operation_pending 事件。
3. 写 operations.jsonl status=pending。
4. 执行工具。
5. 成功写 operation_committed。
6. 失败写 operation_failed。
7. 网络断开或结果未知写 operation_unknown_outcome。
8. 需要回滚时写 rolled_back 或 compensated。
```

## 10. 并发写与一致性

### 单 run 写入器

同一个 run 同一时间只能有一个 EventStoreWriter。

```text
Tool / MCP / SubAgent
  -> Runtime event bus
  -> EventStoreWriter
  -> MinIO events + index + leaf_state
```

任何工具、MCP Connector、SubAgent 都不能直接重写 run 的 `event_index.json` 或 `leaf_state.json`。

### revision / etag

所有可重写索引对象都必须带：

```json
{
  "revision": 1,
  "updated_at": "2026-05-29T12:00:00+08:00"
}
```

更新流程：

```text
1. 读取对象 revision 和 etag。
2. 基于当前对象生成新 payload。
3. revision + 1。
4. 使用 If-Match previous etag 写回。
5. 如果 SDK 不支持 If-Match，写入前重新读取 revision 并比较。
6. 冲突时重新读取、合并、最多重试 3 次。
7. 仍失败时写错误事件，等待 IndexRebuilder 修复。
```

### run ownership / fencing

多 Runtime 实例时用 `runs/{run_id}/manifest.json.owner` 控制接管。

规则：

- run 启动时写入 `runtime_instance_id` 和 `fencing_token`。
- EventStoreWriter 每 30 秒刷新 lease。
- lease 未过期时，其他 Runtime 不得接管。
- lease 过期后，新 Runtime 可进入 recovering 并写入新 fencing token。
- 旧 Runtime 发现 fencing token 失效必须停止写入。

### SubAgent 并发

- SubAgent 不直接写最终 run 状态。
- SubAgent 输出先回主 Agent。
- 写入型 SubAgent 必须声明 `write_scope`。
- `write_scope` 不能重叠。
- 无法证明不重叠时串行执行。

## 11. 索引重建

`IndexRebuilder` 不依赖额外数据库。

可重建对象：

| 索引对象 | 重建来源 |
| --- | --- |
| `event_index.json` | `events/part-*.jsonl` |
| `leaf_state.json` | `events/part-*.jsonl` + `operations.jsonl` |
| `runs_index.json` | `runs/*/manifest.json` + `leaf_state.json` |
| `documents_index.json` | `documents/*/manifest.json` + `versions.json` |
| `memory_index.json` | `memory/*.json` |

run 索引重建流程：

```text
1. list runs/{run_id}/events/part-*.jsonl。
2. 按 segment_no 排序。
3. 逐行解析 JSONL。
4. 校验 event_seq 连续性和 event_id 格式。
5. 跳过重复 event_id，并记录 duplicate_event_count。
6. 生成新的 event_index.json。
7. 从事件重放生成 leaf_state.json。
8. 更新 runs_index.json 中该 run 的摘要。
9. 写 index_rebuilt 审计事件。
```

文档索引重建流程：

```text
1. list documents/*/manifest.json。
2. 读取 versions.json 和 chunks/chunks.json。
3. 生成 documents_index.json。
4. 校验 knowledge_base active_embedding 与 manifest 中 embedding 字段一致。
```

## 12. 断线重连和历史分页

SSE 使用：

```text
GET /runs/{run_id}/events/stream
```

历史分页使用：

```text
GET /runs/{run_id}/events?after_event_id=evt_run_001_000000000120&limit=200
```

断线恢复：

```text
1. 前端保存 last_event_id。
2. 重连时发送 Last-Event-ID 或 after_event_id。
3. Server 读取 event_index.json。
4. 找到对应 events/part-*.jsonl。
5. 补发 event_seq 更大的事件。
6. run 仍 active 时继续挂实时流。
7. run 已完成时补齐后关闭。
```

前端去重：

- 用 `event_id` 去重。
- 发现 `event_seq` 跳号时调用 REST 历史分页补齐。
- 只在事件渲染成功后更新本地 `last_event_id`。

## 13. Milvus 职责

Milvus 只存向量检索需要的数据。

字段：

```text
chunk_id
workspace_id
knowledge_base_id
doc_id
doc_version_id
chunk_index
embedding
object_key
metadata
```

查询必须过滤：

```text
workspace_id
knowledge_base_id
active collection
permission scope
```

更换 embedding 模型或维度：

```text
1. 创建新的 Milvus collection。
2. 重新 embedding 并重新入库。
3. 校验完成后更新 knowledge_bases/{kb_id}/active_embedding.json。
4. 普通检索只查 active collection。
5. 旧 collection 只读保留用于回滚和审计。
```

## 14. Neo4j 职责

Neo4j 保存图谱和 provenance：

```text
Document
DocumentVersion
Chunk
Mention
Entity
RelationFact
Evidence
```

推荐结构：

```text
(:Document)-[:HAS_VERSION]->(:DocumentVersion)
(:DocumentVersion)-[:HAS_CHUNK]->(:Chunk)
(:Chunk)-[:HAS_MENTION]->(:Mention)
(:Mention)-[:REFERS_TO]->(:Entity)
(:Entity)-[:RELATION_SUBJECT]->(:RelationFact)
(:RelationFact)-[:RELATION_OBJECT]->(:Entity)
(:RelationFact)-[:SUPPORTED_BY]->(:Evidence)
(:Evidence)-[:FROM_CHUNK]->(:Chunk)
```

Neo4j 不保存原始全文，不替代 MinIO 索引，不保存 run 事件。

## 15. 查询流程

### 普通 RAG

```text
1. 用户问题进入 Agent Runtime。
2. 生成 query embedding。
3. 读取 knowledge_bases/{kb_id}/active_embedding.json。
4. 查询 Milvus active collection。
5. 返回 chunk_id、doc_id、object_key、score。
6. 从 MinIO chunk JSON 回源正文。
7. 组织证据给 LLM。
8. 写 run 事件和 ToolResult。
```

### GraphRAG

```text
1. 用户问题进入 Agent Runtime。
2. Milvus 召回 chunk 候选。
3. 从候选 chunk 映射到 Neo4j Chunk / Entity。
4. Neo4j 做 1 到 2 跳扩展。
5. 回源 MinIO evidence 文本。
6. 合并文本证据和图谱证据。
7. 生成答案并返回引用。
```

### 文档详情页

```text
1. 前端读 documents_index.json 展示列表。
2. 打开详情时读 documents/{doc_id}/manifest.json。
3. 需要版本历史时读 versions.json。
4. 需要 chunk 列表时读 chunks/chunks.json。
5. 需要原文片段时读 chunk JSON 或 parsed/document.json。
```

### run 日志页

```text
1. 前端读 runs_index.json 展示任务列表。
2. 打开详情时读 runs/{run_id}/leaf_state.json。
3. 历史事件分页读 event_index.json + events/part-*.jsonl。
4. 副作用详情读 operations.jsonl。
5. 工具详情读 tool_calls.jsonl 或 artifact object。
```

## 16. 失败降级

| 失败点 | 策略 |
| --- | --- |
| MinIO 读失败 | 可重试；仍失败则返回证据不可用 |
| MinIO 索引更新失败 | 不回滚权威对象；写错误事件；后台重建索引 |
| event_index 损坏 | 扫描 events/part-*.jsonl 重建 |
| leaf_state 缺失 | 从事件重放生成 |
| operations pending 超时 | 标记 unknown_outcome，要求查询状态或人工确认 |
| Milvus 检索失败 | 尝试 Neo4j 精确实体扩展；答案标明缺少向量召回 |
| Neo4j 查询失败 | 使用 Milvus 文本证据回答 |
| Embedding API 失败 | 保留 chunk JSON，manifest 标记 embedding failed |

## 17. 开发模块

P0 需要实现：

```text
MinIOConnector
DocumentStore
RunStore
EventStoreWriter
EventReader
IndexRebuilder
OperationLogStore
MemoryStore
KnowledgeBaseStore
MilvusConnector
Neo4jConnector
DatabaseHealthService
```

核心接口：

```python
class EventStoreWriter:
    def append_event(self, run_id: str, event: dict) -> dict: ...
    def flush_delta(self, run_id: str) -> None: ...
    def seal_segment(self, run_id: str) -> None: ...

class EventReader:
    def list_events(self, run_id: str, after_event_id: str | None, limit: int) -> list[dict]: ...
    def get_leaf_state(self, run_id: str) -> dict: ...

class IndexRebuilder:
    def rebuild_run(self, run_id: str) -> dict: ...
    def rebuild_workspace_indexes(self, workspace_id: str) -> dict: ...
```

## 18. 最重要的原则

```text
1. 不引入 pgsql，MinIO 显式索引承担 Catalog 职责。
2. MinIO 的索引对象必须可从权威对象重建。
3. 事件必须有 event_seq 和 event_id。
4. SSE 断线恢复必须基于 event_index 和 Last-Event-ID。
5. 同一个 run 只能有一个 EventStoreWriter。
6. 所有副作用操作必须有 operation_id 和 idempotency_key。
7. Milvus 不保存业务主状态。
8. Neo4j 不保存 run/session 状态。
9. Redis 不保存权威状态，不做锁，不做队列。
10. 索引写失败不是数据丢失，必须通过 IndexRebuilder 恢复。
```
