'use client'

import {
  Alert,
  Button,
  Card,
  Col,
  Drawer,
  Input,
  InputNumber,
  Row,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { createStyles } from 'antd-style'
import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  createSubAgentTask,
  getSubAgentTask,
  listSubAgentTasks,
  reviewSubAgentResult,
} from '@/api/agentApiClient'
import type {
  RunStatus,
  SubAgentMode,
  SubAgentReviewDecision,
  SubAgentStatus,
  SubAgentTaskDetail,
  SubAgentTaskInput,
  SubAgentTaskSummary,
  WorkspaceId,
} from '@/api/schemas/workspace'

const { Text, Title } = Typography

const STATUS_OPTIONS: { label: string; value: SubAgentStatus | '' }[] = [
  { label: 'All', value: '' },
  { label: 'created', value: 'created' },
  { label: 'queued', value: 'queued' },
  { label: 'running', value: 'running' },
  { label: 'completed', value: 'completed' },
  { label: 'failed', value: 'failed' },
  { label: 'reviewed', value: 'reviewed' },
]

const MODE_OPTIONS: { label: string; value: SubAgentMode }[] = [
  { label: 'readonly', value: 'readonly' },
  { label: 'write', value: 'write' },
]

const MIN_TIMEOUT_MS = 1000
const MAX_TIMEOUT_MS = 3600000
const MIN_TOKEN_BUDGET = 1000
const MAX_TOKEN_BUDGET = 200000

const DEFAULT_CREATE_INPUT: SubAgentTaskInput = {
  agent_type: 'backend-developer',
  allowed_tools: [],
  expected_output:
    'Return summary, findings, changed files, risks and open questions.',
  forbidden_tools: [],
  mode: 'readonly',
  objective: '',
  parent_run_id: '',
  parent_thread_id: '',
  read_scope: [],
  timeout_ms: 300000,
  token_budget: 12000,
  write_scope: [],
}

const useStyles = createStyles(({ css, token }) => ({
  jsonBox: css`
    background: ${token.colorFillQuaternary};
    border: 1px solid ${token.colorBorderSecondary};
    border-radius: 6px;
    max-height: 280px;
    overflow: auto;
    padding: 12px;
    white-space: pre-wrap;
    word-break: break-word;
  `,
  table: css`
    .ant-table-cell {
      vertical-align: top;
    }
  `,
  toolbar: css`
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: space-between;
  `,
}))

interface SubAgentsPanelProps {
  runtimeContext?: SubAgentRuntimeContext | null
  workspaceId: WorkspaceId
}

interface SubAgentRuntimeContext {
  run_id: string | null
  run_status: RunStatus | null
  thread_id: string | null
}

export function SubAgentsPanel({ runtimeContext, workspaceId }: SubAgentsPanelProps) {
  const { styles } = useStyles()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [createInput, setCreateInput] =
    useState<SubAgentTaskInput>(DEFAULT_CREATE_INPUT)
  const [creating, setCreating] = useState(false)
  const [loading, setLoading] = useState(false)
  const [parentRunId, setParentRunId] = useState('')
  const [reviewing, setReviewing] = useState<SubAgentReviewDecision | null>(
    null,
  )
  const [selectedTask, setSelectedTask] = useState<SubAgentTaskDetail | null>(
    null,
  )
  const [statusFilter, setStatusFilter] = useState<SubAgentStatus | ''>('')
  const [tasks, setTasks] = useState<SubAgentTaskSummary[]>([])

  const selectedJobId = useMemo(
    () => resultJobId(selectedTask?.result),
    [selectedTask],
  )
  const createValidationError = useMemo(
    () => validateCreateInput(createInput),
    [createInput],
  )
  const canReview = Boolean(
    selectedTask?.result &&
    !selectedTask.review &&
    (selectedTask.status === 'completed' || selectedTask.status === 'failed'),
  )

  const loadTasks = useCallback(async () => {
    setLoading(true)
    setErrorMessage(null)
    try {
      const response = await listSubAgentTasks(workspaceId, {
        parent_run_id: parentRunId.trim() || undefined,
        status: statusFilter || undefined,
      })
      setTasks(response.tasks)
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Failed to load SubAgent tasks.',
      )
    } finally {
      setLoading(false)
    }
  }, [parentRunId, statusFilter, workspaceId])

  useEffect(() => {
    void loadTasks()
  }, [loadTasks])

  useEffect(() => {
    if (runtimeContext?.run_status !== 'running' || !runtimeContext.run_id) {
      return
    }
    setCreateInput((current) => ({
      ...current,
      parent_run_id: current.parent_run_id || runtimeContext.run_id || '',
      parent_thread_id:
        current.parent_thread_id || runtimeContext.thread_id || '',
    }))
  }, [runtimeContext])

  const columns = [
    {
      key: 'task',
      title: 'Task',
      render: (_: unknown, record: SubAgentTaskSummary) => (
        <Space direction="vertical" size={2}>
          <Button
            data-testid={`subagent-open-${record.task_id}`}
            onClick={() => void handleOpenTask(record.task_id)}
            type="link"
          >
            {record.agent_type}
          </Button>
          <Text code>{record.task_id}</Text>
          <Text type="secondary">{record.objective}</Text>
        </Space>
      ),
    },
    {
      key: 'runtime',
      title: 'Runtime',
      render: (_: unknown, record: SubAgentTaskSummary) => (
        <Space direction="vertical" size={4}>
          <Space wrap>
            <Tag color={statusColor(record.status)}>{record.status}</Tag>
            <Tag>{record.mode}</Tag>
            {record.needs_main_review && <Tag color="orange">main_review</Tag>}
          </Space>
          <Text code>{record.parent_run_id}</Text>
        </Space>
      ),
    },
    {
      key: 'scope',
      title: 'Scope',
      render: (_: unknown, record: SubAgentTaskSummary) => (
        <Space direction="vertical" size={4}>
          <ScopeTags label="read" values={record.read_scope} />
          <ScopeTags label="write" values={record.write_scope} />
        </Space>
      ),
    },
    {
      key: 'budget',
      title: 'Budget',
      render: (_: unknown, record: SubAgentTaskSummary) => (
        <Space direction="vertical" size={2}>
          <Text>{record.timeout_ms} ms</Text>
          <Text>{record.token_budget} tokens</Text>
        </Space>
      ),
    },
    {
      key: 'actions',
      title: 'Actions',
      render: (_: unknown, record: SubAgentTaskSummary) => (
        <Button onClick={() => void handleOpenTask(record.task_id)}>
          View
        </Button>
      ),
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <div className={styles.toolbar}>
        <Space wrap>
          <Input
            allowClear
            onChange={(event) => setParentRunId(event.target.value)}
            onPressEnter={() => void loadTasks()}
            placeholder="parent_run_id"
            style={{ width: 260 }}
            value={parentRunId}
          />
          <Select
            onChange={setStatusFilter}
            options={STATUS_OPTIONS}
            style={{ width: 160 }}
            value={statusFilter}
          />
          <Button loading={loading} onClick={() => void loadTasks()}>
            Refresh
          </Button>
        </Space>
        {selectedJobId && (
          <Space>
            <Text code>{selectedJobId}</Text>
            <Button href={jobDetailHref(selectedJobId)}>Jobs</Button>
          </Space>
        )}
      </div>

      {errorMessage && <Alert message={errorMessage} showIcon type="error" />}

      <Card title="Create SubAgent task">
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Row gutter={[12, 12]}>
            <Col span={8}>
              <Input
                data-testid="subagent-create-agent-type"
                onChange={(event) =>
                  setCreateInput((current) => ({
                    ...current,
                    agent_type: event.target.value,
                  }))
                }
                placeholder="agent_type"
                value={createInput.agent_type}
              />
            </Col>
            <Col span={8}>
              <Segmented<SubAgentMode>
                block
                data-testid="subagent-create-mode"
                onChange={(mode) =>
                  setCreateInput((current) => ({
                    ...current,
                    mode,
                    write_scope: mode === 'readonly' ? [] : current.write_scope,
                  }))
                }
                options={MODE_OPTIONS}
                value={createInput.mode}
              />
            </Col>
            <Col span={8}>
              <Input
                data-testid="subagent-create-parent-run-id"
                onChange={(event) =>
                  setCreateInput((current) => ({
                    ...current,
                    parent_run_id: event.target.value,
                  }))
                }
                placeholder="parent_run_id"
                value={createInput.parent_run_id}
              />
            </Col>
          </Row>
          <Row gutter={[12, 12]}>
            <Col span={8}>
              <Input
                onChange={(event) =>
                  setCreateInput((current) => ({
                    ...current,
                    parent_thread_id: event.target.value,
                  }))
                }
                placeholder="parent_thread_id"
                value={createInput.parent_thread_id}
              />
            </Col>
            <Col span={8}>
              <InputNumber
                addonAfter="ms"
                max={MAX_TIMEOUT_MS}
                min={MIN_TIMEOUT_MS}
                onChange={(value) =>
                  setCreateInput((current) => ({
                    ...current,
                    timeout_ms: Number(value ?? 0),
                  }))
                }
                placeholder="timeout_ms"
                status={
                  isTimeoutValid(createInput.timeout_ms) ? undefined : 'error'
                }
                step={1000}
                style={{ width: '100%' }}
                value={createInput.timeout_ms}
              />
            </Col>
            <Col span={8}>
              <InputNumber
                addonAfter="tokens"
                max={MAX_TOKEN_BUDGET}
                min={MIN_TOKEN_BUDGET}
                onChange={(value) =>
                  setCreateInput((current) => ({
                    ...current,
                    token_budget: Number(value ?? 0),
                  }))
                }
                placeholder="token_budget"
                status={
                  isTokenBudgetValid(createInput.token_budget)
                    ? undefined
                    : 'error'
                }
                step={1000}
                style={{ width: '100%' }}
                value={createInput.token_budget}
              />
            </Col>
          </Row>
          {createValidationError && (
            <Alert message={createValidationError} showIcon type="warning" />
          )}
          <Input.TextArea
            autoSize={{ minRows: 2, maxRows: 5 }}
            onChange={(event) =>
              setCreateInput((current) => ({
                ...current,
                objective: event.target.value,
              }))
            }
            placeholder="Objective"
            value={createInput.objective}
          />
          <Row gutter={[12, 12]}>
            <Col span={12}>
              <Input.TextArea
                autoSize={{ minRows: 2, maxRows: 4 }}
                onChange={(event) =>
                  setCreateInput((current) => ({
                    ...current,
                    read_scope: splitLines(event.target.value),
                  }))
                }
                placeholder="read_scope, one per line"
                value={createInput.read_scope.join('\n')}
              />
            </Col>
            <Col span={12}>
              <Input.TextArea
                autoSize={{ minRows: 2, maxRows: 4 }}
                data-testid="subagent-create-write-scope"
                onChange={(event) =>
                  setCreateInput((current) => ({
                    ...current,
                    write_scope:
                      current.mode === 'write'
                        ? splitLines(event.target.value)
                        : [],
                  }))
                }
                disabled={createInput.mode === 'readonly'}
                placeholder={
                  createInput.mode === 'readonly'
                    ? 'write_scope disabled in readonly mode'
                    : 'write_scope, one per line'
                }
                value={
                  createInput.mode === 'write'
                    ? createInput.write_scope.join('\n')
                    : ''
                }
              />
            </Col>
          </Row>
          <Row gutter={[12, 12]}>
            <Col span={12}>
              <Input.TextArea
                autoSize={{ minRows: 2, maxRows: 4 }}
                onChange={(event) =>
                  setCreateInput((current) => ({
                    ...current,
                    allowed_tools: splitLines(event.target.value),
                  }))
                }
                placeholder="allowed_tools, one per line"
                value={createInput.allowed_tools.join('\n')}
              />
            </Col>
            <Col span={12}>
              <Input.TextArea
                autoSize={{ minRows: 2, maxRows: 4 }}
                onChange={(event) =>
                  setCreateInput((current) => ({
                    ...current,
                    forbidden_tools: splitLines(event.target.value),
                  }))
                }
                placeholder="forbidden_tools, one per line"
                value={createInput.forbidden_tools.join('\n')}
              />
            </Col>
          </Row>
          <Input.TextArea
            autoSize={{ minRows: 2, maxRows: 4 }}
            onChange={(event) =>
              setCreateInput((current) => ({
                ...current,
                expected_output: event.target.value,
              }))
            }
            placeholder="Expected output"
            value={createInput.expected_output}
          />
          <Space wrap>
            <Button
              disabled={Boolean(createValidationError)}
              loading={creating}
              onClick={() => void handleCreateTask()}
              type="primary"
            >
              Create task
            </Button>
            <Button
              onClick={() => setCreateInput(DEFAULT_CREATE_INPUT)}
              disabled={creating}
            >
              Reset
            </Button>
          </Space>
        </Space>
      </Card>

      <Card title="SubAgent tasks">
        <Table<SubAgentTaskSummary>
          className={styles.table}
          columns={columns}
          dataSource={tasks}
          loading={loading}
          pagination={{ pageSize: 8 }}
          rowKey="task_id"
        />
      </Card>

      <Drawer
        destroyOnHidden
        onClose={() => setDrawerOpen(false)}
        open={drawerOpen}
        title={selectedTask ? selectedTask.task_id : 'SubAgent task'}
        width={720}
      >
        {selectedTask ? (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Space direction="vertical" size={4}>
              <Title level={5} style={{ margin: 0 }}>
                {selectedTask.agent_type}
              </Title>
              <Text>{selectedTask.objective}</Text>
              <Space wrap>
                <Tag color={statusColor(selectedTask.status)}>
                  {selectedTask.status}
                </Tag>
                <Tag>{selectedTask.mode}</Tag>
                <Tag>{selectedTask.output_schema}</Tag>
              </Space>
            </Space>

            <Row gutter={[12, 12]}>
              <Col span={12}>
                <Card size="small" title="Read scope">
                  <ScopeTags values={selectedTask.read_scope} />
                </Card>
              </Col>
              <Col span={12}>
                <Card size="small" title="Write scope">
                  <ScopeTags values={selectedTask.write_scope} />
                </Card>
              </Col>
            </Row>

            {selectedJobId && (
              <Card size="small" title="Execution job">
                <Space>
                  <Text code>{selectedJobId}</Text>
                  <Button href={jobDetailHref(selectedJobId)}>Jobs</Button>
                </Space>
              </Card>
            )}

            <Card size="small" title="Result">
              <pre className={styles.jsonBox}>
                {JSON.stringify(selectedTask.result ?? {}, null, 2)}
              </pre>
            </Card>

            <Card size="small" title="Review">
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <pre className={styles.jsonBox}>
                  {JSON.stringify(selectedTask.review ?? {}, null, 2)}
                </pre>
                {canReview && (
                  <Space wrap>
                    <Button
                      data-testid="subagent-review-accept"
                      loading={reviewing === 'accepted'}
                      onClick={() => void handleReview('accepted')}
                      type="primary"
                    >
                      Accept
                    </Button>
                    <Button
                      data-testid="subagent-review-reject"
                      danger
                      loading={reviewing === 'rejected'}
                      onClick={() => void handleReview('rejected')}
                    >
                      Reject
                    </Button>
                    <Button
                      data-testid="subagent-review-needs-revision"
                      loading={reviewing === 'needs_revision'}
                      onClick={() => void handleReview('needs_revision')}
                    >
                      Needs revision
                    </Button>
                  </Space>
                )}
              </Space>
            </Card>

            <Card size="small" title="Object keys">
              <pre className={styles.jsonBox}>
                {JSON.stringify(selectedTask.object_keys ?? {}, null, 2)}
              </pre>
            </Card>
          </Space>
        ) : (
          <Text type="secondary">No task selected.</Text>
        )}
      </Drawer>
    </Space>
  )

  async function handleOpenTask(taskId: string) {
    setLoading(true)
    setErrorMessage(null)
    try {
      const detail = await getSubAgentTask(taskId, workspaceId)
      setSelectedTask(detail)
      setDrawerOpen(true)
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Failed to load SubAgent task.',
      )
    } finally {
      setLoading(false)
    }
  }

  async function handleReview(decision: SubAgentReviewDecision) {
    if (!selectedTask) {
      return
    }
    setReviewing(decision)
    setErrorMessage(null)
    try {
      await reviewSubAgentResult(
        selectedTask.task_id,
        {
          decision,
          reviewer_notes: `Reviewed from SubAgents page: ${decision}.`,
        },
        workspaceId,
      )
      const detail = await getSubAgentTask(selectedTask.task_id, workspaceId)
      setSelectedTask(detail)
      await loadTasks()
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Failed to review SubAgent result.',
      )
    } finally {
      setReviewing(null)
    }
  }

  async function handleCreateTask() {
    const validationError = validateCreateInput(createInput)
    if (validationError) {
      setErrorMessage(validationError)
      return
    }
    setCreating(true)
    setErrorMessage(null)
    try {
      const created = await createSubAgentTask(
        {
          ...createInput,
          agent_type: createInput.agent_type.trim(),
          expected_output: createInput.expected_output?.trim() || undefined,
          objective: createInput.objective.trim(),
          parent_run_id: createInput.parent_run_id.trim(),
          parent_thread_id: createInput.parent_thread_id?.trim() || undefined,
          write_scope:
            createInput.mode === 'write' ? createInput.write_scope : [],
        },
        workspaceId,
      )
      setSelectedTask(created)
      setDrawerOpen(true)
      setCreateInput(DEFAULT_CREATE_INPUT)
      await loadTasks()
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Failed to create SubAgent task.',
      )
    } finally {
      setCreating(false)
    }
  }
}

function ScopeTags({ label, values }: { label?: string; values: string[] }) {
  if (values.length === 0) {
    return (
      <Space wrap>
        {label && <Text type="secondary">{label}</Text>}
        <Tag>none</Tag>
      </Space>
    )
  }
  return (
    <Space wrap>
      {label && <Text type="secondary">{label}</Text>}
      {values.map((value) => (
        <Tag key={value}>{value}</Tag>
      ))}
    </Space>
  )
}

function splitLines(value: string) {
  return value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function validateCreateInput(input: SubAgentTaskInput) {
  if (!input.agent_type.trim()) {
    return 'agent_type is required.'
  }
  if (!input.parent_run_id?.trim()) {
    return 'parent_run_id is required.'
  }
  if (!input.objective.trim()) {
    return 'Objective is required.'
  }
  if (input.mode === 'write' && input.write_scope.length === 0) {
    return 'write_scope is required in write mode.'
  }
  if (!isTimeoutValid(input.timeout_ms)) {
    return `timeout_ms must be between ${MIN_TIMEOUT_MS} and ${MAX_TIMEOUT_MS}.`
  }
  if (!isTokenBudgetValid(input.token_budget)) {
    return `token_budget must be between ${MIN_TOKEN_BUDGET} and ${MAX_TOKEN_BUDGET}.`
  }
  return null
}

function isTimeoutValid(value: number) {
  return (
    Number.isInteger(value) &&
    value >= MIN_TIMEOUT_MS &&
    value <= MAX_TIMEOUT_MS
  )
}

function isTokenBudgetValid(value: number) {
  return (
    Number.isInteger(value) &&
    value >= MIN_TOKEN_BUDGET &&
    value <= MAX_TOKEN_BUDGET
  )
}

function resultJobId(result?: Record<string, unknown> | null) {
  if (!result) {
    return null
  }
  const createdJobId = result.created_job_id
  if (typeof createdJobId === 'string') {
    return createdJobId
  }
  const execution = result.execution
  if (isRecord(execution) && typeof execution.job_id === 'string') {
    return execution.job_id
  }
  return null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function statusColor(status: string) {
  if (status === 'completed' || status === 'reviewed') {
    return 'green'
  }
  if (status === 'failed') {
    return 'red'
  }
  if (status === 'queued' || status === 'running') {
    return 'blue'
  }
  return 'default'
}

function jobDetailHref(jobId: string) {
  return `/jobs?job_id=${encodeURIComponent(jobId)}`
}
