# MCP 接入、配置与权限设计

## 参考源码标注

本文件中 MCP tool 命名、schema normalization、include/exclude filter、timeout/retry、stdio/http transport 和前端 MCP 配置页，参考以下源码后按本项目权限体系重写：

| 本项目设计点 | 参考源码路径 | GitHub 地址 | 本项目落地 |
| --- | --- | --- | --- |
| MCP tool 命名和 schema 适配 | `.research_repos\hermes-agent\tools\mcp_tool.py` | `https://github.com/nousresearch/hermes-agent/blob/75cd420b3ba1b83185020c6d4506d7cc53b12e2b/tools/mcp_tool.py` | 模型可见名统一为 `mcp_{server_name}_{tool_name}` |
| MCP client timeout/retry/schema cache | `.research_repos\crewai\lib\crewai\src\crewai\mcp\client.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/mcp/client.py` | connect/list_tools/call_tool 分别设置 timeout 和 retry |
| include / exclude filter | `.research_repos\crewai\lib\crewai\src\crewai\mcp\filters.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/mcp/filters.py` | server/tool 过滤后再进入 Tool Registry |
| stdio transport | `.research_repos\crewai\lib\crewai\src\crewai\mcp\transports\stdio.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/mcp/transports/stdio.py` | 本地 MCP 子进程 stdio 管理 |
| HTTP transport | `.research_repos\crewai\lib\crewai\src\crewai\mcp\transports\http.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/mcp/transports/http.py` | 远程 MCP Streamable HTTP |
| MCP 后端和前端配置 | `.research_repos\openhands\openhands\app_server\mcp\mcp_router.py` / `.research_repos\openhands\frontend\src\routes\mcp-settings.tsx` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/openhands/app_server/mcp/mcp_router.py` | P0 前端只读查看 server/capability/tool，支持启用/禁用单个 tool |

## MCP 定位

MCP 是外部工具服务器接入协议。

MCP Server 暴露：

```text
tools
resources
prompts
```

这些能力进入系统后必须被适配成内部 Tool，并受 Runtime 的权限、日志、审批、重试和错误协议治理。

## 接入方式

| 类型 | 说明 |
| --- | --- |
| stdio | Runtime 启动本地 MCP Server 子进程，通过 stdin/stdout 通信 |
| http / streamable-http | 远程 MCP Server，适合云服务和企业内部工具 |
| sse legacy | 旧版远程 MCP，作为兼容 |

stdio 的目标不是 URL，而是：

```text
command + args + cwd + env
```

stdio 通信：

```text
Agent Runtime
  stdin  -> MCP Server Process
  stdout <- MCP Server Process
  stderr <- MCP Server Process logs
```

## 配置字段

stdio Server：

```text
server_name
type = stdio
command
args
env
secret_env_refs
cwd
timeout
enabled
scope
restart_policy
```

HTTP Server：

```text
server_name
type = http
url
public_headers
headers_ref
auth_type
oauth_credential_ref
timeout
enabled
scope
```

SSE Server：

```text
server_name
type = sse
url
public_headers
headers_ref
timeout
enabled
legacy_warning
```

## 生命周期

stdio server：

```text
configured
  -> starting
  -> initializing
  -> connected
  -> tool calls
  -> stopped
```

异常状态：

```text
failed
restarting
disconnected
auth_failed
tool_list_failed
```

启动流程：

```text
1. Runtime 读取配置。
2. 如果配置包含 `headers_ref`、`oauth_credential_ref` 或 `secret_env_refs`，MCP Connector 通过内部 SecretResolver 解密。
3. 如果 type=stdio，启动本地子进程。
4. 发送 initialize。
5. 执行 tools/list、resources/list、prompts/list。
6. 保存 capability snapshot。
7. UI 展示 server 状态和工具列表。
```

HTTP / Streamable HTTP 配置示例：

```json
{
  "server_name": "github",
  "type": "http",
  "url": "https://mcp.example.com/mcp",
  "public_headers": {
    "X-Client": "agent-runtime"
  },
  "headers_ref": "secret_mcp_github_headers",
  "auth_type": "header",
  "timeout_ms": 30000,
  "enabled": true,
  "scope": "workspace"
}
```

stdio 的 `env` 只能保存非敏感字面量；密钥类环境变量必须进入 `secret_env_refs`：

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
  "enabled": true
}
```

Capability Snapshot：

```json
{
  "server": "github",
  "transport": "http",
  "status": "connected",
  "tools": 18,
  "resources": 3,
  "prompts": 2,
  "server_info": {
    "name": "github",
    "version": "1.0.0"
  },
  "updated_at": "2026-05-28T15:00:00+08:00"
}
```

## MCP Tool 适配为内部 Tool

MCP Server 暴露的 tool 不能原样绕过 Runtime。Runtime 在 `tools/list` 后把每个已启用 MCP tool 适配成内部 Tool，再进入 Tool Registry 和 Effective Tool Inventory。

模型可见名称：

```text
mcp_{server_name}_{tool_name}
```

示例：

```text
mcp_github_search_issues
mcp_filesystem_read_file
mcp_playwright_click
```

适配规则：

- `server_name` 来自 MCP server 配置，不直接使用 server 自报名称覆盖。
- `tool_name` 来自 MCP `tools/list` 返回值。
- 名称归一化为小写 snake_case。
- 原始 tool name、server info、transport、input_schema_hash 保留在 metadata 中。
- MCP tool 的 input schema 需要转换成内部 Tool args_schema。
- MCP tool 的返回值统一包装成 ToolResult。
- 模型调用的是内部适配名，例如 `mcp_github_search_issues`；Runtime 真正向 MCP Server 发 `tools/call` 时使用 metadata 中的 `original_tool_name`，因此不破坏 MCP 协议。
- MCP tool 的连接错误、超时、schema 错误统一进入 ToolResult error。
- MCP tool 是否展示给模型，取决于 server enabled、单 tool enabled、权限策略、名称冲突和当前 Agent role。

适配后的 metadata 示例：

```json
{
  "name": "mcp_github_search_issues",
  "display_name": "GitHub search_issues",
  "source": "mcp",
  "server_name": "github",
  "original_tool_name": "search_issues",
  "transport": "http",
  "risk_level": "medium",
  "side_effect": false,
  "requires_approval": false,
  "args_schema_hash": "sha256...",
  "capability_snapshot_hash": "sha256...",
  "timeout_ms": 30000
}
```

名称冲突处理：

- MCP tool 不能覆盖系统内置工具。
- 同名冲突不自动改成难懂的随机名称。
- P0 发现冲突时禁用冲突 tool，并在 MCP 配置页面显示 `name_conflict`。
- 用户修改 server_name 或 tool policy 后，可以刷新 capability snapshot 再重新进入 Tool Registry。

## 权限策略

MCP 工具不能默认全信任。

配置项：

- server 是否启用。
- server 可被哪些 Agent 使用。
- server 默认 approval 策略。
- 单个 MCP tool 是否启用。
- 单个 MCP tool 风险等级。
- 是否允许写操作。
- 是否允许访问 workspace。
- 是否允许联网。
- 最大输出 token。
- tool timeout。

stdio 风险：

- npx / uvx 会运行本地或远程包。
- MCP server 进程可能读文件。
- MCP server 可能访问网络。
- MCP server 可能长期运行。
- MCP server 可能消耗资源。
- stderr 可能输出敏感信息。

安全策略：

```text
stdio MCP 默认必须由用户批准后启动。
npx / uvx 类型 server 首次启动必须由用户批准。
高风险 MCP server 建议在沙盒中启动。
command allowlist。
cwd 限制。
env 脱敏。
headers / token / OAuth 凭据使用 Secret Store 引用。
timeout。
进程资源限制。
stderr 日志截断。
工具权限策略。
是否允许 npx / uvx 自动下载。
```

## MCP 配置页面

MCP 配置页面进入 P0，但 P0 范围收敛为只读查看和单个 MCP tool 启用 / 禁用。

P0 做：

```text
server 只读列表：
  server_name / type / transport / enabled / status / last_seen / tool_count

capability 只读查看：
  tools / resources / prompts / server_info / capability snapshot 更新时间

tool 只读列表：
  tool_name / description / input_schema 摘要 / risk_level / enabled

tool 启用控制：
  启用单个 MCP tool
  禁用单个 MCP tool
  写入 tool policy
  刷新当前 server 的 capability snapshot
```

P0 手动刷新 capability snapshot 必须创建 `mcp_capability_refresh_job`。Job 负责连接 MCP server、initialize、`tools/list`、`resources/list`、`prompts/list`、schema normalization、名称冲突检测、写入 capability snapshot 和更新 Tool Registry。刷新失败时旧 snapshot 保留并标记 `stale=true`，不能清空当前已可用工具。

P0 不做：

- 在页面新增 MCP server。
- 在页面编辑 command、args、url、headers、env、cwd。
- 在页面维护 token、header、OAuth。
- 在页面启动、停止、重启 stdio server。
- 在页面编辑复杂审批策略。
- 在页面查看完整 stderr 原文。

以上能力放到 P1；P0 的 server 配置仍由配置文件或后端配置源提供。配置源里的敏感 header、token、OAuth credential 和 secret env 必须保存为 `headers_ref`、`oauth_credential_ref` 或 `secret_env_refs`，不能保存明文。

单个 tool policy：

```json
{
  "workspace_id": "workspace_001",
  "server_name": "filesystem",
  "tool_name": "read_file",
  "enabled": true,
  "risk_level": "medium",
  "updated_by": "user_001",
  "updated_at": "2026-05-29T00:00:00Z",
  "policy_version": 3
}
```

开关规则：

- 关闭 tool 后，Tool Registry 不再把该 MCP tool 暴露给模型。
- 正在执行中的 tool call 不被强行中断，后续新调用不再允许。
- 重新启用前必须确认 server 仍处于 connected 或可重连状态。
- capability snapshot 变化导致 tool schema 改变时，原 tool policy 保留 enabled 状态，但需要标记 `schema_changed`。
- 操作必须写入审计日志。

## 断线重连

stdio 断线恢复：

```text
disconnected
  -> stop process
  -> collect stderr summary
  -> restart if policy allows
  -> initialize
  -> tools/list
  -> update capability snapshot
  -> connected
```

断线后的自动 capability refresh 也进入 `mcp_capability_refresh_job`，同一 server 的 refresh 互斥，避免多个 Worker 同时覆盖 snapshot。

HTTP 断线恢复：

- 根据 HTTP 状态码分类。
- 短暂 5xx、408、429 可重试。
- 401、403 进入认证错误。
- 重连后重新校验 capability snapshot。

进行中的写操作如果断线，进入 `unknown_outcome`，先查状态，再决定是否重试或请求用户确认。
