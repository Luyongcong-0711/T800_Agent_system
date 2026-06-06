# Tool Schema 与参数校验设计

## 基本方案

每个工具都需要 `args_schema`。

主方案：

```text
原生 tool calling
strict schema
Pydantic args_schema
semantic validator
safety validator
ToolMessage 修复重试
```

XML 标签隔离只作为本地模型或非原生 tool calling 的备用方案。

## 校验层次

Schema 校验：

- 字段是否存在。
- 字段类型是否正确。
- 字符串是否为空。
- 数字范围。
- enum 取值。
- 是否允许多余字段。
- list 长度。
- 嵌套对象结构。

业务语义校验：

- top_k 是否超过系统上限。
- depth 是否超过图谱查询上限。
- collection 是否存在。
- doc_id 是否属于当前 workspace。
- embedding 维度是否匹配。
- URL 是否允许访问。

安全权限校验：

- 文件路径必须在 workspace 内。
- 禁止访问系统敏感目录。
- 外部 URL 必须经过受控联网检查。
- 写操作必须 approval。
- 请求体大小必须受限。
- 图谱写入必须走单独审批。

## 工具校验建议

| 工具类型 | 必要校验 |
| --- | --- |
| 文档检索 | query 非空、top_k 范围、search_type enum |
| 向量检索 | collection 白名单、top_k 范围、filter 合法 |
| 图谱检索 | label 白名单、relationship 白名单、depth 范围 |
| 文件读取 | path 在 workspace 内、文件大小限制 |
| 文件写入 | path 安全、备份、approval、operation_id |
| HTTP API | URL allowlist / block private IP、method 白名单、body size |
| MCP | server 启用、tool name 白名单、schema 匹配 |

## Schema 示例

向量检索工具参数：

```json
{
  "query": "GraphRAG 如何扩展实体关系？",
  "knowledge_base_id": "kb_default",
  "top_k": 20,
  "filters": {
    "doc_id": "doc_001"
  }
}
```

校验规则：

```text
query 非空。
knowledge_base_id 属于当前 workspace。
top_k 在 1 到 100 之间。
filters 只能包含白名单字段。
embedding 模型和 collection 维度一致。
```

图谱扩展工具参数：

```json
{
  "entity_name": "林黛玉",
  "labels": ["Person"],
  "depth": 2,
  "relationship_allowlist": ["MEMBER_OF", "LIVES_IN", "PARTICIPATED_IN"],
  "limit": 30
}
```

校验规则：

```text
entity_name 非空。
labels 在白名单内。
depth 不超过系统上限。
relationship_allowlist 在白名单内。
limit 不超过系统上限。
```

## 模型可修复错误

可返回 ToolMessage 让模型修复：

- 缺少必填参数。
- 字段类型错误。
- enum 值错误。
- top_k 超出范围。
- Cypher 只读查询语法错误。

不可让模型继续尝试：

- API key 无效。
- 权限不足。
- 访问越权路径。
- 写操作需要审批。
- 向量维度不匹配。
- collection 不存在。

