'use client'

import {
  Alert,
  Button,
  Card,
  Col,
  Drawer,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import { createStyles } from 'antd-style'
import { useCallback, useEffect, useRef, useState } from 'react'

import {
  approveMemory,
  createMemory,
  createMemorySyncJob,
  createMemorySnapshot,
  deleteMemory,
  getMemory,
  getMemorySyncState,
  listMemorySyncJobs,
  listMemories,
  patchMemory,
  rejectMemory,
  searchMemories,
} from '@/api/agentApiClient'
import type {
  JobSummary,
  MemoryDetailResponse,
  PatchMemoryInput,
  MemorySnapshotResponse,
  MemorySummary,
  MemorySyncStateResponse,
  UpsertMemoryInput,
  WorkspaceId,
} from '@/api/schemas/workspace'

const { Text } = Typography

const TERMINAL_JOB_STATUSES = new Set([
  'succeeded',
  'partial_success',
  'failed',
  'cancelled',
  'unknown_outcome',
])

const MEMORY_TYPES = [
  'user_profile',
  'user_preference',
  'project_fact',
  'project_rule',
  'tool_usage_preference',
  'correction',
  'safety_boundary',
  'relationship_fact',
]

const useStyles = createStyles(({ css, token }) => ({
  resultBox: css`
    border: 1px solid ${token.colorBorderSecondary};
    border-radius: 6px;
    padding: 12px;
  `,
  syncBar: css`
    align-items: center;
    border: 1px solid ${token.colorBorderSecondary};
    border-radius: 6px;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: space-between;
    padding: 8px 12px;
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

interface MemoryPanelProps {
  workspaceId: WorkspaceId
}

interface SnapshotFormValues {
  query?: string
  thread_id: string
}

interface MemoryEditFormValues {
  confidence?: number
  content: string
  enabled_for_model_context: boolean
  field?: string | null
  scope: MemorySummary['scope']
  status: string
  summary: string
  value?: string | null
}

type EditableMemoryDetail = MemorySummary | MemoryDetailResponse

export function MemoryPanel({ workspaceId }: MemoryPanelProps) {
  const { styles } = useStyles()
  const [memoryEditForm] = Form.useForm<MemoryEditFormValues>()
  const [memoryForm] = Form.useForm<UpsertMemoryInput>()
  const [snapshotForm] = Form.useForm<SnapshotFormValues>()
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailSaving, setDetailSaving] = useState(false)
  const [includeDeleted, setIncludeDeleted] = useState(false)
  const [latestSnapshot, setLatestSnapshot] = useState<MemorySnapshotResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [memories, setMemories] = useState<MemorySummary[]>([])
  const [memorySyncJobs, setMemorySyncJobs] = useState<JobSummary[]>([])
  const [memorySyncLoading, setMemorySyncLoading] = useState(false)
  const [memorySyncMessage, setMemorySyncMessage] = useState<string | null>(null)
  const [memorySyncState, setMemorySyncState] = useState<MemorySyncStateResponse | null>(null)
  const [query, setQuery] = useState('')
  const [searchHits, setSearchHits] = useState<Record<string, unknown>[]>([])
  const [selectedMemory, setSelectedMemory] = useState<EditableMemoryDetail | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const detailRequestRef = useRef(0)

  const pendingMemories = memories.filter(
    (memory) => memory.requires_approval && memory.status === 'pending_approval',
  )
  const activeMemorySyncJobs = memorySyncJobs.filter(
    (job) => !TERMINAL_JOB_STATUSES.has(job.status),
  )
  const latestMemorySyncJob = memorySyncJobs[0]

  const loadMemories = useCallback(async () => {
    setLoading(true)
    setErrorMessage(null)
    try {
      const response = await listMemories(workspaceId, { include_deleted: includeDeleted })
      setMemories(response.memories)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load memories.')
    } finally {
      setLoading(false)
    }
  }, [includeDeleted, workspaceId])

  const loadMemorySyncJobs = useCallback(async () => {
    setMemorySyncLoading(true)
    setErrorMessage(null)
    try {
      const response = await listMemorySyncJobs(workspaceId, { limit: 10 })
      setMemorySyncJobs(response.jobs)
      setMemorySyncState(await getMemorySyncState(workspaceId))
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load memory sync jobs.')
    } finally {
      setMemorySyncLoading(false)
    }
  }, [workspaceId])

  useEffect(() => {
    void loadMemories()
  }, [loadMemories])

  useEffect(() => {
    void loadMemorySyncJobs()
  }, [loadMemorySyncJobs])

  const columns = [
    {
      dataIndex: 'summary',
      key: 'summary',
      title: 'Memory',
      render: (_: unknown, record: MemorySummary) => (
        <Space direction="vertical" size={2}>
          <Text>{record.summary}</Text>
          <Text code>{record.memory_id}</Text>
        </Space>
      ),
    },
    {
      key: 'type',
      title: 'Type',
      render: (_: unknown, record: MemorySummary) => (
        <Space wrap>
          <Tag color={typeColor(record.type)}>{record.type}</Tag>
          <Tag>{record.scope}</Tag>
          {record.field && <Tag>{record.field}</Tag>}
          {record.requires_approval && <Tag color="orange">review</Tag>}
        </Space>
      ),
    },
    {
      key: 'model',
      title: 'Model context',
      render: (_: unknown, record: MemorySummary) => (
        <Space direction="vertical" size={4}>
          <Tag color={record.enabled_for_model_context ? 'green' : 'default'}>
            {record.enabled_for_model_context ? 'enabled' : 'disabled'}
          </Tag>
          <Text type="secondary">confidence {record.confidence}</Text>
        </Space>
      ),
    },
    {
      dataIndex: 'status',
      key: 'status',
      title: 'Status',
      render: (status: string) => <Tag color={statusColor(status)}>{status}</Tag>,
    },
    {
      key: 'actions',
      title: 'Actions',
      render: (_: unknown, record: MemorySummary) => (
        <Space wrap>
          <Button
            data-testid={`memory-edit-${record.memory_id}`}
            onClick={() => void handleOpenMemoryDetail(record)}
          >
            Details
          </Button>
          <Popconfirm
            cancelText="Cancel"
            description={memoryToggleConfirmDescription(record)}
            okText={
              record.enabled_for_model_context && record.status === 'active'
                ? 'Disable memory'
                : 'Enable memory'
            }
            onConfirm={() => void handleToggleMemory(record)}
            title={
              record.enabled_for_model_context && record.status === 'active'
                ? 'Disable memory injection?'
                : 'Enable memory injection?'
            }
          >
            <Button data-testid={`memory-toggle-${record.memory_id}`} loading={submitting}>
              {record.enabled_for_model_context && record.status === 'active'
                ? 'Disable'
                : 'Enable'}
            </Button>
          </Popconfirm>
          <Popconfirm
            cancelText="Cancel"
            description="Deleted memories are removed from model context and sync state until restored or recreated."
            okText="Delete memory"
            onConfirm={() => void handleDeleteMemory(record.memory_id)}
            title="Delete this memory?"
          >
            <Button
              danger
              data-testid={`memory-delete-${record.memory_id}`}
              loading={submitting}
            >
              Delete
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <div className={styles.toolbar}>
        <Space wrap>
          <Input
            allowClear
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search memory"
            style={{ width: 260 }}
            value={query}
          />
          <Button onClick={() => void handleSearch()}>Search</Button>
          <Switch
            checked={includeDeleted}
            checkedChildren="Deleted"
            onChange={setIncludeDeleted}
            unCheckedChildren="Active"
          />
        </Space>
        <Button loading={loading || memorySyncLoading} onClick={() => void handleRefresh()}>
          Refresh
        </Button>
      </div>

      <div aria-live="polite" className={styles.syncBar}>
        <Space wrap>
          <Tag color={memorySyncStatusColor(latestMemorySyncJob?.status)}>
            Memory sync {memorySyncStatusLabel(activeMemorySyncJobs, latestMemorySyncJob)}
          </Tag>
          {activeMemorySyncJobs.length > 0 && (
            <Text type="secondary">{activeMemorySyncJobs.length} queued/running</Text>
          )}
          {memorySyncState && (
            <Text type="secondary">
              {memorySyncState.pending_targets.length} pending targets
            </Text>
          )}
          {latestMemorySyncJob && (
            <Text type="secondary">
              latest {latestMemorySyncJob.status} at {formatDate(latestMemorySyncJob.updated_at)}
            </Text>
          )}
        </Space>
        <Button
          disabled={activeMemorySyncJobs.length > 0}
          loading={memorySyncLoading}
          onClick={() => void handleQueueMemorySync()}
          size="small"
        >
          Queue sync
        </Button>
      </div>

      {errorMessage && <Alert message={errorMessage} showIcon type="error" />}
      {memorySyncMessage && <Alert message={memorySyncMessage} showIcon type="success" />}

      <Row gutter={[16, 16]}>
        <Col lg={16} xs={24}>
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Card title="Memory review">
              <Table<MemorySummary>
                className={styles.table}
                columns={[
                  {
                    dataIndex: 'summary',
                    key: 'summary',
                    title: 'Candidate',
                    render: (_: unknown, record: MemorySummary) => (
                      <Space direction="vertical" size={2}>
                        <Text>{record.summary}</Text>
                        <Text code>{record.memory_id}</Text>
                      </Space>
                    ),
                  },
                  {
                    key: 'type',
                    title: 'Type',
                    render: (_: unknown, record: MemorySummary) => (
                      <Space wrap>
                        <Tag color={typeColor(record.type)}>{record.type}</Tag>
                        <Tag>{record.scope}</Tag>
                        {record.field && <Tag>{record.field}</Tag>}
                      </Space>
                    ),
                  },
                  {
                    key: 'actions',
                    title: 'Review',
                    render: (_: unknown, record: MemorySummary) => (
                      <Space wrap>
                        <Button
                          data-testid={`memory-approve-${record.memory_id}`}
                          loading={submitting}
                          onClick={() => void handleApproveMemory(record)}
                          type="primary"
                        >
                          Approve
                        </Button>
                        <Popconfirm
                          cancelText="Cancel"
                          description="Rejected memory candidates are not injected into future model context."
                          okText="Reject memory"
                          onConfirm={() => void handleRejectMemory(record.memory_id)}
                          title="Reject this memory candidate?"
                        >
                          <Button
                            danger
                            data-testid={`memory-reject-${record.memory_id}`}
                            loading={submitting}
                          >
                            Reject
                          </Button>
                        </Popconfirm>
                      </Space>
                    ),
                  },
                ]}
                dataSource={pendingMemories}
                loading={loading}
                locale={{ emptyText: 'No pending memory candidates' }}
                pagination={false}
                rowKey="memory_id"
              />
            </Card>

            <Card title="Memories">
              <Table<MemorySummary>
                className={styles.table}
                columns={columns}
                dataSource={memories}
                loading={loading}
                pagination={{ pageSize: 8 }}
                rowKey="memory_id"
              />
            </Card>
          </Space>
        </Col>

        <Col lg={8} xs={24}>
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Card title="Save memory">
              <Form
                form={memoryForm}
                initialValues={{
                  confidence: 1,
                  enabled_for_model_context: true,
                  type: 'user_preference',
                }}
                layout="vertical"
                onFinish={handleCreateMemory}
              >
                <Form.Item label="Type" name="type" rules={[{ required: true }]}>
                  <Select options={MEMORY_TYPES.map((type) => ({ label: type, value: type }))} />
                </Form.Item>
                <Form.Item label="Scope" name="scope">
                  <Select
                    allowClear
                    options={[
                      { label: 'global', value: 'global' },
                      { label: 'workspace', value: 'workspace' },
                    ]}
                  />
                </Form.Item>
                <Form.Item label="Field" name="field">
                  <Input placeholder="answer_style" />
                </Form.Item>
                <Form.Item label="Value" name="value">
                  <Input placeholder="concise" />
                </Form.Item>
                <Form.Item label="Summary" name="summary" rules={[{ required: true }]}>
                  <Input placeholder="Memory summary" />
                </Form.Item>
                <Form.Item label="Content" name="content" rules={[{ required: true }]}>
                  <Input.TextArea
                    autoSize={{ maxRows: 5, minRows: 3 }}
                    placeholder="Full memory content"
                  />
                </Form.Item>
                <Form.Item label="Confidence" name="confidence">
                  <InputNumber max={1} min={0} step={0.05} style={{ width: '100%' }} />
                </Form.Item>
                <Form.Item
                  label="Model context"
                  name="enabled_for_model_context"
                  valuePropName="checked"
                >
                  <Switch />
                </Form.Item>
                <Button htmlType="submit" loading={submitting}>
                  Save memory
                </Button>
              </Form>
            </Card>

            <Card title="Snapshot">
              <Form form={snapshotForm} layout="vertical" onFinish={handleCreateSnapshot}>
                <Form.Item label="Thread" name="thread_id" rules={[{ required: true }]}>
                  <Input placeholder="thread_id" />
                </Form.Item>
                <Form.Item label="Query" name="query">
                  <Input placeholder="optional query" />
                </Form.Item>
                <Button htmlType="submit" loading={submitting}>
                  Snapshot
                </Button>
              </Form>
              {latestSnapshot && (
                <div className={styles.resultBox}>
                  <Space direction="vertical" size={4}>
                    <Text code>{latestSnapshot.memory_snapshot_id}</Text>
                    <Text>Included: {latestSnapshot.included_memory_ids.length}</Text>
                  </Space>
                </div>
              )}
            </Card>
          </Space>
        </Col>
      </Row>

      <Drawer
        destroyOnHidden
        extra={
          <Space>
            <Button onClick={handleCloseMemoryDetail}>Cancel</Button>
            <Button
              disabled={!selectedMemory || !isMemoryDetail(selectedMemory)}
              loading={detailSaving}
              onClick={() => void memoryEditForm.submit()}
              type="primary"
            >
              Save changes
            </Button>
          </Space>
        }
        loading={detailLoading}
        onClose={handleCloseMemoryDetail}
        open={detailOpen}
        title="Memory detail"
        width={560}
      >
        {selectedMemory && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Space direction="vertical" size={4}>
              <Text code>{selectedMemory.memory_id}</Text>
              <Space wrap>
                <Tag color={typeColor(selectedMemory.type)}>{selectedMemory.type}</Tag>
                <Tag>{selectedMemory.scope}</Tag>
                <Tag color={statusColor(selectedMemory.status)}>{selectedMemory.status}</Tag>
                <Text type="secondary">updated {formatDate(selectedMemory.updated_at)}</Text>
              </Space>
            </Space>
            {!isMemoryDetail(selectedMemory) && !detailLoading && (
              <Alert message="Full memory detail is unavailable." showIcon type="warning" />
            )}
            <Form
              form={memoryEditForm}
              layout="vertical"
              onFinish={handleSaveMemoryDetail}
            >
              <Form.Item label="Summary" name="summary" rules={[{ required: true }]}>
                <Input aria-label="Summary" placeholder="Memory summary" />
              </Form.Item>
              <Form.Item label="Content" name="content" rules={[{ required: true }]}>
                <Input.TextArea
                  aria-label="Content"
                  autoSize={{ maxRows: 8, minRows: 4 }}
                  placeholder="Full memory content"
                />
              </Form.Item>
              <Form.Item label="Value" name="value">
                <Input.TextArea
                  aria-label="Value"
                  autoSize={{ maxRows: 4, minRows: 2 }}
                  placeholder="optional canonical value"
                />
              </Form.Item>
              <Row gutter={12}>
                <Col sm={12} xs={24}>
                  <Form.Item label="Field" name="field">
                    <Input allowClear aria-label="Field" placeholder="answer_style" />
                  </Form.Item>
                </Col>
                <Col sm={12} xs={24}>
                  <Form.Item label="Scope" name="scope" rules={[{ required: true }]}>
                    <Select
                      aria-label="Scope"
                      options={[
                        { label: 'global', value: 'global' },
                        { label: 'workspace', value: 'workspace' },
                      ]}
                    />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={12}>
                <Col sm={12} xs={24}>
                  <Form.Item label="Status" name="status" rules={[{ required: true }]}>
                    <Select
                      aria-label="Status"
                      options={memoryStatusOptions(selectedMemory.status)}
                    />
                  </Form.Item>
                </Col>
                <Col sm={12} xs={24}>
                  <Form.Item label="Confidence" name="confidence">
                    <InputNumber
                      aria-label="Confidence"
                      max={1}
                      min={0}
                      step={0.05}
                      style={{ width: '100%' }}
                    />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item
                label="Model context"
                name="enabled_for_model_context"
                valuePropName="checked"
              >
                <Switch aria-label="Model context" />
              </Form.Item>
            </Form>
          </Space>
        )}
      </Drawer>

      {searchHits.length > 0 && (
        <Card title="Search hits">
          <Table<Record<string, unknown>>
            dataSource={searchHits}
            pagination={false}
            rowKey={(record) => String(record.memory_id)}
            columns={[
              {
                dataIndex: 'summary',
                key: 'summary',
                title: 'Summary',
              },
              {
                dataIndex: 'type',
                key: 'type',
                title: 'Type',
                render: (type: string) => <Tag color={typeColor(type)}>{type}</Tag>,
              },
              {
                dataIndex: 'score',
                key: 'score',
                title: 'Score',
              },
            ]}
          />
        </Card>
      )}
    </Space>
  )

  async function handleCreateMemory(values: UpsertMemoryInput) {
    setSubmitting(true)
    setErrorMessage(null)
    try {
      await createMemory(values, workspaceId)
      memoryForm.resetFields()
      await Promise.all([loadMemories(), loadMemorySyncJobs()])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to save memory.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleRefresh() {
    await Promise.all([loadMemories(), loadMemorySyncJobs()])
  }

  function handleCloseMemoryDetail() {
    detailRequestRef.current += 1
    setDetailOpen(false)
    setDetailLoading(false)
    setSelectedMemory(null)
    memoryEditForm.resetFields()
  }

  async function handleOpenMemoryDetail(memory: MemorySummary) {
    const requestId = detailRequestRef.current + 1
    detailRequestRef.current = requestId
    setErrorMessage(null)
    setDetailOpen(true)
    setSelectedMemory(memory)
    memoryEditForm.setFieldsValue(memoryToFormValues(memory))
    setDetailLoading(true)
    try {
      const detail = await getMemory(memory.memory_id, workspaceId)
      if (detailRequestRef.current !== requestId) {
        return
      }
      setSelectedMemory(detail)
      memoryEditForm.setFieldsValue(memoryToFormValues(detail))
    } catch (error) {
      if (detailRequestRef.current !== requestId) {
        return
      }
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load memory detail.')
    } finally {
      if (detailRequestRef.current === requestId) {
        setDetailLoading(false)
      }
    }
  }

  async function handleSaveMemoryDetail(values: MemoryEditFormValues) {
    if (!selectedMemory || !isMemoryDetail(selectedMemory)) {
      return
    }

    setDetailSaving(true)
    setErrorMessage(null)
    try {
      const payload: PatchMemoryInput = {
        content: values.content,
        enabled_for_model_context: values.enabled_for_model_context,
        summary: values.summary,
        value: normalizeNullableString(values.value),
      }
      const nextField = normalizeNullableString(values.field)
      if (nextField !== (selectedMemory.field || null)) {
        payload.field = nextField
      }
      if (values.scope !== selectedMemory.scope) {
        payload.scope = values.scope
      }
      const nextStatus = patchableMemoryStatus(values.status)
      if (nextStatus && nextStatus !== selectedMemory.status) {
        payload.status = nextStatus
      }
      if (values.confidence !== selectedMemory.confidence) {
        payload.confidence = values.confidence
      }
      const updated = await patchMemory(
        selectedMemory.memory_id,
        payload,
        workspaceId,
      )
      setSelectedMemory(updated)
      memoryEditForm.setFieldsValue(memoryToFormValues(updated))
      await Promise.all([loadMemories(), loadMemorySyncJobs()])
      setDetailOpen(false)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to update memory.')
    } finally {
      setDetailSaving(false)
    }
  }

  async function handleQueueMemorySync() {
    setMemorySyncLoading(true)
    setErrorMessage(null)
    setMemorySyncMessage(null)
    try {
      const job = await createMemorySyncJob({ limit: 50 }, workspaceId)
      setMemorySyncJobs((current) => [
        job,
        ...current.filter((item) => item.job_id !== job.job_id),
      ])
      await loadMemorySyncJobs()
      setMemorySyncMessage(`Memory sync job queued: ${job.status}`)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to queue memory sync.')
    } finally {
      setMemorySyncLoading(false)
    }
  }

  async function handleCreateSnapshot(values: SnapshotFormValues) {
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const snapshot = await createMemorySnapshot(
        values.thread_id,
        values.query || undefined,
        workspaceId,
      )
      setLatestSnapshot(snapshot)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to create snapshot.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleDeleteMemory(memoryId: string) {
    setSubmitting(true)
    setErrorMessage(null)
    try {
      await deleteMemory(memoryId, workspaceId)
      await Promise.all([loadMemories(), loadMemorySyncJobs()])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to delete memory.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleApproveMemory(memory: MemorySummary) {
    setSubmitting(true)
    setErrorMessage(null)
    try {
      await approveMemory(memory.memory_id, workspaceId)
      await Promise.all([loadMemories(), loadMemorySyncJobs()])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to approve memory.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleRejectMemory(memoryId: string) {
    setSubmitting(true)
    setErrorMessage(null)
    try {
      await rejectMemory(memoryId, workspaceId)
      await Promise.all([loadMemories(), loadMemorySyncJobs()])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to reject memory.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleSearch() {
    if (!query.trim()) {
      setSearchHits([])
      return
    }
    setLoading(true)
    setErrorMessage(null)
    try {
      const response = await searchMemories(query.trim(), [], workspaceId)
      setSearchHits(response.hits)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to search memories.')
    } finally {
      setLoading(false)
    }
  }

  async function handleToggleMemory(memory: MemorySummary) {
    const enabled = !(memory.enabled_for_model_context && memory.status === 'active')
    setSubmitting(true)
    setErrorMessage(null)
    try {
      await patchMemory(
        memory.memory_id,
        {
          enabled_for_model_context: enabled,
          status: enabled ? 'active' : 'disabled',
        },
        workspaceId,
      )
      await Promise.all([loadMemories(), loadMemorySyncJobs()])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to update memory.')
    } finally {
      setSubmitting(false)
    }
  }
}

function isMemoryDetail(memory: EditableMemoryDetail): memory is MemoryDetailResponse {
  return 'content' in memory
}

function memoryToFormValues(memory: EditableMemoryDetail): MemoryEditFormValues {
  return {
    confidence: memory.confidence,
    content: isMemoryDetail(memory) ? memory.content : '',
    enabled_for_model_context: memory.enabled_for_model_context,
    field: memory.field || null,
    scope: memory.scope,
    status: memory.status,
    summary: memory.summary,
    value: isMemoryDetail(memory) ? memory.value || null : null,
  }
}

function memoryStatusOptions(currentStatus: string) {
  return Array.from(new Set([currentStatus, 'active', 'disabled'])).map((status) => ({
    label: status,
    value: status,
  }))
}

function patchableMemoryStatus(status: string): PatchMemoryInput['status'] | null {
  return status === 'active' || status === 'disabled' ? status : null
}

function normalizeNullableString(value?: string | null) {
  const normalized = value?.trim()
  return normalized ? normalized : null
}

function memoryToggleConfirmDescription(memory: MemorySummary) {
  if (memory.enabled_for_model_context && memory.status === 'active') {
    return 'This memory will stop being included in future model-visible memory snapshots.'
  }
  return 'This memory can be included in future model-visible memory snapshots.'
}

function statusColor(status: string) {
  if (status === 'active') {
    return 'green'
  }
  if (status === 'deleted') {
    return 'red'
  }
  if (status === 'pending_approval') {
    return 'orange'
  }
  if (status === 'rejected') {
    return 'red'
  }
  return 'default'
}

function memorySyncStatusLabel(activeJobs: JobSummary[], latestJob?: JobSummary) {
  if (activeJobs.length > 0) {
    const running = activeJobs.find((job) => job.status === 'running')
    return running ? running.status : activeJobs[0].status
  }
  return latestJob ? `last ${latestJob.status}` : 'not queued'
}

function memorySyncStatusColor(status?: string) {
  if (status === 'succeeded' || status === 'partial_success') {
    return 'green'
  }
  if (status === 'failed' || status === 'unknown_outcome') {
    return 'red'
  }
  if (status === 'running' || status === 'recovering') {
    return 'blue'
  }
  if (status === 'queued' || status === 'created' || status === 'waiting_retry') {
    return 'orange'
  }
  return 'default'
}

function formatDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString()
}

function typeColor(type: string) {
  if (type === 'user_profile') {
    return 'blue'
  }
  if (type === 'user_preference') {
    return 'green'
  }
  if (type.startsWith('project_')) {
    return 'purple'
  }
  return 'default'
}
