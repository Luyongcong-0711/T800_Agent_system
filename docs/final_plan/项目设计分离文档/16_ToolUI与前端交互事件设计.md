# Tool UI 与前端交互事件设计

## 基本原则

不要把用户可见描述放进工具参数。

原则：

```text
模型自然语言可以解释意图。
工具参数只放执行所需参数。
Runtime 事件负责稳定展示工具状态。
前端 UI 根据 tool metadata 和 event 渲染。
```

## 通用组件

```text
ToolCallCard
ToolStatusBadge
ToolProgressTimeline
ToolArgsPreview
ToolResultSummary
ToolErrorPanel
ApprovalPrompt
RetryButton
CancelButton
RawJsonViewer
```

## 工具状态

```text
queued
validating
waiting_approval
running
retrying
succeeded
failed
cancelled
rolled_back
```

## Tool UI Metadata

```json
{
  "tool_name": "local_file_search",
  "display_name": "本地文件搜索",
  "category": "filesystem",
  "icon": "search",
  "risk_level": "low",
  "show_args": ["query", "root"],
  "start_message": "正在搜索文件",
  "success_message": "搜索完成",
  "result_summary_fields": ["count", "results"]
}
```

## 专用 UI

| 工具 | UI |
| --- | --- |
| 图谱检索 | 节点、边、路径 |
| 文件搜索 | 文件列表 |
| 代码执行 | stdout / stderr / exit code |
| 网页浏览 | URL、标题、摘要 |
| 审批工具 | approve / reject 按钮 |
| 长任务 | 进度条和步骤 |
| 数据库连接测试 | 状态、延迟、错误类型、重试建议 |
| 记忆工具 | 摘要、来源、敏感标记、删除入口 |

## SSE 事件类型

```text
run_started
token
tool_call_started
tool_call_progress
tool_call_succeeded
tool_call_failed
approval_requested
approval_resolved
checkpoint_saved
compaction_started
compaction_finished
final
run_failed
```

事件结构：

```json
{
  "event_id": "evt_000123",
  "run_id": "run_001",
  "trace_id": "trace_001",
  "tool_call_id": "call_001",
  "type": "tool_call_progress",
  "payload": {
    "status": "running",
    "message": "正在检索向量库"
  },
  "created_at": "2026-05-28T12:00:00+08:00"
}
```

## 前端渲染规则

- token 事件追加到当前 assistant 消息。
- tool_call_started 创建 ToolCallCard。
- tool_call_progress 更新状态和进度。
- approval_requested 展示审批面板。
- tool_call_failed 展示错误摘要和可操作建议。
- final 关闭本次运行的流式状态。
- run_failed 展示整体失败原因。

## 断线恢复

- 前端记录最后收到的 `event_id`。
- 断线后重新连接时带上 `last_event_id`。
- Server 补发未收到事件。
- 如果事件缓冲已过期，前端调用 run status API 获取快照。

