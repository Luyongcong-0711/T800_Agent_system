# Agent System

一个以 LangChain + LangGraph 为后端核心的 Agent 系统原型，前端使用 Next.js + React + TypeScript + Lobe UI 风格组件。项目目标不是做普通聊天壳，而是把对话、工具、MCP、Skill、SubAgent、长期记忆、知识库、日志、审批和任务中心整合成一个可持续扩展的 Agent 平台。

当前版本为了方便稳定调试，默认使用本地对象存储 `.agent_state` 保存会话、记忆、运行状态、Secret Store 密文对象、Job 状态和 MCP 配置。MinIO / Milvus / Neo4j 仍然保留在 Docker Compose 中，但默认不启动；等数据库结构确认后再接入。

## 主要功能

### 多会话对话

- 支持创建多个对话窗口，旧会话不会删除。
- 会话列表、消息历史、Run 事件分离展示。
- 对话输出支持 SSE 流式返回。
- 刷新页面后可从本地状态恢复历史消息和 Run 状态。
- 上下文过长时使用 Hermes-style 压缩策略：保留开头、压缩中间、保留最近尾部。

### LangGraph Agent Runtime

- 后端 Runtime 使用 LangGraph 组织模型调用、工具调用、工具结果回传和最终回答。
- 每次模型请求会携带运行上下文、模型可见工具清单、长期记忆快照、已激活 Skill context、压缩摘要和当前 thread 消息。
- 支持 OpenAI-compatible 与 Anthropic 两类模型 Provider。
- 默认模型上下文为 `200000` tokens，最大输出为 `8192` tokens，可在模型配置页面调整。
- 支持 context overflow 后自动压缩并重试一次。

### Tool 系统

内置 Tool 会通过模型 API 的 tool/function calling 能力暴露给模型，包括：

- RAG 检索：`rag_search`、`document_chunk_get`
- GraphRAG / 图谱查询：schema、实体搜索、关系扩展、路径查询、证据回源、只读 Cypher
- 长期记忆：搜索、读取、写入、删除、候选记忆审核
- 历史会话：搜索历史 thread、读取历史消息
- 本地文件：读取、列目录、搜索、写入、复制、移动、删除
- Skill：搜索、查看、激活、提案、从提案创建、调用入口
- SubAgent：代码审查、研究、日志分析、数据库检查
- 数据库诊断：数据库配置和健康状态检查

写文件、移动文件、删除文件等高风险操作默认需要用户审批。读取工作区外的本机路径也会触发审批。

### MCP 管理

- 支持 MCP Server 配置、查看、刷新、重连。
- 支持 MCP JSON 导入。
- 支持单个 MCP Tool 启用/禁用。
- 未真正配置 transport 的 fallback MCP snapshot 不会暴露给模型，避免模型误调用不存在的 MCP Tool。
- MCP Tool 最终也会合并进统一 Tool Inventory。

### 长期记忆

- 支持 `user_profile`、`user_preference`、`project_fact`、`project_rule`。
- `user_profile` 和 `user_preference` 可被模型直接写入，用户前端可见。
- `project_fact` 和 `project_rule` 走候选记忆和审核流程。
- 只有 `active`、`enabled_for_model_context=true`、非 sensitive 的记忆会注入模型上下文。
- 当前默认是 local-only 记忆存储，不同步到 Milvus / Neo4j。

### Skill 系统

- Skill 是提示词、流程、知识包和可选脚本的组合。
- 初始状态不内置 Skill，用户可以主动创建。
- 模型也可以在对话中提出把某个流程沉淀为 Skill，但需要用户同意。
- Skill 使用渐进式披露：默认只暴露摘要，激活后才把 workflow summary、knowledge notes 和 entrypoint tools 注入当前 run。
- 默认 Skill 脚本只读；需要写入时必须声明写入范围并走审批。

### SubAgent 协作

- 支持业务 Runtime SubAgent：`code_reviewer`、`researcher`、`log_analyst`、`database_checker`。
- SubAgent 输出不会直接改最终结论，必须回到主 Agent 汇总。
- 支持 SubAgent 任务、完成、审核和 leaf_state 查询。
- 设计上允许并发，但需要明确互不重叠的写入范围。

### 知识库、RAG 和 GraphRAG

- 文档入库、切块、embedding、图谱构建等能力已经有 API 和 Job 框架。
- 当前默认不启动 MinIO / Milvus / Neo4j，因此不要在默认模式下做真实文档入库。
- 后续数据库结构确认后，目标架构为：
  - MinIO：原始文件、chunk、manifest、JSON/JSONL 状态和归档
  - Milvus：向量检索
  - Neo4j：实体关系、多跳扩展和只读图谱查询
- 大模型只能查询知识图谱，不能直接写 Neo4j。

### Job 任务中心

- Run 和 Job 分离。
- Job 权威状态当前写入本地对象存储。
- 支持 Job 创建、claim、恢复、取消、重试、事件查询和 SSE 进度流。
- 文档入库、embedding 重建、图谱构建、日志归档、诊断包、MCP capability refresh 都可以进入 Job 系统。

### 日志与可观测性

- 系统运行日志、错误日志、组件日志、Run 事件和 Tool 调用事件分层记录。
- 前端有 Logs / P0 Readiness 页面用于排查系统状态。
- 支持诊断包和日志归档 Job。
- ToolResult、日志和前端响应会做敏感信息脱敏。

### 设置页

- 模型配置：主对话模型、GraphRAG LLM、Embedding API。
- 数据库配置：MinIO、Milvus、Neo4j、Redis 的 endpoint 和健康状态。
- Secret Store：保存模型 API key、数据库密码、MCP header 等密钥引用。
- MCP 配置：MCP server、tool list、tool enable/disable、JSON 导入。

## 技术栈

- Backend：Python 3.13、FastAPI、LangChain、LangGraph、Pydantic
- Frontend：Next.js、React、TypeScript、Ant Design、antd-style、Zustand
- Model Provider：OpenAI-compatible、Anthropic
- Runtime State：默认本地 `.agent_state`
- Optional Data Stack：MinIO、Milvus、Neo4j、Redis
- Network：REST + SSE
- Package Manager：后端 `pip` / 本地 Miniconda `py313`，前端 `pnpm`

项目不使用 PostgreSQL、MySQL、Gateway、WebSocket、Redis Queue 或 Redis Lock。

## 目录结构

```text
agent-system/
  backend/                 FastAPI 后端、Runtime、Tool、Memory、MCP、RAG、Job
  frontend/                Next.js 前端页面和业务组件
  deploy/                  Docker Compose、环境变量样例、部署脚本
  docs/final_plan/         开发前设计文档和开发规划
  scripts/                 验收、MCP smoke、辅助脚本
  tests/                   跨服务或 E2E 测试
  docker-compose.yml       根目录本地 compose
```

## 默认启动方式

默认模式只启动：

- backend：`http://localhost:8000`
- frontend：`http://localhost:3000`
- Redis：`localhost:6379`

默认不会启动 MinIO / Milvus / Neo4j / etcd。

```powershell
docker compose --env-file deploy/env/local.env.example up -d --build
```

查看服务状态：

```powershell
docker compose --env-file deploy/env/local.env.example ps
```

停止服务但保留数据：

```powershell
docker compose --env-file deploy/env/local.env.example stop
```

## 可选启动外部数据库

只有在研究数据库结构、测试文档入库、测试 Milvus / Neo4j / MinIO 集成时才需要启用：

```powershell
docker compose --env-file deploy/env/local.env.example --profile external-db up -d --build
```

该 profile 会额外启动：

- MinIO：`http://localhost:9000`
- MinIO Console：`http://localhost:9001`
- Milvus：`http://localhost:19530`
- Neo4j HTTP：`http://localhost:7474`
- Neo4j Bolt：`bolt://localhost:7687`
- etcd：Milvus 依赖

## 本地源码开发

后端使用本机 Miniconda `py313` 环境：

```powershell
conda activate py313
python -m pip install -e ".\backend[dev]"
```

只启动 Redis：

```powershell
docker compose --env-file deploy/env/local.env.example up -d redis
```

启动后端：

```powershell
cd backend
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

启动前端：

```powershell
cd frontend
pnpm install
pnpm dev
```

## 模型配置

当前默认模型配置写在 `deploy/env/local.env.example`：

```env
DEFAULT_MODEL_PROVIDER=openai_compatible
DEFAULT_MODEL_NAME=mimo-v2.5-pro
DEFAULT_MODEL_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
DEFAULT_MODEL_API_KEY_REF=secret_mimo_openai_compatible_key

DEFAULT_EMBEDDING_MODEL_NAME=text-embedding-v4
DEFAULT_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DEFAULT_EMBEDDING_API_KEY_REF=secret_dashscope_embedding_key
DEFAULT_EMBEDDING_DIMENSION=1024
```

真实 API key 不要写进 Git。请复制环境变量样例到私有 `.env` 后再填写：

```powershell
Copy-Item .env.example .env
```

`.env` 已被 `.gitignore` 忽略，不会上传 GitHub。

## VPS 部署

VPS compose 文件在：

```text
deploy/compose/docker-compose.vps.yml
```

在 VPS 的项目目录中启动：

```bash
docker compose --env-file .env -f deploy/compose/docker-compose.vps.yml up -d --build
```

当前 VPS 推荐默认只运行 backend / frontend / Redis。外部数据库容器可以保留但停用，等数据库结构确定后再启用。

常用检查：

```bash
docker compose --env-file .env -f deploy/compose/docker-compose.vps.yml ps
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:3000
```

## 测试与验证

后端单元测试：

```powershell
python -m pytest backend/tests/unit -q
```

前端类型检查：

```powershell
cd frontend
pnpm typecheck
```

P0 验收辅助脚本：

```powershell
python scripts\p0_acceptance.py --list-final-checks
python scripts\p0_acceptance.py
```

完整最终验收需要真实模型 API、MCP server、Docker 服务、前端浏览器 smoke 和 readiness 报告，不建议在普通开发时频繁运行。

## 安全说明

- 不要提交 `.env`。
- 不要提交 `.agent_state`。
- 不要提交 `logs`。
- 不要把真实 API key、数据库密码、MCP token 写进 README 或 example env。
- Secret 明文只在创建时进入后端，后续业务配置只保存 `secret_ref`。
- 模型、ToolResult、日志和前端响应都不应该暴露 Secret 明文。

## 当前状态说明

当前项目处于 P0 原型开发和稳定调试阶段：

- 主对话、多会话、本地记忆、Tool、MCP 页面、Skill、SubAgent、Job、日志页面已经具备基础闭环。
- 外部数据库能力保留，但默认停用。
- 在不上传文件入库的情况下，系统可以按 local-only 模式正常使用。
- 后续重点是补齐数据库结构、文档入库、Milvus 检索、Neo4j 图谱构建和最终验收流程。
