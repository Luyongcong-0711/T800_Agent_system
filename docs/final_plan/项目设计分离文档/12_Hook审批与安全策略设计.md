# Hook、审批与安全策略设计

## 参考源码标注

本文件中 Hook 拦截、审批前后检查、工具输入/输出可修改和高风险操作 gated execution，参考以下源码后按本项目 Runtime 审批体系重写：

| 本项目设计点 | 参考源码路径 | GitHub 地址 | 本项目落地 |
| --- | --- | --- | --- |
| before / after tool hook | `.research_repos\crewai\lib\crewai\src\crewai\hooks\tool_hooks.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/hooks/tool_hooks.py` | Hook 可以 block、改输入、改 ToolResult |
| 工具调用生命周期事件 | `.research_repos\crewai\lib\crewai\src\crewai\tools\tool_usage.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/tools/tool_usage.py` | tool_call_started / finished / error 事件 |
| Hook loader 和启动容错 | `.research_repos\openhands\openhands\app_server\app_conversation\hook_loader.py` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/openhands/app_server/app_conversation/hook_loader.py` | Hook 加载失败前端可见，启动时不静默吞错 |
| patch 审批前 staged apply | `.research_repos\swe-agent\sweagent\run\hooks\apply_patch.py` | `https://github.com/SWE-agent/SWE-agent/blob/0f4f3bba990e01ca8460b9963abdcd89e38042f2/sweagent/run/hooks/apply_patch.py` | 写文件类 Skill 先生成 diff，经审批后再提交 |

## 基本定义

Hook 是 Runtime 内部拦截点，不等于用户弹窗。

Approval 是用户决策机制。

低风险操作直接执行；中风险操作静默检查；高风险操作沙盒 dry-run 后生成审批请求。

## Hook 清单

| Hook | 位置 | 作用 | 用户是否可见 |
| --- | --- | --- | --- |
| before_model_call | 调模型前 | 裁剪工具列表、检查 token、注入系统提示 | 不可见 |
| after_model_output | 模型输出后 | 解析文本和 tool call，检查 invalid tool call | 不可见 |
| before_tool_validate | schema 校验前 | 检查工具是否存在、是否对当前用户开放 | 失败才可见 |
| after_tool_validate | schema 后 | 业务语义、安全语义、风险标记 | 风险或错误才可见 |
| before_tool_execute | 执行前 | 直接执行、沙盒、dry-run、approval、拒绝 | 状态栏可见 |
| after_sandbox_run | 沙盒后 | 生成 diff、影响范围、approval request | 高风险时可见 |
| before_commit | 审批后真实执行前 | 最后检查、备份、确认 approval 有效 | 通常不可见 |
| after_tool_execute | 工具结束后 | ToolResult、日志、UI 成功/失败事件 | 状态栏可见 |

第一版可以合并为：

```text
before_model_call
after_model_output
validate_and_policy_check
before_execute_or_approval
after_execute
```

## 高风险操作流程

```text
LLM 生成 tool call
  -> validate_tool_call
  -> policy_hook 判断风险
  -> 高风险进入 sandbox dry-run
  -> Runtime 收集 sandbox_result / diff / impact
  -> approval_node 发起一次审批
  -> 用户批准或拒绝
  -> before_commit 静默最终检查
  -> commit_execute 真实执行
  -> 记录 operation_id / rollback_token
  -> 返回 ToolResult
```

## Approval Request

```json
{
  "approval_id": "approval_001",
  "trace_id": "trace_001",
  "tool_call_id": "call_001",
  "tool_name": "apply_patch",
  "risk_level": "medium",
  "reasons": [
    "will_modify_workspace",
    "affects_multiple_files"
  ],
  "sandbox_result": {
    "ok": true,
    "tests": "passed"
  },
  "diff_summary": {
    "files_changed": 2,
    "insertions": 30,
    "deletions": 4
  },
  "rollback_available": true,
  "expires_at": "2026-05-28T12:30:00+08:00"
}
```

## 避免重复提醒

```text
同一个 tool_call_id 只能有一个 active approval_request。
其他 hook 只设置 requires_approval=true。
approval_node 聚合所有原因后一次性展示。
```

before_commit 检查：

- approval 是否仍有效。
- 文件是否被外部改动。
- 路径是否仍在 workspace。
- 是否已经创建备份。
- operation_id 是否唯一。
- 工具参数是否未被替换。

## 审批结果

| 结果 | 处理 |
| --- | --- |
| approved | 进入 before_commit 和真实执行 |
| rejected | 返回 ToolResult，模型改用其他方案或向用户解释 |
| revise_required | 用户要求修改参数，回到模型或参数编辑 UI |
| expired | 审批过期，重新生成审批请求 |

## 安全策略

- 模型不能自行声明操作已获批准。
- 审批只能由用户动作或受信任系统策略产生。
- 审批绑定 tool_call_id、args hash、trace_id。
- 参数变化后原审批失效。
- 高风险写操作必须有回滚或补偿说明。
- `user_profile` / `user_preference` 写入不需要审批，但必须有用户可见记录。
- `user_profile` 只能写 global scope；`user_preference` 可以写 global 或 workspace scope；`project_fact` / `project_rule` 只能写 workspace scope。
- 密钥、token、密码、私钥、强敏感身份标识默认不自动写入长期记忆。

## Skill 脚本审批

Skill 可以携带脚本，审批边界按脚本行为决定。

安装或更新 Skill 时：

- prompt / workflow / knowledge 变更记录版本和摘要。
- scripts 变更必须计算 checksum，并记录变更摘要。
- 新增依赖 Tool、MCP、数据库、网络或文件写权限时，必须触发配置审批。
- 降低权限可以直接生效，但仍记录审计事件。

运行 Skill 脚本时：

- 只读、无外部副作用脚本可以自动执行。
- 读 workspace、读数据库、读记忆的脚本需要权限检查。
- 写文件、写记忆、写图谱、调用网络或启动进程的脚本进入高风险审批链路。
- 写文件型 Skill 脚本必须先在沙盒 overlay 中生成 staged patch，不允许直接改真实 workspace。
- 审批请求必须包含 changed_files、diff_summary、diff_object_key、write_scope 校验结果和 rollback 策略。
- 审批绑定 `skill_id`、`skill_version`、`script_entry`、`args_hash`、`script_checksum` 和 `trace_id`。

模型不能通过自然语言声称某个 Skill 脚本已获批准；审批只能来自用户动作或受信任系统策略。

写文件型 Skill 审批请求示例：

```json
{
  "approval_id": "approval_skillrun_001",
  "trace_id": "trace_001",
  "tool_call_id": "call_001",
  "tool_name": "skill_document_cleaner_generate_report_patch",
  "risk_level": "high",
  "reasons": [
    "skill_script_file_write",
    "will_modify_workspace"
  ],
  "skill": {
    "skill_id": "document_cleaner",
    "skill_version": "0.1.0",
    "script_entry": "generate_report_patch",
    "script_checksum": "sha256...",
    "skill_run_id": "skillrun_001"
  },
  "write_scope": [
    "workspace/reports/**"
  ],
  "changed_files": [
    "workspace/reports/doc_001.md"
  ],
  "diff_summary": {
    "files_changed": 1,
    "insertions": 80,
    "deletions": 10
  },
  "artifacts": {
    "diff_object_key": "workspaces/default/runs/run_001/skill_runs/skillrun_001/diff.patch",
    "operation_plan_object_key": "workspaces/default/runs/run_001/skill_runs/skillrun_001/operation_plan.json"
  },
  "rollback_available": true,
  "expires_at": "2026-05-30T12:30:00+08:00"
}
```

审批通过后的 `before_commit` 额外检查：

- staged patch 对应的 `skill_run_id` 是否存在且未过期。
- 当前文件是否仍与 dry-run 时的 base hash 一致。
- diff 是否仍在声明的 `file_write` 范围内。
- approval 绑定的 args hash、script checksum、diff hash 是否未变化。
- rollback token 是否已经创建。

任何一项不满足，原审批失效，必须重新生成审批请求。
