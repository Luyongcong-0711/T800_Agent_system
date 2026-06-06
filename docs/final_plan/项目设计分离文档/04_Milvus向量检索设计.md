# Milvus 向量检索设计

## 定位

Milvus 是主向量检索层，负责：

- 保存 chunk 向量。
- 进行向量相似度搜索。
- 执行 metadata filter。
- 返回 topK 候选。

Milvus 不负责：

- 切 chunk。
- 调 embedding 模型。
- 保存原始文件。
- 复杂任务状态。
- 审批日志。
- 图谱多跳关系。

## 基本概念

| Milvus 概念 | 类比 | 说明 |
| --- | --- | --- |
| Database | 数据库 | 逻辑隔离空间 |
| Collection | 表 | 一类向量数据，例如文档 chunk |
| Field | 字段 | chunk_id、doc_id、vector 等 |
| Entity | 行 | 一条向量记录 |
| Primary Field | 主键 | chunk 主键 |
| Vector Index | 向量索引 | HNSW、IVF、AUTOINDEX 等 |

Attu 是 Milvus 的可视化管理工具，可以查看 Database、Collection、Schema、Entity、Vector Index、Data Import 和 Vector Search。

## Collection 命名

推荐命名：

```text
kb_{knowledge_base_id}_{embedding_model}_{embedding_dim}
```

示例：

```text
kb_default_text_embedding_v4_1024
```

命名原则：

- 同一 collection 中的向量维度必须一致。
- embedding 模型切换后创建新 collection。
- P0 一个知识库同一时间只允许一个 active collection。
- 旧 collection 可以保留只读用于回滚和审计，但不参与正常检索。
- 不同知识库可以使用不同 collection 做隔离。
- collection 名中不放密钥、用户隐私和完整供应商配置。

## 字段设计

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| pk | Int64 / VarChar | Milvus 主键 |
| chunk_id | VarChar | chunk 唯一 ID |
| doc_id | VarChar | 文档 ID |
| doc_version_id | VarChar | 文档版本 ID |
| workspace_id | VarChar | 工作区 ID |
| knowledge_base_id | VarChar | 知识库 ID |
| object_key | VarChar | MinIO chunk JSON 路径 |
| file_name | VarChar | 来源文件名 |
| page_start | Int64 | 起始页码 |
| page_end | Int64 | 结束页码 |
| chunk_index | Int64 | chunk 顺序 |
| parent_chunk_id | VarChar | 父 chunk ID，可选 |
| section_title | VarChar | 所属章节标题 |
| chunk_type | VarChar | paragraph / table / heading 等 |
| embedding_model | VarChar | embedding 模型 |
| embedding_dim | Int64 | 向量维度 |
| vector | FloatVector | embedding 向量 |
| created_at | VarChar / Int64 | 创建时间 |

MVP 可额外冗余短文本字段用于调试：

```text
text_preview
```

正式回答仍以 MinIO chunk JSON 为正文来源。

## 向量搜索流程

```text
用户问题
  -> Agent Runtime 调 embedding 模型
  -> 得到 query vector
  -> Milvus search
  -> 返回 chunk_id / doc_id / object_key / score
  -> 从 MinIO 读取 chunk 正文
  -> 可选 rerank
  -> 可选 Neo4j 图谱扩展
```

`score` 是 query vector 和 document vector 之间的相似度。如果使用 COSINE：

```text
score = cosine_similarity(query_vector, document_vector)
```

score 用于排序，不应简单理解为百分比。

模型侧不直接调用 Milvus SDK，也不让模型传入任意 collection。模型只能调用 Runtime 暴露的 `vector_search` LangChain Tool；Tool 内部读取知识库 active embedding 配置，再调用 EmbeddingConnector 和 MilvusConnector。

`vector_search` 请求：

```json
{
  "workspace_id": "default",
  "knowledge_base_id": "kb_default",
  "query": "GraphRAG 如何扩展关系？",
  "top_k": 20,
  "filters": {
    "doc_id": "doc_001"
  }
}
```

`vector_search` 返回：

```json
{
  "collection": "kb_default_text_embedding_v4_1024",
  "embedding": {
    "provider": "openai_compatible",
    "model": "text-embedding-v4",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "dimension": 1024
  },
  "hits": [
    {
      "chunk_id": "doc_001_chunk_0001",
      "doc_id": "doc_001",
      "object_key": "workspaces/default/documents/doc_001/chunks/chunk-0001.json",
      "score": 0.82
    }
  ],
  "warnings": []
}
```

LangChain Tool 伪代码：

```python
from langchain_core.tools import tool
from pydantic import BaseModel, Field

class VectorSearchArgs(BaseModel):
    workspace_id: str
    knowledge_base_id: str
    query: str
    top_k: int = Field(default=20, ge=1, le=100)
    filters: dict = Field(default_factory=dict)

@tool("vector_search", args_schema=VectorSearchArgs)
def vector_search_tool(
    workspace_id: str,
    knowledge_base_id: str,
    query: str,
    top_k: int = 20,
    filters: dict | None = None,
) -> dict:
    """使用知识库 active collection 做向量检索，返回 chunk 回源路径。"""
    assert_scope(workspace_id, knowledge_base_id)
    active_embedding = kb_store.get_active_embedding(knowledge_base_id)
    query_vector = embedding_client.embed_query(query, model=active_embedding.model)
    hits = milvus.search(
        collection=active_embedding.collection,
        vector=query_vector,
        top_k=clamp(top_k, 1, 100),
        filters={
            "workspace_id": workspace_id,
            "knowledge_base_id": knowledge_base_id,
            **(filters or {}),
        },
    )
    return ToolResult.ok({
        "collection": active_embedding.collection,
        "embedding": active_embedding.public_summary(),
        "hits": [hit.public_dict() for hit in hits],
        "warnings": [],
    }).to_dict()
```

## 索引策略

常见索引：

| 索引 | 特点 |
| --- | --- |
| FLAT | 暴力搜索，最准确但慢 |
| IVF_FLAT | 聚类分桶，通过 nprobe 控制速度和召回 |
| HNSW | 多层邻居图，搜索快、召回高、内存占用较高 |
| AUTOINDEX | 由 Milvus 自动选择和管理索引策略 |

第一版建议：

```text
AUTOINDEX + COSINE
```

后续根据数据量、查询 QPS、延迟要求、召回要求和内存限制决定是否手动切换 IVF 或 HNSW。

## 写入和建索引

Milvus 建索引是重操作，但不是每次查询都做。

典型过程：

```text
新数据写入 growing segment
  -> flush 成 sealed segment
  -> 对 sealed segment 构建索引
  -> 查询时老数据走索引，新数据可能临时暴力查
```

设计要求：

- 批量写入 chunk 向量。
- 写入时记录 `operation_id`。
- 写入时携带 `doc_version_id`，默认查询只检索当前文档版本。
- 维度必须和 collection 定义一致。
- 更换 embedding 模型或维度时创建新 collection。
- 新 collection 完成重新入库和校验后，才切换为知识库 active collection。
- 不把不同维度向量混在同一个 collection。
- 文档入库中的 Milvus 写入由 `document_ingestion_job` 执行；知识库级 collection 重建由 `embedding_reindex_job` 执行。写入超时或连接中断导致结果不确定时，Job 进入 `unknown_outcome`，先按 `operation_id` / `chunk_id` 查询 Milvus 是否已写入。

## 本地开发连接

本地开发建议使用 Milvus Standalone + Attu。

| 服务 | 端口 | 说明 |
| --- | --- | --- |
| Milvus gRPC | 19530 | Python SDK / LangChain 连接 |
| Milvus metrics | 9091 | 指标 |
| Attu UI | 3000 | 可视化管理 |

Python 连接示例：

```python
from pymilvus import MilvusClient

client = MilvusClient(uri="http://localhost:19530")
```

Docker Compose 内部访问：

```text
standalone:19530
```

宿主机访问：

```text
localhost:19530
```
