# 数据层：MinIO + Milvus + Neo4j

## 数据层限定

当前数据层限定为：

```text
MinIO + Milvus + Neo4j
```

三者职责：

| 组件 | 定位 | 保存内容 |
| --- | --- | --- |
| MinIO | 文件和产物主存储 | 原始文件、解析文本、chunk JSON、manifest、运行日志、系统运行日志归档、开发日志、工具日志、预览图、加密后的 Secret 对象 |
| Milvus | 主向量检索层 | chunk 向量、chunk_id、doc_id、object_key、少量过滤 metadata |
| Neo4j | 图谱和 GraphRAG 层 | Document、Chunk、Entity、Person、Event、Place、Concept、Object 及关系 |

核心原则：

```text
MinIO 保存可回源的数据。
MinIO 保存加密后的 Secret 对象，但不保存 AGENT_MASTER_KEY。
Milvus 负责相似度检索。
Neo4j 负责关系表达和多跳扩展。
embedding、chunk、rerank、权限、工具治理都在 Agent Runtime 层完成。
```

## 第一版状态与索引方案

第一版确认不引入 pgsql、PostgreSQL、MySQL 或其他 Catalog 数据库。运行状态、文档状态、后台 Job 状态、会话事件和索引关系全部落在 MinIO 的 JSON / JSONL 对象中。

MinIO 在本系统中承担两类职责：

```text
1. 权威对象存储：原始文件、解析结果、chunk、日志、产物、长期记忆原文。
2. 显式状态索引：session / run / job / document / chunk / memory 的 manifest、index、event log。
```

轻量索引文件不是新的数据库。索引可以从权威 JSON / JSONL 重建，主要用于避免前端为了展示列表、任务、历史事件而扫描大量 object prefix。

| 文件 | 定位 |
| --- | --- |
| `documents/{doc_id}/manifest.json` | 单个文档当前版本权威状态 |
| `documents/{doc_id}/versions.json` | 单个逻辑文档的版本历史 |
| `documents/{doc_id}/chunks/chunks.json` | 单个文档 chunk 权威索引 |
| `runs/{run_id}/manifest.json` | 单次 Agent run 的权威状态 |
| `runs/{run_id}/events/part-*.jsonl` | 单次运行的分段事件流 |
| `runs/{run_id}/event_index.json` | run 事件索引，支持分页、断线补偿和重放 |
| `runs/{run_id}/leaf_state.json` | run 当前可恢复快照 |
| `runs/{run_id}/operations.jsonl` | 副作用操作日志 |
| `jobs/{job_id}/manifest.json` | 后台 Job 权威状态 |
| `jobs/{job_id}/events/part-*.jsonl` | 后台 Job 分段事件流 |
| `jobs/{job_id}/event_index.json` | Job 事件索引，支持分页、断线补偿和重放 |
| `jobs/{job_id}/leaf_state.json` | Job 当前可恢复快照 |
| `jobs/{job_id}/errors.jsonl` | Job 错误记录 |
| `sessions/{thread_id}/manifest.json` | 前端可见对话窗口的权威状态 |
| `sessions/{thread_id}/messages/part-*.jsonl` | 对话持久化消息历史 |
| `sessions/{thread_id}/messages/message_index.json` | 对话消息分页索引 |
| `sessions/{thread_id}/runs_index.json` | 对话内 run 列表摘要 |
| `workspace_index.json` | 工作区索引入口 |
| `threads_index.json` | 左侧历史会话列表摘要 |
| `documents_index.json` | 文档列表摘要 |
| `runs_index.json` | 运行任务列表摘要 |
| `jobs_index.json` | 后台 Job 列表摘要 |
| `memory_index.json` | 长期记忆列表摘要 |
| `secrets/{secret_id}.json` | 加密后的密钥对象 |
| `secrets_index.json` | 脱敏密钥列表摘要 |
| `system/logs/{date}/{runtime_instance_id}` | 系统运行日志 MinIO 归档 |
| `development/tasks/{dev_task_id}` | Codex/SubAgent 开发日志 |

索引职责替代原本可能由 pgsql 承担的 session/event/document/chunk 关系索引，但不改变数据层限定：

```text
MinIO + Milvus + Neo4j
```

Redis 只缓存热索引和热查询结果，不保存权威状态，不做锁，不做队列。

## Runtime 状态写入原则

- 所有 run 事件必须由 Runtime 的 `EventStoreWriter` 统一写入，工具、SubAgent、MCP Connector 不直接改 MinIO 状态文件。
- 所有 Job 事件必须由 `JobEventWriter` 统一写入，Worker、RAG Pipeline、Graph Pipeline、MCP Connector 不直接改 Job 状态文件。
- 每个事件必须有递增 `event_seq` 和幂等 `event_id`。
- `event_id` 生成规则：`evt_{run_id}_{event_seq_12位补零}`。
- `operation_id`、`tool_call_id`、`message_id`、`approval_id` 独立生成，不复用 `event_id`。
- 写入顺序必须是：权威事件或产物对象先落盘，再更新索引对象。
- 索引对象更新失败时，不回滚已经落盘的权威对象；后台重建索引。
- 高频 token 不逐 token 落 MinIO，Runtime 按 250ms 或 512 字符聚合成 `assistant_delta` 事件，最终再写完整 assistant message。
- SSE 可以实时发送更细粒度 token，但恢复重放只依赖已经持久化的事件。
- 任何有副作用的工具必须先写 `operation_pending`，成功后写 `operation_committed`，失败后写 `operation_failed` 或 `operation_unknown_outcome`。
- SubAgent 并发输出必须回到主 run 的 EventStoreWriter 统一排序后写入，不能让多个 SubAgent 并发重写同一个事件索引。

## MinIO 索引一致性原则

MinIO 单个对象 PUT 是原子的，但 MinIO 不是事务数据库，所以 P0 按以下规则保证可恢复一致性：

- `events/part-*.jsonl` 分段对象写入成功后，才能更新 `event_index.json`。
- `manifest.json`、`leaf_state.json`、`threads_index.json`、`runs_index.json`、`message_index.json` 更新都带 `revision` 字段，每次更新递增。
- Runtime 读取索引时记录对象 `etag` 或 `revision`，写回时执行乐观并发检查。
- 如果 SDK 支持 conditional put，则使用 `If-Match: previous_etag`；如果不可用，则写入前重新读取、比较 `revision`、合并后重试。
- 同一个 `run_id` 在同一时间只能有一个 EventStoreWriter；多进程部署时由 run ownership/fencing token 控制，fencing token 写入 `runs/{run_id}/manifest.json`。
- 同一个 `job_id` 在同一时间只能有一个 Job Worker 持有 owner；多进程部署时由 job ownership/fencing token 控制，fencing token 写入 `jobs/{job_id}/manifest.json`。
- 后台 `IndexRebuilder` 可以从 `manifest.json`、`messages/part-*.jsonl`、`events/part-*.jsonl`、`operations.jsonl`、`memory/*.json`、`jobs/*/manifest.json` 和 `jobs/*/leaf_state.json` 重建索引。
- 索引文件只做快速入口，不是唯一真相；权威真相是 manifest、messages、events、operations、chunk JSON 和 memory JSON。

## 数据流

入库流：

```text
原始文件
  -> MinIO original
  -> 文档解析
  -> MinIO parsed/text.json
  -> 切 chunk
  -> MinIO chunks.json / chunk-{n}.json
  -> embedding
  -> Milvus vector + metadata
  -> 实体抽取
  -> Neo4j Document / Chunk / Entity / Relationship
```

查询流：

```text
用户问题
  -> query embedding
  -> Milvus 搜索 chunk 候选
  -> MinIO 回源 chunk 正文
  -> Neo4j 扩展实体和路径
  -> 可选 rerank
  -> LLM 生成答案
```

## ID 体系

| ID | 说明 |
| --- | --- |
| workspace_id | 工作区隔离 |
| knowledge_base_id | 知识库隔离 |
| doc_id | 文档唯一标识 |
| doc_version_id | 文档版本唯一标识，同一逻辑文档更新时 doc_id 不变，新增 doc_version_id |
| chunk_id | 文档片段唯一标识 |
| entity_id | 图谱实体唯一标识 |
| mention_id | 原文中某一次实体提及 |
| fact_id | 归一化后的关系事实 |
| evidence_id | 支撑实体或关系的证据 |
| job_id | 后台 Job 唯一标识，例如入库、embedding 重建、图谱构建、日志归档 |
| run_id | 一次 Agent 运行唯一标识 |
| trace_id | 跨工具、跨日志追踪 |
| tool_call_id | 单次工具调用标识 |
| operation_id | 有副作用操作标识 |

ID 建议使用业务前缀 + ULID / UUIDv7 / hash：

```text
knowledge_base_id = kb_01J...
doc_id            = doc_01J...
doc_version_id    = docv_01J...
chunk_id          = chk_01J...
mention_id        = men_01J...
entity_id         = ent_01J...
fact_id           = fact_01J...
evidence_id       = ev_01J...
job_id            = job_01J...
```

稳定 ID 规则：

```text
chunk_id = chk_ + hash(doc_version_id + chunk_index + chunk_text_sha256)
mention_id = men_ + hash(chunk_id + start_offset + end_offset + surface)
fact_id = fact_ + hash(subject_entity_id + predicate + object_entity_id + scope_id)
```

如果实体归一化需要人工调整，`entity_id` 优先使用随机稳定 ID，再通过 aliases、merge 记录和 resolution decision 管理归并。

## MinIO Catalog 元数据设计

这里的 Catalog 不是独立数据库，而是指“文档、版本、任务、chunk、对象路径的主账本能力”。当前项目不引入额外数据库，因此第一版把 Catalog 能力映射到 MinIO JSON / JSONL 和轻量索引文件。

| Catalog 概念 | 当前落地位置 |
| --- | --- |
| knowledge_base / dataset | `workspace_index.json` 和知识库配置 JSON |
| document | `documents_index.json` 中的文档摘要 |
| document_version | `manifest.json` 中的当前版本信息和 `versions.json` 中的历史版本 |
| chunk metadata | `chunks.json` 和单个 `chunk-{n}.json` |
| ingestion job | `jobs_index.json`、`jobs/{job_id}/manifest.json`、`jobs/{job_id}/event_index.json`、`jobs/{job_id}/leaf_state.json` 和 `jobs/{job_id}/events/part-*.jsonl` |
| entity resolution decision | `entity_resolution_decisions.jsonl` |
| object uri / key | 各 JSON 文件中的 MinIO object_key 字段 |

这相当于保留 Catalog 的职责，不保留独立 Catalog 数据库。

命名决策：

- `knowledge_base_id` 是最终统一命名。
- `dataset_id` 只作为外部材料同义词参考，不进入最终字段命名。
- 所有对象路径、索引文件、Milvus metadata、Neo4j 查询范围都继续使用 `knowledge_base_id`。

## 数据一致性

- MinIO 是原始文件和可回源产物的来源。
- 第一版中文档状态、运行状态、操作记录也以 MinIO JSON / JSONL 保存。
- Milvus 只保存检索必要字段，正文从 MinIO 读取。
- Neo4j 保存关系结构和必要摘要，不保存完整原始文件。
- `doc_id`、`chunk_id` 必须在 MinIO、Milvus、Neo4j 中一致。
- `doc_version_id` 必须进入 manifest、chunks、Milvus metadata、Neo4j DocumentVersion 节点。
- embedding 模型和维度必须写入 manifest、chunk metadata、Milvus collection 命名或字段。
- 更换 embedding 模型或维度时创建新 collection，不混写旧 collection。
- P0 一个知识库只允许一个 active embedding 版本，普通检索只查询 active collection。
- 旧 embedding collection 可以保留只读用于回滚和审计。

## 权限与隔离

- 所有查询必须带 `workspace_id`。
- 知识库检索必须限制 `knowledge_base_id`。
- 数据库工具不得允许模型传入任意连接串。
- 配置页面保存连接信息和 `secret_ref`，Runtime 通过 connector 和内部 SecretResolver 使用。
- 日志必须脱敏，不记录完整密钥、token、数据库密码、密文、nonce、tag、完整原始 prompt、完整原始 stdout/stderr 和完整 embedding 向量。

## 关联文档

- [03_MinIO文件与产物存储设计](./03_MinIO文件与产物存储设计.md)
- [04_Milvus向量检索设计](./04_Milvus向量检索设计.md)
- [05_Neo4j_GraphRAG设计](./05_Neo4j_GraphRAG设计.md)
- [21_数据库接口规范与数据库工具设计](./21_数据库接口规范与数据库工具设计.md)
- [31_密钥与SecretStore设计](./31_密钥与SecretStore设计.md)
- [34_后台任务Job调度与恢复设计](./34_后台任务Job调度与恢复设计.md)
