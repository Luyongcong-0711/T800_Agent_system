# 文档预处理、分块、知识图谱抽取与实体归一化设计文档

> 本文档整理当前讨论过的原始文件预处理、Document Representation、结构化/语义切块、chunk embedding、chunk-level 实体关系抽取、实体归一化、关系合并、跨 chunk 补全、Neo4j 图谱构建和质量控制流程。

---

## 1. 总体流程

推荐主流程：

```text
Step 1. 原始文件解析
  PDF / DOCX / HTML / Excel / TXT / 图片 → Document Representation

Step 2. 文档级 metadata 抽取
  title / type / author / date / summary / high-level entities / roles

Step 3. 结构化/语义切块
  按标题、段落、条款、表格、页码、token 限制切
  使用 overlap / parent-child / section context

Step 4. chunk embedding
  每个 chunk 生成向量
  写 Milvus

Step 5. chunk-level entity/relation extraction
  输入 chunk_text + section_title + doc_metadata + parent context
  输出 mentions / entity candidates / relation candidates / evidence

Step 6. local graph fragments
  每个 chunk 形成局部小图，只作为中间结果

Step 7. entity resolution
  判断不同 mention 是否指向同一个真实实体
  分配 canonical entity_id

Step 8. relation normalization / merging
  统一关系类型
  合并重复事实
  保留多证据

Step 9. cross-chunk enrichment
  基于相邻 chunk、同 section、共享实体、未解析 mention、低置信度关系做补全

Step 10. graph construction
  写 Neo4j：Document → Chunk → Mention → Entity → RelationFact → Evidence

Step 11. provenance
  每个实体、mention、关系都挂 source_chunk_id / page / offset / extractor_version / confidence

Step 12. status commit
  更新 MinIO 文档 manifest、chunks 索引和 documents_index，标记 indexed
```

图示：

```text
原始文件
  ↓
Document Representation
  ↓
文档级 metadata / 高层实体抽取 ─────────────┐
  ↓                                      │
结构化/语义切块                            │
  ↓                                      │
chunk + section context + doc metadata    │
  ↓                                      │
┌───────────────────────┬────────────────────────┐
│ chunk embedding        │ chunk 局部实体关系抽取   │
│ → Milvus               │ → local graph fragments │
└───────────────────────┴────────────────────────┘
                          ↓
                 实体归一化 / 关系合并
                          ↓
                   Neo4j 全局图谱
                          ↓
                  provenance / source trace
                          ↓
                      可查询知识库
```

---

## 2. 文件预处理方案

已确定的 parser 策略：

```text
默认：Docling
PDF / 扫描 / 复杂版面：MinerU
特别奇怪或长尾格式：Apache Tika 兜底
```

### 2.1 Parser Router

推荐建立 Parser Router，而不是所有文件都走一个解析器。

```text
上传文件
  ↓
识别 MIME / 后缀 / 文件特征
  ↓
if PDF:
    优先 MinerU
    如果失败或质量低，再尝试 Docling
    仍失败，Tika 兜底抽纯文本

elif DOCX / PPTX / HTML / Markdown:
    优先 Docling
    失败后 Tika 兜底

elif Excel / CSV:
    使用专门表格解析逻辑
    必要时 Tika 兜底

elif 图片 / 扫描件:
    OCR / MinerU

else:
    Tika 兜底抽 text + metadata
```

### 2.2 为什么需要 Document Representation

原始文件格式差异很大：

```text
DOCX:
  段落、标题、表格、页眉页脚、批注、编号、换行、空格

PDF:
  版面、页码、分栏、跨页段落、表格、页眉页脚、扫描 OCR

HTML:
  DOM、导航栏、广告、正文抽取、链接、标题层级

Excel:
  sheet、表头、单元格、合并单元格、公式、单位

Markdown:
  标题、列表、表格、代码块、引用

图片:
  OCR、版面识别、文字块顺序
```

如果直接把这些文件转成纯文本，会丢失很多结构。

Document Representation 是系统内部标准中间格式，用来统一表示解析后的文档。

### 2.3 Document Representation 示例

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
    },
    {
      "block_id": "blk_003",
      "type": "table",
      "text": "付款计划表...",
      "rows": [],
      "page_start": 2,
      "page_end": 2
    }
  ]
}
```

### 2.4 文件预处理阶段需要处理的问题

#### 2.4.1 换行和空格

Word / PDF 转文本后经常出现：

```text
一 行 被 强 行 换 开
多余空格
段落断裂
页眉页脚混入正文
目录混入正文
```

需要处理：

```text
1. 合并同一段落中的硬换行
2. 保留标题和列表换行
3. 去除重复空白
4. 去除页眉页脚
5. 修复被断开的中文句子
6. 保留表格结构，不要简单拼成一行
```

#### 2.4.2 页码和来源位置

必须尽量保存：

```text
page_start
page_end
char_start
char_end
block_id
section_path
```

后续用于：

- 引用来源
- 回看原文
- 图谱 evidence
- chunk 更新
- debug 抽取错误

#### 2.4.3 表格

表格不能简单转成无结构文本。

推荐保存两种表示：

```text
1. Markdown 表格，供 LLM 阅读
2. JSON rows，供程序处理
```

示例：

```json
{
  "type": "table",
  "caption": "付款计划表",
  "columns": ["期数", "金额", "日期"],
  "rows": [
    ["第一期", "30万元", "2026-01-01"],
    ["第二期", "70万元", "2026-06-01"]
  ]
}
```

---

## 3. 文档级 metadata 抽取

文档级抽取在 chunk 前执行，目标是建立全局上下文。

### 3.1 抽取内容

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

### 3.2 文档角色映射

合同示例：

```text
甲方：广州星河科技有限公司
乙方：深圳蓝海贸易有限公司
```

文档级 metadata：

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

这个 metadata 后续用于实体归一化。

例如 chunk 里出现：

```text
乙方应于2026年6月30日前完成交付。
```

可以解析为：

```text
乙方 → 深圳蓝海贸易有限公司
```

这一步不一定需要 LLM，规则即可处理。

---

## 4. 结构化/语义切块

### 4.1 定义

“语义切块”是一个统称，意思是：

```text
切块时尽量按照文本语义和文档结构切，而不是机械地每 N 个 token 切一刀。
```

它包括很多策略：

- 标题切块
- 段落切块
- 条款切块
- 表格切块
- 递归切块
- overlap
- parent-child chunking
- section context

### 4.2 机械切块

```text
每 800 token 切一块，overlap 100 token
```

优点：

- 简单
- 稳定
- 适合纯文本

缺点：

- 可能切断句子
- 可能切断条款
- 可能把实体和关系切开
- 可能破坏表格
- 不利于图谱抽取

### 4.3 结构化切块

按文档结构切。

合同示例：

```text
第一条 合同主体
第二条 合同金额
第三条 付款方式
第四条 交付时间
```

切成：

```text
chunk_1 = 第一条 合同主体
chunk_2 = 第二条 合同金额
chunk_3 = 第三条 付款方式
chunk_4 = 第四条 交付时间
```

产品手册示例：

```text
1. 产品概述
2. 技术参数
3. 安装方式
4. API 接口
```

切成对应功能模块。

### 4.4 递归切块

推荐策略：

```text
先按章节切
如果章节太长，再按小标题切
如果小标题还太长，再按段落切
如果段落还太长，再按句子/token 切
```

流程：

```text
Document
  ↓
Section
  ↓
Subsection
  ↓
Paragraph
  ↓
Sentence / token
```

### 4.5 Overlap

为了防止上下文被切断，可以让相邻 chunk 重叠。

```text
chunk_1: A B C D
chunk_2: D E F G
chunk_3: G H I J
```

适合解决：

- 关系跨 chunk 边界
- 前一句定义主体，后一句描述关系
- 表格上下文跨段

### 4.6 Parent-child chunking

同时保留大块和小块。

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

### 4.7 推荐 chunk 字段

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

---

## 5. chunk embedding

每个 chunk 生成 embedding，写入 Milvus。

注意：

```text
embedding 是向量表示，用于语义相似搜索。
实体关系不是从 embedding 中抽取，而是从 chunk 文本中抽取。
```

错误理解：

```text
chunk → embedding → 从 embedding 抽实体关系  ❌
```

正确理解：

```text
chunk text → embedding → Milvus
chunk text → entity/relation extraction → Neo4j
```

Milvus 和 Neo4j 用 `chunk_id` 对齐。

---

## 6. chunk-level 实体关系抽取

### 6.1 抽取输入

不要只给 LLM 当前 chunk。

推荐输入：

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

示例：

```text
文档标题：采购合同001
文档类型：合同
文档级 metadata：
  甲方 = 广州星河科技有限公司
  乙方 = 深圳蓝海贸易有限公司
章节标题：第三条 交付
当前 chunk：乙方应于2026年6月30日前完成设备交付。
```

这样模型可以抽出：

```text
深圳蓝海贸易有限公司 -[DELIVERS_BEFORE]-> 2026年6月30日
```

而不是停留在：

```text
乙方 -[DELIVERS_BEFORE]-> 2026年6月30日
```

### 6.2 抽取输出

每个 chunk 抽取：

```text
mentions
entity_candidates
relation_candidates
evidence
```

示例：

```json
{
  "chunk_id": "chk_001",
  "mentions": [
    {
      "mention_id": "men_001",
      "surface": "甲方",
      "type": "Role",
      "normalized_hint": "广州星河科技有限公司",
      "start_offset": 0,
      "end_offset": 2
    },
    {
      "mention_id": "men_002",
      "surface": "广州星河科技有限公司",
      "type": "Organization"
    },
    {
      "mention_id": "men_003",
      "surface": "乙方",
      "type": "Role",
      "normalized_hint": "深圳蓝海贸易有限公司"
    },
    {
      "mention_id": "men_004",
      "surface": "深圳蓝海贸易有限公司",
      "type": "Organization"
    }
  ],
  "relations": [
    {
      "subject_mention": "广州星河科技有限公司",
      "predicate": "SIGNS_WITH",
      "object_mention": "深圳蓝海贸易有限公司",
      "confidence": 0.92,
      "evidence_text": "甲方为广州星河科技有限公司，乙方为深圳蓝海贸易有限公司。"
    }
  ]
}
```

---

## 7. local graph fragments

每个 chunk 抽取后形成一个局部小图。

例如：

```text
chunk_1 小图:
  广州星河科技有限公司 -[SIGNS_WITH]-> 深圳蓝海贸易有限公司

chunk_2 小图:
  本合同 -[HAS_AMOUNT]-> 人民币100万元

chunk_3 小图:
  乙方 -[DELIVERS_BEFORE]-> 2026年6月30日
```

注意：

```text
局部小图只是中间结果，不是最终知识图谱。
```

如果直接把每个 chunk 小图写入最终图，会产生很多脏节点：

```text
甲方
乙方
该公司
本合同
星河科技
广州星河科技有限公司
```

这些其实可能指向相同实体。

---

## 8. Mention 与 Entity

必须区分 Mention 和 Entity。

```text
Mention = 原文里的某一次提及
Entity  = 归一化后的真实实体
```

例子：

```text
chunk_1: 广州星河科技有限公司
chunk_2: 星河科技
chunk_3: 甲方
chunk_4: 该公司
```

这些是不同 mention，但可能指向同一个 entity：

```text
Entity:
  entity_id = ent_org_xinghe
  canonical_name = 广州星河科技有限公司
  type = Organization
  aliases = ["星河科技", "甲方", "该公司"]
```

Neo4j 推荐模型：

```text
(:Chunk)-[:HAS_MENTION]->(:Mention)-[:REFERS_TO]->(:Entity)
```

---

## 9. 实体归一化 Entity Resolution

### 9.1 目标

实体归一化目标：

```text
判断不同 mention 是否指向同一个真实实体，并分配同一个 entity_id。
```

例如：

```text
甲方
星河科技
广州星河科技有限公司
该公司
```

归一到：

```text
ent_org_xinghe
```

### 9.2 不建议完全依赖 LLM

实体归一化不是只靠大模型，也不是只靠字符串匹配。

推荐多阶段 pipeline：

```text
规则/字段匹配
  ↓
候选实体召回
  ↓
打分排序
  ↓
高置信度自动合并
  ↓
中置信度交给 LLM 判断
  ↓
低置信度新建实体或进入人工审核
```

### 9.3 Entity Resolver 模块

输入：

```json
{
  "mention": {
    "surface": "乙方",
    "type": "Role",
    "chunk_id": "chk_007",
    "doc_id": "doc_contract_001",
    "context": "乙方应于2026年6月30日前完成交付。"
  },
  "doc_metadata": {
    "roles": {
      "甲方": "广州星河科技有限公司",
      "乙方": "深圳蓝海贸易有限公司"
    }
  }
}
```

输出：

```json
{
  "decision": "MATCH",
  "entity_id": "ent_org_lanhai",
  "canonical_name": "深圳蓝海贸易有限公司",
  "confidence": 0.98,
  "decision_source": "doc_role_mapping",
  "reason": "文档级 metadata 中乙方对应深圳蓝海贸易有限公司"
}
```

### 9.4 decision 字段

```text
MATCH        匹配到已有实体
NEW_ENTITY   新建实体
REVIEW       不确定，进入人工审核
IGNORE       忽略，不入图
MERGE        确认两个已有实体应合并
SPLIT        发现之前合错，需要拆分
```

### 9.5 decision_source 字段

```text
strong_id              强 ID 匹配，例如统一社会信用代码
exact_name             标准化名称完全匹配
alias_rule             别名规则匹配
doc_role_mapping       文档角色映射
fuzzy_score            模糊匹配得分
embedding_similarity   上下文向量相似
llm                    LLM 判断
human                  人工审核
```

### 9.6 confidence 来源

`confidence` 不是绝对真理概率，而是系统内部置信度。

可以来自：

#### 固定规则置信度

```python
SOURCE_CONFIDENCE = {
    "strong_id": 1.00,
    "human": 1.00,
    "doc_role_mapping": 0.98,
    "document_alias": 0.96,
    "exact_name_same_doc": 0.94,
    "exact_name_global": 0.92,
    "global_alias": 0.90
}
```

如果命中文档角色映射：

```text
乙方 → 深圳蓝海贸易有限公司
confidence = 0.98
```

#### 加权打分

当没有强规则时，可以打分：

```python
def score_candidate(mention, candidate):
    score = 0.0

    if mention.type == candidate.type:
        score += 0.10

    if normalize(mention.surface) == normalize(candidate.canonical_name):
        score += 0.60

    if mention.surface in candidate.aliases:
        score += 0.35

    if mention.doc_id in candidate.seen_doc_ids:
        score += 0.10

    if context_is_compatible(mention.context, candidate):
        score += 0.10

    return min(score, 1.0)
```

阈值：

```text
score >= 0.90:
  自动 MATCH

0.70 <= score < 0.90:
  LLM 判断或人工审核

score < 0.70:
  NEW_ENTITY
```

### 9.7 实体归一化流程

```text
mention
  ↓
normalize surface
  ↓
strong identifier match
  ↓
document role mapping
  ↓
alias match
  ↓
candidate retrieval
  ↓
candidate scoring
  ↓
decision:
  高分 → 自动合并
  中分 → LLM 判断
  低分 → 新建实体
  高风险 → 人工审核
```

### 9.8 强 ID 匹配

如果有强 ID，优先合并：

```text
统一社会信用代码
税号
邮箱
手机号
SKU
合同编号
订单号
发票号
域名
```

例如：

```text
统一社会信用代码相同 → 同一公司
SKU 相同 → 同一商品
合同编号相同 → 同一合同
```

### 9.9 alias 和 scope

别名要区分全局和局部。

```text
global alias:
  星河科技 → 广州星河科技有限公司

document-scoped alias:
  甲方 → 广州星河科技有限公司，只在 doc_contract_001 内有效
```

非常重要：

```text
不要把所有文档里的“甲方”全局合并到同一个实体。
```

### 9.10 LLM 的作用

LLM 不应该处理全量 mention。

适合处理：

```text
代词 / 指代：该公司、其、上述供应商
多候选歧义：星河科技可能是多个公司
跨段上下文判断
规则分数中等但不确定
```

LLM 输入应受控：

```text
mention: 星河科技
context: 甲方广州星河科技有限公司，以下简称“星河科技”
candidates:
  1. ent_001 广州星河科技有限公司
  2. ent_002 北京星河科技股份有限公司
  3. NEW_ENTITY

请只返回候选 ID 或 NEW_ENTITY。
```

---

## 10. 关系标准化 Relation Normalization

### 10.1 目标

把不同表达映射成统一 predicate。

原始表达：

```text
签署
签约
达成合同
合同相对方
与其签订协议
```

标准关系：

```text
SIGNS_WITH
```

### 10.2 先定义关系 schema

合同领域示例：

```text
HAS_PARTY_A
HAS_PARTY_B
SIGNS_WITH
HAS_AMOUNT
HAS_PAYMENT_TERM
DELIVERS_BEFORE
HAS_PENALTY
GOVERNED_BY
```

产品领域示例：

```text
HAS_FEATURE
SUPPORTS_PROTOCOL
HAS_PARAMETER
COMPATIBLE_WITH
DEPENDS_ON
BELONGS_TO_PRODUCT_LINE
```

抽取时应要求模型只使用允许的关系类型。

### 10.3 标准化示例

原始关系：

```json
{
  "subject": "甲方",
  "predicate_text": "与乙方签订合同",
  "object": "乙方"
}
```

标准化后：

```json
{
  "subject_entity_id": "ent_org_xinghe",
  "predicate": "SIGNS_WITH",
  "object_entity_id": "ent_org_lanhai",
  "source_chunk_id": "chk_001"
}
```

---

## 11. 关系合并 Relation Merging

### 11.1 目标

多个 chunk 抽到同一事实时，不要生成重复关系，而是合并成一条全局事实，并保留多个证据。

### 11.2 合并 key

基础合并 key：

```text
subject_entity_id + predicate + object_entity_id
```

更正式：

```text
subject_entity_id + predicate + object_entity_id + scope_id
```

`scope_id` 可以是：

```text
doc_id
contract_id
event_id
valid_time
project_id
```

避免把不同合同里的金额、义务、期限错误合并。

### 11.3 Evidence

每条关系事实都应保留 evidence。

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

更正式的 Neo4j 模型：

```text
(:Entity)-[:RELATION_SUBJECT]->(:RelationFact)
(:RelationFact)-[:RELATION_OBJECT]->(:Entity)
(:RelationFact)-[:SUPPORTED_BY]->(:Evidence)
(:Evidence)-[:FROM_CHUNK]->(:Chunk)
```

---

## 12. 跨 chunk 关系补全

### 12.1 不要暴力两两比较 chunk

不建议：

```text
for chunk_i in chunks:
  for chunk_j in chunks:
    判断是否有关系
```

成本高、噪音大。

### 12.2 推荐补全范围

#### 相邻 chunk

```text
(chunk_i, chunk_i+1)
```

用于解决边界切断问题。

#### 同 section chunk

只在同一章节内补关系。

```text
第二条 合同金额
  chunk_5
  chunk_6
  chunk_7
```

#### 共享实体 chunk

如果两个 chunk 都提到同一实体，可以考虑补关系。

```text
chunk_2 提到 产品A
chunk_9 也提到 产品A
```

#### 未解析 mention 驱动

只对有这些词的 chunk 做补全：

```text
甲方
乙方
该公司
上述产品
本协议
其
该设备
```

#### 低置信度关系驱动

如果某个关系 confidence 低，可以拿相邻 chunk 或 parent section 做二次判断。

### 12.3 文档级 consolidation

所有 chunk 抽取完后，可以做一轮文档级合并检查。

输入：

```text
文档级 metadata
所有实体列表
所有关系候选
未解析 mention
低置信度关系
章节摘要
```

检查：

```text
1. 是否有甲方/乙方/本合同/该公司未解析？
2. 是否有重复关系？
3. 是否有明显跨 chunk 关系？
4. 是否有冲突事实？
5. 是否有低置信度但高价值关系需要人工审核？
```

---

## 13. 示例：合同文档处理

原文：

```text
采购合同

甲方：广州星河科技有限公司
乙方：深圳蓝海贸易有限公司

第二条 合同金额
本合同金额为人民币100万元。

第三条 交付
乙方应于2026年6月30日前完成设备交付。

第四条 违约责任
若乙方逾期交付，应向甲方支付违约金。
```

### 13.1 文档级 metadata

```json
{
  "doc_type": "contract",
  "contract_name": "采购合同",
  "roles": {
    "甲方": "广州星河科技有限公司",
    "乙方": "深圳蓝海贸易有限公司",
    "本合同": "采购合同"
  }
}
```

### 13.2 chunk 抽取结果

chunk_1：

```text
甲方：广州星河科技有限公司
乙方：深圳蓝海贸易有限公司
```

抽出：

```text
广州星河科技有限公司 -[IS_PARTY_A_OF]-> 采购合同
深圳蓝海贸易有限公司 -[IS_PARTY_B_OF]-> 采购合同
广州星河科技有限公司 -[SIGNS_WITH]-> 深圳蓝海贸易有限公司
```

chunk_2：

```text
本合同金额为人民币100万元。
```

抽出：

```text
本合同 -[HAS_AMOUNT]-> 人民币100万元
```

chunk_3：

```text
乙方应于2026年6月30日前完成设备交付。
```

抽出：

```text
乙方 -[DELIVERS_BEFORE]-> 2026年6月30日
乙方 -[DELIVERS]-> 设备
```

chunk_4：

```text
若乙方逾期交付，应向甲方支付违约金。
```

抽出：

```text
乙方 -[PAYS_PENALTY_TO]-> 甲方
```

### 13.3 实体归一化后

```text
本合同 → 采购合同
乙方 → 深圳蓝海贸易有限公司
甲方 → 广州星河科技有限公司
```

最终关系：

```text
采购合同 -[HAS_AMOUNT]-> 人民币100万元
深圳蓝海贸易有限公司 -[DELIVERS_BEFORE]-> 2026年6月30日
深圳蓝海贸易有限公司 -[DELIVERS]-> 设备
深圳蓝海贸易有限公司 -[PAYS_PENALTY_TO]-> 广州星河科技有限公司
广州星河科技有限公司 -[SIGNS_WITH]-> 深圳蓝海贸易有限公司
```

---

## 14. 开源方案与现实案例路线

### 14.1 文件预处理

推荐组合：

```text
Docling:
  默认多格式解析

MinerU:
  PDF / 扫描 / 复杂版面

Apache Tika:
  长尾格式兜底
```

### 14.2 图谱抽取 / GraphRAG 参考

可参考：

```text
Microsoft GraphRAG:
  学习 raw text → entity/relationship → graph → community summary → retrieval 的整体流程

Neo4j LLM Graph Builder:
  快速体验文档 → Neo4j 图谱

LlamaIndex PropertyGraphIndex:
  编程式 chunk → kg extractors → property graph
```

### 14.3 实体归一化 / Entity Resolution

现实业务中通常不叫“chunk 归一化”，而叫：

```text
Entity Resolution
Record Linkage
Data Matching
Deduplication
Identity Resolution
Master Data Management / MDM
Reconciliation
Golden Record
Entity Linking
```

可参考工具：

```text
Splink:
  probabilistic record linkage / entity resolution

Dedupe:
  机器学习 + 人工训练数据做结构化数据去重

RecordLinkage:
  Python record linkage toolkit

Zingg:
  identity resolution / entity resolution / data mastering

OpenRefine reconciliation:
  把表格中的值对齐到权威实体库
```

### 14.4 对本项目的启发

现实业务不是靠一个万能 LLM，也不是靠一个万能脚本，而是：

```text
强 ID 直接合并
规则和别名覆盖高频情况
概率模型处理批量相似记录
LLM 处理文本上下文和指代
人工审核处理高风险不确定样本
所有决策都记录 provenance
```

---

## 15. 第一版落地建议

### 15.1 文件预处理 V1

```text
1. 默认 Docling
2. PDF 优先 MinerU
3. 复杂/失败文件 Tika 兜底
4. 输出统一 Document Representation
5. 保留 block、page、offset、section path
```

### 15.2 分块 V1

```text
1. 按标题/段落/条款优先切
2. 超长 section 再递归切
3. 使用 overlap
4. 保留 parent_section_summary
5. 每个 chunk 有 chunk_id、page、offset、section_title
```

### 15.3 图谱抽取 V1

```text
1. 文档级抽取 roles / aliases / summary
2. chunk 级抽 mentions / relation candidates
3. 抽取结果先写 staging
4. 不直接把 local graph 当最终图
```

### 15.4 实体归一化 V1

```text
1. strong_id 匹配
2. doc_role_mapping
3. document-scoped alias
4. normalized exact match
5. fuzzy match 高分合并
6. 中分 REVIEW / LLM
7. 低分 NEW_ENTITY
8. 保存 resolution decision
```

### 15.5 关系合并 V1

```text
1. 定义允许的 relation schema
2. predicate_text 映射为标准 predicate
3. subject/object mention 映射为 canonical entity_id
4. 用 subject + predicate + object + scope 合并
5. 保留 source_chunk_ids 和 Evidence
```

---

## 16. 最重要的原则

```text
1. Document Representation 是后续质量的基础。
2. 切块不是越小越好，要尊重结构和语义。
3. embedding 只用于语义检索，实体关系从文本抽。
4. chunk 小图只是中间结果，不是最终图谱。
5. Mention 和 Entity 必须区分。
6. 实体归一化不能只靠 LLM，也不能只靠字符串匹配。
7. 甲方/乙方/本合同等别名必须有 scope。
8. 关系合并必须带 scope，避免跨文档错误合并。
9. 每条实体、关系、证据都要有 provenance。
10. 错合并比漏合并更危险，低置信度应保守处理。
```
