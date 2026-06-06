'use client'

import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  List,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Timeline,
  Typography,
} from 'antd'
import { createStyles } from 'antd-style'
import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'

import {
  cancelJob,
  getLogArtifact,
  getJob,
  getJobWorkerStatus,
  listJobEvents,
  listJobs,
  processNextJob,
  rebuildJobsIndex,
  recoverStaleJobs,
  retryJob,
  startJobWorker,
  stopJobWorker,
} from '@/api/agentApiClient'
import type {
  JobEvent,
  JobSummary,
  JobWorkerStatusResponse,
  LogArtifactResponse,
  WorkspaceId,
} from '@/api/schemas/workspace'
import { connectJobEventStream } from '@/api/jobEventStream'
import {
  downloadLogArtifact,
  formatArtifactBytes,
  isSupportedLogArtifactKey,
  logArtifactPreviewContent,
} from '@/components/logs/logArtifactUtils'
import { useJobStore } from '@/stores/useJobStore'

const { Text, Title } = Typography

const TERMINAL_STATUSES = new Set([
  'succeeded',
  'partial_success',
  'failed',
  'cancelled',
  'unknown_outcome',
])

const useStyles = createStyles(({ css, token }) => ({
  artifactPreview: css`
    background: ${token.colorFillQuaternary};
    border: 1px solid ${token.colorBorderSecondary};
    border-radius: 6px;
    max-height: 460px;
    overflow: auto;
    padding: 12px;
    white-space: pre-wrap;
    word-break: break-word;
  `,
  detailJson: css`
    background: ${token.colorFillQuaternary};
    border: 1px solid ${token.colorBorderSecondary};
    border-radius: 6px;
    max-height: 220px;
    overflow: auto;
    padding: 12px;
    white-space: pre-wrap;
    word-break: break-word;
  `,
  filters: css`
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: space-between;
    margin-bottom: 12px;
  `,
  workerBar: css`
    align-items: center;
    border: 1px solid ${token.colorBorderSecondary};
    border-radius: 8px;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: space-between;
    padding: 12px;
  `,
  table: css`
    .ant-table-cell {
      vertical-align: top;
    }
  `,
}))

interface JobTaskCenterProps {
  workspaceId: WorkspaceId
}

export function JobTaskCenter({ workspaceId }: JobTaskCenterProps) {
  const { styles } = useStyles()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [jobTypeFilter, setJobTypeFilter] = useState<string | undefined>()
  const [loading, setLoading] = useState(false)
  const [maintenanceMessage, setMaintenanceMessage] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string | undefined>()
  const [workerLoading, setWorkerLoading] = useState(false)
  const [workerStatus, setWorkerStatus] = useState<JobWorkerStatusResponse | null>(null)
  const [artifactLoadingKey, setArtifactLoadingKey] = useState<string | null>(null)
  const [artifactPreview, setArtifactPreview] =
    useState<LogArtifactResponse | null>(null)
  const [artifactPreviewOpen, setArtifactPreviewOpen] = useState(false)
  const [urlJobId, setUrlJobId] = useState<string | null>(null)
  const [openedUrlJobId, setOpenedUrlJobId] = useState<string | null>(null)

  const activeJobId = useJobStore((state) => state.activeJobId)
  const activeJob = useJobStore((state) =>
    state.activeJobId ? state.jobsById[state.activeJobId] : undefined,
  )
  const activeDetail = useJobStore((state) =>
    state.activeJobId ? state.jobDetailsById[state.activeJobId] : undefined,
  )
  const applyJobEvent = useJobStore((state) => state.applyJobEvent)
  const eventsByJobId = useJobStore((state) => state.eventsByJobId)
  const jobOrder = useJobStore((state) => state.jobOrder)
  const jobsById = useJobStore((state) => state.jobsById)
  const lastEventIdByJob = useJobStore((state) => state.lastEventIdByJob)
  const setActiveJobId = useJobStore((state) => state.setActiveJobId)
  const setJobDetail = useJobStore((state) => state.setJobDetail)
  const setJobEvents = useJobStore((state) => state.setJobEvents)
  const setJobs = useJobStore((state) => state.setJobs)
  const setStreamStatus = useJobStore((state) => state.setStreamStatus)

  const jobs = useMemo(
    () => jobOrder.map((jobId) => jobsById[jobId]).filter(Boolean),
    [jobOrder, jobsById],
  )
  const activeEvents = useMemo(
    () => (activeJobId ? eventsByJobId[activeJobId] ?? [] : []),
    [activeJobId, eventsByJobId],
  )
  const latestAttentionEvent = useMemo(
    () => latestAttentionJobEvent(activeEvents),
    [activeEvents],
  )
  const activeArtifactEntries = useMemo(
    () => artifactEntriesFromLeafState(activeDetail?.leaf_state),
    [activeDetail],
  )

  const loadJobs = useCallback(async () => {
    setLoading(true)
    setErrorMessage(null)
    try {
      const response = await listJobs(workspaceId, {
        job_type: jobTypeFilter,
        limit: 100,
        status: statusFilter,
      })
      setJobs(response.jobs)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setLoading(false)
    }
  }, [jobTypeFilter, setJobs, statusFilter, workspaceId])

  const loadWorkerStatus = useCallback(async () => {
    setErrorMessage(null)
    try {
      setWorkerStatus(await getJobWorkerStatus(workspaceId))
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : String(error))
    }
  }, [workspaceId])

  const openJob = useCallback(
    async (jobId: string) => {
      setActiveJobId(jobId)
      setUrlJobId(jobId)
      updateJobIdInUrl(jobId)
      setDrawerOpen(true)
      setErrorMessage(null)
      try {
        const [detail, events] = await Promise.all([
          getJob(jobId, workspaceId),
          listJobEvents(jobId, { limit: 200 }, workspaceId),
        ])
        setJobDetail(detail)
        setJobEvents(jobId, events.events)
      } catch (error) {
        setActiveJobId(null)
        setDrawerOpen(false)
        setUrlJobId(null)
        updateJobIdInUrl(null)
        setErrorMessage(error instanceof Error ? error.message : String(error))
      }
    },
    [setActiveJobId, setJobDetail, setJobEvents, workspaceId],
  )

  useEffect(() => {
    void loadJobs()
  }, [loadJobs])

  useEffect(() => {
    void loadWorkerStatus()
  }, [loadWorkerStatus])

  useEffect(() => {
    setUrlJobId(new URLSearchParams(window.location.search).get('job_id'))
  }, [])

  useEffect(() => {
    if (!urlJobId || urlJobId === openedUrlJobId) {
      return
    }
    setOpenedUrlJobId(urlJobId)
    void openJob(urlJobId)
  }, [openJob, openedUrlJobId, urlJobId])

  useEffect(() => {
    if (!drawerOpen || !activeJobId || TERMINAL_STATUSES.has(activeJob?.status ?? '')) {
      return undefined
    }
    setStreamStatus(activeJobId, 'connecting')
    const connection = connectJobEventStream({
      afterEventId: lastEventIdByJob[activeJobId] || activeJob?.last_event_id,
      jobId: activeJobId,
      onClose: () => setStreamStatus(activeJobId, 'closed'),
      onError: (error) => {
        setErrorMessage(error.message)
        setStreamStatus(activeJobId, 'error')
      },
      onEvent: (event) => {
        setStreamStatus(activeJobId, 'open')
        applyJobEvent(event)
      },
      workspaceId,
    })

    return () => connection.close()
  }, [
    activeJob?.last_event_id,
    activeJob?.status,
    activeJobId,
    applyJobEvent,
    drawerOpen,
    lastEventIdByJob,
    setStreamStatus,
    workspaceId,
  ])

  const columns = [
    {
      dataIndex: 'title',
      key: 'title',
      title: 'Job',
      render: (_: unknown, record: JobSummary) => (
        <Space direction="vertical" size={2}>
          <Button onClick={() => void openJob(record.job_id)} type="link">
            {record.title}
          </Button>
          <Text code>{record.job_type}</Text>
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
      dataIndex: 'current_stage',
      key: 'stage',
      title: 'Stage',
      render: (_: unknown, record: JobSummary) => (
        <Space direction="vertical" size={4} style={{ minWidth: 180 }}>
          <Text>{record.current_stage ?? 'queued'}</Text>
          <Progress percent={Math.round(record.progress_percent)} size="small" />
        </Space>
      ),
    },
    {
      dataIndex: 'target_scope',
      key: 'target',
      title: 'Target',
      render: (target: Record<string, string>) => (
        <Space wrap>
          {Object.entries(target).map(([key, value]) => (
            <Tag key={key}>
              {key}:{value}
            </Tag>
          ))}
        </Space>
      ),
    },
    {
      dataIndex: 'updated_at',
      key: 'updated_at',
      title: 'Updated',
      render: (value: string) => <Text type="secondary">{formatDate(value)}</Text>,
    },
  ]

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <div className={styles.workerBar}>
        <Space wrap>
          <Tag color={workerStatus?.running ? 'green' : 'default'}>
            worker {workerStatus?.running ? 'running' : 'stopped'}
          </Tag>
          <Text type="secondary">processed: {workerStatus?.processed_count ?? 0}</Text>
          <Text type="secondary">ticks: {workerStatus?.tick_count ?? 0}</Text>
          {workerStatus?.last_tick_at && (
            <Text type="secondary">last tick: {formatDate(workerStatus.last_tick_at)}</Text>
          )}
          {workerStatus?.last_error && (
            <Tag color="red">
              {workerStatus.last_error.error_type ?? 'worker_error'}
            </Tag>
          )}
        </Space>
        <Space wrap>
          <Button
            disabled={workerStatus?.running}
            loading={workerLoading}
            onClick={() => void handleStartWorker()}
          >
            Start worker
          </Button>
          <Popconfirm
            cancelText="Cancel"
            description="Running jobs are not force-killed, but no new job will be claimed after the worker stops."
            disabled={!workerStatus?.running}
            okText="Stop worker"
            onConfirm={() => void handleStopWorker()}
            title="Stop the Job worker?"
          >
            <Button disabled={!workerStatus?.running} loading={workerLoading}>
              Stop worker
            </Button>
          </Popconfirm>
          <Button loading={workerLoading} onClick={() => void handleProcessNextJob()}>
            Process next
          </Button>
          <Popconfirm
            cancelText="Cancel"
            description="Stale running jobs may move to recovering or unknown_outcome after recovery."
            okText="Recover jobs"
            onConfirm={() => void handleRecoverStaleJobs()}
            title="Recover stale running jobs?"
          >
            <Button loading={workerLoading}>Recover stale running jobs</Button>
          </Popconfirm>
          <Popconfirm
            cancelText="Cancel"
            description="The jobs index will be rebuilt from job manifests in ObjectStore."
            okText="Rebuild index"
            onConfirm={() => void handleRebuildJobsIndex()}
            title="Rebuild the jobs index?"
          >
            <Button loading={workerLoading}>Rebuild jobs index</Button>
          </Popconfirm>
          <Button loading={workerLoading} onClick={() => void loadWorkerStatus()}>
            Worker status
          </Button>
        </Space>
      </div>

      <div className={styles.filters}>
        <Space wrap>
          <Select
            allowClear
            onChange={setStatusFilter}
            options={[
              'queued',
              'running',
              'succeeded',
              'partial_success',
              'failed',
              'cancelled',
              'unknown_outcome',
              'recovering',
            ].map((status) => ({ label: status, value: status }))}
            placeholder="Status"
            style={{ width: 180 }}
            value={statusFilter}
          />
          <Select
            allowClear
            onChange={setJobTypeFilter}
            options={[
              'document_ingestion_job',
              'embedding_reindex_job',
              'graph_build_job',
              'memory_sync_job',
              'subagent_execution_job',
              'mcp_capability_refresh_job',
              'diagnostic_bundle_job',
              'log_archive_job',
              'log_shipper_job',
            ].map((jobType) => ({ label: jobType, value: jobType }))}
            placeholder="Job type"
            style={{ width: 260 }}
            value={jobTypeFilter}
          />
        </Space>
        <Button loading={loading} onClick={() => void loadJobs()}>
          Refresh
        </Button>
      </div>

      {errorMessage && <Alert message={errorMessage} showIcon type="error" />}
      {maintenanceMessage && <Alert message={maintenanceMessage} showIcon type="info" />}

      <Table<JobSummary>
        className={styles.table}
        columns={columns}
        dataSource={jobs}
        loading={loading}
        pagination={{ pageSize: 10 }}
        rowKey="job_id"
      />

      <Drawer
        destroyOnHidden
        extra={
          activeJobId && activeJob ? (
            <Space>
              <Popconfirm
                cancelText="Cancel"
                description="This queues a follow-up attempt from the persisted job state and may create new job events."
                disabled={!canRetryOrRecover(activeJob.status)}
                okText={primaryActionLabel(activeJob.status)}
                onConfirm={() => void handleRetry(activeJobId)}
                title={`${primaryActionLabel(activeJob.status)} this job?`}
              >
                <Button disabled={!canRetryOrRecover(activeJob.status)}>
                  {primaryActionLabel(activeJob.status)}
                </Button>
              </Popconfirm>
              <Popconfirm
                cancelText="Cancel"
                description="The job manifest will be marked cancelled. Already persisted job events remain immutable."
                disabled={!canCancel(activeJob.status)}
                okText="Cancel job"
                onConfirm={() => void handleCancel(activeJobId)}
                title="Cancel this job?"
              >
                <Button danger disabled={!canCancel(activeJob.status)}>
                  Cancel
                </Button>
              </Popconfirm>
            </Space>
          ) : null
        }
        onClose={() => {
          setDrawerOpen(false)
          setActiveJobId(null)
          setUrlJobId(null)
          updateJobIdInUrl(null)
        }}
        open={drawerOpen}
        title={activeJob?.title ?? 'Job detail'}
        width={720}
      >
        {activeJob && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions
              bordered
              column={1}
              items={[
                { key: 'job_id', label: 'Job ID', children: activeJob.job_id },
                { key: 'job_type', label: 'Type', children: activeJob.job_type },
                {
                  key: 'status',
                  label: 'Status',
                  children: <Tag color={statusColor(activeJob.status)}>{activeJob.status}</Tag>,
                },
                {
                  key: 'stage',
                  label: 'Stage',
                  children: activeJob.current_stage ?? 'queued',
                },
              ]}
              size="small"
            />

            {latestAttentionEvent && (
              <Alert
                description={attentionEventDescription(latestAttentionEvent)}
                message={attentionEventTitle(latestAttentionEvent)}
                showIcon
                type={attentionEventAlertType(latestAttentionEvent)}
              />
            )}

            <div>
              <Title level={5}>Events</Title>
              <Timeline
                items={activeEvents.map((event) => ({
                  children: (
                    <Space direction="vertical" size={2}>
                      <Text>{event.type}</Text>
                      {typeof event.payload.message === 'string' && (
                        <Text>{event.payload.message}</Text>
                      )}
                      <Text type="secondary">{formatDate(event.created_at)}</Text>
                    </Space>
                  ),
                  color: eventColor(event.type),
                }))}
              />
            </div>

            {activeDetail && (
              <div>
                <Title level={5}>Leaf state</Title>
                <pre className={styles.detailJson}>
                  {JSON.stringify(activeDetail.leaf_state, null, 2)}
                </pre>
              </div>
            )}

            {activeArtifactEntries.length > 0 && (
              <div>
                <Title level={5}>Artifacts</Title>
                <List
                  bordered
                  dataSource={activeArtifactEntries}
                  renderItem={(item) => (
                    <List.Item
                      actions={artifactListActions(
                        item,
                        artifactLoadingKey,
                        handleViewArtifact,
                        handleDownloadArtifact,
                      )}
                    >
                      <List.Item.Meta
                        description={
                          <Text code style={{ wordBreak: 'break-all' }}>
                            {item.objectKey}
                          </Text>
                        }
                        title={
                          <Space wrap>
                            <Text>{item.label}</Text>
                            {item.artifactType && <Tag>{item.artifactType}</Tag>}
                          </Space>
                        }
                      />
                    </List.Item>
                  )}
                />
              </div>
            )}
          </Space>
        )}
      </Drawer>

      <Modal
        footer={
          <Space>
            {artifactPreview && (
              <Button onClick={() => downloadLogArtifact(artifactPreview)}>
                Download
              </Button>
            )}
            <Button onClick={() => setArtifactPreviewOpen(false)} type="primary">
              Close
            </Button>
          </Space>
        }
        onCancel={() => setArtifactPreviewOpen(false)}
        open={artifactPreviewOpen}
        title={artifactPreview?.file_name ?? 'Artifact'}
        width={860}
      >
        {artifactPreview && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions
              column={1}
              items={[
                {
                  key: 'object_key',
                  label: 'Object key',
                  children: (
                    <Text code style={{ wordBreak: 'break-all' }}>
                      {artifactPreview.object_key}
                    </Text>
                  ),
                },
                {
                  key: 'metadata',
                  label: 'Metadata',
                  children: (
                    <Space wrap>
                      <Tag>{artifactPreview.content_type}</Tag>
                      <Tag>{formatArtifactBytes(artifactPreview.size_bytes)}</Tag>
                      {artifactPreview.truncated && <Tag color="orange">truncated</Tag>}
                      <Tag color={artifactPreview.redacted ? 'green' : 'orange'}>
                        {artifactPreview.redacted ? 'redacted' : 'raw'}
                      </Tag>
                    </Space>
                  ),
                },
                {
                  key: 'sha256',
                  label: 'SHA256',
                  children: (
                    <Text code style={{ wordBreak: 'break-all' }}>
                      {artifactPreview.sha256}
                    </Text>
                  ),
                },
              ]}
              size="small"
            />
            <pre className={styles.artifactPreview}>
              {logArtifactPreviewContent(artifactPreview)}
            </pre>
          </Space>
        )}
      </Modal>
    </Space>
  )

  async function handleRetry(jobId: string) {
    const detail = await retryJob(jobId, {}, workspaceId)
    setJobDetail(detail)
    await loadJobs()
  }

  async function handleViewArtifact(objectKey: string) {
    if (artifactLoadingKey) {
      return
    }
    setArtifactLoadingKey(objectKey)
    setErrorMessage(null)
    try {
      const artifact = await getLogArtifact(objectKey, workspaceId)
      if (artifact.artifact_type === 'binary') {
        downloadLogArtifact(artifact)
        return
      }
      setArtifactPreview(artifact)
      setArtifactPreviewOpen(true)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setArtifactLoadingKey(null)
    }
  }

  async function handleDownloadArtifact(objectKey: string) {
    if (artifactLoadingKey) {
      return
    }
    setArtifactLoadingKey(objectKey)
    setErrorMessage(null)
    try {
      downloadLogArtifact(await getLogArtifact(objectKey, workspaceId))
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setArtifactLoadingKey(null)
    }
  }

  async function handleCancel(jobId: string) {
    await cancelJob(jobId, workspaceId)
    await loadJobs()
  }

  async function handleStartWorker() {
    setWorkerLoading(true)
    setErrorMessage(null)
    try {
      const status = await startJobWorker(workspaceId, {
        max_jobs_per_tick: 5,
        poll_interval_ms: 1000,
      })
      setWorkerStatus(status)
      await loadJobs()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setWorkerLoading(false)
    }
  }

  async function handleStopWorker() {
    setWorkerLoading(true)
    setErrorMessage(null)
    try {
      setWorkerStatus(await stopJobWorker(workspaceId))
      await loadJobs()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setWorkerLoading(false)
    }
  }

  async function handleProcessNextJob() {
    setWorkerLoading(true)
    setErrorMessage(null)
    try {
      await processNextJob(
        workspaceId,
        jobTypeFilter ? { job_type: [jobTypeFilter] } : {},
      )
      await Promise.all([loadJobs(), loadWorkerStatus()])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setWorkerLoading(false)
    }
  }

  async function handleRecoverStaleJobs() {
    setWorkerLoading(true)
    setErrorMessage(null)
    setMaintenanceMessage(null)
    try {
      const result = await recoverStaleJobs(workspaceId, { stale_after_seconds: 60 })
      setMaintenanceMessage(`Recovered stale running jobs: ${Number(result.recovered_count ?? 0)}`)
      await Promise.all([loadJobs(), loadWorkerStatus()])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setWorkerLoading(false)
    }
  }

  async function handleRebuildJobsIndex() {
    setWorkerLoading(true)
    setErrorMessage(null)
    setMaintenanceMessage(null)
    try {
      const result = await rebuildJobsIndex(workspaceId)
      setMaintenanceMessage(
        `Rebuilt jobs index: ${Number(result.rebuilt_count ?? 0)}, skipped: ${Number(
          result.skipped_count ?? 0,
        )}`,
      )
      await loadJobs()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setWorkerLoading(false)
    }
  }
}

function statusColor(status: string) {
  if (status === 'succeeded' || status === 'partial_success') {
    return 'green'
  }
  if (status === 'failed' || status === 'unknown_outcome') {
    return 'red'
  }
  if (status === 'running' || status === 'recovering') {
    return 'blue'
  }
  return 'default'
}

function eventColor(type: string) {
  if (type.includes('failed') || type.includes('unknown')) {
    return 'red'
  }
  if (type.includes('succeeded') || type === 'stream_closed') {
    return 'green'
  }
  return 'blue'
}

function primaryActionLabel(status: string) {
  if (status === 'unknown_outcome') {
    return 'Recover'
  }
  if (status === 'partial_success') {
    return 'Retry failed chunks'
  }
  return 'Retry'
}

function canRetryOrRecover(status: string) {
  return ['failed', 'cancelled', 'unknown_outcome', 'partial_success'].includes(status)
}

function canCancel(status: string) {
  return !TERMINAL_STATUSES.has(status)
}

function latestAttentionJobEvent(events: JobEvent[]) {
  return [...events]
    .reverse()
    .find((event) => isErrorEvent(event) || isRecoveryEvent(event))
}

function isErrorEvent(event: JobEvent) {
  return (
    event.type.includes('failed') ||
    event.type.includes('unknown') ||
    event.type.includes('error') ||
    typeof event.payload.error_type === 'string'
  )
}

function isRecoveryEvent(event: JobEvent) {
  return (
    event.type.includes('recover') ||
    event.payload.status === 'recovering' ||
    typeof event.payload.recovered_at === 'string'
  )
}

function attentionEventTitle(event: JobEvent) {
  if (isRecoveryEvent(event)) {
    return `Latest recovery event: ${event.type}`
  }
  return `Latest error event: ${event.type}`
}

function attentionEventDescription(event: JobEvent) {
  const parts = [
    stringPayload(event.payload.error_type),
    stringPayload(event.payload.message),
    formatDate(event.created_at),
  ].filter(Boolean)
  return parts.join(' | ')
}

function attentionEventAlertType(event: JobEvent) {
  return isRecoveryEvent(event) ? 'warning' : 'error'
}

function stringPayload(value: unknown, fallback?: string) {
  return typeof value === 'string' ? value : fallback
}

function formatDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString()
}

interface JobArtifactEntry {
  label: string
  objectKey: string
  artifactType?: string
}

function artifactEntriesFromLeafState(
  leafState: Record<string, unknown> | undefined,
): JobArtifactEntry[] {
  const rawArtifacts = Array.isArray(leafState?.artifacts)
    ? leafState.artifacts
    : []
  const seen = new Set<string>()
  const entries: JobArtifactEntry[] = []
  rawArtifacts.forEach((artifact, index) => {
    if (!isRecord(artifact)) {
      return
    }
    Object.entries(artifact).forEach(([key, value]) => {
      if (
        !key.includes('object_key') ||
        typeof value !== 'string' ||
        !isSupportedLogArtifactKey(value) ||
        seen.has(value)
      ) {
        return
      }
      seen.add(value)
      entries.push({
        artifactType: stringPayload(artifact.artifact_type),
        label: `${artifactLabel(key)} #${index + 1}`,
        objectKey: value,
      })
    })
  })
  return entries
}

function artifactLabel(key: string) {
  if (key.includes('manifest')) {
    return 'Manifest'
  }
  if (key.includes('package')) {
    return 'Package'
  }
  if (key.includes('payload') || key === 'object_key') {
    return 'Payload'
  }
  return key
}

function artifactListActions(
  item: JobArtifactEntry,
  loadingKey: string | null,
  onView: (objectKey: string) => Promise<void>,
  onDownload: (objectKey: string) => Promise<void>,
) {
  const actions: ReactNode[] = []
  if (!item.objectKey.endsWith('.zip')) {
    actions.push(
      <Button
        key="view"
        loading={loadingKey === item.objectKey}
        onClick={() => void onView(item.objectKey)}
        size="small"
      >
        View
      </Button>,
    )
  }
  actions.push(
    <Button
      key="download"
      loading={loadingKey === item.objectKey}
      onClick={() => void onDownload(item.objectKey)}
      size="small"
    >
      Download
    </Button>,
  )
  return actions
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function updateJobIdInUrl(jobId: string | null) {
  const url = new URL(window.location.href)
  if (jobId) {
    url.searchParams.set('job_id', jobId)
  } else {
    url.searchParams.delete('job_id')
  }
  window.history.replaceState(null, '', `${url.pathname}${url.search}`)
}
