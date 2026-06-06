# RAG 与 GraphRAG 流程设计

## 第一版范围

第一版确认做基础自动图谱入库和只读 GraphRAG 查询。

做：

- PDF、Word/DOCX、Markdown、HTML、Excel/CSV、TXT、代码文件和图片 OCR 的 Parser Router 入口。
- Document Representation。
- 结构化 / 语义切块。
- chunk 级 mentions / relations / evidence 抽取。
- 实体归一化。
- 关系标准化和关系合并。
- 写入 Neo4j 的 Document / DocumentVersion / Chunk / Mention / Entity / RelationFact / Evidence。
- 查询时使用 Milvus 召回 chunk，再用 Neo4j 做 1 到 2 跳扩展。

不做：

- 图谱可视化编辑。
- 复杂关系权重配置。
- 人工审核后台。
- 自动图谱质量优化。
- 多知识库全局实体主数据治理。

## 文件预处理

第一版需要 Parser Router，而不是所有文件都走同一个解析器。

```text
上传文件
  -> Parser Router
  -> PDF / DOCX / Markdown / HTML / Excel / CSV / TXT / 代码文件 / 图片 OCR 等格式解析
  -> 输出统一 Document Representation
```

P0 文件类型范围：

| 类型 | P0 行为 |
| --- | --- |
| PDF | 支持文字型 PDF 和扫描 PDF；扫描内容走 OCR，复杂版面保留 block 顺序和页码 |
| Word / DOCX | 支持标题、段落、表格、列表和页内结构 |
| Markdown / TXT | 支持标题层级、代码块、列表和引用块 |
| HTML | 提取正文结构，过滤导航、脚本和样式 |
| Excel / CSV | 按 sheet / table / row chunk 保存，保留表头和单元格坐标 |
| 代码文件 | 按语言、函数、类、注释和文件路径切块，保留 symbol metadata |
| 图片 | OCR 提取文字块，保存图片 object_key、bbox、页序和置信度 |

解析器候选：

| 解析器 | 用途 |
| --- | --- |
| Docling | 默认多格式解析，优先覆盖 PDF / DOCX / PPTX / HTML / 图片 OCR |
| MinerU | PDF、扫描件、复杂版面，作为 PDF 高质量解析候选 |
| Apache Tika | 长尾格式兜底，解析失败时尝试抽纯文本 |

P0 必须实现 Parser Router 接口和上述文件类型的稳定入口。具体底层解析器可以按开发阶段接入，但接口不能变；某类文件暂时只能纯文本兜底时，manifest 必须标记 `parser_quality=degraded`，前端显示“已降级解析”，不能假装完整支持。

## Document Representation

Document Representation 是系统内部标准中间格式，用来统一表示解析后的文档，避免直接把 PDF、Word、表格、图片 OCR 全部压成纯文本。

需要保留：

- 标题、段落、列表、表格、代码块、引用。
- OCR 文字块顺序。
- page_start / page_end。
- char_start / char_end。
- block_id。
- section_path。
- parser_name / parser_version。
- source_file_name / mime_type / file_sha256。
- parser_quality：`full` / `degraded` / `failed`。
- warnings：解析降级、OCR 低置信度、表格跨页、代码语言未知等。

示例：

```json
{
  "doc_id": "doc_001",
  "doc_version_id": "docv_001",
  "parser": "mineru",
  "parser_version": "x.y.z",
  "title": "采购合同",
  "doc_type": "contract",
  "language": "zh",
  "blocks": [
    {
      "block_id": "blk_001",
      "type": "heading",
      "level": 1,
      "text": "第一条 合同主体",
      "page_start": 1,
      "page_end": 1,
      "char_start": 0,
      "char_end": 12
    },
    {
      "block_id": "blk_002",
      "type": "paragraph",
      "text": "甲方为广州星河科技有限公司，乙方为深圳蓝海贸易有限公司。",
      "page_start": 1,
      "page_end": 1,
      "char_start": 13,
      "char_end": 52
    }
  ]
}
```

Document Representation 保存到：

```text
workspaces/{workspace_id}/documents/{doc_id}/parsed/document.json
```

## 文档级 Metadata 抽取

文档级抽取在 chunk 前执行，用于建立全局上下文。

抽取内容：

```text
title
doc_type
author
created_date
published_date
summary
language
main_entities
roles
aliases
high_level_topics
```

文档级 metadata 必须统一写入 `manifest.json` 和 `parsed/document.json`，供检索过滤、图谱构建和前端展示使用。P0 metadata 字段：

```json
{
  "workspace_id": "default",
  "knowledge_base_id": "kb_001",
  "doc_id": "doc_001",
  "doc_version_id": "docv_001",
  "source_file_name": "合同.pdf",
  "mime_type": "application/pdf",
  "file_sha256": "sha256...",
  "parser_name": "docling",
  "parser_version": "x.y.z",
  "parser_quality": "full",
  "language": "zh",
  "doc_type": "contract",
  "title": "采购合同",
  "page_count": 12,
  "block_count": 238,
  "table_count": 4,
  "image_count": 3,
  "ocr_used": false,
  "warnings": []
}
```

合同类文档尤其要抽：

```text
甲方
乙方
买方
卖方
供应商
采购方
合同编号
签署日期
合同金额
本合同 / 本协议 指代对象
```

文档角色映射示例：

```json
{
  "doc_type": "contract",
  "roles": {
    "甲方": {
      "entity_name": "广州星河科技有限公司",
      "entity_type": "Organization"
    },
    "乙方": {
      "entity_name": "深圳蓝海贸易有限公司",
      "entity_type": "Organization"
    },
    "本合同": {
      "entity_name": "采购合同001",
      "entity_type": "Document"
    }
  }
}
```

## 入库流程

第一版完整入库流程：

```text
用户上传文件
  -> MinIO 保存 original
  -> 写 manifest.json
  -> 创建 document_ingestion_job
  -> Parser Router 解析文件
  -> MinIO 保存 parsed/document.json 和 parsed/text.json
  -> 文档级 metadata 抽取
  -> 文本清洗
  -> 结构化/语义切 chunk
  -> MinIO 保存 chunks.json 和 chunk-{n}.json
  -> 调 embedding 模型
  -> Milvus 写入 chunk_id + vector + metadata
  -> chunk 级 mentions / entity candidates / relation candidates 抽取
  -> MinIO 保存 staging 抽取结果
  -> Entity Resolution
  -> Relation Normalization
  -> Relation Merging
  -> MinIO 保存 entities.json / relation_facts.jsonl / evidence.jsonl / decisions.jsonl
  -> Neo4j 写入 Document / DocumentVersion / Chunk / Mention / Entity / RelationFact / Evidence
  -> 更新 manifest 状态
  -> 写 ingestion event
```

该流程在 P0 由 `document_ingestion_job` 后台执行。上传接口只负责保存原始文件、创建文档 manifest 和创建 Job；解析、切块、embedding、图谱抽取、Neo4j 写入和索引更新都由 Job Worker 推进。Job 事件写入 `jobs/{job_id}/events/part-*.jsonl`，前端在任务中心和文档详情页通过 Job SSE 展示进度。

Chunk 发生在：

```text
文件解析之后，embedding 之前。
```

可使用：

```text
RecursiveCharacterTextSplitter
默认文本 chunk_size: 800 tokens
默认文本 chunk_overlap: 120 tokens
最小 chunk_size: 300 tokens
最大 child chunk_size: 1200 tokens
parent chunk 目标: 2500 到 3500 tokens
```

P0 不把 chunk 参数留空。不同文档类型采用固定初始策略，后续可以通过知识库配置覆盖：

| 文档类型 | 切块策略 | 默认参数 |
| --- | --- | --- |
| 普通文本 / DOCX / HTML | 标题层级 -> 段落 -> 句子/token | child 800 tokens，overlap 120 tokens |
| PDF | 先保留 page/block 顺序，再按 section/paragraph 切块 | child 800 tokens，跨页 overlap 80 tokens |
| Markdown | 按 heading、list、blockquote、code fence 切块 | heading 内 child 900 tokens，代码块不拆小于 1200 tokens 的块 |
| 表格 / CSV / Excel | 每个 sheet/table 建 parent，按表头 + row window 切 child | 每块 20 到 50 行，必须重复表头 |
| 代码文件 | 按 file -> class/function -> logical block 切块 | 单个 symbol 不超过 1200 tokens，超长函数按语句窗口拆 |
| 图片 OCR | 按图片/page 的 OCR block 顺序切块 | child 500 tokens，保留 bbox 和 confidence |

切块结果必须做到：

- 每个 chunk 有稳定 `chunk_id`，由 `doc_version_id + chunk_index + text_hash` 生成。
- 每个 child chunk 关联 parent chunk。
- 每个 chunk 保存 `source_block_ids`，方便回源定位。
- 每个 chunk 保存 `metadata_filter`，用于 Milvus 查询过滤。
- chunk 文本为空、OCR 置信度过低或表格无法提取时不写入 Milvus，但要写入 `chunk_errors.jsonl`。

## 结构化/语义切块

切块时优先尊重文档结构和语义，而不是机械地每 N 个 token 切一刀。

推荐流程：

```text
Document
  -> Section
  -> Subsection
  -> Paragraph
  -> Sentence / token
```

规则：

- 先按章节切。
- 章节太长时按小标题切。
- 小标题仍太长时按段落切。
- 段落仍太长时按句子或 token 切。
- 保留 overlap 防止边界信息丢失。
- 保留 parent section summary 供回答和 KG 抽取使用。

Parent-child chunking：

```text
parent chunk:
  第二章 付款条款完整内容，3000 tokens

child chunk:
  付款金额，500 tokens
  付款时间，500 tokens
  违约金，500 tokens
```

用途：

```text
Milvus 检索 child chunk
LLM 回答时带 parent context
KG 抽取时使用 child chunk + parent summary
```

推荐 chunk 字段：

```json
{
  "chunk_id": "chk_001",
  "doc_id": "doc_001",
  "doc_version_id": "docv_001",
  "chunk_index": 1,
  "parent_chunk_id": "pchk_001",
  "section_title": "第二条 合同金额",
  "section_path": ["采购合同", "第二条 合同金额"],
  "chunk_type": "paragraph",
  "text": "本合同金额为人民币100万元。",
  "page_start": 2,
  "page_end": 2,
  "char_start": 1024,
  "char_end": 1098,
  "token_count": 42,
  "overlap_prev": "甲方为广州星河科技有限公司...",
  "overlap_next": "乙方应于2026年6月30日前..."
}
```

chunk metadata 字段必须进入 Milvus payload 和 MinIO chunk JSON，P0 字段：

```json
{
  "workspace_id": "default",
  "knowledge_base_id": "kb_001",
  "doc_id": "doc_001",
  "doc_version_id": "docv_001",
  "chunk_id": "chk_001",
  "parent_chunk_id": "pchk_001",
  "chunk_index": 1,
  "chunk_type": "paragraph",
  "source_file_name": "合同.pdf",
  "mime_type": "application/pdf",
  "language": "zh",
  "doc_type": "contract",
  "section_path": ["采购合同", "第二条 合同金额"],
  "page_start": 2,
  "page_end": 2,
  "source_block_ids": ["blk_018", "blk_019"],
  "text_hash": "sha256...",
  "token_count": 842,
  "embedding_model": "text-embedding-v4",
  "embedding_dimension": 1024,
  "parser_quality": "full",
  "visibility": "workspace",
  "created_at": "2026-05-30T12:00:00Z"
}
```

## 入库失败重试和部分成功

文档入库是分阶段 Job，不要求所有阶段一次成功才可见。P0 阶段状态：

```text
uploaded
parsed
chunked
embedded_partial
embedded
graph_partial
graph_built
indexed
failed
```

失败处理规则：

| 阶段 | 可重试 | 失败后的状态 | 用户可见表现 |
| --- | --- | --- | --- |
| 保存 original | 是 | `failed` | 上传失败，不能检索 |
| Parser Router | 是 | `failed` 或 `parsed_degraded` | 解析失败不可检索；降级解析可继续但显示 warning |
| chunk | 是 | `failed` | 文档已上传但未入库 |
| embedding batch | 是，只重试失败 batch | `embedded_partial` | 已成功 chunk 可检索，失败 chunk 在详情页列出 |
| Milvus 写入 | 是，unknown 时先探测 | `unknown_outcome` / `embedded_partial` | 任务中心显示恢复中或部分可检索 |
| KG 抽取 | 是 | `graph_partial` | RAG 可用，GraphRAG 标记图谱未完整 |
| Neo4j 写入 | 是，operation_id 探测 | `unknown_outcome` / `graph_partial` | 图谱结果可能不完整 |
| index 更新 | 是，可重建 | `indexed` 或 `index_stale` | 文档详情可见，列表可能稍后刷新 |

部分成功不是静默成功。前端文档详情必须显示：

- 当前 `ingestion_status`。
- 成功 chunk 数、失败 chunk 数、总 chunk 数。
- Milvus 写入批次进度。
- Neo4j 写入批次进度。
- warnings 和可重试错误。
- 关联 `document_ingestion_job`。

manifest 示例：

```json
{
  "doc_id": "doc_001",
  "doc_version_id": "docv_001",
  "ingestion_status": "embedded_partial",
  "chunk_total": 120,
  "chunk_embedded": 116,
  "chunk_failed": 4,
  "graph_status": "pending",
  "search_available": true,
  "graphrag_available": false,
  "last_job_id": "job_ingest_001",
  "warnings": [
    {"type": "embedding_batch_failed", "count": 4, "retryable": true}
  ]
}
```

## Chunk 级图谱抽取

实体和关系不是从 embedding 中抽取，而是从 chunk 文本中抽取。
P0 采用 GraphRAG LLM 做结构化抽取，规则抽取作为兜底 fallback，不把规则抽取当成最终主路径。

```text
chunk text -> embedding -> Milvus
chunk text -> GraphRAG LLM -> mentions / relations / evidence -> Neo4j
GraphRAG LLM failed -> rule-based extractor fallback -> Neo4j
```

GraphRAG LLM 抽取按 chunk batch 执行，默认每批最多 24 个 chunk。每个 batch 返回局部 `entities / mentions / relation_facts / evidence / decisions`，Job Worker 按稳定 ID 合并，同名实体合并 alias、source_chunk_ids 和最高 confidence。

抽取输入不应只有当前 chunk，还应包含：

```text
chunk_text
section_title
section_path
parent_section_summary
doc_metadata
previous_chunk_summary，可选
next_chunk_summary，可选
allowed_entity_types
allowed_relation_types
```

抽取输出：

```text
mentions
entity_candidates
relation_candidates
evidence
```

chunk 级抽取结果只是 local graph fragment，不能直接当最终知识图谱。它必须经过实体归一化、关系标准化和关系合并。
graph_build_job 会把抽取来源、fallback 原因、usage 概要一起写入 graph decisions 和 job artifact，方便前端和诊断包回看。

## 实体归一化

Mention 和 Entity 必须区分。

```text
Mention = 原文里的某一次提及
Entity = 归一化后的真实实体
```

示例：

```text
广州星河科技有限公司
星河科技
甲方
该公司
```

这些是不同 mention，但可能指向同一个 entity。

实体归一化流程：

```text
mention
  -> normalize surface
  -> strong identifier match
  -> document role mapping
  -> alias match
  -> candidate retrieval
  -> candidate scoring
  -> decision:
      高分 -> 自动合并
      中分 -> LLM 判断或人工审核
      低分 -> 新建实体
```

decision 枚举：

```text
MATCH
NEW_ENTITY
REVIEW
IGNORE
MERGE
SPLIT
```

decision_source 枚举：

```text
strong_id
exact_name
alias_rule
doc_role_mapping
fuzzy_score
embedding_similarity
llm
human
```

关键原则：

- 不完全依赖 LLM。
- 甲方、乙方、本合同这类别名必须有文档 scope。
- 错合并比漏合并危险，低置信度保守处理。
- 每次归一化决策都要保存 provenance。

## 关系标准化与合并

抽取出的原始 predicate_text 必须映射成标准 predicate。

示例：

```text
签署
签约
达成合同
合同相对方
与其签订协议
  -> SIGNS_WITH
```

关系合并 key：

```text
subject_entity_id + predicate + object_entity_id + scope_id
```

scope_id 可以是：

```text
doc_id
contract_id
event_id
valid_time
project_id
```

每条关系事实必须保留 Evidence：

```json
{
  "fact_id": "fact_001",
  "subject_entity_id": "ent_org_xinghe",
  "predicate": "SIGNS_WITH",
  "object_entity_id": "ent_org_lanhai",
  "confidence": 0.94,
  "source_chunk_ids": ["chk_001", "chk_005"],
  "evidence_count": 2
}
```

## 跨 Chunk 关系补全

不要暴力两两比较 chunk。

推荐补全范围：

- 相邻 chunk。
- 同 section chunk。
- 共享实体 chunk。
- 包含未解析 mention 的 chunk。
- 低置信度关系所在 chunk 的 parent section。

文档级 consolidation 检查：

- 是否有甲方、乙方、本合同、该公司未解析。
- 是否有重复关系。
- 是否有明显跨 chunk 关系。
- 是否有冲突事实。
- 是否有低置信度但高价值关系需要人工审核。

## 基础查询流程

```text
用户问题
  -> query embedding
  -> Milvus 向量召回 topN
  -> 根据 object_key 从 MinIO 读取 chunk 正文
  -> 可选 rerank
  -> 取 topK chunk
  -> LLM 生成答案
```

基础 RAG 优先实现为 LangChain Tool。底层 Milvus / MinIO / Embedding 仍由 Connector 执行，但模型看到的是一个受控 `rag_search` 工具。

LangChain Tool 伪代码：

```python
from langchain_core.tools import tool
from pydantic import BaseModel, Field

class RagSearchArgs(BaseModel):
    workspace_id: str
    knowledge_base_id: str
    query: str
    top_k: int = Field(default=50, ge=1, le=100)
    final_top_k: int = Field(default=10, ge=1, le=20)
    filters: dict = Field(default_factory=dict)
    max_chars_per_chunk: int = Field(default=1200, ge=200, le=4000)

@tool("rag_search", args_schema=RagSearchArgs)
def rag_search_tool(
    workspace_id: str,
    knowledge_base_id: str,
    query: str,
    top_k: int = 50,
    final_top_k: int = 10,
    filters: dict | None = None,
    max_chars_per_chunk: int = 1200,
) -> dict:
    """从 Milvus 召回文本 chunk，并从 MinIO 回源正文作为可引用证据。"""
    assert_scope(workspace_id, knowledge_base_id)
    active_embedding = kb_store.get_active_embedding(knowledge_base_id)

    query_vector = embedding_client.embed_query(
        text=query,
        model=active_embedding.model,
        dimension=active_embedding.dimension,
    )

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

    chunks = []
    for hit in hits:
        chunk = minio.read_json(hit.object_key)
        chunks.append({
            "chunk_id": hit.chunk_id,
            "doc_id": hit.doc_id,
            "doc_version_id": hit.doc_version_id,
            "score": hit.score,
            "text": truncate(chunk["text"], max_chars_per_chunk),
            "source": chunk["source"],
        })

    ranked = maybe_rerank(query, chunks)
    return ToolResult.ok({"text_evidence": ranked[:final_top_k], "warnings": []}).to_dict()
```

## GraphRAG 查询流程

```text
用户问题
  -> query embedding
  -> Milvus 召回相关 chunk
  -> 从 chunk 提取或读取实体
  -> Neo4j 扩展 1 到 2 跳关系
  -> 合并 chunk 证据和 graph 证据
  -> LLM 生成答案
```

GraphRAG P0 查询流程：

```text
1. 用 active embedding 生成 query vector。
2. Milvus 在 active collection 召回 topN chunk。
3. 从 MinIO 回源 chunk 正文和 metadata。
4. 从 chunk_id 查 Neo4j 中的 Chunk -> Mention -> Entity。
5. 从问题中抽取显式实体名，并用 graph_entity_search 解析候选实体。
6. 合并“Milvus 命中的 chunk 实体”和“问题显式实体”作为 graph seed。
7. 对 seed entity 做 1 到 2 跳 graph_expand_entity。
8. 如果问题是两个实体关系，调用 graph_find_relationship 和 graph_find_paths。
9. 根据 fact_id / evidence_id 调 graph_get_evidence 回源证据。
10. 合并 text_evidence 和 graph_evidence。
11. 可选 rerank，只对候选证据执行，不对全库执行。
12. 生成答案时强制带 source、方向、限制说明。
```

GraphRAG 不是一个普通函数串起来，而是一个 LangGraph 子图。它可以被包装成 `graphrag_search` Tool，也可以作为主 Agent 图里的检索子流程调用。

LangGraph State 伪代码：

```python
from typing import TypedDict
from langgraph.graph import StateGraph, START, END

class GraphRAGState(TypedDict):
    workspace_id: str
    knowledge_base_id: str
    query: str
    filters: dict
    graph_depth: int
    relationship_allowlist: list[str]
    text_evidence: list[dict]
    explicit_entity_names: list[str]
    seed_entities: list[dict]
    graph_paths: list[dict]
    graph_evidence: list[dict]
    merged_evidence: dict
    warnings: list[str]

def vector_retrieval_node(state: GraphRAGState) -> GraphRAGState:
    text_result = rag_search_tool.invoke({
        "workspace_id": state["workspace_id"],
        "knowledge_base_id": state["knowledge_base_id"],
        "query": state["query"],
        "top_k": 50,
        "final_top_k": 10,
        "filters": state.get("filters", {}),
    })
    return {"text_evidence": text_result["data"]["text_evidence"]}

def seed_entity_node(state: GraphRAGState) -> GraphRAGState:
    seed_entities = []
    for chunk in state["text_evidence"]:
        seed_entities.extend(neo4j.entities_by_chunk_id(
            workspace_id=state["workspace_id"],
            knowledge_base_id=state["knowledge_base_id"],
            chunk_id=chunk["chunk_id"],
            limit=10,
        ))

    explicit_names = entity_mention_extractor.extract(state["query"])
    for name in explicit_names:
        candidates = graph_entity_search_tool.invoke({
            "workspace_id": state["workspace_id"],
            "knowledge_base_id": state["knowledge_base_id"],
            "query": name,
            "limit": 5,
        })
        seed_entities.extend(candidates["data"]["entities"])

    return {
        "explicit_entity_names": explicit_names,
        "seed_entities": dedupe_and_rank_entities(seed_entities, limit=10),
    }

def graph_expand_node(state: GraphRAGState) -> GraphRAGState:
    graph_paths = []
    for entity in state["seed_entities"]:
        expanded = graph_expand_entity_tool.invoke({
            "workspace_id": state["workspace_id"],
            "knowledge_base_id": state["knowledge_base_id"],
            "entity_id": entity["entity_id"],
            "depth": clamp(state["graph_depth"], 1, 2),
            "relationship_allowlist": state.get("relationship_allowlist", []),
            "limit": 30,
            "include_evidence": True,
        })
        graph_paths.extend(expanded["data"]["paths"])
    return {"graph_paths": graph_paths}

def two_entity_path_node(state: GraphRAGState) -> GraphRAGState:
    names = state["explicit_entity_names"]
    if not detect_two_entity_relation_question(state["query"], names):
        return {}
    direct = graph_find_relationship_tool.invoke({
        "workspace_id": state["workspace_id"],
        "knowledge_base_id": state["knowledge_base_id"],
        "source_entity": names[0],
        "target_entity": names[1],
        "relationship_allowlist": state.get("relationship_allowlist", []),
        "include_evidence": True,
    })
    paths = graph_find_paths_tool.invoke({
        "workspace_id": state["workspace_id"],
        "knowledge_base_id": state["knowledge_base_id"],
        "source_entity": names[0],
        "target_entity": names[1],
        "max_depth": clamp(state["graph_depth"], 1, 2),
        "relationship_allowlist": state.get("relationship_allowlist", []),
        "limit": 10,
    })
    return {
        "graph_paths": state["graph_paths"]
            + direct["data"].get("relationships", [])
            + paths["data"].get("paths", [])
    }

def evidence_merge_node(state: GraphRAGState) -> GraphRAGState:
    graph_evidence = graph_get_evidence_for_paths(state["graph_paths"], include_chunk_text=True)
    merged = merge_text_and_graph_evidence(state["text_evidence"], graph_evidence)
    ranked = maybe_rerank(state["query"], merged)
    return {
        "graph_evidence": ranked.graph_evidence,
        "merged_evidence": ranked.to_dict(),
        "warnings": build_graphrag_warnings(state["text_evidence"], state["graph_paths"]),
    }

graphrag_builder = StateGraph(GraphRAGState)
graphrag_builder.add_node("vector_retrieval", vector_retrieval_node)
graphrag_builder.add_node("seed_entities", seed_entity_node)
graphrag_builder.add_node("graph_expand", graph_expand_node)
graphrag_builder.add_node("two_entity_paths", two_entity_path_node)
graphrag_builder.add_node("evidence_merge", evidence_merge_node)
graphrag_builder.add_edge(START, "vector_retrieval")
graphrag_builder.add_edge("vector_retrieval", "seed_entities")
graphrag_builder.add_edge("seed_entities", "graph_expand")
graphrag_builder.add_edge("graph_expand", "two_entity_paths")
graphrag_builder.add_edge("two_entity_paths", "evidence_merge")
graphrag_builder.add_edge("evidence_merge", END)

graphrag_subgraph = graphrag_builder.compile()
```

把 GraphRAG 子图包装成 LangChain Tool：

```python
class GraphRAGSearchArgs(BaseModel):
    workspace_id: str
    knowledge_base_id: str
    query: str
    filters: dict = Field(default_factory=dict)
    graph_depth: int = Field(default=2, ge=1, le=2)
    relationship_allowlist: list[str] = Field(default_factory=list)

@tool("graphrag_search", args_schema=GraphRAGSearchArgs)
def graphrag_search_tool(**kwargs) -> dict:
    """执行文本召回 + 图谱扩展 + 证据合并的 GraphRAG 检索。"""
    final_state = graphrag_subgraph.invoke(kwargs)
    return ToolResult.ok({
        "text_evidence": final_state["text_evidence"],
        "graph_evidence": final_state["graph_evidence"],
        "warnings": final_state["warnings"],
    }).to_dict()
```

两个实体关系问题：

```text
用户问题：A 和 B 是什么关系？
  -> Milvus 找到含 A / B 或相关语义的 chunk
  -> Neo4j 查询 A 和 B 的直接关系
  -> Neo4j 查询 A 和 B 的多跳路径
  -> 合并文本证据、路径证据、事件证据
```

两个实体关系问题在 LangGraph 子图中由 `two_entity_path_node` 处理。它不是让模型直接写查询，而是固定调用 `graph_entity_search_tool`、`graph_find_relationship_tool`、`graph_find_paths_tool` 和 `graph_get_evidence_tool`。

LangGraph 节点伪代码：

```python
def two_entity_relationship_node(state: GraphRAGState) -> GraphRAGState:
    names = entity_mention_extractor.extract_two_entities(state["query"])
    if len(names) != 2:
        return {"warnings": ["not_a_two_entity_question"]}

    source = graph_entity_search_tool.invoke({
        "workspace_id": state["workspace_id"],
        "knowledge_base_id": state["knowledge_base_id"],
        "query": names[0],
        "limit": 5,
    })
    target = graph_entity_search_tool.invoke({
        "workspace_id": state["workspace_id"],
        "knowledge_base_id": state["knowledge_base_id"],
        "query": names[1],
        "limit": 5,
    })

    if needs_disambiguation(source, target):
        return {"warnings": ["needs_entity_disambiguation"], "candidate_entities": {"source": source, "target": target}}

    direct = graph_find_relationship_tool.invoke({
        "workspace_id": state["workspace_id"],
        "knowledge_base_id": state["knowledge_base_id"],
        "source_entity": source["data"]["entities"][0]["entity_id"],
        "target_entity": target["data"]["entities"][0]["entity_id"],
        "relationship_allowlist": state.get("relationship_allowlist", []),
        "include_evidence": True,
    })

    paths = graph_find_paths_tool.invoke({
        "workspace_id": state["workspace_id"],
        "knowledge_base_id": state["knowledge_base_id"],
        "source_entity": source["data"]["entities"][0]["entity_id"],
        "target_entity": target["data"]["entities"][0]["entity_id"],
        "max_depth": 2,
        "relationship_allowlist": state.get("relationship_allowlist", []),
        "limit": 10,
    })

    evidence = graph_get_evidence_for_paths(
        direct["data"]["relationships"] + paths["data"]["paths"],
        include_chunk_text=True,
    )
    return {
        "graph_paths": direct["data"]["relationships"] + paths["data"]["paths"],
        "graph_evidence": evidence,
    }
```

## 混合检索

生产型 RAG 常见流程：

```text
metadata filter
  -> dense vector search
  -> sparse / keyword search
  -> merge / fusion
  -> rerank
  -> topK 给 LLM
```

当前数据库限定下，第一版可以先做：

```text
Milvus dense vector search
  -> Neo4j graph expansion
  -> rerank 候选 chunk / path
```

rerank 不对全库执行。

推荐：

```text
Milvus top50 / top100
  -> 取正文和 metadata
  -> rerank top50 / top100
  -> 最终 top5 / top10 给 LLM
```

适合增强混合检索的场景：

- 错误码。
- API 名。
- 函数名。
- 产品型号。
- 人名。
- 项目名。
- 技术文档。
- 制度文档。

## 证据合并

LLM 生成答案前，应把证据分成清晰结构：

```json
{
  "question": "用户问题",
  "text_evidence": [
    {
      "chunk_id": "doc_001_chunk_0001",
      "doc_id": "doc_001",
      "source": "example.pdf",
      "score": 0.82,
      "text": "片段正文"
    }
  ],
  "graph_evidence": [
    {
      "path": ["A", "RELATION", "B"],
      "depth": 1,
      "weight": 0.9
    }
  ],
  "limitations": []
}
```

要求：

- 文本证据和图谱证据都要带来源。
- 图谱路径要保留方向和关系类型。
- 空结果不是错误，应返回受限回答或询问用户。
- 证据不足时不要编造。

## 入库失败恢复

| 失败阶段 | 恢复策略 |
| --- | --- |
| 原始文件上传失败 | 重新上传，不创建完整 manifest |
| 解析失败 | manifest 记录 failed，可重试解析 |
| 切 chunk 失败 | 保留 parsed/text.json，重试 chunk |
| embedding 失败 | 保留 chunk JSON，暂停向量入库 |
| Milvus 写入失败 | 使用 operation_id 去重，重试写入 |
| 实体抽取失败 | 不影响基础 RAG，可延后图谱入库 |
| Neo4j 写入失败 | 基础 RAG 可用，GraphRAG 标记降级 |

这些失败状态必须同步写入 `document_ingestion_job` 的 `manifest.json`、`leaf_state.json` 和事件流。Milvus / Neo4j 写入结果不确定时，Job 进入 `unknown_outcome`，先按 `operation_id`、`chunk_id`、`doc_version_id` 或 `fact_id` 探测实际写入结果，再决定继续、重试或失败。
