# Embedding 与 Rerank 策略

## Embedding 定位

embedding 通常在 Agent Runtime / RAG Pipeline 中完成，不在 Milvus 中配置。

入库：

```text
chunk text
  -> embedding model
  -> vector
  -> Milvus insert
```

查询：

```text
user question
  -> embedding model
  -> query vector
  -> Milvus search
```

## 设计要求

- embedding 模型、维度、版本必须写入 manifest 和 chunk metadata。
- query embedding 必须和 collection 的 embedding 模型 / 维度匹配。
- 维度不匹配是不可重试错误。
- 更换 embedding 模型时创建新 collection。
- 不直接用维度不同的 fallback embedding 查旧索引。
- P0 一个知识库只允许一个 active embedding 版本。
- 更换 embedding 模型或维度时，新建 collection 并重新入库。
- 旧 collection 可以保留只读用于回滚和审计，但不参与正常检索。

## Embedding 版本策略

P0 决策：

```text
一个 knowledge_base_id 同一时间只允许一个 active embedding 版本。
```

active embedding 版本由知识库配置记录：

```json
{
  "knowledge_base_id": "kb_default",
  "active_embedding": {
    "provider": "openai_compatible",
    "model": "text-embedding-v4",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "dimension": 1024,
    "version": "embv_001",
    "collection": "kb_default_text_embedding_v4_1024",
    "activated_at": "2026-05-29T00:00:00Z"
  }
}
```

更换 embedding 时：

1. 创建新的 embedding version。
2. 创建新的 Milvus collection。
3. 对知识库文档重新 embedding 并写入新 collection。
4. 新 collection 完成校验后切换 active embedding。
5. 旧 collection 标记为 `readonly_retained`，用于回滚或审计。

P0 更换 embedding 必须创建 `embedding_reindex_job`，不能在 API 请求线程里同步跑完整重建。Job 的 target_scope 是 `knowledge_base_id`，同一知识库的 embedding 重建互斥；新 collection 校验通过前不能更新 `active_embedding.json`。Job 事件用于展示重建批次、失败 chunk、validation 结果和最终 active collection 切换。

查询规则：

- 普通 RAG / GraphRAG 只查询 active collection。
- 不混查多个 embedding 版本。
- 不用备用 embedding 模型查询旧 collection。
- 维度不匹配时直接返回不可重试配置错误。

P1 再考虑多 embedding 版本并存检索、双写迁移、灰度切换和跨版本结果合并。

## Embedding 配置

配置对象建议：

```json
{
  "provider": "openai_compatible",
  "model": "text-embedding-v4",
  "dimension": 1024,
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "api_key_ref": "secret_ref_embedding_main",
  "timeout_ms": 30000,
  "batch_size": 32,
  "enabled": true
}
```

前端配置页需要区分：

- 主对话模型 API。
- GraphRAG 抽取和总结用 LLM API。
- 压缩模型 API。
- Embedding API。
- Rerank API。
- 备用模型 API。

LLM 类 API 默认 `context_window_tokens=200000`，默认 `max_output_tokens=8192`，用户可以在 API 配置页面修改。Embedding / Rerank 不使用这两个字段作为核心配置。

## Rerank 定位

rerank 是候选重排，不是全库搜索。

第一版决策：

```text
Rerank 作为可选开关。
默认关闭。
主 RAG / GraphRAG 流程不依赖 rerank。
Rerank 失败时直接跳过，使用原始排序继续回答。
```

推荐流程：

```text
Milvus top50 / top100
  -> MinIO 回源 chunk 正文
  -> 可合并 Neo4j path evidence
  -> rerank top50 / top100
  -> top5 / top10 给 LLM
```

## Rerank 输入

```json
{
  "query": "用户问题",
  "candidates": [
    {
      "id": "doc_001_chunk_0001",
      "text": "chunk 正文",
      "metadata": {
        "doc_id": "doc_001",
        "page": 1,
        "source": "example.pdf"
      }
    }
  ],
  "top_k": 10
}
```

## Rerank 输出

```json
{
  "results": [
    {
      "id": "doc_001_chunk_0001",
      "rank": 1,
      "score": 0.91,
      "reason": "与问题中的实体和关系高度相关"
    }
  ]
}
```

## 第一版策略

当前决策：

```text
P0 预留 rerank 配置和接口。
P0 默认关闭 rerank。
P0 查询链路必须在 rerank 关闭时正常运行。
P1 再根据评估结果决定是否默认启用。
```

配置要求：

- 前端 API 配置页提供 rerank 开关。
- 默认 `enabled=false`。
- 支持外部 API 或本地模型作为后续扩展。
- `timeout` 必须独立配置。
- 失败策略固定为 `skip_rerank`。
