'use client'

import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Row,
  Select,
  Skeleton,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import { createStyles } from 'antd-style'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  getMcpServer,
  getMcpServerHealth,
  getToolInventory,
  listSecrets,
  listMcpServers,
  listMcpTools,
  reconnectMcpServer,
  refreshMcpServer,
  saveMcpServer,
  setMcpToolPolicy,
} from '@/api/agentApiClient'
import type {
  McpServerConfigInput,
  McpServerHealthResponse,
  McpServerDetailResponse,
  McpServerSummary,
  McpToolSummary,
  SecretSummary,
  SecretType,
  ToolInventoryItem,
  WorkspaceId,
} from '@/api/schemas/workspace'

const { Text } = Typography

const DEFAULT_SERVER_NAME = 'filesystem'
const MCP_SECRET_TYPES: SecretType[] = ['mcp_headers', 'mcp_oauth_credential']
const MCP_JSON_PLACEHOLDER = `{
  "mcpServers": {
    "agent_smoke": {
      "command": "python",
      "args": ["-m", "app.mcp_smoke_server"],
      "env": {}
    }
  }
}`

const useStyles = createStyles(({ css, token }) => ({
  jsonBox: css`
    background: ${token.colorFillQuaternary};
    border: 1px solid ${token.colorBorderSecondary};
    border-radius: 6px;
    max-height: 180px;
    overflow: auto;
    padding: 10px;
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

interface McpToolsPanelProps {
  workspaceId: WorkspaceId
}

interface McpServerConfigFormValues {
  args_text?: string
  auth_type?: string
  command?: string
  cwd?: string
  enabled?: boolean
  env_json?: string
  headers_ref?: string
  oauth_credential_ref?: string
  public_headers_json?: string
  scope?: 'workspace' | 'system'
  secret_env_refs_json?: string
  server_name: string
  timeout_ms?: number
  transport: McpServerConfigInput['transport']
  url?: string
}

export function McpToolsPanel({ workspaceId }: McpToolsPanelProps) {
  const { styles } = useStyles()
  const [configForm] = Form.useForm<McpServerConfigFormValues>()
  const skipNextDefaultConfigSyncRef = useRef(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [inventory, setInventory] = useState<ToolInventoryItem[]>([])
  const [latestJobId, setLatestJobId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [mcpJsonImportMessage, setMcpJsonImportMessage] = useState<string | null>(null)
  const [mcpJson, setMcpJson] = useState('')
  const [importingMcpJson, setImportingMcpJson] = useState(false)
  const [serverDetail, setServerDetail] = useState<McpServerDetailResponse | null>(null)
  const [serverHealth, setServerHealth] = useState<McpServerHealthResponse | null>(null)
  const [reconnecting, setReconnecting] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [selectedServerName, setSelectedServerName] = useState(DEFAULT_SERVER_NAME)
  const [secrets, setSecrets] = useState<SecretSummary[]>([])
  const [savingConfig, setSavingConfig] = useState(false)
  const [servers, setServers] = useState<McpServerSummary[]>([])
  const [tools, setTools] = useState<McpToolSummary[]>([])
  const [updatingToolName, setUpdatingToolName] = useState<string | null>(null)

  const selectedServer = useMemo(
    () => servers.find((server) => server.server_name === selectedServerName),
    [selectedServerName, servers],
  )

  const loadServers = useCallback(async (preferredServerName = selectedServerName) => {
    const response = await listMcpServers(workspaceId)
    setServers(response.servers)
    const nextServerName = response.servers.some(
      (server) => server.server_name === preferredServerName,
    )
      ? preferredServerName
      : response.servers[0]?.server_name || preferredServerName || DEFAULT_SERVER_NAME
    setSelectedServerName(nextServerName)
    return nextServerName
  }, [selectedServerName, workspaceId])

  const loadSecrets = useCallback(async () => {
    const response = await listSecrets(workspaceId)
    setSecrets(response.secrets)
  }, [workspaceId])

  const loadTools = useCallback(
    async (serverName: string) => {
      if (!serverName.trim()) {
        clearSelectedServerData()
        return
      }
      const [toolResponse, inventoryResponse, healthResponse, detailResponse] = await Promise.all([
        listMcpTools(serverName, workspaceId),
        getToolInventory(workspaceId),
        getMcpServerHealth(serverName, workspaceId),
        getMcpServer(serverName, workspaceId),
      ])
      setTools(toolResponse.tools)
      setInventory(inventoryResponse.tools.filter((tool) => tool.source === 'mcp'))
      setServerHealth(healthResponse)
      setServerDetail(detailResponse)
    },
    [workspaceId],
  )

  const reload = useCallback(async () => {
    setLoading(true)
    setErrorMessage(null)
    try {
      const [nextServerName] = await Promise.all([
        loadServers(selectedServerName),
        loadSecrets(),
      ])
      await loadTools(nextServerName)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load MCP tools.')
    } finally {
      setLoading(false)
    }
  }, [loadSecrets, loadServers, loadTools, selectedServerName])

  useEffect(() => {
    void reload()
  }, [workspaceId])

  useEffect(() => {
    const server = serverDetail?.server
    if (!server) {
      if (skipNextDefaultConfigSyncRef.current) {
        skipNextDefaultConfigSyncRef.current = false
        return
      }
      configForm.setFieldsValue({
        enabled: true,
        scope: 'workspace',
        server_name: selectedServerName,
        timeout_ms: 30000,
        transport: 'stdio',
      })
      return
    }
    configForm.setFieldsValue({
      args_text: Array.isArray(server.args) ? server.args.map(String).join('\n') : '',
      auth_type: stringValue(server.auth_type),
      command: stringValue(server.command),
      cwd: stringValue(server.cwd),
      enabled: server.enabled !== false,
      env_json: formatJson(asRecord(server.env) ?? {}),
      headers_ref: stringValue(server.headers_ref),
      oauth_credential_ref: stringValue(server.oauth_credential_ref),
      public_headers_json: formatJson(asRecord(server.public_headers) ?? {}),
      scope: server.scope === 'system' ? 'system' : 'workspace',
      secret_env_refs_json: formatJson(asRecord(server.secret_env_refs) ?? {}),
      server_name: serverDetail.server_name,
      timeout_ms: typeof server.timeout_ms === 'number' ? server.timeout_ms : 30000,
      transport: isMcpTransport(server.transport) ? server.transport : 'stdio',
      url: stringValue(server.url),
    })
  }, [configForm, selectedServerName, serverDetail])

  const serverColumns = [
    {
      dataIndex: 'server_name',
      key: 'server',
      title: 'Server',
      render: (_: unknown, record: McpServerSummary) => (
        <Button
          data-testid={`mcp-server-${record.server_name}`}
          onClick={() => void handleSelectServer(record.server_name)}
          type={record.server_name === selectedServerName ? 'primary' : 'default'}
        >
          {record.server_name}
        </Button>
      ),
    },
    {
      dataIndex: 'transport',
      key: 'transport',
      title: 'Transport',
      render: (transport: string) => <Tag>{transport}</Tag>,
    },
    {
      key: 'status',
      title: 'Status',
      render: (_: unknown, record: McpServerSummary) => (
        <Space wrap>
          <Tag color={record.enabled ? 'green' : 'default'}>
            {record.enabled ? 'enabled' : 'disabled'}
          </Tag>
          <Tag color={serverStatusColor(record.status)}>{record.status ?? 'configured'}</Tag>
          {record.stale && <Tag color="orange">stale</Tag>}
        </Space>
      ),
    },
    {
      dataIndex: 'tool_count',
      key: 'tool_count',
      title: 'Tools',
      render: (toolCount?: number) => toolCount ?? 0,
    },
  ]

  const toolColumns = [
    {
      key: 'tool',
      title: 'Tool',
      render: (_: unknown, record: McpToolSummary) => (
        <Space direction="vertical" size={2}>
          <Text strong>{displayToolName(record)}</Text>
          <Text code>{record.model_name ?? record.name}</Text>
          {record.description && <Text type="secondary">{record.description}</Text>}
        </Space>
      ),
    },
    {
      key: 'policy',
      title: 'Policy',
      render: (_: unknown, record: McpToolSummary) => (
        <Space wrap>
          <Tag color={record.enabled ? 'green' : 'default'}>
            {record.enabled ? 'enabled' : 'disabled'}
          </Tag>
          <Tag color={riskColor(record.risk_level)}>{record.risk_level ?? 'medium'}</Tag>
          {record.requires_approval && <Tag color="orange">approval</Tag>}
          {record.name_conflict && <Tag color="red">name_conflict</Tag>}
          {record.disabled_reason && <Tag>{record.disabled_reason}</Tag>}
        </Space>
      ),
    },
    {
      key: 'schema',
      title: 'Schema',
      render: (_: unknown, record: McpToolSummary) => (
        <Space direction="vertical" size={2}>
          <Text code>{record.input_schema_hash ?? record.args_schema_hash ?? 'no_hash'}</Text>
          <pre className={styles.jsonBox}>{JSON.stringify(record.args_schema ?? {}, null, 2)}</pre>
        </Space>
      ),
    },
    {
      key: 'actions',
      title: 'Actions',
      render: (_: unknown, record: McpToolSummary) => {
        const toolName = policyToolName(record)
        return (
          <Popconfirm
            cancelText="Cancel"
            description={toolPolicyConfirmDescription(record)}
            okText={record.enabled ? 'Disable tool' : 'Enable tool'}
            onConfirm={() => void handleToggleTool(record)}
            title={record.enabled ? 'Disable this MCP tool?' : 'Enable this MCP tool?'}
          >
            <Button
              data-testid={`mcp-tool-toggle-${record.server_name}-${toolName}`}
              loading={updatingToolName === toolName}
            >
              {record.enabled ? 'Disable' : 'Enable'}
            </Button>
          </Popconfirm>
        )
      },
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <div className={styles.toolbar}>
        <Space wrap>
          <Input
            onChange={(event) => handleServerNameInput(event.target.value)}
            placeholder="server_name"
            style={{ width: 240 }}
            value={selectedServerName}
          />
          <Button loading={loading} onClick={() => void reload()}>
            Refresh
          </Button>
          <Popconfirm
            cancelText="Cancel"
            description="This queues a capability snapshot refresh and can change which MCP tools are visible to the model."
            okText="Queue refresh"
            onConfirm={() => void handleRefreshServer()}
            title={`Refresh capability snapshot for ${selectedServerName}?`}
          >
            <Button data-testid="mcp-refresh-server" loading={refreshing}>
              Refresh snapshot
            </Button>
          </Popconfirm>
          <Popconfirm
            cancelText="Cancel"
            description="This reconnects the MCP server and refreshes runtime capability state."
            disabled={serverHealth?.reconnect.supported === false}
            okText="Reconnect server"
            onConfirm={() => void handleReconnectServer()}
            title={`Reconnect ${selectedServerName}?`}
          >
            <Button
              data-testid="mcp-reconnect-server"
              disabled={serverHealth?.reconnect.supported === false}
              loading={reconnecting}
            >
              Reconnect
            </Button>
          </Popconfirm>
        </Space>
        {latestJobId && (
          <Space>
            <Text code>{latestJobId}</Text>
            <Button href={jobDetailHref(latestJobId)}>Jobs</Button>
          </Space>
        )}
      </div>

      {errorMessage && <Alert message={errorMessage} showIcon type="error" />}
      {mcpJsonImportMessage && <Alert message={mcpJsonImportMessage} showIcon type="success" />}
      {serverHealth?.last_error && (
        <Alert
          message={`${String(serverHealth.last_error.error_type ?? 'mcp_error')}: ${String(
            serverHealth.last_error.message ?? '',
          )}`}
          showIcon
          type={serverHealth.last_error.retryable === false ? 'error' : 'warning'}
        />
      )}
      {selectedServerNeedsSnapshot(selectedServer, serverHealth) && (
        <Alert
          description="The model can only see MCP tools after a capability snapshot succeeds. This server is configured but currently has no model-visible tools. Run Refresh snapshot, then check the Job result. For stdio MCP in Docker, the command runs inside the backend container, so Windows paths and npx.cmd are not available unless you mount the folder and install the command in that container."
          message="MCP server is not model-visible yet"
          showIcon
          type="warning"
        />
      )}

      <Row gutter={[16, 16]}>
        <Col lg={8} xs={24}>
          <Card title="Servers">
            <Table<McpServerSummary>
              className={styles.table}
              columns={serverColumns}
              dataSource={servers}
              loading={loading}
              pagination={false}
              rowKey="server_name"
            />
          </Card>
          <Card
            title="MCP JSON import"
            style={{ marginTop: 16 }}
          >
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Text type="secondary">
                Paste a JSON object with mcpServers. Load one server into the form for review, or
                import every server and queue capability snapshot refresh jobs.
              </Text>
              <Input.TextArea
                autoSize={{ maxRows: 10, minRows: 6 }}
                data-testid="mcp-json-input"
                onChange={(event) => setMcpJson(event.target.value)}
                placeholder={MCP_JSON_PLACEHOLDER}
                value={mcpJson}
              />
              <Button
                data-testid="mcp-load-json"
                onClick={() => handleLoadMcpJson()}
              >
                Load JSON into form
              </Button>
              <Popconfirm
                cancelText="Cancel"
                description="This saves every server under mcpServers, then queues snapshot refresh jobs so successful MCP tools become model-visible. Review secrets before importing plaintext headers."
                okText="Import and refresh"
                onConfirm={() => void handleImportMcpJson()}
                title="Import MCP servers and refresh snapshots?"
              >
                <Button
                  data-testid="mcp-import-json"
                  loading={importingMcpJson}
                  type="primary"
                >
                  Import and refresh
                </Button>
              </Popconfirm>
            </Space>
          </Card>
          <Card
            title={
              <Space wrap>
                <span>Server config</span>
                <Tag>{selectedServerName}</Tag>
              </Space>
            }
            style={{ marginTop: 16 }}
          >
            <Form
              form={configForm}
              initialValues={{
                enabled: true,
                scope: 'workspace',
                server_name: selectedServerName,
                timeout_ms: 30000,
                transport: 'stdio',
              }}
              layout="vertical"
            >
              <Form.Item label="Server" name="server_name" rules={[{ required: true }]}>
                <Input placeholder="filesystem" />
              </Form.Item>
              <Row gutter={8}>
                <Col span={12}>
                  <Form.Item label="Transport" name="transport" rules={[{ required: true }]}>
                    <Select
                      options={[
                        { label: 'stdio', value: 'stdio' },
                        { label: 'http', value: 'http' },
                        { label: 'streamable_http', value: 'streamable_http' },
                        { label: 'sse', value: 'sse' },
                      ]}
                    />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="Timeout" name="timeout_ms">
                    <InputNumber min={1000} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={8}>
                <Col span={12}>
                  <Form.Item label="Scope" name="scope">
                    <Select
                      options={[
                        { label: 'workspace', value: 'workspace' },
                        { label: 'system', value: 'system' },
                      ]}
                    />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="Enabled" name="enabled" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item label="Command" name="command">
                <Input placeholder="python" />
              </Form.Item>
              <Form.Item label="Args" name="args_text">
                <Input.TextArea autoSize={{ maxRows: 4, minRows: 2 }} />
              </Form.Item>
              <Form.Item label="CWD" name="cwd">
                <Input />
              </Form.Item>
              <Form.Item label="URL" name="url">
                <Input placeholder="http://localhost:3001/mcp" />
              </Form.Item>
              <Form.Item label="Public headers JSON" name="public_headers_json">
                <Input.TextArea autoSize={{ maxRows: 4, minRows: 2 }} />
              </Form.Item>
              <Form.Item label="Env JSON" name="env_json">
                <Input.TextArea autoSize={{ maxRows: 4, minRows: 2 }} />
              </Form.Item>
              <Form.Item label="Secret env refs JSON" name="secret_env_refs_json">
                <Input.TextArea
                  autoSize={{ maxRows: 4, minRows: 2 }}
                  placeholder='{"GITHUB_TOKEN":"secret_ref://mcp_headers/github"}'
                />
              </Form.Item>
              <Text type="secondary">
                {mcpSecretHelperText(secrets)}
              </Text>
              <Form.Item label="Headers ref">
                <Space.Compact style={{ width: '100%' }}>
                  <Form.Item name="headers_ref" noStyle>
                    <Input placeholder="secret_ref://mcp_headers/name" />
                  </Form.Item>
                  <Select
                    data-testid="mcp-headers-secret-ref"
                    onChange={(value) => configForm.setFieldsValue({ headers_ref: value })}
                    optionFilterProp="label"
                    options={secretOptionsForTypes(secrets, ['mcp_headers'])}
                    placeholder="Pick"
                    showSearch
                    style={{ width: 160 }}
                  />
                </Space.Compact>
              </Form.Item>
              <Form.Item label="Auth type" name="auth_type">
                <Input placeholder="bearer" />
              </Form.Item>
              <Form.Item label="OAuth credential ref">
                <Space.Compact style={{ width: '100%' }}>
                  <Form.Item name="oauth_credential_ref" noStyle>
                    <Input placeholder="secret_ref://mcp_oauth_credential/name" />
                  </Form.Item>
                  <Select
                    data-testid="mcp-oauth-secret-ref"
                    onChange={(value) => configForm.setFieldsValue({ oauth_credential_ref: value })}
                    optionFilterProp="label"
                    options={secretOptionsForTypes(secrets, ['mcp_oauth_credential'])}
                    placeholder="Pick"
                    showSearch
                    style={{ width: 160 }}
                  />
                </Space.Compact>
              </Form.Item>
              <Popconfirm
                cancelText="Cancel"
                description="Saving server config can invalidate the current capability snapshot and change model-visible tools after refresh."
                okText="Confirm save"
                onConfirm={() => void handleConfirmSaveServerConfig()}
                title={`Save MCP config for ${selectedServerName}?`}
              >
                <Button data-testid="mcp-save-server-config" loading={savingConfig}>
                  Save config
                </Button>
              </Popconfirm>
            </Form>
          </Card>
          <Card
            title={
              <Space wrap>
                <span>Server details</span>
                <Tag>{selectedServerName}</Tag>
              </Space>
            }
            style={{ marginTop: 16 }}
          >
            {loading && !serverDetail ? (
              <Skeleton active paragraph={{ rows: 6 }} />
            ) : (
              <McpServerDetails
                detail={serverDetail}
                health={serverHealth}
                jsonBoxClassName={styles.jsonBox}
                summary={selectedServer}
              />
            )}
          </Card>
        </Col>
        <Col lg={16} xs={24}>
          <Card
            title={
              <Space wrap>
                <span>Tools</span>
                <Tag>{selectedServerName}</Tag>
                {selectedServer?.last_seen && <Tag>{selectedServer.last_seen}</Tag>}
                {serverHealth && <Tag color={serverStatusColor(serverHealth.status)}>{serverHealth.status}</Tag>}
                {serverHealth?.runtime_configured === false && <Tag color="orange">not_configured</Tag>}
              </Space>
            }
          >
            <Table<McpToolSummary>
              className={styles.table}
              columns={toolColumns}
              dataSource={tools}
              loading={loading}
              pagination={{ pageSize: 8 }}
              rowKey={(record) => `${record.server_name}:${policyToolName(record)}`}
            />
          </Card>
        </Col>
      </Row>

      <Card title="Model-visible MCP inventory">
        <Table<ToolInventoryItem>
          className={styles.table}
          dataSource={inventory}
          pagination={{ pageSize: 8 }}
          rowKey={(record) => record.name}
          columns={[
            {
              dataIndex: 'name',
              key: 'name',
              title: 'Model tool',
              render: (name: string) => <Text code>{name}</Text>,
            },
            {
              key: 'source',
              title: 'Source',
              render: (_: unknown, record: ToolInventoryItem) => (
                <Space wrap>
                  <Tag>{record.server_name ?? 'mcp'}</Tag>
                  <Tag color={record.enabled === false ? 'default' : 'green'}>
                    {record.enabled === false ? 'disabled' : 'enabled'}
                  </Tag>
                </Space>
              ),
            },
            {
              key: 'description',
              title: 'Description',
              render: (_: unknown, record: ToolInventoryItem) => (
                <Text type="secondary">{record.description ?? ''}</Text>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  )

  async function handleSelectServer(serverName: string) {
    setSelectedServerName(serverName)
    clearSelectedServerData()
    setLoading(true)
    setErrorMessage(null)
    try {
      await loadTools(serverName)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load MCP server tools.')
    } finally {
      setLoading(false)
    }
  }

  async function handleSaveServerConfig(values: McpServerConfigFormValues) {
    const serverName = values.server_name.trim()
    if (!serverName) {
      setErrorMessage('Server name is required.')
      return
    }
    setSavingConfig(true)
    setErrorMessage(null)
    try {
      const payload = formValuesToMcpServerConfig(values)
      const detail = await saveMcpServer(serverName, payload, workspaceId)
      setSelectedServerName(detail.server_name)
      setServerDetail(detail)
      await loadServers(detail.server_name)
      await loadTools(detail.server_name)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to save MCP server config.')
    } finally {
      setSavingConfig(false)
    }
  }

  async function handleConfirmSaveServerConfig() {
    try {
      const values = await configForm.validateFields()
      await handleSaveServerConfig(values)
    } catch {
      // Validation errors are already surfaced by Antd form fields.
    }
  }

  async function handleRefreshServer() {
    setRefreshing(true)
    setErrorMessage(null)
    try {
      const response = await refreshMcpServer(
        selectedServerName,
        { refresh_reason: 'manual_frontend' },
        workspaceId,
      )
      setLatestJobId(response.job_id)
      await loadServers()
      await loadTools(selectedServerName)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to refresh MCP snapshot.')
    } finally {
      setRefreshing(false)
    }
  }

  async function handleReconnectServer() {
    setReconnecting(true)
    setErrorMessage(null)
    try {
      const response = await reconnectMcpServer(selectedServerName, {}, workspaceId)
      setLatestJobId(response.job_id)
      setServerHealth(response.health)
      await loadServers()
      await loadTools(selectedServerName)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to reconnect MCP server.')
    } finally {
      setReconnecting(false)
    }
  }

  async function handleToggleTool(tool: McpToolSummary) {
    const toolName = policyToolName(tool)
    setUpdatingToolName(toolName)
    setErrorMessage(null)
    try {
      await setMcpToolPolicy(
        {
          enabled: !tool.enabled,
          input_schema_hash: tool.input_schema_hash ?? tool.args_schema_hash ?? null,
          risk_level: tool.risk_level ?? 'medium',
          server_name: tool.server_name,
          tool_name: toolName,
        },
        workspaceId,
      )
      await loadTools(tool.server_name)
      await loadServers()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to update MCP tool policy.')
    } finally {
      setUpdatingToolName(null)
    }
  }

  function handleServerNameInput(serverName: string) {
    setSelectedServerName(serverName)
    if (serverName !== serverDetail?.server_name) {
      clearSelectedServerData()
    }
  }

  function handleLoadMcpJson() {
    setErrorMessage(null)
    setMcpJsonImportMessage(null)
    try {
      const imports = parseMcpJson(mcpJson)
      const preferred = imports.find((item) => item.serverName === selectedServerName)
      const imported = preferred ?? imports[0]
      skipNextDefaultConfigSyncRef.current = true
      setSelectedServerName(imported.serverName)
      clearSelectedServerData()
      configForm.setFieldsValue(imported.values)
      setMcpJsonImportMessage(
        imports.length > 1
          ? `Loaded ${imported.serverName} from MCP JSON. ${imports.length} servers were found; change the server name field and load again to pick another configured server.`
          : `Loaded ${imported.serverName} from MCP JSON. Review the form and save when ready.`,
      )
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to parse MCP JSON.')
    }
  }

  async function handleImportMcpJson() {
    setImportingMcpJson(true)
    setErrorMessage(null)
    setMcpJsonImportMessage(null)
    try {
      const imports = parseMcpJson(mcpJson)
      const saved = []
      const refreshJobs: string[] = []
      const refreshFailures: string[] = []
      for (const item of imports) {
        saved.push(
          await saveMcpServer(
            item.serverName,
            formValuesToMcpServerConfig(item.values),
            workspaceId,
          ),
        )
      }
      for (const detail of saved) {
        if (!mcpServerCanRefresh(detail.server)) {
          continue
        }
        try {
          const refresh = await refreshMcpServer(
            detail.server_name,
            { refresh_reason: 'mcp_json_import' },
            workspaceId,
          )
          refreshJobs.push(refresh.job_id)
        } catch (error) {
          refreshFailures.push(
            `${detail.server_name}: ${
              error instanceof Error ? error.message : 'snapshot refresh failed'
            }`,
          )
        }
      }
      const selectedName = saved[0]?.server_name || imports[0]?.serverName || selectedServerName
      setSelectedServerName(selectedName)
      setServerDetail(saved[0] ?? null)
      setLatestJobId(refreshJobs.at(-1) ?? null)
      await loadServers(selectedName)
      await loadTools(selectedName)
      const refreshMessage =
        refreshJobs.length > 0
          ? ` Queued ${refreshJobs.length} snapshot refresh job${
              refreshJobs.length === 1 ? '' : 's'
            }: ${refreshJobs.join(', ')}.`
          : ' No snapshot refresh job was queued because no imported server had a runtime command or URL.'
      const failureMessage =
        refreshFailures.length > 0
          ? ` Refresh failed for ${refreshFailures.join('; ')}.`
          : ''
      setMcpJsonImportMessage(
        `Imported ${saved.length} MCP server${
          saved.length === 1 ? '' : 's'
        } from MCP JSON.${refreshMessage}${failureMessage} The model can see MCP tools only after the snapshot refresh job succeeds.`,
      )
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to import MCP JSON.')
    } finally {
      setImportingMcpJson(false)
    }
  }

  function clearSelectedServerData() {
    setServerDetail(null)
    setServerHealth(null)
    setTools([])
  }
}

function toolPolicyConfirmDescription(tool: McpToolSummary) {
  const modelName = tool.model_name ?? tool.name
  return tool.enabled
    ? `Disabling ${modelName} removes it from the model-visible MCP inventory.`
    : `Enabling ${modelName} makes it available in the model-visible MCP inventory.`
}

interface McpServerDetailsProps {
  detail: McpServerDetailResponse | null
  health: McpServerHealthResponse | null
  jsonBoxClassName: string
  summary?: McpServerSummary
}

function McpServerDetails({
  detail,
  health,
  jsonBoxClassName,
  summary,
}: McpServerDetailsProps) {
  if (!detail) {
    return <Empty description="No server details loaded" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  }

  const manifest = detail.server
  const snapshot = asRecord(detail.snapshot)
  const lastError = asRecord(health?.last_error ?? manifest.last_error ?? snapshot?.last_error)
  const objectKeys = collectObjectKeys(detail).slice(0, 12)
  const status = stringValue(health?.status ?? manifest.status ?? snapshot?.status ?? summary?.status)
  const transport = stringValue(
    health?.transport ?? manifest.transport ?? manifest.type ?? snapshot?.transport ?? summary?.transport,
  )
  const stale = health?.stale ?? manifest.stale ?? snapshot?.stale ?? summary?.stale
  const reconnectSupported = health?.reconnect.supported
  const reconnectMode = health?.reconnect.mode

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Descriptions
        bordered
        column={1}
        items={[
          {
            children: <Tag>{transport || 'unknown'}</Tag>,
            key: 'transport',
            label: 'Transport',
          },
          {
            children: <Tag color={serverStatusColor(status)}>{status || 'configured'}</Tag>,
            key: 'status',
            label: 'Status',
          },
          {
            children: boolLabel(stale),
            key: 'stale',
            label: 'Stale',
          },
          {
            children: (
              <Space wrap>
                <Tag color={reconnectSupported ? 'green' : 'default'}>
                  {reconnectSupported ? 'supported' : 'unsupported'}
                </Tag>
                {reconnectMode && <Tag>{reconnectMode}</Tag>}
              </Space>
            ),
            key: 'reconnect',
            label: 'Reconnect',
          },
        ]}
        size="small"
      />

      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        <Text strong>Last error</Text>
        {lastError ? (
          <pre className={jsonBoxClassName}>{formatJson(lastError)}</pre>
        ) : (
          <Text type="secondary">No last_error reported.</Text>
        )}
      </Space>

      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        <Text strong>Object keys</Text>
        {objectKeys.length > 0 ? (
          <Space direction="vertical" size={2} style={{ width: '100%' }}>
            {objectKeys.map((entry) => (
              <Text code key={`${entry.path}:${entry.value}`} style={{ wordBreak: 'break-all' }}>
                {entry.path}: {entry.value}
              </Text>
            ))}
          </Space>
        ) : (
          <Text type="secondary">No object keys in the detail payload.</Text>
        )}
      </Space>

      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        <Text strong>Manifest / config snapshot</Text>
        <pre className={jsonBoxClassName}>{formatJson(manifest)}</pre>
      </Space>

      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        <Text strong>Capability snapshot</Text>
        {snapshot ? (
          <pre className={jsonBoxClassName}>{formatJson(snapshot)}</pre>
        ) : (
          <Text type="secondary">No capability snapshot available.</Text>
        )}
      </Space>
    </Space>
  )
}

function displayToolName(tool: McpToolSummary) {
  return tool.tool_name ?? tool.original_tool_name ?? tool.normalized_tool_name ?? tool.name
}

function policyToolName(tool: McpToolSummary) {
  return tool.tool_name ?? tool.original_tool_name ?? tool.normalized_tool_name ?? tool.name
}

function riskColor(risk?: string) {
  if (risk === 'critical' || risk === 'high') {
    return 'red'
  }
  if (risk === 'medium') {
    return 'orange'
  }
  return 'green'
}

function serverStatusColor(status?: string) {
  if (status === 'connected') {
    return 'green'
  }
  if (status === 'failed' || status === 'auth_failed' || status === 'tool_list_failed') {
    return 'red'
  }
  if (status === 'starting' || status === 'initializing' || status === 'restarting') {
    return 'blue'
  }
  return 'default'
}

function selectedServerNeedsSnapshot(
  selectedServer: McpServerSummary | undefined,
  serverHealth: McpServerHealthResponse | null,
) {
  if (!selectedServer) {
    return false
  }
  const toolCount = serverHealth?.tool_count ?? selectedServer.tool_count ?? 0
  return selectedServer.enabled && (toolCount === 0 || selectedServer.stale || serverHealth?.stale)
}

function jobDetailHref(jobId: string) {
  return `/jobs?job_id=${encodeURIComponent(jobId)}`
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return null
}

function boolLabel(value: unknown) {
  if (value === true) {
    return 'yes'
  }
  if (value === false) {
    return 'no'
  }
  return 'unknown'
}

function collectObjectKeys(value: unknown, path = ''): Array<{ path: string; value: string }> {
  const record = asRecord(value)
  if (!record) {
    return []
  }

  return Object.entries(record).flatMap(([key, item]) => {
    const nextPath = path ? `${path}.${key}` : key
    if (key === 'object_keys' && asRecord(item)) {
      return Object.entries(asRecord(item) ?? {}).map(([objectKeyName, objectKeyValue]) => ({
        path: `${nextPath}.${objectKeyName}`,
        value: String(objectKeyValue),
      }))
    }
    if (key.endsWith('_object_key') && item !== undefined && item !== null) {
      return [{ path: nextPath, value: String(item) }]
    }
    if (asRecord(item)) {
      return collectObjectKeys(item, nextPath)
    }
    return []
  })
}

function formatJson(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2)
}

function stringValue(value: unknown) {
  return typeof value === 'string' ? value : ''
}

function emptyToNull(value: string | undefined) {
  const trimmed = value?.trim()
  return trimmed ? trimmed : null
}

function parseArgsText(value: string | undefined) {
  const text = value?.trim()
  if (!text) {
    return []
  }
  if (text.startsWith('[')) {
    const parsed = JSON.parse(text) as unknown
    if (Array.isArray(parsed)) {
      return parsed.map(String)
    }
    throw new Error('Args JSON must be an array.')
  }
  return text.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
}

function parseJsonRecord(value: string | undefined) {
  const text = value?.trim()
  if (!text) {
    return {}
  }
  const parsed = JSON.parse(text) as unknown
  const record = asRecord(parsed)
  if (!record) {
    throw new Error('JSON field must be an object.')
  }
  return Object.fromEntries(
    Object.entries(record).map(([key, item]) => [key, String(item)]),
  )
}

function parseSecretRefRecord(value: string | undefined) {
  const record = parseJsonRecord(value)
  return Object.fromEntries(
    Object.entries(record).map(([key, item]) => [key, normalizeSecretRef(item, 'mcp_headers')]),
  )
}

function formValuesToMcpServerConfig(values: McpServerConfigFormValues): McpServerConfigInput {
  return {
    args: parseArgsText(values.args_text),
    auth_type: emptyToNull(values.auth_type),
    command: emptyToNull(values.command),
    cwd: emptyToNull(values.cwd),
    enabled: values.enabled !== false,
    env: parseJsonRecord(values.env_json),
    headers_ref: normalizeOptionalSecretRef(values.headers_ref, 'mcp_headers'),
    oauth_credential_ref: normalizeOptionalSecretRef(
      values.oauth_credential_ref,
      'mcp_oauth_credential',
    ),
    public_headers: parseJsonRecord(values.public_headers_json),
    scope: values.scope ?? 'workspace',
    secret_env_refs: parseSecretRefRecord(values.secret_env_refs_json),
    timeout_ms: values.timeout_ms ?? 30000,
    transport: values.transport,
    url: emptyToNull(values.url),
  }
}

function parseMcpJson(value: string) {
  const text = value.trim()
  if (!text) {
    throw new Error('MCP JSON is required.')
  }
  const root = asRecord(JSON.parse(text))
  const mcpServers = asRecord(root?.mcpServers)
  if (!mcpServers) {
    throw new Error('MCP JSON must include an mcpServers object.')
  }
  const entries = Object.entries(mcpServers)
  if (entries.length === 0) {
    throw new Error('mcpServers is empty. Add at least one named MCP server.')
  }

  return entries.map(([serverName, server]) => {
    const record = asRecord(server)
    if (!record) {
      throw new Error(`MCP server ${serverName} must be a JSON object.`)
    }
    return {
      serverName,
      values: mcpServerToFormValues(serverName, record),
    }
  })
}

function mcpServerToFormValues(
  serverName: string,
  server: Record<string, unknown>,
): McpServerConfigFormValues {
  const transport = inferMcpTransport(server)
  const enabled = server.disabled === true ? false : server.enabled !== false
  const headers = asRecord(server.headers)
  const publicHeaders = asRecord(server.public_headers) ?? headers ?? {}
  const timeoutMs = numberValue(server.timeout_ms) ?? numberValue(server.timeoutMs) ?? numberValue(server.timeout)

  return {
    args_text: formatArgsText(server.args),
    auth_type: stringValue(server.auth_type),
    command: stringValue(server.command),
    cwd: stringValue(server.cwd),
    enabled,
    env_json: formatJson(stringRecord(server.env)),
    headers_ref: stringValue(server.headers_ref),
    oauth_credential_ref: stringValue(server.oauth_credential_ref),
    public_headers_json: formatJson(stringRecord(publicHeaders)),
    scope: server.scope === 'system' ? 'system' : 'workspace',
    secret_env_refs_json: formatJson(stringRecord(server.secret_env_refs)),
    server_name: serverName,
    timeout_ms: timeoutMs ?? 30000,
    transport,
    url: stringValue(server.url),
  }
}

function inferMcpTransport(
  server: Record<string, unknown>,
): McpServerConfigInput['transport'] {
  const declared = stringValue(server.transport) || stringValue(server.type)
  if (isMcpTransport(declared)) {
    return declared
  }
  if (declared === 'http' || declared === 'streamable-http') {
    return 'streamable_http'
  }
  if (stringValue(server.url)) {
    return 'streamable_http'
  }
  return 'stdio'
}

function formatArgsText(value: unknown) {
  if (!value) {
    return ''
  }
  if (Array.isArray(value)) {
    return value.map(String).join('\n')
  }
  if (typeof value === 'string') {
    return value
  }
  throw new Error('MCP args must be a string array or string.')
}

function stringRecord(value: unknown) {
  const record = asRecord(value)
  if (!record) {
    return {}
  }
  return Object.fromEntries(
    Object.entries(record).map(([key, item]) => [key, String(item)]),
  )
}

function numberValue(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : undefined
  }
  return undefined
}

function normalizeOptionalSecretRef(value: string | undefined, type: SecretType) {
  const trimmed = value?.trim()
  return trimmed ? normalizeSecretRef(trimmed, type) : null
}

function normalizeSecretRef(value: string, type: SecretType) {
  const trimmed = value.trim()
  if (!trimmed) {
    return trimmed
  }
  if (trimmed.startsWith('secret_ref://')) {
    return trimmed
  }
  if (trimmed.includes('/')) {
    return `secret_ref://${trimmed.replace(/^\/+/, '')}`
  }
  return `secret_ref://${type}/${trimmed}`
}

function mcpServerCanRefresh(server: unknown) {
  const record = asRecord(server)
  if (!record || record.enabled === false) {
    return false
  }
  const transport = stringValue(record.transport) || stringValue(record.type)
  if (transport === 'stdio') {
    return Boolean(stringValue(record.command))
  }
  if (transport === 'http' || transport === 'streamable_http' || transport === 'sse') {
    return Boolean(stringValue(record.url))
  }
  return false
}

function secretOptionsForTypes(secrets: SecretSummary[], types: SecretType[]) {
  return secrets
    .filter((secret) => secret.status === 'active' && types.includes(secret.type))
    .map((secret) => ({
      label: `${secret.display_name} (${secret.type}, ${secret.masked})`,
      value: normalizeSecretRef(secret.secret_ref, secret.type),
    }))
}

function mcpSecretHelperText(secrets: SecretSummary[]) {
  const activeCount = secrets.filter(
    (secret) => secret.status === 'active' && MCP_SECRET_TYPES.includes(secret.type),
  ).length
  if (activeCount === 0) {
    return 'No active MCP header or OAuth secrets are available. Create them in Settings > Secrets, or enter a secret_ref://... value manually.'
  }
  return `${activeCount} active MCP secret${activeCount === 1 ? '' : 's'} available. Secret refs are saved as secret_ref://... values.`
}

function isMcpTransport(value: unknown): value is McpServerConfigInput['transport'] {
  return value === 'stdio' || value === 'http' || value === 'streamable_http' || value === 'sse'
}
