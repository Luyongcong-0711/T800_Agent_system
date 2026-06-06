'use client'

import {
  Alert,
  Button,
  Card,
  Input,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import { createStyles } from 'antd-style'
import { useCallback, useEffect, useState } from 'react'

import {
  checkDatabaseHealth,
  getDatabaseConfig,
  getDatabaseHealth,
  listSecrets,
  updateDatabaseConfig,
} from '@/api/agentApiClient'
import type {
  DatabaseHealthSnapshotResponse,
  DatabaseTargetConfig,
  SecretSummary,
  SecretType,
  ServiceHealth,
  WorkspaceId,
} from '@/api/schemas/workspace'

const { Text } = Typography

const TARGETS: DatabaseTargetConfig['target'][] = ['minio', 'milvus', 'neo4j', 'redis']
type JsonField = 'credential_refs' | 'options'
type ValidationLevel = 'warning' | 'error'

interface CredentialSlot {
  key: string
  label: string
  types: SecretType[]
}

interface ConfigValidationHint {
  level: ValidationLevel
  target: DatabaseTargetConfig['target']
  message: string
}

const DATABASE_CREDENTIAL_SLOTS: Record<DatabaseTargetConfig['target'], CredentialSlot[]> = {
  milvus: [{ key: 'token', label: 'Token', types: ['milvus_token'] }],
  minio: [
    { key: 'access_key', label: 'Access key', types: ['minio_access_key'] },
    { key: 'secret_key', label: 'Secret key', types: ['minio_secret_key'] },
  ],
  neo4j: [
    {
      key: 'username_password',
      label: 'Username/password',
      types: ['neo4j_username_password'],
    },
  ],
  redis: [],
}

const useStyles = createStyles(({ css, token }) => ({
  jsonInput: css`
    font-family: ${token.fontFamilyCode};
    min-width: 220px;
  `,
  table: css`
    .ant-table-cell {
      vertical-align: top;
    }
  `,
  targetMeta: css`
    max-width: 260px;
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

interface DatabaseSettingsPanelProps {
  workspaceId: WorkspaceId
}

export function DatabaseSettingsPanel({ workspaceId }: DatabaseSettingsPanelProps) {
  const { styles } = useStyles()
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [health, setHealth] = useState<DatabaseHealthSnapshotResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)
  const [secrets, setSecrets] = useState<SecretSummary[]>([])
  const [targets, setTargets] = useState<DatabaseTargetConfig[]>([])
  const [jsonDrafts, setJsonDrafts] = useState<Record<string, string>>({})
  const [jsonErrors, setJsonErrors] = useState<Record<string, string>>({})
  const validationHints = buildConfigValidationHints(targets, jsonDrafts, jsonErrors)
  const validationErrors = validationHints.filter((hint) => hint.level === 'error')
  const validationWarnings = validationHints.filter((hint) => hint.level === 'warning')
  const hasJsonErrors = Object.keys(jsonErrors).length > 0
  const hasBlockingValidation = hasJsonErrors || validationErrors.length > 0
  const enabledCount = targets.filter((target) => target.enabled).length
  const remoteCount = targets.filter((target) => target.mode === 'remote').length

  const loadDatabaseSettings = useCallback(async () => {
    setLoading(true)
    setErrorMessage(null)
    try {
      const [configResponse, healthResponse, secretsResponse] = await Promise.all([
        getDatabaseConfig(workspaceId),
        getDatabaseHealth(workspaceId),
        listSecrets(workspaceId),
      ])
      const nextTargets = orderTargets(configResponse.targets)
      setTargets(nextTargets)
      setJsonDrafts(buildJsonDrafts(nextTargets))
      setJsonErrors({})
      setHealth(healthResponse)
      setSecrets(secretsResponse.secrets)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load database settings.')
    } finally {
      setLoading(false)
    }
  }, [workspaceId])

  useEffect(() => {
    void loadDatabaseSettings()
  }, [loadDatabaseSettings])

  const configColumns = [
    {
      dataIndex: 'target',
      key: 'target',
      title: 'Target',
      render: (target: DatabaseTargetConfig['target']) => (
        <Space className={styles.targetMeta} direction="vertical" size={4}>
          <Space wrap size={4}>
            <Text strong>{target}</Text>
            {renderTargetHealthTag(target)}
          </Space>
          {target === 'redis' && <Tag>cache_only</Tag>}
        </Space>
      ),
    },
    {
      key: 'mode',
      title: 'Mode',
      render: (_: unknown, record: DatabaseTargetConfig) => (
        <Select
          data-testid={`database-mode-${record.target}`}
          onChange={(mode) => updateTarget(record.target, { mode })}
          options={[
            { label: 'local', value: 'local' },
            { label: 'remote', value: 'remote' },
          ]}
          style={{ width: 120 }}
          value={record.mode}
        />
      ),
    },
    {
      key: 'endpoint',
      title: 'Endpoint',
      render: (_: unknown, record: DatabaseTargetConfig) => (
        <Input
          data-testid={`database-endpoint-${record.target}`}
          onChange={(event) => updateTarget(record.target, { endpoint: event.target.value })}
          placeholder={endpointPlaceholder(record.target)}
          status={record.enabled && !record.endpoint.trim() ? 'error' : undefined}
          style={{ minWidth: 260 }}
          value={record.endpoint}
        />
      ),
    },
    {
      key: 'bucket',
      title: 'Bucket',
      render: (_: unknown, record: DatabaseTargetConfig) => (
        <Input
          data-testid={`database-bucket-${record.target}`}
          disabled={record.target !== 'minio'}
          onChange={(event) =>
            updateTarget(record.target, { bucket: event.target.value || null })
          }
          placeholder="agent-system"
          value={record.bucket ?? ''}
        />
      ),
    },
    {
      key: 'flags',
      title: 'Flags',
      render: (_: unknown, record: DatabaseTargetConfig) => (
        <Space direction="vertical" size={8}>
          <Space>
            <Text>Enabled</Text>
            <Switch
              checked={record.enabled}
              data-testid={`database-enabled-${record.target}`}
              onChange={(enabled) => updateTarget(record.target, { enabled })}
            />
          </Space>
          <Space>
            <Text>TLS</Text>
            <Switch
              checked={record.tls}
              data-testid={`database-tls-${record.target}`}
              onChange={(tls) => updateTarget(record.target, { tls })}
            />
          </Space>
          <Space size={4} wrap>
            <Tag color={record.enabled ? 'green' : 'default'}>
              {record.enabled ? 'enabled' : 'disabled'}
            </Tag>
            <Tag color={record.mode === 'remote' ? 'blue' : 'default'}>{record.mode}</Tag>
          </Space>
        </Space>
      ),
    },
    {
      key: 'credential_refs',
      title: 'Credential refs',
      render: (_: unknown, record: DatabaseTargetConfig) =>
        renderCredentialRefsEditor(record),
    },
    {
      key: 'options',
      title: 'Options',
      render: (_: unknown, record: DatabaseTargetConfig) => renderJsonEditor(record, 'options'),
    },
  ]

  const healthColumns = [
    {
      key: 'target',
      title: 'Target',
      render: (_: unknown, record: ServiceHealth) => record.target,
    },
    {
      dataIndex: 'status',
      key: 'status',
      title: 'Status',
      render: (status: string) => <Tag color={healthColor(status)}>{status}</Tag>,
    },
    {
      dataIndex: 'latency_ms',
      key: 'latency',
      title: 'Latency',
      render: (latency?: number | null) => (latency == null ? '-' : `${latency} ms`),
    },
    {
      dataIndex: 'message',
      key: 'message',
      title: 'Message',
      render: (message?: string | null) => <Text type="secondary">{message ?? ''}</Text>,
    },
    {
      dataIndex: 'checked_at',
      key: 'checked_at',
      title: 'Checked',
      render: (checkedAt?: string | null) => <Text type="secondary">{checkedAt ?? '-'}</Text>,
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card title="Database connections">
        <div className={styles.toolbar}>
          <Space wrap>
            <Button
              data-testid="database-refresh"
              loading={loading}
              onClick={() => void loadDatabaseSettings()}
            >
              Load config
            </Button>
            <Popconfirm
              cancelText="Cancel"
              description="Saving database config changes runtime endpoints, TLS flags, and credential references for backend services."
              disabled={hasBlockingValidation}
              okText="Save database config"
              onConfirm={() => void handleSave()}
              title="Save database connection config?"
            >
              <Button
                data-testid="database-save"
                disabled={hasBlockingValidation}
                loading={saving}
                type="primary"
              >
                Save
              </Button>
            </Popconfirm>
            <Button
              data-testid="database-health-refresh"
              loading={loading}
              onClick={() => void handleHealthRefresh()}
            >
              Get health
            </Button>
            <Button
              data-testid="database-health-check"
              loading={loading}
              onClick={() => void handleHealthCheck()}
            >
              Run health
            </Button>
          </Space>
          <Space wrap>
            <Tag>{enabledCount}/4 enabled</Tag>
            <Tag color={remoteCount ? 'blue' : 'default'}>{remoteCount} remote</Tag>
            {health && (
              <>
                <Tag color={health.ok ? 'green' : 'orange'}>{health.source}</Tag>
                <Tag color={health.ok ? 'green' : 'red'}>{health.ok ? 'ok' : 'not_ok'}</Tag>
                {health.checked_at && <Tag>{health.checked_at}</Tag>}
              </>
            )}
          </Space>
        </div>

        {errorMessage && <Alert message={errorMessage} showIcon type="error" />}
        {saveMessage && <Alert message={saveMessage} showIcon type="success" />}
        {validationErrors.length > 0 && (
          <Alert
            description={renderValidationHints(validationErrors)}
            showIcon
            title="Fix database settings before saving."
            type="error"
          />
        )}
        {validationWarnings.length > 0 && (
          <Alert
            description={renderValidationHints(validationWarnings)}
            showIcon
            title="Review database settings."
            type="warning"
          />
        )}

        <Table<DatabaseTargetConfig>
          className={styles.table}
          columns={configColumns}
          dataSource={targets}
          loading={loading && targets.length === 0}
          pagination={false}
          rowKey="target"
          scroll={{ x: 1200 }}
        />
      </Card>

      <Card title="Database health">
        <Table<ServiceHealth>
          className={styles.table}
          columns={healthColumns}
          dataSource={health?.services ?? []}
          pagination={false}
          rowKey={(record) => record.target}
        />
      </Card>
    </Space>
  )

  function updateTarget(
    targetName: DatabaseTargetConfig['target'],
    patch: Partial<DatabaseTargetConfig>,
  ) {
    setTargets((current) =>
      current.map((target) =>
        target.target === targetName ? normalizeTarget({ ...target, ...patch }) : target,
      ),
    )
    setSaveMessage(null)
  }

  function renderJsonEditor(record: DatabaseTargetConfig, field: JsonField) {
    const key = jsonDraftKey(record.target, field)
    const error = jsonErrors[key]
    return (
      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        <Input.TextArea
          autoSize={{ maxRows: 6, minRows: 3 }}
          className={styles.jsonInput}
          data-testid={`database-${field.replace('_', '-')}-${record.target}`}
          onChange={(event) => updateJsonDraft(record.target, field, event.target.value)}
          status={error ? 'error' : undefined}
          value={jsonDraftValue(record, field)}
        />
        {error ? (
          <Text type="danger">{error}</Text>
        ) : (
          <Text type="secondary">{jsonHelperText(record, field)}</Text>
        )}
      </Space>
    )
  }

  function renderCredentialRefsEditor(record: DatabaseTargetConfig) {
    const slots = DATABASE_CREDENTIAL_SLOTS[record.target]
    return (
      <Space direction="vertical" size={8} style={{ width: '100%' }}>
        {renderJsonEditor(record, 'credential_refs')}
        {slots.length > 0 && (
          <Space direction="vertical" size={4} style={{ width: '100%' }}>
            {slots.map((slot) => (
              <Space key={slot.key} direction="vertical" size={2} style={{ width: '100%' }}>
                <Select
                  allowClear
                  data-testid={`database-secret-${record.target}-${slot.key}`}
                  onChange={(value?: string) =>
                    updateCredentialSecretRef(record.target, slot.key, value)
                  }
                  optionFilterProp="label"
                  options={secretOptionsForCredentialSlot(slot, secrets)}
                  placeholder={slot.label}
                  showSearch
                  status={
                    record.enabled &&
                    record.mode === 'remote' &&
                    !currentCredentialRef(record, slot.key)
                      ? 'warning'
                      : undefined
                  }
                  style={{ minWidth: 220, width: '100%' }}
                  value={currentCredentialRef(record, slot.key)}
                />
                <Text type="secondary">{credentialSlotHelperText(slot, secrets)}</Text>
              </Space>
            ))}
          </Space>
        )}
      </Space>
    )
  }

  function renderTargetHealthTag(target: DatabaseTargetConfig['target']) {
    const serviceHealth = health?.services.find((service) => service.target === target)
    if (!serviceHealth) {
      return <Tag>no_health</Tag>
    }
    return (
      <Tag color={healthColor(serviceHealth.status)}>
        {serviceHealth.status}
        {serviceHealth.latency_ms == null ? '' : ` ${serviceHealth.latency_ms}ms`}
      </Tag>
    )
  }

  async function handleHealthRefresh() {
    setLoading(true)
    setErrorMessage(null)
    try {
      const nextHealth = await getDatabaseHealth(workspaceId)
      setHealth(nextHealth)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load database health.')
    } finally {
      setLoading(false)
    }
  }

  function updateJsonDraft(
    targetName: DatabaseTargetConfig['target'],
    field: JsonField,
    value: string,
  ) {
    const key = jsonDraftKey(targetName, field)
    setJsonDrafts((current) => ({ ...current, [key]: value }))
    setJsonErrors((current) =>
      updateJsonError(current, key, validateJsonDraft(targetName, field, value)),
    )
    setSaveMessage(null)
  }

  function updateCredentialSecretRef(
    targetName: DatabaseTargetConfig['target'],
    credentialKey: string,
    value?: string,
  ) {
    const target = targets.find((item) => item.target === targetName)
    if (!target) {
      return
    }

    const parsed = parseJsonObject(jsonDraftValue(target, 'credential_refs'))
    const current = parsed.ok ? stringifyRecord(parsed.value) : target.credential_refs
    const nextCredentialRefs = { ...current }
    if (value) {
      nextCredentialRefs[credentialKey] = value
    } else {
      delete nextCredentialRefs[credentialKey]
    }

    updateTarget(targetName, { credential_refs: nextCredentialRefs })
    updateJsonDraft(targetName, 'credential_refs', formatJson(nextCredentialRefs))
  }

  async function handleSave() {
    const parsedTargets = parseDraftTargets()
    if (!parsedTargets) {
      setErrorMessage('Fix database JSON fields before saving.')
      return
    }
    setSaving(true)
    setErrorMessage(null)
    setSaveMessage(null)
    try {
      const updated = await updateDatabaseConfig(parsedTargets.map(normalizeTarget), workspaceId)
      const nextTargets = orderTargets(updated.targets)
      setTargets(nextTargets)
      setJsonDrafts(buildJsonDrafts(nextTargets))
      setJsonErrors({})
      setSaveMessage(`Database config saved. Revision ${updated.revision}.`)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to save database config.')
    } finally {
      setSaving(false)
    }
  }

  async function handleHealthCheck() {
    setLoading(true)
    setErrorMessage(null)
    try {
      const nextHealth = await checkDatabaseHealth(workspaceId)
      setHealth(nextHealth)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to run database health check.')
    } finally {
      setLoading(false)
    }
  }

  function jsonDraftValue(record: DatabaseTargetConfig, field: JsonField) {
    return jsonDrafts[jsonDraftKey(record.target, field)] ?? formatJson(record[field] ?? {})
  }

  function parseDraftTargets() {
    const nextErrors: Record<string, string> = {}
    const nextTargets = targets.map((target) => {
      const credentialRefs = parseDraftField(target, 'credential_refs', nextErrors)
      const options = parseDraftField(target, 'options', nextErrors)
      return normalizeTarget({
        ...target,
        credential_refs: credentialRefs ?? target.credential_refs,
        options: options ?? target.options,
      })
    })
    setJsonErrors(nextErrors)
    return Object.keys(nextErrors).length > 0 ? null : nextTargets
  }

  function parseDraftField(
    target: DatabaseTargetConfig,
    field: JsonField,
    nextErrors: Record<string, string>,
  ): Record<string, string> | null {
    const key = jsonDraftKey(target.target, field)
    const value = jsonDraftValue(target, field)
    const parsed = parseJsonObject(value)
    if (!parsed.ok) {
      nextErrors[key] = `${target.target} ${field} ${parsed.error}`
      return null
    }
    const validationError = validateParsedJson(target.target, field, parsed.value)
    if (validationError) {
      nextErrors[key] = validationError
      return null
    }
    return stringifyRecord(parsed.value)
  }
}

function currentCredentialRef(record: DatabaseTargetConfig, key: string) {
  const parsed = parseJsonObject(formatJson(record.credential_refs ?? {}))
  if (!parsed.ok) {
    return undefined
  }
  const value = parsed.value[key]
  return typeof value === 'string' && value ? value : undefined
}

function secretOptionsForCredentialSlot(slot: CredentialSlot, secrets: SecretSummary[]) {
  return secrets
    .filter((secret) => secret.status === 'active' && slot.types.includes(secret.type))
    .map((secret) => ({
      label: `${secret.display_name} (${secret.type}, ${secret.masked})`,
      value: toSecretRefUri(secret.secret_ref),
    }))
}

function orderTargets(targets: DatabaseTargetConfig[]) {
  const byTarget = new Map(targets.map((target) => [target.target, normalizeTarget(target)]))
  return TARGETS.map((target) => byTarget.get(target) ?? defaultTarget(target))
}

function normalizeTarget(target: DatabaseTargetConfig): DatabaseTargetConfig {
  return {
    bucket: target.target === 'minio' ? target.bucket || null : null,
    credential_refs: stringifyRecord(target.credential_refs),
    enabled: target.enabled,
    endpoint: target.endpoint,
    mode: target.mode,
    options: stringifyRecord(
      target.target === 'redis'
        ? { ...(target.options ?? {}), role: 'cache_only' }
        : target.options ?? {},
    ),
    target: target.target,
    tls: target.tls,
  }
}

function buildJsonDrafts(targets: DatabaseTargetConfig[]) {
  return Object.fromEntries(
    targets.flatMap((target) => [
      [jsonDraftKey(target.target, 'credential_refs'), formatJson(target.credential_refs ?? {})],
      [jsonDraftKey(target.target, 'options'), formatJson(target.options ?? {})],
    ]),
  )
}

function jsonDraftKey(target: DatabaseTargetConfig['target'], field: JsonField) {
  return `${target}.${field}`
}

function formatJson(value: Record<string, unknown>) {
  return JSON.stringify(value, null, 2)
}

function updateJsonError(
  current: Record<string, string>,
  key: string,
  error: string | null,
) {
  const next = { ...current }
  if (error) {
    next[key] = error
  } else {
    delete next[key]
  }
  return next
}

function validateJsonDraft(
  target: DatabaseTargetConfig['target'],
  field: JsonField,
  value: string,
) {
  const parsed = parseJsonObject(value)
  if (!parsed.ok) {
    return `${target} ${field} ${parsed.error}`
  }
  return validateParsedJson(target, field, parsed.value)
}

function validateParsedJson(
  target: DatabaseTargetConfig['target'],
  field: JsonField,
  value: Record<string, unknown>,
) {
  if (field === 'credential_refs') {
    const invalid = Object.entries(value).find(
      ([, item]) => String(item ?? '').trim() && !String(item).trim().startsWith('secret_ref://'),
    )
    return invalid
      ? `${target} credential_refs.${invalid[0]} must use secret_ref://...`
      : null
  }
  if (field === 'options' && target === 'redis' && value.role && value.role !== 'cache_only') {
    return 'redis options.role is fixed to cache_only.'
  }
  return null
}

function buildConfigValidationHints(
  targets: DatabaseTargetConfig[],
  jsonDrafts: Record<string, string>,
  jsonErrors: Record<string, string>,
): ConfigValidationHint[] {
  return targets.flatMap((target) => {
    const hints: ConfigValidationHint[] = []

    if (target.enabled && !target.endpoint.trim()) {
      hints.push({
        level: 'error',
        message: 'Enabled target requires an endpoint.',
        target: target.target,
      })
    }

    if (target.enabled && target.mode === 'remote') {
      const credentialRefs = parseJsonObject(
        jsonDrafts[jsonDraftKey(target.target, 'credential_refs')] ??
          formatJson(target.credential_refs ?? {}),
      )

      DATABASE_CREDENTIAL_SLOTS[target.target].forEach((slot) => {
        const ref =
          credentialRefs.ok && typeof credentialRefs.value[slot.key] === 'string'
            ? credentialRefs.value[slot.key]
            : ''
        if (!String(ref).trim()) {
          hints.push({
            level: 'warning',
            message: `Remote mode usually needs ${slot.label}.`,
            target: target.target,
          })
        }
      })
    }

    if (target.enabled && target.mode === 'local' && Object.keys(target.credential_refs).length > 0) {
      hints.push({
        level: 'warning',
        message: 'Local mode has credential refs; confirm this is intentional.',
        target: target.target,
      })
    }

    if (jsonErrors[jsonDraftKey(target.target, 'credential_refs')]) {
      hints.push({
        level: 'error',
        message: 'Credential refs JSON is invalid.',
        target: target.target,
      })
    }

    if (jsonErrors[jsonDraftKey(target.target, 'options')]) {
      hints.push({
        level: 'error',
        message: 'Options JSON is invalid.',
        target: target.target,
      })
    }

    return hints
  })
}

function renderValidationHints(hints: ConfigValidationHint[]) {
  return (
    <Space direction="vertical" size={2}>
      {hints.map((hint) => (
        <Text key={`${hint.level}-${hint.target}-${hint.message}`}>
          <Text strong>{hint.target}</Text>: {hint.message}
        </Text>
      ))}
    </Space>
  )
}

function jsonHelperText(record: DatabaseTargetConfig, field: JsonField) {
  if (field === 'credential_refs') {
    if (DATABASE_CREDENTIAL_SLOTS[record.target].length === 0) {
      return 'No credential slots are required for this target.'
    }
    return 'Use secret_ref://... values or pick an active secret below.'
  }
  if (record.target === 'redis') {
    return 'Redis options.role is pinned to cache_only.'
  }
  return 'Optional backend-specific string options.'
}

function credentialSlotHelperText(slot: CredentialSlot, secrets: SecretSummary[]) {
  const count = secretOptionsForCredentialSlot(slot, secrets).length
  if (count === 0) {
    return `No active ${slot.types.join('/')} secret is available.`
  }
  return `${count} active ${slot.types.join('/')} secret${count === 1 ? '' : 's'} available.`
}

function parseJsonObject(value: string):
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; error: string } {
  try {
    const parsed = JSON.parse(value.trim() || '{}') as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { error: 'must be a JSON object.', ok: false }
    }
    return { ok: true, value: parsed as Record<string, unknown> }
  } catch {
    return { error: 'must be valid JSON.', ok: false }
  }
}

function defaultTarget(target: DatabaseTargetConfig['target']): DatabaseTargetConfig {
  return normalizeTarget({
    bucket: target === 'minio' ? 'agent-system' : null,
    credential_refs: {},
    enabled: true,
    endpoint: endpointPlaceholder(target),
    mode: 'local',
    options: target === 'redis' ? { role: 'cache_only' } : {},
    target,
    tls: false,
  })
}

function stringifyRecord(value: Record<string, unknown> = {}) {
  return Object.fromEntries(
    Object.entries(value)
      .filter(([, item]) => item !== undefined && item !== null && item !== '')
      .map(([key, item]) => [key, String(item)]),
  )
}

function toSecretRefUri(secretRef: string) {
  return secretRef.startsWith('secret_ref://') ? secretRef : `secret_ref://${secretRef}`
}

function endpointPlaceholder(target: DatabaseTargetConfig['target']) {
  if (target === 'minio') {
    return 'http://localhost:9000'
  }
  if (target === 'milvus') {
    return 'http://localhost:19530'
  }
  if (target === 'neo4j') {
    return 'bolt://localhost:7687'
  }
  return 'redis://localhost:6379/0'
}

function healthColor(status: string) {
  if (status === 'healthy') {
    return 'green'
  }
  if (status === 'unhealthy') {
    return 'red'
  }
  return 'default'
}
