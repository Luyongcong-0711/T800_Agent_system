# Skill 脚本与版本治理设计

## 参考源码标注

本文件中 Skill 渐进披露、Skill loader、脚本沙盒、超时、staged patch 和 Hook 审批，参考以下源码后按本项目 Skill Runner 重写：

| 本项目设计点 | 参考源码路径 | GitHub 地址 | 本项目落地 |
| --- | --- | --- | --- |
| 渐进披露 | `.research_repos\hermes-agent\tools\tool_search.py` | `https://github.com/nousresearch/hermes-agent/blob/75cd420b3ba1b83185020c6d4506d7cc53b12e2b/tools/tool_search.py` | `skill_search` / `skill_view` / `skill_activate` |
| Skill loader | `.research_repos\openhands\openhands\app_server\app_conversation\skill_loader.py` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/openhands/app_server/app_conversation/skill_loader.py` | Skill manifest 加载、错误可视化、启动容错 |
| Hook loader | `.research_repos\openhands\openhands\app_server\app_conversation\hook_loader.py` | `https://github.com/All-Hands-AI/OpenHands/blob/e073659755487d831eb6eb4ef0e6a543f64fdb80/openhands/app_server/app_conversation/hook_loader.py` | Hook 加载失败不静默，前端可查看错误 |
| 命令超时和中断 | `.research_repos\swe-agent\sweagent\environment\swe_env.py` | `https://github.com/SWE-agent/SWE-agent/blob/0f4f3bba990e01ca8460b9963abdcd89e38042f2/sweagent/environment/swe_env.py` | Skill Script execution_timeout、total_timeout、连续超时阈值 |
| patch / staged write 思路 | `.research_repos\swe-agent\sweagent\run\hooks\apply_patch.py` | `https://github.com/SWE-agent/SWE-agent/blob/0f4f3bba990e01ca8460b9963abdcd89e38042f2/sweagent/run/hooks/apply_patch.py` | overlay diff 经审批后再提交到真实 workspace |
| Tool Hook | `.research_repos\crewai\lib\crewai\src\crewai\hooks\tool_hooks.py` | `https://github.com/crewAIInc/crewAI/blob/fca21b155c4f316ee63d4aa1725361aff392e47e/lib/crewai/src/crewai/hooks/tool_hooks.py` | before/after hook 可 block、改输入、改输出 |

## 定位

Skill 是提示词、流程、知识包、操作经验和可选脚本的组合。

Skill 可以携带脚本，但脚本不能绕过 Runtime 直接执行。脚本入口必须由 Skill Registry 解析、由 Skill Runner 执行，并进入权限、沙盒、审批、日志、ToolResult 体系。

P0 初始状态下没有用户业务 Skill。系统只内置 Skill 管理工具：

```text
skill_search
skill_view
skill_activate
skill_propose
skill_create_from_proposal
```

用户可以在对话中主动要求创建 Skill，例如“把这个流程保存成 Skill”。大模型也可以在发现某个流程未来会重复使用时，向用户建议创建 Skill，但不能直接创建；必须先生成 proposal，经用户同意后，才能调用创建工具写入 Skill manifest。

Skill 与 Tool 的边界：

| 类型 | 定位 |
| --- | --- |
| Skill | 打包提示词、流程、知识、脚本入口和依赖声明 |
| Skill Script | Skill 内部携带的可执行入口 |
| Tool | Runtime 暴露给模型的可治理能力 |
| Skill Runner | 执行 Skill Script 的受控运行器 |

模型不能直接执行 Skill 包里的脚本路径。Runtime 可以把某个 Skill Script 暴露成内部 Tool-like entrypoint，再按工具规则执行。

## P0 执行边界

P0 对 Skill Script 的执行边界：

- 正式支持 Python 脚本入口。
- Shell / 系统命令类脚本可以打包，但默认禁用或强审批，不作为 P0 常规能力。
- 默认只读。
- 默认禁网。
- 默认不能读取系统敏感路径。
- 默认不能读取模型 API key、数据库密码、MCP secret、Secret Store 对象或 `AGENT_MASTER_KEY`。
- 默认不能直接连接 MinIO、Milvus、Neo4j；需要数据库能力时，通过 Runtime 注入的受控 Tool / Connector。SecretResolver 只允许后端内部 Connector 调用，不能暴露给 Skill Script。
- 允许写文件，但必须在 manifest 中声明 `permissions.file_write` 范围，并走 Hook / 审批。
- 未声明写入范围的 Skill Script 即使代码尝试写文件，也必须被沙盒拦截。

写入型 Skill Script 不直接修改真实 workspace。P0 使用 staged write：

```text
Skill Script 在沙盒 overlay 中写入
  -> Runtime 收集 changed_files 和 diff
  -> Hook 生成审批请求
  -> 用户批准
  -> Runtime 通过受控文件写入工具把 diff 提交到真实 workspace
  -> 记录 operation_id / rollback_token
```

审批通过后不重新运行脚本来改真实文件，避免脚本第二次运行产生不同结果。

## Skill 渐进式披露

Skill 不采用“一次性全部塞进模型上下文”的方式。P0 采用渐进式披露：

```text
模型初始可见：
  skill_search
  skill_view
  skill_activate

模型按任务搜索 Skill：
  skill_search(query)
    -> 返回少量候选 Skill 的 id、名称、短描述、适用场景、风险摘要

模型需要深入了解某个 Skill：
  skill_view(skill_id)
    -> 返回该 Skill 的压缩说明、workflow 摘要、知识目录、entrypoint 摘要、权限摘要

模型确认需要使用某个 Skill：
  skill_activate(skill_id, reason)
    -> Runtime 记录激活事件
    -> 把该 Skill 的必要 prompt / workflow / knowledge 摘要注入后续模型上下文
    -> 把该 Skill 的脚本入口作为 entrypoint_tool_name 写入激活上下文

模型调用 Skill 脚本入口：
  skill_entrypoint_call(entrypoint_tool_name, args)
    -> Skill Runner 校验权限、沙盒、审批
    -> 返回 ToolResult
```

这样做的原因：

- Skill 的 prompt、流程、示例和知识包可能很长，直接全部暴露会消耗上下文。
- 大多数任务只需要少数 Skill，先搜索再查看可以降低无关信息干扰。
- Skill 脚本入口可能有权限风险，只有激活后才可通过 `skill_entrypoint_call` 和对应 `entrypoint_tool_name` 调用，更容易审计。
- 保持主系统 prompt 稳定，Skill 内容作为运行期上下文块注入，不污染全局系统提示词。

Skill discovery tools 的返回必须短小、结构化，方便模型判断是否继续查看。

## Skill 创建流程

P0 Skill 来源只有两种：

| 来源 | 是否允许 | 流程 |
| --- | --- | --- |
| 用户主动创建 | 允许 | 用户描述流程 -> 大模型整理 proposal -> 用户确认 -> 创建 Skill |
| 模型主动建议 | 允许但必须确认 | 模型发现重复流程 -> 调用 `skill_propose` -> 前端显示建议 -> 用户批准 -> 创建 Skill |
| 系统预装业务 Skill | 不做 | 初始 `skill_index.json` 可以为空 |
| 外部市场安装 | 不做 | P1 再考虑 |

Skill 创建不是直接把当前对话全文塞进 Skill。Runtime 必须把流程整理成结构化 proposal：

```json
{
  "proposal_id": "skillprop_001",
  "display_name": "合同入库清洗流程",
  "description": "把合同类文件清洗成适合入库和图谱抽取的结构化文本。",
  "when_to_use": ["用户上传合同", "合同版面复杂", "需要抽取合同主体和条款"],
  "workflow_steps": [
    "读取解析后的 Document Representation",
    "规范标题和条款编号",
    "提取合同主体、金额、日期和义务",
    "输出清洗后的 blocks 和 metadata"
  ],
  "knowledge_notes": [
    "合同中甲方、乙方、供应商、采购方可能是角色别名"
  ],
  "entrypoints": [
    {
      "name": "normalize_contract",
      "type": "prompt_workflow",
      "args_schema": {"document_id": "string"},
      "risk_level": "low",
      "script_required": false
    }
  ],
  "scripts": [],
  "permissions": {
    "file_read": ["workspace"],
    "file_write": [],
    "database_read": ["minio"],
    "database_write": [],
    "network": false
  },
  "source": {
    "thread_id": "thread_001",
    "message_ids": ["msg_010", "msg_018"]
  }
}
```

如果 Skill 需要脚本，proposal 必须额外写清楚脚本能力、输入输出、权限和风险。P0 允许脚本，但创建时遵循：

- 默认创建无脚本 Skill，即 `type=prompt_workflow`。
- 只有用户明确同意“创建带脚本的 Skill”时，才能生成 `scripts/`。
- 脚本必须是 Python 入口优先，Shell / 系统命令入口默认高风险。
- 带脚本 Skill 创建后默认 `enabled=false`，依赖、checksum、sandbox profile 校验通过后才能启用。
- 新增文件写、数据库写、联网、系统命令能力必须单独审批。

创建后的 MinIO 路径：

```text
skills/{workspace_id}/{skill_id}/{version}/skill.yaml
skills/{workspace_id}/{skill_id}/{version}/README.md
skills/{workspace_id}/{skill_id}/{version}/prompts/task.md
skills/{workspace_id}/{skill_id}/{version}/workflows/workflow.yaml
skills/{workspace_id}/{skill_id}/{version}/knowledge/notes.md
skills/{workspace_id}/{skill_id}/{version}/scripts/{entrypoint}.py
skills/{workspace_id}/{skill_id}/latest.json
skills/{workspace_id}/skill_index.json
```

用户批准流程：

```text
用户或模型提出“这个流程可以做成 Skill”
  -> 模型调用 skill_propose
  -> Runtime 生成 proposal 和 risk_summary
  -> 前端 ApprovalCard 展示 Skill 名称、用途、入口、权限、是否带脚本
  -> 用户批准
  -> 模型调用 skill_create_from_proposal 或 Runtime 自动完成创建
  -> Skill Registry 写入 MinIO manifest
  -> 校验依赖、checksum、权限
  -> 更新 skill_index.json
  -> 当前 run 写 skill_created 事件
  -> 新 Skill 可被 skill_search 搜到，但需要 skill_activate 后才进入模型上下文
```

LangChain Tool 伪代码：

```python
from pydantic import BaseModel, Field
from langchain_core.tools import tool


class SkillProposeArgs(BaseModel):
    display_name: str
    description: str
    when_to_use: list[str] = Field(default_factory=list)
    workflow_steps: list[str]
    knowledge_notes: list[str] = Field(default_factory=list)
    entrypoints: list[dict]
    script_required: bool = False
    source_message_ids: list[str] = Field(default_factory=list)


@tool("skill_propose", args_schema=SkillProposeArgs)
def skill_propose_tool(**kwargs) -> dict:
    """把当前对话中可复用的流程整理为 Skill 创建提案；不会直接创建 Skill。"""
    proposal = skill_authoring_service.create_proposal(
        workspace_id=current_workspace_id(),
        thread_id=current_thread_id(),
        proposed_by="model",
        **kwargs,
    )
    approval = approval_service.create(
        action="create_skill",
        risk_level=proposal.risk_level,
        payload=proposal.public_dict(),
    )
    return ToolResult.approval_required(
        tool="skill_propose",
        approval_id=approval.approval_id,
        data=proposal.public_dict(),
    ).to_dict()


class SkillCreateFromProposalArgs(BaseModel):
    proposal_id: str
    approval_id: str
    skill_id: str | None = None
    version: str = "0.1.0"


@tool("skill_create_from_proposal", args_schema=SkillCreateFromProposalArgs)
def skill_create_from_proposal_tool(
    proposal_id: str,
    approval_id: str,
    skill_id: str | None = None,
    version: str = "0.1.0",
) -> dict:
    """在用户批准后，把 Skill proposal 写入 Skill Registry。"""
    approval_service.assert_approved(approval_id, action="create_skill", proposal_id=proposal_id)
    skill = skill_authoring_service.materialize_proposal(
        workspace_id=current_workspace_id(),
        proposal_id=proposal_id,
        skill_id=skill_id,
        version=version,
    )
    skill_registry.reload(skill.skill_id)
    event_store.append_run_event({
        "type": "skill_created",
        "skill_id": skill.skill_id,
        "version": skill.version,
        "proposal_id": proposal_id,
    })
    return ToolResult.ok(skill.public_dict()).to_dict()
```

`skill_search` 返回示例：

```json
{
  "items": [
    {
      "skill_id": "document_cleaner",
      "display_name": "文档清洗 Skill",
      "version": "0.1.0",
      "description": "清洗上传文档并提取结构化 metadata",
      "when_to_use": ["上传文档解析前", "文档结构混乱需要规范化"],
      "entrypoint_count": 2,
      "risk_level": "medium",
      "requires_activation": true
    }
  ]
}
```

`skill_view` 返回示例：

```json
{
  "skill_id": "document_cleaner",
  "version": "0.1.0",
  "summary": "用于清洗上传文档、规范标题层级、提取 metadata。",
  "workflow_summary": [
    "读取文档解析结果",
    "规范段落和标题",
    "提取 metadata",
    "返回清洗结果对象"
  ],
  "knowledge_sections": [
    {
      "section_id": "format_rules",
      "title": "文档格式规则",
      "token_estimate": 900
    }
  ],
  "entrypoints": [
    {
      "name": "normalize_input",
      "tool_name_when_activated": "skill_document_cleaner_normalize_input",
      "risk_level": "medium",
      "requires_approval": false,
      "args_schema_summary": "document_id: string"
    }
  ],
  "permissions": {
    "file_read": ["workspace"],
    "file_write": [],
    "database_read": ["minio"],
    "database_write": [],
    "network": false
  }
}
```

`skill_activate` 必须写入事件：

```json
{
  "type": "skill_activated",
  "skill_id": "document_cleaner",
  "skill_version": "0.1.0",
  "reason": "用户上传文档需要清洗后再切片",
  "activated_entrypoint_tools": [
    "skill_document_cleaner_normalize_input"
  ],
  "model_visible_tool": "skill_entrypoint_call",
  "context_block_object_key": "workspaces/default/runs/run_001/skills/document_cleaner/context_block.json",
  "created_at": "2026-05-30T12:00:00+08:00"
}
```

激活状态保存到 run 的 `leaf_state.json`：

```json
{
  "active_skills": [
    {
      "skill_id": "document_cleaner",
      "version": "0.1.0",
      "activated_at": "2026-05-30T12:00:00+08:00",
      "entrypoint_tools": [
        "skill_document_cleaner_normalize_input"
      ],
      "model_visible_tool": "skill_entrypoint_call"
    }
  ]
}
```

LangChain 风格伪代码：

```python
from pydantic import BaseModel, Field
from langchain_core.tools import tool


class SkillSearchArgs(BaseModel):
    query: str = Field(description="当前任务需要的能力或流程")
    top_k: int = Field(default=5, ge=1, le=10)


@tool("skill_search", args_schema=SkillSearchArgs)
def skill_search(query: str, top_k: int = 5) -> dict:
    return skill_registry.search(query=query, top_k=top_k).to_tool_result()


class SkillViewArgs(BaseModel):
    skill_id: str
    version: str | None = None


@tool("skill_view", args_schema=SkillViewArgs)
def skill_view(skill_id: str, version: str | None = None) -> dict:
    return skill_registry.view_compact(skill_id=skill_id, version=version).to_tool_result()


class SkillActivateArgs(BaseModel):
    skill_id: str
    version: str | None = None
    reason: str


@tool("skill_activate", args_schema=SkillActivateArgs)
def skill_activate(skill_id: str, reason: str, version: str | None = None) -> dict:
    activation = skill_runner.activate(
        skill_id=skill_id,
        version=version,
        reason=reason,
        run_id=current_run_id(),
    )
    tool_inventory_service.refresh_for_run(current_run_id())
    return activation.to_tool_result()
```

## Skill 包结构

推荐结构：

```text
skill_root/
  skill.yaml
  README.md
  prompts/
    system.md
    task.md
  workflows/
    workflow.yaml
  knowledge/
    guide.md
    examples.json
  scripts/
    normalize_input.py
    extract_metadata.py
  tests/
    sample_input.json
    expected_output.json
```

P0 不要求每个目录都存在，但必须存在 `skill.yaml`。

## Manifest 字段

`skill.yaml` 至少包含：

```yaml
skill_id: document_cleaner
display_name: 文档清洗 Skill
version: 0.1.0
description: 清洗上传文档并提取结构化 metadata
owner: user

entrypoints:
  - name: normalize_input
    type: script
    runtime: python
    path: scripts/normalize_input.py
    args_schema:
      type: object
      required: [document_id]
      properties:
        document_id:
          type: string
    risk_level: medium
    sandbox_profile: skill_script_readonly
    timeout_ms: 30000
    max_stdout_bytes: 65536
    max_stderr_bytes: 65536
    max_output_object_bytes: 5242880
    write_mode: none
    output_schema: ToolResult
  - name: generate_report_patch
    type: script
    runtime: python
    path: scripts/generate_report_patch.py
    args_schema:
      type: object
      required: [document_id]
      properties:
        document_id:
          type: string
    risk_level: high
    sandbox_profile: skill_script_workspace
    timeout_ms: 60000
    write_mode: staged_patch
    file_write:
      - workspace/reports/**
    allow_create: true
    allow_modify: true
    allow_delete: false
    output_schema: ToolResult

dependencies:
  tools:
    - object_store_get
  mcp_servers: []
  models: []
  databases:
    - minio
  network: false

permissions:
  file_read:
    - workspace
  file_write:
    - workspace/reports/**
  database_read:
    - minio
  database_write: []
  network: false

checksums:
  scripts/normalize_input.py: sha256:...
```

## 版本规则

Skill 必须有版本号。

版本规则：

- 修改 prompt、workflow、knowledge：至少更新 patch 版本。
- 修改脚本逻辑、参数 schema、输出 schema：更新 minor 版本。
- 修改权限模型、删除入口、破坏兼容性：更新 major 版本。
- 每次安装或更新记录 `skill_id`、version、manifest hash、script checksum。

运行记录必须绑定：

```text
skill_id
skill_version
entrypoint
script_checksum
args_hash
trace_id
run_id
```

这样后续审计时可以知道某次执行到底用了哪个 Skill、哪个版本、哪个脚本内容和哪组参数。

## 依赖声明

Skill 必须显式声明依赖，不允许脚本运行时临时申请未知能力。

可声明依赖：

- 内置 Tool。
- MCP Server 和 MCP Tool。
- 模型能力，例如主对话模型、GraphRAG LLM、Embedding。
- 数据库连接，例如 MinIO、Milvus、Neo4j。
- 网络访问能力。
- 文件读写范围。

Runtime 加载 Skill 时，需要检查依赖是否存在、是否启用、是否对当前 workspace 开放。

依赖缺失时：

- Skill 可以安装为 disabled 状态。
- 不允许执行缺失依赖的 entrypoint。
- 前端配置页显示缺失依赖和修复入口。

## 脚本执行流程

```text
模型通过 skill_search / skill_view 找到 Skill
  -> 模型通过 skill_activate 激活 Skill
  -> Runtime 注入 Skill 上下文块并确认固定 skill_entrypoint_call 可见
  -> 模型调用 skill_entrypoint_call(entrypoint_tool_name, args)
  -> Skill Registry 找到 skill_id / version
  -> 校验 entrypoint 是否存在且已经激活
  -> 校验 args_schema
  -> 检查依赖和权限
  -> 检查 file_read / file_write 是否匹配当前 workspace 策略
  -> 根据 risk_level 进入 Hook / 沙盒 / 审批
  -> Skill Runner 在沙盒中执行脚本
  -> 转换 stdout / stderr / return value / exception
  -> 如果 write_mode=staged_patch，收集 diff、changed_files、operation plan
  -> 写入 skill_run 日志和原始产物
  -> 如果需要审批，返回 approval_required ToolResult
  -> 用户批准后由 Runtime 受控提交 diff
  -> 返回 ToolResult
  -> 写入 events/part-*.jsonl / operations.jsonl / audit log
```

脚本不得直接返回无限制原始输出。Runtime 需要截断 stdout、stderr，并按 ToolResult 协议包装。

## 写文件规则

Skill Script 允许写文件，但必须满足全部条件：

- entrypoint 声明 `write_mode: staged_patch`。
- entrypoint 声明 `file_write` 范围。
- `permissions.file_write` 包含该 entrypoint 的写入范围。
- 写入路径必须规范化为 workspace 内绝对路径后再做范围判断。
- 禁止通过 `..`、符号链接、快捷方式、挂载点逃逸 workspace。
- 未声明 `allow_delete: true` 时禁止删除文件。
- 未声明 `allow_create: true` 时禁止创建新文件。
- 未声明 `allow_modify: true` 时禁止修改已有文件。
- 批量写入超过文件数、字节数或目录范围时，自动升级为 critical 或拒绝。

P0 默认限制：

```yaml
skill_script_defaults:
  write_mode: none
  allow_create: false
  allow_modify: false
  allow_delete: false
  max_changed_files: 20
  max_changed_bytes: 1048576
  max_single_file_bytes: 262144
```

写入型脚本运行流程：

```text
1. Runtime 创建 skill_run_id。
2. Sandbox Manager 创建只包含允许读范围和临时 overlay 的沙盒。
3. Skill Runner 把脚本写入、依赖、参数和 scoped runtime bridge 注入沙盒。
4. 脚本在 overlay 中运行，不能直接改真实 workspace。
5. Runtime 枚举 overlay changed files。
6. 对每个 changed file 做路径规范化和 write_scope 校验。
7. Runtime 生成 diff.patch、diff_summary 和 operation_plan。
8. Hook 根据 diff、风险等级和 entrypoint 权限生成审批请求。
9. 用户批准后，Runtime 用受控 FileSystemConnector / apply_patch 提交 diff。
10. 提交成功后写 operation_id、rollback_token、skill_run_completed 事件。
```

写入型脚本 ToolResult 示例：

```json
{
  "ok": false,
  "tool": "skill_document_cleaner_generate_report_patch",
  "stage": "skill_script",
  "error_type": "approval_required",
  "retryable": false,
  "recoverable_by_model": false,
  "message_for_model": "The skill script produced a staged patch and requires user approval before committing it.",
  "message_for_user": "Skill 脚本生成了文件修改，需要确认后写入。",
  "next_action": "request_approval",
  "risk_level": "high",
  "skill": {
    "skill_id": "document_cleaner",
    "skill_version": "0.1.0",
    "entrypoint": "generate_report_patch",
    "skill_run_id": "skillrun_001"
  },
  "diff_summary": {
    "files_changed": 2,
    "insertions": 80,
    "deletions": 10
  },
  "artifacts": {
    "diff_object_key": "workspaces/default/runs/run_001/skill_runs/skillrun_001/diff.patch",
    "stdout_object_key": "workspaces/default/runs/run_001/skill_runs/skillrun_001/stdout.txt",
    "stderr_object_key": "workspaces/default/runs/run_001/skill_runs/skillrun_001/stderr.txt"
  }
}
```

审批通过后的成功结果：

```json
{
  "ok": true,
  "tool": "skill_document_cleaner_generate_report_patch",
  "stage": "skill_script_commit",
  "side_effect": true,
  "operation_id": "op_skillrun_001_commit",
  "reversible": true,
  "rollback_token": "rollback_skillrun_001",
  "data": {
    "changed_files": [
      "workspace/reports/doc_001.md"
    ]
  }
}
```

## Skill Runner 执行产物

每次脚本执行都必须保存可审计产物：

```text
workspaces/{workspace_id}/runs/{run_id}/skill_runs/{skill_run_id}/manifest.json
workspaces/{workspace_id}/runs/{run_id}/skill_runs/{skill_run_id}/args.json
workspaces/{workspace_id}/runs/{run_id}/skill_runs/{skill_run_id}/stdout.txt
workspaces/{workspace_id}/runs/{run_id}/skill_runs/{skill_run_id}/stderr.txt
workspaces/{workspace_id}/runs/{run_id}/skill_runs/{skill_run_id}/result.json
workspaces/{workspace_id}/runs/{run_id}/skill_runs/{skill_run_id}/diff.patch
workspaces/{workspace_id}/runs/{run_id}/skill_runs/{skill_run_id}/operation_plan.json
```

`manifest.json` 示例：

```json
{
  "schema_version": 1,
  "skill_run_id": "skillrun_001",
  "workspace_id": "default",
  "thread_id": "thread_001",
  "run_id": "run_001",
  "tool_call_id": "call_001",
  "skill_id": "document_cleaner",
  "skill_version": "0.1.0",
  "entrypoint": "generate_report_patch",
  "script_checksum": "sha256...",
  "args_hash": "sha256...",
  "sandbox_profile": "skill_script_workspace",
  "write_mode": "staged_patch",
  "status": "waiting_approval",
  "started_at": "2026-05-30T12:00:00+08:00",
  "finished_at": "2026-05-30T12:00:04+08:00"
}
```

模型默认只能看到 ToolResult 摘要和截断后的 stdout / stderr preview，不能直接读取完整原始日志。完整日志只能通过日志审计页面或受控 `safe_log_search` 摘要查看。

## 依赖和运行环境

P0 不允许 Skill Script 在运行时随意安装依赖。

依赖规则：

- Python 依赖必须在 manifest 中声明。
- 安装或更新 Skill 时构建运行环境，记录 lock hash。
- 运行时缺依赖，entrypoint 返回 `skill_dependency_missing`，不尝试在线安装。
- 需要联网下载依赖时，属于安装 / 更新阶段行为，不属于普通脚本执行行为。
- 构建失败时 Skill 可以安装为 disabled 状态，前端显示失败原因。

环境记录：

```yaml
runtime:
  python_version: "3.11"
  dependency_lock: "requirements.lock"
  environment_hash: "sha256..."
```

## 超时和失败熔断

P0 默认：

```yaml
skill_script_limits:
  default_timeout_ms: 30000
  max_timeout_ms: 300000
  max_consecutive_failures: 3
  max_stdout_bytes_for_model: 65536
  max_stderr_bytes_for_model: 65536
  raw_log_max_bytes: 5242880
```

失败处理：

- 超时后 Runtime kill 沙盒进程树，返回 `skill_script_timeout`。
- stdout / stderr 超过模型可见上限时截断，完整日志落 MinIO。
- 原始日志超过 `raw_log_max_bytes` 时继续截断并记录 `log_truncated=true`。
- 同一 workspace、同一 `skill_id`、同一 entrypoint 连续失败达到 3 次时，当前 run 内临时禁用该 entrypoint。
- 临时禁用只影响当前 run；是否全局禁用交给 Skill 管理页面或管理员策略。

## LangChain Tool 伪代码

```python
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool


class SkillEntrypointCallArgs(BaseModel):
    entrypoint_tool_name: str = Field(description="激活上下文里的 entrypoint_tools 条目")
    args: dict = Field(default_factory=dict)
    run_id: str
    thread_id: str


def make_skill_entrypoint_call_tool() -> StructuredTool:
    def skill_entrypoint_call(
        entrypoint_tool_name: str,
        args: dict,
        run_id: str,
        thread_id: str,
    ) -> dict:
        request = SkillRunRequest(
            workspace_id=current_workspace_id(),
            thread_id=thread_id,
            run_id=run_id,
            tool_call_id=current_tool_call_id(),
            entrypoint_tool_name=entrypoint_tool_name,
            args=args,
        )

        validation = skill_runner.validate_request(request)
        if not validation.ok:
            return validation.to_tool_result()

        sandbox_result = skill_runner.run_in_sandbox(request)
        if sandbox_result.requires_approval:
            approval = approval_service.create_from_skill_run(sandbox_result)
            return ToolResult.approval_required(
                tool="skill_entrypoint_call",
                approval_id=approval.approval_id,
                diff_summary=sandbox_result.diff_summary,
                artifacts=sandbox_result.artifacts,
            ).to_dict()

        return sandbox_result.to_tool_result()

    return StructuredTool.from_function(
        name="skill_entrypoint_call",
        description="执行已激活 Skill 的受控入口。entrypoint_tool_name 必须来自激活上下文。",
        args_schema=SkillEntrypointCallArgs,
        func=skill_entrypoint_call,
    )
```

## 风险等级

| 行为 | 风险等级 | 策略 |
| --- | --- | --- |
| 纯文本转换、JSON 结构整理、无外部访问 | low | 自动执行 |
| 读取 workspace 文件、读取 MinIO 对象、只读向量检索 | medium | 权限检查，必要时审批 |
| 写 workspace 文件、写长期记忆、写 Neo4j、发起网络访问 | high | 沙盒和审批 |
| 执行系统命令、删除文件、批量修改、访问敏感目录 | critical | 默认拒绝或强审批 |

脚本风险按实际能力和权限判定，不按语言后缀判定。

## 沙盒策略

P0 先支持 Python 类脚本优先落地。

默认限制：

- 默认禁网。
- 默认只挂载 workspace 和临时目录。
- 默认不能读取系统敏感路径。
- 默认不能读取模型 API key、数据库密码、MCP secret、Secret Store 对象或 `AGENT_MASTER_KEY`。
- 有 CPU、内存、进程数、执行时间和输出大小限制。
- 需要外部能力时，通过 Runtime 注入的受控 Tool / Connector 调用；脚本不能直接拿到 SecretResolver。

Shell / 系统命令类脚本可以随 Skill 打包，但默认高风险。只有 manifest 明确声明、用户审批通过、沙盒 profile 允许时才能执行。

## 更新审批

安装或更新 Skill 时按变更类型处理：

| 变更 | 策略 |
| --- | --- |
| 只改描述、示例、非执行知识文本 | 记录审计，可直接更新 |
| 改 prompt 或 workflow | 记录版本和 diff 摘要 |
| 改脚本内容 | 需要生成 checksum 和变更摘要 |
| 新增文件写、数据库写、网络、进程权限 | 必须审批 |
| 删除权限、降低风险等级 | 可直接生效，但必须记录 |
| 修改参数 schema 或输出 schema | 更新版本，并检查引用方兼容性 |

审批记录必须保存新旧 manifest hash、脚本 checksum、权限 diff 和批准人。

## 存储位置

Skill 包可以存储在 MinIO：

```text
skills/{workspace_id}/{skill_id}/{version}/skill.yaml
skills/{workspace_id}/{skill_id}/{version}/prompts/...
skills/{workspace_id}/{skill_id}/{version}/knowledge/...
skills/{workspace_id}/{skill_id}/{version}/scripts/...
skills/{workspace_id}/{skill_id}/latest.json
skills/{workspace_id}/skill_index.json
```

`latest.json` 只记录当前启用版本，不作为唯一历史。历史版本保留用于回滚和审计。

## 前端管理能力

P0 Skill 创建主要发生在对话中：用户主动要求或模型建议，前端用 ApprovalCard 展示 proposal 并让用户批准。Skill 管理页面 P0 可以先做只读和启用状态管理，完整编辑器后置。

Skill 管理页面后续需要支持：

- 查看 Skill 列表、版本、启用状态。
- 查看 prompt、workflow、knowledge、script entrypoints。
- 查看依赖 Tool、MCP、数据库、网络权限。
- 查看脚本风险等级、sandbox profile、checksum。
- 安装、禁用、升级、回滚 Skill。
- 对新增高风险权限发起审批。

P0 必做：

- `skill_search` 能看到用户创建的 Skill。
- 创建 proposal 的审批卡片能显示用途、步骤、入口、权限和脚本风险。
- 用户批准后能创建 Skill manifest 并更新 `skill_index.json`。
- Skill 详情页或管理页能查看 prompt、workflow、knowledge、entrypoint、权限、脚本 checksum。
- 带脚本 Skill 默认需要校验 sandbox profile 和 checksum 后启用。
