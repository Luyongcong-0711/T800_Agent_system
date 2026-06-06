# SubAgent 并发编排与结果审核设计

## 参考源码标注

本文件中 SubAgent Tool、受控并发、上下文隔离、handoff 等价能力、终止条件和结果回主 Agent 审核，参考以下源码后按本项目命名和审批体系重写：

| 本项目设计点 | 参考源码路径 | GitHub 地址 | 本项目落地 |
| --- | --- | --- | --- |
| delegate_task / 子任务隔离 | `.research_repos\hermes-agent\tools\delegate_tool.py` | `https://github.com/nousresearch/hermes-agent/blob/75cd420b3ba1b83185020c6d4506d7cc53b12e2b/tools/delegate_tool.py` | `call_subagent_{agent_type}` |
| SubAgent spawn 参数 | `.research_repos\openclaw\src\agents\subagent-spawn.ts` | `https://github.com/openclaw/openclaw/blob/f2ba23424eacc7f567018e5d8862bdeddfa9c740/src/agents/subagent-spawn.ts` | read_scope、write_scope、timeout、sandbox、cleanup、attachments |
| Handoff 抽象 | `.research_repos\autogen\python\packages\autogen-agentchat\src\autogen_agentchat\base\_handoff.py` | `https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/python/packages/autogen-agentchat/src/autogen_agentchat/base/_handoff.py` | 本项目不使用 Handoff 名称，模型只看到 SubAgent Tool |
| GroupChat manager | `.research_repos\autogen\python\packages\autogen-agentchat\src\autogen_agentchat\teams\_group_chat\_base_group_chat_manager.py` | `https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_base_group_chat_manager.py` | team state、current_turn、speaker/candidate 后续扩展 |
| 终止条件 DSL | `.research_repos\autogen\python\packages\autogen-agentchat\src\autogen_agentchat\conditions\_terminations.py` | `https://github.com/microsoft/autogen/blob/027ecf0a379bcc1d09956d46d12d44a3ad9cee14/python/packages/autogen-agentchat/src/autogen_agentchat/conditions/_terminations.py` | max turns、timeout、token usage、external cancel |
| delegate work tool | `.research_repos\crewai\lib\crewai\src\crewai\tools\agent_tools\delegate_work_tool.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/tools/agent_tools/delegate_work_tool.py` | Agent 委托能力作为工具进入 Runtime |

## 定位

SubAgent 是带角色、目标、工具集合、预算、上下文边界和输出契约的执行单元。

P0 支持受控并发 SubAgent，但并发不是默认无边界扩散。主 Agent 始终负责：

- 任务拆分。
- 分配只读范围和写入范围。
- 控制并发数量、预算和超时。
- 汇总 SubAgent 输出。
- 审核冲突和风险。
- 生成最终结论或最终写入方案。

SubAgent 不直接决定最终结论，也不绕过主 Agent 直接提交最终变更。

## 两类 SubAgent 的边界

本项目中有两类名字相似但用途不同的 SubAgent，必须分开实现和记录：

| 类型 | 用途 | 运行位置 | 状态记录 | 是否属于产品 Runtime |
| --- | --- | --- | --- | --- |
| Codex 开发 SubAgent | 开发这个 Agent 系统时，主 Codex 把编码、测试、审查任务分给子 Agent | Codex 开发环境 | `33_Codex开发日志与SubAgent开发流程设计.md` 定义的 dev task / subagent report | 否 |
| 业务 Agent SubAgent | 系统上线后，用户对话中的主 Agent 调用 researcher、reviewer、planner 等子 Agent 完成任务 | Agent Runtime / LangGraph | run event、subagent task、SubAgentResult、audit log | 是 |

实际例子：

```text
Codex 开发 SubAgent：
  用户让 Codex 开发 Secret Store。
  主 Codex 分配一个 backend-subagent 写 backend/secret_store/**，
  再分配一个 tester-subagent 写 tests/secret_store/**。
  这些记录进入开发日志，不会出现在最终 Agent 产品的用户对话里。

业务 Agent SubAgent：
  用户在产品里问“帮我检查这个知识库入库失败的原因”。
  主 Agent 调用 call_subagent_log_analyst 分析日志，
  再调用 call_subagent_database_checker 检查 Milvus / Neo4j 健康状态。
  两个 SubAgent 的结果回到主 Agent，由主 Agent 汇总给用户。
```

P0 决策：

- 业务 SubAgent 默认是 Run 内的受控子任务，不默认进入 Job 系统。
- 主 Agent 可以并发调用多个业务 SubAgent，但必须声明 read_scope / write_scope。
- SubAgentResult 必须回到主 Agent 审核后，才能影响最终回答或后续写入。
- 如果某个 SubAgent 触发文档入库、embedding 重建、图谱构建、诊断包等长耗时后台任务，真正的长耗时部分创建 Job，SubAgent 只返回 `created_job_id` 和初步判断。
- Codex 开发 SubAgent 只写开发日志，不复用业务 Runtime 的 run_id/job_id。

## SubAgent 调用工具

一些 Agent 框架把“把任务交给另一个 Agent”叫 Handoff。这个词偏框架内部，本项目不作为模型可见名称和前端名称使用。

本项目统一使用 SubAgent Tool：

```text
call_subagent_{agent_type}
```

工具描述必须让模型一眼看懂：

```text
调用一个 {agent_type} 类型的 SubAgent 执行受控子任务。
SubAgent 只能使用分配给它的上下文、工具、读范围和写范围。
SubAgent 的结果必须回到主 Agent，由主 Agent 审核后才能进入最终结论。
```

示例：

```text
call_subagent_code_reviewer
call_subagent_researcher
call_subagent_frontend_designer
```

SubAgent Tool 参数：

```json
{
  "objective": "检查 Tool Registry 设计是否存在权限绕过风险",
  "mode": "readonly",
  "read_scope": [
    "项目设计分离文档/09_Tool体系与内置工具设计.md",
    "项目设计分离文档/12_Hook审批与安全策略设计.md"
  ],
  "write_scope": [],
  "allowed_tools": [
    "read_file",
    "search_files"
  ],
  "forbidden_tools": [
    "write_file",
    "apply_patch",
    "exec"
  ],
  "timeout_ms": 300000,
  "token_budget": 12000,
  "expected_output": "列出问题、证据路径、风险等级和建议修复方向"
}
```

返回结果必须是 `SubAgentResult`，不能直接变成最终回答：

```json
{
  "task_id": "subtask_001",
  "agent_type": "code_reviewer",
  "status": "completed",
  "summary": "发现 MCP tool 命名冲突需要在 inventory 阶段阻断。",
  "findings": [
    {
      "severity": "P1",
      "title": "MCP tool normalized name collision",
      "evidence": "两个 MCP tool 归一化后都叫 mcp_fs_read_file",
      "recommendation": "冲突时禁用冲突工具并提示用户"
    }
  ],
  "changed_files": [],
  "risks": [],
  "open_questions": [],
  "needs_main_review": true
}
```

LangChain 风格伪代码：

```python
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool


class CallSubAgentArgs(BaseModel):
    objective: str = Field(description="SubAgent 要完成的明确目标")
    mode: str = Field(description="readonly 或 write")
    read_scope: list[str] = Field(default_factory=list)
    write_scope: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    timeout_ms: int = Field(default=300000, ge=1000)
    token_budget: int = Field(default=12000, ge=1000)
    expected_output: str = Field(default="")


def make_subagent_tool(agent_type: str) -> StructuredTool:
    def call_subagent(**kwargs) -> dict:
        args = CallSubAgentArgs(**kwargs)
        task = subagent_scheduler.create_task(
            agent_type=agent_type,
            objective=args.objective,
            mode=args.mode,
            read_scope=args.read_scope,
            write_scope=args.write_scope,
            allowed_tools=args.allowed_tools,
            forbidden_tools=args.forbidden_tools,
            timeout_ms=args.timeout_ms,
            token_budget=args.token_budget,
            parent_run_id=current_run_id(),
        )
        subagent_scheduler.enqueue(task)
        return subagent_scheduler.wait_result(task.task_id).to_tool_result()

    return StructuredTool.from_function(
        name=f"call_subagent_{agent_type}",
        description=f"调用 {agent_type} 类型 SubAgent 执行受控子任务，结果回到主 Agent 审核。",
        args_schema=CallSubAgentArgs,
        func=call_subagent,
    )
```

## 市场调研归纳

主流 Agent 系统大多采用主控编排模型：

- Orchestrator-worker：主控拆任务，多个 worker 并行处理，主控综合结果。
- Supervisor / manager：manager 负责分派、检查、回收结果和最终响应。
- Agents-as-tools：子 Agent 被当成工具调用，调用结果回到主 Agent。
- Group / team：多个 Agent 可以对话或协作，但通常有 selector、manager、termination 或 final response 控制点。
- 本地编码 SubAgent：强调独立上下文、明确职责、工具边界和主线程集成。

这些模式共同点：

- 并发用于独立子任务，而不是让多个 Agent 同时争夺同一最终状态。
- 需要明确任务边界、上下文边界和输出格式。
- 写入型任务必须控制写入范围。
- 最终回答或最终提交由主控统一整理。
- 中间状态要可追踪、可恢复、可审计。

## 并发策略

P0 支持三类并发：

| 类型 | 是否并发 | 说明 |
| --- | --- | --- |
| 只读分析 | 可以并发 | 代码定位、资料总结、方案评审、日志分析 |
| 独立写入 | 可以并发 | 写入范围明确不重叠 |
| 共享写入 | 默认串行 | 同一文件、同一配置、同一数据库对象、同一状态文件 |

不能证明互不影响时，按串行处理。

## 写入范围

写入型 SubAgent 启动前必须声明 `write_scope`。

`write_scope` 可以是：

- 文件路径。
- 目录路径。
- 模块名。
- 配置 namespace。
- 数据库对象，例如 collection、label、relationship type。
- MinIO 对象路径前缀。
- 前端页面或组件范围。

示例：

```json
{
  "task_id": "subtask_backend_health",
  "agent_type": "backend-developer",
  "mode": "write",
  "write_scope": [
    "backend/storage/health.py",
    "backend/api/db_health.py"
  ]
}
```

禁止并发写入：

- 同一个文件。
- 父子目录范围互相覆盖。
- 同一个配置 key 或配置 namespace。
- 同一个 MinIO JSON / JSONL 状态文件。
- 同一个数据库 schema 对象。
- 同一个前端页面状态容器。

## 任务契约

SubAgentTask：

```json
{
  "task_id": "subtask_001",
  "agent_type": "code-mapper",
  "mode": "readonly",
  "objective": "定位数据库连接配置相关代码",
  "read_scope": ["backend", "config"],
  "write_scope": [],
  "allowed_tools": ["read_file", "search_files"],
  "forbidden_tools": ["write_file", "apply_patch", "exec"],
  "timeout_ms": 300000,
  "token_budget": 12000,
  "output_schema": "SubAgentResult",
  "requires_main_review": true
}
```

SubAgentResult：

```json
{
  "task_id": "subtask_001",
  "status": "completed",
  "summary": "数据库配置入口位于 config/database.py",
  "findings": [
    {
      "type": "code_path",
      "path": "backend/config/database.py",
      "reason": "定义 MinIO、Milvus、Neo4j 连接参数"
    }
  ],
  "changed_files": [],
  "evidence": [],
  "risks": [],
  "open_questions": [],
  "needs_main_review": true
}
```

## 主 Agent 审核

所有 SubAgent 输出必须回到主 Agent 审核。

审核内容：

- 是否完成目标。
- 是否超出 read_scope 或 write_scope。
- 是否调用了未授权工具。
- 多个 SubAgent 结论是否冲突。
- 写入型任务是否修改了未分配文件。
- 是否需要测试、回滚、补偿或用户确认。

主 Agent 输出最终结论时需要标记：

- 采用了哪些 SubAgent 结果。
- 舍弃了哪些结果，原因是什么。
- 存在哪些冲突或残余风险。
- 哪些变更需要用户审批。

## 并发调度流程

```text
main_agent
  -> analyze_task
  -> split_subtasks
  -> classify readonly / write / high_risk
  -> allocate read_scope / write_scope
  -> conflict_check
  -> spawn readonly tasks in parallel
  -> spawn non-overlapping write tasks in parallel
  -> serialize overlapping or high-risk tasks
  -> collect results
  -> main_review
  -> final answer / patch / approval request
```

## 冲突处理

发现冲突时：

- 只读结论冲突：主 Agent 标记冲突点，必要时再派一个只读 reviewer。
- 写入范围冲突：停止并发，改为串行。
- 已发生越界写入：主 Agent 拒绝直接合并，要求回滚或人工确认。
- 结果不完整：主 Agent 可要求原 SubAgent 补充，或重新拆分任务。
- 超时：保留已完成结果，未完成任务标记 timeout，不阻塞可独立完成的主流程。

## Memory 与上下文

P0 默认：

- SubAgent 不共享父 Agent 的完整短期上下文。
- SubAgent 只接收完成任务必要的上下文切片。
- SubAgent 可以读取主 Agent 分配的只读记忆摘要。
- SubAgent 不直接写长期记忆。
- 需要写长期记忆时，必须产出候选记忆，由主 Agent 审核后走 memory_upsert。

## 日志与恢复

运行期 SubAgent 日志进入本文件定义的 run event / subagent event；开发期 Codex SubAgent 的任务分配、写入范围、报告和验证结果进入 `33_Codex开发日志与SubAgent开发流程设计.md`，两者不能混用。

每个 SubAgent 都必须产生可审计记录：

```text
subagent_task_created
subagent_started
subagent_tool_call_started
subagent_tool_call_finished
subagent_completed
subagent_failed
subagent_result_reviewed
```

日志字段：

- task_id。
- parent_run_id。
- agent_type。
- read_scope。
- write_scope。
- allowed_tools。
- status。
- changed_files。
- result_summary。
- error_type。

Checkpoint 至少保存：

- 已创建的 SubAgentTask。
- 已完成的 SubAgentResult。
- 已分配的 write_scope。
- 主 Agent 审核状态。

## P0 限制

P0 先限制并发规模：

- 默认最大并发 SubAgent 数：3 到 5。
- 写入型并发必须显式开启。
- 高风险工具任务不并发。
- 同一个运行任务内只能由主 Agent 发起最终写入或最终回答。

后续可以扩展：

- 自动任务图规划。
- 更复杂的资源调度。
- 跨进程 SubAgent worker pool。
- 基于评估结果的自动结果仲裁。
