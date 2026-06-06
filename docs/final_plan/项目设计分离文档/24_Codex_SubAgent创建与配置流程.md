# Codex SubAgent 创建与配置流程

状态：流程记录版，可作为后续正式设计文档的候选内容  
模型要求：所有自定义 SubAgent 统一使用 `gpt-5.5`

## 目标

本文记录在 Codex 中创建、安装、校验和测试自定义 SubAgent 的完整流程。

本项目的 SubAgent 体系服务于 Agent Runtime 平台开发，覆盖：

- 架构设计、API 契约、代码路径分析。
- Python、LangChain、LangGraph、FastAPI 后端开发。
- React、TypeScript、Next.js、@lobehub/ui、Antd、antd-style 前端开发。
- MinIO、Milvus、Neo4j、RAG、GraphRAG、Embedding、Rerank 数据与 AI 流程。
- MCP、Tool Registry、Skill、SubAgent、多 Agent 编排。
- Hook、审批、安全沙盒、日志审计、回滚、评估和观测。
- Codex 开发日志、SubAgent 报告、验证报告和 ADR 必须进入 `33_Codex开发日志与SubAgent开发流程设计.md` 定义的流程。
- Docker、DevOps、SRE、部署、文档和测试自动化。

## 存放位置

Codex 支持两类自定义 SubAgent 配置目录：

| 类型 | 路径 | 生效范围 | 优先级 |
| --- | --- | --- | --- |
| 全局 SubAgent | `C:\Users\Administrator\.codex\agents` | 所有项目 | 低 |
| 项目 SubAgent | `<project>\.codex\agents` | 当前项目 | 高 |

当前采用全局安装：

```text
C:\Users\Administrator\.codex\agents
```

## 单个 SubAgent 文件格式

每个 SubAgent 是一个 `.toml` 文件，文件名建议与 `name` 一致。

示例：

```toml
name = "python-pro"
description = "Use when a task needs Python-focused implementation, runtime behavior, packaging, typing, or testing."
model = "gpt-5.5"
model_reasoning_effort = "high"
sandbox_mode = "workspace-write"

developer_instructions = """
Own Python tasks as production behavior and contract work.

Working mode:
1. Map the execution boundary.
2. Identify the root cause or design gap.
3. Implement the smallest coherent fix.
4. Validate the success path and one failure path.

Do not perform broad refactors unless explicitly requested.
"""

[tools]
web_search = false
```

关键字段：

| 字段 | 说明 |
| --- | --- |
| `name` | 调用时使用的 `agent_type` |
| `description` | 说明什么时候应该调用这个 agent |
| `model` | 当前统一写 `gpt-5.5` |
| `model_reasoning_effort` | 可用 `low`、`medium`、`high`、`xhigh` |
| `sandbox_mode` | 常用 `read-only` 或 `workspace-write` |
| `developer_instructions` | 该 agent 的角色、边界和工作规则 |
| `[tools]` | 可选工具开关，例如 `web_search = false` |

## TOML 顺序注意事项

`developer_instructions` 必须定义在顶层。

如果先写 `[tools]`，再写 `developer_instructions`，TOML 会把 `developer_instructions` 解析成 `[tools]` 表里的字段，Codex 会认为该 agent 缺少顶层 `developer_instructions`。

错误写法：

```toml
name = "demo"
description = "Demo agent."
model = "gpt-5.5"

[tools]
web_search = false

developer_instructions = """
This is now inside [tools], not top level.
"""
```

正确写法：

```toml
name = "demo"
description = "Demo agent."
model = "gpt-5.5"

developer_instructions = """
This is a top-level field.
"""

[tools]
web_search = false
```

## 从外部仓库筛选 Agent

本次使用的来源：

```text
https://github.com/VoltAgent/awesome-codex-subagents
```

筛选原则：

- 匹配项目技术栈，而不是全部安装。
- 覆盖完整开发流程，而不是只覆盖编码。
- 区分只读角色和可写角色。
- 所有 `model` 字段统一改成 `gpt-5.5`。
- 安装后必须用 `codex doctor` 和实际唤醒测试校验。

## 已安装 Agent 清单

当前全局目录中共有 56 个 SubAgent，模型全部为 `gpt-5.5`。

架构、API、代码定位：

```text
agent-organizer
api-designer
api-documenter
architect-reviewer
codebase-orchestrator
code-mapper
```

后端、Python、全栈：

```text
backend-developer
fastapi-developer
python-pro
fullstack-developer
```

前端、React、TypeScript、UI：

```text
frontend-developer
typescript-pro
ui-designer
ui-fixer
browser-debugger
```

数据、数据库、AI、RAG：

```text
ai-engineer
data-engineer
database-administrator
database-optimizer
llm-architect
nlp-engineer
prompt-engineer
```

MCP、工具链、开发体验：

```text
mcp-developer
build-engineer
dependency-manager
documentation-engineer
docs-researcher
dx-optimizer
tooling-engineer
```

质量、安全、测试：

```text
code-reviewer
reviewer
qa-expert
security-auditor
test-automator
performance-engineer
```

部署、平台、可靠性：

```text
deployment-engineer
devops-engineer
docker-expert
sre-engineer
platform-engineer
idp-architect
golden-path-designer
```

AI 治理、评估、观测：

```text
ai-governance-auditor
ai-observability-engineer
eval-engineer
hallucination-investigator
policy-guardrail-designer
prompt-regression-tester
responsible-ai-reviewer
```

多 Agent 编排和上下文：

```text
context-manager
error-coordinator
knowledge-synthesizer
multi-agent-coordinator
workflow-orchestrator
```

本地演示 Agent：

```text
codex_demo_reviewer
```

## 批量安装流程

1. 下载或克隆外部 SubAgent 仓库。
2. 根据项目技术栈选择 `.toml` 文件。
3. 复制到全局目录：

```powershell
$dest = "C:\Users\Administrator\.codex\agents"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item -LiteralPath "<source-agent.toml>" -Destination $dest
```

4. 统一模型字段为 `gpt-5.5`：

```powershell
Get-ChildItem -LiteralPath "C:\Users\Administrator\.codex\agents" -Filter *.toml |
  ForEach-Object {
    $text = Get-Content -Raw -Encoding UTF8 -LiteralPath $_.FullName
    $new = $text -replace '(?m)^model\s*=\s*"[^"]+"', 'model = "gpt-5.5"'
    if ($new -ne $text) {
      Set-Content -Encoding UTF8 -LiteralPath $_.FullName -Value $new
    }
  }
```

5. 检查是否还有非 `gpt-5.5` 模型：

```powershell
Get-ChildItem -LiteralPath "C:\Users\Administrator\.codex\agents" -Filter *.toml |
  ForEach-Object {
    $m = (Select-String -Encoding UTF8 -LiteralPath $_.FullName -Pattern '^model\s*=\s*"([^"]+)"').Matches.Groups[1].Value
    if ($m -ne "gpt-5.5") {
      [pscustomobject]@{ File = $_.Name; Model = $m }
    }
  }
```

没有输出表示全部通过。

## 配置校验

使用 `codex doctor` 校验 Codex 是否能正常解析配置：

```powershell
codex doctor --summary --ascii
```

期望结果：

```text
Configuration
  [ok] config loaded

0 warn
0 fail
```

如果出现 malformed agent role definition，优先检查：

- `developer_instructions` 是否存在。
- `developer_instructions` 是否在顶层，而不是 `[tools]` 下面。
- TOML 字符串三引号是否闭合。
- `name` 是否唯一。
- `model` 是否写成 `gpt-5.5`。

## 加载机制

Codex 当前会话不一定热加载新加入的自定义 SubAgent。

推荐流程：

1. 写入或修改 `.toml`。
2. 运行 `codex doctor`。
3. 重启或刷新 Codex 会话。
4. 再调用 `spawn_agent` 测试。

现象：

```text
unknown agent_type 'api-designer'
```

常见原因是当前会话还没有重新加载 agent registry。重启 Codex 后即可识别。

## 单个 Agent 唤醒测试

测试目标是验证 agent 能被创建、执行并返回结果，不验证业务能力。

测试消息：

```text
启动测试。不要读写文件，不要运行命令。请只返回：AGENT_TEST_OK: <agent_type>。
```

示例：

```text
agent_type: api-designer
message: 启动测试。不要读写文件，不要运行命令。请只返回：AGENT_TEST_OK: api-designer。
fork_context: false
```

期望返回：

```text
AGENT_TEST_OK: api-designer
```

## 批量测试策略

当前环境实测并发上限约为 6 个 agent 线程。

建议每批启动 5 个：

1. `spawn_agent` 启动 5 个。
2. `wait_agent` 等待全部完成。
3. 检查返回文本是否匹配 `AGENT_TEST_OK: <agent_type>`。
4. `close_agent` 关闭已完成线程。
5. 再进入下一批。

如果一次性启动过多，可能出现：

```text
collab spawn failed: agent thread limit reached
```

此时关闭已完成 agent 后重试未启动的 agent。

## 测试结果记录

本次 56 个 SubAgent 全部通过最小唤醒测试。

结果摘要：

```text
总数：56
成功：56
失败：0
模型：全部 gpt-5.5
配置校验：codex doctor 0 warn / 0 fail
```

测试只验证：

- Codex 能识别 `agent_type`。
- Agent 能成功启动。
- Agent 能接收最小任务。
- Agent 能按要求返回。

测试不验证：

- 真实项目代码修改能力。
- 对应技术栈的业务实现质量。
- 复杂工具调用、MCP 连接和浏览器调试能力。
- 多 Agent 协作流程的最终产出质量。

## 推荐调用方式

不要假设 Codex 会自动选择自定义 SubAgent。需要在提示词里显式说明。

示例：

```text
请并行调用 code-mapper、api-designer、llm-architect：
1. code-mapper 只读分析当前代码路径。
2. api-designer 审查 REST/SSE/API 契约。
3. llm-architect 审查工具调用、RAG 和多步 Agent 工作流。
等待三者完成后汇总结论。
```

实现型任务示例：

```text
先让 code-mapper 定位代码路径。
再让 backend-developer 修改后端。
让 frontend-developer 修改 React / Next.js 页面。
最后让 reviewer 和 test-automator 做审查与测试补充。
```

## 推荐角色分工

需求和设计阶段：

```text
api-designer
architect-reviewer
llm-architect
policy-guardrail-designer
golden-path-designer
```

实现阶段：

```text
backend-developer
fastapi-developer
python-pro
frontend-developer
vue-expert
typescript-pro
mcp-developer
data-engineer
```

验证阶段：

```text
reviewer
code-reviewer
qa-expert
test-automator
security-auditor
performance-engineer
eval-engineer
```

部署和运行阶段：

```text
docker-expert
deployment-engineer
devops-engineer
sre-engineer
ai-observability-engineer
```

复杂任务编排：

```text
multi-agent-coordinator
workflow-orchestrator
agent-organizer
context-manager
knowledge-synthesizer
error-coordinator
```

## 后续维护规则

- 新增 SubAgent 时必须写 `model = "gpt-5.5"`。
- 新增后必须运行 `codex doctor --summary --ascii`。
- 新增后必须做一次最小唤醒测试。
- 修改 `[tools]` 时确认 `developer_instructions` 仍在顶层。
- 对可写 agent 使用 `workspace-write`，对审查和设计 agent 优先使用 `read-only`。
- 大批量测试时每批不超过 5 个，测试后及时关闭 agent。
- 如果当前会话报 `unknown agent_type`，先重启或刷新 Codex。
