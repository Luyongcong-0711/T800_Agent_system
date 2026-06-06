# 前端 Lobe UI 适配设计

## 结论

P0 前端使用：

```text
React
TypeScript
Next.js
@lobehub/ui
Antd
antd-style
Zustand
fetch API
fetch + ReadableStream SSE client
```

前端使用 `@lobehub/ui` 的视觉系统和基础组件，不 fork `lobehub/lobehub` 完整产品，不要求后端适配 LobeHub 原始接口。

本项目前端需要新增 Agent API Adapter，把本项目后端 REST / SSE / 配置接口适配成 React store 和页面组件需要的数据结构。

## 本地调研结果

已将 `lobehub/lobe-ui` 拉取到：

```text
C:\Users\Administrator\Desktop\Agent design\前期技术栈设计\.research_repos\lobe-ui
```

本设计文件已放在最终设计文档目录：

```text
C:\Users\Administrator\Desktop\Agent design\前期技术栈设计\项目设计分离文档\28_前端LobeUI适配设计.md
```

当前仓库关键信息：

```text
repository: https://github.com/lobehub/lobe-ui.git
local_path: C:\Users\Administrator\Desktop\Agent design\前期技术栈设计\.research_repos\lobe-ui
branch: master
commit: 220a26b08c5b05c0125ea186742a18474dc138f7
package: @lobehub/ui
version: 5.15.5
license: MIT
module: ESM only
UI base: Antd + antd-style
peer dependencies:
  react ^19
  react-dom ^19
  antd ^6.1.1
  motion ^12
```

说明：

- `.research_repos/lobe-ui` 是调研参考仓库，不是本项目最终前端源码目录。
- 本项目开发时优先通过 npm / pnpm 安装 `@lobehub/ui`，不要直接复制 `.research_repos/lobe-ui/src` 到业务项目。
- 本地源码索引用于查看组件 props、导出入口、demo 写法和样式边界。
- 若后续 `@lobehub/ui` 版本升级，需要重新核对下面的源码路径和组件 props。

## Lobe UI 本地源码索引

### 仓库入口

| 用途 | 本地路径 |
| --- | --- |
| 仓库根目录 | `C:\Users\Administrator\Desktop\Agent design\前期技术栈设计\.research_repos\lobe-ui` |
| package / 依赖 / exports | `C:\Users\Administrator\Desktop\Agent design\前期技术栈设计\.research_repos\lobe-ui\package.json` |
| README / Next.js 转译说明 | `C:\Users\Administrator\Desktop\Agent design\前期技术栈设计\.research_repos\lobe-ui\README.md` |
| 主导出入口 | `C:\Users\Administrator\Desktop\Agent design\前期技术栈设计\.research_repos\lobe-ui\src\index.ts` |
| chat 子包导出入口 | `C:\Users\Administrator\Desktop\Agent design\前期技术栈设计\.research_repos\lobe-ui\src\chat\index.ts` |
| i18n 资源 | `C:\Users\Administrator\Desktop\Agent design\前期技术栈设计\.research_repos\lobe-ui\src\i18n\resources` |

### 主对话相关

| 本项目用途 | Lobe UI 源码路径 | 使用方式 |
| --- | --- | --- |
| 消息列表 | `...\src\chat\ChatList\ChatList.tsx` / `...\src\chat\ChatList\type.ts` | 直接使用 `ChatList`，通过 `data: ChatMessage[]` 传入消息 |
| 单条消息 | `...\src\chat\ChatItem\ChatItem.tsx` / `...\src\chat\ChatItem\type.ts` | `ChatList` 内部使用，也可在特殊卡片场景单独使用 |
| 消息输入区 | `...\src\chat\ChatInputArea\ChatInputArea.tsx` / `...\src\chat\ChatInputArea\type.ts` | 直接使用，绑定 `onInput`、`onSend`、`onStop` |
| 简单输入组件 | `...\src\chat\MessageInput\MessageInput.tsx` / `...\src\chat\MessageInput\type.ts` | 可用于轻量输入场景 |
| 顶部栏 | `...\src\chat\ChatHeader\ChatHeader.tsx` / `...\src\chat\ChatHeader\type.ts` | 主对话标题、模型状态、运行状态 |
| 消息弹窗编辑 | `...\src\chat\MessageModal\MessageModal.tsx` / `...\src\chat\MessageModal\type.ts` | 可用于消息编辑、详情查看 |
| token 标签 | `...\src\chat\TokenTag\TokenTag.tsx` / `...\src\chat\TokenTag\type.ts` | 可用于 token / context usage 小标签 |
| ChatMessage 类型 | `...\src\chat\types\chatMessage.ts` | 本项目 `AgentMessage -> ChatMessage` 适配依据 |

### Markdown、代码和 Diff

| 本项目用途 | Lobe UI 源码路径 | 使用方式 |
| --- | --- | --- |
| assistant Markdown | `...\src\Markdown\Markdown.tsx` / `...\src\Markdown\type.ts` | 直接使用，支持 chat variant、stream、Mermaid、Latex 等 |
| Markdown 代码块 | `...\src\Markdown\components\CodeBlock.tsx` | 默认由 Markdown 内部使用 |
| 代码高亮 | `...\src\Highlighter\index.ts` / `...\src\Highlighter\type.ts` | ToolResult JSON、日志、配置 schema 展示 |
| 文件 diff | `...\src\CodeDiff\CodeDiff.tsx` / `...\src\CodeDiff\type.ts` | old/new content diff |
| patch diff | `...\src\CodeDiff\PatchDiff.tsx` / `...\src\CodeDiff\type.ts` | Skill staged patch、审批 diff、rollback diff |
| HTML 预览 | `...\src\HtmlPreview\index.ts` / `...\src\HtmlPreview\type.ts` | P1 可用于报告类 artifact 预览 |
| Mermaid | `...\src\Mermaid\index.ts` / `...\src\Mermaid\type.ts` | GraphRAG 或流程图展示 |

### 工作台布局

| 本项目用途 | Lobe UI 源码路径 | 使用方式 |
| --- | --- | --- |
| 基础布局 | `...\src\Layout\index.ts` / `...\src\Layout\type.ts` | 工作台 shell |
| 左侧主导航 | `...\src\SideNav\SideNav.tsx` / `...\src\SideNav\type.ts` | 顶部 / 底部 action 区 |
| 可拖拽侧栏 | `...\src\DraggableSideNav\DraggableSideNav.tsx` / `...\src\DraggableSideNav\type.ts` | 会话列表侧栏可伸缩 |
| 可拖拽详情面板 | `...\src\DraggablePanel\DraggablePanel.tsx` / `...\src\DraggablePanel\type.ts` | 右侧 Tool / SubAgent / GraphRAG 详情 |
| Header | `...\src\Header\index.ts` / `...\src\Header\type.ts` | 页面顶部栏 |
| Flex 布局 | `...\src\Flex\index.ts` / `...\src\Flex\type.ts` | 页面内布局组合 |
| Grid 布局 | `...\src\Grid\index.ts` / `...\src\Grid\type.ts` | 配置页分栏 |

### 表单、列表和配置页

| 本项目用途 | Lobe UI 源码路径 | 使用方式 |
| --- | --- | --- |
| 配置表单 | `...\src\Form\Form.tsx` / `...\src\Form\type.ts` | API、数据库、MCP、Skill 配置 |
| 弹窗表单 | `...\src\FormModal\FormModal.tsx` / `...\src\FormModal\type.ts` | 新建/编辑配置 |
| 输入框 | `...\src\Input\index.tsx` / `...\src\Input\type.ts` | 普通输入、密码输入、数字输入 |
| 下拉选择 | `...\src\Select\index.ts` / `...\src\Select\type.ts` | provider、模型、状态筛选 |
| 分段控制 | `...\src\Segmented\index.ts` / `...\src\Segmented\type.ts` | 配置子页面切换 |
| Tabs | `...\src\Tabs\index.ts` / `...\src\Tabs\type.ts` | API 配置、Skill 详情多页签 |
| Slider + Input | `...\src\SliderWithInput\index.ts` / `...\src\SliderWithInput\type.ts` | context window、max output、timeout、top_k |
| 列表 | `...\src\List\index.ts` / `...\src\List\type.ts` | thread、MCP tool、Skill、memory 列表 |
| 可排序列表 | `...\src\SortableList\index.ts` / `...\src\SortableList\type.ts` | P1 可用于工具排序、配置排序 |
| 搜索框 | `...\src\SearchBar\index.ts` / `...\src\SearchBar\type.ts` | thread、memory、audit 搜索 |

### 弹层、状态和反馈

| 本项目用途 | Lobe UI 源码路径 | 使用方式 |
| --- | --- | --- |
| Drawer | `...\src\Drawer\index.ts` / `...\src\Drawer\type.ts` | MCP tool schema、Skill manifest、日志详情 |
| Modal | `...\src\Modal\index.ts` / `...\src\Modal\type.ts` | 审批、确认、危险操作 |
| Popover | `...\src\Popover\index.ts` / `...\src\Popover\type.ts` | 小型详情 |
| Collapse | `...\src\Collapse\index.ts` / `...\src\Collapse\type.ts` | ToolResult、GraphRAG evidence、日志折叠 |
| Alert | `...\src\Alert\index.ts` / `...\src\Alert\type.ts` | 错误、警告、审批提示 |
| Tag | `...\src\Tag\index.tsx` / `...\src\Tag\type.ts` | 状态、风险、relation_strength |
| Toast | `...\src\Toast\index.ts` | 操作反馈 |
| Skeleton | `...\src\Skeleton\index.ts` / `...\src\Skeleton\type.ts` | 加载态 |
| Empty | `...\src\Empty\index.ts` / `...\src\Empty\type.ts` | 空状态 |
| NeuralNetworkLoading | `...\src\NeuralNetworkLoading\index.ts` / `...\src\NeuralNetworkLoading\type.ts` | Agent 运行状态 |

### 文件、图标和头像

| 本项目用途 | Lobe UI 源码路径 | 使用方式 |
| --- | --- | --- |
| 文件图标 | `...\src\FileTypeIcon\FileTypeIcon.tsx` / `...\src\FileTypeIcon\type.ts` | 文档、附件、artifact |
| Material 文件图标 | `...\src\MaterialFileTypeIcon\MaterialFileTypeIcon.tsx` / `...\src\MaterialFileTypeIcon\type.ts` | 知识库文件列表 |
| 头像 | `...\src\Avatar\index.ts` / `...\src\Avatar\type.ts` | 用户、模型、Agent |
| 群组头像 | `...\src\GroupAvatar\index.ts` / `...\src\GroupAvatar\type.ts` | Agent Group / SubAgent |
| ActionIcon | `...\src\ActionIcon\index.ts` / `...\src\ActionIcon\type.ts` | 工具按钮 |
| ActionIconGroup | `...\src\ActionIconGroup\index.ts` / `...\src\ActionIconGroup\type.ts` | 消息操作按钮 |
| lucide icon 封装 | `...\src\Icon\index.ts` / `...\src\Icon\type.ts` | 常规图标按钮 |

### Demo 参考路径

开发时优先看 demo 目录理解 props 用法：

| 组件 | Demo 路径 |
| --- | --- |
| ChatList | `...\src\chat\ChatList\demos\index.tsx` |
| ChatItem | `...\src\chat\ChatItem\demos\index.tsx` |
| ChatInputArea | `...\src\chat\ChatInputArea\demos\index.tsx` |
| Markdown | `...\src\Markdown\demos\index.tsx` |
| CodeDiff | `...\src\CodeDiff\demos\index.tsx` |
| Form | `...\src\Form\demos\index.tsx` |
| DraggablePanel | `...\src\DraggablePanel\demos\index.tsx` |
| SideNav | `...\src\SideNav\demos\index.tsx` |
| List | `...\src\List\demos\index.tsx` |

Next.js 需要在 `next.config.js` 中转译：

```js
const nextConfig = {
  transpilePackages: ['@lobehub/ui'],
};

export default nextConfig;
```

应用入口需要包裹：

```tsx
import { ConfigProvider, ThemeProvider, I18nProvider } from '@lobehub/ui';
import { motion } from 'motion/react';

export function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <ConfigProvider motion={motion}>
      <ThemeProvider>
        <I18nProvider>{children}</I18nProvider>
      </ThemeProvider>
    </ConfigProvider>
  );
}
```

## 适配原则

```text
后端不适配 LobeHub。
前端适配本项目后端。
Lobe UI 提供视觉和基础交互。
业务页面和特殊组件由本项目自己实现。
```

落地规则：

- 不直接复用 LobeHub 产品项目的数据层、路由层和接口协议。
- 不把 MinIO / Milvus / Neo4j / LangGraph Runtime 改成 LobeHub 的后端模型。
- 页面优先使用 `@lobehub/ui` 组件组合。
- Lobe UI 没有的 Agent Runtime 专用能力，新增业务组件。
- 新增组件必须使用 `antd-style`、Lobe UI token、Antd 表单和 Lobe UI 布局风格。
- 所有后端调用先进入 `AgentApiClient`，页面不直接拼接 fetch。
- SSE 由 `RunEventStreamClient` 和 `JobEventStreamClient` 统一管理，页面只消费 store 状态。

## Lobe UI 可直接使用的组件

从本地 `src` 目录确认，P0 可直接使用这些组件：

| 组件 | 用途 |
| --- | --- |
| `Layout` | 工作台基础布局 |
| `SideNav` / `DraggableSideNav` | 左侧导航和可拖拽侧栏 |
| `ChatList` / `ChatItem` | 主对话消息列表 |
| `ChatInputArea` / `MessageInput` | 对话输入区 |
| `ChatHeader` | 对话顶部栏 |
| `Markdown` | assistant 文本、引用、公式、Mermaid、流式 Markdown |
| `Highlighter` | 代码块展示 |
| `CodeDiff` / `PatchDiff` | Skill staged patch、文件变更、回滚 diff |
| `Form` / `FormModal` | API、数据库、MCP、Skill 配置表单 |
| `List` / `SortableList` | 会话列表、MCP tool 列表、Skill 列表 |
| `Tag` / `Alert` / `Tooltip` | 状态、风险、错误提示 |
| `Drawer` / `Modal` / `Popover` | 配置详情、审批、日志详情 |
| `Collapse` / `Accordion` | ToolResult、GraphRAG 证据、日志折叠 |
| `SearchBar` | 历史会话、记忆、日志搜索 |
| `Segmented` / `Tabs` | 页面内子配置区分 |
| `SliderWithInput` | token、timeout、top_k、阈值配置 |
| `FileTypeIcon` / `MaterialFileTypeIcon` | 文档和附件展示 |
| `GroupAvatar` / `Avatar` | Agent Group、SubAgent、用户和模型头像 |
| `DraggablePanel` | 右侧运行详情、工具详情、GraphRAG 证据面板 |
| `Toast` | 操作反馈 |
| `Skeleton` / `Empty` / `NeuralNetworkLoading` | 加载、空状态和模型运行状态 |

## 需要自研的业务组件

Lobe UI 不负责本项目的 Agent Runtime 业务语义，因此这些组件必须自研：

| 自研组件 | 说明 | 可复用 Lobe UI |
| --- | --- | --- |
| `AgentWorkspaceShell` | 整体工作台壳，左导航、主区、右详情面板 | `Layout`、`SideNav`、`DraggablePanel` |
| `ThreadSidebar` | 多对话历史、置顶、归档、软删除 | `List`、`SearchBar`、`DropdownMenu` |
| `AgentChatView` | 当前 thread 的消息、SSE 流和输入区 | `ChatList`、`ChatInputArea`、`Markdown` |
| `RunEventRenderer` | 把 SSE event 转成消息、工具、审批、SubAgent 卡片 | `Collapse`、`Tag`、`Alert` |
| `ToolCallCard` | 展示 tool_call_started/update/finished | `Collapse`、`Highlighter` |
| `ToolResultCard` | 展示统一 ToolResult、错误、重试状态 | `Alert`、`CodeEditor`、`Tag` |
| `ApprovalCard` | 用户批准、拒绝、要求修改 | `Modal`、`Button`、`CodeDiff` |
| `SubAgentPanel` | SubAgent 状态、读写范围、输出审核 | `GroupAvatar`、`List`、`Tag` |
| `SkillRunPanel` | Skill 执行日志、stdout/stderr preview、staged patch | `CodeDiff`、`Tabs`、`Collapse` |
| `McpServerPanel` | MCP server 状态、capability snapshot | `List`、`Tag`、`Form` |
| `McpToolPolicyDrawer` | 单个 MCP tool 启用/禁用、risk/schema 摘要 | `Drawer`、Antd `Switch`、`Highlighter` |
| `DatabaseHealthPanel` | MinIO、Milvus、Neo4j 连接测试和失败诊断 | `Form`、`Alert`、`List` |
| `KnowledgeBasePipelineView` | 文档上传、解析、切片、向量化、图谱入库状态 | `List`、`FileTypeIcon`、`Tag` |
| `GraphRAGPathList` | 实体路径、关系方向、强弱、证据卡片 | `Collapse`、`Tag`、`Markdown` |
| `MemoryManager` | user_profile / user_preference CRUD 和启用状态 | `List`、`EditableText`、`Tag` |
| `ContextUsagePanel` | token、压缩状态、memory snapshot、tool inventory | `DraggablePanel`、`Tag` |
| `AuditTimeline` | run timeline、events、operations、rollback | `List`、`Collapse`、`CodeDiff` |
| `JobTaskCenter` | 后台 Job 列表、进度、取消、重试、事件时间线 | `List`、`Tag`、`Progress`、`Drawer`、`Collapse` |

## 页面到组件映射

| 页面 | 直接复用 | 自研重点 |
| --- | --- | --- |
| 主对话页 | `Layout`、`SideNav`、`ChatList`、`ChatInputArea`、`Markdown` | SSE 事件合并、ToolCallCard、ApprovalCard、SubAgentPanel |
| 多会话历史 | `List`、`SearchBar`、`DropdownMenu` | thread archive、soft delete、message pagination |
| API 配置页 | `Form`、`Tabs`、`Input`、`Select`、`SliderWithInput` | 主模型、GraphRAG LLM、Embedding、Rerank、压缩模型分区 |
| 数据库配置页 | `Form`、`Alert`、`Tag`、`List` | MinIO/Milvus/Neo4j health check 和诊断策略 |
| MCP 配置页 | `List`、`Tag`、`Drawer`、Antd `Switch` | capability snapshot、单 tool policy、schema_changed |
| Tool 权限页 | `List`、`Form`、`Tag` | Effective Tool Inventory、risk、approval、sandbox profile |
| Skill 管理页 | `List`、`CodeEditor`、`CodeDiff`、`Form` | skill manifest、对话创建 proposal、渐进披露、脚本权限、staged patch |
| Memory 页面 | `List`、`EditableText`、`SearchBar` | user_profile/user_preference、source evidence、snapshot |
| 知识库页面 | `List`、`FileTypeIcon`、`Tag`、`SearchBar` | active embedding version、pipeline 状态、检索测试 |
| 文档详情页 | `Markdown`、`CodeEditor`、`Collapse` | chunk、entity mention、GraphRAG provenance |
| 任务中心 / Jobs 页面 | `List`、`Tag`、`Progress`、`Drawer`、`Collapse` | Job SSE、jobs_index 分页、取消、重试、unknown_outcome/recovering |
| GraphRAG 结果页 | `Collapse`、`Tag`、`Markdown` | 关系方向、relation_strength、evidence cards |
| 日志审计页 | `List`、`Collapse`、`CodeDiff`、`Highlighter` | event replay、ToolResult、operations、skill_runs |
| 沙盒与安全页 | `Form`、`List`、`Alert` | sandbox profile、联网策略、危险操作规则 |
| 评估页面 | `List`、`Form`、`Tag` | provider matrix、SSE reconnect、ToolResult ordering |

## Agent API Adapter

前端不得散落调用后端 API。所有接口通过 `AgentApiClient`：

P0 前端启动后先调用 `/bootstrap`，把默认 `workspace_id=default` 写入 `useIdentityStore`。除 `/bootstrap` 和用户全局资料接口外，业务请求都必须拼到 `/workspaces/{workspace_id}/...` 下。

```ts
export class AgentApiClient {
  constructor(
    private readonly baseUrl: string,
    private readonly tokenProvider: TokenProvider,
    private readonly workspaceIdProvider: () => string,
  ) {}

  bootstrap(): Promise<BootstrapResult> {}

  listThreads(params: ListThreadsParams): Promise<ThreadSummary[]> {}
  getThread(threadId: string): Promise<ThreadDetail> {}
  listMessages(threadId: string, params: MessagePageParams): Promise<MessagePage> {}
  createThread(input: CreateThreadInput): Promise<ThreadDetail> {}
  createRun(threadId: string, input: CreateRunInput): Promise<RunDetail> {}
  cancelRun(runId: string): Promise<void> {}
  listJobs(params: JobListParams): Promise<JobPage> {}
  getJob(jobId: string): Promise<JobDetail> {}
  createJob(input: CreateJobInput): Promise<JobDetail> {}
  cancelJob(jobId: string): Promise<void> {}
  retryJob(jobId: string): Promise<JobDetail> {}
  approve(approvalId: string, input: ApprovalInput): Promise<ApprovalResult> {}
  reject(approvalId: string, input: ApprovalRejectInput): Promise<ApprovalResult> {}

  listMcpServers(): Promise<McpServerSummary[]> {}
  setMcpToolEnabled(input: McpToolPolicyInput): Promise<McpToolPolicy> {}

  getDatabaseHealth(): Promise<DatabaseHealthSummary> {}
  testDatabaseConnection(input: DatabaseConnectionTestInput): Promise<DatabaseHealthResult> {}

  listSecrets(params?: SecretListParams): Promise<SecretSummary[]> {}
  createSecret(input: CreateSecretInput): Promise<SecretCreated> {}
  getSecretReferences(secretId: string): Promise<SecretReference[]> {}

  listMemories(params: MemoryListParams): Promise<MemoryRecord[]> {}
  updateMemory(memoryId: string, input: MemoryUpdateInput): Promise<MemoryRecord> {}

  listRunEvents(runId: string, params: EventPageParams): Promise<RunEventPage> {}
  getToolInventory(runId: string): Promise<ToolInventorySnapshot> {}
  getSkillRun(skillRunId: string): Promise<SkillRunDetail> {}
}
```

SSE 单独封装：

```ts
export type RunEventStreamOptions = {
  workspaceId: string;
  runId: string;
  afterEventId?: string;
  signal: AbortSignal;
  onEvent: (event: RunEvent) => void;
  onError: (error: StreamError) => void;
  onClosed: () => void;
};

export async function connectRunEventStream(options: RunEventStreamOptions): Promise<void> {
  // fetch + ReadableStream
  // parse SSE frames
  // dedupe by event_id
  // update last_event_id
}

export type JobEventStreamOptions = {
  workspaceId: string;
  jobId: string;
  afterEventId?: string;
  signal: AbortSignal;
  onEvent: (event: JobEvent) => void;
  onError: (error: StreamError) => void;
  onClosed: () => void;
};

export async function connectJobEventStream(options: JobEventStreamOptions): Promise<void> {
  // fetch + ReadableStream
  // parse SSE frames
  // dedupe by event_id
  // update last_event_id_by_job
}
```

## Store 划分

P0 使用 Zustand，按业务域拆分：

| Store | 说明 |
| --- | --- |
| `useThreadStore` | thread 列表、activeThreadId、归档、软删除 |
| `useMessageStore` | 当前 thread 消息分页、临时流式消息、消息去重 |
| `useRunStore` | activeRunId、lastEventId、SSE 状态、取消和恢复 |
| `useJobStore` | Job 列表、activeJobId、lastEventIdByJob、SSE 状态、取消、重试和恢复 |
| `useToolStore` | ToolCallCard、ToolResult、tool inventory、MCP tool 启用状态 |
| `useConfigStore` | API、数据库、MCP、Tool 权限配置缓存 |
| `useMemoryStore` | 长期记忆列表、启用状态、来源和 memory snapshot |
| `useKnowledgeBaseStore` | 知识库、文档、chunk、入库状态 |
| `useAuditStore` | run timeline、errors、operations、rollback 状态 |
| `useSkillStore` | Skill 列表、manifest、entrypoint、skill proposals、skill_runs |
| `useSubAgentStore` | active subagents、结果审核、冲突状态 |
| `useIdentityStore` | P0 默认用户、role、workspace_id、workspace_role、bootstrap feature flags |

## 目录结构建议

```text
frontend/
  app/
    layout.tsx
    providers.tsx
    (workspace)/
      chat/[threadId]/page.tsx
      jobs/page.tsx
      knowledge-bases/page.tsx
      memory/page.tsx
      settings/api/page.tsx
      settings/database/page.tsx
      settings/mcp/page.tsx
      settings/tools/page.tsx
      settings/skills/page.tsx
      audit/runs/[runId]/page.tsx
  src/
    api/
      agentApiClient.ts
      sseParser.ts
      runEventStreamClient.ts
      jobEventStreamClient.ts
      schemas/
    stores/
      useThreadStore.ts
      useMessageStore.ts
      useRunStore.ts
      useJobStore.ts
      useToolStore.ts
      useSkillStore.ts
      useIdentityStore.ts
    components/
      workspace/
      chat/
      tool/
      approval/
      jobs/
      mcp/
      skill/
      memory/
      database/
      graphrag/
      audit/
    styles/
      theme.ts
```

## 关键实现规则

### ChatList 数据适配

`@lobehub/ui/chat` 的 `ChatList` 接收 `ChatMessage[]`，本项目需要把后端 `Message` / `RunEvent` 映射为 UI message：

```ts
function toLobeChatMessage(message: AgentMessage): ChatMessage {
  return {
    id: message.message_id,
    role: message.role,
    content: message.content_text ?? '',
    createAt: new Date(message.created_at).getTime(),
    updateAt: new Date(message.updated_at).getTime(),
    extra: {
      threadId: message.thread_id,
      runId: message.run_id,
      citations: message.citations,
      toolCalls: message.tool_calls,
      approvals: message.approvals,
    },
  };
}
```

Tool、审批、SubAgent 不一定都变成普通文本消息。P0 采用：

```text
assistant 文本 -> ChatMessage.content
tool call -> ChatItem.messageExtra 中渲染 ToolCallCard
approval -> ChatItem.messageExtra 中渲染 ApprovalCard
subagent -> 右侧 SubAgentPanel 或 messageExtra
GraphRAG evidence -> messageExtra / 右侧证据面板
```

### SSE 事件合并

```text
assistant_delta
  -> 更新临时 assistant message

assistant_message
  -> 固化完整 assistant message

tool_call_started / tool_call_update / tool_call_finished
  -> 更新 toolStore，再映射到 ToolCallCard

approval_requested
  -> 更新 runStore.pendingApprovals，并显示 ApprovalCard

subagent_started / subagent_completed / subagent_failed
  -> 更新 useSubAgentStore

skill_patch_staged
  -> 更新 useSkillStore，并显示 SkillRunPanel / ApprovalCard
```

### 配置页分区

API 配置页使用 `Tabs` 或 `Segmented` 分区：

```text
主对话模型
GraphRAG LLM
Embedding
Rerank
压缩模型
备用模型
通用代理和密钥策略
```

数据库配置页使用独立分组：

```text
MinIO
Milvus
Neo4j
Redis 缓存状态
健康检查结果
失败诊断
```

MCP 配置页：

```text
Server 列表
Capability Snapshot
Tool 列表
单 Tool 启用 / 禁用
Schema changed 标记
最近连接错误摘要
```

Skill 管理页：

```text
Skill 列表
Skill 详情
Skill Proposal 审批
Manifest
Entrypoints
权限和风险
脚本执行记录 skill_runs
staged patch / diff
```

## 端到端伪代码

这一节说明“已有 UI 砖块直接用 Lobe UI；独有 Agent 功能自己写业务组件；所有接口通过 Agent API Adapter 接后端”的具体开发方式。

### 1. Provider 接入 Lobe UI

文件：

```text
frontend/app/providers.tsx
```

伪代码：

```tsx
'use client';

import { ConfigProvider, I18nProvider, ThemeProvider, ToastHost } from '@lobehub/ui';
import chatZhCN from '@lobehub/ui/i18n/resources/zhCn/chat';
import formZhCN from '@lobehub/ui/i18n/resources/zhCn/form';
import { motion } from 'motion/react';
import type { ReactNode } from 'react';

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ConfigProvider motion={motion}>
      <I18nProvider resources={[chatZhCN, formZhCN]}>
        <ThemeProvider>
          {children}
          <ToastHost />
        </ThemeProvider>
      </I18nProvider>
    </ConfigProvider>
  );
}
```

Next.js 根布局：

```tsx
import { Providers } from './providers';

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
```

### 2. Agent API Adapter

页面和组件不直接 `fetch('/runs/...')`。所有请求先进入 `AgentApiClient`。

文件：

```text
frontend/src/api/agentApiClient.ts
```

伪代码：

```ts
export type TokenProvider = () => Promise<string | undefined>;

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly body?: unknown,
  ) {
    super(message);
  }
}

type RequestOptions = {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  query?: Record<string, string | number | boolean | undefined>;
  body?: unknown;
  signal?: AbortSignal;
};

export class AgentApiClient {
  constructor(
    private readonly baseUrl: string,
    private readonly tokenProvider: TokenProvider,
    private readonly workspaceIdProvider: () => string,
  ) {}

  private workspacePath(path: string): string {
    return `/workspaces/${this.workspaceIdProvider()}${path}`;
  }

  private async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const token = await this.tokenProvider();
    const url = new URL(path, this.baseUrl);

    for (const [key, value] of Object.entries(options.query ?? {})) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }

    const response = await fetch(url, {
      method: options.method ?? 'GET',
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      },
      body: options.body ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
    });

    const text = await response.text();
    const data = text ? JSON.parse(text) : undefined;

    if (!response.ok) {
      throw new ApiError(
        response.status,
        data?.error_type ?? 'http_error',
        data?.message_for_user ?? data?.message ?? response.statusText,
        data,
      );
    }

    return data as T;
  }

  bootstrap(): Promise<BootstrapResult> {
    return this.request('/bootstrap');
  }

  listThreads(params: ListThreadsParams): Promise<ThreadSummary[]> {
    return this.request(this.workspacePath('/threads'), { query: params });
  }

  getThread(threadId: string): Promise<ThreadDetail> {
    return this.request(this.workspacePath(`/threads/${threadId}`));
  }

  listMessages(threadId: string, params: MessagePageParams): Promise<MessagePage> {
    return this.request(this.workspacePath(`/threads/${threadId}/messages`), { query: params });
  }

  createThread(input: CreateThreadInput): Promise<ThreadDetail> {
    return this.request(this.workspacePath('/threads'), { method: 'POST', body: input });
  }

  createRun(threadId: string, input: CreateRunInput): Promise<RunDetail> {
    return this.request(this.workspacePath(`/threads/${threadId}/runs`), { method: 'POST', body: input });
  }

  cancelRun(runId: string): Promise<void> {
    return this.request(this.workspacePath(`/runs/${runId}/cancel`), { method: 'POST' });
  }

  listJobs(params: JobListParams): Promise<JobPage> {
    return this.request(this.workspacePath('/jobs'), { query: params });
  }

  getJob(jobId: string): Promise<JobDetail> {
    return this.request(this.workspacePath(`/jobs/${jobId}`));
  }

  createJob(input: CreateJobInput): Promise<JobDetail> {
    return this.request(this.workspacePath('/jobs'), { method: 'POST', body: input });
  }

  cancelJob(jobId: string): Promise<void> {
    return this.request(this.workspacePath(`/jobs/${jobId}/cancel`), { method: 'POST' });
  }

  retryJob(jobId: string): Promise<JobDetail> {
    return this.request(this.workspacePath(`/jobs/${jobId}/retry`), { method: 'POST' });
  }

  approve(approvalId: string, input: ApprovalInput): Promise<ApprovalResult> {
    return this.request(this.workspacePath(`/approvals/${approvalId}/approve`), { method: 'POST', body: input });
  }

  reject(approvalId: string, input: ApprovalRejectInput): Promise<ApprovalResult> {
    return this.request(this.workspacePath(`/approvals/${approvalId}/reject`), { method: 'POST', body: input });
  }

  listMcpServers(): Promise<McpServerSummary[]> {
    return this.request(this.workspacePath('/mcp/servers'));
  }

  setMcpToolEnabled(input: McpToolPolicyInput): Promise<McpToolPolicy> {
    return this.request(this.workspacePath('/mcp/tools/policy'), { method: 'POST', body: input });
  }

  getDatabaseHealth(): Promise<DatabaseHealthSummary> {
    return this.request(this.workspacePath('/database/health'));
  }

  listSecrets(params?: SecretListParams): Promise<SecretSummary[]> {
    return this.request(this.workspacePath('/secrets'), { query: params });
  }

  createSecret(input: CreateSecretInput): Promise<SecretCreated> {
    return this.request(this.workspacePath('/secrets'), { method: 'POST', body: input });
  }

  getSecretReferences(secretId: string): Promise<SecretReference[]> {
    return this.request(this.workspacePath(`/secrets/${secretId}/references`));
  }

  getSkillRun(skillRunId: string): Promise<SkillRunDetail> {
    return this.request(this.workspacePath(`/skill-runs/${skillRunId}`));
  }
}
```

客户端单例：

```ts
export const agentApi = new AgentApiClient(
  process.env.NEXT_PUBLIC_AGENT_API_BASE_URL!,
  async () => localStorage.getItem('access_token') ?? undefined,
  () => useIdentityStore.getState().workspaceId,
);
```

### 3. SSE Client

文件：

```text
frontend/src/api/sseParser.ts
frontend/src/api/runEventStreamClient.ts
```

伪代码：

```ts
export type RunEventStreamOptions = {
  workspaceId: string;
  runId: string;
  afterEventId?: string;
  signal: AbortSignal;
  onEvent: (event: RunEvent) => void;
  onError: (error: StreamError) => void;
  onClosed: () => void;
};

function parseSseFrames(buffer: string): { frames: string[]; rest: string } {
  const parts = buffer.split('\n\n');
  return { frames: parts.slice(0, -1), rest: parts.at(-1) ?? '' };
}

function parseRunEvent(frame: string): RunEvent | undefined {
  const lines = frame.split('\n');
  const dataLines = lines
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice('data:'.length).trimStart());

  if (dataLines.length === 0) return undefined;
  return JSON.parse(dataLines.join('\n')) as RunEvent;
}

export async function connectRunEventStream(options: RunEventStreamOptions): Promise<void> {
  const url = new URL(
    `/workspaces/${options.workspaceId}/runs/${options.runId}/events/stream`,
    process.env.NEXT_PUBLIC_AGENT_API_BASE_URL,
  );
  if (options.afterEventId) url.searchParams.set('after_event_id', options.afterEventId);

  const response = await fetch(url, {
    headers: {
      Accept: 'text/event-stream',
      Authorization: `Bearer ${localStorage.getItem('access_token')}`,
    },
    signal: options.signal,
  });

  if (!response.ok || !response.body) {
    options.onError({ type: 'stream_open_failed', status: response.status });
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSseFrames(buffer);
      buffer = parsed.rest;

      for (const frame of parsed.frames) {
        const event = parseRunEvent(frame);
        if (event) options.onEvent(event);
      }
    }
  } catch (error) {
    if (!options.signal.aborted) {
      options.onError({ type: 'stream_interrupted', cause: error });
    }
  } finally {
    options.onClosed();
  }
}
```

文件：

```text
frontend/src/api/jobEventStreamClient.ts
```

Job SSE 与 Run SSE 分开封装，避免任务中心和主对话互相影响。P0 不使用 WebSocket，前端通过 `after_event_id` 和 `Last-Event-ID` 做断线补偿。

```ts
export type JobEventStreamOptions = {
  workspaceId: string;
  jobId: string;
  afterEventId?: string;
  signal: AbortSignal;
  onEvent: (event: JobEvent) => void;
  onError: (error: StreamError) => void;
  onClosed: () => void;
};

function parseJobEvent(frame: string): JobEvent | undefined {
  const lines = frame.split('\n');
  const dataLines = lines
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice('data:'.length).trimStart());

  if (dataLines.length === 0) return undefined;
  return JSON.parse(dataLines.join('\n')) as JobEvent;
}

export async function connectJobEventStream(options: JobEventStreamOptions): Promise<void> {
  const url = new URL(
    `/workspaces/${options.workspaceId}/jobs/${options.jobId}/events/stream`,
    process.env.NEXT_PUBLIC_AGENT_API_BASE_URL,
  );
  if (options.afterEventId) url.searchParams.set('after_event_id', options.afterEventId);

  const response = await fetch(url, {
    headers: {
      Accept: 'text/event-stream',
      ...(options.afterEventId ? { 'Last-Event-ID': options.afterEventId } : {}),
      Authorization: `Bearer ${localStorage.getItem('access_token')}`,
    },
    signal: options.signal,
  });

  if (!response.ok || !response.body) {
    options.onError({ type: 'stream_open_failed', status: response.status });
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSseFrames(buffer);
      buffer = parsed.rest;

      for (const frame of parsed.frames) {
        const event = parseJobEvent(frame);
        if (event) options.onEvent(event);
      }
    }
  } catch (error) {
    if (!options.signal.aborted) {
      options.onError({ type: 'stream_interrupted', cause: error });
    }
  } finally {
    options.onClosed();
  }
}
```

### 4. Zustand Store

文件：

```text
frontend/src/stores/useRunStore.ts
frontend/src/stores/useMessageStore.ts
frontend/src/stores/useToolStore.ts
frontend/src/stores/useSkillStore.ts
frontend/src/stores/useJobStore.ts
```

`useRunStore`：

```ts
import { create } from 'zustand';

type RunState = {
  activeRunId?: string;
  streamStatus: 'idle' | 'connecting' | 'streaming' | 'closed' | 'error';
  lastEventIdByRun: Record<string, string>;
  pendingApprovals: Record<string, ApprovalRequest>;
  setActiveRun: (runId: string) => void;
  applyRunEvent: (event: RunEvent) => void;
};

export const useRunStore = create<RunState>((set, get) => ({
  streamStatus: 'idle',
  lastEventIdByRun: {},
  pendingApprovals: {},

  setActiveRun: (runId) => set({ activeRunId: runId }),

  applyRunEvent: (event) => {
    set((state) => ({
      lastEventIdByRun: {
        ...state.lastEventIdByRun,
        [event.run_id]: event.event_id,
      },
    }));

    if (event.type === 'approval_requested') {
      set((state) => ({
        pendingApprovals: {
          ...state.pendingApprovals,
          [event.payload.approval_id]: event.payload,
        },
      }));
    }
  },
}));
```

`useMessageStore`：

```ts
type MessageState = {
  messagesByThread: Record<string, AgentMessage[]>;
  streamingAssistantByRun: Record<string, AgentMessage>;
  appendUserMessage: (threadId: string, message: AgentMessage) => void;
  applyRunEvent: (event: RunEvent) => void;
};

export const useMessageStore = create<MessageState>((set) => ({
  messagesByThread: {},
  streamingAssistantByRun: {},

  appendUserMessage: (threadId, message) =>
    set((state) => ({
      messagesByThread: {
        ...state.messagesByThread,
        [threadId]: [...(state.messagesByThread[threadId] ?? []), message],
      },
    })),

  applyRunEvent: (event) => {
    if (event.type === 'assistant_delta') {
      set((state) => {
        const current = state.streamingAssistantByRun[event.run_id] ?? createEmptyAssistantMessage(event);
        return {
          streamingAssistantByRun: {
            ...state.streamingAssistantByRun,
            [event.run_id]: {
              ...current,
              content_text: current.content_text + event.payload.text,
            },
          },
        };
      });
    }

    if (event.type === 'assistant_message') {
      set((state) => {
        const threadId = event.thread_id;
        return {
          streamingAssistantByRun: omitKey(state.streamingAssistantByRun, event.run_id),
          messagesByThread: {
            ...state.messagesByThread,
            [threadId]: [...(state.messagesByThread[threadId] ?? []), event.payload.message],
          },
        };
      });
    }
  },
}));
```

`useToolStore`：

```ts
type ToolState = {
  toolCallsByRun: Record<string, Record<string, ToolCallViewModel>>;
  applyRunEvent: (event: RunEvent) => void;
};

export const useToolStore = create<ToolState>((set) => ({
  toolCallsByRun: {},

  applyRunEvent: (event) => {
    if (!event.type.startsWith('tool_call_')) return;

    set((state) => {
      const runTools = state.toolCallsByRun[event.run_id] ?? {};
      const current = runTools[event.payload.tool_call_id] ?? {};

      return {
        toolCallsByRun: {
          ...state.toolCallsByRun,
          [event.run_id]: {
            ...runTools,
            [event.payload.tool_call_id]: {
              ...current,
              ...event.payload,
              status: mapToolEventToStatus(event.type),
            },
          },
        },
      };
    });
  },
}));
```

`useJobStore`：

```ts
type JobStatus =
  | 'created'
  | 'queued'
  | 'running'
  | 'waiting_retry'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'unknown_outcome'
  | 'recovering';

type JobSummary = {
  job_id: string;
  job_type: string;
  status: JobStatus;
  title: string;
  progress_percent: number;
  current_stage?: string;
  target_scope: Record<string, string>;
  related_run_id?: string;
  trace_id?: string;
  updated_at: string;
  cancel_requested?: boolean;
};

type JobState = {
  jobsById: Record<string, JobSummary>;
  activeJobId?: string;
  eventsByJob: Record<string, JobEvent[]>;
  lastEventIdByJob: Record<string, string>;
  streamStatusByJob: Record<string, 'idle' | 'connecting' | 'streaming' | 'closed' | 'error'>;
  listJobs: (params: JobListParams) => Promise<void>;
  openJob: (jobId: string, signal: AbortSignal) => Promise<void>;
  cancelJob: (jobId: string) => Promise<void>;
  retryJob: (jobId: string) => Promise<void>;
  applyJobEvent: (event: JobEvent) => void;
};

export const useJobStore = create<JobState>((set, get) => ({
  jobsById: {},
  eventsByJob: {},
  lastEventIdByJob: {},
  streamStatusByJob: {},

  listJobs: async (params) => {
    const page = await agentApi.listJobs(params);
    set((state) => ({
      jobsById: {
        ...state.jobsById,
        ...Object.fromEntries(page.items.map((job) => [job.job_id, job])),
      },
    }));
  },

  openJob: async (jobId, signal) => {
    set((state) => ({
      activeJobId: jobId,
      streamStatusByJob: { ...state.streamStatusByJob, [jobId]: 'connecting' },
    }));

    await connectJobEventStream({
      workspaceId: useIdentityStore.getState().workspaceId,
      jobId,
      afterEventId: get().lastEventIdByJob[jobId],
      signal,
      onEvent: get().applyJobEvent,
      onError: () => set((state) => ({
        streamStatusByJob: { ...state.streamStatusByJob, [jobId]: 'error' },
      })),
      onClosed: () => set((state) => ({
        streamStatusByJob: { ...state.streamStatusByJob, [jobId]: 'closed' },
      })),
    });
  },

  cancelJob: async (jobId) => {
    await agentApi.cancelJob(jobId);
    set((state) => ({
      jobsById: {
        ...state.jobsById,
        [jobId]: { ...state.jobsById[jobId], cancel_requested: true },
      },
    }));
  },

  retryJob: async (jobId) => {
    const retryJob = await agentApi.retryJob(jobId);
    set((state) => ({
      jobsById: { ...state.jobsById, [retryJob.job_id]: retryJob },
      activeJobId: retryJob.job_id,
    }));
  },

  applyJobEvent: (event) => {
    set((state) => ({
      lastEventIdByJob: {
        ...state.lastEventIdByJob,
        [event.job_id]: event.event_id,
      },
      eventsByJob: {
        ...state.eventsByJob,
        [event.job_id]: dedupeByEventId([...(state.eventsByJob[event.job_id] ?? []), event]),
      },
      jobsById: {
        ...state.jobsById,
        [event.job_id]: reduceJobSummary(state.jobsById[event.job_id], event),
      },
      streamStatusByJob: {
        ...state.streamStatusByJob,
        [event.job_id]: isTerminalJobEvent(event) ? 'closed' : 'streaming',
      },
    }));
  },
}));
```

统一派发事件：

```ts
export function dispatchRunEvent(event: RunEvent) {
  useRunStore.getState().applyRunEvent(event);
  useMessageStore.getState().applyRunEvent(event);
  useToolStore.getState().applyRunEvent(event);
  useSkillStore.getState().applyRunEvent(event);
  useSubAgentStore.getState().applyRunEvent(event);
}

export function dispatchJobEvent(event: JobEvent) {
  useJobStore.getState().applyJobEvent(event);
}
```

### 5. Lobe UI ChatList 适配

文件：

```text
frontend/src/components/chat/AgentChatView.tsx
```

伪代码：

```tsx
'use client';

import { ChatInputArea, ChatList } from '@lobehub/ui/chat';
import { Flexbox } from '@lobehub/ui';
import { useMemo, useRef, useState } from 'react';
import { agentApi } from '@/api/agentApiClient';
import { connectRunEventStream } from '@/api/runEventStreamClient';
import { useIdentityStore } from '@/stores/useIdentityStore';
import { useMessageStore } from '@/stores/useMessageStore';
import { useRunStore } from '@/stores/useRunStore';
import { dispatchRunEvent } from '@/stores/dispatchRunEvent';
import { MessageExtraRenderer } from './MessageExtraRenderer';

export function AgentChatView({ threadId }: { threadId: string }) {
  const [input, setInput] = useState('');
  const abortRef = useRef<AbortController | null>(null);
  const messages = useMessageStore((state) => state.messagesByThread[threadId] ?? []);
  const streaming = useMessageStore((state) => Object.values(state.streamingAssistantByRun));
  const lastEventIdByRun = useRunStore((state) => state.lastEventIdByRun);

  const data = useMemo(
    () => [...messages, ...streaming].map(toLobeChatMessage),
    [messages, streaming],
  );

  async function sendMessage() {
    const text = input.trim();
    if (!text) return;

    setInput('');

    const run = await agentApi.createRun(threadId, {
      user_message: text,
      stream: true,
    });

    useRunStore.getState().setActiveRun(run.run_id);

    abortRef.current?.abort();
    abortRef.current = new AbortController();

    await connectRunEventStream({
      workspaceId: useIdentityStore.getState().workspaceId,
      runId: run.run_id,
      afterEventId: lastEventIdByRun[run.run_id],
      signal: abortRef.current.signal,
      onEvent: dispatchRunEvent,
      onError: (error) => useRunStore.setState({ streamStatus: 'error' }),
      onClosed: () => useRunStore.setState({ streamStatus: 'closed' }),
    });
  }

  async function stopRun() {
    const runId = useRunStore.getState().activeRunId;
    if (!runId) return;
    abortRef.current?.abort();
    await agentApi.cancelRun(runId);
  }

  return (
    <Flexbox height="100%" width="100%">
      <ChatList
        data={data}
        renderMessagesExtra={{
          assistant: (message) => <MessageExtraRenderer message={message} />,
          tool: (message) => <MessageExtraRenderer message={message} />,
        }}
        showTitle
      />

      <ChatInputArea
        loading={useRunStore.getState().streamStatus === 'streaming'}
        onInput={setInput}
        onSend={sendMessage}
        onStop={stopRun}
        placeholder="输入消息"
        value={input}
      />
    </Flexbox>
  );
}
```

后端消息到 Lobe UI 消息：

```ts
import type { ChatMessage } from '@lobehub/ui/chat';

export function toLobeChatMessage(message: AgentMessage): ChatMessage {
  return {
    id: message.message_id,
    role: message.role === 'user' ? 'user' : 'assistant',
    content: message.content_text ?? '',
    createAt: new Date(message.created_at).getTime(),
    updateAt: new Date(message.updated_at ?? message.created_at).getTime(),
    extra: {
      threadId: message.thread_id,
      runId: message.run_id,
      citations: message.citations,
      toolCallIds: message.tool_call_ids,
      approvalIds: message.approval_ids,
      graphEvidenceIds: message.graph_evidence_ids,
    },
  };
}
```

### 6. MessageExtraRenderer

`ChatList` 负责聊天外观，业务卡片由 `message.extra` 关联 store 后渲染。

文件：

```text
frontend/src/components/chat/MessageExtraRenderer.tsx
```

伪代码：

```tsx
import { Flexbox } from '@lobehub/ui';
import type { ChatMessage } from '@lobehub/ui/chat';
import { ApprovalCard } from '@/components/approval/ApprovalCard';
import { GraphRAGPathList } from '@/components/graphrag/GraphRAGPathList';
import { SkillRunPanel } from '@/components/skill/SkillRunPanel';
import { ToolCallCard } from '@/components/tool/ToolCallCard';
import { useRunStore } from '@/stores/useRunStore';
import { useSkillStore } from '@/stores/useSkillStore';
import { useToolStore } from '@/stores/useToolStore';

export function MessageExtraRenderer({ message }: { message: ChatMessage }) {
  const runId = message.extra?.runId;
  const toolCallIds = message.extra?.toolCallIds ?? [];
  const approvalIds = message.extra?.approvalIds ?? [];
  const skillRunIds = message.extra?.skillRunIds ?? [];

  const tools = useToolStore((state) => runId ? state.toolCallsByRun[runId] ?? {} : {});
  const approvals = useRunStore((state) => state.pendingApprovals);
  const skillRuns = useSkillStore((state) => state.skillRunsById);

  return (
    <Flexbox gap={8} style={{ marginTop: 8 }}>
      {toolCallIds.map((id) => tools[id] && <ToolCallCard key={id} toolCall={tools[id]} />)}
      {approvalIds.map((id) => approvals[id] && <ApprovalCard key={id} approval={approvals[id]} />)}
      {skillRunIds.map((id) => skillRuns[id] && <SkillRunPanel key={id} skillRun={skillRuns[id]} />)}
      {message.extra?.graphEvidence && <GraphRAGPathList evidence={message.extra.graphEvidence} />}
    </Flexbox>
  );
}
```

### 7. 自研 ToolCallCard 使用 Lobe UI 砖块

文件：

```text
frontend/src/components/tool/ToolCallCard.tsx
```

伪代码：

```tsx
import { Alert, Collapse, Highlighter, Tag } from '@lobehub/ui';

export function ToolCallCard({ toolCall }: { toolCall: ToolCallViewModel }) {
  const statusColor = {
    running: 'blue',
    succeeded: 'green',
    failed: 'red',
    waiting_approval: 'orange',
  }[toolCall.status];

  return (
    <Collapse
      items={[
        {
          key: toolCall.tool_call_id,
          label: (
            <span>
              <Tag color={statusColor}>{toolCall.status}</Tag>
              {toolCall.tool_name}
            </span>
          ),
          children: (
            <>
              <Highlighter language="json" variant="filled">
                {JSON.stringify(toolCall.args_preview ?? {}, null, 2)}
              </Highlighter>

              {toolCall.result && (
                <ToolResultCard result={toolCall.result} />
              )}
            </>
          ),
        },
      ]}
    />
  );
}

function ToolResultCard({ result }: { result: ToolResult }) {
  if (!result.ok) {
    return (
      <Alert
        message={result.message_for_user ?? result.error_type}
        type={result.retryable ? 'warning' : 'error'}
      />
    );
  }

  return (
    <Highlighter language="json" variant="borderless">
      {JSON.stringify(result.data ?? {}, null, 2)}
    </Highlighter>
  );
}
```

### 8. 自研 ApprovalCard 使用 Lobe UI 砖块

文件：

```text
frontend/src/components/approval/ApprovalCard.tsx
```

伪代码：

```tsx
import { Alert, Button, Flexbox, PatchDiff, Tag, toast } from '@lobehub/ui';
import { agentApi } from '@/api/agentApiClient';

export function ApprovalCard({ approval }: { approval: ApprovalRequest }) {
  async function approve() {
    await agentApi.approve(approval.approval_id, {
      args_hash: approval.args_hash,
      decision: 'approved',
    });
    toast.success('已批准');
  }

  async function reject() {
    await agentApi.reject(approval.approval_id, {
      decision: 'rejected',
      reason: 'user_rejected',
    });
    toast.info('已拒绝');
  }

  return (
    <Alert
      type="warning"
      message={
        <Flexbox gap={8}>
          <Flexbox horizontal gap={8}>
            <Tag color="orange">{approval.risk_level}</Tag>
            <span>{approval.tool_name}</span>
          </Flexbox>

          {approval.diff_patch && (
            <PatchDiff
              patch={approval.diff_patch}
              viewMode="unified"
              variant="outlined"
            />
          )}

          <Flexbox horizontal gap={8}>
            <Button onClick={approve} type="primary">批准</Button>
            <Button onClick={reject}>拒绝</Button>
          </Flexbox>
        </Flexbox>
      }
    />
  );
}
```

### 9. 自研 SkillRunPanel 使用 Lobe UI 砖块

文件：

```text
frontend/src/components/skill/SkillRunPanel.tsx
```

伪代码：

```tsx
import { Collapse, Highlighter, PatchDiff, Tabs, Tag } from '@lobehub/ui';

export function SkillRunPanel({ skillRun }: { skillRun: SkillRunDetail }) {
  return (
    <Collapse
      items={[
        {
          key: skillRun.skill_run_id,
          label: (
            <span>
              <Tag color={skillRun.status === 'failed' ? 'red' : 'blue'}>{skillRun.status}</Tag>
              {skillRun.skill_id} / {skillRun.entrypoint}
            </span>
          ),
          children: (
            <Tabs
              items={[
                {
                  key: 'stdout',
                  label: 'stdout',
                  children: <Highlighter language="log">{skillRun.stdout_preview ?? ''}</Highlighter>,
                },
                {
                  key: 'stderr',
                  label: 'stderr',
                  children: <Highlighter language="log">{skillRun.stderr_preview ?? ''}</Highlighter>,
                },
                {
                  key: 'diff',
                  label: 'diff',
                  children: skillRun.diff_patch ? <PatchDiff patch={skillRun.diff_patch} /> : null,
                },
                {
                  key: 'manifest',
                  label: 'manifest',
                  children: (
                    <Highlighter language="json">
                      {JSON.stringify(skillRun.manifest, null, 2)}
                    </Highlighter>
                  ),
                },
              ]}
            />
          ),
        },
      ]}
    />
  );
}
```

### 10. 自研 GraphRAGPathList 使用 Lobe UI 砖块

文件：

```text
frontend/src/components/graphrag/GraphRAGPathList.tsx
```

伪代码：

```tsx
import { Collapse, Flexbox, Markdown, Tag } from '@lobehub/ui';

export function GraphRAGPathList({ evidence }: { evidence: GraphRAGEvidence }) {
  return (
    <Collapse
      items={evidence.paths.map((path) => ({
        key: path.path_id,
        label: (
          <Flexbox horizontal gap={8}>
            <Tag>{path.relation_strength}</Tag>
            <span>{path.source_entity.name}</span>
            <span>{path.direction}</span>
            <span>{path.target_entity.name}</span>
          </Flexbox>
        ),
        children: (
          <Flexbox gap={8}>
            {path.evidence_cards.map((card) => (
              <Markdown key={card.evidence_id} variant="chat">
                {`**${card.document_title}** p.${card.page ?? '-'} chunk:${card.chunk_id}\n\n${card.quote_summary}`}
              </Markdown>
            ))}
          </Flexbox>
        ),
      }))}
    />
  );
}
```

### 11. 配置页直接使用 Lobe UI Form

文件：

```text
frontend/app/(workspace)/settings/api/page.tsx
```

伪代码：

```tsx
'use client';

import { Form, FormSubmitFooter, Input, InputPassword, Select, SliderWithInput, Tabs } from '@lobehub/ui';
import { Button, Space } from 'antd';
import { agentApi } from '@/api/agentApiClient';

export default function ApiSettingsPage() {
  return (
    <Tabs
      items={[
        {
          key: 'chat',
          label: '主对话模型',
          children: (
            <ModelProviderForm configKey="chat_model" />
          ),
        },
        {
          key: 'graphrag',
          label: 'GraphRAG LLM',
          children: (
            <ModelProviderForm configKey="graphrag_llm" />
          ),
        },
        {
          key: 'embedding',
          label: 'Embedding',
          children: (
            <EmbeddingProviderForm />
          ),
        },
        {
          key: 'compression',
          label: '压缩模型',
          children: (
            <ModelProviderForm configKey="compression_model" />
          ),
        },
      ]}
    />
  );
}

function ModelProviderForm({ configKey }: { configKey: string }) {
  return (
    <Form
      items={[
        {
          title: 'Provider',
          children: [
            { name: 'provider', label: '供应商', children: <Select options={providerOptions} /> },
            { name: 'base_url', label: 'Base URL', children: <Input /> },
            { name: 'api_key_ref', label: 'API Key', children: <SecretRefInput type="model_api_key" /> },
            { name: 'model', label: '模型', children: <Input /> },
            { name: 'context_window_tokens', label: '上下文窗口', children: <SliderWithInput /> },
            { name: 'max_output_tokens', label: '最大输出', children: <SliderWithInput /> },
          ],
        },
      ]}
      onFinish={(values) => submitModelConfig(configKey, values)}
    >
      <FormSubmitFooter />
    </Form>
  );
}

function SecretRefInput({ type, value, onChange }: SecretRefInputProps) {
  const { secrets, refresh } = useSecretStore(type);
  const [newPlaintext, setNewPlaintext] = useState('');

  async function createAndSelect() {
    const secret = await agentApi.createSecret({
      type,
      display_name: `${type} secret`,
      plaintext: newPlaintext,
      scope: 'workspace',
    });
    await refresh();
    onChange?.(secret.secret_ref);
    setNewPlaintext('');
  }

  return (
    <Space.Compact>
      <Select
        options={secrets.map((item) => ({
          label: `${item.display_name} (${item.masked})`,
          value: item.secret_id,
        }))}
        value={value}
        onChange={onChange}
      />
      <InputPassword
        value={newPlaintext}
        onChange={(event) => setNewPlaintext(event.target.value)}
      />
      <Button onClick={createAndSelect}>保存</Button>
    </Space.Compact>
  );
}

async function submitModelConfig(configKey: string, values: ModelConfigFormValues) {
  await agentApi.updateModelConfig(configKey, {
    provider: values.provider,
    base_url: values.base_url,
    api_key_ref: values.api_key_ref,
    model: values.model,
    context_window_tokens: values.context_window_tokens,
    max_output_tokens: values.max_output_tokens,
  });
}
```

### 12. 数据库配置页使用 Lobe UI + 自研健康检查

文件：

```text
frontend/app/(workspace)/settings/database/page.tsx
```

伪代码：

```tsx
'use client';

import { Alert, Button, Form, List, Tag } from '@lobehub/ui';
import { useEffect, useState } from 'react';
import { agentApi } from '@/api/agentApiClient';

export default function DatabaseSettingsPage() {
  const [health, setHealth] = useState<DatabaseHealthSummary>();

  async function refreshHealth() {
    setHealth(await agentApi.getDatabaseHealth());
  }

  useEffect(() => {
    refreshHealth();
  }, []);

  return (
    <>
      <List
        items={(health?.items ?? []).map((item) => ({
          key: item.name,
          title: item.name,
          description: item.message,
          addon: <Tag color={item.ok ? 'green' : 'red'}>{item.status}</Tag>,
          actions: <Button onClick={() => agentApi.testDatabaseConnection({ database: item.name })}>测试连接</Button>,
        }))}
      />

      {health?.items.some((item) => !item.ok) && (
        <Alert type="error" message="存在数据库连接异常，请查看诊断结果。" />
      )}

      <Form items={databaseConnectionFormItems} />
    </>
  );
}
```

### 13. MCP 配置页使用 Lobe UI + 自研 Tool Policy

文件：

```text
frontend/app/(workspace)/settings/mcp/page.tsx
```

伪代码：

```tsx
'use client';

import { Drawer, Highlighter, List, Tag } from '@lobehub/ui';
import { Switch } from 'antd';
import { useEffect, useState } from 'react';
import { agentApi } from '@/api/agentApiClient';

export default function McpSettingsPage() {
  const [servers, setServers] = useState<McpServerSummary[]>([]);
  const [selectedTool, setSelectedTool] = useState<McpToolSummary | null>(null);

  useEffect(() => {
    agentApi.listMcpServers().then(setServers);
  }, []);

  return (
    <>
      {servers.map((server) => (
        <List
          key={server.server_name}
          items={server.tools.map((tool) => ({
            key: `${server.server_name}:${tool.tool_name}`,
            title: tool.model_visible_name,
            description: tool.description,
            addon: <Tag>{tool.risk_level}</Tag>,
            actions: (
              <Switch
                checked={tool.enabled}
                onChange={(enabled) =>
                  agentApi.setMcpToolEnabled({
                    server_name: server.server_name,
                    tool_name: tool.tool_name,
                    enabled,
                  })
                }
              />
            ),
            onClick: () => setSelectedTool(tool),
          }))}
        />
      ))}

      <Drawer open={!!selectedTool} onClose={() => setSelectedTool(null)} title={selectedTool?.model_visible_name}>
        <Highlighter language="json">
          {JSON.stringify(selectedTool?.input_schema ?? {}, null, 2)}
        </Highlighter>
      </Drawer>
    </>
  );
}
```

### 14. 任务中心 / Jobs 页面使用 Lobe UI 砖块

文件：

```text
frontend/app/(workspace)/jobs/page.tsx
frontend/src/components/jobs/JobTaskCenter.tsx
frontend/src/components/jobs/JobTimeline.tsx
frontend/src/components/jobs/JobDetailDrawer.tsx
```

页面职责：

```text
Jobs 页面只展示后台 Job。
Run 对话流仍留在 Chat 页面。
Job 列表从 jobs_index.json 对应 API 分页读取。
当前打开的 Job 详情通过 SSE 实时更新。
超过 5 个 active Job 时，列表用轮询摘要刷新，不同时订阅全部 SSE。
```

`jobs/page.tsx`：

```tsx
'use client';

import { AppLayout } from '@/components/workspace/AppLayout';
import { JobTaskCenter } from '@/components/jobs/JobTaskCenter';

export default function JobsPage() {
  return (
    <AppLayout activeKey="jobs">
      <JobTaskCenter />
    </AppLayout>
  );
}
```

`JobTaskCenter`：

```tsx
import { Button, Drawer, Flexbox, Progress, Select, Tag } from '@lobehub/ui';
import { Table } from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useJobStore } from '@/stores/useJobStore';
import { JobDetailDrawer } from './JobDetailDrawer';

export function JobTaskCenter() {
  const [filters, setFilters] = useState<JobListParams>({ status: 'active' });
  const [detailOpen, setDetailOpen] = useState(false);
  const streamAbortRef = useRef<AbortController | null>(null);

  const jobs = useJobStore((state) => Object.values(state.jobsById));
  const activeJobId = useJobStore((state) => state.activeJobId);
  const listJobs = useJobStore((state) => state.listJobs);
  const openJob = useJobStore((state) => state.openJob);
  const cancelJob = useJobStore((state) => state.cancelJob);
  const retryJob = useJobStore((state) => state.retryJob);

  useEffect(() => {
    void listJobs(filters);
  }, [filters, listJobs]);

  const columns = useMemo(() => [
    {
      title: '任务',
      dataIndex: 'title',
      render: (_: string, job: JobSummary) => (
        <Flexbox gap={4}>
          <span>{job.title}</span>
          <span style={{ color: 'var(--lobehub-text-color-secondary)' }}>{job.job_type}</span>
        </Flexbox>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      render: (status: JobStatus) => <Tag color={statusToColor(status)}>{status}</Tag>,
    },
    {
      title: '进度',
      dataIndex: 'progress_percent',
      render: (_: number, job: JobSummary) => (
        <Progress percent={job.progress_percent} size="small" status={progressStatus(job.status)} />
      ),
    },
    {
      title: '阶段',
      dataIndex: 'current_stage',
    },
    {
      title: '操作',
      render: (_: unknown, job: JobSummary) => (
        <Flexbox horizontal gap={8}>
          <Button onClick={() => showJob(job.job_id)} size="small">详情</Button>
          {canCancelJob(job) && <Button onClick={() => cancelJob(job.job_id)} size="small">取消</Button>}
          {canRetryJob(job) && <Button onClick={() => retryJob(job.job_id)} size="small">重试</Button>}
        </Flexbox>
      ),
    },
  ], [cancelJob, retryJob]);

  async function showJob(jobId: string) {
    setDetailOpen(true);
    streamAbortRef.current?.abort();
    streamAbortRef.current = new AbortController();
    await openJob(jobId, streamAbortRef.current.signal);
  }

  return (
    <Flexbox gap={12} height="100%">
      <Flexbox horizontal gap={8}>
        <Select
          value={filters.status}
          onChange={(status) => setFilters((current) => ({ ...current, status }))}
          options={[
            { label: '活跃', value: 'active' },
            { label: '成功', value: 'succeeded' },
            { label: '失败', value: 'failed' },
            { label: '全部', value: 'all' },
          ]}
        />
        <Select
          value={filters.job_type}
          onChange={(jobType) => setFilters((current) => ({ ...current, job_type: jobType }))}
          options={JOB_TYPE_OPTIONS}
          placeholder="任务类型"
        />
      </Flexbox>

      <Table
        columns={columns}
        dataSource={jobs}
        pagination={{ pageSize: 20 }}
        rowKey="job_id"
        size="middle"
      />

      <Drawer open={detailOpen} onClose={() => setDetailOpen(false)} width={640}>
        {activeJobId && <JobDetailDrawer jobId={activeJobId} />}
      </Drawer>
    </Flexbox>
  );
}
```

`JobDetailDrawer`：

```tsx
import { Alert, Collapse, Highlighter, Tabs, Timeline } from '@lobehub/ui';
import { useJobStore } from '@/stores/useJobStore';

export function JobDetailDrawer({ jobId }: { jobId: string }) {
  const job = useJobStore((state) => state.jobsById[jobId]);
  const events = useJobStore((state) => state.eventsByJob[jobId] ?? []);

  if (!job) return null;

  return (
    <Tabs
      items={[
        {
          key: 'overview',
          label: '概览',
          children: (
            <Collapse
              items={[
                {
                  key: 'manifest',
                  label: 'manifest 摘要',
                  children: (
                    <Highlighter language="json">
                      {JSON.stringify({
                        job_id: job.job_id,
                        job_type: job.job_type,
                        status: job.status,
                        target_scope: job.target_scope,
                        trace_id: job.trace_id,
                        related_run_id: job.related_run_id,
                      }, null, 2)}
                    </Highlighter>
                  ),
                },
              ]}
            />
          ),
        },
        {
          key: 'timeline',
          label: '事件',
          children: <JobTimeline events={events} />,
        },
        {
          key: 'error',
          label: '错误',
          children: job.status === 'failed' || job.status === 'unknown_outcome'
            ? <Alert type="warning" message={buildJobErrorMessage(job, events)} />
            : null,
        },
      ]}
    />
  );
}

function JobTimeline({ events }: { events: JobEvent[] }) {
  return (
    <Timeline
      items={events.map((event) => ({
        key: event.event_id,
        label: event.created_at,
        children: (
          <Highlighter language="json">
            {JSON.stringify({ type: event.type, payload: event.payload }, null, 2)}
          </Highlighter>
        ),
      }))}
    />
  );
}
```

### 15. 用户发送消息的完整流程

```text
用户在 ChatInputArea 输入
  -> AgentChatView.sendMessage()
  -> AgentApiClient.createRun(thread_id, user_message)
  -> useRunStore.setActiveRun(run_id)
  -> RunEventStreamClient.connect(run_id, after_event_id)
  -> 后端 SSE 推 assistant_delta / tool_call / approval / subagent / skill_run
  -> dispatchRunEvent(event)
  -> useMessageStore / useToolStore / useRunStore / useSkillStore 分别更新状态
  -> AgentChatView 重新渲染 ChatList
  -> MessageExtraRenderer 渲染 ToolCallCard / ApprovalCard / SkillRunPanel / GraphRAGPathList
```

### 16. 自研组件和 Lobe UI 的边界

```text
Lobe UI 组件只负责：
  视觉、布局、输入、列表、抽屉、Modal、Markdown、代码、Diff、状态。

本项目自研组件负责：
  读取 store。
  调用 AgentApiClient。
  解释 RunEvent / ToolResult / Approval / SkillRun / GraphRAG evidence。
  把业务对象转成 Lobe UI props。
```

组件边界示例：

```tsx
// 自研业务组件
export function ToolCallCard({ toolCall }: { toolCall: ToolCallViewModel }) {
  // 业务判断由自己做
  const risk = getToolRisk(toolCall);
  const result = normalizeToolResult(toolCall.result);

  // 视觉展示交给 Lobe UI
  return (
    <Collapse
      items={[
        {
          key: toolCall.tool_call_id,
          label: <ToolCallTitle risk={risk} name={toolCall.tool_name} status={toolCall.status} />,
          children: <ToolResultCard result={result} />,
        },
      ]}
    />
  );
}
```

## 缺口结论

`@lobehub/ui` 可以覆盖本项目 70% 到 80% 的视觉和基础交互需求，包括聊天、Markdown、代码、diff、表单、侧栏、抽屉、列表、状态和加载。

必须自研的是 Agent Runtime 专用业务层：

- SSE event 到 UI 状态的合并。
- ToolResult 展示。
- Approval 审批交互。
- SubAgent 审核。
- Skill staged patch。
- MCP capability snapshot 和 tool policy。
- MinIO / Milvus / Neo4j 健康诊断。
- Job 任务中心、Job SSE、取消、重试和 unknown_outcome 展示。
- GraphRAG 路径证据。
- Memory snapshot 和 user_profile / user_preference 管理。
- run timeline / event replay / rollback。

因此 P0 前端开发策略是：

```text
使用 Lobe UI 做设计系统。
使用 Antd 做复杂表单和表格补充。
使用 antd-style 做样式和主题。
使用本项目自研业务组件承接 Agent Runtime。
```
