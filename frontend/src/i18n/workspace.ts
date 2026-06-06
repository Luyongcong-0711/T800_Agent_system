import type { AppLanguage } from '@/stores/useUiPreferencesStore'

import type { PanelSpec, SectionKey } from '@/components/workspace/routes'

export interface WorkspaceCopy {
  apiSurface: string
  bootstrapFailed: string
  dark: string
  disabled: string
  enabled: string
  featureFlags: string
  languageTooltip: string
  light: string
  loginPage: string
  p0Checks: string
  retry: string
  role: string
  themeTooltip: string
  user: string
  workspace: string
  workspaceConsole: string
  workspaceRole: string
}

const zhPanelSpecs: Record<SectionKey, PanelSpec> = {
  chat: {
    title: '对话 Runtime',
    description: '会话、Run、SSE 回放、LangGraph Runtime、Tool、Skill 和 SubAgent 调用。',
    endpoints: [
      '/workspaces/{workspace_id}/threads',
      '/workspaces/{workspace_id}/runs/{run_id}/events/stream',
    ],
    checks: ['会话历史持久化', '仅使用 REST + SSE', 'ToolResult 保持审核'],
  },
  jobs: {
    title: '后台 Job',
    description: '文档入库、重建、诊断包和 MCP 刷新 Job 中心。',
    endpoints: [
      '/workspaces/{workspace_id}/jobs',
      '/workspaces/{workspace_id}/jobs/{job_id}/events/stream',
    ],
    checks: ['ObjectStore 是权威状态', 'unknown_outcome 可见', '不使用 Redis 队列'],
  },
  knowledge: {
    title: '知识库',
    description: '文档上传、chunk 检查、Milvus 检索和 GraphRAG 证据。',
    endpoints: [
      '/workspaces/{workspace_id}/knowledge-bases',
      '/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents',
      '/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/active-embedding',
      '/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/embedding/reindex',
      '/workspaces/{workspace_id}/chunks/{chunk_id}',
    ],
    checks: ['只允许一个 active embedding 版本', 'Chunk 回源到 MinIO', '模型只能只读 Neo4j'],
  },
  memory: {
    title: '长期记忆',
    description: '用户画像、偏好、项目事实和模型可见记忆快照。',
    endpoints: [
      '/workspaces/{workspace_id}/memories',
      '/workspaces/{workspace_id}/memory-snapshots',
    ],
    checks: ['用户可见画像记忆', '删除后停止注入', '兼容 Hermes 压缩策略'],
  },
  skills: {
    title: 'Skill',
    description: '经用户批准的提示词、流程、知识包和脚本包。',
    endpoints: [
      '/workspaces/{workspace_id}/skills',
      '/workspaces/{workspace_id}/skill-proposals',
      '/workspaces/{workspace_id}/skills/{skill_id}/activate',
    ],
    checks: ['初始无业务 Skill', '脚本默认禁用', '创建需要审批'],
  },
  subagents: {
    title: 'SubAgent',
    description: '带角色、读写范围和主 Agent 审核的执行单元。',
    endpoints: [
      '/workspaces/{workspace_id}/subagents/tasks',
      '/workspaces/{workspace_id}/subagents/tasks/{task_id}',
      '/workspaces/{workspace_id}/subagents/tasks/{task_id}/review',
    ],
    checks: ['任务可进入 Job', '写入范围显式声明', '主 Agent 汇总审核'],
  },
  mcp: {
    title: 'MCP Tool',
    description: 'Server 能力快照、Tool 列表、单个 Tool 启用和禁用。',
    endpoints: [
      '/workspaces/{workspace_id}/mcp/servers',
      '/workspaces/{workspace_id}/mcp/servers/{server_name}/tools',
      '/workspaces/{workspace_id}/mcp/servers/{server_name}/refresh',
      '/workspaces/{workspace_id}/mcp/tools/policy',
      '/workspaces/{workspace_id}/tools/inventory',
    ],
    checks: ['能力刷新走 Job', '保留过期快照', '冲突工具默认禁用'],
  },
  logs: {
    title: '日志',
    description: '系统摘要、完整 JSONL 日志、组件日志和脱敏诊断包。',
    endpoints: [
      '/workspaces/{workspace_id}/logs/system/full',
      '/workspaces/{workspace_id}/logs/components/{component}',
      '/workspaces/{workspace_id}/logs/diagnostic-bundles',
    ],
    checks: ['每个请求都有 trace_id', '诊断包脱敏', '不暴露 secret-like 字段'],
  },
  readiness: {
    title: 'P0 就绪状态',
    description: '聚合身份、存储、模型、数据库、Job、Runtime 注册表、日志和外部 smoke 检查。',
    endpoints: [
      '/workspaces/{workspace_id}/readiness',
    ],
    checks: ['Required blockers 可见', '外部 smoke 项可追踪', '无 WebSocket 或 SQL store 依赖'],
  },
  settings: {
    title: '设置',
    description: '模型 API、数据库连接、密钥、缓存和 workspace 默认配置。',
    endpoints: [
      '/workspaces/{workspace_id}/model-configs',
      '/workspaces/{workspace_id}/model-configs/{config_id}/test',
      '/workspaces/{workspace_id}/database/config',
      '/workspaces/{workspace_id}/database/health',
      '/workspaces/{workspace_id}/secrets',
    ],
    checks: ['OpenAI-compatible 和 Anthropic', 'MinIO + Milvus + Neo4j', 'Redis 只做缓存'],
  },
}

const zhMenuLabels: Record<SectionKey, string> = {
  chat: '对话',
  jobs: 'Job',
  knowledge: '知识库',
  memory: '记忆',
  skills: 'Skill',
  subagents: 'SubAgent',
  mcp: 'MCP Tool',
  logs: '日志',
  readiness: 'P0 就绪',
  settings: '设置',
}

const enCopy: WorkspaceCopy = {
  apiSurface: 'API surface',
  bootstrapFailed: 'Bootstrap failed',
  dark: 'Dark',
  disabled: 'Disabled',
  enabled: 'Enabled',
  featureFlags: 'Feature flags',
  languageTooltip: 'Language',
  light: 'Light',
  loginPage: 'Login page',
  p0Checks: 'P0 checks',
  retry: 'Retry',
  role: 'Role',
  themeTooltip: 'Theme',
  user: 'User',
  workspace: 'Workspace',
  workspaceConsole: 'Workspace console',
  workspaceRole: 'Workspace role',
}

const zhCopy: WorkspaceCopy = {
  apiSurface: 'API 接口',
  bootstrapFailed: '初始化失败',
  dark: '深色',
  disabled: '关闭',
  enabled: '开启',
  featureFlags: '功能开关',
  languageTooltip: '语言',
  light: '浅色',
  loginPage: '登录页',
  p0Checks: 'P0 检查',
  retry: '重试',
  role: '角色',
  themeTooltip: '主题',
  user: '用户',
  workspace: '工作区',
  workspaceConsole: '工作台',
  workspaceRole: '工作区角色',
}

export function getWorkspaceCopy(language: AppLanguage): WorkspaceCopy {
  return language === 'zh' ? zhCopy : enCopy
}

export function getLocalizedPanelSpecs(
  language: AppLanguage,
  englishPanelSpecs: Record<SectionKey, PanelSpec>,
): Record<SectionKey, PanelSpec> {
  return language === 'zh' ? zhPanelSpecs : englishPanelSpecs
}

export function getLocalizedWorkspaceMenuItems(
  language: AppLanguage,
  sectionKeys: SectionKey[],
) {
  if (language === 'en') {
    return sectionKeys.map((key) => ({ key, label: englishMenuLabel(key) }))
  }

  return sectionKeys.map((key) => ({ key, label: zhMenuLabels[key] }))
}

function englishMenuLabel(key: SectionKey): string {
  const labels: Record<SectionKey, string> = {
    chat: 'Chat',
    jobs: 'Jobs',
    knowledge: 'Knowledge',
    memory: 'Memory',
    skills: 'Skills',
    subagents: 'SubAgents',
    mcp: 'MCP Tools',
    logs: 'Logs',
    readiness: 'P0 Readiness',
    settings: 'Settings',
  }
  return labels[key]
}
