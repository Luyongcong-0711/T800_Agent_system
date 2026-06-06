'use client'

import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  List,
  Row,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { createStyles } from 'antd-style'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { getP0Readiness } from '@/api/agentApiClient'
import type {
  P0ReadinessResponse,
  ReadinessCategory,
  ReadinessCheck,
  ReadinessStatus,
  WorkspaceId,
} from '@/api/schemas/workspace'
import { getWorkspaceSectionPath, type SectionKey } from '@/components/workspace/routes'

const { Text, Title } = Typography

const MODEL_CONFIG_SMOKE_TARGETS = [
  {
    checkIds: ['external.main_chat_model_smoke', 'runtime.model_config.main_chat_smoke'],
    key: 'main_chat',
    label: 'main_chat',
  },
  {
    checkIds: ['external.graphrag_llm_model_smoke', 'runtime.model_config.graphrag_llm_smoke'],
    key: 'graphrag_llm',
    label: 'graphrag_llm',
  },
  {
    checkIds: ['external.embedding_model_smoke', 'runtime.model_config.embedding_smoke'],
    key: 'embedding',
    label: 'embedding',
  },
] as const

const DATABASE_HEALTH_CHECK_IDS = [
  'database.health_snapshot',
  'external.database_live_health',
  'external.docker_compose',
  'runtime.database_live_health',
  'runtime.docker_compose_ps',
]

const useStyles = createStyles(({ css, token }) => ({
  categoryCard: css`
    height: 100%;
  `,
  codeBlock: css`
    background: ${token.colorFillQuaternary};
    border: 1px solid ${token.colorBorderSecondary};
    border-radius: 6px;
    margin: 0;
    max-height: 240px;
    overflow: auto;
    padding: 8px 10px;
    white-space: pre-wrap;
    word-break: break-word;
  `,
  evidenceList: css`
    margin: 0;
    padding-left: 18px;
  `,
  summaryCard: css`
    height: 100%;
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

interface P0ReadinessPanelProps {
  workspaceId: WorkspaceId
}

export function P0ReadinessPanel({ workspaceId }: P0ReadinessPanelProps) {
  const { styles } = useStyles()
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [readiness, setReadiness] = useState<P0ReadinessResponse | null>(null)

  const loadReadiness = useCallback(async () => {
    setLoading(true)
    setErrorMessage(null)
    try {
      setReadiness(await getP0Readiness(workspaceId))
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load readiness.')
    } finally {
      setLoading(false)
    }
  }, [workspaceId])

  useEffect(() => {
    void loadReadiness()
  }, [loadReadiness])

  const requiredBlockers = useMemo(
    () => readiness?.checks.filter((check) => check.required && ['fail', 'blocked'].includes(check.status)) ?? [],
    [readiness],
  )
  const modelSmokeRows = useMemo(
    () => modelConfigSmokeRows(readiness?.checks ?? []),
    [readiness],
  )
  const databaseHealthRows = useMemo(
    () => databaseHealthStateRows(readiness?.checks ?? []),
    [readiness],
  )

  const columns = [
    {
      dataIndex: 'title',
      key: 'title',
      title: 'Check',
      render: (_: unknown, record: ReadinessCheck) => (
        <Space direction="vertical" size={2}>
          <Space wrap>
            <Text strong>{record.title}</Text>
            <Tag color={record.required ? 'red' : 'default'}>
              {record.required ? 'required' : 'optional'}
            </Tag>
          </Space>
          <Text code>{record.check_id}</Text>
          <Text type="secondary">{record.summary}</Text>
        </Space>
      ),
    },
    {
      dataIndex: 'category',
      key: 'category',
      title: 'Category',
      render: (category: string) => <Tag>{category}</Tag>,
    },
    {
      dataIndex: 'status',
      key: 'status',
      title: 'Status',
      render: (status: ReadinessStatus) => <Tag color={statusColor(status)}>{status}</Tag>,
    },
    {
      key: 'evidence',
      title: 'Evidence',
      render: (_: unknown, record: ReadinessCheck) => (
        <Space direction="vertical" size={6}>
          {record.evidence.length > 0 ? (
            <ul className={styles.evidenceList}>
              {record.evidence.slice(0, 5).map((item) => (
                <li key={item}>
                  <Text type="secondary">{item}</Text>
                </li>
              ))}
            </ul>
          ) : (
            <Text type="secondary">none</Text>
          )}
          {record.next_actions.length > 0 && (
            <Space direction="vertical" size={2}>
              {record.next_actions.map((action) => (
                <Text key={action}>{action}</Text>
              ))}
            </Space>
          )}
        </Space>
      ),
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <div className={styles.toolbar}>
        <Space wrap>
          <Title level={4} style={{ margin: 0 }}>
            P0 readiness
          </Title>
          {readiness && <Tag color={statusColor(readiness.status)}>{readiness.status}</Tag>}
          {readiness && (
            readiness.ok ? <Tag color="green">required checks ok</Tag> : <Tag color="red">action required</Tag>
          )}
        </Space>
        <Button loading={loading} onClick={() => void loadReadiness()}>
          Refresh
        </Button>
      </div>

      {errorMessage && <Alert message={errorMessage} showIcon type="error" />}

      {readiness ? (
        <>
          <Row gutter={[16, 16]}>
            <Col lg={8} xs={24}>
              <Card className={styles.summaryCard} title="Runtime">
                <Descriptions
                  column={1}
                  items={[
                    { key: 'workspace', label: 'Workspace', children: readiness.workspace_id },
                    { key: 'environment', label: 'Environment', children: readiness.environment },
                    { key: 'runtime', label: 'Runtime', children: readiness.runtime_instance_id },
                    { key: 'generated', label: 'Generated', children: formatDate(readiness.generated_at) },
                  ]}
                  size="small"
                />
              </Card>
            </Col>
            <Col lg={8} xs={24}>
              <Card className={styles.summaryCard} title="Summary">
                <Space wrap>
                  {Object.entries(readiness.summary).map(([key, value]) => (
                    <Tag color={summaryColor(key)} key={key}>
                      {key}: {value}
                    </Tag>
                  ))}
                </Space>
              </Card>
            </Col>
            <Col lg={8} xs={24}>
              <Card className={styles.summaryCard} title="Blockers">
                {requiredBlockers.length === 0 ? (
                  <Text type="secondary">No required blockers.</Text>
                ) : (
                  <List
                    dataSource={requiredBlockers}
                    renderItem={(item) => (
                      <List.Item>
                        <Space direction="vertical" size={2}>
                          <Text strong>{item.check_id}</Text>
                          <Text>{item.summary}</Text>
                          {remediationLinksForCheck(item).length > 0 && (
                            <Space wrap>
                              {remediationLinksForCheck(item).map((link) => (
                                <Button href={link.href} key={link.key} size="small">
                                  {link.label}
                                </Button>
                              ))}
                            </Space>
                          )}
                        </Space>
                      </List.Item>
                    )}
                    size="small"
                  />
                )}
              </Card>
            </Col>
          </Row>

          <Row gutter={[16, 16]}>
            <Col lg={12} xs={24}>
              <Card className={styles.summaryCard} title="Model config smoke">
                <List
                  dataSource={modelSmokeRows}
                  renderItem={(item) => (
                    <List.Item>
                      <Space direction="vertical" size={2} style={{ width: '100%' }}>
                        <Space wrap>
                          <Text strong>{item.label}</Text>
                          <Tag color={statusColor(item.status)}>{item.status}</Tag>
                          <Tag color={item.source === 'runtime' ? 'blue' : 'default'}>
                            {item.source === 'runtime' ? 'runtime smoke' : 'config readiness'}
                          </Tag>
                        </Space>
                        <Text type={item.status === 'pass' ? 'secondary' : undefined}>
                          {item.summary}
                        </Text>
                        {item.nextAction && <Text type="secondary">{item.nextAction}</Text>}
                      </Space>
                    </List.Item>
                  )}
                  size="small"
                />
              </Card>
            </Col>
            <Col lg={12} xs={24}>
              <Card className={styles.summaryCard} title="Database health">
                <List
                  dataSource={databaseHealthRows}
                  renderItem={(item) => (
                    <List.Item>
                      <Space direction="vertical" size={2} style={{ width: '100%' }}>
                        <Space wrap>
                          <Text strong>{item.label}</Text>
                          <Tag color={statusColor(item.status)}>{databaseStateLabel(item.status)}</Tag>
                        </Space>
                        <Text type={item.status === 'pass' ? 'secondary' : undefined}>
                          {item.summary}
                        </Text>
                        {item.evidence.length > 0 && (
                          <Space wrap>
                            {item.evidence.map((entry) => (
                              <Tag color={databaseEvidenceColor(entry)} key={entry}>
                                {entry}
                              </Tag>
                            ))}
                          </Space>
                        )}
                        {item.nextAction && <Text type="secondary">{item.nextAction}</Text>}
                      </Space>
                    </List.Item>
                  )}
                  size="small"
                />
              </Card>
            </Col>
          </Row>

          <Row gutter={[16, 16]}>
            {readiness.categories.map((category) => (
              <Col key={category.category} lg={8} md={12} xs={24}>
                <CategoryCard category={category} />
              </Col>
            ))}
          </Row>

          <Card title="Checks">
            <Table<ReadinessCheck>
              className={styles.table}
              columns={columns}
              dataSource={readiness.checks}
              expandable={{
                expandedRowRender: (record) => <ReadinessDetails check={record} />,
                rowExpandable: hasReadableDetails,
              }}
              loading={loading}
              pagination={{ pageSize: 12 }}
              rowKey="check_id"
            />
          </Card>
        </>
      ) : (
        <Card>
          <Empty description="No readiness snapshot loaded." />
        </Card>
      )}
    </Space>
  )
}

function ReadinessDetails({ check }: { check: ReadinessCheck }) {
  const { styles } = useStyles()
  const sourceCheck = readRecord(check.details.source_check)
  const routeSmokeCheck = routeSmokeRecordForCheck(check)
  const routeSmokeRows = parseRouteSmokeRows(routeSmokeCheck?.stdout_tail)
  const routeSmokeNextAction =
    routeSmokeCheck?.next_action || concreteRouteSmokeNextAction(check)
  const finalHandoff = readRecord(check.details.final_handoff)
  const finalHandoffRows = finalHandoffRowsFromDetails(finalHandoff)
  const staleRequiredCheckIds = arrayFromUnknown(check.details.stale_required_check_ids)
  const staleRequiredFlags = arrayFromUnknown(check.details.stale_required_flags)
  const reportSummary = readRecord(check.details.report_summary)
  const genericDetails = Object.fromEntries(
    Object.entries(check.details).filter(
      ([key, value]) =>
        key !== 'source_check' &&
        key !== 'final_handoff' &&
        key !== 'report_summary' &&
        key !== 'stale_required_check_ids' &&
        key !== 'stale_required_flags' &&
        value !== undefined,
    ),
  )

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {sourceCheck && (
        <Descriptions
          column={{ lg: 3, md: 2, xs: 1 }}
          items={[
            {
              key: 'source_status',
              label: 'Source status',
              children: String(sourceCheck.status ?? 'unknown'),
            },
            {
              key: 'duration',
              label: 'Duration',
              children:
                sourceCheck.duration_ms === undefined
                  ? 'unknown'
                  : `${String(sourceCheck.duration_ms)} ms`,
            },
            {
              key: 'cwd',
              label: 'CWD',
              children: String(sourceCheck.cwd ?? 'n/a'),
            },
            {
              key: 'command',
              label: 'Command',
              children: Array.isArray(sourceCheck.command)
                ? sourceCheck.command.map(String).join(' ')
                : String(sourceCheck.command ?? 'n/a'),
            },
            {
              key: 'next_action',
              label: 'Next action',
              children: String(sourceCheck.next_action || 'none'),
            },
          ]}
          size="small"
        />
      )}

      {routeSmokeCheck && (
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Alert
            message="Route smoke evidence"
            description={String(routeSmokeNextAction)}
            showIcon
            type={routeSmokeCheck.status === 'pass' ? 'success' : 'warning'}
          />
          {routeSmokeRows.length > 0 && (
            <Table
              columns={[
                {
                  dataIndex: 'status',
                  key: 'status',
                  title: 'Result',
                  render: (status: string) => (
                    <Tag color={status === 'PASS' ? 'green' : 'red'}>{status}</Tag>
                  ),
                },
                {
                  dataIndex: 'route',
                  key: 'route',
                  title: 'Route',
                  render: (route: string) => <Text code>{route}</Text>,
                },
                {
                  dataIndex: 'details',
                  key: 'details',
                  title: 'Details',
                  render: (details: string) => <Text type="secondary">{details}</Text>,
                },
              ]}
              dataSource={routeSmokeRows}
              pagination={false}
              rowKey="line"
              size="small"
            />
          )}
        </Space>
      )}

      {finalHandoff && (
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Alert
            message="Final handoff"
            description={
              finalHandoff.ready === true
                ? 'All required final handoff checks passed.'
                : String(
                    finalHandoff.recommended_command ||
                      'Run the final acceptance command.',
                  )
            }
            showIcon
            type={finalHandoff.ready === true ? 'success' : 'warning'}
          />
          <Space wrap>
            <Tag color={finalHandoff.ready === true ? 'green' : 'orange'}>
              ready: {String(finalHandoff.ready === true)}
            </Tag>
            <Tag>missing flags: {String(arrayLength(finalHandoff.missing_flags))}</Tag>
            <Tag>missing: {String(arrayLength(finalHandoff.missing_check_ids))}</Tag>
            <Tag>non passing: {String(arrayLength(finalHandoff.non_passing_checks))}</Tag>
            <Tag>
              executed failures: {String(arrayLength(finalHandoff.non_passing_executed_checks))}
            </Tag>
            <Tag color={staleRequiredCheckIds.length > 0 ? 'orange' : undefined}>
              stale checks: {String(staleRequiredCheckIds.length)}
            </Tag>
            <Tag color={staleRequiredFlags.length > 0 ? 'orange' : undefined}>
              stale flags: {String(staleRequiredFlags.length)}
            </Tag>
          </Space>
          {staleRequiredCheckIds.length > 0 && (
            <Space wrap>
              {staleRequiredCheckIds.map((checkId) => (
                <Tag color="orange" key={String(checkId)}>
                  {String(checkId)}
                </Tag>
              ))}
            </Space>
          )}
          {staleRequiredFlags.length > 0 && (
            <Space wrap>
              {staleRequiredFlags.map((flag) => (
                <Tag color="orange" key={String(flag)}>
                  {String(flag)}
                </Tag>
              ))}
            </Space>
          )}
          {Array.isArray(finalHandoff.missing_flags) && finalHandoff.missing_flags.length > 0 && (
            <Space wrap>
              {finalHandoff.missing_flags.map((flag) => (
                <Tag color="orange" key={String(flag)}>
                  {String(flag)}
                </Tag>
              ))}
            </Space>
          )}
          {finalHandoffRows.length > 0 && (
            <Table
              columns={[
                {
                  dataIndex: 'check_id',
                  key: 'check_id',
                  title: 'Check',
                  render: (checkId: string) => <Text code>{checkId}</Text>,
                },
                {
                  dataIndex: 'status',
                  key: 'status',
                  title: 'Status',
                  render: (status: string) => (
                    <Tag color={status === 'fail' ? 'red' : 'orange'}>{status}</Tag>
                  ),
                },
                {
                  dataIndex: 'summary',
                  key: 'summary',
                  title: 'Summary',
                  render: (summary: string) => <Text type="secondary">{summary}</Text>,
                },
                {
                  dataIndex: 'next_action',
                  key: 'next_action',
                  title: 'Next action',
                  render: (action: string) => (
                    <Text>{action || 'Rerun final P0 acceptance.'}</Text>
                  ),
                },
              ]}
              dataSource={finalHandoffRows}
              pagination={false}
              rowKey="check_id"
              size="small"
            />
          )}
        </Space>
      )}

      {sourceCheck?.stdout_tail ? (
        <Space direction="vertical" size={4} style={{ width: '100%' }}>
          <Text strong>stdout tail</Text>
          <pre className={styles.codeBlock}>{String(sourceCheck.stdout_tail)}</pre>
        </Space>
      ) : null}

      {sourceCheck?.stderr_tail ? (
        <Space direction="vertical" size={4} style={{ width: '100%' }}>
          <Text strong>stderr tail</Text>
          <pre className={styles.codeBlock}>{String(sourceCheck.stderr_tail)}</pre>
        </Space>
      ) : null}

      {Object.keys(genericDetails).length > 0 && (
        <Space direction="vertical" size={4} style={{ width: '100%' }}>
          <Text strong>details</Text>
          <pre className={styles.codeBlock}>{JSON.stringify(genericDetails, null, 2)}</pre>
        </Space>
      )}

      {reportSummary && (
        <Space wrap>
          {Object.entries(reportSummary).map(([key, value]) => (
            <Tag key={key}>{`${key}: ${String(value)}`}</Tag>
          ))}
        </Space>
      )}
    </Space>
  )
}

function CategoryCard({ category }: { category: ReadinessCategory }) {
  const { styles } = useStyles()
  return (
    <Card className={styles.categoryCard} title={category.category}>
      <Space direction="vertical" size={8} style={{ width: '100%' }}>
        <Tag color={statusColor(category.status)}>{category.status}</Tag>
        <Space wrap>
          <Tag color="green">pass {category.pass_count}</Tag>
          <Tag color="gold">warn {category.warn_count}</Tag>
          <Tag color="red">fail {category.fail_count}</Tag>
          <Tag color="orange">blocked {category.blocked_count}</Tag>
        </Space>
      </Space>
    </Card>
  )
}

function statusColor(status: ReadinessStatus) {
  if (status === 'pass') {
    return 'green'
  }
  if (status === 'warn') {
    return 'gold'
  }
  if (status === 'blocked') {
    return 'orange'
  }
  if (status === 'fail') {
    return 'red'
  }
  return 'default'
}

function summaryColor(key: string) {
  if (key === 'pass') {
    return 'green'
  }
  if (key === 'warn') {
    return 'gold'
  }
  if (key === 'blocked') {
    return 'orange'
  }
  if (key === 'fail') {
    return 'red'
  }
  return 'default'
}

interface ModelConfigSmokeRow {
  key: string
  label: string
  nextAction: string
  source: 'runtime' | 'config'
  status: ReadinessStatus
  summary: string
}

function modelConfigSmokeRows(checks: ReadinessCheck[]): ModelConfigSmokeRow[] {
  const modelConfigCheck = checks.find((check) => check.check_id === 'models.config')
  return MODEL_CONFIG_SMOKE_TARGETS.map((target) => {
    const runtimeCheck = checks.find((check) =>
      (target.checkIds as readonly string[]).includes(check.check_id),
    )
    if (runtimeCheck) {
      return {
        key: target.key,
        label: target.label,
        nextAction: runtimeCheck.next_actions[0] || '',
        source: 'runtime',
        status: runtimeCheck.status,
        summary: runtimeCheck.summary,
      }
    }
    const modelConfigEvidence = modelConfigEvidenceForTarget(modelConfigCheck, target.key)
    return {
      key: target.key,
      label: target.label,
      nextAction:
        modelConfigCheck?.next_actions[0] ||
        'Run final acceptance with model smoke checks after this config is ready.',
      source: 'config',
      status: statusFromModelConfigEvidence(modelConfigCheck, modelConfigEvidence),
      summary: modelConfigEvidence
        ? `No runtime smoke check is recorded yet. ${modelConfigEvidence}`
        : 'No runtime smoke check or config readiness evidence is recorded yet.',
    }
  })
}

function modelConfigEvidenceForTarget(
  check: ReadinessCheck | undefined,
  targetKey: string,
) {
  return check?.evidence.find((item) => item.startsWith(`${targetKey}:`)) || ''
}

function statusFromModelConfigEvidence(
  check: ReadinessCheck | undefined,
  evidence: string,
): ReadinessStatus {
  if (!check || !evidence) {
    return 'blocked'
  }
  if (check.status !== 'pass') {
    return check.status
  }
  return evidence.includes(':configured:') ? 'pass' : 'blocked'
}

interface DatabaseHealthRow {
  evidence: string[]
  label: string
  nextAction: string
  status: ReadinessStatus
  summary: string
}

function databaseHealthStateRows(checks: ReadinessCheck[]): DatabaseHealthRow[] {
  const rows = checks
    .filter((check) => DATABASE_HEALTH_CHECK_IDS.includes(check.check_id))
    .map((check) => ({
      evidence: databaseHealthEvidence(check),
      label: check.title,
      nextAction: check.next_actions[0] || '',
      status: check.status,
      summary: check.summary,
    }))

  if (rows.length > 0) {
    return rows
  }

  return [
    {
      evidence: [],
      label: 'Database health snapshot',
      nextAction: 'Run database health check from Settings or final acceptance.',
      status: 'blocked',
      summary: 'No database health evidence is available.',
    },
  ]
}

function databaseHealthEvidence(check: ReadinessCheck) {
  return check.evidence.filter((entry) =>
    /source=|source_status=|http_status=|status_code=|ok=|unhealthy=|missing=|not_running=|health=|state=/.test(entry),
  )
}

function databaseStateLabel(status: ReadinessStatus) {
  if (status === 'pass') {
    return 'healthy'
  }
  if (status === 'warn') {
    return 'degraded'
  }
  if (status === 'blocked') {
    return 'blocked'
  }
  if (status === 'fail') {
    return 'failed'
  }
  return status
}

function databaseEvidenceColor(entry: string) {
  if (
    /ok=false|source_status=fail|http_status=4\d\d|http_status=5\d\d|status_code=4\d\d|status_code=5\d\d|unhealthy=(?!none)|missing=\[.+\]|not_running=\[.+\]|fail|unhealthy/i.test(
      entry,
    )
  ) {
    return 'red'
  }
  if (/source=unknown|unknown|degraded/i.test(entry)) {
    return 'gold'
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

function hasReadableDetails(check: ReadinessCheck) {
  return Object.keys(check.details).length > 0
}

function routeSmokeRecordForCheck(check: ReadinessCheck): Record<string, unknown> | null {
  const sourceCheck = readRecord(check.details.source_check)
  const directCheck = hasRouteSmokeEvidence(check.details) ? check.details : null
  const namedDetail =
    readRecord(check.details.route_smoke) ||
    readRecord(check.details.page_smoke) ||
    readRecord(check.details.browser_smoke)
  const candidates = [sourceCheck, namedDetail, directCheck]
  return candidates.find((candidate) => candidate && hasRouteSmokeEvidence(candidate)) ?? null
}

function hasRouteSmokeEvidence(value: Record<string, unknown>) {
  const text = [
    value.check_id,
    value.summary,
    value.stdout_tail,
    value.next_action,
  ]
    .filter(Boolean)
    .map(String)
    .join(' ')
    .toLowerCase()
  return /frontend_route_smoke|frontend_browser_smoke|browser_e2e_smoke|route smoke|page smoke|browser smoke|workspace route smoke/.test(text)
}

interface RouteSmokeRow {
  details: string
  line: string
  route: string
  status: 'PASS' | 'FAIL'
}

function parseRouteSmokeRows(value: unknown): RouteSmokeRow[] {
  if (typeof value !== 'string') {
    return []
  }
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => /^(PASS|FAIL)\s+/.test(line))
    .map((line) => {
      const [status, route, ...details] = line.split(/\s+/)
      return {
        details: details.join(' '),
        line,
        route: route || 'unknown',
        status: status === 'PASS' ? 'PASS' : 'FAIL',
      }
    })
}

function concreteRouteSmokeNextAction(check: ReadinessCheck) {
  return (
    check.next_actions[0] ||
    'Start the frontend service, inspect failed routes, then rerun p0_acceptance.py with --include-runtime-http.'
  )
}

interface FinalHandoffRow {
  check_id: string
  next_action: string
  status: string
  summary: string
}

function finalHandoffRowsFromDetails(value: Record<string, unknown> | null): FinalHandoffRow[] {
  const requiredNonPassing = Array.isArray(value?.non_passing_checks)
    ? value.non_passing_checks
    : []
  const executedNonPassing = Array.isArray(value?.non_passing_executed_checks)
    ? value.non_passing_executed_checks
    : []
  const seen = new Set<string>()
  const nonPassing = [...requiredNonPassing, ...executedNonPassing].filter((item) => {
    const record = readRecord(item)
    const key = String(record?.check_id || '')
    if (!key || seen.has(key)) {
      return false
    }
    seen.add(key)
    return true
  })
  return nonPassing.flatMap((item) => {
    const record = readRecord(item)
    if (!record) {
      return []
    }
    const checkId = String(record.check_id || '')
    if (!checkId) {
      return []
    }
    return [
      {
        check_id: checkId,
        next_action: String(record.next_action || ''),
        status: String(record.status || 'unknown'),
        summary: String(record.summary || ''),
      },
    ]
  })
}

function arrayLength(value: unknown) {
  return Array.isArray(value) ? value.length : 0
}

function arrayFromUnknown(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function readRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null
  }
  return value as Record<string, unknown>
}

interface RemediationLink {
  href: string
  key: string
  label: string
}

const REMEDIATION_LABELS: Record<SectionKey, string> = {
  chat: 'Chat',
  jobs: 'Jobs',
  knowledge: 'Knowledge',
  memory: 'Memory',
  skills: 'Skills',
  subagents: 'SubAgents',
  mcp: 'MCP',
  logs: 'Logs',
  readiness: 'Readiness',
  settings: 'Settings',
}

function remediationLinksForCheck(check: ReadinessCheck): RemediationLink[] {
  const text = [
    check.check_id,
    check.category,
    check.title,
    check.summary,
    ...check.evidence,
    ...check.next_actions,
  ]
    .join(' ')
    .toLowerCase()
  const links: RemediationLink[] = []
  const addLink = (section: SectionKey, options: { href?: string; key?: string; label?: string } = {}) => {
    const href = options.href ?? getWorkspaceSectionPath(section)
    if (links.some((link) => link.href === href)) {
      return
    }
    links.push({
      href,
      key: options.key ?? section,
      label: options.label ?? REMEDIATION_LABELS[section],
    })
  }
  if (/model|llm|embedding|rerank|api_key|api key/.test(text)) {
    addLink('settings', {
      href: `${getWorkspaceSectionPath('settings')}?tab=models`,
      key: 'settings-models',
      label: 'Model APIs',
    })
  } else if (/database|minio|milvus|neo4j|redis/.test(text)) {
    addLink('settings', {
      href: `${getWorkspaceSectionPath('settings')}?tab=databases`,
      key: 'settings-databases',
      label: 'Databases',
    })
  } else if (/secret|credential|key_ref/.test(text)) {
    addLink('settings', {
      href: `${getWorkspaceSectionPath('settings')}?tab=secrets`,
      key: 'settings-secrets',
      label: 'Secrets',
    })
  } else if (/config/.test(text)) {
    addLink('settings')
  }
  if (/job|worker|recover|unknown_outcome|docker/.test(text)) {
    addLink('jobs')
  }
  if (/mcp|tool inventory|capability/.test(text)) {
    addLink('mcp')
  }
  if (/log|trace|diagnostic|acceptance|browser|e2e|smoke/.test(text)) {
    addLink('logs')
  }
  if (/knowledge|document|chunk|rag|graphrag|embedding/.test(text)) {
    addLink('knowledge')
  }
  if (/memory|profile|preference|compaction/.test(text)) {
    addLink('memory')
  }
  if (/skill/.test(text)) {
    addLink('skills')
  }
  if (/subagent|sub-agent/.test(text)) {
    addLink('subagents')
  }
  if (/thread|run|conversation|runtime/.test(text)) {
    addLink('chat')
  }
  return links.slice(0, 4)
}
