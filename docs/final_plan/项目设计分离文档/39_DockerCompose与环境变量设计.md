# Docker Compose 与环境变量设计

状态：P0 本地开发部署设计  
更新时间：2026-05-30

## 定位

P0 默认用 docker compose 启动本地依赖：MinIO、Milvus、Neo4j、Redis。用户可以在配置页改成远程服务，但本地 compose 是开发、测试、验收的标准环境。

## 服务列表

```text
agent-server
agent-frontend
minio
milvus
neo4j
redis
```

## 端口约定

| 服务 | 端口 | 用途 |
| --- | --- | --- |
| agent-frontend | 3000 | Next.js Web UI |
| agent-server | 8000 | REST + SSE API |
| minio | 9000 | S3 API |
| minio-console | 9001 | MinIO Console |
| milvus | 19530 | Milvus gRPC |
| milvus-http | 9091 | Milvus health |
| neo4j-bolt | 7687 | Neo4j Bolt |
| neo4j-http | 7474 | Neo4j Browser / health |
| redis | 6379 | Redis cache |

## docker-compose.local.yml 草案

```yaml
services:
  minio:
    image: minio/minio:RELEASE.2025-04-22T22-12-26Z
    command: server /data --console-address ":9001"
    ports:
      - "9000:9000"
      - "9001:9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    volumes:
      - minio_data:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 10s
      timeout: 5s
      retries: 10

  milvus:
    image: milvusdb/milvus:v2.5.12
    command: ["milvus", "run", "standalone"]
    ports:
      - "19530:19530"
      - "9091:9091"
    volumes:
      - milvus_data:/var/lib/milvus
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9091/healthz"]
      interval: 10s
      timeout: 5s
      retries: 20

  neo4j:
    image: neo4j:5.26-community
    ports:
      - "7474:7474"
      - "7687:7687"
    environment:
      NEO4J_AUTH: ${NEO4J_AUTH}
      NEO4J_dbms_security_procedures_unrestricted: apoc.*
    volumes:
      - neo4j_data:/data
      - neo4j_logs:/logs
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:7474 || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 20

  redis:
    image: redis:7.4-alpine
    command: ["redis-server", "--appendonly", "no"]
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 10

volumes:
  minio_data:
  milvus_data:
  neo4j_data:
  neo4j_logs:
  redis_data:
```

开发时可以先只启动依赖服务，后端和前端在宿主机运行：

```text
docker compose -f deploy/compose/docker-compose.local.yml up -d redis
docker compose -f deploy/compose/docker-compose.local.yml --profile external-db up -d minio milvus neo4j
```

## .env.example

```env
# Runtime
APP_ENV=development
AGENT_SERVER_HOST=0.0.0.0
AGENT_SERVER_PORT=8000
FRONTEND_URL=http://localhost:3000

# Identity
DEFAULT_USER_ID=default_user
DEFAULT_USER_ROLE=owner
DEFAULT_WORKSPACE_ID=default

# Secret Store
AGENT_MASTER_KEY=replace-with-32-byte-base64-key

# MinIO
MINIO_ENDPOINT=http://localhost:9000
MINIO_REGION=us-east-1
MINIO_BUCKET=agent-system
MINIO_ROOT_USER=agentadmin
MINIO_ROOT_PASSWORD=agentadmin_password_change_me
MINIO_ACCESS_KEY_REF=
MINIO_SECRET_KEY_REF=

# Milvus
MILVUS_URI=http://localhost:19530
MILVUS_TOKEN_REF=
MILVUS_DATABASE=default
MILVUS_COLLECTION_PREFIX=agent

# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_AUTH=neo4j/agentpassword_change_me
NEO4J_DATABASE=neo4j
NEO4J_CREDENTIAL_REF=

# Redis cache only
REDIS_URL=redis://localhost:6379/0
REDIS_CREDENTIAL_REF=
REDIS_NAMESPACE=agent-cache

# Frontend
NEXT_PUBLIC_AGENT_API_BASE_URL=http://localhost:8000
```

## 测试模型配置

用户当前可用于 P0 开发测试的是 OpenAI-compatible API：

```text
provider = openai_compatible
model = mimo-v2.5-pro
base_url = https://token-plan-cn.xiaomimimo.com/v1
api_key = <set in private .env or Secret Store>
api_key_ref = secret_mimo_openai_compatible_key

embedding_provider = openai_compatible
embedding_model = text-embedding-v4
embedding_base_url = https://dashscope.aliyuncs.com/compatible-mode/v1
embedding_api_key = <set in private .env or Secret Store>
embedding_api_key_ref = secret_dashscope_text_embedding_v4_key
embedding_dimension = 1024
```

注意：

- 测试 API key 不能提交到 GitHub；开发时放入私有 `.env` 或 Secret Store。
- 开发实现时仍要支持 Secret Store，并创建 `secret_mimo_openai_compatible_key` 作为正式配置引用。
- 日志、诊断包、前端响应、Tool 参数回显、SubAgent 报告仍必须脱敏，不能输出明文 API key。
- 正式部署或多人协作前，确认明文 key 只存在于 Secret Store / 本地私有环境变量 / 部署平台密钥中。
- 没有 Anthropic key 时，不阻塞 Phase A 到 Phase D；Phase C 先用 OpenAI-compatible 跑通，Anthropic adapter 可用 fake provider 和后续真实 key 验证。

模型配置 manifest 示例：

```json
{
  "config_id": "main_chat",
  "provider": "openai_compatible",
  "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
  "api_key": "<redacted>",
  "api_key_ref": "secret_mimo_openai_compatible_key",
  "model": "mimo-v2.5-pro",
  "context_window_tokens": 200000,
  "max_output_tokens": 8192,
  "timeout_ms": 60000,
  "supports_streaming": true,
  "supports_tool_calling": true
}
```

Embedding 配置 manifest 示例：

```json
{
  "config_id": "embedding",
  "provider": "openai_compatible",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "api_key": "<redacted>",
  "api_key_ref": "secret_dashscope_text_embedding_v4_key",
  "model": "text-embedding-v4",
  "dimension": 1024,
  "timeout_ms": 60000,
  "supports_streaming": false,
  "supports_tool_calling": false
}
```

## 远程服务覆盖

数据库配置页面允许将本地配置改为远程：

```text
MinIO endpoint + access_key_ref + secret_key_ref
Milvus uri + token_ref / credential_ref
Neo4j uri + credential_ref
Redis url + credential_ref
```

规则：

- `.env` 只提供启动默认值。
- 用户在前端保存后的配置以 MinIO config manifest 为准。
- 配置 manifest 只保存 secret_ref，不保存明文。
- 快速连接测试使用后端 SecretResolver 解密。
- Redis 即使远程也只做 cache only。

## 初始化流程

```text
docker compose up -d redis
docker compose --profile external-db up -d minio milvus neo4j
  -> create MinIO bucket
  -> apply Neo4j constraints
  -> backend /bootstrap 创建 default workspace manifest
  -> backend 初始化 indexes
  -> frontend 读取 /bootstrap
```

## Neo4j 约束

```cypher
CREATE CONSTRAINT entity_id IF NOT EXISTS
FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE;

CREATE CONSTRAINT document_id IF NOT EXISTS
FOR (d:Document) REQUIRE d.doc_id IS UNIQUE;

CREATE CONSTRAINT chunk_id IF NOT EXISTS
FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE;

CREATE CONSTRAINT relation_fact_id IF NOT EXISTS
FOR (r:RelationFact) REQUIRE r.relation_fact_id IS UNIQUE;
```

## 健康检查

| 服务 | 检查方式 |
| --- | --- |
| MinIO | bucket exists + test put/read/delete 临时对象 |
| Milvus | connect + list collections + dimension test |
| Neo4j | driver verify connectivity + readonly query |
| Redis | ping + set/get 临时 cache key |

## 验收命令

```text
docker compose -f deploy/compose/docker-compose.local.yml ps
backend: pytest tests/integration/test_database_health.py
frontend: pnpm test
e2e: pytest tests/e2e/test_bootstrap_and_health.py
```

## 安全规则

- `.env` 可以用于本地开发，但不能作为正式 Secret Store。
- `AGENT_MASTER_KEY` 不能写入 MinIO、日志、前端响应或诊断包。
- 模型 API key、Anthropic key、MinIO secret、Milvus token、Neo4j password、Redis password 都必须进入 Secret Store，以 `secret_ref` 被业务配置引用。
- 诊断包必须隐藏所有 Authorization、Cookie、token、password、ciphertext、nonce、tag。
- 本地默认密码必须在生产部署前替换。
