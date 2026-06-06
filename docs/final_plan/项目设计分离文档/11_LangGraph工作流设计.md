# LangGraph 工作流设计

## 推荐节点

```text
build_context
call_model
validate_tool_call
route_after_validation
approval_node
execute_tool
route_after_execution
final_answer
final_error
```

## 基本流程

```text
START
  -> build_context
  -> call_model
  -> validate_tool_call
      -> 无 tool call -> final_answer
      -> unknown_tool -> call_model 修复
      -> schema 错误 -> call_model 修复
      -> semantic 错误 -> call_model 修复或问用户
      -> safety 风险 -> approval_node
      -> 校验通过 -> execute_tool
  -> execute_tool
      -> transient error -> RetryPolicy
      -> model_repairable_error -> call_model
      -> permanent error -> final_error
      -> empty result -> call_model
      -> success -> call_model
```

## AgentState

```python
class AgentState(TypedDict):
    workspace_id: str
    user_id: str
    thread_id: str
    run_id: str
    knowledge_base_id: str | None
    messages: list
    memory_snapshot: dict
    compaction: dict | None
    context_window_tokens: int
    max_output_tokens: int
    repair_attempts: int
    max_repair_attempts: int
    validated_calls: list[dict]
    tool_results: list[dict]
    tool_errors: list[dict]
    subagent_tasks: list[dict]
    subagent_results: list[dict]
    write_scopes: list[dict]
    retry_budget: dict
    degraded_services: list[str]
    pending_approvals: list[dict]
    checkpoints: list[dict]
    final_answer: str | None
```

## LangGraph 图伪代码

Agent 主流程优先用 LangGraph `StateGraph` 表达。LangChain Tool 由 Tool Registry 生成，执行节点可以使用统一 `execute_tool` 节点；后续也可以替换为 `ToolNode`，但仍要先经过本系统的校验、Hook、审批和 ToolResult 包装。

后台 Job 的长流程也可以用 LangGraph 子图表达，但 Job 不复用主 Agent Run 的 `AgentState`。例如 `document_ingestion_job` 使用独立 `IngestionJobState`，由 Job Worker 调用解析、切块、embedding、图谱抽取和索引更新节点；Job 事件写入 `jobs/{job_id}`，不写入 `runs/{run_id}`。

```python
from langgraph.graph import StateGraph, START, END

builder = StateGraph(AgentState)

builder.add_node("build_context", build_context_node)
builder.add_node("call_model", call_model_node)
builder.add_node("validate_tool_call", validate_tool_call_node)
builder.add_node("approval_node", approval_node)
builder.add_node("execute_tool", execute_tool_node)
builder.add_node("final_answer", final_answer_node)
builder.add_node("final_error", final_error_node)

builder.add_edge(START, "build_context")
builder.add_edge("build_context", "call_model")
builder.add_edge("call_model", "validate_tool_call")

builder.add_conditional_edges(
    "validate_tool_call",
    route_after_validation,
    {
        "no_tool_call": "final_answer",
        "repair": "call_model",
        "approval_required": "approval_node",
        "execute": "execute_tool",
        "error": "final_error",
    },
)

builder.add_conditional_edges(
    "execute_tool",
    route_after_execution,
    {
        "success": "call_model",
        "empty_result": "call_model",
        "retry": "execute_tool",
        "model_repair": "call_model",
        "final_error": "final_error",
    },
)

builder.add_edge("approval_node", "execute_tool")
builder.add_edge("final_answer", END)
builder.add_edge("final_error", END)

agent_graph = builder.compile(checkpointer=runtime_checkpointer)
```

调用带 checkpoint 的 LangGraph 时必须把当前 `thread_id` 放进 config，避免不同会话的 checkpoint 混在一起：

```python
agent_graph.invoke(
    initial_state,
    config={"configurable": {"thread_id": initial_state["thread_id"]}},
)
```

`build_context_node` 负责在每次模型调用前构建长期记忆快照、计算 token、按 Hermes 策略压缩上下文。它可以直接调用长期记忆文档中的 `context_preflight_graph` 子图。

```python
def build_context_node(state: AgentState) -> dict:
    context_state = context_preflight_graph.invoke({
        "workspace_id": state["workspace_id"],
        "user_id": state["user_id"],
        "thread_id": state["thread_id"],
        "run_id": state["run_id"],
        "messages": state["messages"],
        "memory_snapshot": state.get("memory_snapshot", {}),
        "prompt_tokens": 0,
        "context_window_tokens": state["context_window_tokens"],
        "max_output_tokens": state["max_output_tokens"],
        "compaction": state.get("compaction"),
        "warnings": [],
    }, config={"configurable": {"thread_id": state["thread_id"]}})
    return {
        "messages": context_state["messages"],
        "memory_snapshot": context_state["memory_snapshot"],
        "compaction": context_state.get("compaction"),
    }
```

`execute_tool_node` 不直接把模型 tool call 发给外部服务，而是走 Runtime 治理：

```python
def execute_tool_node(state: AgentState) -> dict:
    tool_messages = []
    tool_results = []
    for call in state["validated_calls"]:
        metadata = tool_registry.get(call["name"])
        hook_result = hook_runner.before_tool_call(call, metadata)
        if hook_result.blocked:
            blocked = ToolResult.blocked(hook_result.reason)
            tool_results.append(blocked)
            tool_messages.append(blocked.to_tool_message())
            continue

        result = tool_executor.execute(call, metadata)
        result = hook_runner.after_tool_call(call, result)
        event_store.append_event(state["run_id"], tool_result_event(result))
        tool_results.append(result)
        tool_messages.append(result.to_tool_message())

    return {"messages": tool_messages, "tool_results": tool_results}
```

## 修复次数建议

| 错误 | 最大自动修复 |
| --- | --- |
| schema_validation_error | 2 次 |
| unknown_tool | 1 次 |
| graph_entity_disambiguation | 1 次 |
| 权限和安全错误 | 0 次 |

## Checkpoint

Checkpoint 用于恢复：

- 模型调用前后。
- 工具校验后。
- 高风险审批前。
- 工具执行前后。
- 长任务阶段切换时。

Checkpoint 不替代日志。Checkpoint 用于恢复运行状态，日志用于审计、回放和前端事件补发。

## Human-in-the-loop

需要人工参与的情况：

- 高风险工具执行。
- 写文件、执行命令、启动进程。
- 写高风险长期记忆或系统判断不应自动保存的敏感内容。
- 远程数据库连接配置测试失败。
- 需要用户补充缺失业务信息。

`user_profile` / `user_preference` 自动写入不需要暂停工作流等待审批，但必须写入用户可见记录，并在后续模型调用的 memory snapshot 中可追踪。`user_profile` 固定为 global scope，`user_preference` 可以是 global 或当前 workspace scope。

LangGraph 中审批节点应使用当前 LangGraph 的 human-in-the-loop 语义暂停工作流，等待用户 approve / reject / revise：

- 节点内调用 `interrupt(payload)`，payload 必须是可 JSON 序列化的审批摘要，不包含密钥或未脱敏原始输出。
- 图必须使用可持久化 checkpointer；调用和恢复都必须带同一个 `config={"configurable": {"thread_id": thread_id}}`。
- 用户操作后通过 `Command(resume=decision)` 恢复，`decision` 成为审批节点内 `interrupt()` 的返回值。
- `interrupt()` 前发生的副作用必须可幂等重放，或必须延后到审批通过后的 commit 节点执行。
- P0 的审批 payload 继续落 MinIO run event / leaf_state；LangGraph checkpoint 只负责恢复执行位置，不替代审计记录。

## SubAgent 调度

SubAgent 可以作为图中的 worker 节点执行，也可以被封装成 tool-like 能力。

```text
main_agent
  -> plan_subagent_tasks
  -> allocate_write_scopes
  -> spawn_subagents_parallel
  -> collect_subagent_results
  -> main_agent_review
  -> final_answer_or_patch
```

规则：

- P0 支持受控并发 SubAgent。
- 只读分析类 SubAgent 可以并发。
- 写文件、改配置、执行命令类 SubAgent 必须先分配互不重叠的写入范围。
- 写入范围按文件、目录、模块、数据库对象或配置 namespace 声明。
- 不能证明写入范围互不重叠时，任务必须串行。
- 并发 SubAgent 需要预算、超时、权限隔离、失败策略和结果合并策略。
- SubAgent 不自动继承父 Agent 的全部工具。
- SubAgent 输出必须回到主 Agent 汇总审核，不直接生成最终结论。
- 主 Agent 负责冲突判断、结果取舍、最终回答和最终写入决策。

SubAgent 任务结构：

```json
{
  "task_id": "subtask_001",
  "agent_type": "backend-developer",
  "mode": "write",
  "objective": "实现数据库连接健康检查接口",
  "allowed_tools": ["read_file", "apply_patch", "run_tests"],
  "read_scope": ["backend/config", "backend/storage"],
  "write_scope": ["backend/storage/health.py", "backend/api/db_health.py"],
  "timeout_ms": 600000,
  "token_budget": 20000,
  "requires_main_review": true
}
```

SubAgent 结果结构：

```json
{
  "task_id": "subtask_001",
  "status": "completed",
  "summary": "新增数据库健康检查接口",
  "changed_files": ["backend/storage/health.py", "backend/api/db_health.py"],
  "evidence": ["unit tests passed"],
  "risks": [],
  "needs_main_review": true
}
```

主 Agent 审核：

- 检查所有 SubAgent 是否遵守 read_scope / write_scope。
- 检查是否出现同一文件、同一配置 namespace、同一数据库对象的并发写入冲突。
- 对只读结论做交叉验证，标记冲突观点。
- 对写入结果做 diff 汇总、测试建议和回滚记录。
- 最终回答只由主 Agent 生成。
