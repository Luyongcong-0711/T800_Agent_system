import type { BootstrapResponse, ErrorResponse } from './schemas/bootstrap'
import type {
  ChunkResponse,
  ActiveEmbeddingResponse,
  CreateKnowledgeBaseInput,
  CreateJobInput,
  CreateRunInput,
  CreateSecretInput,
  CreateThreadInput,
  CancelRunResponse,
  DatabaseConfigResponse,
  DatabaseHealthSnapshotResponse,
  DatabaseTargetConfig,
  DiagnosticBundleInput,
  DiagnosticBundleResponse,
  DocumentSummary,
  EmbeddingReindexInput,
  EmbeddingReindexResponse,
  GraphBuildResponse,
  GraphEntitySearchInput,
  GraphEntitySearchResponse,
  GraphEvidenceInput,
  GraphEvidenceResponse,
  GraphExpandEntityInput,
  GraphExpandEntityResponse,
  GraphFindRelationshipInput,
  GraphFindRelationshipResponse,
  GraphFindPathsInput,
  GraphFindPathsResponse,
  GraphRagSearchInput,
  GraphRagSearchResponse,
  GraphSchemaResponse,
  JobDetailResponse,
  JobSummary,
  JobWorkerStatusResponse,
  KnowledgeBaseResponse,
  ListChunksResponse,
  ListDocumentsResponse,
  ListJobEventsResponse,
  ListJobsResponse,
  ListKnowledgeBasesResponse,
  ListMessagesResponse,
  ListMemoriesResponse,
  ListModelConfigsResponse,
  ListRunEventsResponse,
  ListSecretsResponse,
  ListSkillsResponse,
  ListThreadsResponse,
  LogArtifactResponse,
  LogArchiveJobInput,
  LogArchiveJobResponse,
  LogQueryResponse,
  LogSummaryResponse,
  McpServersResponse,
  McpServerConfigInput,
  McpReconnectInput,
  McpReconnectResponse,
  McpRefreshInput,
  McpRefreshResponse,
  McpServerDetailResponse,
  McpServerHealthResponse,
  McpToolPolicyInput,
  McpToolPolicyResponse,
  McpToolsResponse,
  MemoryDetailResponse,
  MemorySyncJobInput,
  MemorySyncStateResponse,
  PatchMemoryInput,
  PatchThreadInput,
  MemorySearchResponse,
  MemorySnapshotResponse,
  ModelConfigId,
  ModelConfigResponse,
  PageParams,
  P0ReadinessResponse,
  ProcessNextJobResponse,
  RecoverStaleRunsResponse,
  RunDetailResponse,
  RunApprovalDecisionInput,
  RunApprovalDecisionResponse,
  RotateSecretInput,
  RunOperationRollbackInput,
  RunOperationRollbackResponse,
  SecretReferencesResponse,
  SecretSummary,
  SkillActivationResponse,
  SkillActivateInput,
  SkillCreateFromProposalInput,
  SkillDetailResponse,
  SkillDisableInput,
  SkillProposalInput,
  SkillProposalResponse,
  SkillSearchResponse,
  SkillValidateInput,
  ListSubAgentTasksResponse,
  TestModelConfigInput,
  TestModelConfigResponse,
  ThreadDetailResponse,
  ToolInventoryResponse,
  UpdateModelConfigInput,
  UpdateSecretInput,
  SubAgentReviewInput,
  SubAgentReviewResponse,
  SubAgentTaskDetail,
  SubAgentTaskInput,
  UpsertMemoryInput,
  UploadDocumentInput,
  WorkspaceId,
} from './schemas/workspace'

const DEFAULT_AGENT_API_BASE_URL = 'http://localhost:8000'

export interface RecoverStaleJobsResponse {
  workspace_id: string
  recovered_count: number
  recovered_jobs?: JobSummary[]
}

export interface RebuildJobsIndexResponse {
  workspace_id: string
  rebuilt_count: number
  skipped_count: number
  skipped?: Array<Record<string, unknown>>
  index_object_key?: string
}

export class AgentApiError extends Error {
  readonly errorType?: string
  readonly retryable?: boolean
  readonly traceId?: string
  readonly status?: number

  constructor(message: string, options: Partial<AgentApiError> = {}) {
    super(message)
    this.name = 'AgentApiError'
    this.errorType = options.errorType
    this.retryable = options.retryable
    this.traceId = options.traceId
    this.status = options.status
  }
}

export function getAgentApiBaseUrl() {
  const configured = process.env.NEXT_PUBLIC_AGENT_API_BASE_URL
  return (configured || DEFAULT_AGENT_API_BASE_URL).replace(/\/$/, '')
}

export function buildAgentApiUrl(path: string) {
  return `${getAgentApiBaseUrl()}${path}`
}

function segment(value: string) {
  return encodeURIComponent(value)
}

function workspacePath(workspaceId: WorkspaceId) {
  return `/workspaces/${segment(workspaceId)}`
}

function buildQuery(params: object = {}) {
  const search = new URLSearchParams()

  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') {
      return
    }
    if (Array.isArray(value)) {
      value.forEach((item) => search.append(key, String(item)))
      return
    }
    search.set(key, String(value))
  })

  const query = search.toString()
  return query ? `?${query}` : ''
}

function jsonRequest(
  method: 'PATCH' | 'POST' | 'PUT',
  body?: unknown,
): RequestInit {
  return {
    body: body === undefined ? undefined : JSON.stringify(body),
    headers: { 'Content-Type': 'application/json' },
    method,
  }
}

async function parseErrorResponse(response: Response): Promise<AgentApiError> {
  try {
    const body = (await response.json()) as Partial<ErrorResponse>

    return new AgentApiError(
      body.message_for_user || `Request failed with status ${response.status}`,
      {
        errorType: body.error_type,
        retryable: body.retryable,
        traceId: body.trace_id,
        status: response.status,
      },
    )
  } catch {
    return new AgentApiError(`Request failed with status ${response.status}`, {
      status: response.status,
    })
  }
}

export async function requestAgentApi<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(buildAgentApiUrl(path), {
    ...init,
    headers: {
      Accept: 'application/json',
      ...init.headers,
    },
  })

  if (!response.ok) {
    throw await parseErrorResponse(response)
  }

  return (await response.json()) as T
}

export function getBootstrap() {
  return requestAgentApi<BootstrapResponse>('/bootstrap')
}

export function getP0Readiness(workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<P0ReadinessResponse>(
    `${workspacePath(workspaceId)}/readiness`,
  )
}

export function listThreads(workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<ListThreadsResponse>(
    `${workspacePath(workspaceId)}/threads`,
  )
}

export function createThread(
  input: CreateThreadInput = {},
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<ThreadDetailResponse>(
    `${workspacePath(workspaceId)}/threads`,
    jsonRequest('POST', input),
  )
}

export function getThread(
  threadId: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<ThreadDetailResponse>(
    `${workspacePath(workspaceId)}/threads/${segment(threadId)}`,
  )
}

export function patchThread(
  threadId: string,
  input: PatchThreadInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<ThreadDetailResponse>(
    `${workspacePath(workspaceId)}/threads/${segment(threadId)}`,
    jsonRequest('PATCH', input),
  )
}

export function listMessages(
  threadId: string,
  params: PageParams = {},
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<ListMessagesResponse>(
    `${workspacePath(workspaceId)}/threads/${segment(threadId)}/messages${buildQuery(params)}`,
  )
}

export function createRun(
  threadId: string,
  input: CreateRunInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<RunDetailResponse>(
    `${workspacePath(workspaceId)}/threads/${segment(threadId)}/runs`,
    jsonRequest('POST', input),
  )
}

export function getRun(runId: string, workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<RunDetailResponse>(
    `${workspacePath(workspaceId)}/runs/${segment(runId)}`,
  )
}

export function recoverStaleRuns(workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<RecoverStaleRunsResponse>(
    `${workspacePath(workspaceId)}/runs/recover-stale`,
    jsonRequest('POST'),
  )
}

export function cancelRun(runId: string, workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<CancelRunResponse>(
    `${workspacePath(workspaceId)}/runs/${segment(runId)}/cancel`,
    jsonRequest('POST'),
  )
}

export function approveRunApproval(
  runId: string,
  approvalId: string,
  input: RunApprovalDecisionInput = {},
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<RunApprovalDecisionResponse>(
    `${workspacePath(workspaceId)}/runs/${segment(runId)}/approvals/${segment(approvalId)}/approve`,
    jsonRequest('POST', input),
  )
}

export function rejectRunApproval(
  runId: string,
  approvalId: string,
  input: RunApprovalDecisionInput = {},
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<RunApprovalDecisionResponse>(
    `${workspacePath(workspaceId)}/runs/${segment(runId)}/approvals/${segment(approvalId)}/reject`,
    jsonRequest('POST', input),
  )
}

export function rollbackRunOperation(
  runId: string,
  operationId: string,
  input: RunOperationRollbackInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<RunOperationRollbackResponse>(
    `${workspacePath(workspaceId)}/runs/${segment(runId)}/operations/${segment(operationId)}/rollback`,
    jsonRequest('POST', input),
  )
}

export function listRunEvents(
  runId: string,
  params: PageParams = {},
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<ListRunEventsResponse>(
    `${workspacePath(workspaceId)}/runs/${segment(runId)}/events${buildQuery(params)}`,
  )
}

export function listSubAgentTasks(
  workspaceId: WorkspaceId = 'default',
  params: Pick<PageParams, 'parent_run_id' | 'status'> = {},
) {
  return requestAgentApi<ListSubAgentTasksResponse>(
    `${workspacePath(workspaceId)}/subagents/tasks${buildQuery(params)}`,
  )
}

export function createSubAgentTask(
  input: SubAgentTaskInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<SubAgentTaskDetail>(
    `${workspacePath(workspaceId)}/subagents/tasks`,
    jsonRequest('POST', input),
  )
}

export function getSubAgentTask(
  taskId: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<SubAgentTaskDetail>(
    `${workspacePath(workspaceId)}/subagents/tasks/${segment(taskId)}`,
  )
}

export function reviewSubAgentResult(
  taskId: string,
  input: SubAgentReviewInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<SubAgentReviewResponse>(
    `${workspacePath(workspaceId)}/subagents/tasks/${segment(taskId)}/review`,
    jsonRequest('POST', input),
  )
}

export function listJobs(
  workspaceId: WorkspaceId = 'default',
  params: PageParams = {},
) {
  return requestAgentApi<ListJobsResponse>(
    `${workspacePath(workspaceId)}/jobs${buildQuery(params)}`,
  )
}

export function createJob(input: CreateJobInput, workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<JobDetailResponse>(
    `${workspacePath(workspaceId)}/jobs`,
    jsonRequest('POST', input),
  )
}

export function listMemorySyncJobs(
  workspaceId: WorkspaceId = 'default',
  params: Omit<PageParams, 'job_type'> = {},
) {
  return listJobs(workspaceId, {
    ...params,
    job_type: 'memory_sync_job',
  })
}

export function createMemorySyncJob(
  input: MemorySyncJobInput = {},
  workspaceId: WorkspaceId = 'default',
) {
  return createJob(
    {
      idempotency_key: `memory-sync-${workspaceId}-${Date.now()}`,
      input,
      job_type: 'memory_sync_job',
      priority: 'normal',
      target_scope: {
        scope_type: 'memory_sync',
        sync_stream: `${workspaceId}:memory_sync`,
      },
      title: 'Memory sync',
    },
    workspaceId,
  )
}

export function getJobWorkerStatus(workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<JobWorkerStatusResponse>(
    `${workspacePath(workspaceId)}/jobs/worker/status`,
  )
}

export function startJobWorker(
  workspaceId: WorkspaceId = 'default',
  params: {
    job_type?: string[]
    max_jobs_per_tick?: number
    poll_interval_ms?: number
  } = {},
) {
  return requestAgentApi<JobWorkerStatusResponse>(
    `${workspacePath(workspaceId)}/jobs/worker/start${buildQuery(params)}`,
    jsonRequest('POST'),
  )
}

export function stopJobWorker(workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<JobWorkerStatusResponse>(
    `${workspacePath(workspaceId)}/jobs/worker/stop`,
    jsonRequest('POST'),
  )
}

export function processNextJob(
  workspaceId: WorkspaceId = 'default',
  params: { job_type?: string[] } = {},
) {
  return requestAgentApi<ProcessNextJobResponse>(
    `${workspacePath(workspaceId)}/jobs/process-next${buildQuery(params)}`,
    jsonRequest('POST'),
  )
}

export function recoverStaleJobs(
  workspaceId: WorkspaceId = 'default',
  params: { stale_after_seconds?: number } = {},
) {
  return requestAgentApi<RecoverStaleJobsResponse>(
    `${workspacePath(workspaceId)}/jobs/recover-stale${buildQuery(params)}`,
    jsonRequest('POST'),
  )
}

export function rebuildJobsIndex(workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<RebuildJobsIndexResponse>(
    `${workspacePath(workspaceId)}/jobs/rebuild-index`,
    jsonRequest('POST'),
  )
}

export function getJob(jobId: string, workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<JobDetailResponse>(
    `${workspacePath(workspaceId)}/jobs/${segment(jobId)}`,
  )
}

export function cancelJob(jobId: string, workspaceId: WorkspaceId = 'default') {
  return requestAgentApi(
    `${workspacePath(workspaceId)}/jobs/${segment(jobId)}/cancel`,
    jsonRequest('POST'),
  )
}

export function retryJob(
  jobId: string,
  input: unknown = {},
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<JobDetailResponse>(
    `${workspacePath(workspaceId)}/jobs/${segment(jobId)}/retry`,
    jsonRequest('POST', input),
  )
}

export function listJobEvents(
  jobId: string,
  params: PageParams = {},
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<ListJobEventsResponse>(
    `${workspacePath(workspaceId)}/jobs/${segment(jobId)}/events${buildQuery(params)}`,
  )
}

export function getJobEventsStreamUrl(
  jobId: string,
  params: PageParams = {},
  workspaceId: WorkspaceId = 'default',
) {
  return buildAgentApiUrl(
    `${workspacePath(workspaceId)}/jobs/${segment(jobId)}/events/stream${buildQuery(params)}`,
  )
}

export function getRunEventsStreamUrl(
  runId: string,
  params: PageParams = {},
  workspaceId: WorkspaceId = 'default',
) {
  return buildAgentApiUrl(
    `${workspacePath(workspaceId)}/runs/${segment(runId)}/events/stream${buildQuery(params)}`,
  )
}

export function getSystemLogSummary(
  workspaceId: WorkspaceId = 'default',
  params: PageParams = {},
) {
  return requestAgentApi<LogSummaryResponse>(
    `${workspacePath(workspaceId)}/logs/system/summary${buildQuery(params)}`,
  )
}

export function getSystemLogs(
  workspaceId: WorkspaceId = 'default',
  stream: 'full' | 'errors' = 'full',
  params: PageParams = {},
) {
  return requestAgentApi<LogQueryResponse>(
    `${workspacePath(workspaceId)}/logs/system/${stream}${buildQuery(params)}`,
  )
}

export function getLogTail(
  workspaceId: WorkspaceId = 'default',
  params: Pick<PageParams, 'limit'> = {},
) {
  return requestAgentApi<LogQueryResponse>(
    `${workspacePath(workspaceId)}/logs/tail${buildQuery(params)}`,
  )
}

export function getLogArtifact(
  objectKey: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<LogArtifactResponse>(
    `${workspacePath(workspaceId)}/logs/artifacts${buildQuery({ object_key: objectKey })}`,
  )
}

export function getComponentLogs(
  component: string,
  params: PageParams = {},
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<LogQueryResponse>(
    `${workspacePath(workspaceId)}/logs/components/${segment(component)}${buildQuery(params)}`,
  )
}

export function createDiagnosticBundle(
  input: DiagnosticBundleInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<DiagnosticBundleResponse>(
    `${workspacePath(workspaceId)}/logs/diagnostic-bundles`,
    jsonRequest('POST', input),
  )
}

export function createLogArchiveJob(
  input: LogArchiveJobInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<LogArchiveJobResponse>(
    `${workspacePath(workspaceId)}/logs/archive-jobs`,
    jsonRequest('POST', input),
  )
}

export function getDatabaseConfig(workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<DatabaseConfigResponse>(
    `${workspacePath(workspaceId)}/database/config`,
  )
}

export function updateDatabaseConfig(
  targets: DatabaseTargetConfig[],
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<DatabaseConfigResponse>(
    `${workspacePath(workspaceId)}/database/config`,
    jsonRequest('PUT', { targets }),
  )
}

export function getDatabaseHealth(workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<DatabaseHealthSnapshotResponse>(
    `${workspacePath(workspaceId)}/database/health`,
  )
}

export function checkDatabaseHealth(workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<DatabaseHealthSnapshotResponse>(
    `${workspacePath(workspaceId)}/database/health/check`,
    jsonRequest('POST'),
  )
}

export function listModelConfigs(workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<ListModelConfigsResponse>(
    `${workspacePath(workspaceId)}/model-configs`,
  )
}

export function getModelConfig(
  configId: ModelConfigId,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<ModelConfigResponse>(
    `${workspacePath(workspaceId)}/model-configs/${segment(configId)}`,
  )
}

export function updateModelConfig(
  configId: ModelConfigId,
  input: UpdateModelConfigInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<ModelConfigResponse>(
    `${workspacePath(workspaceId)}/model-configs/${segment(configId)}`,
    jsonRequest('PUT', input),
  )
}

export function testModelConfig(
  configId: ModelConfigId,
  input: TestModelConfigInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<TestModelConfigResponse>(
    `${workspacePath(workspaceId)}/model-configs/${segment(configId)}/test`,
    jsonRequest('POST', input),
  )
}

export function listSecrets(workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<ListSecretsResponse>(
    `${workspacePath(workspaceId)}/secrets`,
  )
}

export function createSecret(
  input: CreateSecretInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<SecretSummary>(
    `${workspacePath(workspaceId)}/secrets`,
    jsonRequest('POST', input),
  )
}

export function getSecret(
  secretId: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<SecretSummary>(
    `${workspacePath(workspaceId)}/secrets/${segment(secretId)}`,
  )
}

export function updateSecret(
  secretId: string,
  input: UpdateSecretInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<SecretSummary>(
    `${workspacePath(workspaceId)}/secrets/${segment(secretId)}`,
    jsonRequest('PATCH', input),
  )
}

export function disableSecret(
  secretId: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<SecretSummary>(
    `${workspacePath(workspaceId)}/secrets/${segment(secretId)}/disable`,
    jsonRequest('POST'),
  )
}

export function rotateSecret(
  secretId: string,
  input: RotateSecretInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<SecretSummary>(
    `${workspacePath(workspaceId)}/secrets/${segment(secretId)}/rotate`,
    jsonRequest('POST', input),
  )
}

export function deleteSecret(
  secretId: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<SecretSummary>(
    `${workspacePath(workspaceId)}/secrets/${segment(secretId)}`,
    { method: 'DELETE' },
  )
}

export function getSecretReferences(
  secretId: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<SecretReferencesResponse>(
    `${workspacePath(workspaceId)}/secrets/${segment(secretId)}/references`,
  )
}

export function listMemories(
  workspaceId: WorkspaceId = 'default',
  params: PageParams = {},
) {
  return requestAgentApi<ListMemoriesResponse>(
    `${workspacePath(workspaceId)}/memories${buildQuery(params)}`,
  )
}

export function getMemorySyncState(workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<MemorySyncStateResponse>(
    `${workspacePath(workspaceId)}/memories/sync-state`,
  )
}

export function createMemory(
  input: UpsertMemoryInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<MemoryDetailResponse>(
    `${workspacePath(workspaceId)}/memories`,
    jsonRequest('POST', input),
  )
}

export function searchMemories(
  query: string,
  memoryTypes: string[] = [],
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<MemorySearchResponse>(
    `${workspacePath(workspaceId)}/memories/search${buildQuery({
      memory_type: memoryTypes,
      query,
    })}`,
  )
}

export function getMemory(
  memoryId: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<MemoryDetailResponse>(
    `${workspacePath(workspaceId)}/memories/${segment(memoryId)}`,
  )
}

export function patchMemory(
  memoryId: string,
  input: PatchMemoryInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<MemoryDetailResponse>(
    `${workspacePath(workspaceId)}/memories/${segment(memoryId)}`,
    jsonRequest('PATCH', input),
  )
}

export function approveMemory(
  memoryId: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<MemoryDetailResponse>(
    `${workspacePath(workspaceId)}/memories/${segment(memoryId)}/approve`,
    jsonRequest('POST'),
  )
}

export function rejectMemory(
  memoryId: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<MemoryDetailResponse>(
    `${workspacePath(workspaceId)}/memories/${segment(memoryId)}/reject`,
    jsonRequest('POST'),
  )
}

export function deleteMemory(
  memoryId: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<MemoryDetailResponse>(
    `${workspacePath(workspaceId)}/memories/${segment(memoryId)}`,
    {
      method: 'DELETE',
    },
  )
}

export function createMemorySnapshot(
  threadId: string,
  query?: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<MemorySnapshotResponse>(
    `${workspacePath(workspaceId)}/memory-snapshots${buildQuery({
      query,
      thread_id: threadId,
    })}`,
    jsonRequest('POST'),
  )
}

export function getMemorySnapshot(
  snapshotId: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<MemorySnapshotResponse>(
    `${workspacePath(workspaceId)}/memory-snapshots/${segment(snapshotId)}`,
  )
}

export function listSkills(
  workspaceId: WorkspaceId = 'default',
  params: PageParams = {},
) {
  return requestAgentApi<ListSkillsResponse>(
    `${workspacePath(workspaceId)}/skills${buildQuery(params)}`,
  )
}

export function searchSkills(
  query: string,
  topK = 5,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<SkillSearchResponse>(
    `${workspacePath(workspaceId)}/skills/search${buildQuery({ query, top_k: topK })}`,
  )
}

export function proposeSkill(
  input: SkillProposalInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<SkillProposalResponse>(
    `${workspacePath(workspaceId)}/skill-proposals`,
    jsonRequest('POST', input),
  )
}

export function createSkillFromProposal(
  input: SkillCreateFromProposalInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<SkillDetailResponse>(
    `${workspacePath(workspaceId)}/skills/from-proposal`,
    jsonRequest('POST', input),
  )
}

export function getSkill(
  skillId: string,
  version?: string,
  workspaceId: WorkspaceId = 'default',
) {
  const base = `${workspacePath(workspaceId)}/skills/${segment(skillId)}`
  const path = version ? `${base}/versions/${segment(version)}` : base
  return requestAgentApi<SkillDetailResponse>(path)
}

export function activateSkill(
  skillId: string,
  input: SkillActivateInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<SkillActivationResponse>(
    `${workspacePath(workspaceId)}/skills/${segment(skillId)}/activate`,
    jsonRequest('POST', input),
  )
}

export function disableSkill(
  skillId: string,
  input: SkillDisableInput = {},
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<SkillDetailResponse>(
    `${workspacePath(workspaceId)}/skills/${segment(skillId)}/disable`,
    jsonRequest('POST', input),
  )
}

export function validateSkill(
  skillId: string,
  input: SkillValidateInput = {},
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<SkillDetailResponse>(
    `${workspacePath(workspaceId)}/skills/${segment(skillId)}/validate`,
    jsonRequest('POST', input),
  )
}

export function listMcpServers(workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<McpServersResponse>(
    `${workspacePath(workspaceId)}/mcp/servers`,
  )
}

export function getMcpServer(
  serverName: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<McpServerDetailResponse>(
    `${workspacePath(workspaceId)}/mcp/servers/${segment(serverName)}`,
  )
}

export function saveMcpServer(
  serverName: string,
  input: McpServerConfigInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<McpServerDetailResponse>(
    `${workspacePath(workspaceId)}/mcp/servers/${segment(serverName)}`,
    jsonRequest('PUT', input),
  )
}

export function listMcpTools(
  serverName: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<McpToolsResponse>(
    `${workspacePath(workspaceId)}/mcp/servers/${segment(serverName)}/tools`,
  )
}

export function getMcpServerHealth(
  serverName: string,
  workspaceId: WorkspaceId = 'default',
  params: { live_probe?: boolean } = {},
) {
  return requestAgentApi<McpServerHealthResponse>(
    `${workspacePath(workspaceId)}/mcp/servers/${segment(serverName)}/health${buildQuery(params)}`,
  )
}

export function refreshMcpServer(
  serverName: string,
  input: McpRefreshInput = {},
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<McpRefreshResponse>(
    `${workspacePath(workspaceId)}/mcp/servers/${segment(serverName)}/refresh`,
    jsonRequest('POST', input),
  )
}

export function reconnectMcpServer(
  serverName: string,
  input: McpReconnectInput = {},
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<McpReconnectResponse>(
    `${workspacePath(workspaceId)}/mcp/servers/${segment(serverName)}/reconnect`,
    jsonRequest('POST', input),
  )
}

export function setMcpToolPolicy(
  input: McpToolPolicyInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<McpToolPolicyResponse>(
    `${workspacePath(workspaceId)}/mcp/tools/policy`,
    jsonRequest('POST', input),
  )
}

export function getToolInventory(workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<ToolInventoryResponse>(
    `${workspacePath(workspaceId)}/tools/inventory`,
  )
}

export function listKnowledgeBases(workspaceId: WorkspaceId = 'default') {
  return requestAgentApi<ListKnowledgeBasesResponse>(
    `${workspacePath(workspaceId)}/knowledge-bases`,
  )
}

export function createKnowledgeBase(
  input: CreateKnowledgeBaseInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<KnowledgeBaseResponse>(
    `${workspacePath(workspaceId)}/knowledge-bases`,
    jsonRequest('POST', input),
  )
}

export function getKnowledgeBase(
  knowledgeBaseId: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<KnowledgeBaseResponse>(
    `${workspacePath(workspaceId)}/knowledge-bases/${segment(knowledgeBaseId)}`,
  )
}

export function listDocuments(
  knowledgeBaseId = 'kb_default',
  params: PageParams = {},
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<ListDocumentsResponse>(
    `${workspacePath(workspaceId)}/knowledge-bases/${segment(knowledgeBaseId)}/documents${buildQuery(params)}`,
  )
}

export function uploadDocument(
  input: UploadDocumentInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<DocumentSummary>(
    `${workspacePath(workspaceId)}/documents/upload`,
    jsonRequest('POST', input),
  )
}

export function uploadDocumentToKnowledgeBase(
  knowledgeBaseId: string,
  input: UploadDocumentInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<DocumentSummary>(
    `${workspacePath(workspaceId)}/knowledge-bases/${segment(knowledgeBaseId)}/documents`,
    jsonRequest('POST', input),
  )
}

export function uploadDocumentFileToKnowledgeBase(
  knowledgeBaseId: string,
  file: File,
  workspaceId: WorkspaceId = 'default',
  input: { idempotency_key?: string; source_file_name?: string } = {},
) {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('source_file_name', input.source_file_name || file.name)
  if (input.idempotency_key) {
    formData.append('idempotency_key', input.idempotency_key)
  }
  return requestAgentApi<DocumentSummary>(
    `${workspacePath(workspaceId)}/knowledge-bases/${segment(knowledgeBaseId)}/documents`,
    {
      body: formData,
      method: 'POST',
    },
  )
}

export function getDocument(
  knowledgeBaseId: string,
  documentId: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<DocumentSummary>(
    `${workspacePath(workspaceId)}/knowledge-bases/${segment(knowledgeBaseId)}/documents/${segment(documentId)}`,
  )
}

export function listDocumentChunks(
  knowledgeBaseId: string,
  documentId: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<ListChunksResponse>(
    `${workspacePath(workspaceId)}/knowledge-bases/${segment(knowledgeBaseId)}/documents/${segment(documentId)}/chunks`,
  )
}

export function getChunk(
  chunkId: string,
  params: PageParams & {
    doc_id?: string
    knowledge_base_id?: string
    max_chars?: number
  } = {},
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<ChunkResponse>(
    `${workspacePath(workspaceId)}/chunks/${segment(chunkId)}${buildQuery(params)}`,
  )
}

export function getActiveEmbedding(
  knowledgeBaseId = 'kb_default',
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<ActiveEmbeddingResponse>(
    `${workspacePath(workspaceId)}/knowledge-bases/${segment(knowledgeBaseId)}/active-embedding`,
  )
}

export function createEmbeddingReindexJob(
  knowledgeBaseId: string,
  input: EmbeddingReindexInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<EmbeddingReindexResponse>(
    `${workspacePath(workspaceId)}/knowledge-bases/${segment(knowledgeBaseId)}/embedding/reindex`,
    jsonRequest('POST', input),
  )
}

export function getGraphSchema(
  knowledgeBaseId = 'kb_default',
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<GraphSchemaResponse>(
    `${workspacePath(workspaceId)}/graph/schema${buildQuery({
      knowledge_base_id: knowledgeBaseId,
    })}`,
  )
}

export function searchGraphEntities(
  input: GraphEntitySearchInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<GraphEntitySearchResponse>(
    `${workspacePath(workspaceId)}/graph/entities/search`,
    jsonRequest('POST', input),
  )
}

export function expandGraphEntity(
  entityId: string,
  input: GraphExpandEntityInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<GraphExpandEntityResponse>(
    `${workspacePath(workspaceId)}/graph/entities/${segment(entityId)}/expand`,
    jsonRequest('POST', input),
  )
}

export function findGraphPaths(
  input: GraphFindPathsInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<GraphFindPathsResponse>(
    `${workspacePath(workspaceId)}/graph/paths/find`,
    jsonRequest('POST', input),
  )
}

export function findGraphRelationship(
  input: GraphFindRelationshipInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<GraphFindRelationshipResponse>(
    `${workspacePath(workspaceId)}/graph/relationships/find`,
    jsonRequest('POST', input),
  )
}

export function getGraphEvidence(
  input: GraphEvidenceInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<GraphEvidenceResponse>(
    `${workspacePath(workspaceId)}/graph/evidence`,
    jsonRequest('POST', input),
  )
}

export function searchGraphRag(
  input: GraphRagSearchInput,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<GraphRagSearchResponse>(
    `${workspacePath(workspaceId)}/graph/search`,
    jsonRequest('POST', input),
  )
}

export function buildDocumentGraph(
  knowledgeBaseId: string,
  documentId: string,
  workspaceId: WorkspaceId = 'default',
) {
  return requestAgentApi<GraphBuildResponse>(
    `${workspacePath(workspaceId)}/graph/build/${segment(knowledgeBaseId)}/documents/${segment(documentId)}`,
    jsonRequest('POST'),
  )
}
