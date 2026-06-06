import type { AppLanguage } from '@/stores/useUiPreferencesStore'

const TRANSLATABLE_ATTRIBUTES = [
  'aria-label',
  'placeholder',
  'title',
  'alt',
] as const

const SKIP_TEXT_SELECTOR = [
  'script',
  'style',
  'pre',
  'code',
  'kbd',
  'samp',
  'textarea',
  'input',
  '[data-no-translate]',
  '[data-testid="workspace-route-marker"]',
].join(',')

const SKIP_ATTRIBUTE_SELECTOR = [
  'script',
  'style',
  'pre',
  'code',
  'kbd',
  'samp',
  '[data-no-translate]',
  '[data-testid="workspace-route-marker"]',
].join(',')

const textOriginals = new WeakMap<Text, string>()
const attributeOriginals = new WeakMap<Element, Map<string, string>>()

const zhDictionary: Record<string, string> = {
  // Common controls
  Actions: '操作',
  Activate: '激活',
  Active: '启用中',
  Archive: '归档',
  Cancel: '取消',
  Close: '关闭',
  Create: '创建',
  Delete: '删除',
  Details: '详情',
  Disable: '禁用',
  Download: '下载',
  Enable: '启用',
  Enabled: '已启用',
  Refresh: '刷新',
  Rename: '重命名',
  Reset: '重置',
  Retry: '重试',
  Save: '保存',
  Search: '搜索',
  Select: '选择',
  Status: '状态',
  Test: '测试',
  Type: '类型',
  Updated: '更新时间',
  View: '查看',

  // Workspace and tabs
  Chat: '对话',
  Databases: '数据库',
  Jobs: 'Job',
  Logs: '日志',
  'MCP Tools': 'MCP Tool',
  'Model APIs': '模型 API',
  Secrets: '密钥',
  Settings: '设置',
  Skills: 'Skill',
  SubAgents: 'SubAgent',

  // Chat page
  Threads: '会话',
  New: '新建',
  'New chat': '新对话',
  'No threads': '暂无会话',
  'Thread actions': '会话操作',
  'No messages yet': '暂无消息',
  'Start a conversation': '开始对话',
  'Ask the agent...': '询问 Agent...',
  Send: '发送',
  Running: '运行中',
  'Recover stale': '恢复陈旧运行',
  'Waiting approval': '等待审批',
  'Run events': 'Run 事件',
  'Run event stream': 'Run 事件流',
  'Run events are the SSE debug timeline for one execution. assistant_delta entries are streaming model chunks; the final assistant message is saved into chat history when the Run closes.':
    'Run 事件是一次执行的 SSE 调试时间线。assistant_delta 是模型流式输出片段；Run 关闭后，最终 assistant 消息会写入对话历史。',
  'No run events': '暂无 Run 事件',
  'Rename thread': '重命名会话',
  'Thread title': '会话标题',
  'Delete thread': '删除会话',
  'Archive thread': '归档会话',
  'Delete this thread from the active history?': '从活跃历史中删除这个会话？',
  'Archive this thread from the active history?': '从活跃历史中归档这个会话？',
  Pin: '置顶',
  Unpin: '取消置顶',
  Pinned: '已置顶',
  'Approval required': '需要审批',
  'Skill staged patch approval': 'Skill 暂存补丁审批',
  'Approval approved': '审批已通过',
  'Approval rejected': '审批已拒绝',
  Approve: '通过',
  Reject: '拒绝',
  'Approval accepted': '审批已接受',
  'Post-approval execution started': '审批后执行已开始',
  'Post-approval execution completed': '审批后执行已完成',
  'Post-approval execution failed': '审批后执行失败',
  'Operation rolled back': '操作已回滚',
  'Approval artifact': '审批产物',
  'Operation status': '操作状态',
  'Workspace commit': '工作区提交',
  'Rollback workspace files': '回滚工作区文件',
  'Workspace files rolled back': '工作区文件已回滚',

  // Jobs page
  Job: 'Job',
  Worker: 'Worker',
  Stage: '阶段',
  Target: '目标',
  Events: '事件',
  Artifacts: '产物',
  'Job type': 'Job 类型',
  'Job detail': 'Job 详情',
  'Job ID': 'Job ID',
  'Leaf state': '叶子状态',
  'Start worker': '启动 Worker',
  'Stop worker': '停止 Worker',
  'Stop the Job worker?': '停止 Job Worker？',
  'Process next': '处理下一个',
  'Recover jobs': '恢复 Job',
  'Recover stale running jobs': '恢复陈旧运行 Job',
  'Recover stale running jobs?': '恢复陈旧运行 Job？',
  'Rebuild index': '重建索引',
  'Rebuild jobs index': '重建 Job 索引',
  'Rebuild the jobs index?': '重建 Job 索引？',
  'Worker status': 'Worker 状态',
  'last tick:': '上次轮询：',
  'Cancel job': '取消 Job',
  'Cancel this job?': '取消这个 Job？',
  'Retry failed chunks': '重试失败 chunk',
  Recover: '恢复',
  'Running jobs are not force-killed, but no new job will be claimed after the worker stops.':
    '正在运行的 Job 不会被强制终止，但 Worker 停止后不会再认领新 Job。',
  'Stale running jobs may move to recovering or unknown_outcome after recovery.':
    '陈旧运行 Job 在恢复后可能进入 recovering 或 unknown_outcome 状态。',
  'The jobs index will be rebuilt from job manifests in ObjectStore.':
    'Job 索引会从 ObjectStore 中的 Job manifest 重建。',
  'This queues a follow-up attempt from the persisted job state and may create new job events.':
    '这会基于持久化 Job 状态排队一次后续尝试，并可能产生新的 Job 事件。',
  'The job manifest will be marked cancelled. Already persisted job events remain immutable.':
    'Job manifest 会被标记为已取消，已经持久化的 Job 事件保持不可变。',

  // Knowledge and GraphRAG
  'Knowledge base': '知识库',
  'Active embedding': '当前 Embedding',
  Upload: '上传',
  Documents: '文档',
  Document: '文档',
  Chunks: '分块',
  'Last job': '最近 Job',
  'Open job': '打开 Job',
  'Build graph': '构建图谱',
  'Queued job': '已排队 Job',
  Name: '名称',
  Provider: '供应商',
  Model: '模型',
  Dimension: '维度',
  Collection: 'Collection',
  Config: '配置',
  Reindex: '重新入库',
  File: '文件',
  'File name': '文件名',
  'MIME type': 'MIME 类型',
  Content: '内容',
  'Select file': '选择文件',
  'No knowledge base selected.': '未选择知识库。',
  'No active embedding.': '暂无当前 Embedding。',
  'Milvus vector search pending': 'Milvus 向量检索待完成',
  'No chunks selected.': '未选择分块。',
  'Loading chunks.': '正在加载分块。',
  'Settings default': '设置页默认值',
  'Settings embedding model': '设置页 Embedding 模型',
  'Default KB': '默认知识库',
  'GraphRAG evidence': 'GraphRAG 证据',
  Entity: '实体',
  Score: '得分',
  Expand: '扩展',
  'Entity query': '实体查询',
  readonly: '只读',
  'Ask the knowledge graph': '询问知识图谱',
  'GraphRAG search': 'GraphRAG 检索',
  'Source entity': '源实体',
  'Target entity': '目标实体',
  'Find paths': '查找路径',
  'Find direct relations': '查找直接关系',
  'Text evidence': '文本证据',
  Paths: '路径',
  'Direct relationships': '直接关系',
  'Graph evidence': '图谱证据',
  'No text evidence.': '暂无文本证据。',
  'No paths.': '暂无路径。',
  'No direct relationships.': '暂无直接关系。',
  'No evidence.': '暂无证据。',
  'Open source chunk': '打开来源分块',
  'Source chunk': '来源分块',
  Chunk: '分块',
  'Object key': '对象 Key',
  'No source chunk loaded.': '未加载来源分块。',

  // Memory
  Memory: '记忆',
  'Memory review': '记忆审核',
  Memories: '记忆',
  Candidate: '候选记忆',
  Review: '审核',
  'Model context': '模型上下文',
  'Save memory': '保存记忆',
  Snapshot: '快照',
  Thread: '会话',
  Query: '查询',
  Field: '字段',
  Value: '值',
  Summary: '摘要',
  'Memory summary': '记忆摘要',
  Confidence: '置信度',
  'Search memory': '搜索记忆',
  Deleted: '已删除',
  'Memory sync': '记忆同步',
  'Queue sync': '排队同步',
  'No pending memory candidates': '暂无待审核记忆候选',
  'Disable memory': '禁用记忆',
  'Enable memory': '启用记忆',
  'Disable memory injection?': '禁用记忆注入？',
  'Enable memory injection?': '启用记忆注入？',
  'Delete memory': '删除记忆',
  'Delete this memory?': '删除这条记忆？',
  'Reject memory': '拒绝记忆',
  'Reject this memory candidate?': '拒绝这个记忆候选？',
  'Memory detail': '记忆详情',
  'Save changes': '保存修改',
  'Full memory detail is unavailable.': '完整记忆详情不可用。',
  'Search hits': '搜索结果',
  Included: '已包含',
  'Deleted memories are removed from model context and sync state until restored or recreated.':
    '删除的记忆会从模型上下文和同步状态中移除，直到恢复或重新创建。',
  'Rejected memory candidates are not injected into future model context.':
    '被拒绝的记忆候选不会注入未来的模型上下文。',
  'Full memory content': '完整记忆内容',
  'optional query': '可选查询',
  'optional canonical value': '可选标准值',

  // MCP
  Server: '服务',
  Servers: '服务列表',
  Transport: '传输方式',
  Tools: 'Tool',
  Tool: 'Tool',
  Policy: '策略',
  Schema: 'Schema',
  'Server config': '服务配置',
  'Server details': '服务详情',
  'MCP JSON import': 'MCP JSON 导入',
  'MCP server is not model-visible yet': 'MCP 服务还没有对模型可见',
  'Model-visible MCP inventory': '模型可见 MCP 清单',
  'Model tool': '模型 Tool',
  'Refresh snapshot': '刷新快照',
  Reconnect: '重连',
  'Reconnect server': '重连服务',
  'Queue refresh': '排队刷新',
  'Save config': '保存配置',
  'Confirm save': '确认保存',
  'Load JSON into form': '加载 JSON 到表单',
  'Import all servers': '导入全部服务',
  'Import servers': '导入服务',
  'Import and refresh': '导入并刷新',
  'Import all MCP servers from JSON?': '从 JSON 导入全部 MCP 服务？',
  'Import MCP servers and refresh snapshots?': '导入 MCP 服务并刷新快照？',
  Timeout: '超时',
  Scope: '范围',
  Command: '命令',
  Args: '参数',
  URL: 'URL',
  'Public headers JSON': '公开请求头 JSON',
  'Env JSON': '环境变量 JSON',
  'Secret env refs JSON': '密钥环境变量引用 JSON',
  'Headers ref': '请求头密钥引用',
  Pick: '选择',
  'Auth type': '认证类型',
  'OAuth credential ref': 'OAuth 凭据引用',
  'Last error': '最近错误',
  'Object keys': '对象 Key',
  'Manifest / config snapshot': 'Manifest / 配置快照',
  'Capability snapshot': '能力快照',
  'Disable tool': '禁用 Tool',
  'Enable tool': '启用 Tool',
  'Disable this MCP tool?': '禁用这个 MCP Tool？',
  'Enable this MCP tool?': '启用这个 MCP Tool？',
  'No server details loaded': '未加载服务详情',
  'This queues a capability snapshot refresh and can change which MCP tools are visible to the model.':
    '这会排队刷新能力快照，并可能改变模型可见的 MCP 工具。',
  'This reconnects the MCP server and refreshes runtime capability state.':
    '这会重连 MCP 服务并刷新运行时能力状态。',
  'Saving server config can invalidate the current capability snapshot and change model-visible tools after refresh.':
    '保存服务配置可能使当前能力快照失效，并在刷新后改变模型可见工具。',
  'Paste a JSON object with mcpServers. Load one server into the form for review, or import every server through the existing MCP config API.':
    '粘贴包含 mcpServers 的 JSON。你可以先把一个服务加载到表单检查，也可以通过现有 MCP 配置 API 一次导入全部服务。',
  'Paste a JSON object with mcpServers. Load one server into the form for review, or import every server and queue capability snapshot refresh jobs.':
    '粘贴包含 mcpServers 的 JSON。可以先加载一个服务到表单检查，也可以导入全部服务并排队刷新能力快照。',
  'This saves every server under mcpServers through the existing MCP config API. Review secrets before importing plaintext headers.':
    '这会通过现有 MCP 配置 API 保存 mcpServers 下的每个服务。导入明文请求头前请先检查密钥。',
  'This saves every server under mcpServers, then queues snapshot refresh jobs so successful MCP tools become model-visible. Review secrets before importing plaintext headers.':
    '这会保存 mcpServers 下的每个服务，然后排队刷新 snapshot，让成功连接的 MCP Tool 进入模型可见清单。导入明文 headers 前请先检查密钥。',
  'The model can only see MCP tools after a capability snapshot succeeds. This server is configured but currently has no model-visible tools. Run Refresh snapshot, then check the Job result. For stdio MCP in Docker, the command runs inside the backend container, so Windows paths and npx.cmd are not available unless you mount the folder and install the command in that container.':
    '模型只有在能力快照成功后才能看到 MCP Tool。这个服务已经配置，但当前没有模型可见 Tool。请运行刷新快照，然后检查 Job 结果。Docker 中的 stdio MCP 命令运行在 backend 容器里，因此 Windows 路径和 npx.cmd 默认不可用，除非你挂载目录并在容器里安装对应命令。',
  'No last_error reported.': '暂无 last_error。',
  'No object keys in the detail payload.': '详情载荷中没有对象 Key。',
  'No capability snapshot available.': '暂无能力快照。',
  '{"GITHUB_TOKEN":"secret_ref://mcp_headers/github"}':
    '{"GITHUB_TOKEN":"secret_ref://mcp_headers/github"}',

  // Skills
  Skill: 'Skill',
  'Selected skill': '已选 Skill',
  'No skill selected.': '未选择 Skill。',
  'No running chat run detected.': '未检测到正在运行的对话。',
  Run: '运行',
  Reason: '原因',
  'Propose skill': '提议 Skill',
  'Create approved skill': '创建已批准 Skill',
  Proposal: '提议',
  Approval: '审批',
  Version: '版本',
  'Display name': '显示名称',
  'Skill ID': 'Skill ID',
  Entrypoint: '入口',
  'Workflow steps': '流程步骤',
  'When to use': '使用时机',
  'Knowledge notes': '知识备注',
  Sandbox: '沙箱',
  'Write mode': '写入模式',
  'Script content': '脚本内容',
  'File read': '文件读取',
  'Database read': '数据库读取',
  Propose: '提议',
  'Create skill': '创建 Skill',
  'Use latest proposal': '使用最新提议',
  'Create latest skill': '创建最新 Skill',
  Validate: '验证',
  'Disable skill': '禁用 Skill',
  'Disable this Skill?': '禁用这个 Skill？',
  'Search skills': '搜索 Skill',
  'Disabling a Skill removes it from activation and model-visible use until it is re-enabled.':
    '禁用 Skill 会将其从激活和模型可见使用中移除，直到重新启用。',
  'activation reason': '激活原因',
  'Contract cleanup workflow': '合同清理流程',
  'Normalize contract text and extract reusable metadata.':
    '规范化合同文本并提取可复用元数据。',
  'One step per line': '每行一个步骤',
  'One item per line': '每行一项',
  'One note per line': '每行一条备注',
  'Optional script body': '可选脚本内容',

  // SubAgents
  Task: 'Task',
  Runtime: 'Runtime',
  Budget: '预算',
  'Create SubAgent task': '创建 SubAgent Task',
  'SubAgent tasks': 'SubAgent Task',
  'SubAgent task': 'SubAgent Task',
  Objective: '目标',
  'Expected output': '期望输出',
  'Create task': '创建 Task',
  'Read scope': '读取范围',
  'Write scope': '写入范围',
  'Execution job': '执行 Job',
  Result: '结果',
  Accept: '接受',
  'Needs revision': '需要修改',
  none: '无',
  All: '全部',
  'read_scope, one per line': '读取范围，每行一个',
  'write_scope, one per line': '写入范围，每行一个',
  'write_scope disabled in readonly mode': '只读模式下禁用写入范围',
  'allowed_tools, one per line': '允许工具，每行一个',
  'forbidden_tools, one per line': '禁止工具，每行一个',
  'No task selected.': '未选择 Task。',

  // Readiness
  Check: '检查项',
  Category: '分类',
  Evidence: '证据',
  'P0 readiness': 'P0 就绪状态',
  'required checks ok': '必需检查通过',
  'action required': '需要处理',
  Environment: '环境',
  Generated: '生成时间',
  Blockers: '阻塞项',
  'No required blockers.': '暂无必需阻塞项。',
  'Model config smoke': '模型配置 Smoke',
  'Database health': '数据库健康',
  'Database health snapshot': '数据库健康快照',
  Checks: '检查项',
  'No readiness snapshot loaded.': '未加载就绪快照。',
  'Source status': '来源状态',
  Duration: '耗时',
  CWD: '工作目录',
  'Next action': '下一步',
  'Route smoke evidence': '路由 Smoke 证据',
  'Final handoff': '最终交接',
  'stdout tail': 'stdout 尾部',
  'stderr tail': 'stderr 尾部',
  details: '详情',
  Route: '路由',
  'non passing:': '未通过：',
  'executed failures:': '执行失败：',

  // Logs
  Level: '级别',
  Component: '组件',
  Event: '事件',
  Message: '消息',
  Trace: 'Trace',
  Time: '时间',
  Full: '完整日志',
  Errors: '错误',
  'Diagnostic bundle': '诊断包',
  'Log archive': '日志归档',
  'Archive logs': '归档日志',
  'trace_id / run_id / text': 'trace_id / run_id / 文本',
  'Open Jobs': '打开任务',
  'Reload logs': '重新加载日志',
  'Diagnostic bundle queued': '诊断包已排队',
  'Log archive queued': '日志归档已排队',
  'Job status': 'Job 状态',
  'Bundle ID': '包 ID',
  'Runtime instance ID': '运行实例 ID',
  'Related job ID': '关联任务 ID',
  'Manifest object key': 'Manifest 对象 Key',
  'Package object key': '包对象 Key',
  'Created at': '创建时间',
  Metadata: '元数据',
  Redacted: '已脱敏',

  // Settings
  'Model API configuration': '模型 API 配置',
  'API key secret': 'API Key 密钥',
  'Base URL': 'Base URL',
  'Context window': '上下文窗口',
  'Max output': '最大输出',
  'Timeout ms': '超时时间 ms',
  'Tool calling': 'Tool Calling',
  Purpose: '用途',
  Revision: '修订',
  Error: '错误',
  'Model test passed': '模型测试通过',
  'Model test failed': '模型测试失败',
  'Select stored secret': '选择已保存密钥',
  'Save model config': '保存模型配置',
  'Database connections': '数据库连接',
  'Load config': '加载配置',
  'Save database config': '保存数据库配置',
  'Save database connection config?': '保存数据库连接配置？',
  'Saving database config changes runtime endpoints, TLS flags, and credential references for backend services.':
    '保存数据库配置会修改后端服务的运行时端点、TLS 标记和凭据引用。',
  'Get health': '获取健康状态',
  'Run health': '运行健康检查',
  Mode: '模式',
  Endpoint: '端点',
  Bucket: 'Bucket',
  Flags: '标记',
  'Credential refs': '凭据引用',
  'Access key': 'Access key',
  'Secret key': 'Secret key',
  Options: '选项',
  TLS: 'TLS',
  Checked: '检查时间',
  'Fix database settings before saving.': '保存前请修复数据库设置。',
  'Review database settings.': '请检查数据库设置。',
  'Enabled target requires an endpoint.': '已启用目标必须填写端点。',
  'Local mode has credential refs; confirm this is intentional.':
    '本地模式配置了凭据引用，请确认这是有意设置。',
  'Credential refs JSON is invalid.': '凭据引用 JSON 无效。',
  'Options JSON is invalid.': '选项 JSON 无效。',
  'Selected secret': '已选密钥',
  Masked: '掩码',
  References: '引用',
  Rotate: '轮换',
  'new encrypted value': '新的加密值',
  'No references.': '暂无引用。',
  'No secret selected.': '未选择密钥。',
  'Enable secret': '启用密钥',
  'Disable secret': '禁用密钥',
  'Enable this secret?': '启用这个密钥？',
  'Disable this secret?': '禁用这个密钥？',
  'Delete secret': '删除密钥',
  'Delete this secret?': '删除这个密钥？',
  'Rotate secret': '轮换密钥',
  'Rotate this secret?': '轮换这个密钥？',
  'The previous value will stop being used after rotation succeeds.':
    '轮换成功后会停止使用之前的值。',
  'This may break any model, database, or MCP configuration that still references the secret.':
    '这可能会影响仍引用该密钥的模型、数据库或 MCP 配置。',
  'Main chat API key': '主对话 API Key',
  'stored encrypted': '加密保存',

  // Status and enum values
  active: '启用中',
  approved: '已批准',
  blocked: '已阻塞',
  cancelled: '已取消',
  completed: '已完成',
  configured: '已配置',
  connected: '已连接',
  created: '已创建',
  degraded: '降级',
  deleted: '已删除',
  disabled: '已禁用',
  embedded: '已向量化',
  enabled: '已启用',
  failed: '失败',
  healthy: '健康',
  indexed: '已索引',
  local: '本地',
  optional: '可选',
  pass: '通过',
  pending: '等待中',
  pending_approval: '待审批',
  queued: '排队中',
  raw: '原始',
  redacted: '已脱敏',
  rejected: '已拒绝',
  remote: '远程',
  required: '必需',
  retryable: '可重试',
  reviewed: '已审核',
  running: '运行中',
  searchable: '可检索',
  stopped: '已停止',
  succeeded: '成功',
  unknown: '未知',
  unknown_outcome: '结果未知',
  uploaded: '已上传',
  warn: '警告',
  warning: '警告',
}

export function startDomTranslationObserver(language: AppLanguage) {
  if (typeof window === 'undefined' || !document.body) {
    return () => {}
  }

  let scheduled = false
  let applying = false

  const apply = () => {
    scheduled = false
    applying = true
    try {
      applyDomTranslations(language, document.body)
    } finally {
      applying = false
    }
  }

  const schedule = () => {
    if (scheduled || applying) {
      return
    }
    scheduled = true
    const runLater = window.requestAnimationFrame ?? ((callback: FrameRequestCallback) => {
      window.setTimeout(() => callback(Date.now()), 0)
      return 0
    })
    runLater(apply)
  }

  apply()

  const observer = new MutationObserver(schedule)
  observer.observe(document.body, {
    attributeFilter: [...TRANSLATABLE_ATTRIBUTES],
    attributes: true,
    characterData: true,
    childList: true,
    subtree: true,
  })

  return () => observer.disconnect()
}

export function translateUiText(text: string, language: AppLanguage) {
  if (language === 'en') {
    return text
  }

  const leading = text.match(/^\s*/)?.[0] ?? ''
  const trailing = text.match(/\s*$/)?.[0] ?? ''
  const body = text.trim().replace(/\s+/g, ' ')
  if (!body) {
    return text
  }

  const exact = zhDictionary[body]
  if (exact) {
    return `${leading}${exact}${trailing}`
  }

  const dynamic = translateDynamicText(body)
  return dynamic === body ? text : `${leading}${dynamic}${trailing}`
}

function applyDomTranslations(language: AppLanguage, root: ParentNode) {
  translateTextNodes(language, root)
  translateAttributes(language, root)
}

function translateTextNodes(language: AppLanguage, root: ParentNode) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement
      if (!parent || shouldSkipTextElement(parent) || !node.nodeValue?.trim()) {
        return NodeFilter.FILTER_REJECT
      }
      return NodeFilter.FILTER_ACCEPT
    },
  })

  const nodes: Text[] = []
  while (walker.nextNode()) {
    nodes.push(walker.currentNode as Text)
  }

  nodes.forEach((node) => {
    const currentValue = node.nodeValue ?? ''
    let original = textOriginals.get(node)
    if (!original) {
      original = currentValue
      textOriginals.set(node, original)
    } else {
      const translatedOriginal = translateUiText(original, 'zh')
      const currentLooksLikeTrackedValue =
        currentValue === original || currentValue === translatedOriginal
      if (!currentLooksLikeTrackedValue) {
        original = currentValue
        textOriginals.set(node, original)
      }
    }
    const nextValue = language === 'zh' ? translateUiText(original, language) : original
    if (node.nodeValue !== nextValue) {
      node.nodeValue = nextValue
    }
  })
}

function translateAttributes(language: AppLanguage, root: ParentNode) {
  const elements =
    root instanceof Element
      ? [root, ...Array.from(root.querySelectorAll('*'))]
      : Array.from(root.querySelectorAll('*'))

  elements.forEach((element) => {
    if (shouldSkipAttributeElement(element)) {
      return
    }
    TRANSLATABLE_ATTRIBUTES.forEach((attribute) => {
      const value = element.getAttribute(attribute)
      if (!value?.trim()) {
        return
      }
      let originals = attributeOriginals.get(element)
      if (!originals) {
        originals = new Map()
        attributeOriginals.set(element, originals)
      }
      let original = originals.get(attribute)
      if (!original) {
        original = value
        originals.set(attribute, original)
      } else {
        const translatedOriginal = translateUiText(original, 'zh')
        const valueLooksLikeTrackedValue =
          value === original || value === translatedOriginal
        if (!valueLooksLikeTrackedValue) {
          original = value
          originals.set(attribute, original)
        }
      }
      const nextValue = language === 'zh' ? translateUiText(original, language) : original
      if (element.getAttribute(attribute) !== nextValue) {
        element.setAttribute(attribute, nextValue)
      }
    })
  })
}

function shouldSkipTextElement(element: Element) {
  return Boolean(element.closest(SKIP_TEXT_SELECTOR))
}

function shouldSkipAttributeElement(element: Element) {
  return Boolean(element.closest(SKIP_ATTRIBUTE_SELECTOR))
}

function translateDynamicText(text: string) {
  const replacements: Array<[RegExp, string | ((...args: string[]) => string)]> = [
    [/^Status: (.+); run: (.+)$/u, '状态：$1；Run：$2'],
    [/^processed: (.+)$/u, '已处理：$1'],
    [/^ticks: (.+)$/u, '轮询次数：$1'],
    [/^last tick: (.+)$/u, '上次轮询：$1'],
    [/^worker (running|stopped)$/u, (_, status) => `Worker ${translateUiText(status, 'zh')}`],
    [/^failed (.+)$/u, '失败 $1'],
    [/^embedded (.+)$/u, '已向量化 $1'],
    [/^depth (.+)$/u, '深度 $1'],
    [/^confidence (.+)$/u, '置信度 $1'],
    [/^latest (.+) at (.+)$/u, '最近 $1 于 $2'],
    [/^(.+) queued\/running$/u, '$1 个排队/运行中'],
    [/^(.+) pending targets$/u, '$1 个待同步目标'],
    [/^(.+) configured$/u, '$1 个已配置'],
    [/^(.+) enabled$/u, '$1 个已启用'],
    [/^(.+) remote$/u, '$1 个远程'],
    [/^(.+) active MCP secrets? available\.$/u, '$1 个可用 MCP 密钥。'],
    [/^Latency: (.+)$/u, '延迟：$1'],
    [/^Purpose: (.+)$/u, '用途：$1'],
    [/^Revision: (.+)$/u, '修订：$1'],
    [/^Tools: (.+)$/u, 'Tool：$1'],
    [/^Included: (.+)$/u, '已包含：$1'],
    [/^ready: (.+)$/u, '就绪：$1'],
    [/^missing flags: (.+)$/u, '缺失标记：$1'],
    [/^missing: (.+)$/u, '缺失：$1'],
    [/^non passing: (.+)$/u, '未通过：$1'],
    [/^executed failures: (.+)$/u, '执行失败：$1'],
    [/^stale checks: (.+)$/u, '过期检查：$1'],
    [/^stale flags: (.+)$/u, '过期标记：$1'],
    [/^pass (.+)$/u, '通过 $1'],
    [/^warn (.+)$/u, '警告 $1'],
    [/^fail (.+)$/u, '失败 $1'],
    [/^blocked (.+)$/u, '阻塞 $1'],
    [/^(.+): (.+)$/u, (_, label, value) => {
      const translatedLabel = zhDictionary[label] ?? label
      return translatedLabel === label ? `${label}: ${value}` : `${translatedLabel}：${value}`
    }],
  ]

  for (const [pattern, replacement] of replacements) {
    if (pattern.test(text)) {
      return text.replace(pattern, replacement as string)
    }
  }

  return text
}
