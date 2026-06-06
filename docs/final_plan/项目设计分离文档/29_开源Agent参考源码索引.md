# 开源 Agent 参考源码索引

## 定位

本文件只记录本项目调研和借鉴过的开源 Agent 系统源码地址，方便开发时回到具体实现核对细节。

最终实现仍以 `项目设计分离文档` 中的设计规则、接口、流程和伪代码为准；这里的源码索引不能替代本项目自己的设计。

本地调研仓库根目录：

```text
C:\Users\Administrator\Desktop\Agent design\前期技术栈设计\.research_repos
```

使用规则：

- 正式设计文档中如果采用了某个开源系统的机制，必须尽量标注本文件中的仓库、commit 和源码路径。
- 开发时优先按本项目设计实现，不直接复制开源项目的数据模型、接口协议或目录结构。
- 如果后续重新拉取或升级这些仓库，需要更新本文件中的 commit 和源码路径。

## 仓库快照

| 仓库 | 本地目录 | Remote | Branch | Commit |
| --- | --- | --- | --- | --- |
| Hermes Agent | `.research_repos\hermes-agent` | `https://github.com/nousresearch/hermes-agent.git` | `main` | `75cd420b3ba1b83185020c6d4506d7cc53b12e2b` |
| OpenClaw | `.research_repos\openclaw` | `https://github.com/openclaw/openclaw.git` | `main` | `f2ba23424eacc7f567018e5d8862bdeddfa9c740` |
| pi | `.research_repos\pi` | `https://github.com/earendil-works/pi.git` | `main` | `ce554ad3dec5c675a737cc3bc4f5a62809b4c166` |
| OpenHands | `.research_repos\openhands` | `https://github.com/All-Hands-AI/OpenHands.git` | `main` | `e073659755487d831eb6eb4ef0e6a543f64fdb80` |
| SWE-agent | `.research_repos\swe-agent` | `https://github.com/SWE-agent/SWE-agent.git` | `main` | `0f4f3bba990e01ca8460b9963abdcd89e38042f2` |
| Aider | `.research_repos\aider` | `https://github.com/Aider-AI/aider.git` | `main` | `5dc9490bb35f9729ef2c95d00a19ccd30c26339c` |
| AutoGen | `.research_repos\autogen` | `https://github.com/microsoft/autogen.git` | `main` | `027ecf0a379bcc1d09956d46d12d44a3ad9cee14` |
| CrewAI | `.research_repos\crewai` | `https://github.com/crewAIInc/crewAI.git` | `main` | `fca21b155c4f316ee63d4aa1725361aff392e47e` |
| Lobe UI | `.research_repos\lobe-ui` | `https://github.com/lobehub/lobe-ui.git` | `master` | `220a26b08c5b05c0125ea186742a18474dc138f7` |

## Hermes Agent

本项目主要借鉴 Hermes 的上下文压缩、长期记忆快照、MCP 工具命名、工具渐进披露和 SubAgent 隔离思路。其中上下文压缩已经作为 P0 默认策略写入 `18_长期记忆与上下文压缩设计.md`。

| 本项目功能 | 本地源码路径 | GitHub 地址 | 借鉴点 |
| --- | --- | --- | --- |
| 上下文压缩核心算法 | `.research_repos\hermes-agent\agent\context_compressor.py` | `https://github.com/nousresearch/hermes-agent/blob/75cd420b3ba1b83185020c6d4506d7cc53b12e2b/agent/context_compressor.py` | 50% 触发阈值、保留 head/tail、中间 summary、旧工具结果裁剪、图片占位、summary failure fallback、孤儿 tool result 修复 |
| 压缩调用、锁和会话轮转 | `.research_repos\hermes-agent\agent\conversation_compression.py` | `https://github.com/nousresearch/hermes-agent/blob/75cd420b3ba1b83185020c6d4506d7cc53b12e2b/agent/conversation_compression.py` | compression lock、压缩前 memory flush、session rotation、parent session、压缩失败不轮转 |
| 记忆管理接口 | `.research_repos\hermes-agent\agent\memory_manager.py` | `https://github.com/nousresearch/hermes-agent/blob/75cd420b3ba1b83185020c6d4506d7cc53b12e2b/agent/memory_manager.py` | memory provider 生命周期、session switch、system prompt memory block |
| 文件型长期记忆工具 | `.research_repos\hermes-agent\tools\memory_tool.py` | `https://github.com/nousresearch/hermes-agent/blob/75cd420b3ba1b83185020c6d4506d7cc53b12e2b/tools/memory_tool.py` | `MEMORY.md` / `USER.md`、记忆大小限制、写入前威胁扫描、冻结 snapshot |
| 后台复盘 | `.research_repos\hermes-agent\agent\background_review.py` | `https://github.com/nousresearch/hermes-agent/blob/75cd420b3ba1b83185020c6d4506d7cc53b12e2b/agent/background_review.py` | 后台 review 与主会话共享 session 时需要压缩锁，避免并发压缩分叉 |
| SubAgent 委托工具 | `.research_repos\hermes-agent\tools\delegate_tool.py` | `https://github.com/nousresearch/hermes-agent/blob/75cd420b3ba1b83185020c6d4506d7cc53b12e2b/tools/delegate_tool.py` | 子任务隔离上下文、独立 task、受控工具集合 |
| 工具调度帮助函数 | `.research_repos\hermes-agent\agent\tool_dispatch_helpers.py` | `https://github.com/nousresearch/hermes-agent/blob/75cd420b3ba1b83185020c6d4506d7cc53b12e2b/agent/tool_dispatch_helpers.py` | 工具并发、参数准备、执行边界 |
| 工具渐进披露 | `.research_repos\hermes-agent\tools\tool_search.py` | `https://github.com/nousresearch/hermes-agent/blob/75cd420b3ba1b83185020c6d4506d7cc53b12e2b/tools/tool_search.py` | search / describe / call 风格的工具发现；本项目只把渐进披露用于 Skill |
| MCP 工具适配 | `.research_repos\hermes-agent\tools\mcp_tool.py` | `https://github.com/nousresearch/hermes-agent/blob/75cd420b3ba1b83185020c6d4506d7cc53b12e2b/tools/mcp_tool.py` | `mcp_{server}_{tool}` 命名、schema normalize、连接错误恢复 |
| 会话状态 | `.research_repos\hermes-agent\hermes_state.py` | `https://github.com/nousresearch/hermes-agent/blob/75cd420b3ba1b83185020c6d4506d7cc53b12e2b/hermes_state.py` | session、状态 DB、压缩锁相关状态管理思路 |

## OpenClaw

本项目借鉴 OpenClaw 的 Context Engine 契约、SubAgent spawn 参数、Tool Policy、MCP stdio server 和 session memory hook。OpenClaw 有 Gateway，但本项目已经确认不需要独立 Gateway。

| 本项目功能 | 本地源码路径 | GitHub 地址 | 借鉴点 |
| --- | --- | --- | --- |
| ContextEngine 契约 | `.research_repos\openclaw\src\context-engine\types.ts` | `https://github.com/openclaw/openclaw/blob/f2ba23424eacc7f567018e5d8862bdeddfa9c740/src/context-engine/types.ts` | bootstrap、maintain、ingest、assemble、compact、subagent spawn 生命周期 |
| 工具搜索 | `.research_repos\openclaw\src\agents\tool-search.ts` | `https://github.com/openclaw/openclaw/blob/f2ba23424eacc7f567018e5d8862bdeddfa9c740/src/agents/tool-search.ts` | catalog snapshot、tool search、fingerprint |
| SubAgent spawn | `.research_repos\openclaw\src\agents\subagent-spawn.ts` | `https://github.com/openclaw/openclaw/blob/f2ba23424eacc7f567018e5d8862bdeddfa9c740/src/agents/subagent-spawn.ts` | spawn 参数、隔离上下文、cwd、timeout、sandbox、cleanup |
| 工具策略 | `.research_repos\openclaw\src\agents\tool-policy.ts` | `https://github.com/openclaw/openclaw/blob/f2ba23424eacc7f567018e5d8862bdeddfa9c740/src/agents/tool-policy.ts` | allow / deny、tool group、plugin-only allowlist、unknown allowlist 检测 |
| MCP stdio server | `.research_repos\openclaw\src\mcp\tools-stdio-server.ts` | `https://github.com/openclaw/openclaw/blob/f2ba23424eacc7f567018e5d8862bdeddfa9c740/src/mcp/tools-stdio-server.ts` | stdio MCP 工具暴露和调用边界 |
| Session memory hook | `.research_repos\openclaw\src\hooks\bundled\session-memory\handler.ts` | `https://github.com/openclaw/openclaw/blob/f2ba23424eacc7f567018e5d8862bdeddfa9c740/src/hooks/bundled/session-memory/handler.ts` | 会话归档为 memory 文件、过滤内部事件和工具事件 |
| Memory state plugin | `.research_repos\openclaw\src\plugins\memory-state.ts` | `https://github.com/openclaw/openclaw/blob/f2ba23424eacc7f567018e5d8862bdeddfa9c740/src/plugins/memory-state.ts` | memory 插件状态 |
| Memory backend 配置 | `.research_repos\openclaw\packages\memory-host-sdk\src\host\backend-config.ts` | `https://github.com/openclaw/openclaw/blob/f2ba23424eacc7f567018e5d8862bdeddfa9c740/packages/memory-host-sdk/src/host/backend-config.ts` | 外部 memory backend 配置模型 |
| Memory schema | `.research_repos\openclaw\packages\memory-host-sdk\src\host\memory-schema.ts` | `https://github.com/openclaw/openclaw/blob/f2ba23424eacc7f567018e5d8862bdeddfa9c740/packages/memory-host-sdk/src/host/memory-schema.ts` | memory schema、字段约束 |

## pi

本项目借鉴 pi 的 JSONL session event tree、compaction entry、provider overflow 识别和流式工具调用处理。

| 本项目功能 | 本地源码路径 | GitHub 地址 | 借鉴点 |
| --- | --- | --- | --- |
| Agent loop | `.research_repos\pi\packages\agent\src\agent-loop.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/agent/src/agent-loop.ts` | Agent 主循环、工具调用顺序、事件处理 |
| Compaction 核心 | `.research_repos\pi\packages\agent\src\harness\compaction\compaction.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/agent/src/harness/compaction/compaction.ts` | compaction 边界、keep recent tokens、重复压缩 |
| Branch summarization | `.research_repos\pi\packages\agent\src\harness\compaction\branch-summarization.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/agent/src/harness/compaction/branch-summarization.ts` | 分支摘要、历史分叉摘要 |
| JSONL storage | `.research_repos\pi\packages\agent\src\harness\session\jsonl-storage.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/agent/src/harness/session/jsonl-storage.ts` | append-only JSONL 持久化 |
| JSONL repo | `.research_repos\pi\packages\agent\src\harness\session\jsonl-repo.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/agent/src/harness/session/jsonl-repo.ts` | session event repo、读取与重放 |
| Session schema | `.research_repos\pi\packages\agent\src\harness\session\session.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/agent/src/harness/session/session.ts` | header、entry、leaf、parent 关系 |
| Provider 消息转换 | `.research_repos\pi\packages\ai\src\providers\transform-messages.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/ai/src/providers/transform-messages.ts` | provider 消息格式兼容 |
| OpenAI completions provider | `.research_repos\pi\packages\ai\src\providers\openai-completions.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/ai/src/providers/openai-completions.ts` | OpenAI-compatible 调用细节 |
| OpenAI Codex responses provider | `.research_repos\pi\packages\ai\src\providers\openai-codex-responses.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/ai/src/providers/openai-codex-responses.ts` | Responses API 流式、tool call、reasoning 处理 |
| Context overflow | `.research_repos\pi\packages\ai\src\utils\overflow.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/ai/src/utils/overflow.ts` | 多 provider overflow 错误识别 |
| Coding agent session | `.research_repos\pi\packages\coding-agent\src\core\agent-session.ts` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/coding-agent/src/core/agent-session.ts` | Agent session 封装 |
| Compaction 文档 | `.research_repos\pi\packages\coding-agent\docs\compaction.md` | `https://github.com/earendil-works/pi/blob/ce554ad3dec5c675a737cc3bc4f5a62809b4c166/packages/coding-agent/docs/compaction.md` | compaction 设计说明 |

## OpenHands

本项目借鉴 OpenHands 的事件服务、历史事件分页、沙盒生命周期、Skill/Hook loader、压缩事件前端展示和 MCP 配置 UI。OpenHands 使用 WebSocket，但本项目不使用 WebSocket，只借鉴“实时流 + 历史补偿分离”的思路。

| 本项目功能 | 本地源码路径 | GitHub 地址 | 借鉴点 |
| --- | --- | --- | --- |
| Event service | `.research_repos\openhands\openhands\app_server\event\event_service.py` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/openhands/app_server/event/event_service.py` | event get/search/count/save/batch_get |
| Event base | `.research_repos\openhands\openhands\app_server\event\event_service_base.py` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/openhands/app_server/event/event_service_base.py` | event service 抽象 |
| Event router | `.research_repos\openhands\openhands\app_server\event\event_router.py` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/openhands/app_server/event/event_router.py` | REST 事件接口 |
| Sandbox service | `.research_repos\openhands\openhands\app_server\sandbox\sandbox_service.py` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/openhands/app_server/sandbox/sandbox_service.py` | sandbox 生命周期、状态恢复 |
| Docker sandbox | `.research_repos\openhands\openhands\app_server\sandbox\docker_sandbox_service.py` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/openhands/app_server/sandbox/docker_sandbox_service.py` | Docker sandbox 管理 |
| Sandbox router | `.research_repos\openhands\openhands\app_server\sandbox\sandbox_router.py` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/openhands/app_server/sandbox/sandbox_router.py` | sandbox REST 接口 |
| Skill loader | `.research_repos\openhands\openhands\app_server\app_conversation\skill_loader.py` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/openhands/app_server/app_conversation/skill_loader.py` | Skill 加载失败策略和 viewer |
| Hook loader | `.research_repos\openhands\openhands\app_server\app_conversation\hook_loader.py` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/openhands/app_server/app_conversation/hook_loader.py` | Hook 加载与启动容错 |
| 前端沙盒恢复 | `.research_repos\openhands\frontend\src\hooks\use-sandbox-recovery.ts` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/frontend/src/hooks/use-sandbox-recovery.ts` | 页面刷新、tab focus 后 refetch |
| 压缩事件类型 | `.research_repos\openhands\frontend\src\types\v1\core\events\condensation-event.ts` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/frontend/src/types/v1/core/events/condensation-event.ts` | 前端展示压缩摘要、遗忘事件 ID |
| Context window UI | `.research_repos\openhands\frontend\src\components\features\conversation\metrics-modal\context-window-section.tsx` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/frontend/src/components/features/conversation/metrics-modal/context-window-section.tsx` | token/context usage 可视化 |
| MCP router | `.research_repos\openhands\openhands\app_server\mcp\mcp_router.py` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/openhands/app_server/mcp/mcp_router.py` | MCP 后端配置接口 |
| MCP settings route | `.research_repos\openhands\frontend\src\routes\mcp-settings.tsx` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/frontend/src/routes/mcp-settings.tsx` | MCP 设置页路由和状态 |
| MCP server form | `.research_repos\openhands\frontend\src\components\features\settings\mcp-settings\mcp-server-form.tsx` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/frontend/src/components/features/settings/mcp-settings/mcp-server-form.tsx` | MCP server 表单字段和校验 |

## SWE-agent

本项目借鉴 SWE-agent 的历史裁剪处理器、工具安全、超时、编辑唯一命中、lint 回滚、hook 和 reviewer 思路。第一版如果不做代码编辑 Agent，可只把这些作为 Skill Runner / 沙盒安全设计参考。

| 本项目功能 | 本地源码路径 | GitHub 地址 | 借鉴点 |
| --- | --- | --- | --- |
| History processors | `.research_repos\swe-agent\sweagent\agent\history_processors.py` | `https://github.com/SWE-agent/SWE-agent/blob/0f4f3bba990e01ca8460b9963abdcd89e38042f2/sweagent/agent/history_processors.py` | LastN、remove_output、closed window、cache control |
| Agent 主体 | `.research_repos\swe-agent\sweagent\agent\agents.py` | `https://github.com/SWE-agent/SWE-agent/blob/0f4f3bba990e01ca8460b9963abdcd89e38042f2/sweagent/agent/agents.py` | Agent loop、工具执行、状态 |
| 模型层 | `.research_repos\swe-agent\sweagent\agent\models.py` | `https://github.com/SWE-agent/SWE-agent/blob/0f4f3bba990e01ca8460b9963abdcd89e38042f2/sweagent/agent/models.py` | token count、cost limit、call limit |
| Tool 系统 | `.research_repos\swe-agent\sweagent\tools\tools.py` | `https://github.com/SWE-agent/SWE-agent/blob/0f4f3bba990e01ca8460b9963abdcd89e38042f2/sweagent/tools/tools.py` | tool bundle、重复名检测、安装和 PATH |
| 环境执行 | `.research_repos\swe-agent\sweagent\environment\swe_env.py` | `https://github.com/SWE-agent/SWE-agent/blob/0f4f3bba990e01ca8460b9963abdcd89e38042f2/sweagent/environment/swe_env.py` | 环境命令、超时、interrupt |
| Hook 抽象 | `.research_repos\swe-agent\sweagent\agent\hooks\abstract.py` | `https://github.com/SWE-agent/SWE-agent/blob/0f4f3bba990e01ca8460b9963abdcd89e38042f2/sweagent/agent/hooks/abstract.py` | Agent lifecycle hook |
| apply_patch hook | `.research_repos\swe-agent\sweagent\run\hooks\apply_patch.py` | `https://github.com/SWE-agent/SWE-agent/blob/0f4f3bba990e01ca8460b9963abdcd89e38042f2/sweagent/run/hooks/apply_patch.py` | patch 应用、输出检查 |
| Reviewer | `.research_repos\swe-agent\sweagent\agent\reviewer.py` | `https://github.com/SWE-agent/SWE-agent/blob/0f4f3bba990e01ca8460b9963abdcd89e38042f2/sweagent/agent/reviewer.py` | 多 attempt review / chooser |
| Windowed edit | `.research_repos\swe-agent\tools\windowed_edit_replace\bin\edit` | `https://github.com/SWE-agent/SWE-agent/blob/0f4f3bba990e01ca8460b9963abdcd89e38042f2/tools/windowed_edit_replace/bin/edit` | 搜索替换唯一命中、失败提示 |
| Filemap config | `.research_repos\swe-agent\tools\filemap\config.yaml` | `https://github.com/SWE-agent/SWE-agent/blob/0f4f3bba990e01ca8460b9963abdcd89e38042f2/tools/filemap/config.yaml` | repo/file map 工具配置 |

## Aider

本项目借鉴 Aider 的 RepoMap、消息格式校验、编辑块、统一 diff、lint 和 architect/editor 分工思路。P0 不强制实现代码 Agent，但消息格式校验和上下文预算思想应保留。

| 本项目功能 | 本地源码路径 | GitHub 地址 | 借鉴点 |
| --- | --- | --- | --- |
| RepoMap | `.research_repos\aider\aider\repomap.py` | `https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/repomap.py` | token budget 下的 repo map |
| Base coder | `.research_repos\aider\aider\coders\base_coder.py` | `https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/coders/base_coder.py` | coder 主流程、消息组织 |
| Models | `.research_repos\aider\aider\models.py` | `https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/models.py` | 模型能力、上下文窗口、token 估算 |
| History | `.research_repos\aider\aider\history.py` | `https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/history.py` | 历史消息管理 |
| Send chat | `.research_repos\aider\aider\sendchat.py` | `https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/sendchat.py` | provider 调用和消息 sanity check |
| Git repo wrapper | `.research_repos\aider\aider\repo.py` | `https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/repo.py` | repo 状态读取 |
| Linter | `.research_repos\aider\aider\linter.py` | `https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/linter.py` | 只报 fatal、新增错误和上下文 |
| Edit block coder | `.research_repos\aider\aider\coders\editblock_coder.py` | `https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/coders/editblock_coder.py` | SEARCH/REPLACE 编辑块 |
| Unified diff coder | `.research_repos\aider\aider\coders\udiff_coder.py` | `https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/coders/udiff_coder.py` | unified diff 编辑 |
| Architect coder | `.research_repos\aider\aider\coders\architect_coder.py` | `https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/coders/architect_coder.py` | 规划模型和编辑模型分离 |

## AutoGen

本项目借鉴 AutoGen 的 termination condition、handoff 工具化、GroupChat 管理、Memory 协议、Workbench 生命周期和 Agent/Team state 序列化。

| 本项目功能 | 本地源码路径 | GitHub 地址 | 借鉴点 |
| --- | --- | --- | --- |
| 终止条件 | `.research_repos\autogen\python\packages\autogen-agentchat\src\autogen_agentchat\conditions\_terminations.py` | `https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/python/packages/autogen-agentchat/src/autogen_agentchat/conditions/_terminations.py` | max message、timeout、token usage、handoff、external cancel |
| Handoff | `.research_repos\autogen\python\packages\autogen-agentchat\src\autogen_agentchat\base\_handoff.py` | `https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/python/packages/autogen-agentchat/src/autogen_agentchat/base/_handoff.py` | handoff 作为可调用能力；本项目模型可见名用 `call_subagent_*` |
| GroupChat manager | `.research_repos\autogen\python\packages\autogen-agentchat\src\autogen_agentchat\teams\_group_chat\_base_group_chat_manager.py` | `https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_base_group_chat_manager.py` | speaker、message_thread、current_turn |
| Memory base | `.research_repos\autogen\python\packages\autogen-core\src\autogen_core\memory\_base_memory.py` | `https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/python/packages/autogen-core/src/autogen_core/memory/_base_memory.py` | update_context、query、add、clear、close |
| List memory | `.research_repos\autogen\python\packages\autogen-core\src\autogen_core\memory\_list_memory.py` | `https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/python/packages/autogen-core/src/autogen_core/memory/_list_memory.py` | 简单 memory 实现 |
| Tool caller loop | `.research_repos\autogen\python\packages\autogen-core\src\autogen_core\tool_agent\_caller_loop.py` | `https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/python/packages/autogen-core/src/autogen_core/tool_agent/_caller_loop.py` | 工具调用循环和结果处理 |
| Workbench | `.research_repos\autogen\python\packages\autogen-core\src\autogen_core\tools\_workbench.py` | `https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/python/packages/autogen-core/src/autogen_core/tools/_workbench.py` | list_tools、call_tool、start、stop、reset、save/load state |
| AssistantAgent | `.research_repos\autogen\python\packages\autogen-agentchat\src\autogen_agentchat\agents\_assistant_agent.py` | `https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/python/packages/autogen-agentchat/src/autogen_agentchat/agents/_assistant_agent.py` | 工具名唯一、handoff 名唯一、max_tool_iterations、并发安全边界 |
| Team state | `.research_repos\autogen\python\packages\autogen-agentchat\src\autogen_agentchat\state\_states.py` | `https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/python/packages/autogen-agentchat/src/autogen_agentchat/state/_states.py` | Agent / Team state 序列化 |
| Selector group chat | `.research_repos\autogen\python\packages\autogen-agentchat\src\autogen_agentchat\teams\_group_chat\_selector_group_chat.py` | `https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_selector_group_chat.py` | speaker 选择策略 |
| Swarm group chat | `.research_repos\autogen\python\packages\autogen-agentchat\src\autogen_agentchat\teams\_group_chat\_swarm_group_chat.py` | `https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_swarm_group_chat.py` | swarm / handoff 运行模式 |

## CrewAI

本项目借鉴 CrewAI 的 task/crew process、memory record、recall scoring、tool hooks、MCP client、checkpoint/event record 和 delegate work 工具。

| 本项目功能 | 本地源码路径 | GitHub 地址 | 借鉴点 |
| --- | --- | --- | --- |
| Crew process | `.research_repos\crewai\lib\crewai\src\crewai\crew.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/crew.py` | sequential / hierarchical、planning、usage metrics、callbacks |
| Task | `.research_repos\crewai\lib\crewai\src\crewai\task.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/task.py` | expected_output、context、tools、guardrail、human_input |
| Unified memory | `.research_repos\crewai\lib\crewai\src\crewai\memory\unified_memory.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/memory/unified_memory.py` | 统一 memory save / recall |
| Memory types | `.research_repos\crewai\lib\crewai\src\crewai\memory\types.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/memory/types.py` | MemoryRecord 字段、scope、metadata、importance |
| Memory analyze | `.research_repos\crewai\lib\crewai\src\crewai\memory\analyze.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/memory/analyze.py` | LLM 分析 memory scope / category / entities |
| Recall flow | `.research_repos\crewai\lib\crewai\src\crewai\memory\recall_flow.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/memory/recall_flow.py` | semantic + recency + importance 召回 |
| Encoding flow | `.research_repos\crewai\lib\crewai\src\crewai\memory\encoding_flow.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/memory/encoding_flow.py` | memory 编码和写入 |
| Tool hooks | `.research_repos\crewai\lib\crewai\src\crewai\hooks\tool_hooks.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/hooks/tool_hooks.py` | before / after tool call，可修改输入、阻断或修改结果 |
| Tool usage | `.research_repos\crewai\lib\crewai\src\crewai\tools\tool_usage.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/tools/tool_usage.py` | 工具调用解析、重试、重复检测、usage limit |
| MCP client | `.research_repos\crewai\lib\crewai\src\crewai\mcp\client.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/mcp/client.py` | connect / list_tools / call_tool 超时、重试、schema cache |
| MCP filters | `.research_repos\crewai\lib\crewai\src\crewai\mcp\filters.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/mcp/filters.py` | include / exclude filter |
| MCP tool resolver | `.research_repos\crewai\lib\crewai\src\crewai\mcp\tool_resolver.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/mcp/tool_resolver.py` | MCP tool 解析 |
| MCP native tool | `.research_repos\crewai\lib\crewai\src\crewai\tools\mcp_native_tool.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/tools/mcp_native_tool.py` | MCP tool 包装为系统工具 |
| MCP stdio transport | `.research_repos\crewai\lib\crewai\src\crewai\mcp\transports\stdio.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/mcp/transports/stdio.py` | stdio transport |
| MCP HTTP transport | `.research_repos\crewai\lib\crewai\src\crewai\mcp\transports\http.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/mcp/transports/http.py` | Streamable HTTP / HTTP transport |
| Checkpoint config | `.research_repos\crewai\lib\crewai\src\crewai\state\checkpoint_config.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/state/checkpoint_config.py` | checkpoint 配置 |
| Event record | `.research_repos\crewai\lib\crewai\src\crewai\state\event_record.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/state/event_record.py` | 有向事件记录、parent/child/trigger |
| SQLite state provider | `.research_repos\crewai\lib\crewai\src\crewai\state\provider\sqlite_provider.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/state/provider/sqlite_provider.py` | 状态 provider 接口设计；本项目不使用 SQLite/pgsql |
| Delegate work tool | `.research_repos\crewai\lib\crewai\src\crewai\tools\agent_tools\delegate_work_tool.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/tools/agent_tools/delegate_work_tool.py` | Agent 委托工具 |

## Lobe UI

Lobe UI 已在 `28_前端LobeUI适配设计.md` 中做了更详细的源码索引，这里只保留总入口。

| 本项目功能 | 本地源码路径 | GitHub 地址 | 借鉴点 |
| --- | --- | --- | --- |
| Lobe UI 仓库根目录 | `.research_repos\lobe-ui` | `https://github.com/lobehub/lobe-ui/tree/220a26b08c5b05c0125ea186742a18474dc138f7` | 前端视觉系统和基础组件 |
| package / exports | `.research_repos\lobe-ui\package.json` | `https://github.com/lobehub/lobe-ui/blob/220a26b08c5b05c0125ea186742a18474dc138f7/package.json` | `@lobehub/ui` 依赖、版本、peer dependencies |
| 主导出入口 | `.research_repos\lobe-ui\src\index.ts` | `https://github.com/lobehub/lobe-ui/blob/220a26b08c5b05c0125ea186742a18474dc138f7/src/index.ts` | 组件导出 |
| Chat 子包入口 | `.research_repos\lobe-ui\src\chat\index.ts` | `https://github.com/lobehub/lobe-ui/blob/220a26b08c5b05c0125ea186742a18474dc138f7/src/chat/index.ts` | ChatList、ChatInputArea、ChatHeader 等 |

## 本项目功能到参考源码映射

| 本项目设计点 | 主要参考源码 | 已写入的最终设计文档 |
| --- | --- | --- |
| Hermes 风格上下文压缩 | Hermes `context_compressor.py`、`conversation_compression.py` | `18_长期记忆与上下文压缩设计.md` |
| 长期记忆 user_profile / user_preference | Hermes `memory_tool.py`、CrewAI `memory/types.py`、OpenClaw `memory-schema.ts` | `18_长期记忆与上下文压缩设计.md` |
| MinIO JSONL 主状态和 SSE 断线补偿 | pi `jsonl-storage.ts`、pi `jsonl-repo.ts`、OpenHands event service | `03_MinIO文件与产物存储设计.md`、`08_网络传输与断线重连设计.md`、`17_日志审计回滚与恢复设计.md` |
| 后台 Job 调度、事件和恢复 | pi `jsonl-storage.ts`、pi `jsonl-repo.ts`、OpenHands event service / router、CrewAI event bus / event record、OpenClaw SubAgent/session metrics | `34_后台任务Job调度与恢复设计.md` |
| Provider 兼容和 context overflow | pi `overflow.ts`、Aider `models.py`、Aider `sendchat.py` | `27_模型API配置与Token预算设计.md` |
| ToolResult / Hook / Tool Usage | CrewAI `tool_hooks.py`、CrewAI `tool_usage.py`、AutoGen `_caller_loop.py` | `12_Hook审批与安全策略设计.md`、`14_ToolResult错误协议与重试策略.md` |
| MCP 命名、过滤、重连、schema cache | Hermes `mcp_tool.py`、CrewAI MCP client / filters / transports、OpenHands MCP UI | `15_MCP接入配置与权限设计.md` |
| Effective Tool Inventory | Hermes `tool_search.py`、OpenClaw `tool-policy.ts`、AutoGen `_assistant_agent.py` | `09_Tool体系与内置工具设计.md`、`10_ToolSchema与参数校验设计.md` |
| Skill 渐进披露和脚本治理 | Hermes `tool_search.py`、OpenHands `skill_loader.py`、SWE-agent tool safety | `25_Skill脚本与版本治理设计.md` |
| SubAgent 并发和结果审核 | Hermes `delegate_tool.py`、OpenClaw `subagent-spawn.ts`、AutoGen group chat、CrewAI delegate work | `26_SubAgent并发编排与结果审核设计.md` |
| 沙盒、超时、资源限制 | OpenHands sandbox service、SWE-agent `swe_env.py`、SWE-agent `tools.py` | `13_沙盒与受控联网设计.md`、`25_Skill脚本与版本治理设计.md` |
| 前端 Lobe UI 适配 | Lobe UI `src`、OpenHands MCP/settings/context UI | `20_前端页面与配置中心设计.md`、`28_前端LobeUI适配设计.md` |
| 系统运行日志与可观测性 | Hermes `hermes_logging.py`、OpenHands `logger.py`、SWE-agent `utils/log.py`、OpenClaw `logs.ts` / `subsystem.ts`、CrewAI `event_bus.py`、pi `event-bus.ts` | `32_系统运行日志与可观测性设计.md` |
| Codex 开发日志与 SubAgent 开发流程 | OpenClaw SubAgent/session 日志、SWE-agent reviewer/hooks、CrewAI event bus、pi EventBus | `33_Codex开发日志与SubAgent开发流程设计.md` |

## 后续维护要求

新增或修改正式设计时，如果出现下面这类句子，必须补上源码地址或删除空泛引用：

```text
参考 Hermes
参考 OpenHands
参考 AutoGen
参考主流 Agent 系统
```

应改成：

```text
参考源码：Hermes Agent `agent/context_compressor.py` @ 75cd420b...
本项目采用的具体规则：threshold=0.50，protect_first_n=3，protect_last_n=20，target_ratio=0.20，压缩前先裁剪旧工具结果，压缩失败时不轮转会话。
```

每个最终设计文档都要写清楚“本项目最终怎么做”，源码索引只用于追溯来源。
