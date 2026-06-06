'use client'

import {
  Alert,
  Button,
  Card,
  Dropdown,
  Empty,
  Input,
  List,
  Modal,
  Skeleton,
  Space,
  Tag,
  Typography,
} from 'antd'
import type { MenuProps } from 'antd'
import { createStyles } from 'antd-style'
import { useCallback, useEffect, useRef, useState } from 'react'

import {
  approveRunApproval,
  cancelRun,
  createRun,
  createThread,
  getRunEventsStreamUrl,
  listMessages,
  listRunEvents,
  listThreads,
  patchThread,
  recoverStaleRuns,
  rejectRunApproval,
  rollbackRunOperation,
} from '@/api/agentApiClient'
import { newClientRequestId } from '@/api/clientRequestId'
import type {
  ConversationMessage,
  PatchThreadInput,
  RunApprovalDecisionResponse,
  RunDetailResponse,
  RunEvent,
  RunOperationRollbackResponse,
  RunStatus,
  ThreadSummary,
  WorkspaceId,
} from '@/api/schemas/workspace'

const { Text } = Typography
const { TextArea } = Input

const RUN_EVENT_TYPES = [
  'run_started',
  'run_recovery_started',
  'user_message',
  'model_call_started',
  'model_call_completed',
  'model_call_failed',
  'assistant_delta',
  'model_usage_delta',
  'model_tool_call_delta',
  'memory_snapshot_created',
  'context_compaction_created',
  'tool_call_started',
  'tool_call_completed',
  'tool_call_failed',
  'approval_requested',
  'approval_approved',
  'approval_rejected',
  'approval_execution_started',
  'approval_execution_completed',
  'approval_execution_failed',
  'operation_rolled_back',
  'skill_patch_staged',
  'skill_patch_committed',
  'skill_proposal_created',
  'skill_activated',
  'skill_entrypoint_completed',
  'skill_entrypoint_failed',
  'skill_entrypoint_approval_required',
  'skill_entrypoint_approval_execution_started',
  'skill_entrypoint_approval_execution_completed',
  'skill_entrypoint_approval_execution_failed',
  'subagent_task_created',
  'subagent_task_queued',
  'subagent_task_completed',
  'subagent_task_failed',
  'subagent_result_reviewed',
  'diagnostic_bundle_created',
  'assistant_message',
  'run_completed',
  'run_waiting_approval',
  'run_failed',
  'run_cancelled',
  'stream_closed',
]

const RUN_CLOSED_STATUSES: RunStatus[] = [
  'completed',
  'failed',
  'cancelled',
  'waiting_approval',
]

const useStyles = createStyles(({ css, token }) => ({
  chatBody: css`
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
  `,
  composer: css`
    border-top: 1px solid ${token.colorBorderSecondary};
    flex: 0 0 auto;
    padding-top: 12px;
  `,
  eventBody: css`
    display: flex;
    flex-direction: column;
    gap: 12px;
    height: 100%;
    min-height: 0;
  `,
  eventScroll: css`
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding-right: 4px;
  `,
  grid: css`
    display: grid;
    gap: 16px;
    grid-template-columns: 280px minmax(0, 1fr) 300px;
    height: calc(100vh - 142px);
    min-height: 560px;

    @media (max-width: 1180px) {
      grid-template-columns: 240px minmax(0, 1fr);
    }

    @media (max-width: 820px) {
      height: auto;
      grid-template-columns: 1fr;
      min-height: 0;
    }
  `,
  messageBody: css`
    white-space: pre-wrap;
  `,
  messageScroll: css`
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding-right: 4px;
  `,
  payloadBox: css`
    background: ${token.colorFillQuaternary};
    border: 1px solid ${token.colorBorderSecondary};
    border-radius: 6px;
    max-height: 180px;
    max-width: 100%;
    overflow: auto;
    padding: 8px;
    white-space: pre-wrap;
    word-break: break-word;
  `,
  panelCard: css`
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;

    > .ant-card-body {
      flex: 1;
      min-height: 0;
      overflow: hidden;
    }

    @media (max-width: 820px) {
      height: 68vh;
      min-height: 420px;
    }
  `,
  sidePanel: css`
    @media (max-width: 1180px) {
      grid-column: 1 / -1;
    }
  `,
  threadPanel: css`
    > .ant-card-body {
      overflow-y: auto;
      padding: 0;
    }
  `,
  threadItem: css`
    cursor: pointer;
    height: 76px;
    overflow: hidden;
    padding: 10px 12px !important;

    .ant-list-item-meta {
      min-width: 0;
    }

    .ant-list-item-meta-title,
    .ant-list-item-meta-description {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .ant-list-item-action {
      margin-inline-start: 8px;
    }
  `,
  threadTitle: css`
    min-width: 0;
    word-break: break-word;
  `,
}))

interface ChatPanelProps {
  onRuntimeContextChange?: (context: ChatRuntimeContext) => void
  workspaceId: WorkspaceId
}

export interface ChatRuntimeContext {
  run_id: string | null
  run_status: RunStatus | null
  thread_id: string | null
}

function roleColor(role: ConversationMessage['role']) {
  if (role === 'assistant') {
    return 'blue'
  }
  if (role === 'tool') {
    return 'purple'
  }
  if (role === 'system') {
    return 'gold'
  }
  return 'green'
}

function parseSseEvent(event: MessageEvent<string>): RunEvent | null {
  try {
    const parsed = JSON.parse(event.data) as RunEvent
    return parsed.event_id ? parsed : null
  } catch {
    return null
  }
}

function approvalIdFromEvent(event: RunEvent) {
  const approvalId = event.payload?.approval_id
  return typeof approvalId === 'string' ? approvalId : null
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function recordListValue(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.flatMap((item) => {
        const record = recordValue(item)
        return record ? [record] : []
      })
    : []
}

function stringValue(value: unknown) {
  return typeof value === 'string' && value ? value : null
}

function approvalArtifacts(
  event: RunEvent,
  decision?: RunApprovalDecisionResponse,
) {
  return {
    ...(recordValue(event.payload?.artifacts) ?? {}),
    ...(recordValue(decision?.artifacts) ?? {}),
  }
}

function approvalArtifactValue(
  event: RunEvent,
  key: string,
  decision?: RunApprovalDecisionResponse,
) {
  const value =
    approvalArtifacts(event, decision)[key] ||
    event.payload?.[key] ||
    decision?.artifacts?.[key]
  return typeof value === 'string' ? value : null
}

function operationDataFromApproval(decision?: RunApprovalDecisionResponse) {
  const skillResult = recordValue(decision?.artifacts?.['approved_skill_result'])
  return recordValue(skillResult?.data) ?? {}
}

function operationIdFromApproval(
  event: RunEvent,
  decision?: RunApprovalDecisionResponse,
) {
  const data = operationDataFromApproval(decision)
  return stringValue(data.operation_id) || stringValue(event.payload?.operation_id)
}

function approvalKind(event: RunEvent, decision?: RunApprovalDecisionResponse) {
  const explicitKind = stringValue(event.payload?.approval_kind)
  if (explicitKind) {
    return explicitKind
  }
  if (
    event.type === 'skill_entrypoint_approval_required' ||
    approvalArtifactValue(event, 'diff_object_key', decision)
  ) {
    return 'skill_script_staged_patch'
  }
  return 'approval'
}

function isApprovalRequestEvent(event: RunEvent) {
  return (
    (event.type === 'skill_entrypoint_approval_required' ||
      event.type === 'approval_requested') &&
    Boolean(approvalIdFromEvent(event))
  )
}

function isApprovalOutcomeEvent(event: RunEvent) {
  return (
    event.type === 'approval_approved' ||
    event.type === 'approval_rejected' ||
    event.type === 'approval_execution_started' ||
    event.type === 'approval_execution_completed' ||
    event.type === 'approval_execution_failed' ||
    event.type === 'operation_rolled_back' ||
    event.type === 'skill_patch_staged' ||
    event.type === 'skill_patch_committed' ||
    event.type === 'skill_entrypoint_approval_execution_started' ||
    event.type === 'skill_entrypoint_approval_execution_completed' ||
    event.type === 'skill_entrypoint_approval_execution_failed'
  )
}

function isApprovalResolved(events: RunEvent[], approvalId: string) {
  return events.some(
    (event) =>
      (event.type === 'approval_approved' ||
        event.type === 'approval_rejected') &&
      event.payload?.approval_id === approvalId,
  )
}

function isOperationAlreadyRolledBack(
  events: RunEvent[],
  operationId: string | null,
) {
  return Boolean(operationId) && events.some((event) => {
    if (event.type !== 'operation_rolled_back') {
      return false
    }
    return (
      event.payload?.operation_id === operationId ||
      event.payload?.target_operation_id === operationId
    )
  })
}

function closedRunStatusFromEvent(event: RunEvent): RunStatus {
  const status = event.payload?.run_status || event.payload?.status
  return isClosedRunStatus(status) ? status : 'completed'
}

function isClosedRunStatus(status: unknown): status is RunStatus {
  return (
    typeof status === 'string' &&
    RUN_CLOSED_STATUSES.includes(status as RunStatus)
  )
}

function effectiveThreadRunStatus(
  thread: ThreadSummary,
  overrides: Record<string, RunStatus>,
): RunStatus | null | undefined {
  return thread.current_run_id
    ? overrides[thread.current_run_id] || thread.current_run_status
    : thread.current_run_status
}

function isThreadBlocked(
  thread: ThreadSummary,
  overrides: Record<string, RunStatus>,
) {
  const status = effectiveThreadRunStatus(thread, overrides)
  return status === 'running' || status === 'waiting_approval'
}

function isStreamingAssistantMessage(message: ConversationMessage) {
  return message.role === 'assistant' && message.message_id.startsWith('stream-')
}

function hasPersistedAssistantForRun(
  messages: ConversationMessage[],
  runId: string | null | undefined,
) {
  return messages.some(
    (message) =>
      message.role === 'assistant' &&
      message.run_id === runId &&
      !isStreamingAssistantMessage(message) &&
      message.content.trim().length > 0,
  )
}

function mergeMessagesWithStreamingDrafts(
  persistedMessages: ConversationMessage[],
  currentMessages: ConversationMessage[],
  threadId: string,
) {
  const draftsToKeep = currentMessages.filter(
    (message) =>
      message.thread_id === threadId &&
      isStreamingAssistantMessage(message) &&
      !hasPersistedAssistantForRun(persistedMessages, message.run_id),
  )
  return [...persistedMessages, ...draftsToKeep]
}

function sortActiveThreads(threads: ThreadSummary[]) {
  return [...threads]
    .filter((thread) => thread.status === 'active')
    .sort((left, right) => {
      if (left.pinned !== right.pinned) {
        return left.pinned ? -1 : 1
      }
      return (
        new Date(right.updated_at).getTime() -
        new Date(left.updated_at).getTime()
      )
    })
}

interface RunApprovalCardProps {
  decision?: RunApprovalDecisionResponse
  event: RunEvent
  onResolve: (event: RunEvent, decision: 'approve' | 'reject') => Promise<void>
  onRollback: (event: RunEvent, decision?: RunApprovalDecisionResponse) => Promise<void>
  resolvingApprovalId: string | null
  rollingBackOperationId: string | null
  rollbackResultsByOperationId: Record<string, RunOperationRollbackResponse>
  runEvents: RunEvent[]
}

function RunApprovalCard({
  decision,
  event,
  onRollback,
  onResolve,
  resolvingApprovalId,
  rollingBackOperationId,
  rollbackResultsByOperationId,
  runEvents,
}: RunApprovalCardProps) {
  const approvalId = approvalIdFromEvent(event)
  if (!approvalId) {
    return null
  }
  const resolved = isApprovalResolved(runEvents, approvalId)
  const kind = approvalKind(event, decision)
  const status =
    stringValue(event.payload?.status) || decision?.status || 'waiting_approval'
  const skillRunId =
    stringValue(event.payload?.skill_run_id) || decision?.skill_run_id || null
  const entrypoint =
    stringValue(event.payload?.entrypoint_tool_name) ||
    stringValue(event.payload?.entrypoint) ||
    null
  const operationId = operationIdFromApproval(event, decision)

  return (
    <Card
      size="small"
      title={
        <Space wrap>
          <span>
            {kind === 'skill_script_staged_patch'
              ? 'Skill staged patch approval'
              : 'Approval required'}
          </span>
          <Tag color={resolved ? 'green' : 'orange'}>{status}</Tag>
        </Space>
      }
    >
      <Space direction="vertical" size={6} style={{ width: '100%' }}>
        <Text copyable code>
          {approvalId}
        </Text>
        <Space wrap size={4}>
          <Tag color="blue">{kind}</Tag>
          {skillRunId && <Tag>{skillRunId}</Tag>}
          {entrypoint && <Tag>{entrypoint}</Tag>}
        </Space>
        <ApprovalArtifacts event={event} decision={decision} />
        <ApprovalOperationSummary
          decision={decision}
          event={event}
          onRollback={onRollback}
          rollbackResult={
            rollbackResultsByOperationId[operationId || '']
          }
          rolledBack={isOperationAlreadyRolledBack(runEvents, operationId)}
          rollingBackOperationId={rollingBackOperationId}
        />
        {decision && (
          <Alert
            message={
              decision.decision === 'approved'
                ? 'Approval approved'
                : 'Approval rejected'
            }
            description={`Status: ${decision.status}; run: ${decision.run_status}`}
            showIcon
            type={decision.decision === 'approved' ? 'success' : 'warning'}
          />
        )}
        <Space>
          <Button
            disabled={resolved}
            loading={resolvingApprovalId === approvalId}
            onClick={() => void onResolve(event, 'approve')}
            size="small"
            type="primary"
          >
            Approve
          </Button>
          <Button
            danger
            disabled={resolved}
            loading={resolvingApprovalId === approvalId}
            onClick={() => void onResolve(event, 'reject')}
            size="small"
          >
            Reject
          </Button>
        </Space>
      </Space>
    </Card>
  )
}

function RunApprovalArtifactSummary({
  decision,
  event,
  onRollback,
  payloadClassName,
  rollingBackOperationId,
  rollbackResultsByOperationId,
  runEvents,
}: {
  decision?: RunApprovalDecisionResponse
  event: RunEvent
  onRollback: (event: RunEvent, decision?: RunApprovalDecisionResponse) => Promise<void>
  payloadClassName: string
  rollingBackOperationId: string | null
  rollbackResultsByOperationId: Record<string, RunOperationRollbackResponse>
  runEvents: RunEvent[]
}) {
  const approvalId = approvalIdFromEvent(event)
  const operationId = operationIdFromApproval(event, decision)
  const status =
    stringValue(event.payload?.status) ||
    stringValue(event.payload?.decision) ||
    decision?.status
  const hasArtifacts =
    approvalArtifactRows(event, decision).length > 0 ||
    Boolean(approvalId) ||
    Boolean(status)

  if (!hasArtifacts) {
    return null
  }

  return (
    <Card size="small" title={approvalOutcomeTitle(event.type)}>
      <Space direction="vertical" size={6} style={{ width: '100%' }}>
        <Space wrap size={4}>
          {approvalId && <Tag>{approvalId}</Tag>}
          {status && (
            <Tag color={event.type.includes('failed') ? 'red' : 'green'}>
              {status}
            </Tag>
          )}
          {stringValue(event.payload?.tool_name) && (
            <Tag>{stringValue(event.payload?.tool_name)}</Tag>
          )}
        </Space>
        <ApprovalArtifacts event={event} decision={decision} />
        <ApprovalOperationSummary
          decision={decision}
          event={event}
          onRollback={onRollback}
          rollbackResult={rollbackResultsByOperationId[operationId || '']}
          rolledBack={isOperationAlreadyRolledBack(runEvents, operationId)}
          rollingBackOperationId={rollingBackOperationId}
        />
        {recordValue(decision?.artifacts?.['approved_tool_result']) && (
          <pre className={payloadClassName}>
            {JSON.stringify(
              decision?.artifacts?.['approved_tool_result'],
              null,
              2,
            )}
          </pre>
        )}
        {recordValue(decision?.artifacts?.['approved_skill_result']) && (
          <pre className={payloadClassName}>
            {JSON.stringify(
              decision?.artifacts?.['approved_skill_result'],
              null,
              2,
            )}
          </pre>
        )}
      </Space>
    </Card>
  )
}

function approvalOutcomeTitle(type: string) {
  if (type === 'approval_approved') {
    return 'Approval accepted'
  }
  if (type === 'approval_rejected') {
    return 'Approval rejected'
  }
  if (
    type === 'approval_execution_started' ||
    type === 'skill_entrypoint_approval_execution_started'
  ) {
    return 'Post-approval execution started'
  }
  if (
    type === 'approval_execution_completed' ||
    type === 'skill_patch_committed' ||
    type === 'skill_entrypoint_approval_execution_completed'
  ) {
    return 'Post-approval execution completed'
  }
  if (
    type === 'approval_execution_failed' ||
    type === 'skill_entrypoint_approval_execution_failed'
  ) {
    return 'Post-approval execution failed'
  }
  if (type === 'operation_rolled_back') {
    return 'Operation rolled back'
  }
  return 'Approval artifact'
}

function ApprovalArtifacts({
  decision,
  event,
}: {
  decision?: RunApprovalDecisionResponse
  event: RunEvent
}) {
  const rows = approvalArtifactRows(event, decision)
  if (rows.length === 0) {
    return null
  }

  return (
    <Space direction="vertical" size={2} style={{ width: '100%' }}>
      {rows.map(([label, value]) => (
        <Text key={label} type="secondary">
          {label}: {value}
        </Text>
      ))}
    </Space>
  )
}

function ApprovalOperationSummary({
  decision,
  event,
  onRollback,
  rollbackResult,
  rolledBack: rolledBackFromEvents,
  rollingBackOperationId,
}: {
  decision?: RunApprovalDecisionResponse
  event: RunEvent
  onRollback: (event: RunEvent, decision?: RunApprovalDecisionResponse) => Promise<void>
  rollbackResult?: RunOperationRollbackResponse
  rolledBack: boolean
  rollingBackOperationId: string | null
}) {
  const data = operationDataFromApproval(decision)
  const operationId =
    stringValue(data?.operation_id) || stringValue(event.payload?.operation_id)
  const operationStatus =
    stringValue(data?.operation_status) ||
    stringValue(event.payload?.operation_status)
  const workspaceCommitStatus =
    stringValue(data?.workspace_commit_status) ||
    stringValue(event.payload?.workspace_commit_status)
  const rollbackToken =
    stringValue(data?.rollback_token) ||
    stringValue(event.payload?.rollback_token)
  const committedFiles = recordListValue(data?.committed_files)
  const rollbackResultFiles = recordListValue(rollbackResult?.restored_files)
  const eventRestoredFiles = recordListValue(event.payload?.restored_files)
  const restoredFiles =
    rollbackResultFiles.length > 0 ? rollbackResultFiles : eventRestoredFiles
  const rolledBack =
    rolledBackFromEvents ||
    Boolean(rollbackResult) ||
    event.type === 'operation_rolled_back' ||
    workspaceCommitStatus === 'rolled_back'
  const canRollback =
    Boolean(operationId) &&
    Boolean(rollbackToken) &&
    workspaceCommitStatus === 'committed' &&
    !rolledBack

  if (
    !operationId &&
    !operationStatus &&
    !workspaceCommitStatus &&
    !rollbackToken &&
    committedFiles.length === 0 &&
    restoredFiles.length === 0
  ) {
    return null
  }

  return (
    <Space direction="vertical" size={6} style={{ width: '100%' }}>
      <Space wrap size={4}>
        {operationId && <Tag color="purple">op {operationId}</Tag>}
        {operationStatus && (
          <Tag color={operationStatusColor(operationStatus)}>
            {operationStatus}
          </Tag>
        )}
        {workspaceCommitStatus && (
          <Tag color={operationStatusColor(workspaceCommitStatus)}>
            {workspaceCommitStatus}
          </Tag>
        )}
        {rollbackToken && <Tag>rollback {rollbackToken}</Tag>}
        {rolledBack && <Tag color="green">rolled_back</Tag>}
      </Space>
      {canRollback && (
        <Button
          danger
          loading={rollingBackOperationId === operationId}
          onClick={() => void onRollback(event, decision)}
          size="small"
        >
          Rollback workspace files
        </Button>
      )}
      {rollbackResult && (
        <Alert
          message="Workspace files rolled back"
          description={`Event: ${rollbackResult.event_id || 'recorded'}`}
          showIcon
          type="success"
        />
      )}
      {committedFiles.length > 0 && (
        <List
          bordered
          dataSource={committedFiles}
          renderItem={(file) => (
            <List.Item>
              <Space wrap size={4}>
                <Text code>{stringValue(file.path) || 'workspace file'}</Text>
                {stringValue(file.object_key) && (
                  <Text copyable type="secondary">
                    {stringValue(file.object_key)}
                  </Text>
                )}
              </Space>
            </List.Item>
          )}
          size="small"
        />
      )}
      {restoredFiles.length > 0 && (
        <List
          bordered
          dataSource={restoredFiles}
          renderItem={(file) => (
            <List.Item>
              <Space wrap size={4}>
                <Text code>{stringValue(file.path) || 'workspace file'}</Text>
                {stringValue(file.rollback_action) && (
                  <Tag>{stringValue(file.rollback_action)}</Tag>
                )}
              </Space>
            </List.Item>
          )}
          size="small"
        />
      )}
    </Space>
  )
}

function approvalArtifactRows(
  event: RunEvent,
  decision?: RunApprovalDecisionResponse,
): Array<[string, string]> {
  const artifactLabels: Array<[string, string]> = [
    ['Operation', 'operation_id'],
    ['Operation status', 'operation_status'],
    ['Workspace commit', 'workspace_commit_status'],
    ['Rollback', 'rollback_token'],
    ['Plan', 'operation_plan_object_key'],
    ['Diff', 'diff_object_key'],
    ['Manifest', 'manifest_object_key'],
    ['Args', 'args_object_key'],
    ['Result', 'result_object_key'],
    ['Stdout', 'stdout_object_key'],
    ['Stderr', 'stderr_object_key'],
  ]

  return artifactLabels.flatMap(([label, key]) => {
    const value = approvalArtifactValue(event, key, decision)
    return value ? [[label, value] as [string, string]] : []
  })
}

function operationStatusColor(status: string) {
  if (status === 'committed' || status === 'executed' || status === 'rolled_back') {
    return 'green'
  }
  if (status.includes('failed')) {
    return 'red'
  }
  if (status.includes('not_committed')) {
    return 'orange'
  }
  return 'blue'
}

export function ChatPanel({
  onRuntimeContextChange,
  workspaceId,
}: ChatPanelProps) {
  const { styles } = useStyles()
  const eventSourceRef = useRef<EventSource | null>(null)
  const messageScrollRef = useRef<HTMLDivElement | null>(null)
  const resumedRunIdRef = useRef<string | null>(null)
  const runEventScrollRef = useRef<HTMLDivElement | null>(null)
  const [threads, setThreads] = useState<ThreadSummary[]>([])
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ConversationMessage[]>([])
  const [runEvents, setRunEvents] = useState<RunEvent[]>([])
  const [approvalDecisionsById, setApprovalDecisionsById] = useState<
    Record<string, RunApprovalDecisionResponse>
  >({})
  const [rollbackResultsByOperationId, setRollbackResultsByOperationId] =
    useState<Record<string, RunOperationRollbackResponse>>({})
  const [prompt, setPrompt] = useState('')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [loadingThreads, setLoadingThreads] = useState(false)
  const [loadingMessages, setLoadingMessages] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [recoveringRun, setRecoveringRun] = useState(false)
  const [runRecoveryMessage, setRunRecoveryMessage] = useState<string | null>(null)
  const [runningRunId, setRunningRunId] = useState<string | null>(null)
  const [resolvingApprovalId, setResolvingApprovalId] = useState<string | null>(
    null,
  )
  const [rollingBackOperationId, setRollingBackOperationId] = useState<
    string | null
  >(null)
  const [sending, setSending] = useState(false)
  const [creatingThread, setCreatingThread] = useState(false)
  const [mutatingThreadId, setMutatingThreadId] = useState<string | null>(null)
  const [renameThread, setRenameThread] = useState<ThreadSummary | null>(null)
  const [renameTitle, setRenameTitle] = useState('')
  const [pendingThreadStatusAction, setPendingThreadStatusAction] = useState<{
    status: Extract<ThreadSummary['status'], 'archived' | 'soft_deleted'>
    thread: ThreadSummary
  } | null>(null)
  const [runStatusOverrides, setRunStatusOverrides] = useState<
    Record<string, RunStatus>
  >({})
  const visibleThreads = sortActiveThreads(threads)
  const activeThread = threads.find((item) => item.thread_id === activeThreadId)
  const activeThreadRunStatus = activeThread
    ? effectiveThreadRunStatus(activeThread, runStatusOverrides)
    : null
  const threadBlockingRunStatus =
    activeThreadRunStatus === 'running' ||
    activeThreadRunStatus === 'waiting_approval'
      ? activeThreadRunStatus
      : null
  const threadBlockingRunId = threadBlockingRunStatus
    ? activeThread?.current_run_id || null
    : null
  const activeBlockingRunId = runningRunId || threadBlockingRunId
  const activeBlockingRunStatus: RunStatus | null = runningRunId
    ? 'running'
    : threadBlockingRunStatus
  const canRecoverActiveRun =
    Boolean(activeBlockingRunId) && activeBlockingRunStatus === 'running'

  const closeEventSource = useCallback(() => {
    eventSourceRef.current?.close()
    eventSourceRef.current = null
  }, [])

  useEffect(() => {
    const scroll = messageScrollRef.current
    if (scroll) {
      scroll.scrollTop = scroll.scrollHeight
    }
  }, [messages, loadingMessages])

  useEffect(() => {
    const scroll = runEventScrollRef.current
    if (scroll) {
      scroll.scrollTop = scroll.scrollHeight
    }
  }, [runEvents])

  const loadMessagesForThread = useCallback(
    async (threadId: string) => {
      setLoadingMessages(true)
      setErrorMessage(null)
      try {
        const response = await listMessages(
          threadId,
          { limit: 100 },
          workspaceId,
        )
        setMessages((current) =>
          mergeMessagesWithStreamingDrafts(response.messages, current, threadId),
        )
      } catch (error) {
        setErrorMessage(
          error instanceof Error ? error.message : 'Failed to load messages.',
        )
      } finally {
        setLoadingMessages(false)
      }
    },
    [workspaceId],
  )

  const loadThreads = useCallback(async () => {
    setLoadingThreads(true)
    setErrorMessage(null)
    try {
      const response = await listThreads(workspaceId)
      const nextVisibleThreads = sortActiveThreads(response.threads)
      setThreads(response.threads)
      setActiveThreadId((current) =>
        current &&
        response.threads.some(
          (thread) =>
            thread.thread_id === current && thread.status === 'active',
        )
          ? current
          : nextVisibleThreads[0]?.thread_id || null,
      )
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Failed to load threads.',
      )
    } finally {
      setLoadingThreads(false)
    }
  }, [workspaceId])

  useEffect(() => {
    void loadThreads()
    return () => closeEventSource()
  }, [closeEventSource, loadThreads])

  useEffect(() => {
    if (activeThreadId) {
      void loadMessagesForThread(activeThreadId)
    } else {
      setMessages([])
    }
  }, [activeThreadId, loadMessagesForThread])

  useEffect(() => {
    const thread = threads.find((item) => item.thread_id === activeThreadId)
    const threadRunId =
      activeThreadRunStatus === 'running' ||
      activeThreadRunStatus === 'waiting_approval'
        ? thread?.current_run_id || null
        : null
    const runId = runningRunId || threadRunId
    onRuntimeContextChange?.({
      run_id: runId,
      run_status: runningRunId ? 'running' : activeThreadRunStatus || null,
      thread_id: activeThreadId,
    })
  }, [
    activeThreadId,
    activeThreadRunStatus,
    onRuntimeContextChange,
    runningRunId,
    threads,
  ])

  const appendAssistantDelta = useCallback((event: RunEvent) => {
    const delta = event.payload?.delta
    if (typeof delta !== 'string' || !delta) {
      return
    }
    const streamMessageId = `stream-${event.run_id}`
    setMessages((current) => {
      const existing = current.find(
        (message) => message.message_id === streamMessageId,
      )
      if (existing) {
        return current.map((message) =>
          message.message_id === streamMessageId
            ? { ...message, content: `${message.content}${delta}` }
            : message,
        )
      }
      return [
        ...current,
        {
          content: delta,
          created_at: event.created_at,
          message_id: streamMessageId,
          role: 'assistant',
          run_id: event.run_id,
          thread_id: event.thread_id,
          workspace_id: event.workspace_id,
        },
      ]
    })
  }, [])

  const appendRunEvent = useCallback((event: RunEvent) => {
    if (event.type === 'assistant_delta') {
      appendAssistantDelta(event)
    }
    setRunEvents((current) => {
      if (current.some((item) => item.event_id === event.event_id)) {
        return current
      }
      return [...current, event].sort(
        (left, right) => left.event_seq - right.event_seq,
      )
    })
  }, [appendAssistantDelta])

  const markThreadRunStatus = useCallback(
    (threadId: string, runId: string, status: RunStatus) => {
      setRunStatusOverrides((current) => ({ ...current, [runId]: status }))
      setThreads((current) =>
        current.map((thread) =>
          thread.thread_id === threadId && thread.current_run_id === runId
            ? { ...thread, current_run_status: status }
            : thread,
        ),
      )
    },
    [],
  )

  const startRunEventReplay = useCallback(
    (run: RunDetailResponse, afterEventId?: string | null) => {
      if (typeof EventSource === 'undefined') {
        setRunningRunId(null)
        return
      }
      closeEventSource()
      let latestEventId = afterEventId || null
      const source = new EventSource(
        getRunEventsStreamUrl(
          run.run_id,
          latestEventId ? { after_event_id: latestEventId } : {},
          workspaceId,
        ),
      )
      RUN_EVENT_TYPES.forEach((type) => {
        source.addEventListener(type, (event) => {
          const parsed = parseSseEvent(event as MessageEvent<string>)
          if (parsed) {
            latestEventId = parsed.event_id
            appendRunEvent(parsed)
          }
          if (type === 'stream_closed') {
            source.close()
            eventSourceRef.current = null
            setRunningRunId(null)
            if (parsed) {
              markThreadRunStatus(
                run.thread_id,
                run.run_id,
                closedRunStatusFromEvent(parsed),
              )
            }
            void loadMessagesForThread(run.thread_id)
            void loadThreads()
          }
        })
      })
      source.onerror = () => {
        source.close()
        eventSourceRef.current = null
        void (async () => {
          try {
            const replay = await listRunEvents(
              run.run_id,
              latestEventId ? { after_event_id: latestEventId } : {},
              workspaceId,
            )
            replay.events.forEach(appendRunEvent)
            const nextEventId = replay.next_after_event_id || latestEventId
            if (isClosedRunStatus(replay.run_status)) {
              setRunningRunId(null)
              markThreadRunStatus(run.thread_id, run.run_id, replay.run_status)
              await loadMessagesForThread(run.thread_id)
              await loadThreads()
              return
            }
            startRunEventReplay(run, nextEventId)
          } catch (error) {
            setRunningRunId(null)
            setErrorMessage(
              error instanceof Error
                ? error.message
                : 'Failed to recover run event stream.',
            )
            await loadMessagesForThread(run.thread_id)
            await loadThreads()
          }
        })()
      }
      eventSourceRef.current = source
    },
    [
      appendRunEvent,
      closeEventSource,
      loadMessagesForThread,
      loadThreads,
      markThreadRunStatus,
      workspaceId,
    ],
  )

  useEffect(() => {
    const thread = threads.find((item) => item.thread_id === activeThreadId)
    const runId = thread?.current_run_id
    const currentRunStatus = runId
      ? runStatusOverrides[runId] || thread.current_run_status
      : thread?.current_run_status
    if (
      !thread ||
      !runId ||
      !['running', 'waiting_approval'].includes(currentRunStatus || '')
    ) {
      resumedRunIdRef.current = null
      return
    }
    if (resumedRunIdRef.current === runId) {
      return
    }
    resumedRunIdRef.current = runId
    setRunningRunId(runId)
    void (async () => {
      try {
        const events = await listRunEvents(runId, {}, workspaceId)
        setRunEvents(events.events)
        if (currentRunStatus === 'waiting_approval') {
          setRunningRunId(null)
          return
        }
        if (isClosedRunStatus(events.run_status)) {
          setRunningRunId(null)
          markThreadRunStatus(thread.thread_id, runId, events.run_status)
          await loadMessagesForThread(thread.thread_id)
          await loadThreads()
          return
        }
        const resumableRun: RunDetailResponse = {
          assistant_message_id: null,
          created_at: thread.updated_at,
          idempotency_key: '',
          last_event_id: events.next_after_event_id,
          last_event_seq: events.events.at(-1)?.event_seq ?? 0,
          leaf_state: {},
          model_error: null,
          run_id: runId,
          status: 'running',
          thread_id: thread.thread_id,
          updated_at: thread.updated_at,
          user_message_id: null,
          workspace_id: workspaceId,
        }
        startRunEventReplay(resumableRun, events.next_after_event_id)
      } catch (error) {
        setRunningRunId(null)
        setErrorMessage(
          error instanceof Error
            ? error.message
            : 'Failed to resume run stream.',
        )
      }
    })()
  }, [
    activeThreadId,
    loadMessagesForThread,
    loadThreads,
    markThreadRunStatus,
    runStatusOverrides,
    startRunEventReplay,
    threads,
    workspaceId,
  ])

  const ensureThread = async () => {
    if (activeThreadId) {
      return activeThreadId
    }
    const title = prompt.trim().slice(0, 60) || 'New chat'
    const thread = await createThread({ title }, workspaceId)
    setThreads((current) => [thread, ...current])
    setActiveThreadId(thread.thread_id)
    return thread.thread_id
  }

  const handleNewThread = async () => {
    if (creatingThread) {
      return
    }
    setCreatingThread(true)
    closeEventSource()
    setRunningRunId(null)
    setErrorMessage(null)
    setRunRecoveryMessage(null)
    try {
      const thread = await createThread({ title: 'New chat' }, workspaceId)
      setThreads((current) => [thread, ...current])
      setActiveThreadId(thread.thread_id)
      setMessages([])
      setRunEvents([])
      setApprovalDecisionsById({})
      setRollbackResultsByOperationId({})
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Failed to create thread.',
      )
    } finally {
      setCreatingThread(false)
    }
  }

  const handleSelectThread = (thread: ThreadSummary) => {
    if (mutatingThreadId === thread.thread_id) {
      return
    }
    closeEventSource()
    resumedRunIdRef.current = null
    setRunRecoveryMessage(null)
    setRunningRunId(
      isThreadBlocked(thread, runStatusOverrides)
        ? thread.current_run_id || null
        : null,
    )
    setActiveThreadId(thread.thread_id)
    setRunEvents([])
    setApprovalDecisionsById({})
    setRollbackResultsByOperationId({})
  }

  const applyThreadPatch = async (
    thread: ThreadSummary,
    input: PatchThreadInput,
  ) => {
    if (mutatingThreadId) {
      return false
    }
    setMutatingThreadId(thread.thread_id)
    setErrorMessage(null)
    try {
      const updated = await patchThread(thread.thread_id, input, workspaceId)
      const hidesThread = updated.status !== 'active'
      const nextThreads = hidesThread
        ? threads.filter((item) => item.thread_id !== updated.thread_id)
        : threads.map((item) =>
            item.thread_id === updated.thread_id ? updated : item,
          )
      setThreads(nextThreads)

      if (hidesThread && activeThreadId === updated.thread_id) {
        const nextThread = sortActiveThreads(nextThreads)[0]
        closeEventSource()
        resumedRunIdRef.current = null
        setRunningRunId(null)
        setActiveThreadId(nextThread?.thread_id || null)
        setMessages([])
        setRunEvents([])
      }
      return true
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Failed to update thread.',
      )
      return false
    } finally {
      setMutatingThreadId(null)
    }
  }

  const openRenameThread = (thread: ThreadSummary) => {
    setRenameThread(thread)
    setRenameTitle(thread.title)
  }

  const handleRenameThread = async () => {
    if (!renameThread) {
      return
    }
    const title = renameTitle.trim()
    if (!title) {
      return
    }
    if (title === renameThread.title) {
      setRenameThread(null)
      setRenameTitle('')
      return
    }
    const updated = await applyThreadPatch(renameThread, { title })
    if (updated) {
      setRenameThread(null)
      setRenameTitle('')
    }
  }

  const handleConfirmThreadStatusAction = async () => {
    if (!pendingThreadStatusAction) {
      return
    }
    const updated = await applyThreadPatch(pendingThreadStatusAction.thread, {
      status: pendingThreadStatusAction.status,
    })
    if (updated) {
      setPendingThreadStatusAction(null)
    }
  }

  const handleThreadMenuClick = (
    thread: ThreadSummary,
    info: Parameters<NonNullable<MenuProps['onClick']>>[0],
  ) => {
    info.domEvent.stopPropagation()
    if (mutatingThreadId === thread.thread_id) {
      return
    }
    if (info.key === 'pin') {
      void applyThreadPatch(thread, { pinned: !thread.pinned })
      return
    }
    if (info.key === 'rename') {
      openRenameThread(thread)
      return
    }
    if (isThreadBlocked(thread, runStatusOverrides)) {
      return
    }
    if (info.key === 'archive') {
      setPendingThreadStatusAction({ status: 'archived', thread })
      return
    }
    if (info.key === 'delete') {
      setPendingThreadStatusAction({ status: 'soft_deleted', thread })
    }
  }

  const threadMenuItems = (thread: ThreadSummary): MenuProps['items'] => {
    const blocked = isThreadBlocked(thread, runStatusOverrides)
    const mutating = mutatingThreadId === thread.thread_id
    return [
      {
        disabled: mutating,
        key: 'pin',
        label: thread.pinned ? 'Unpin' : 'Pin',
      },
      {
        disabled: mutating,
        key: 'rename',
        label: 'Rename',
      },
      { type: 'divider' },
      {
        disabled: mutating || blocked,
        key: 'archive',
        label: 'Archive',
      },
      {
        danger: true,
        disabled: mutating || blocked,
        key: 'delete',
        label: 'Delete',
      },
    ]
  }

  const handleSend = async () => {
    const content = prompt.trim()
    if (!content || activeBlockingRunId) {
      return
    }
    setSending(true)
    setErrorMessage(null)
    setRunRecoveryMessage(null)
    setRunEvents([])
    setApprovalDecisionsById({})
    setRollbackResultsByOperationId({})
    try {
      const threadId = await ensureThread()
      setPrompt('')
      const run = await createRun(
        threadId,
        {
          idempotency_key: newClientRequestId(),
          stream: true,
          user_message: content,
        },
        workspaceId,
      )
      setRunningRunId(run.status === 'running' ? run.run_id : null)
      const events = await listRunEvents(run.run_id, {}, workspaceId)
      setRunEvents(events.events)
      resumedRunIdRef.current = run.status === 'running' ? run.run_id : null
      startRunEventReplay(run, events.next_after_event_id)
      await loadMessagesForThread(threadId)
      await loadThreads()
    } catch (error) {
      setRunningRunId(null)
      setErrorMessage(
        error instanceof Error ? error.message : 'Failed to send message.',
      )
    } finally {
      setSending(false)
    }
  }

  const handleCancelRun = async () => {
    const runId = activeBlockingRunId
    if (!runId || cancelling) {
      return
    }

    setCancelling(true)
    setErrorMessage(null)
    setRunRecoveryMessage(null)
    try {
      const cancelled = await cancelRun(runId, workspaceId)
      closeEventSource()
      resumedRunIdRef.current = null
      setRunningRunId(null)
      markThreadRunStatus(
        cancelled.thread_id,
        cancelled.run_id,
        cancelled.status,
      )

      const events = await listRunEvents(runId, {}, workspaceId)
      setRunEvents(events.events)
      await loadMessagesForThread(cancelled.thread_id)
      await loadThreads()
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Failed to cancel run.',
      )
    } finally {
      setCancelling(false)
    }
  }

  const handleRecoverStaleRun = async () => {
    const runId = activeBlockingRunId
    const thread = activeThread
    if (!runId || !thread || recoveringRun) {
      return
    }

    setRecoveringRun(true)
    setErrorMessage(null)
    setRunRecoveryMessage(null)
    closeEventSource()
    try {
      const result = await recoverStaleRuns(workspaceId)
      const events = await listRunEvents(runId, {}, workspaceId)
      setRunEvents(events.events)

      if (isClosedRunStatus(events.run_status)) {
        setRunningRunId(null)
        resumedRunIdRef.current = null
        markThreadRunStatus(thread.thread_id, runId, events.run_status)
      } else if (events.run_status === 'running') {
        setRunningRunId(runId)
        resumedRunIdRef.current = runId
        startRunEventReplay(
          {
            assistant_message_id: null,
            created_at: thread.updated_at,
            idempotency_key: '',
            last_event_id: events.next_after_event_id,
            last_event_seq: events.events.at(-1)?.event_seq ?? 0,
            leaf_state: {},
            model_error: null,
            run_id: runId,
            status: 'running',
            thread_id: thread.thread_id,
            updated_at: thread.updated_at,
            user_message_id: null,
            workspace_id: workspaceId,
          },
          events.next_after_event_id,
        )
      }

      await loadMessagesForThread(thread.thread_id)
      await loadThreads()
      setRunRecoveryMessage(
        result.recovered_count > 0
          ? `Recovered ${result.recovered_count} stale run(s).`
          : 'No stale runs were old enough to recover.',
      )
    } catch (error) {
      setRunningRunId(null)
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Failed to recover stale run.',
      )
    } finally {
      setRecoveringRun(false)
    }
  }

  const handleResolveApproval = async (
    event: RunEvent,
    decision: 'approve' | 'reject',
  ) => {
    const approvalId = approvalIdFromEvent(event)
    if (!approvalId || resolvingApprovalId) {
      return
    }
    setResolvingApprovalId(approvalId)
    setErrorMessage(null)
    try {
      const input = {
        idempotency_key: newClientRequestId(),
        reason:
          decision === 'approve'
            ? 'Approved from chat run event.'
            : 'Rejected from chat run event.',
      }
      if (decision === 'approve') {
        const response = await approveRunApproval(
          event.run_id,
          approvalId,
          input,
          workspaceId,
        )
        setApprovalDecisionsById((current) => ({
          ...current,
          [approvalId]: response,
        }))
      } else {
        const response = await rejectRunApproval(
          event.run_id,
          approvalId,
          input,
          workspaceId,
        )
        setApprovalDecisionsById((current) => ({
          ...current,
          [approvalId]: response,
        }))
      }
      const events = await listRunEvents(event.run_id, {}, workspaceId)
      setRunEvents(events.events)
      await loadThreads()
      if (decision === 'reject') {
        await loadMessagesForThread(event.thread_id)
      }
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Failed to resolve approval.',
      )
    } finally {
      setResolvingApprovalId(null)
    }
  }

  const handleRollbackOperation = async (
    event: RunEvent,
    decision?: RunApprovalDecisionResponse,
  ) => {
    const data = operationDataFromApproval(decision)
    const operationId = operationIdFromApproval(event, decision)
    const rollbackToken =
      stringValue(data.rollback_token) || stringValue(event.payload?.rollback_token)
    if (!operationId || !rollbackToken || rollingBackOperationId) {
      return
    }
    if (
      typeof window !== 'undefined' &&
      !window.confirm(`Rollback workspace files for operation ${operationId}?`)
    ) {
      return
    }

    setRollingBackOperationId(operationId)
    setErrorMessage(null)
    try {
      const response = await rollbackRunOperation(
        event.run_id,
        operationId,
        {
          idempotency_key: newClientRequestId(),
          reason: 'Rollback requested from chat run event.',
          rollback_token: rollbackToken,
        },
        workspaceId,
      )
      setRollbackResultsByOperationId((current) => ({
        ...current,
        [operationId]: response,
      }))
      const events = await listRunEvents(event.run_id, {}, workspaceId)
      setRunEvents(events.events)
      await loadThreads()
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Failed to rollback operation.',
      )
    } finally {
      setRollingBackOperationId(null)
    }
  }

  return (
    <div className={styles.grid}>
      <Card
        className={`${styles.panelCard} ${styles.threadPanel}`}
        title="Threads"
        extra={
          <Button
            loading={creatingThread}
            onClick={() => void handleNewThread()}
            size="small"
          >
            New
          </Button>
        }
      >
        {loadingThreads && threads.length === 0 ? (
          <Skeleton active paragraph={{ rows: 6 }} />
        ) : (
          <List
            dataSource={visibleThreads}
            locale={{
              emptyText: (
                <Empty
                  description="No threads"
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                />
              ),
            }}
            renderItem={(thread) => (
              <List.Item
                actions={[
                  <Dropdown
                    key="actions"
                    menu={{
                      items: threadMenuItems(thread),
                      onClick: (info) => handleThreadMenuClick(thread, info),
                    }}
                    trigger={['click']}
                  >
                    <Button
                      aria-label={`Thread actions for ${thread.title}`}
                      loading={mutatingThreadId === thread.thread_id}
                      onClick={(event) => event.stopPropagation()}
                      size="small"
                    >
                      Actions
                    </Button>
                  </Dropdown>,
                ]}
                className={styles.threadItem}
                onClick={() => handleSelectThread(thread)}
              >
                <List.Item.Meta
                  description={thread.last_message_preview || 'No messages yet'}
                  title={
                    <Space>
                      {thread.pinned && <Tag color="gold">Pinned</Tag>}
                      <Text
                        className={styles.threadTitle}
                        strong={thread.thread_id === activeThreadId}
                      >
                        {thread.title}
                      </Text>
                      {effectiveThreadRunStatus(thread, runStatusOverrides) && (
                        <Tag>
                          {effectiveThreadRunStatus(thread, runStatusOverrides)}
                        </Tag>
                      )}
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Card>

      <Card className={styles.panelCard} title="Chat">
        <div className={styles.chatBody}>
          <div className={styles.messageScroll} ref={messageScrollRef}>
            <Space direction="vertical" size={16} style={{ width: '100%' }}>
              {errorMessage && (
                <Alert message={errorMessage} showIcon type="error" />
              )}
              {runRecoveryMessage && (
                <Alert message={runRecoveryMessage} showIcon type="info" />
              )}
              {loadingMessages ? (
                <Skeleton active paragraph={{ rows: 8 }} />
              ) : (
                <List
                  dataSource={messages}
                  locale={{
                    emptyText: (
                      <Empty
                        description="Start a conversation"
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                      />
                    ),
                  }}
                  renderItem={(message) => (
                    <List.Item>
                      <Space align="start" direction="vertical" size={4}>
                        <Tag color={roleColor(message.role)}>{message.role}</Tag>
                        <Text className={styles.messageBody}>
                          {message.content}
                        </Text>
                      </Space>
                    </List.Item>
                  )}
                />
              )}
            </Space>
          </div>

          <div className={styles.composer}>
            <Space.Compact block>
              <TextArea
                autoSize={{ minRows: 2, maxRows: 6 }}
                disabled={Boolean(activeBlockingRunId)}
                onChange={(event) => setPrompt(event.target.value)}
                onPressEnter={(event) => {
                  if (!event.shiftKey) {
                    event.preventDefault()
                    void handleSend()
                  }
                }}
                placeholder="Ask the agent..."
                value={prompt}
              />
              <Button
                disabled={Boolean(activeBlockingRunId)}
                loading={sending}
                onClick={() => void handleSend()}
                type="primary"
              >
                {activeBlockingRunStatus === 'waiting_approval'
                  ? 'Waiting approval'
                  : activeBlockingRunId
                    ? 'Running'
                    : 'Send'}
              </Button>
              {activeBlockingRunId && (
                <Button
                  danger
                  loading={cancelling}
                  onClick={() => void handleCancelRun()}
                >
                  Cancel
                </Button>
              )}
              {canRecoverActiveRun && (
                <Button
                  loading={recoveringRun}
                  onClick={() => void handleRecoverStaleRun()}
                >
                  Recover stale
                </Button>
              )}
            </Space.Compact>
          </div>
        </div>
      </Card>

      <Card className={`${styles.panelCard} ${styles.sidePanel}`} title="Run events">
        <div className={styles.eventBody}>
          <Alert
            description="Run events are the SSE debug timeline for one execution. assistant_delta entries are streaming model chunks; the final assistant message is saved into chat history when the Run closes."
            message="Run event stream"
            showIcon
            type="info"
          />
          <div className={styles.eventScroll} ref={runEventScrollRef}>
            <List
              dataSource={runEvents}
              locale={{
                emptyText: (
                  <Empty
                    description="No run events"
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                  />
                ),
              }}
              renderItem={(event) => (
                <List.Item>
                  <Space direction="vertical" size={2}>
                    <Text code>{event.type}</Text>
                    <Text type="secondary">{event.event_id}</Text>
                    {isApprovalRequestEvent(event) && (
                      <RunApprovalCard
                        decision={
                          approvalDecisionsById[approvalIdFromEvent(event) || '']
                        }
                        event={event}
                        onRollback={handleRollbackOperation}
                        onResolve={handleResolveApproval}
                        resolvingApprovalId={resolvingApprovalId}
                        rollbackResultsByOperationId={rollbackResultsByOperationId}
                        rollingBackOperationId={rollingBackOperationId}
                        runEvents={runEvents}
                      />
                    )}
                    {isApprovalOutcomeEvent(event) && (
                      <RunApprovalArtifactSummary
                        decision={
                          approvalDecisionsById[approvalIdFromEvent(event) || '']
                        }
                        event={event}
                        onRollback={handleRollbackOperation}
                        payloadClassName={styles.payloadBox}
                        rollbackResultsByOperationId={rollbackResultsByOperationId}
                        rollingBackOperationId={rollingBackOperationId}
                        runEvents={runEvents}
                      />
                    )}
                    {Object.keys(event.payload || {}).length > 0 && (
                      <pre
                        className={styles.payloadBox}
                        data-testid={`run-event-payload-${event.event_id}`}
                      >
                        {JSON.stringify(event.payload, null, 2)}
                      </pre>
                    )}
                  </Space>
                </List.Item>
              )}
            />
          </div>
        </div>
      </Card>

      <Modal
        confirmLoading={mutatingThreadId === renameThread?.thread_id}
        okButtonProps={{ disabled: !renameTitle.trim() }}
        onCancel={() => {
          setRenameThread(null)
          setRenameTitle('')
        }}
        onOk={() => void handleRenameThread()}
        open={Boolean(renameThread)}
        title="Rename thread"
      >
        <Input
          aria-label="Thread title"
          maxLength={120}
          onChange={(event) => setRenameTitle(event.target.value)}
          onPressEnter={() => void handleRenameThread()}
          value={renameTitle}
        />
      </Modal>

      <Modal
        confirmLoading={
          mutatingThreadId === pendingThreadStatusAction?.thread.thread_id
        }
        okButtonProps={{
          danger: pendingThreadStatusAction?.status === 'soft_deleted',
        }}
        okText={
          pendingThreadStatusAction?.status === 'soft_deleted'
            ? 'Delete'
            : 'Archive'
        }
        onCancel={() => setPendingThreadStatusAction(null)}
        onOk={() => void handleConfirmThreadStatusAction()}
        open={Boolean(pendingThreadStatusAction)}
        title={
          pendingThreadStatusAction?.status === 'soft_deleted'
            ? 'Delete thread'
            : 'Archive thread'
        }
      >
        <Text>
          {pendingThreadStatusAction?.status === 'soft_deleted'
            ? 'Delete this thread from the active history?'
            : 'Archive this thread from the active history?'}
        </Text>
      </Modal>
    </div>
  )
}
