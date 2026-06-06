# Tool 体系与内置工具设计

## 参考源码标注

本文件中 Effective Tool Inventory、MCP tool 命名、Skill 渐进披露、工具冲突检查和工具使用限制，参考以下源码后按本项目 Runtime 规则重写：

| 本项目设计点 | 参考源码路径 | GitHub 地址 | 本项目落地 |
| --- | --- | --- | --- |
| Skill / Tool 渐进披露 | `.research_repos\hermes-agent\tools\tool_search.py` | `https://github.com/nousresearch/hermes-agent/blob/75cd420b3ba1b83185020c6d4506d7cc53b12e2b/tools/tool_search.py` | 本项目仅 Skill 使用 `skill_search` / `skill_view` / `skill_activate` |
| Tool allow / deny / group 策略 | `.research_repos\openclaw\src\agents\tool-policy.ts` | `https://github.com/openclaw/openclaw/blob/f2ba23424eacc7f567018e5d8862bdeddfa9c740/src/agents/tool-policy.ts` | Tool Registry 合成时处理 role policy 和 tool group |
| 工具名唯一与 handoff 名冲突检查 | `.research_repos\autogen\python\packages\autogen-agentchat\src\autogen_agentchat\agents\_assistant_agent.py` | `https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/python/packages/autogen-agentchat/src/autogen_agentchat/agents/_assistant_agent.py` | P0 inventory 阶段发现冲突直接禁用冲突工具 |
| 工具调用解析、重复工具检测和 usage limit | `.research_repos\crewai\lib\crewai\src\crewai\tools\tool_usage.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/tools/tool_usage.py` | Tool Executor 统一记录 usage_count、max_usage_count、重复调用告警 |
| before / after tool hook | `.research_repos\crewai\lib\crewai\src\crewai\hooks\tool_hooks.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/hooks/tool_hooks.py` | Hook 可以 block、改输入、改 ToolResult |

## Tool 定义

Tool 是 Runtime 可治理、可校验、可执行、可记录结果的能力。

来源包括：

- 内置工具。
- 数据库工具。
- 文件工具。
- 网络工具。
- 记忆工具。
- 日志工具。
- MCP Tool。
- SubAgent Tool。

所有工具都注册到 Tool Registry。

## 模型可见工具清单

每次模型调用前，Runtime 都要生成一次 Effective Tool Inventory，也就是本次模型实际可见、可调用的工具集合。

P0 规则：

- 内置 Tool、数据库 Tool、记忆 Tool、会话 Tool、MCP Tool、SubAgent Tool 默认可以直接出现在模型工具列表中。
- Tool 和 MCP Tool 的 schema 通常只有名称、描述、参数 JSON schema 和少量 metadata，上下文成本可接受，不做渐进式披露。
- Skill 是唯一例外：Skill 的完整 prompt、workflow、knowledge 和脚本入口不一次性塞进模型上下文，必须使用渐进式披露。
- 模型初始只看到 Skill 管理工具，例如 `skill_search`、`skill_view`、`skill_activate`、`skill_propose`、`skill_create_from_proposal`。
- 只有 Skill 被查看或激活后，Runtime 才把相关 Skill 内容注入上下文；P0 使用固定 `skill_entrypoint_call` Tool 调用已激活脚本入口，不为每个 Skill entrypoint 动态注册新 Tool。

工具来源：

| 来源 | 是否直接给模型看 | 说明 |
| --- | --- | --- |
| built_in_tools | 是 | 文件、搜索、执行、计划、日志等系统内置能力 |
| database_tools | 是 | Milvus、Neo4j、MinIO 的参数化只读或受控写入工具 |
| memory_tools | 是 | memory_search、memory_get、memory_upsert、memory_delete |
| session_tools | 是 | thread、run、message、SSE 补偿和状态查询工具 |
| mcp_tools | 是 | 已启用 MCP server 中已启用的单个 MCP tool |
| subagent_tools | 是 | `call_subagent_{agent_type}` 形式的 SubAgent 调用工具 |
| skill_discovery_tools | 是 | skill_search、skill_view、skill_activate、skill_propose、skill_create_from_proposal |
| activated_skill_script_tools | 激活后可见 | 固定 `skill_entrypoint_call` Tool；已激活 entrypoint 作为 `entrypoint_tool_name` 参数出现在 Skill context 中 |
| full_skill_content | 否，按需注入 | Skill 的完整说明、流程、知识和示例 |

合成流程：

```text
1. 读取 Tool Registry 中当前 workspace 可用的内置工具、数据库工具、记忆工具和会话工具。
2. 读取 MCP capability snapshot，并过滤未启用的 server / tool。
3. 读取 SubAgent Registry，将允许当前主 Agent 调用的 SubAgent 转成 Tool schema。
4. 加入 Skill discovery tools，但不加入所有 Skill 的完整内容和脚本入口。
5. 读取当前 run 已激活的 Skill，把已激活 entrypoint 写入 Skill context；模型仍通过固定 `skill_entrypoint_call` Tool 调用。
6. 统一校验名称、参数 schema、风险等级、审批策略和 timeout。
7. 处理名称冲突；P0 发现冲突时禁用冲突工具并写入 tool_inventory_changed 事件。
8. 计算 inventory hash。
9. 保存 tool_inventory_snapshot 到 MinIO。
10. 把最终 Tool schema 传给模型。
```

MCP Tool 命名规则：

```text
mcp_{server_name}_{tool_name}
```

命名规范：

- `server_name` 和 `tool_name` 统一转成小写 snake_case。
- 非字母、数字、下划线字符替换为 `_`。
- 多个连续 `_` 折叠成一个。
- 原始 MCP tool name 保存在 metadata 中，不丢失。
- MCP Tool 不能覆盖内置 Tool、数据库 Tool、记忆 Tool、Skill discovery tool 或 SubAgent Tool。
- 同一个 MCP server 下 normalized tool name 冲突时，P0 禁用冲突项，并在 MCP 配置页面显示 `name_conflict`。

SubAgent Tool 命名规则：

```text
call_subagent_{agent_type}
```

例如：

```text
call_subagent_code_reviewer
call_subagent_researcher
call_subagent_frontend_designer
```

这里不使用 Handoff 作为对外名称。Handoff 在很多 Agent 框架里表示“把任务或控制权交给另一个 Agent”，但本项目只需要让模型明确知道这是调用 SubAgent，因此统一叫 SubAgent Tool。

Skill 脚本入口调用规则：

```text
skill_entrypoint_call(entrypoint_tool_name, args)
```

例如：

```text
skill_entrypoint_call(
  entrypoint_tool_name="skill_document_cleaner_normalize_input",
  args={"document_id": "doc_001"}
)
```

`skill_document_cleaner_normalize_input` 是已激活 entrypoint 的稳定参数值和审计标识，不是单独注册给模型的 Tool 名。P0 不为每个激活 Skill 动态新增 LangChain Tool，避免模型可见 schema 在运行中膨胀和漂移。

## tool_inventory_snapshot

`tool_inventory_snapshot` 是运行期审计、调试、恢复数据，不默认提供给大模型。

保存位置：

```text
workspaces/{workspace_id}/runs/{run_id}/tool_inventory_snapshot.json
```

用途：

- 复盘某次模型调用时，知道模型当时能看见哪些工具。
- 判断某个 Tool 为什么不可用，例如 MCP tool 被禁用、schema 冲突、权限策略拒绝。
- SSE 断线、Runtime 崩溃或 run 接管后，用 `tool_inventory_hash` 判断工具集合是否发生变化。
- 审计高风险工具是否在模型可见范围内。
- 调试 MCP capability snapshot 变化导致的 tool schema 漂移。

示例：

```json
{
  "schema_version": 1,
  "workspace_id": "default",
  "thread_id": "thread_001",
  "run_id": "run_001",
  "model_call_id": "modelcall_001",
  "inventory_hash": "sha256...",
  "visible_to_model": false,
  "tools": [
    {
      "name": "rag_search",
      "source": "built_in",
      "risk_level": "low",
      "enabled": true,
      "requires_approval": false,
      "schema_hash": "sha256..."
    },
    {
      "name": "mcp_github_search_issues",
      "source": "mcp",
      "server_name": "github",
      "original_tool_name": "search_issues",
      "risk_level": "medium",
      "enabled": true,
      "requires_approval": false,
      "schema_hash": "sha256..."
    },
    {
      "name": "skill_search",
      "source": "skill_discovery",
      "risk_level": "low",
      "enabled": true,
      "requires_approval": false,
      "schema_hash": "sha256..."
    },
    {
      "name": "skill_propose",
      "source": "skill_discovery",
      "risk_level": "medium",
      "enabled": true,
      "requires_approval": true,
      "schema_hash": "sha256..."
    }
  ],
  "disabled_tools": [
    {
      "name": "mcp_filesystem_delete_file",
      "source": "mcp",
      "reason": "tool_disabled_by_user"
    }
  ],
  "created_at": "2026-05-30T12:00:00+08:00"
}
```

LangChain 风格伪代码：

```python
from langchain_core.tools import StructuredTool


def build_effective_tool_inventory(ctx: RunContext) -> list[StructuredTool]:
    candidates = []
    candidates.extend(tool_registry.list_enabled_builtin_tools(ctx.workspace_id))
    candidates.extend(database_tool_registry.list_enabled_tools(ctx.workspace_id))
    candidates.extend(memory_tool_registry.list_enabled_tools(ctx.workspace_id))
    candidates.extend(session_tool_registry.list_enabled_tools(ctx.workspace_id))
    candidates.extend(mcp_adapter.list_enabled_tools(ctx.workspace_id))
    candidates.extend(subagent_registry.as_call_tools(ctx.main_agent_role))

    candidates.extend(skill_registry.discovery_tools())
    candidates.append(skill_runner.entrypoint_call_tool())

    normalized = tool_inventory_normalizer.normalize(candidates)
    allowed = tool_policy_engine.apply(ctx, normalized)
    collision_free = tool_inventory_normalizer.reject_name_conflicts(allowed)

    snapshot = tool_inventory_snapshot_store.save(
        workspace_id=ctx.workspace_id,
        thread_id=ctx.thread_id,
        run_id=ctx.run_id,
        model_call_id=ctx.model_call_id,
        tools=collision_free,
    )

    event_writer.append(
        run_id=ctx.run_id,
        type="tool_inventory_changed",
        payload={
            "inventory_hash": snapshot.inventory_hash,
            "snapshot_object_key": snapshot.object_key,
        },
    )

    return [tool.to_langchain_tool() for tool in collision_free]
```

## Tool Metadata

```json
{
  "name": "exec",
  "display_name": "执行命令",
  "category": "runtime",
  "risk_level": "high",
  "side_effect": true,
  "requires_approval": true,
  "sandbox_profile": "workspace_shell",
  "args_schema": "ExecArgs",
  "result_schema": "ToolResult",
  "ui_component": "CommandExecutionCard",
  "retry_policy": "no_auto_retry_for_write",
  "rollback_strategy": "none_or_compensation"
}
```

Runtime 根据 metadata 决定：

- 是否允许调用。
- 是否需要沙盒。
- 是否需要 dry-run。
- 是否需要用户审批。
- 是否可以自动重试。
- 是否需要 operation_id。
- UI 如何展示。
- 日志如何脱敏。

## 工具分组

| 分组 | 工具 |
| --- | --- |
| 文件类 | read_file、search_files、write_file、edit_file、apply_patch |
| 网络类 | web_search、web_fetch |
| 记忆和日志类 | memory_search、memory_get、memory_upsert、memory_delete、safe_log_search |
| 会话和任务类 | session_status、update_plan、sessions_spawn、sessions_yield |
| 执行类 | exec、process、python_sandbox、code_execution |
| 数据类 | rag_search、graphrag_search、vector_search、graph_schema_get、graph_entity_search、graph_expand_entity、graph_find_relationship、graph_find_paths、graph_get_evidence、graph_timeline_query、object_store_get |
| 调度类 | schedule_task |
| UI 事件类 | tool_status_event |

## 风险分级

| 风险等级 | 示例 | 默认策略 |
| --- | --- | --- |
| low | 读取允许范围文件、向量检索、会话状态 | 自动执行并记录日志 |
| medium | web_fetch、memory_get、写低风险草稿、写用户资料和偏好记忆 | 静默检查，前端可见 |
| high | 写文件、执行命令、启动进程、修改图谱、写高风险长期记忆 | 审批或沙盒 |
| critical | 删除大量文件、访问系统敏感目录、生产副作用 | 默认拒绝或强审批 |

## 文件工具

| 工具 | 风险 | 限制 |
| --- | --- | --- |
| read_file | 低到中 | 只能读 workspace，敏感文件审批或拒绝，大小限制 |
| search_files | 低 | 路径限制，返回数量限制，内容截断 |
| write_file | 中到高 | diff、备份、operation_id、approval |
| edit_file | 中到高 | 局部 diff、备份、rollback |
| apply_patch | 中到高 | patch 校验、dry-run、diff、approval、commit |

## 网络工具

| 工具 | 风险 | 限制 |
| --- | --- | --- |
| web_search | 中 | 只能访问配置好的搜索 provider，限制 top_k、地区、时间 |
| web_fetch | 中到高 | URL / DNS / IP / redirect / content-type / size 检查 |

web_search 不允许模型指定任意 URL。

## 记忆和日志工具

| 工具 | 说明 |
| --- | --- |
| memory_search | 搜索长期记忆摘要 |
| memory_get | 按需读取具体记忆，需范围限制和脱敏 |
| memory_upsert | 写入或更新长期记忆，需要 Hook；`user_profile` / `user_preference` 不需要审批 |
| memory_delete | 删除长期记忆，需要用户可见记录 |
| safe_log_search | 查询脱敏日志摘要，不暴露原始 headers、密钥、完整文件内容 |

原则：

```text
search 和 get 分离。
memory_search 只返回摘要。
memory_get 按需回源。
safe_log_search 只返回 safe summary。
写长期记忆需要可追踪的来源、前端可见性和类型策略。
user_profile / user_preference 自动写入不需要审批，但必须可见、可修正、可删除。
user_profile 是 global scope；user_preference 支持 global / workspace scope；project_fact / project_rule 是 workspace scope。
```

## 会话和子任务工具

| 工具 | 说明 |
| --- | --- |
| session_status | 查看当前会话状态、模型、工具、任务状态 |
| update_plan | 更新任务计划和步骤状态 |
| call_subagent_{agent_type} | 调用指定类型 SubAgent 执行受控子任务，结果回到主 Agent 审核 |
| sessions_spawn | 启动子任务或子 Agent |
| sessions_yield | 暂停等待子任务完成 |

子任务规则：

```text
子任务不继承父任务全部权限。
调用 call_subagent_{agent_type} 或 spawn 时显式指定 allowed_tools、budget、timeout。
yield 依赖事件或 checkpoint 恢复，不做粗暴轮询。
```

## 执行工具

| 工具 | 说明 | 规则 |
| --- | --- | --- |
| exec | 执行命令 | 强沙盒，高风险命令审批 |
| process | 管理 exec 启动的后台进程 | 只能管理沙盒内进程 |
| python_sandbox | 执行 Python 代码 | 沙盒，默认禁网 |
| code_execution | 多语言代码执行 | 资源限制、超时、输出截断 |

## Skill 脚本入口

Skill 可以携带脚本，但脚本不是绕过 Runtime 的自由执行文件。

Skill 脚本入口必须满足：

- 在 Skill manifest 中声明入口名称、参数 schema、运行时、风险等级和沙盒 profile。
- 默认只读；如需写文件，必须声明 `write_mode: staged_patch` 和 `file_write` 范围。
- 声明依赖的 Tool、MCP server、模型、数据库或网络权限。
- 使用 Skill 版本号和脚本 checksum 绑定执行记录。
- 通过 Skill Runner 调用，不允许模型直接拼接路径执行脚本。
- 执行结果统一返回 ToolResult，并写入事件、日志和操作记录。
- 写文件型脚本先在沙盒 overlay 里生成 staged patch，审批通过后由 Runtime 受控提交，不允许脚本直接改真实 workspace。

Skill 脚本执行可以被适配成内部 Tool：

```text
Skill package
  -> Skill Registry 解析 manifest
  -> Skill Runner 注册 skill_script entrypoint
  -> Tool Registry 暴露受控能力
  -> Tool Executor 执行审批、沙盒、重试和 ToolResult
```

脚本风险默认按能力而不是按文件类型判断：

| 脚本行为 | 风险 | 默认策略 |
| --- | --- | --- |
| 纯文本转换、格式整理、只读解析 | low | 自动执行并记录 |
| 读取 workspace 文件、调用只读数据库工具 | medium | 权限检查，必要时审批 |
| 写文件、更新记忆、写图谱、调用外部网络 | high | 沙盒或审批 |
| 执行系统命令、批量删除、访问敏感路径 | critical | 默认拒绝或强审批 |

## Tool、MCP、Skill、SubAgent 的区别

| 类型 | 本质 | 是否直接执行 | 是否需要 Runtime 治理 |
| --- | --- | --- | --- |
| Tool | 能力 | 是 | 是 |
| MCP | 外部工具协议 | 不是，需适配成 Tool | 是 |
| Skill | 提示词 / 流程 / 知识包 / 脚本包 | 可以携带脚本，但必须由 Skill Runner 受控执行 | 是 |
| SubAgent | 角色化执行单元 | 通过调度执行 | 是 |
