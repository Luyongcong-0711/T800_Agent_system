'use client'

import {
  Alert,
  Button,
  Card,
  Descriptions,
  Input,
  List,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { createStyles } from 'antd-style'
import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react'

import {
  createDiagnosticBundle,
  createLogArchiveJob,
  getLogArtifact,
  getSystemLogSummary,
  getSystemLogs,
} from '@/api/agentApiClient'
import { newClientRequestId } from '@/api/clientRequestId'
import type {
  DiagnosticBundleResponse,
  LogArtifactResponse,
  LogArchiveJobResponse,
  LogRecord,
  WorkspaceId,
} from '@/api/schemas/workspace'
import {
  downloadLogArtifact,
  formatArtifactBytes,
  isSupportedLogArtifactKey,
  logArtifactPreviewContent,
} from './logArtifactUtils'

const { Text } = Typography

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
  toolbar: css`
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: space-between;
    margin-bottom: 12px;
  `,
}))

interface SystemLogsPanelProps {
  workspaceId: WorkspaceId
}

type LogStream = 'summary' | 'full' | 'errors'

type QueuedArtifactKind = 'diagnostic-bundle' | 'log-archive'

interface QueuedArtifactJobState {
  kind: QueuedArtifactKind
  response: DiagnosticBundleResponse | LogArchiveJobResponse
}

export function SystemLogsPanel({ workspaceId }: SystemLogsPanelProps) {
  const { styles } = useStyles()
  const requestSeqRef = useRef(0)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [latestArtifactJob, setLatestArtifactJob] =
    useState<QueuedArtifactJobState | null>(null)
  const [actionLoading, setActionLoading] =
    useState<QueuedArtifactKind | null>(null)
  const [artifactLoadingKey, setArtifactLoadingKey] = useState<string | null>(null)
  const [artifactPreview, setArtifactPreview] =
    useState<LogArtifactResponse | null>(null)
  const [artifactPreviewOpen, setArtifactPreviewOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [logs, setLogs] = useState<LogRecord[]>([])
  const [query, setQuery] = useState('')
  const [stream, setStream] = useState<LogStream>('full')
  const [summary, setSummary] = useState<string[]>([])
  const artifactActionsDisabled = actionLoading !== null

  const loadLogs = useCallback(async () => {
    const requestSeq = requestSeqRef.current + 1
    requestSeqRef.current = requestSeq
    setLoading(true)
    setErrorMessage(null)
    const params = {
      limit: 100,
      query: query || undefined,
    }
    try {
      if (stream === 'summary') {
        const response = await getSystemLogSummary(workspaceId, params)
        if (requestSeqRef.current !== requestSeq) {
          return
        }
        setSummary(response.items)
        setLogs([])
      } else {
        const response = await getSystemLogs(workspaceId, stream, params)
        if (requestSeqRef.current !== requestSeq) {
          return
        }
        setLogs(response.items)
        setSummary([])
      }
    } catch (error) {
      if (requestSeqRef.current === requestSeq) {
        setErrorMessage(error instanceof Error ? error.message : String(error))
      }
    } finally {
      if (requestSeqRef.current === requestSeq) {
        setLoading(false)
      }
    }
  }, [query, stream, workspaceId])

  useEffect(() => {
    void loadLogs()
  }, [loadLogs])

  const columns = [
    {
      dataIndex: 'severity',
      key: 'severity',
      title: 'Level',
      render: (severity: string) => <Tag color={levelColor(severity)}>{severity}</Tag>,
    },
    {
      dataIndex: 'component',
      key: 'component',
      title: 'Component',
      render: (component: string) => <Text code>{component}</Text>,
    },
    {
      dataIndex: 'event_type',
      key: 'event_type',
      title: 'Event',
    },
    {
      dataIndex: 'message',
      key: 'message',
      title: 'Message',
    },
    {
      dataIndex: 'trace_id',
      key: 'trace_id',
      title: 'Trace',
      render: (traceId: string) => <Text code>{traceId}</Text>,
    },
    {
      dataIndex: 'timestamp',
      key: 'timestamp',
      title: 'Time',
      render: (value: string) => <Text type="secondary">{formatDate(value)}</Text>,
    },
  ]

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <div className={styles.toolbar}>
        <Space wrap>
          <Select
            onChange={setStream}
            options={[
              { label: 'Full', value: 'full' },
              { label: 'Errors', value: 'errors' },
              { label: 'Summary', value: 'summary' },
            ]}
            style={{ width: 140 }}
            value={stream}
          />
          <Input
            allowClear
            onChange={(event) => setQuery(event.target.value)}
            placeholder="trace_id / run_id / text"
            style={{ width: 280 }}
            value={query}
          />
          <Button loading={loading} onClick={() => void loadLogs()}>
            Refresh
          </Button>
        </Space>
        <Space wrap>
          <Button
            disabled={artifactActionsDisabled}
            loading={actionLoading === 'diagnostic-bundle'}
            onClick={() => void handleDiagnosticBundle()}
          >
            Diagnostic bundle
          </Button>
          <Button
            disabled={artifactActionsDisabled}
            loading={actionLoading === 'log-archive'}
            onClick={() => void handleArchiveLogs()}
          >
            Archive logs
          </Button>
        </Space>
      </div>

      {errorMessage && <Alert message={errorMessage} showIcon type="error" />}
      {latestArtifactJob && (
        <Card
          extra={
            <Space wrap>
              <Button href={artifactJobHref(latestArtifactJob.response)} size="small">
                Open Jobs
              </Button>
              <Button loading={loading} onClick={() => void loadLogs()} size="small">
                Reload logs
              </Button>
            </Space>
          }
          size="small"
          title={queuedArtifactTitle(latestArtifactJob.kind)}
        >
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            <Space wrap>
              <Tag color={jobStatusColor(artifactJobStatus(latestArtifactJob.response))}>
                {artifactJobStatus(latestArtifactJob.response)}
              </Tag>
              <Tag>{queuedArtifactKindLabel(latestArtifactJob.kind)}</Tag>
            </Space>
            <Descriptions
              column={1}
              items={artifactDetails(latestArtifactJob.response, {
                loadingKey: artifactLoadingKey,
                onDownload: handleDownloadArtifact,
                onView: handleViewArtifact,
              })}
              size="small"
            />
          </Space>
        </Card>
      )}

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

      {stream === 'summary' ? (
        <List
          bordered
          dataSource={summary}
          loading={loading}
          renderItem={(item) => (
            <List.Item>
              <Text>{item}</Text>
            </List.Item>
          )}
        />
      ) : (
        <Table<LogRecord>
          columns={columns}
          dataSource={logs}
          loading={loading}
          pagination={{ pageSize: 10 }}
          rowKey={(record) => `${record.timestamp}:${record.trace_id}:${record.event_type}`}
        />
      )}
    </Space>
  )

  async function handleDiagnosticBundle() {
    if (actionLoading) {
      return
    }
    setActionLoading('diagnostic-bundle')
    setErrorMessage(null)
    try {
      const response = await createDiagnosticBundle(
        {
          components: ['api'],
          request_id: newClientRequestId('diag'),
          trace_id: query || undefined,
        },
        workspaceId,
      )
      setLatestArtifactJob({
        kind: 'diagnostic-bundle',
        response,
      })
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setActionLoading(null)
    }
  }

  async function handleArchiveLogs() {
    if (actionLoading) {
      return
    }
    setActionLoading('log-archive')
    setErrorMessage(null)
    try {
      const response = await createLogArchiveJob(
        { request_id: newClientRequestId('log-archive') },
        workspaceId,
      )
      setLatestArtifactJob({
        kind: 'log-archive',
        response,
      })
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setActionLoading(null)
    }
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
}

function levelColor(level: string) {
  const normalized = level.toUpperCase()
  if (normalized === 'ERROR' || normalized === 'FATAL') {
    return 'red'
  }
  if (normalized === 'WARN' || normalized === 'WARNING') {
    return 'orange'
  }
  return 'blue'
}

function formatDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString()
}

function queuedArtifactKindLabel(kind: QueuedArtifactKind) {
  return kind === 'diagnostic-bundle' ? 'Diagnostic bundle' : 'Log archive'
}

function queuedArtifactTitle(kind: QueuedArtifactKind) {
  return `${queuedArtifactKindLabel(kind)} queued`
}

interface ArtifactActions {
  loadingKey: string | null
  onDownload: (objectKey: string) => void
  onView: (objectKey: string) => void
}

function jobStatusColor(status: string) {
  const normalized = status.toLowerCase()
  if (normalized.includes('fail') || normalized.includes('error')) {
    return 'red'
  }
  if (
    normalized.includes('success') ||
    normalized.includes('complete') ||
    normalized.includes('done') ||
    normalized.includes('succeed')
  ) {
    return 'green'
  }
  if (normalized.includes('queue') || normalized.includes('pend')) {
    return 'orange'
  }
  return 'blue'
}

function artifactDetails(
  response: DiagnosticBundleResponse | LogArchiveJobResponse,
  actions: ArtifactActions,
) {
  const record = response as unknown as Record<string, unknown>
  const items: Array<{
    key: string
    label: string
    children: ReactNode
  }> = []

  addArtifactItem(items, 'job_id', 'Job ID', record)
  addArtifactStatusItem(
    items,
    firstStringKey(record, ['job_status', 'status']),
    'Job status',
    record,
  )
  addArtifactItem(items, 'bundle_id', 'Bundle ID', record)
  addArtifactItem(items, 'date', 'Date', record)
  addArtifactItem(items, 'runtime_instance_id', 'Runtime instance ID', record)
  if (record.related_job_id !== record.job_id) {
    addArtifactItem(items, 'related_job_id', 'Related job ID', record)
  }
  addArtifactItem(items, 'manifest_object_key', 'Manifest object key', record, actions)
  addArtifactItem(items, 'object_key', 'Object key', record, actions)
  addArtifactItem(
    items,
    firstStringKey(record, [
      'package_object_key',
      'package_key',
      'archive_package_object_key',
    ]),
    'Package object key',
    record,
    actions,
  )
  addArtifactItem(items, 'created_at', 'Created at', record)
  addArtifactBooleanItem(items, 'redacted', 'Redacted', record)

  return items
}

function addArtifactItem(
  items: Array<{
    key: string
    label: string
    children: ReactNode
  }>,
  key: string | null,
  label: string,
  record: Record<string, unknown>,
  actions?: ArtifactActions,
) {
  if (!key) {
    return
  }
  const value = record[key]
  if (typeof value !== 'string' || !value) {
    return
  }
  const showArtifactActions = key.includes('object_key') && isSupportedLogArtifactKey(value)
  items.push({
    children: (
      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        <Text code style={{ wordBreak: 'break-all' }}>
          {value}
        </Text>
        {actions && showArtifactActions && (
          <Space wrap>
            {!value.endsWith('.zip') && (
              <Button
                loading={actions.loadingKey === value}
                onClick={() => actions.onView(value)}
                size="small"
              >
                View
              </Button>
            )}
            <Button
              loading={actions.loadingKey === value}
              onClick={() => actions.onDownload(value)}
              size="small"
            >
              Download
            </Button>
          </Space>
        )}
      </Space>
    ),
    key,
    label,
  })
}

function addArtifactStatusItem(
  items: Array<{
    key: string
    label: string
    children: ReactNode
  }>,
  key: string | null,
  label: string,
  record: Record<string, unknown>,
) {
  if (!key) {
    return
  }
  const value = record[key]
  if (typeof value !== 'string' || !value) {
    return
  }
  items.push({
    children: <Tag color={jobStatusColor(value)}>{value}</Tag>,
    key,
    label,
  })
}

function addArtifactBooleanItem(
  items: Array<{
    key: string
    label: string
    children: ReactNode
  }>,
  key: string,
  label: string,
  record: Record<string, unknown>,
) {
  const value = record[key]
  if (typeof value !== 'boolean') {
    return
  }
  items.push({
    children: <Tag color={value ? 'green' : 'orange'}>{value ? 'yes' : 'no'}</Tag>,
    key,
    label,
  })
}

function artifactJobStatus(
  response: DiagnosticBundleResponse | LogArchiveJobResponse,
) {
  const record = response as unknown as Record<string, unknown>
  return firstStringValue(record, ['job_status', 'status']) ?? 'queued'
}

function artifactJobHref(response: DiagnosticBundleResponse | LogArchiveJobResponse) {
  const record = response as unknown as Record<string, unknown>
  const jobId = firstStringValue(record, ['job_id', 'related_job_id'])
  return jobId ? `/jobs?job_id=${encodeURIComponent(jobId)}` : '/jobs'
}

function firstStringValue(record: Record<string, unknown>, keys: string[]) {
  const key = firstStringKey(record, keys)
  return key ? (record[key] as string) : null
}

function firstStringKey(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'string' && value) {
      return key
    }
  }
  return null
}
