# 密钥与 Secret Store 设计

## 定位

Secret Store 负责保存模型 API Key、数据库凭据、MCP 凭据和未来受控联网凭据。

P0 决策：

```text
密钥加密后存入 MinIO。
主密钥由部署环境变量 AGENT_MASTER_KEY 提供。
前端、模型、日志、事件和 ToolResult 永远不返回明文密钥。
```

Secret Store 不是给模型调用的工具。模型只能看到配置摘要、`secret_ref`、`masked` 和健康状态，不能读取密钥明文。

简单理解：

```text
Secret Store = 本系统自己的加密密码库。
```

它解决的问题是：模型 API key、MinIO 密钥、Milvus token、Neo4j 密码、MCP header 这些敏感信息不能散落在配置文件、前端页面、日志和 ToolResult 里。用户只在创建 Secret 时输入一次明文；系统用 `AGENT_MASTER_KEY` 加密后把密文保存到 MinIO。业务配置只保存 `secret_ref`，例如：

```json
{
  "provider": "openai_compatible",
  "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
  "model": "mimo-v2.5-pro",
  "api_key_ref": "secret_mimo_openai_compatible_key"
}
```

运行时只有后端内部 `SecretResolver` 可以把 `api_key_ref` 解密成明文去调用模型。前端、模型、Skill、SubAgent、日志、诊断包都只能看到 `secret_ref` 或 `masked`，不能看到明文。

## P0 范围

P0 支持 workspace 级密钥：

```text
workspaces/{workspace_id}/secrets/{secret_id}.json
workspaces/{workspace_id}/indexes/secrets_index.json
```

P0 默认只有一个 workspace，但所有路径和 API 必须保留 `workspace_id`。如果后续要做跨 workspace 的全局用户密钥，可以使用同样结构扩展到：

```text
users/{user_id}/secrets/{secret_id}.json
users/{user_id}/indexes/secrets_index.json
```

第一版先不把全局用户密钥作为默认主路径，避免 global 配置和 workspace 配置边界混乱。

## 密钥类别

Secret Store 至少覆盖：

| 类型 | 示例 | 配置里保存 |
| --- | --- | --- |
| `model_api_key` | 主对话模型、GraphRAG LLM、压缩模型、备用模型 API Key | `api_key_ref` |
| `embedding_api_key` | Embedding Provider API Key | `api_key_ref` |
| `rerank_api_key` | Rerank Provider API Key | `api_key_ref` |
| `minio_access_key` | MinIO Access Key | `access_key_ref` |
| `minio_secret_key` | MinIO Secret Key | `secret_key_ref` |
| `milvus_token` | Milvus token | `token_ref` |
| `milvus_username_password` | Milvus 用户名密码 | `credential_ref` |
| `neo4j_username_password` | Neo4j 用户名密码 | `credential_ref` |
| `mcp_headers` | 远程 MCP Authorization / X-API-Key 等 header | `headers_ref` |
| `mcp_oauth_credential` | MCP OAuth access / refresh credential | `oauth_credential_ref` |
| `http_proxy_credential` | 代理用户名密码 | `proxy_credential_ref` |
| `web_fetch_credential` | 未来联网工具站点凭据 | `credential_ref` |

非密钥配置，例如 `base_url`、`model`、`timeout_ms`、`bucket`、`collection_prefix`、`enabled`，仍保存在普通配置对象中。

## 密钥对象结构

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
  "rotated_from_secret_id": null,
  "disabled_at": null,
  "deleted_at": null,
  "revision": 1
}
```

字段规则：

- `encrypted_value.ciphertext`、`nonce`、`tag` 都使用 base64 编码。
- `nonce` 每个 secret 随机生成，不能复用。
- `key_version` 用于后续主密钥轮换。
- `masked` 只用于前端展示，不能用于恢复明文。
- `status` 可选值：`active`、`disabled`、`rotated`、`soft_deleted`。
- `revision` 用于乐观并发控制。

## 密钥索引

`workspaces/{workspace_id}/indexes/secrets_index.json`：

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "secrets": [
    {
      "secret_id": "secret_model_main_api_key",
      "type": "model_api_key",
      "display_name": "主对话模型 API Key",
      "masked": "sk-****abcd",
      "status": "active",
      "object_key": "workspaces/default/secrets/secret_model_main_api_key.json",
      "last_used_at": null,
      "updated_at": "2026-05-30T00:00:00+08:00"
    }
  ],
  "revision": 1,
  "updated_at": "2026-05-30T00:00:00+08:00"
}
```

索引只保存脱敏摘要。索引损坏时可以通过扫描 `workspaces/{workspace_id}/secrets/*.json` 重建。

## 配置对象引用方式

模型配置只保存引用：

```json
{
  "provider": "openai_compatible",
  "base_url": "https://api.example.com/v1",
  "api_key_ref": "secret_model_main_api_key",
  "model": "model-name",
  "context_window_tokens": 200000,
  "max_output_tokens": 8192
}
```

MinIO 配置：

```json
{
  "endpoint": "https://minio.example.com",
  "access_key_ref": "secret_minio_access_key",
  "secret_key_ref": "secret_minio_secret_key",
  "bucket": "agent-files",
  "region": "cn",
  "use_ssl": true
}
```

Milvus 配置：

```json
{
  "uri": "https://milvus.example.com",
  "auth_mode": "token",
  "token_ref": "secret_milvus_token",
  "database": "default",
  "default_collection_prefix": "agent_default",
  "tls": true
}
```

Neo4j 配置：

```json
{
  "uri": "neo4j+s://neo4j.example.com",
  "auth_mode": "username_password",
  "credential_ref": "secret_neo4j_credential",
  "database": "neo4j",
  "readonly_user": true
}
```

MCP HTTP 配置：

```json
{
  "server_name": "github",
  "type": "http",
  "url": "https://mcp.example.com/mcp",
  "headers_ref": "secret_mcp_github_headers",
  "auth_type": "header",
  "timeout_ms": 30000,
  "enabled": true,
  "scope": "workspace"
}
```

stdio MCP 配置：

```json
{
  "server_name": "filesystem",
  "type": "stdio",
  "command": "mcp-filesystem",
  "args": ["--root", "{workspace_root}"],
  "env": {
    "LOG_LEVEL": "info"
  },
  "secret_env_refs": {
    "GITHUB_TOKEN": "secret_mcp_github_token"
  },
  "cwd": "{workspace_root}",
  "timeout_ms": 30000,
  "enabled": true,
  "scope": "workspace"
}
```

`env` 只能保存非敏感字面量。密钥类环境变量必须放在 `secret_env_refs`。

## 主密钥规则

部署环境变量：

```text
AGENT_MASTER_KEY
```

规则：

- 生产环境启动时如果缺少 `AGENT_MASTER_KEY`，后端必须 fail fast，不能降级成明文保存。
- `AGENT_MASTER_KEY` 推荐使用 32 字节随机值的 base64 表示。
- 开发环境可以从 `.env` 读取启动默认值，但 `.env` 不是正式密钥存储。
- `AGENT_MASTER_KEY` 不能写入 MinIO、日志、前端响应、ToolResult、错误堆栈。
- 主密钥轮换时通过 `key_version` 分阶段重加密，不在 P0 强制做 UI。

## 加密规则

P0 使用：

```text
AES-256-GCM
```

写入流程：

```text
1. 用户在前端输入明文密钥。
2. 前端通过 HTTPS POST 到后端。
3. 后端从 AGENT_MASTER_KEY 读取当前主密钥。
4. 后端生成随机 nonce。
5. 后端使用 AES-256-GCM 加密明文。
6. 后端把 ciphertext / nonce / tag / key_version 写入 MinIO。
7. 后端更新 secrets_index.json。
8. 后端只返回 secret_id、secret_ref、masked、status。
```

读取流程：

```text
1. Runtime Connector 收到内部调用，需要连接模型、数据库或 MCP。
2. Connector 传入 workspace_id、secret_ref、purpose 调用 SecretResolver。
3. SecretResolver 校验调用方是后端内部服务。
4. SecretResolver 校验 secret 类型和 purpose 匹配。
5. SecretResolver 从 MinIO 读取密文对象。
6. SecretResolver 使用 AGENT_MASTER_KEY 解密。
7. 明文只在当前调用栈中使用，不写入任何返回值。
8. 写入 secret_resolved 审计事件，只记录 secret_id、purpose 和调用方，不记录明文。
```

## SecretResolver 伪代码

```python
from dataclasses import dataclass
from typing import Literal

SecretPurpose = Literal[
    "model_call",
    "embedding_call",
    "rerank_call",
    "minio_connect",
    "milvus_connect",
    "neo4j_connect",
    "mcp_connect",
    "proxy_connect",
]

@dataclass
class ResolvedSecret:
    secret_id: str
    plaintext: str

class SecretResolver:
    def __init__(self, minio, master_key_provider, audit_log):
        self.minio = minio
        self.master_key_provider = master_key_provider
        self.audit_log = audit_log

    def resolve(self, workspace_id: str, secret_ref: str, purpose: SecretPurpose) -> ResolvedSecret:
        assert_internal_caller()
        object_key = f"workspaces/{workspace_id}/secrets/{secret_ref}.json"
        secret_obj = self.minio.read_json(object_key)

        if secret_obj["status"] != "active":
            raise SecretUnavailable(secret_ref)

        assert_secret_purpose_allowed(secret_obj["type"], purpose)

        encrypted = secret_obj["encrypted_value"]
        master_key = self.master_key_provider.current(encrypted["key_version"])
        plaintext = decrypt_aes_256_gcm(
            ciphertext=encrypted["ciphertext"],
            nonce=encrypted["nonce"],
            tag=encrypted["tag"],
            key=master_key,
            aad=f"{workspace_id}:{secret_ref}:{secret_obj['type']}",
        )

        self.minio.write_json(
            object_key,
            {**secret_obj, "last_used_at": now_iso(), "revision": secret_obj["revision"] + 1},
            expected_revision=secret_obj["revision"],
        )
        self.audit_log.write(
            "secret_resolved",
            workspace_id=workspace_id,
            secret_id=secret_ref,
            purpose=purpose,
            redacted=True,
        )
        return ResolvedSecret(secret_id=secret_ref, plaintext=plaintext)
```

`SecretResolver` 不能注册为 LangChain Tool，不能进入 Tool Registry，不能被模型通过 tool call 调用。

## 模型调用接入伪代码

```python
class LLMConnector:
    def __init__(self, secret_resolver: SecretResolver, provider_factory):
        self.secret_resolver = secret_resolver
        self.provider_factory = provider_factory

    def call(self, workspace_id: str, config: dict, request: ModelRequest) -> ModelResult:
        resolved = self.secret_resolver.resolve(
            workspace_id=workspace_id,
            secret_ref=config["api_key_ref"],
            purpose="model_call",
        )
        provider = self.provider_factory.create(
            provider=config["provider"],
            base_url=config["base_url"],
            api_key=resolved.plaintext,
        )
        return provider.call(request)
```

LangGraph 节点只拿配置引用，不拿明文：

```python
def call_model_node(state: AgentState) -> dict:
    model_config = config_store.get_model_config(
        workspace_id=state["workspace_id"],
        role="main_chat",
    )
    result = llm_connector.call(
        workspace_id=state["workspace_id"],
        config=model_config,
        request=model_request_builder.build(state, model_config),
    )
    return {"messages": [result.assistant_message], "model_usage": result.usage}
```

## Secret API 契约

所有接口都在 workspace scope 下：

| 接口 | 方法 | 用途 |
| --- | --- | --- |
| `/workspaces/{workspace_id}/secrets` | GET | 获取脱敏密钥列表 |
| `/workspaces/{workspace_id}/secrets` | POST | 创建密钥并加密保存 |
| `/workspaces/{workspace_id}/secrets/{secret_id}` | GET | 获取脱敏详情 |
| `/workspaces/{workspace_id}/secrets/{secret_id}` | PATCH | 修改显示名、禁用、恢复 |
| `/workspaces/{workspace_id}/secrets/{secret_id}/rotate` | POST | 用新明文创建轮换版本 |
| `/workspaces/{workspace_id}/secrets/{secret_id}` | DELETE | 软删除密钥 |
| `/workspaces/{workspace_id}/secrets/{secret_id}/references` | GET | 查询引用关系 |

创建密钥请求：

```json
{
  "type": "model_api_key",
  "display_name": "主对话模型 API Key",
  "plaintext": "sk-xxxx",
  "scope": "workspace"
}
```

创建密钥响应：

```json
{
  "secret_id": "secret_model_main_api_key",
  "secret_ref": "secret_model_main_api_key",
  "type": "model_api_key",
  "display_name": "主对话模型 API Key",
  "masked": "sk-****abcd",
  "status": "active",
  "created_at": "2026-05-30T00:00:00+08:00"
}
```

列表响应：

```json
{
  "workspace_id": "default",
  "secrets": [
    {
      "secret_id": "secret_model_main_api_key",
      "type": "model_api_key",
      "display_name": "主对话模型 API Key",
      "masked": "sk-****abcd",
      "status": "active",
      "last_used_at": null,
      "updated_at": "2026-05-30T00:00:00+08:00"
    }
  ]
}
```

禁止任何响应包含：

```text
plaintext
ciphertext
nonce
tag
AGENT_MASTER_KEY
```

## 删除、禁用和引用检查

删除前必须检查引用关系：

```text
model config
embedding config
rerank config
database config
MCP server config
proxy config
future web_fetch config
```

删除流程：

```text
1. 前端请求 references。
2. 后端扫描配置对象中的 *_ref 字段。
3. 如果仍被 active 配置引用，默认阻止删除。
4. 用户先替换配置或禁用配置。
5. 删除只做 soft_delete，保留审计对象。
```

禁用流程：

```text
1. 设置 status=disabled。
2. 更新 secrets_index.json。
3. 后续 SecretResolver resolve 直接报 SecretUnavailable。
4. 配置页和健康检查显示 auth_config_disabled。
```

轮换流程：

```text
1. 用户输入新密钥。
2. POST /secrets/{secret_id}/rotate。
3. 后端创建新密文，保持 secret_id 不变或创建 new_secret_id。
4. 如果保持 secret_id，revision + 1，rotated_from_secret_id 写旧版本引用。
5. 如果创建 new_secret_id，配置对象改指向新 secret_ref，旧 secret status=rotated。
6. 轮换事件写入审计日志，只记录 secret_id 和 masked。
```

P0 推荐“保持 secret_id，更新密文和 revision”的简单模式；需要完整历史回滚时再使用 new_secret_id 模式。

## 前端行为

前端配置页只处理两种值：

```text
用户正在输入的 plaintext
后端返回的 secret_ref / masked
```

前端保存配置时只提交 `*_ref` 字段：

```tsx
async function saveModelConfig(values: ModelConfigFormValues) {
  let apiKeyRef = values.api_key_ref;

  if (values.new_api_key_plaintext) {
    const secret = await agentApi.createSecret({
      type: 'model_api_key',
      display_name: `${values.model} API Key`,
      plaintext: values.new_api_key_plaintext,
      scope: 'workspace',
    });
    apiKeyRef = secret.secret_ref;
  }

  await agentApi.updateModelConfig('main_chat', {
    provider: values.provider,
    base_url: values.base_url,
    api_key_ref: apiKeyRef,
    model: values.model,
    context_window_tokens: values.context_window_tokens,
    max_output_tokens: values.max_output_tokens,
  });
}
```

UI 规则：

- 已保存密钥显示 `masked`。
- 修改密钥时显示空输入框，不回显旧明文。
- 清空密钥表示解除引用，不表示删除 Secret 对象。
- 删除 Secret 必须进入引用检查。
- 测试连接时前端只发 `secret_ref`，后端内部解密。

## MCP 凭据处理

HTTP / Streamable HTTP MCP：

- `headers` 中的敏感字段不能明文保存在配置对象。
- 整组敏感 header 可以加密成一个 `mcp_headers` secret。
- 非敏感 header 可以保留在 `public_headers`。

stdio MCP：

- `env` 只保存非敏感字面量。
- 需要密钥的环境变量放入 `secret_env_refs`。
- 启动子进程前由 MCP Connector 内部解密并注入子进程环境。
- stderr 日志必须截断并脱敏。

MCP ToolResult 不能包含请求 header、token、OAuth access token、refresh token 或完整错误堆栈。

## Tool、Skill 和 SubAgent 限制

规则：

- 模型不可见 SecretResolver。
- LangChain Tool args_schema 不允许出现 `api_key`、`password`、`token` 明文字段。
- Tool 参数可以包含 `secret_ref`，但只有系统内部 Connector 可以 resolve。
- Skill Script 默认不能读取 `AGENT_MASTER_KEY`、Secret Store 对象、模型 API Key、数据库密码、MCP secret。
- Skill Script 需要外部访问时必须通过受控 Tool / Connector。
- SubAgent 只能继承主 Agent 授权后的工具集合，不能额外拿密钥。
- 日志审计页面可以显示 `secret_id` 和 `masked`，不能显示密文和明文。

## 日志脱敏规则

日志允许记录：

```text
secret_id
secret_ref
masked
secret type
purpose
status
error_type
```

日志禁止记录：

```text
plaintext secret
ciphertext
nonce
tag
AGENT_MASTER_KEY
Authorization
Cookie
Set-Cookie
X-API-Key
access_token
refresh_token
database password
完整连接串中带密码的 URI
```

脱敏函数必须在这些入口统一执行：

- REST request / response logging。
- SSE event logging。
- ToolResult。
- Provider Adapter error。
- MCP stderr preview。
- Skill stdout / stderr preview。
- database health check error。
- audit log。

## 失败策略

| 失败 | 分类 | 处理 |
| --- | --- | --- |
| `AGENT_MASTER_KEY` 缺失 | `secret_store_unavailable` | 非 dev 环境启动失败 |
| secret 不存在 | `secret_not_found` | 配置页提示重新选择或创建 |
| secret disabled | `secret_disabled` | 不重试，提示启用或替换 |
| 解密失败 | `secret_decrypt_failed` | 不重试，进入配置修复 |
| purpose 不匹配 | `secret_purpose_denied` | 拒绝调用，写安全审计 |
| provider auth failed | `auth_failed` | 不重试，提示检查密钥 |
| 引用删除冲突 | `secret_still_referenced` | 阻止删除并返回引用列表 |

## 开发测试清单

P0 测试必须覆盖：

- 创建 secret 后 MinIO 对象不包含明文。
- `secrets_index.json` 只包含脱敏摘要。
- GET list/detail 不返回 `encrypted_value`。
- 模型配置保存的是 `api_key_ref`。
- 数据库配置保存的是 `access_key_ref`、`secret_key_ref`、`token_ref`、`credential_ref`。
- MCP 配置保存的是 `headers_ref`、`oauth_credential_ref`、`secret_env_refs`。
- SecretResolver 能成功解密 active secret。
- disabled secret 无法 resolve。
- wrong purpose 无法 resolve。
- 删除被引用 secret 时返回 `secret_still_referenced`。
- 日志和 ToolResult 中出现 `Authorization`、`Cookie`、`X-API-Key` 时会被脱敏。
- 非 dev 环境缺少 `AGENT_MASTER_KEY` 时后端启动失败。
