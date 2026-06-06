# Redis 缓存设计

## 定位

Redis 第一版只承担可重建缓存职责，不作为主状态存储，不作为锁，不作为队列。

Redis 缓存丢失后，系统必须可以从 MCP Server、MinIO、Milvus、Neo4j 或运行时健康检查重新生成对应数据。

第一版不使用：

```text
Redis 分布式锁
Redis 队列
Redis 作为任务状态主存储
```

P0 默认通过 docker compose 启动本地 Redis。数据库配置页面允许切换远程 Redis，但远程 Redis 仍只承担可重建缓存职责，不改变“不做锁、不做队列、不做主状态”的边界。

## P0 缓存内容

| 内容 | 说明 |
| --- | --- |
| MCP tool list | 避免频繁 tools/list |
| capability snapshot | MCP 能力快照 |
| query embedding | 相同问题减少 embedding 调用 |
| 数据库连接健康状态 | 缓存 MinIO / Milvus / Neo4j / Redis 的短周期健康检查结果 |
| Milvus 检索结果 | 缓存热查询的向量召回候选，降低重复检索成本 |

## P0 暂不缓存内容

| 内容 | 原因 |
| --- | --- |
| Neo4j 图谱路径 | 路径受关系版本、跳数、关系 allowlist 和权限影响，第一版直接查询更稳 |
| rerank 结果 | 复用条件复杂，rerank 默认关闭，失败也不阻断回答 |
| approval 状态 | 审批属于运行状态，第一版由 Runtime / MinIO 事件提供权威记录 |
| 工具执行进度 | 工具进度属于运行状态，第一版由 SSE 事件和 MinIO 日志提供权威记录 |
| Job 状态和进度 | Job 权威状态在 MinIO 的 `manifest.json`、`events/part-*.jsonl`、`leaf_state.json`、`jobs_index.json`，Redis 不能作为任务队列或任务状态 |
| 最终回答内容 | 受上下文、模型、权限、时间和工具结果影响，不做 Redis 复用 |

## 缓存 Key 设计

```text
mcp:tools:{workspace_id}:{server_name}:{capability_hash}
mcp:snapshot:{workspace_id}:{server_name}
embedding:{provider}:{model}:{hash(query)}
db:health:{workspace_id}:{db_type}:{connection_id}
milvus:search:{workspace_id}:{knowledge_base_id}:{collection}:{embedding_model}:{kb_version}:{hash(query_embedding+filters+top_k)}
```

## 缓存 Value 设计

MCP tool list：

```json
{
  "server_name": "filesystem",
  "capability_hash": "sha256...",
  "tools": [
    {
      "name": "read_file",
      "description": "...",
      "input_schema_hash": "sha256..."
    }
  ],
  "cached_at": "2026-05-29T00:00:00Z"
}
```

capability snapshot：

```json
{
  "server_name": "filesystem",
  "protocol": "stdio",
  "capabilities": {
    "tools": true,
    "resources": false,
    "prompts": false
  },
  "cached_at": "2026-05-29T00:00:00Z"
}
```

query embedding：

```json
{
  "provider": "openai_compatible",
  "model": "text-embedding-v4",
  "query_hash": "sha256...",
  "vector": [0.01, 0.02],
  "dimension": 1024,
  "cached_at": "2026-05-29T00:00:00Z"
}
```

数据库连接健康状态：

```json
{
  "db_type": "milvus",
  "connection_id": "default",
  "status": "healthy",
  "latency_ms": 24,
  "checked_at": "2026-05-29T00:00:00Z",
  "error_code": null
}
```

Milvus 检索结果：

```json
{
  "knowledge_base_id": "kb_xxx",
  "collection": "kb_default_text_embedding_v4_1024",
  "embedding_model": "text-embedding-v4",
  "kb_version": "v12",
  "filters_hash": "sha256...",
  "top_k": 20,
  "hits": [
    {
      "chunk_id": "chunk_xxx",
      "doc_id": "doc_xxx",
      "doc_version_id": "docv_xxx",
      "score": 0.82
    }
  ],
  "cached_at": "2026-05-29T00:00:00Z"
}
```

Milvus 检索结果缓存只保存召回候选的标识和分数，默认不缓存 chunk 正文、原文片段、完整 metadata。回答链路需要正文时，再按 `chunk_id` / `doc_version_id` 回源读取。

## TTL 建议

| 内容 | 建议 TTL | 说明 |
| --- | --- | --- |
| MCP tool list | 5 到 30 分钟 | MCP server 重启或配置变化后失效 |
| capability snapshot | 5 到 30 分钟 | 能力变化频率低，但不能长期假设稳定 |
| query embedding | 1 到 7 天 | key 必须包含 provider、model 和 query hash |
| 数据库连接健康状态 | 15 到 60 秒 | 只用于减少频繁探活，不代表长期可用 |
| Milvus 检索结果 | 1 到 10 分钟 | 只用于热查询，知识库变更后必须失效 |

## 失效规则

- 所有缓存 key 必须包含 workspace 或权限上下文。
- Milvus 检索结果缓存必须包含 `knowledge_base_id`、collection、embedding model、知识库版本、query embedding hash、filters hash、top_k。
- 数据变更后要让相关缓存失效。
- 敏感内容不直接缓存明文。
- 不能把 Redis 当作唯一状态来源。

Milvus 检索结果必须在以下场景失效：

- 文档新增、删除、重切块、重新 embedding。
- Milvus collection 重建或索引重建。
- embedding provider、embedding model、dimension 或归一化策略变化。
- 知识库权限、workspace 权限、文档可见范围变化。
- 查询过滤条件、top_k 或检索参数变化。

## 失败策略

- Redis 不可用时，直接绕过缓存，回源调用 MCP、Embedding、数据库健康检查或 Milvus 查询。
- Redis 读取失败不阻断主流程。
- Redis 写入失败只记录日志，不影响回答。
- 反序列化失败时删除对应 key 并回源重建。
- 命中缓存后仍必须执行权限上下文校验，不能只依赖 key 命名。
