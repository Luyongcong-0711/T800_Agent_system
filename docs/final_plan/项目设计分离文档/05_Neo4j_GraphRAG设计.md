# Neo4j 与 GraphRAG 设计

## 定位

Neo4j 是图谱层，负责实体、关系、路径和多跳扩展。

第一版范围已经确认：做基础自动图谱入库和只读 GraphRAG 查询。

P0 做：

- 文档入库过程中自动写入 Document / DocumentVersion / Chunk / Mention / Entity / RelationFact / Evidence。
- 使用实体归一化、关系标准化、关系合并生成基础图谱。
- Milvus 检索后使用 Neo4j 做 1 到 2 跳关系扩展。
- 提供只读 graph tool 查询实体、路径、时间线和 evidence。

P0 不做：

- 图谱可视化编辑。
- 复杂关系权重系统。
- 人工审核后台。
- 自动图谱质量优化。
- 多知识库全局实体主数据治理。

图谱写入只允许入库 pipeline 或受控管理任务执行，不暴露给普通模型 tool call。普通大模型只能通过参数化只读图谱工具查询知识图谱，不能写入 Neo4j，不能自由执行 Cypher。

它解决的问题：

```text
实体之间是什么关系？
一个实体沿关系能扩展到哪些事件、地点、概念？
两个实体之间是否存在多跳路径？
某个 chunk 提到哪些实体？
某个事件由哪些实体参与？
```

## 图谱节点

推荐节点：

| 节点 | 说明 |
| --- | --- |
| Document | 文档 |
| DocumentVersion | 文档版本 |
| Chunk | 文档片段 |
| Mention | 原文中的一次实体提及 |
| Entity | 通用实体 |
| Person | 人物 |
| Event | 事件 |
| Concept | 概念 |
| Place | 地点 |
| Object | 物件 |
| Topic | 主题 |
| RelationFact | 归一化后的关系事实 |
| Evidence | 支撑实体或关系的证据 |

已验证的节点类型：

```text
Person
Event
Concept
Place
Object
```

节点级分段字段：

| 类型 | embeddingText 字段 |
| --- | --- |
| Person | 人物名、别名、性别、家族、居所、身份、代际、说明 |
| Event | 事件名、阶段、类型、章节、摘要 |
| Concept | 概念名、类型、说明 |
| Place | 地点名、类型、说明 |
| Object | 物件名、类型、说明 |

实验结果样例：

```text
Person：173
Event：43
Concept：10
Place：42
Object：21
已向量化节点总数：289
```

## 图谱关系

推荐通用关系：

| 关系 | 说明 |
| --- | --- |
| DOCUMENT_HAS_CHUNK | 文档包含 chunk |
| DOCUMENT_HAS_VERSION | 文档包含版本 |
| VERSION_HAS_CHUNK | 文档版本包含 chunk |
| CHUNK_HAS_MENTION | chunk 中有一次 mention |
| MENTION_REFERS_TO_ENTITY | mention 指向归一化实体 |
| CHUNK_MENTIONS_ENTITY | chunk 提到实体 |
| ENTITY_RELATED_TO | 实体相关 |
| RELATION_SUBJECT | 实体是关系事实主语 |
| RELATION_OBJECT | 实体是关系事实宾语 |
| RELATION_SUPPORTED_BY | 关系事实由证据支持 |
| EVIDENCE_FROM_CHUNK | 证据来自 chunk |
| EVENT_INVOLVES_ENTITY | 事件涉及实体 |
| EVENT_OCCURS_AT | 事件发生地点 |
| ENTITY_HAS_CONCEPT | 实体关联概念 |
| PERSON_KNOWS_PERSON | 人物关系 |

P0 关系方向规则：

- 所有图谱路径返回时必须保留方向。
- `A -[:REL]-> B` 不能展示成无方向的 `A REL B`。
- 如果查询时使用了无方向匹配，返回结果仍要标明实际存储方向。
- LLM 生成答案时必须区分 `outgoing`、`incoming`、`undirected_for_search_only`。

P0 关系强弱规则：

| 类型 | 含义 | 查询策略 |
| --- | --- | --- |
| strong | 语义明确、方向明确、证据明确的关系 | 正常参与排序和答案生成 |
| weak | 共现、宽泛相关、列表收录、章节归属等弱语义关系 | 默认降权，作为补充证据 |
| search_only | 仅用于召回扩展，不应直接写进最终答案 | 只辅助找上下文 |

第一版不做复杂可配置权重系统，只在关系上保留 `relation_strength` 和 `rank_penalty`。

```json
{
  "type": "MEMBER_OF",
  "direction": "outgoing",
  "relation_strength": "weak",
  "rank_penalty": 0.3
}
```

领域图谱可继续扩展具体关系。

文学图谱关系示例：

```text
BORN_INTO
MARRIED_TO
SIBLING_OR_COUSIN_OF
MEMBER_OF
LIVES_IN
PARTICIPATED_IN
ASSOCIATED_WITH_PLACE
EXPRESSES_CONCEPT
LEADS_TO
LISTED_IN
```

大规模人物与组织关系示例：

```text
SWORN_TO
PARENT_OF
SPOUSE
LED_BY
FOUNDED_BY
HEIR_TO
BRANCH_OF
IN_REGION
SEAT_OF
HAS_TITLE
HAS_ALIAS
HAS_CULTURE
APPEARS_IN
POV_IN
APPEARS_IN_TV_SEASON
PLAYED
HAS_ANCESTRAL_WEAPON
```

## Chunk 到图谱的正式形态

当前应向完整 GraphRAG 演进：

```text
原始文档 / 资料
  -> Document
  -> DocumentVersion
  -> Chunk
  -> Chunk embedding 写 Milvus
  -> graph_build_job 按 chunk batch 调用 GraphRAG LLM 进行结构化抽取
  -> 抽取失败时降级到 rule-based extractor
  -> Chunk -[:CHUNK_HAS_MENTION]-> Mention
  -> Mention -[:MENTION_REFERS_TO_ENTITY]-> Entity
  -> Entity -[:RELATION_SUBJECT]-> RelationFact
  -> RelationFact -[:RELATION_OBJECT]-> Entity
  -> RelationFact -[:RELATION_SUPPORTED_BY]-> Evidence
  -> Evidence -[:EVIDENCE_FROM_CHUNK]-> Chunk
  -> query embedding 搜 Chunk
  -> 从 Chunk 进入图谱扩展
  -> 汇总上下文给 LLM
```

## Mention、Entity、RelationFact、Evidence

必须区分 Mention 和 Entity：

```text
Mention = 原文里的某一次提及
Entity = 归一化后的真实实体
```

例如：

```text
广州星河科技有限公司
星河科技
甲方
该公司
```

这些是不同 Mention，但可能指向同一个 Entity。

关系事实推荐使用 `RelationFact` 节点，而不是只用实体之间的一条边。这样能管理多证据、版本、置信度、审核状态和冲突事实。

正式结构：

```text
(:Entity)-[:RELATION_SUBJECT]->(:RelationFact)
(:RelationFact)-[:RELATION_OBJECT]->(:Entity)
(:RelationFact)-[:RELATION_SUPPORTED_BY]->(:Evidence)
(:Evidence)-[:EVIDENCE_FROM_CHUNK]->(:Chunk)
```

`RelationFact` 示例：

```json
{
  "fact_id": "fact_01JXYZ",
  "predicate": "SIGNS_WITH",
  "scope_id": "doc_001",
  "confidence": 0.94,
  "status": "active",
  "extractor_version": "kg_extractor_v1"
}
```

`Evidence` 示例：

```json
{
  "evidence_id": "ev_01JXYZ",
  "source_chunk_id": "chk_001",
  "page_start": 1,
  "page_end": 1,
  "evidence_text": "甲方为广州星河科技有限公司，乙方为深圳蓝海贸易有限公司。",
  "confidence": 0.92
}
```

## 约束建议

```cypher
CREATE CONSTRAINT document_id_unique IF NOT EXISTS
FOR (d:Document)
REQUIRE d.doc_id IS UNIQUE;

CREATE CONSTRAINT doc_version_id_unique IF NOT EXISTS
FOR (v:DocumentVersion)
REQUIRE v.doc_version_id IS UNIQUE;

CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS
FOR (c:Chunk)
REQUIRE c.chunk_id IS UNIQUE;

CREATE CONSTRAINT mention_id_unique IF NOT EXISTS
FOR (m:Mention)
REQUIRE m.mention_id IS UNIQUE;

CREATE CONSTRAINT entity_id_unique IF NOT EXISTS
FOR (e:Entity)
REQUIRE e.entity_id IS UNIQUE;

CREATE CONSTRAINT fact_id_unique IF NOT EXISTS
FOR (f:RelationFact)
REQUIRE f.fact_id IS UNIQUE;

CREATE CONSTRAINT evidence_id_unique IF NOT EXISTS
FOR (e:Evidence)
REQUIRE e.evidence_id IS UNIQUE;
```

## 多个不连通子图

Neo4j 同一个 database 可以存在多个不连通子图。不同文档、不同知识库之间不必强行连成一个大图。

建议至少有管理关系：

```text
KnowledgeBase -> Document -> DocumentVersion -> Chunk
```

业务实体之间可以不连接。查询时通过 `knowledge_base_id`、`doc_id`、`doc_version_id`、`chunk_id` 控制范围。

## 图谱外部接口边界

图谱接口分为四层：

| 层 | 调用方 | 是否给模型 | 说明 |
| --- | --- | --- | --- |
| Connector | Runtime 后端代码 | 否 | Neo4j Driver 封装，执行参数化查询 |
| Runtime Tool | LLM 通过 Tool Executor | 是，只读 | 参数化图谱查询工具 |
| Internal Pipeline | 入库 Worker / Job Worker | 否 | 图谱写入、重建、修复 |
| Admin Debug | 管理员诊断接口 | 否 | 受限 readonly Cypher 和 schema 检查 |

P0 决策：

```text
普通大模型只能查知识图谱，不能写入知识图谱。
普通大模型不能调用 graph_write_batch_internal。
普通大模型不能调用自由 graph_readonly_query。
普通大模型只能调用参数化只读图谱工具。
```

允许模型调用的 P0 图谱工具：

| 工具 | 作用 |
| --- | --- |
| graph_schema_get | 查看允许的 label、relationship、属性摘要 |
| graph_entity_search | 按名称、别名、关键词查实体候选 |
| graph_expand_entity | 扩展单个实体 1 到 2 跳关系 |
| graph_find_relationship | 查询两个实体之间的直接关系 |
| graph_find_paths | 查询两个实体之间的受限多跳路径 |
| graph_get_evidence | 根据 fact_id / evidence_id 回源证据 |
| graph_timeline_query | 查询实体相关事件时间线 |

不允许模型调用的 P0 图谱能力：

| 能力 | 原因 | 替代 |
| --- | --- | --- |
| graph_write_batch_internal | 写入图谱，有副作用 | 只允许入库 pipeline 调用 |
| graph_readonly_query | 自由 Cypher 容易越权、超时、误查 | 使用参数化只读工具 |
| Neo4j Driver 连接 | 模型不能持有数据库凭证 | Runtime Connector 代理 |
| APOC / dbms / schema 管理 | 高风险管理能力 | 管理员离线执行 |

## 图谱工具

Neo4j 不进入模型内部，而是包装成 Agent 可调用工具。

建议工具：

| 工具 | 作用 |
| --- | --- |
| graph_schema_get | 返回当前知识库允许查询的 label、relationship、属性白名单 |
| graph_entity_search | 根据实体名称、别名或关键词查候选实体 |
| graph_expand_entity | 根据实体 ID 或名称扩展 1 到 2 跳关系 |
| graph_find_relationship | 查询两个实体之间的直接关系 |
| graph_find_paths | 查询两个实体之间的受限多跳路径 |
| graph_get_evidence | 根据 fact_id / evidence_id 读取证据和来源 chunk |
| graph_timeline_query | 查询事件时间线 |
| graphrag_search | 组合 Milvus、MinIO、Neo4j 的文本和图谱联合检索 |

安全原则：

- 优先使用只读账号。
- 普通模型不开放任意 Cypher。
- 禁止写入、删除、管理类语句。
- 限制 depth。
- 限制返回路径数量。
- 限制每类向量结果数量。
- 对 label 和 relationship 做白名单。
- 所有工具都必须带 `workspace_id` 和 `knowledge_base_id`。
- 所有工具都必须有 timeout、limit 和返回字段裁剪。
- 所有工具都必须返回 ToolResult，不直接返回数据库驱动原始对象。

禁止或强限制：

```text
CREATE
MERGE
SET
DELETE
DETACH DELETE
DROP
REMOVE
LOAD CSV
CALL dbms
CALL apoc
```

## 参数化图谱工具规格

图谱工具对模型暴露时必须按 LangChain Tool 注册。每个工具都要有 Pydantic args schema、`@tool` 包装、scope 校验、白名单过滤、ToolResult 返回和审计日志。

底层 Neo4j 查询不写在模型提示词里，也不让模型自由生成 Cypher；底层实现放在 `GraphQueryService` / `Neo4jConnector` 中，LangChain Tool 只负责接收结构化参数、调用受控服务、裁剪结果和写审计。

LangChain Tool 公共骨架：

```python
from langchain_core.tools import tool
from pydantic import BaseModel, Field

class GraphScopeArgs(BaseModel):
    workspace_id: str = Field(..., description="当前工作区 ID")
    knowledge_base_id: str = Field(..., description="当前知识库 ID")

def get_graph_services():
    return runtime_services.graph_query, runtime_services.minio, runtime_services.audit_log

def finish_graph_tool(tool_name: str, result: ToolResult, params_preview: dict | None = None) -> dict:
    runtime_services.audit_log.write_tool_event(
        tool_name,
        params_preview=redact(params_preview or {}),
        result_summary=summarize_result(result),
    )
    return result.to_dict()
```

### graph_schema_get

用途：让模型知道当前知识库里可以查询哪些实体类型、关系类型和属性，但不暴露数据库结构管理能力。

请求：

```json
{
  "workspace_id": "default",
  "knowledge_base_id": "kb_default"
}
```

返回：

```json
{
  "labels": ["Document", "DocumentVersion", "Chunk", "Mention", "Entity", "Person", "Event", "Place", "Concept", "Object", "RelationFact", "Evidence"],
  "relationships": ["CHUNK_HAS_MENTION", "MENTION_REFERS_TO_ENTITY", "RELATION_SUBJECT", "RELATION_OBJECT", "RELATION_SUPPORTED_BY", "EVIDENCE_FROM_CHUNK"],
  "properties": {
    "Entity": ["entity_id", "name", "aliases", "type"],
    "RelationFact": ["fact_id", "predicate", "confidence", "relation_strength", "rank_penalty"]
  },
  "allowed_depth": 2,
  "readonly": true
}
```

LangChain Tool 伪代码：

```python
class GraphSchemaGetArgs(GraphScopeArgs):
    pass

@tool("graph_schema_get", args_schema=GraphSchemaGetArgs)
def graph_schema_get_tool(workspace_id: str, knowledge_base_id: str) -> dict:
    """查看当前知识库允许模型使用的图谱 label、relationship 和属性白名单。"""
    graph_query, _, _ = get_graph_services()
    assert_scope(workspace_id, knowledge_base_id)
    schema = graph_query.get_schema_snapshot(workspace_id, knowledge_base_id)
    visible = redact_internal_schema_fields(schema)

    result = ToolResult.ok({
        "labels": visible.labels,
        "relationships": visible.relationships,
        "properties": visible.properties,
        "allowed_depth": 2,
        "readonly": True,
    })
    return finish_graph_tool("graph_schema_get", result)
```

### graph_entity_search

用途：把用户问题中的实体名称、别名或关键词解析成候选 `entity_id`。

请求：

```json
{
  "workspace_id": "default",
  "knowledge_base_id": "kb_default",
  "query": "林黛玉",
  "entity_types": ["Person"],
  "limit": 10,
  "include_aliases": true
}
```

返回：

```json
{
  "entities": [
    {
      "entity_id": "ent_001",
      "labels": ["Entity", "Person"],
      "name": "林黛玉",
      "aliases": ["黛玉", "潇湘妃子"],
      "match_type": "exact_name",
      "score": 1.0,
      "evidence_count": 12
    }
  ],
  "warnings": []
}
```

LangChain Tool 伪代码：

```python
class GraphEntitySearchArgs(GraphScopeArgs):
    query: str = Field(..., description="实体名称、别名或关键词")
    entity_types: list[str] = Field(default_factory=list, description="允许查询的实体类型")
    limit: int = Field(default=10, ge=1, le=20)
    include_aliases: bool = True

@tool("graph_entity_search", args_schema=GraphEntitySearchArgs)
def graph_entity_search_tool(
    workspace_id: str,
    knowledge_base_id: str,
    query: str,
    entity_types: list[str] | None = None,
    limit: int = 10,
    include_aliases: bool = True,
) -> dict:
    """搜索图谱实体候选，只返回只读实体摘要和匹配证据数量。"""
    graph_query, _, _ = get_graph_services()
    assert_scope(workspace_id, knowledge_base_id)
    normalized_query = normalize_text(query)
    labels = filter_allowed_labels(entity_types or [])
    safe_limit = clamp(limit, 1, 20)

    exact_hits = graph_query.find_entities_by_exact_name(
        workspace_id=workspace_id,
        knowledge_base_id=knowledge_base_id,
        name=normalized_query,
        labels=labels,
        limit=safe_limit,
    )
    alias_hits = []
    if include_aliases:
        alias_hits = graph_query.find_entities_by_alias(
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            alias=normalized_query,
            labels=labels,
            limit=safe_limit,
        )
    fuzzy_hits = graph_query.find_entities_by_keyword(
        workspace_id=workspace_id,
        knowledge_base_id=knowledge_base_id,
        keyword=normalized_query,
        labels=labels,
        limit=safe_limit,
    )

    merged = merge_rank_dedupe([exact_hits, alias_hits, fuzzy_hits], key="entity_id")
    result = ToolResult.ok({"entities": merged[:safe_limit], "warnings": []})
    return finish_graph_tool("graph_entity_search", result, {"query": query, "entity_types": entity_types})
```

### graph_expand_entity

用途：围绕一个实体扩展 1 到 2 跳关系，返回方向、强弱、证据引用。

请求：

```json
{
  "workspace_id": "default",
  "knowledge_base_id": "kb_default",
  "entity_id": "ent_001",
  "depth": 2,
  "relationship_allowlist": ["PARTICIPATED_IN", "MEMBER_OF", "LIVES_IN"],
  "limit": 30,
  "include_evidence": true
}
```

返回：

```json
{
  "start_entity": {"entity_id": "ent_001", "name": "林黛玉"},
  "paths": [
    {
      "path_id": "path_001",
      "depth": 1,
      "nodes": [
        {"entity_id": "ent_001", "name": "林黛玉", "labels": ["Person"]},
        {"entity_id": "ent_event_001", "name": "海棠诗社结社", "labels": ["Event"]}
      ],
      "relationships": [
        {
          "type": "PARTICIPATED_IN",
          "direction": "outgoing",
          "relation_strength": "strong",
          "rank_penalty": 0,
          "fact_id": "fact_001",
          "evidence_ids": ["ev_001"]
        }
      ],
      "score": 0.91
    }
  ],
  "warnings": []
}
```

LangChain Tool 伪代码：

```python
class GraphExpandEntityArgs(GraphScopeArgs):
    entity_id: str
    depth: int = Field(default=1, ge=1, le=2)
    relationship_allowlist: list[str] = Field(default_factory=list)
    limit: int = Field(default=30, ge=1, le=50)
    include_evidence: bool = True

@tool("graph_expand_entity", args_schema=GraphExpandEntityArgs)
def graph_expand_entity_tool(
    workspace_id: str,
    knowledge_base_id: str,
    entity_id: str,
    depth: int = 1,
    relationship_allowlist: list[str] | None = None,
    limit: int = 30,
    include_evidence: bool = True,
) -> dict:
    """扩展单个实体周边 1 到 2 跳关系，返回方向、强弱和 evidence 引用。"""
    graph_query, _, _ = get_graph_services()
    assert_scope(workspace_id, knowledge_base_id)
    safe_depth = clamp(depth, 1, 2)
    safe_limit = clamp(limit, 1, 50)
    rels = filter_allowed_relationships(relationship_allowlist or [])

    entity = graph_query.resolve_entity_id_or_fail(entity_id, workspace_id, knowledge_base_id)
    raw_paths = graph_query.expand_entity(
        workspace_id=workspace_id,
        knowledge_base_id=knowledge_base_id,
        entity_id=entity.entity_id,
        depth=safe_depth,
        relationships=rels,
        limit=safe_limit * 3,
        include_evidence=include_evidence,
    )

    paths = []
    for path in raw_paths:
        normalized = preserve_direction(path)
        normalized.score = rank_path(
            confidence=path.fact_confidence,
            relation_strength=path.relation_strength,
            rank_penalty=path.rank_penalty,
            depth=path.depth,
        )
        paths.append(normalized)

    result = ToolResult.ok({"start_entity": entity.summary(), "paths": top_k(paths, safe_limit)})
    return finish_graph_tool("graph_expand_entity", result, {"entity_id": entity_id, "depth": safe_depth})
```

### graph_find_relationship

用途：查询两个实体之间是否存在直接关系。

请求：

```json
{
  "workspace_id": "default",
  "knowledge_base_id": "kb_default",
  "source_entity": "林黛玉",
  "target_entity": "贾宝玉",
  "relationship_allowlist": ["PARTICIPATED_IN", "KNOWS", "RELATION_SUBJECT"],
  "include_evidence": true
}
```

返回：

```json
{
  "source": {"entity_id": "ent_001", "name": "林黛玉"},
  "target": {"entity_id": "ent_002", "name": "贾宝玉"},
  "relationships": [
    {
      "type": "PARTICIPATED_IN",
      "direction": "source_to_target_via_fact",
      "fact_id": "fact_010",
      "confidence": 0.92,
      "evidence_ids": ["ev_010"]
    }
  ],
  "ok": true,
  "empty": false
}
```

LangChain Tool 伪代码：

```python
class GraphFindRelationshipArgs(GraphScopeArgs):
    source_entity: str
    target_entity: str
    relationship_allowlist: list[str] = Field(default_factory=list)
    include_evidence: bool = True

@tool("graph_find_relationship", args_schema=GraphFindRelationshipArgs)
def graph_find_relationship_tool(
    workspace_id: str,
    knowledge_base_id: str,
    source_entity: str,
    target_entity: str,
    relationship_allowlist: list[str] | None = None,
    include_evidence: bool = True,
) -> dict:
    """查询两个实体之间的直接关系。遇到歧义实体时返回候选，不让模型猜。"""
    graph_query, _, _ = get_graph_services()
    assert_scope(workspace_id, knowledge_base_id)
    source = graph_query.resolve_entity(source_entity, workspace_id, knowledge_base_id)
    target = graph_query.resolve_entity(target_entity, workspace_id, knowledge_base_id)
    if source.is_ambiguous or target.is_ambiguous:
        result = ToolResult.ok({"needs_disambiguation": True, "candidates": source.candidates + target.candidates})
        return finish_graph_tool("graph_find_relationship", result, {"source_entity": source_entity, "target_entity": target_entity})

    relationships = graph_query.find_direct_relationships(
        workspace_id=workspace_id,
        knowledge_base_id=knowledge_base_id,
        source_entity_id=source.entity_id,
        target_entity_id=target.entity_id,
        relationships=filter_allowed_relationships(relationship_allowlist or []),
        limit=20,
        include_evidence=include_evidence,
    )
    result = ToolResult.ok({
        "source": source.summary(),
        "target": target.summary(),
        "relationships": rank_relationships(relationships),
        "ok": True,
        "empty": len(relationships) == 0,
    })
    return finish_graph_tool("graph_find_relationship", result, {"source_entity": source_entity, "target_entity": target_entity})
```

### graph_find_paths

用途：查询两个实体之间的受限多跳路径。P0 最大深度为 2，避免路径爆炸。

请求：

```json
{
  "workspace_id": "default",
  "knowledge_base_id": "kb_default",
  "source_entity": "林黛玉",
  "target_entity": "贾宝玉",
  "max_depth": 2,
  "limit": 10,
  "relationship_allowlist": ["PARTICIPATED_IN", "MEMBER_OF", "LIVES_IN"]
}
```

LangChain Tool 伪代码：

```python
class GraphFindPathsArgs(GraphScopeArgs):
    source_entity: str
    target_entity: str
    max_depth: int = Field(default=2, ge=1, le=2)
    relationship_allowlist: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=20)

@tool("graph_find_paths", args_schema=GraphFindPathsArgs)
def graph_find_paths_tool(
    workspace_id: str,
    knowledge_base_id: str,
    source_entity: str,
    target_entity: str,
    max_depth: int = 2,
    relationship_allowlist: list[str] | None = None,
    limit: int = 10,
) -> dict:
    """查询两个实体之间的受限多跳路径。P0 最大深度固定为 2。"""
    graph_query, _, _ = get_graph_services()
    assert_scope(workspace_id, knowledge_base_id)
    safe_depth = clamp(max_depth, 1, 2)
    safe_limit = clamp(limit, 1, 20)
    source = graph_query.resolve_entity(source_entity, workspace_id, knowledge_base_id)
    target = graph_query.resolve_entity(target_entity, workspace_id, knowledge_base_id)
    if source.is_ambiguous or target.is_ambiguous:
        result = ToolResult.ok({"needs_disambiguation": True, "candidates": source.candidates + target.candidates})
        return finish_graph_tool("graph_find_paths", result, {"source_entity": source_entity, "target_entity": target_entity})
    rels = filter_allowed_relationships(relationship_allowlist or [])

    paths = graph_query.find_paths(
        workspace_id=workspace_id,
        knowledge_base_id=knowledge_base_id,
        source_entity_id=source.entity_id,
        target_entity_id=target.entity_id,
        max_depth=safe_depth,
        relationship_allowlist=rels,
        limit=safe_limit * 3,
    )
    paths = [preserve_direction(path) for path in paths]
    paths = rank_and_drop_search_only_paths(paths)
    result = ToolResult.ok({"paths": paths[:safe_limit], "empty": len(paths) == 0})
    return finish_graph_tool("graph_find_paths", result, {"source_entity": source_entity, "target_entity": target_entity})
```

### graph_get_evidence

用途：根据关系事实或证据 ID 回源证据，必要时从 MinIO 读取 chunk 正文。

请求：

```json
{
  "workspace_id": "default",
  "knowledge_base_id": "kb_default",
  "fact_ids": ["fact_001"],
  "evidence_ids": [],
  "include_chunk_text": true,
  "max_chars_per_chunk": 1200
}
```

LangChain Tool 伪代码：

```python
class GraphGetEvidenceArgs(GraphScopeArgs):
    fact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    include_chunk_text: bool = True
    max_chars_per_chunk: int = Field(default=1200, ge=200, le=4000)

@tool("graph_get_evidence", args_schema=GraphGetEvidenceArgs)
def graph_get_evidence_tool(
    workspace_id: str,
    knowledge_base_id: str,
    fact_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    include_chunk_text: bool = True,
    max_chars_per_chunk: int = 1200,
) -> dict:
    """根据 fact_id / evidence_id 回源图谱证据，必要时从 MinIO 读取 chunk 正文。"""
    graph_query, minio, _ = get_graph_services()
    assert_scope(workspace_id, knowledge_base_id)
    evidence_refs = graph_query.get_evidence_refs(
        workspace_id=workspace_id,
        knowledge_base_id=knowledge_base_id,
        fact_ids=fact_ids or [],
        evidence_ids=evidence_ids or [],
        limit=50,
    )

    results = []
    for ev in evidence_refs:
        item = ev.to_dict()
        if include_chunk_text:
            chunk = minio.read_json(ev.chunk_object_key)
            item["chunk_text"] = truncate(chunk["text"], max_chars_per_chunk)
            item["source"] = chunk["source"]
        results.append(item)

    result = ToolResult.ok({"evidence": results})
    return finish_graph_tool("graph_get_evidence", result, {"fact_ids": fact_ids, "evidence_ids": evidence_ids})
```

### graph_timeline_query

用途：查询某个实体相关事件，按时间排序。

LangChain Tool 伪代码：

```python
class GraphTimelineQueryArgs(GraphScopeArgs):
    entity: str
    date_from: str | None = None
    date_to: str | None = None
    limit: int = Field(default=20, ge=1, le=50)

@tool("graph_timeline_query", args_schema=GraphTimelineQueryArgs)
def graph_timeline_query_tool(
    workspace_id: str,
    knowledge_base_id: str,
    entity: str,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> dict:
    """查询某个实体相关事件，按时间排序。"""
    graph_query, _, _ = get_graph_services()
    assert_scope(workspace_id, knowledge_base_id)
    resolved = graph_query.resolve_entity(entity, workspace_id, knowledge_base_id)
    events = graph_query.find_events_for_entity(
        workspace_id=workspace_id,
        knowledge_base_id=knowledge_base_id,
        entity_id=resolved.entity_id,
        date_from=date_from,
        date_to=date_to,
        limit=clamp(limit, 1, 50),
    )
    result = ToolResult.ok({"entity": resolved.summary(), "events": sort_by_event_time(events)})
    return finish_graph_tool("graph_timeline_query", result, {"entity": entity})
```

## 内部写入接口

`graph_write_batch_internal` 是内部接口，只允许入库 pipeline 或受控后台 Job 调用。

调用条件：

- `caller_type` 必须是 `ingestion_pipeline`、`graph_build_job`、`graph_rebuild_job` 或 `admin_maintenance_job`。
- 必须带 `operation_id`。
- 必须带 `workspace_id`、`knowledge_base_id`、`doc_id`、`doc_version_id`。
- 必须先写入 MinIO staging 产物，再写 Neo4j。
- 必须关联 `job_id` 或内部 pipeline trace；P0 图谱构建和重建由 `graph_build_job` / `graph_rebuild_job` 推进。
- 写入失败必须用 `operation_id` 去重重试。
- 普通模型 tool inventory 中不得出现 `graph_write_batch_internal`。

伪代码：

```python
def graph_write_batch_internal(req):
    assert req.caller_type in {
        "ingestion_pipeline",
        "graph_build_job",
        "graph_rebuild_job",
        "admin_maintenance_job",
    }
    assert_operation_id(req.operation_id)
    assert_scope(req.workspace_id, req.knowledge_base_id)
    validate_graph_batch_schema(req.nodes, req.relationships)

    if operation_log.exists(req.operation_id, status="committed"):
        return {"ok": True, "deduped": True}

    operation_log.write_pending(req.operation_id)
    try:
        with neo4j.write_transaction() as tx:
            upsert_documents(tx, req.documents)
            upsert_chunks(tx, req.chunks)
            upsert_mentions(tx, req.mentions)
            upsert_entities(tx, req.entities)
            upsert_relation_facts(tx, req.relation_facts)
            upsert_evidence(tx, req.evidence)
        operation_log.write_committed(req.operation_id)
        return {"ok": True, "deduped": False}
    except RetryableNeo4jError as exc:
        operation_log.write_failed(req.operation_id, retryable=True, error=redact(exc))
        raise
    except Exception as exc:
        operation_log.write_failed(req.operation_id, retryable=False, error=redact(exc))
        raise
```

## 受限 Admin 查询

`graph_readonly_query` 只用于管理员诊断和调试页，不进入普通模型工具清单。

伪代码：

```python
def graph_readonly_query_admin(req):
    assert_admin_user(req.user_id)
    assert_scope(req.workspace_id, req.knowledge_base_id)
    cypher = req.cypher.strip()
    reject_if_contains_write_or_admin_keyword(cypher)
    reject_if_missing_limit(cypher, default_limit=100)
    reject_if_uses_unapproved_label_or_relationship(cypher)
    return neo4j.readonly_query(cypher, params=req.params, timeout=5)
```

## GraphRAG 工具返回格式

GraphRAG 工具输出应包含：

```text
问题
Embedding 信息
向量检索命中节点
图谱扩展路径
原始 JSON
```

Embedding 信息：

```json
{
  "provider": "openai_compatible",
  "model": "text-embedding-v4",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "dimension": 1024
}
```

向量命中字段：

| 字段 | 说明 |
| --- | --- |
| rank | 排名 |
| labels | 节点标签 |
| name | 节点名 |
| score | 相似度 |
| text | embeddingText 或摘要 |

图谱扩展路径字段：

```json
{
  "start": "林黛玉",
  "relationships": [
    {
      "type": "MEMBER_OF",
      "direction": "outgoing",
      "relation_strength": "weak",
      "rank_penalty": 0.3,
      "target": "海棠诗社"
    }
  ],
  "depth": 2
}
```

输出时要关注：

- 关键实体是否被召回。
- 分数是否区分明显。
- 是否出现大量弱相关人物。
- 关系方向是否容易误导。
- 是否返回太多弱关系。
- 是否缺少关键关系。
- `embeddingText` 是否信息不足。

## 第一版关系排序边界

P0 已确认：

- 图谱路径展示必须显示方向。
- 区分强关系和弱关系。
- 对 `LIVES_IN`、`MEMBER_OF`、`LISTED_IN` 等弱关系默认降权。
- 不做专门的复杂关系权重配置页面。

后续可扩展：

- 每个知识库自定义关系权重。
- 不同文档类型使用不同关系权重。
- 图谱评估结果反向调整权重。
- 第一版是只读扩展，还是完整图谱构建和可视化一起做。
