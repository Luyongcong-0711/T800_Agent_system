export type WorkspaceId = string

export interface PageParams {
  limit?: number
  status?: string
  job_type?: string
  parent_run_id?: string
  related_run_id?: string
  level?: string
  trace_id?: string
  run_id?: string
  after_event_id?: string
  wait_ms?: number
  query?: string
  component?: string
  include_deleted?: boolean
}

export interface CreateJobInput {
  job_type: string
  priority?: 'low' | 'normal' | 'high'
  title?: string | null
  target_scope?: Record<string, string>
  input?: object
  idempotency_key?: string | null
  related_run_id?: string | null
  related_thread_id?: string | null
  trace_id?: string | null
}

export interface MemorySyncJobInput {
  limit?: number
  collection?: string
  model?: string
  dimension?: number
  provider?: string
}

export interface JobSummary {
  job_id: string
  workspace_id: string
  job_type: string
  status: string
  priority: string
  title: string
  target_scope: Record<string, string>
  target_scope_key?: string | null
  progress_percent: number
  current_stage?: string | null
  idempotency_key?: string
  related_run_id?: string | null
  related_thread_id?: string | null
  last_event_id?: string | null
  last_event_seq?: number
  manifest_object_key?: string | null
  event_index_object_key?: string | null
  leaf_state_object_key?: string | null
  created_by?: string | null
  created_at: string
  updated_at: string
}

export interface JobDetailResponse extends JobSummary {
  manifest: Record<string, unknown>
  leaf_state: Record<string, unknown>
}

export interface JobWorkerStatusResponse {
  workspace_id: string
  running: boolean
  job_types?: string[]
  poll_interval_seconds?: number
  max_jobs_per_tick?: number
  tick_count?: number
  processed_count?: number
  started_at?: string | null
  stopped_at?: string | null
  last_tick_at?: string | null
  last_error?: { error_type?: string; message?: string } | null
  last_result?: Record<string, unknown> | null
}

export interface ProcessNextJobResponse {
  workspace_id: string
  claimed: boolean
  job?: JobSummary | JobDetailResponse | Record<string, unknown> | null
}

export interface ListJobsResponse {
  workspace_id: string
  jobs: JobSummary[]
  next_cursor?: string | null
}

export interface JobEvent {
  event_seq: number
  event_id: string
  workspace_id: string
  job_id: string
  type: string
  created_at: string
  payload: Record<string, unknown>
}

export interface ListJobEventsResponse {
  workspace_id: string
  job_id: string
  events: JobEvent[]
  next_after_event_id?: string | null
  job_status: string
}

export type RunStatus =
  | 'created'
  | 'running'
  | 'waiting_approval'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface ThreadSummary {
  thread_id: string
  workspace_id: string
  user_id: string
  title: string
  status: 'active' | 'archived' | 'soft_deleted'
  pinned: boolean
  current_run_id?: string | null
  current_run_status?: RunStatus | null
  last_message_id?: string | null
  last_message_preview?: string | null
  last_message_at?: string | null
  message_count: number
  run_count: number
  created_at: string
  updated_at: string
}

export interface ThreadDetailResponse extends ThreadSummary {
  current_run?: Record<string, unknown> | null
}

export interface ListThreadsResponse {
  workspace_id: string
  threads: ThreadSummary[]
}

export interface ConversationMessage {
  message_id: string
  workspace_id: string
  thread_id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  run_id?: string | null
  created_at: string
}

export interface ListMessagesResponse {
  workspace_id: string
  thread_id: string
  messages: ConversationMessage[]
}

export interface RunDetailResponse {
  run_id: string
  workspace_id: string
  thread_id: string
  status: RunStatus
  idempotency_key: string
  user_message_id?: string | null
  last_event_id?: string | null
  last_event_seq: number
  assistant_message_id?: string | null
  model_error?: string | null
  trace_id?: string | null
  created_at: string
  updated_at: string
  leaf_state: Record<string, unknown>
}

export interface CancelRunResponse {
  run_id: string
  workspace_id: string
  thread_id: string
  status: RunStatus
}

export interface RecoverStaleRunsResponse {
  workspace_id: string
  recovered_count: number
  recovered_runs?: RunDetailResponse[]
}

export interface RunApprovalDecisionInput {
  reason?: string | null
  idempotency_key?: string | null
}

export type RunApprovalKind =
  | 'tool_invocation'
  | 'skill_script_staged_patch'
  | (string & {})

export interface RunApprovalArtifacts extends Record<string, unknown> {
  args_object_key?: string
  diff_object_key?: string
  manifest_object_key?: string
  operation_plan_object_key?: string
  result_object_key?: string
  stderr_object_key?: string
  stdout_object_key?: string
}

export interface RunApprovalEventPayload extends Record<string, unknown> {
  approval_id?: string
  approval_kind?: RunApprovalKind
  artifacts?: RunApprovalArtifacts
  decision?: 'approved' | 'rejected' | (string & {})
  diff_summary?: string | string[] | Record<string, unknown>
  entrypoint?: string
  entrypoint_tool_name?: string
  operation_plan_object_key?: string
  skill_run_id?: string | null
  status?: string
  tool_name?: string
  write_mode?: 'none' | 'staged_patch' | (string & {})
}

export interface RunApprovalDecisionResponse {
  run_id: string
  workspace_id: string
  thread_id: string
  approval_id: string
  decision: 'approved' | 'rejected'
  status: string
  run_status: RunStatus
  operation_plan_object_key: string
  skill_run_id?: string | null
  artifacts: RunApprovalArtifacts
  updated_at: string
}

export interface RunOperationRollbackInput {
  rollback_token: string
  reason?: string | null
  idempotency_key?: string | null
}

export interface RunOperationRollbackResponse {
  run_id: string
  workspace_id: string
  thread_id: string
  operation_id: string
  rollback_token: string
  status: string
  restored_files: Record<string, unknown>[]
  event_id?: string | null
  updated_at: string
}

export interface RunEvent {
  event_seq: number
  event_id: string
  workspace_id: string
  thread_id: string
  run_id: string
  trace_id?: string | null
  type: string
  created_at: string
  payload: Record<string, unknown>
}

export interface ListRunEventsResponse {
  workspace_id: string
  run_id: string
  events: RunEvent[]
  next_after_event_id?: string | null
  run_status: string
}

export type SubAgentMode = 'readonly' | 'write'
export type SubAgentStatus =
  | 'created'
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'reviewed'
export type SubAgentReviewDecision = 'accepted' | 'rejected' | 'needs_revision'

export interface SubAgentTaskSummary {
  task_id: string
  workspace_id: string
  parent_run_id: string
  parent_thread_id?: string | null
  agent_type: string
  objective: string
  mode: SubAgentMode
  read_scope: string[]
  write_scope: string[]
  allowed_tools: string[]
  forbidden_tools: string[]
  timeout_ms: number
  token_budget: number
  status: SubAgentStatus
  needs_main_review: boolean
  requires_main_review: boolean
  output_schema: string
  created_at: string
  updated_at: string
}

export interface SubAgentTaskDetail extends SubAgentTaskSummary {
  schema_version: number
  expected_output: string
  result?: Record<string, unknown> | null
  review?: Record<string, unknown> | null
  object_keys: Record<string, string>
}

export interface ListSubAgentTasksResponse {
  workspace_id: string
  tasks: SubAgentTaskSummary[]
}

export interface SubAgentTaskInput {
  parent_run_id: string
  parent_thread_id?: string
  agent_type: string
  objective: string
  mode: SubAgentMode
  read_scope: string[]
  write_scope: string[]
  allowed_tools: string[]
  forbidden_tools: string[]
  timeout_ms: number
  token_budget: number
  expected_output?: string
}

export interface SubAgentReviewInput {
  decision: SubAgentReviewDecision
  reviewer_notes?: string
}

export interface SubAgentReviewResponse {
  schema_version: number
  task_id: string
  workspace_id: string
  parent_run_id: string
  decision: SubAgentReviewDecision
  review_status: string
  reviewer_notes: string
  reviewed_subagent_result: Record<string, unknown>
  reviewed_at: string
}

export interface CreateThreadInput {
  title?: string | null
  idempotency_key?: string | null
}

export interface PatchThreadInput {
  title?: string | null
  status?: ThreadSummary['status'] | null
  pinned?: boolean | null
}

export interface CreateRunInput {
  user_message: string
  idempotency_key?: string | null
  trace_id?: string | null
  stream?: boolean
}

export interface LogRecord {
  timestamp: string
  severity: string
  component: string
  event_type: string
  message: string
  trace_id: string
  run_id?: string | null
  error_type?: string | null
  redacted: boolean
  [key: string]: unknown
}

export interface LogQueryResponse {
  workspace_id: string
  items: LogRecord[]
  next_cursor?: string | null
  truncated: boolean
  redacted: boolean
}

export interface LogSummaryResponse {
  workspace_id: string
  items: string[]
  next_cursor?: string | null
  truncated: boolean
  redacted: boolean
}

export interface LogArtifactResponse {
  workspace_id: string
  object_key: string
  file_name: string
  content_type: string
  artifact_type: 'json' | 'text' | 'binary' | string
  size_bytes: number
  sha256: string
  text?: string | null
  parsed_json?: unknown
  base64?: string | null
  truncated: boolean
  redacted: boolean
}

export interface DiagnosticBundleInput {
  trace_id?: string
  run_id?: string
  component?: string
  components?: string[]
  include_summary?: boolean
  include_errors?: boolean
  include_component_logs?: boolean
  request_id?: string
}

export interface DiagnosticBundleResponse {
  schema_version?: number
  bundle_id: string
  workspace_id: string
  created_by?: string
  created_at?: string
  runtime_instance_id?: string
  filters?: Record<string, unknown>
  job_id: string
  related_job_id?: string | null
  job_status: string
  manifest_object_key: string | null
  object_key: string | null
  package_object_key?: string | null
  package_sha256?: string | null
  package_bytes?: number | null
  item_counts?: Record<string, number>
  redacted: boolean
}

export interface LogArchiveJobInput {
  date?: string
  runtime_instance_id?: string
  request_id?: string
}

export interface LogArchiveJobResponse {
  schema_version?: number
  workspace_id: string
  date: string
  runtime_instance_id: string
  manifest_object_key?: string | null
  related_job_id: string
  job_id: string
  job_status: string
  redacted: boolean
}

export interface ServiceHealth {
  target: string
  status: string
  message?: string | null
  latency_ms?: number | null
  checked_at?: string | null
  details?: Record<string, unknown>
}

export type ReadinessStatus = 'pass' | 'warn' | 'fail' | 'blocked' | 'not_applicable'

export interface ReadinessCheck {
  check_id: string
  category: string
  title: string
  status: ReadinessStatus
  summary: string
  required: boolean
  evidence: string[]
  next_actions: string[]
  details: Record<string, unknown>
}

export interface ReadinessCategory {
  category: string
  status: ReadinessStatus
  pass_count: number
  warn_count: number
  fail_count: number
  blocked_count: number
  checks: ReadinessCheck[]
}

export interface P0ReadinessResponse {
  workspace_id: string
  ok: boolean
  status: ReadinessStatus
  generated_at: string
  environment: string
  runtime_instance_id: string
  summary: Record<string, number>
  categories: ReadinessCategory[]
  checks: ReadinessCheck[]
  remaining_blockers: string[]
}

export interface DatabaseTargetConfig {
  target: 'minio' | 'milvus' | 'neo4j' | 'redis'
  mode: 'local' | 'remote'
  enabled: boolean
  endpoint: string
  tls: boolean
  bucket?: string | null
  credential_refs: Record<string, string>
  options: Record<string, string>
}

export interface DatabaseConfigResponse {
  workspace_id: string
  targets: DatabaseTargetConfig[]
  updated_at: string
  revision: number
}

export interface DatabaseHealthSnapshotResponse {
  ok: boolean
  workspace_id: string
  services: ServiceHealth[]
  checked_at?: string | null
  source: 'snapshot' | 'unknown' | 'live_check' | 'job_check'
}

export type ModelConfigId =
  | 'main_chat'
  | 'graphrag_llm'
  | 'embedding'
  | 'rerank'
  | 'compression'
  | 'fallback'

export type ModelProvider = 'openai_compatible' | 'anthropic'

export interface ModelConfigResponse {
  schema_version: number
  workspace_id: string
  config_id: ModelConfigId
  display_name: string
  purpose: 'chat' | 'embedding' | 'rerank' | 'compression' | 'fallback'
  provider: ModelProvider
  model: string
  base_url?: string | null
  api_key_ref?: string | null
  context_window_tokens: number
  max_output_tokens: number
  timeout_ms: number
  supports_tool_calling: boolean
  enabled: boolean
  status: 'configured' | 'missing_secret' | 'disabled'
  source: 'stored' | 'default_env'
  updated_at: string
  revision: number
}

export interface ListModelConfigsResponse {
  workspace_id: string
  configs: ModelConfigResponse[]
}

export interface UpdateModelConfigInput {
  provider: ModelProvider
  model: string
  base_url: string
  api_key_ref?: string | null
  context_window_tokens: number
  max_output_tokens: number
  timeout_ms: number
  supports_tool_calling: boolean
  enabled: boolean
}

export interface TestModelConfigInput {
  prompt: string
  max_output_tokens: number
  config?: UpdateModelConfigInput
}

export interface TestModelConfigResponse {
  workspace_id: string
  config_id: ModelConfigId
  ok: boolean
  provider: ModelProvider
  model: string
  latency_ms: number
  content_preview?: string | null
  usage?: Record<string, number | boolean> | null
  error_type?: string | null
  retryable?: boolean
  redacted: boolean
}

export type SecretType =
  | 'model_api_key'
  | 'embedding_api_key'
  | 'rerank_api_key'
  | 'minio_access_key'
  | 'minio_secret_key'
  | 'milvus_token'
  | 'milvus_username_password'
  | 'neo4j_username_password'
  | 'mcp_headers'
  | 'mcp_oauth_credential'
  | 'http_proxy_credential'
  | 'web_fetch_credential'

export type SecretStatus = 'active' | 'disabled' | 'rotated' | 'soft_deleted'

export interface SecretSummary {
  secret_id: string
  secret_ref: string
  type: SecretType
  display_name: string
  masked: string
  status: SecretStatus
  last_used_at?: string | null
  updated_at: string
}

export interface ListSecretsResponse {
  workspace_id: string
  secrets: SecretSummary[]
}

export interface CreateSecretInput {
  type: SecretType
  display_name: string
  plaintext: string
  scope?: 'workspace'
}

export interface UpdateSecretInput {
  display_name?: string | null
  status?: 'active' | 'disabled' | null
}

export interface RotateSecretInput {
  plaintext: string
}

export interface SecretReferencesResponse {
  secret_id: string
  references: Record<string, string>[]
}

export interface MemorySummary {
  memory_id: string
  workspace_id?: string | null
  user_id: string
  scope: 'global' | 'workspace'
  type: string
  field?: string | null
  summary: string
  sensitive: boolean
  status: string
  enabled_for_model_context: boolean
  frontend_visible: boolean
  requires_approval: boolean
  confidence: number
  created_at: string
  updated_at: string
}

export interface ListMemoriesResponse {
  workspace_id: string
  memories: MemorySummary[]
}

export interface MemorySource {
  thread_id?: string | null
  message_id?: string | null
  run_id?: string | null
  evidence?: string | null
  [key: string]: unknown
}

export interface UpsertMemoryInput {
  memory_id?: string | null
  scope?: 'global' | 'workspace' | null
  type: string
  field?: string | null
  value?: string | null
  summary: string
  content: string
  source?: MemorySource
  confidence?: number
  enabled_for_model_context?: boolean
}

export interface PatchMemoryInput {
  summary?: string
  content?: string
  field?: string | null
  scope?: 'global' | 'workspace'
  value?: string | null
  confidence?: number
  enabled_for_model_context?: boolean
  status?: 'active' | 'disabled'
}

export interface MemoryDetailResponse extends MemorySummary {
  content: string
  content_object_key: string
  source: MemorySource
  value?: string | null
  visibility: 'user_visible'
  deleted_at?: string | null
  revision: number
}

export interface MemorySearchResponse {
  workspace_id: string
  hits: Record<string, unknown>[]
}

export interface MemorySyncStateResponse {
  schema_version: number
  workspace_id: string
  pending_targets: Record<string, unknown>[]
  external_sync_enabled?: boolean | null
  sync_mode?: string | null
  last_event_id?: string | null
  last_event_seq: number
  last_enqueue?: Record<string, unknown> | null
  last_processed_at?: string | null
  last_result?: Record<string, unknown> | null
  updated_at?: string | null
  revision: number
}

export interface MemorySnapshotResponse {
  memory_snapshot_id: string
  workspace_id: string
  user_id: string
  thread_id: string
  included_memory_ids: string[]
  profile: Record<string, unknown>
  preferences: string[]
  project_facts: string[]
  project_rules: string[]
  created_at: string
}

export interface SkillSummary {
  skill_id: string
  workspace_id: string
  display_name: string
  version: string
  description: string
  when_to_use: string[]
  entrypoint_count: number
  risk_level: string
  status: string
  enabled: boolean
  requires_activation: boolean
  requires_validation: boolean
  updated_at: string
}

export interface ListSkillsResponse {
  workspace_id: string
  skills: SkillSummary[]
}

export interface SkillSearchResponse {
  workspace_id: string
  items: SkillSummary[]
}

export interface SkillDetailResponse extends SkillSummary {
  summary: string
  workflow_summary: string[]
  knowledge_sections: Record<string, unknown>[]
  entrypoints: Record<string, unknown>[]
  permissions: Record<string, unknown>
  validation_status: string
  created_at: string
}

export interface SkillEntrypointInput {
  name: string
  type?: 'prompt_workflow' | 'script'
  runtime?: string | null
  args_schema?: Record<string, unknown>
  risk_level?: 'low' | 'medium' | 'high' | 'critical'
  script_required?: boolean
  script_content?: string | null
  sandbox_profile?: string | null
  timeout_ms?: number
  write_mode?: 'none' | 'staged_patch'
  file_write?: string[]
}

export interface SkillPermissionsInput {
  file_read?: string[]
  file_write?: string[]
  database_read?: string[]
  database_write?: string[]
  network?: boolean
}

export interface SkillProposalInput {
  display_name: string
  description: string
  when_to_use?: string[]
  workflow_steps: string[]
  knowledge_notes?: string[]
  entrypoints?: SkillEntrypointInput[]
  scripts?: Record<string, unknown>[]
  permissions?: SkillPermissionsInput
  script_required?: boolean
  source?: MemorySource
}

export interface SkillProposalResponse {
  schema_version?: number
  proposal_id: string
  workspace_id: string
  display_name: string
  description: string
  when_to_use: string[]
  workflow_steps: string[]
  knowledge_notes: string[]
  entrypoints: Record<string, unknown>[]
  permissions: Record<string, unknown>
  source: Record<string, unknown>
  script_required: boolean
  risk_level: 'low' | 'medium' | 'high' | 'critical'
  approval_required: boolean
  approval_id: string
  status: 'pending_approval' | 'materialized'
  created_at: string
  updated_at: string
}

export interface SkillCreateFromProposalInput {
  proposal_id: string
  approval_id: string
  skill_id?: string | null
  version?: string
}

export interface SkillActivateInput {
  run_id: string
  thread_id: string
  reason: string
  version?: string | null
}

export interface SkillDisableInput {
  reason?: string | null
}

export interface SkillValidateInput {
  version?: string | null
}

export interface SkillActivationResponse {
  workspace_id: string
  run_id: string
  thread_id: string
  skill_id: string
  version: string
  reason: string
  activated_entrypoint_tools: string[]
  context_block_object_key: string
  created_at: string
}

export interface McpServerSummary {
  server_name: string
  transport?: 'stdio' | 'http' | 'streamable_http' | 'sse' | string
  enabled: boolean
  status?: string
  scope?: string
  type?: string
  last_seen?: string | null
  tool_count?: number
  stale?: boolean
  last_error?: Record<string, unknown> | null
  last_snapshot_hash?: string | null
  updated_at?: string | null
}

export interface McpServersResponse {
  workspace_id: string
  servers: McpServerSummary[]
}

export interface McpServerDetailResponse {
  workspace_id: string
  server_name: string
  server: Record<string, unknown>
  snapshot?: Record<string, unknown> | null
}

export type McpRiskLevel = 'low' | 'medium' | 'high' | 'critical'

export interface McpToolSummary {
  server_name: string
  name: string
  model_name?: string
  tool_name?: string
  original_tool_name?: string
  normalized_name?: string
  normalized_tool_name?: string
  description?: string
  args_schema?: Record<string, unknown>
  input_schema_hash?: string
  args_schema_hash?: string
  enabled: boolean
  policy_enabled?: boolean
  risk_level?: McpRiskLevel | string
  requires_approval?: boolean
  side_effect?: boolean
  name_conflict?: boolean
  schema_changed?: boolean
  disabled_reason?: string | null
  source?: string
  transport?: string
  timeout_ms?: number
  [key: string]: unknown
}

export interface McpToolsResponse {
  workspace_id: string
  server_name: string
  tools: McpToolSummary[]
}

export interface McpRefreshInput {
  refresh_reason?: string
  idempotency_key?: string | null
}

export interface McpRefreshResponse {
  workspace_id: string
  server_name: string
  server: Record<string, unknown>
  snapshot: Record<string, unknown>
  refresh_job: JobSummary
  job_id: string
}

export interface McpServerHealthResponse {
  workspace_id: string
  server_name: string
  enabled: boolean
  transport: string
  status: string
  runtime_configured: boolean
  connected: boolean
  stale: boolean
  tool_count: number
  last_seen?: string | null
  last_error?: Record<string, unknown> | null
  snapshot_hash?: string | null
  snapshot_updated_at?: string | null
  next_action: string
  live_probe?: Record<string, unknown> | null
  reconnect: {
    supported: boolean
    mode: string
    refresh_reason: string
    uses_sse_job_progress: boolean
  }
}

export interface McpServerConfigInput {
  transport: 'stdio' | 'http' | 'streamable_http' | 'sse'
  enabled: boolean
  scope?: 'workspace' | 'system'
  timeout_ms?: number
  command?: string | null
  args?: string[]
  cwd?: string | null
  env?: Record<string, string>
  secret_env_refs?: Record<string, string>
  url?: string | null
  public_headers?: Record<string, string>
  headers_ref?: string | null
  auth_type?: string | null
  oauth_credential_ref?: string | null
}

export interface McpReconnectInput {
  idempotency_key?: string | null
}

export interface McpReconnectResponse extends McpRefreshResponse {
  health: McpServerHealthResponse
}

export interface McpToolPolicyInput {
  server_name: string
  tool_name: string
  enabled: boolean
  risk_level?: McpRiskLevel | string
  input_schema_hash?: string | null
}

export interface McpToolPolicyResponse {
  workspace_id: string
  server_name: string
  tool_name: string
  model_name?: string
  enabled: boolean
  risk_level: McpRiskLevel | string
  updated_by: string
  updated_at: string
  policy_version: number
}

export interface ToolInventoryItem {
  name: string
  description?: string
  source?: string
  server_name?: string
  original_tool_name?: string
  enabled?: boolean
  name_conflict?: boolean
  disabled_reason?: string | null
  risk_level?: string
  requires_approval?: boolean
  [key: string]: unknown
}

export interface ToolInventoryResponse {
  workspace_id: string
  tools: ToolInventoryItem[]
  created_at: string
}

export interface KnowledgeBaseResponse {
  workspace_id: string
  knowledge_base_id: string
  name: string
  status: string
  manifest_object_key?: string | null
  updated_at: string
}

export interface ListKnowledgeBasesResponse {
  workspace_id: string
  knowledge_bases: KnowledgeBaseResponse[]
}

export interface CreateKnowledgeBaseInput {
  knowledge_base_id: string
  name?: string | null
}

export interface UploadDocumentInput {
  content: string
  source_file_name: string
  mime_type?: string | null
  metadata?: Record<string, unknown>
  idempotency_key?: string | null
}

export interface DocumentSummary {
  workspace_id: string
  knowledge_base_id: string
  doc_id: string
  doc_version_id: string
  current_doc_version_id?: string | null
  source_file_name: string
  mime_type: string
  size_bytes?: number | null
  file_sha256?: string | null
  title?: string | null
  parser_quality?: string | null
  ingestion_status: string
  parse_status?: string | null
  chunk_status?: string | null
  embedding_status?: string | null
  graph_status?: string | null
  chunk_total: number
  chunk_embedded: number
  chunk_failed: number
  search_available: boolean
  graphrag_available: boolean
  retryable?: boolean
  failure_strategy?: string | null
  last_error?: Record<string, unknown> | null
  last_job_id?: string | null
  job_id?: string | null
  job_type?: string | null
  warnings?: Record<string, unknown>[]
  object_keys?: Record<string, unknown>
  created_at?: string | null
  updated_at?: string | null
}

export interface ListDocumentsResponse {
  workspace_id: string
  knowledge_base_id: string
  documents: DocumentSummary[]
  next_cursor?: string | null
}

export interface ActiveEmbeddingResponse {
  schema_version?: number
  workspace_id?: string
  knowledge_base_id?: string
  version_id: string
  provider: string
  model: string
  dimension: number
  collection: string
  status: string
  previous_version_id?: string | null
  previous_collection?: string | null
  chunk_count?: number
  active?: boolean
  updated_at?: string
  revision?: number
  [key: string]: unknown
}

export interface EmbeddingReindexInput {
  provider?: string | null
  model?: string | null
  dimension?: number | null
  collection?: string | null
  config_id?: string | null
  idempotency_key?: string | null
}

export interface EmbeddingReindexResponse {
  workspace_id: string
  knowledge_base_id: string
  job_id: string
  job_type: 'embedding_reindex_job'
  job_status: string
  active_embedding: ActiveEmbeddingResponse | Record<string, unknown>
}

export interface GraphSchemaResponse {
  labels: string[]
  relationships: string[]
  properties?: Record<string, string[]>
  allowed_depth?: number
  readonly?: boolean
  warnings?: string[]
  [key: string]: unknown
}

export interface GraphEntity {
  entity_id: string
  name?: string
  entity_type?: string | null
  aliases?: string[]
  evidence_count?: number
  score?: number
  match_type?: string
  [key: string]: unknown
}

export interface GraphRelationship {
  type?: string | null
  direction?: string | null
  fact_id?: string | null
  source_entity_id?: string | null
  target_entity_id?: string | null
  confidence?: number | null
  relation_strength?: number | string | null
  evidence_ids?: string[]
  [key: string]: unknown
}

export interface GraphPath {
  path_id: string
  depth: number
  source_entity_id?: string | null
  target_entity_id?: string | null
  nodes: GraphEntity[] | Record<string, unknown>[]
  relationships: GraphRelationship[]
  direction_preserved?: boolean
  [key: string]: unknown
}

export interface GraphEvidence {
  evidence_id?: string
  fact_id?: string
  source_chunk_id?: string
  chunk_id?: string
  chunk_object_key?: string
  evidence_text?: string
  chunk_text?: string
  source?: Record<string, unknown>
  [key: string]: unknown
}

export interface GraphEntitySearchInput {
  knowledge_base_id: string
  query: string
  entity_types?: string[]
  limit?: number
  include_aliases?: boolean
}

export interface GraphEntitySearchResponse {
  entities: GraphEntity[]
  warnings: string[]
}

export interface GraphExpandEntityInput {
  knowledge_base_id: string
  depth?: number
  relationship_allowlist?: string[]
  limit?: number
  include_evidence?: boolean
}

export interface GraphExpandEntityResponse {
  entity_id: string
  paths: GraphPath[]
  warnings: string[]
}

export interface GraphFindPathsInput {
  knowledge_base_id: string
  source_entity: string
  target_entity: string
  max_depth?: number
  relationship_allowlist?: string[]
  limit?: number
}

export interface GraphFindPathsResponse {
  paths: GraphPath[]
  empty: boolean
  warnings: string[]
}

export interface GraphFindRelationshipInput {
  knowledge_base_id: string
  source_entity: string
  target_entity: string
  relationship_allowlist?: string[]
  include_evidence?: boolean
}

export interface GraphFindRelationshipResponse {
  source: GraphEntity | Record<string, unknown>
  target: GraphEntity | Record<string, unknown>
  relationships: GraphRelationship[]
  empty: boolean
  warnings: string[]
}

export interface GraphEvidenceInput {
  knowledge_base_id: string
  fact_ids?: string[]
  evidence_ids?: string[]
  include_chunk_text?: boolean
  max_chars_per_chunk?: number
}

export interface GraphEvidenceResponse {
  evidence: GraphEvidence[]
  warnings: string[]
}

export interface GraphRagTextEvidence {
  chunk_id?: string
  doc_id?: string
  doc_version_id?: string
  score?: number | null
  object_key?: string
  text?: string
  source?: Record<string, unknown>
  metadata_filter?: Record<string, unknown>
  [key: string]: unknown
}

export interface GraphRagSearchInput {
  knowledge_base_id: string
  query: string
  filters?: Record<string, unknown>
  top_k?: number
  final_top_k?: number
  graph_depth?: number
  relationship_allowlist?: string[]
  include_sources?: boolean
}

export interface GraphRagSearchResponse {
  text_evidence: GraphRagTextEvidence[]
  graph_evidence: GraphEvidence[] | GraphPath[]
  warnings: string[]
}

export interface GraphBuildResponse {
  ok: boolean
  workspace_id: string
  knowledge_base_id: string
  doc_id: string
  job_id: string
  result: Record<string, unknown>
}

export interface ChunkResponse {
  workspace_id?: string | null
  knowledge_base_id?: string | null
  doc_id: string
  doc_version_id?: string | null
  chunk_id: string
  parent_chunk_id?: string | null
  chunk_index?: number | null
  chunk_type?: string | null
  text: string
  section_path: string[]
  page_start?: number | null
  page_end?: number | null
  char_start?: number | null
  char_end?: number | null
  source_block_ids?: string[]
  token_count?: number | null
  text_hash?: string | null
  metadata_filter?: Record<string, unknown>
  source?: Record<string, unknown>
  object_key?: string | null
}

export interface ListChunksResponse {
  workspace_id: string
  knowledge_base_id: string
  doc_id: string
  chunks: ChunkResponse[]
}
