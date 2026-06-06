# Codex 开发日志与 SubAgent 开发流程设计

## 定位

本文件定义用 Codex 主 Agent + Codex SubAgent 开发本 Agent 系统时的开发日志、任务调度、验证测试和验收规范。

目标：

```text
每个开发任务都有清晰目标。
每个 SubAgent 都有明确职责和不重叠写入范围。
每次代码改动都有开发日志、验证记录和验收结论。
主 Agent 负责最终汇总审核，不让 SubAgent 直接改最终结论。
```

这不是替代系统运行日志。它是开发期工作日志，用来保证后续真正开始编码时，每个模块怎么开发、谁负责、改了什么、测了什么、还剩什么问题，都能追踪。

## 和系统运行日志的区别

| 类型 | 记录对象 | 主要读者 | 存储 |
| --- | --- | --- | --- |
| 系统运行日志 | Agent 系统运行时的 API、MCP、模型、数据库、错误、组件状态 | 用户、开发者、排障工具 | 本地热日志 + MinIO |
| Run Event / Audit | 单次 Agent run 的业务事件、Tool、审批、回滚 | 前端、审计、模型安全工具 | MinIO run objects |
| Codex 开发日志 | 开发这个 Agent 系统时的任务、SubAgent 分工、代码改动、测试验收 | 主 Agent、开发者、后续接手者 | 开发仓库本地 Markdown / JSONL，后续可导入 MinIO |

## P0 开发日志目录

在系统还没开发完成前，开发日志先落到本地设计/开发仓库中：

```text
项目设计分离文档/_development_logs/
  index.md
  tasks/
    devtask_YYYYMMDD_001.md
  events/
    devtask_YYYYMMDD_001.jsonl
  subagents/
    devtask_YYYYMMDD_001/
      subtask_backend_runtime.md
      subtask_frontend_settings.md
      subtask_tests.md
  verification/
    devtask_YYYYMMDD_001_verification.md
  decisions/
    adr_YYYYMMDD_001.md
```

等 Agent 系统可运行后，开发日志可以同步进入 MinIO：

```text
workspaces/{workspace_id}/development/tasks/{dev_task_id}/manifest.json
workspaces/{workspace_id}/development/tasks/{dev_task_id}/events/part-000001.jsonl
workspaces/{workspace_id}/development/tasks/{dev_task_id}/subagents/{subtask_id}/report.md
workspaces/{workspace_id}/development/tasks/{dev_task_id}/verification/report.md
workspaces/{workspace_id}/development/tasks/{dev_task_id}/decisions/{adr_id}.md
workspaces/{workspace_id}/indexes/development_tasks_index.json
```

P0 先用本地 Markdown / JSONL，因为开发早期不能依赖尚未完成的系统能力。

## dev task manifest

每个开发任务一个 `devtask_*.md`。

模板：

```md
# devtask_20260530_001：实现 Secret Store

## 基本信息

- dev_task_id: devtask_20260530_001
- status: planned | in_progress | blocked | verifying | completed
- owner: main_codex
- workspace_id: default
- created_at: 2026-05-30T16:30:00+08:00
- related_design_docs:
  - 31_密钥与SecretStore设计.md
- priority: P0

## 目标

[本任务要完成什么]

## 范围

### 允许修改

- backend/secret_store/**
- backend/config/**
- frontend/app/settings/api/**

### 禁止修改

- unrelated module
- generated data
- old source design docs

## 子任务分配

| subtask_id | subagent | 职责 | 写入范围 | 状态 |
| --- | --- | --- | --- | --- |
| subtask_secret_backend | backend-architect | Secret Store 后端接口 | backend/secret_store/** | planned |

## 验收标准

- [ ] Secret 创建后 MinIO 不包含明文。
- [ ] 配置只保存 secret_ref。
- [ ] 日志脱敏测试通过。

## 风险与冲突

- [ ] 是否需要 key rotation UI。

## 最终结论

主 Agent 汇总填写。
```

## dev event JSONL

`events/{dev_task_id}.jsonl` 记录开发过程中的结构化事件。

事件类型：

```text
dev_task_created
dev_task_scope_defined
subagent_assigned
subagent_started
subagent_report_submitted
code_change_planned
file_changed
command_run
test_run_started
test_run_finished
review_finding
conflict_found
conflict_resolved
decision_recorded
verification_passed
verification_failed
dev_task_completed
```

示例：

```json
{
  "schema_version": 1,
  "event_id": "devevt_000001",
  "dev_task_id": "devtask_20260530_001",
  "type": "subagent_assigned",
  "created_at": "2026-05-30T16:30:00+08:00",
  "actor": "main_codex",
  "subagent": "backend-architect",
  "subtask_id": "subtask_secret_backend",
  "write_scope": ["backend/secret_store/**"],
  "read_scope": ["项目设计分离文档/31_密钥与SecretStore设计.md"],
  "status": "planned"
}
```

命令事件：

```json
{
  "schema_version": 1,
  "event_id": "devevt_000010",
  "dev_task_id": "devtask_20260530_001",
  "type": "command_run",
  "created_at": "2026-05-30T16:40:00+08:00",
  "actor": "main_codex",
  "command": "pytest tests/secret_store",
  "cwd": "backend",
  "exit_code": 0,
  "duration_ms": 8231,
  "stdout_summary": "12 passed",
  "stderr_summary": "",
  "full_output_ref": "verification/devtask_20260530_001_pytest_secret_store.log"
}
```

## SubAgent 分配原则

P0 开发时允许并发，但必须满足：

- 每个 SubAgent 有明确 `read_scope`。
- 每个会写文件的 SubAgent 必须声明 `write_scope`。
- 多个 SubAgent 的 `write_scope` 默认不能重叠。
- 如果确实要重叠，必须由主 Agent 串行合并，并记录 `scope_conflict_resolved`。
- SubAgent 输出必须回到主 Agent 汇总审核。
- SubAgent 不直接修改最终结论、不直接发布任务完成状态。
- 主 Agent 负责把 SubAgent 报告转成最终代码修改、最终文档修改或最终验收结论。

SubAgent 任务输入模板：

```md
# SubAgent Task

## subtask_id

subtask_secret_backend

## 目标

实现 Secret Store 后端接口和测试。

## read_scope

- 项目设计分离文档/31_密钥与SecretStore设计.md
- backend/config/**

## write_scope

- backend/secret_store/**
- backend/tests/secret_store/**

## 禁止事项

- 不修改前端。
- 不改数据库接口文档。
- 不写最终验收结论。

## 输出

- 修改摘要。
- 关键文件列表。
- 测试结果。
- 风险与未解决问题。
```

## 主 Agent 调度流程

```text
1. 读取最终设计文档。
2. 创建 dev_task manifest。
3. 拆分子任务。
4. 给每个 SubAgent 分配 read_scope / write_scope / 验收标准。
5. 并发执行互不重叠的子任务。
6. 收集 SubAgent 报告。
7. 主 Agent 审核冲突、重复、缺口和风险。
8. 主 Agent 应用最终代码或文档修改。
9. 运行验证测试。
10. 写 verification report。
11. 如有问题，重新分配修复子任务。
12. 所有 P0 验收通过后，写 dev_task_completed。
```

LangGraph 调度伪代码：

```python
from typing import TypedDict
from langgraph.graph import StateGraph, END

class DevTaskState(TypedDict):
    dev_task_id: str
    design_docs: list[str]
    subtasks: list[dict]
    subagent_reports: list[dict]
    conflicts: list[dict]
    verification: dict
    final_status: str

def create_dev_task_node(state: DevTaskState) -> dict:
    manifest = dev_log.create_task(
        design_docs=state["design_docs"],
        status="planned",
    )
    return {"dev_task_id": manifest.dev_task_id}

def plan_subtasks_node(state: DevTaskState) -> dict:
    subtasks = planner.split_by_module_and_write_scope(state["design_docs"])
    dev_log.append_event("dev_task_scope_defined", subtasks=subtasks)
    return {"subtasks": subtasks}

def spawn_subagents_node(state: DevTaskState) -> dict:
    runnable = ensure_non_overlapping_write_scopes(state["subtasks"])
    reports = subagent_runner.run_parallel(runnable)
    for report in reports:
        dev_log.append_event("subagent_report_submitted", report=report.safe_summary())
    return {"subagent_reports": reports}

def review_reports_node(state: DevTaskState) -> dict:
    conflicts = main_agent_reviewer.find_conflicts(state["subagent_reports"])
    return {"conflicts": conflicts}

def route_after_review(state: DevTaskState) -> str:
    return "resolve_conflicts" if state["conflicts"] else "apply_changes"

def verify_node(state: DevTaskState) -> dict:
    result = verification_runner.run_required_checks(state["dev_task_id"])
    dev_log.write_verification_report(result)
    return {"verification": result}

builder = StateGraph(DevTaskState)
builder.add_node("create_dev_task", create_dev_task_node)
builder.add_node("plan_subtasks", plan_subtasks_node)
builder.add_node("spawn_subagents", spawn_subagents_node)
builder.add_node("review_reports", review_reports_node)
builder.add_node("verify", verify_node)
builder.set_entry_point("create_dev_task")
builder.add_edge("create_dev_task", "plan_subtasks")
builder.add_edge("plan_subtasks", "spawn_subagents")
builder.add_edge("spawn_subagents", "review_reports")
builder.add_conditional_edges("review_reports", route_after_review, {
    "resolve_conflicts": "plan_subtasks",
    "apply_changes": "verify",
})
builder.add_edge("verify", END)
dev_task_graph = builder.compile()
```

## 写入范围锁

开发日志中必须记录写入范围，避免 SubAgent 互相覆盖。

写入范围锁对象：

```json
{
  "dev_task_id": "devtask_20260530_001",
  "locks": [
    {
      "lock_id": "devlock_001",
      "subtask_id": "subtask_secret_backend",
      "owner": "backend-architect",
      "write_scope": ["backend/secret_store/**"],
      "status": "active",
      "created_at": "2026-05-30T16:30:00+08:00"
    }
  ]
}
```

冲突判断：

```python
def ensure_non_overlapping_write_scopes(subtasks: list[dict]) -> list[dict]:
    for left, right in pairwise(subtasks):
        if glob_scopes_overlap(left["write_scope"], right["write_scope"]):
            dev_log.append_event(
                "scope_conflict_found",
                left=left["subtask_id"],
                right=right["subtask_id"],
                write_scope_left=left["write_scope"],
                write_scope_right=right["write_scope"],
            )
            raise ScopeConflict(left, right)
    return subtasks
```

## 开发验证报告

每个任务必须有 `verification/{dev_task_id}_verification.md`。

模板：

```md
# devtask_20260530_001 Verification

## 总结

- status: passed | failed | partial
- verified_by: main_codex
- verified_at: 2026-05-30T17:30:00+08:00

## 检查项

| 检查 | 命令 / 方法 | 结果 | 证据 |
| --- | --- | --- | --- |
| 单元测试 | pytest tests/secret_store | passed | 12 passed |
| 类型检查 | pyright backend | passed | 0 errors |
| 脱敏检查 | rg secret pattern | passed | 未发现明文 |

## 修改文件

- backend/secret_store/service.py
- backend/tests/secret_store/test_secret_store.py

## 风险

- key rotation UI 未进入本任务。

## 验收结论

通过 / 不通过。
```

## 验证测试验收流程

每个开发任务的最低验收流程：

```text
1. 设计对齐：确认相关最终设计文档已覆盖实现细节。
2. 静态检查：类型、lint、格式。
3. 单元测试：模块核心逻辑。
4. 集成测试：跨组件接口。
5. 安全检查：密钥、日志脱敏、权限、路径。
6. 回归检查：不影响已有 P0 功能。
7. 前端任务：必须有页面状态、错误状态、移动端/桌面布局验证。
8. 日志检查：新增功能必须写必要运行日志和开发日志。
9. 主 Agent 审核：检查 SubAgent 输出和实际修改一致。
10. 验收报告：写明通过项、失败项、未覆盖风险。
```

不同任务的追加验收：

| 任务类型 | 追加验收 |
| --- | --- |
| Secret / 凭据 | 明文不落盘、日志脱敏、wrong purpose 拒绝 |
| MCP | stdio/http 连接、断线、tool list、单 tool enable/disable |
| RAG / GraphRAG | 文档入库、Milvus 检索、Neo4j 只读查询、证据回源 |
| SSE / 运行事件 | 断线补偿、event_id 去重、分页 |
| 前端配置页 | create/read/update、错误提示、masked secret、不回显明文 |
| Skill Script | 沙盒、staged patch、审批、stdout/stderr 截断 |
| SubAgent | 并发范围不重叠、结果回主 Agent 审核 |

## 开发 ADR

重大技术决策必须写 ADR。

路径：

```text
项目设计分离文档/_development_logs/decisions/adr_YYYYMMDD_001.md
```

模板：

```md
# ADR 20260530-001：Secret Store 使用 MinIO 加密对象

## 状态

accepted

## 背景

[为什么出现这个决策]

## 决策

[最终选型]

## 影响

[开发、测试、运维影响]

## 替代方案

[为什么不选其他方案]

## 验证要求

[需要哪些测试证明这个决策落地]
```

## 主 Agent 最终汇总规范

主 Agent 在任务完成时必须输出：

```text
完成了什么
修改了哪些文件
验证了什么
没有验证什么
风险和后续任务
对应 dev_task_id
```

不能只写“已完成”。必须有证据。

## 任务状态机

```text
planned
  -> in_progress
  -> verifying
  -> completed

planned / in_progress
  -> blocked

verifying
  -> in_progress  # 验证失败返工
```

状态变更必须写 dev event：

```json
{
  "type": "dev_task_status_changed",
  "dev_task_id": "devtask_20260530_001",
  "from": "in_progress",
  "to": "verifying",
  "reason": "implementation finished, running tests"
}
```

## 开发日志 writer 伪代码

```python
class DevLogWriter:
    def __init__(self, root: Path):
        self.root = root

    def create_task(self, task: DevTaskManifest) -> None:
        write_markdown(self.root / "tasks" / f"{task.dev_task_id}.md", task.to_markdown())
        self.append_event(task.dev_task_id, {
            "type": "dev_task_created",
            "actor": "main_codex",
            "status": task.status,
        })
        self.update_index(task)

    def append_event(self, dev_task_id: str, event: dict) -> None:
        path = self.root / "events" / f"{dev_task_id}.jsonl"
        event = {
            "schema_version": 1,
            "event_id": new_dev_event_id(),
            "dev_task_id": dev_task_id,
            "created_at": now_iso(),
            **event,
        }
        append_jsonl(path, event)

    def write_subagent_report(self, dev_task_id: str, subtask_id: str, report: str) -> None:
        path = self.root / "subagents" / dev_task_id / f"{subtask_id}.md"
        write_markdown(path, report)
        self.append_event(dev_task_id, {
            "type": "subagent_report_submitted",
            "subtask_id": subtask_id,
            "report_path": str(path),
        })

    def write_verification_report(self, dev_task_id: str, report: VerificationReport) -> None:
        path = self.root / "verification" / f"{dev_task_id}_verification.md"
        write_markdown(path, report.to_markdown())
        self.append_event(dev_task_id, {
            "type": "verification_passed" if report.passed else "verification_failed",
            "report_path": str(path),
            "summary": report.summary,
        })
```

## P0 开发规范

- 所有开发任务先创建 dev task。
- 所有 SubAgent 先声明 read_scope / write_scope。
- 主 Agent 必须检查写入范围是否重叠。
- SubAgent 报告必须进入 `subagents/{dev_task_id}`。
- 代码修改后必须写 verification report。
- 新增模块必须同时补测试和运行日志。
- 涉及密钥、文件写入、数据库写入、网络、MCP 的任务必须补安全检查。
- 前端任务必须补交互状态和错误状态说明。
- 未验证项必须写清楚，不允许空泛说“未测试”。

## P0 开发规划入口

P0 总开发顺序已经写入：

```text
项目设计分离文档/35_P0开发规划与验收矩阵.md
```

开始真实编码前，主 Codex 需要基于该文件生成本地开发日志入口：

```text
项目设计分离文档/_development_logs/master_plan.md
```

`master_plan.md` 必须继承 `35_P0开发规划与验收矩阵.md` 的阶段顺序，并补充：

- P0 模块开发顺序。
- 每个模块对应的设计文档。
- 每个模块可用 SubAgent。
- 每个模块的 read_scope / write_scope。
- 验证命令。
- 验收标准。
- 风险优先级。

主 Agent 后续按 `master_plan.md` 调度 Codex SubAgent 开发。
