export type SectionKey =
  | 'chat'
  | 'jobs'
  | 'knowledge'
  | 'memory'
  | 'skills'
  | 'subagents'
  | 'mcp'
  | 'logs'
  | 'readiness'
  | 'settings'

export interface PanelSpec {
  title: string
  description: string
  endpoints: string[]
  checks: string[]
}

export const workspaceSections: SectionKey[] = [
  'chat',
  'jobs',
  'knowledge',
  'memory',
  'skills',
  'subagents',
  'mcp',
  'logs',
  'readiness',
  'settings',
]

export const workspaceMenuItems = [
  { key: 'chat', label: 'Chat' },
  { key: 'jobs', label: 'Jobs' },
  { key: 'knowledge', label: 'Knowledge' },
  { key: 'memory', label: 'Memory' },
  { key: 'skills', label: 'Skills' },
  { key: 'subagents', label: 'SubAgents' },
  { key: 'mcp', label: 'MCP Tools' },
  { key: 'logs', label: 'Logs' },
  { key: 'readiness', label: 'P0 Readiness' },
  { key: 'settings', label: 'Settings' },
]

export const panelSpecs: Record<SectionKey, PanelSpec> = {
  chat: {
    title: 'Chat runtime',
    description: 'Threads, runs, SSE replay, LangGraph runtime, tools, Skill and SubAgent calls.',
    endpoints: [
      '/workspaces/{workspace_id}/threads',
      '/workspaces/{workspace_id}/runs/{run_id}/events/stream',
    ],
    checks: ['Thread history is persistent', 'REST + SSE only', 'Tool results remain reviewed'],
  },
  jobs: {
    title: 'Jobs',
    description: 'Background task center for ingestion, rebuild, diagnostics and MCP refresh.',
    endpoints: [
      '/workspaces/{workspace_id}/jobs',
      '/workspaces/{workspace_id}/jobs/{job_id}/events/stream',
    ],
    checks: ['ObjectStore is source of truth', 'unknown_outcome is visible', 'No Redis queue'],
  },
  knowledge: {
    title: 'Knowledge',
    description: 'Document upload, chunk inspection, Milvus retrieval and GraphRAG evidence.',
    endpoints: [
      '/workspaces/{workspace_id}/knowledge-bases',
      '/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents',
      '/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/active-embedding',
      '/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/embedding/reindex',
      '/workspaces/{workspace_id}/chunks/{chunk_id}',
    ],
    checks: ['One active embedding version', 'Chunks point back to MinIO', 'Neo4j is read-only to model'],
  },
  memory: {
    title: 'Memory',
    description: 'User profile, preference, project facts and model-visible memory snapshots.',
    endpoints: [
      '/workspaces/{workspace_id}/memories',
      '/workspaces/{workspace_id}/memory-snapshots',
    ],
    checks: ['User-visible profile memory', 'Delete disables injection', 'Hermes compaction compatible'],
  },
  skills: {
    title: 'Skills',
    description: 'User-approved prompt, workflow, knowledge and script packages.',
    endpoints: [
      '/workspaces/{workspace_id}/skills',
      '/workspaces/{workspace_id}/skill-proposals',
      '/workspaces/{workspace_id}/skills/{skill_id}/activate',
    ],
    checks: ['Initial state has no business Skill', 'Scripts default disabled', 'Creation requires approval'],
  },
  subagents: {
    title: 'SubAgents',
    description: 'Role-scoped execution units with explicit read/write scope and main-agent review.',
    endpoints: [
      '/workspaces/{workspace_id}/subagents/tasks',
      '/workspaces/{workspace_id}/subagents/tasks/{task_id}',
      '/workspaces/{workspace_id}/subagents/tasks/{task_id}/review',
    ],
    checks: ['Tasks can be queued as Jobs', 'Write scope is explicit', 'Main Agent reviews all results'],
  },
  mcp: {
    title: 'MCP tools',
    description: 'Server capability snapshots, tool list, single-tool enable and disable.',
    endpoints: [
      '/workspaces/{workspace_id}/mcp/servers',
      '/workspaces/{workspace_id}/mcp/servers/{server_name}/tools',
      '/workspaces/{workspace_id}/mcp/servers/{server_name}/refresh',
      '/workspaces/{workspace_id}/mcp/tools/policy',
      '/workspaces/{workspace_id}/tools/inventory',
    ],
    checks: ['Capability refresh is a Job', 'Stale snapshots are kept', 'Conflicting tools are disabled'],
  },
  logs: {
    title: 'Logs',
    description: 'System summary, full JSONL logs, component logs and redacted diagnostic bundles.',
    endpoints: [
      '/workspaces/{workspace_id}/logs/system/full',
      '/workspaces/{workspace_id}/logs/components/{component}',
      '/workspaces/{workspace_id}/logs/diagnostic-bundles',
    ],
    checks: ['Every request has trace_id', 'Diagnostic bundle is redacted', 'No secret-like field names'],
  },
  readiness: {
    title: 'P0 readiness',
    description: 'Aggregated launch checks for identity, storage, models, databases, jobs, runtime registries, logs and external smoke items.',
    endpoints: [
      '/workspaces/{workspace_id}/readiness',
    ],
    checks: ['Required blockers are visible', 'External smoke items are tracked', 'No WebSocket or SQL store dependency'],
  },
  settings: {
    title: 'Settings',
    description: 'Model APIs, database connections, secrets, cache and workspace defaults.',
    endpoints: [
      '/workspaces/{workspace_id}/model-configs',
      '/workspaces/{workspace_id}/model-configs/{config_id}/test',
      '/workspaces/{workspace_id}/database/config',
      '/workspaces/{workspace_id}/database/health',
      '/workspaces/{workspace_id}/secrets',
    ],
    checks: ['OpenAI-compatible and Anthropic', 'MinIO + Milvus + Neo4j', 'Redis is cache only'],
  },
}

export function isWorkspaceSection(value: string): value is SectionKey {
  return workspaceSections.includes(value as SectionKey)
}

export function getWorkspaceSectionPath(section: SectionKey): string {
  return section === 'chat' ? '/' : `/${section}`
}

export function getWorkspaceSectionFromPathname(pathname: string | null): SectionKey | null {
  const firstSegment = pathname?.split('/').filter(Boolean)[0]
  if (!firstSegment) {
    return 'chat'
  }
  return isWorkspaceSection(firstSegment) ? firstSegment : null
}
