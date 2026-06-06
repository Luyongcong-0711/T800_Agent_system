'use client'

import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { createStyles } from 'antd-style'
import { useCallback, useEffect, useState } from 'react'

import {
  createSecret,
  deleteSecret,
  disableSecret,
  getSecretReferences,
  listSecrets,
  rotateSecret,
  updateSecret,
} from '@/api/agentApiClient'
import type {
  CreateSecretInput,
  SecretReferencesResponse,
  SecretSummary,
  SecretType,
  WorkspaceId,
} from '@/api/schemas/workspace'

const { Text } = Typography

const SECRET_TYPES: SecretType[] = [
  'model_api_key',
  'embedding_api_key',
  'rerank_api_key',
  'minio_access_key',
  'minio_secret_key',
  'milvus_token',
  'milvus_username_password',
  'neo4j_username_password',
  'mcp_headers',
  'mcp_oauth_credential',
  'http_proxy_credential',
  'web_fetch_credential',
]

const useStyles = createStyles(({ css }) => ({
  formGrid: css`
    display: grid;
    gap: 8px;
    grid-template-columns: repeat(3, minmax(0, 1fr));

    @media (max-width: 920px) {
      grid-template-columns: 1fr;
    }
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

interface SecretsPanelProps {
  workspaceId: WorkspaceId
}

export function SecretsPanel({ workspaceId }: SecretsPanelProps) {
  const { styles } = useStyles()
  const [createForm] = Form.useForm<CreateSecretInput>()
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [references, setReferences] = useState<SecretReferencesResponse | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [rotatePlaintext, setRotatePlaintext] = useState('')
  const [secrets, setSecrets] = useState<SecretSummary[]>([])
  const [selectedSecret, setSelectedSecret] = useState<SecretSummary | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const loadSecrets = useCallback(async () => {
    setLoading(true)
    setErrorMessage(null)
    try {
      const response = await listSecrets(workspaceId)
      setSecrets(response.secrets)
      setSelectedSecret((current) =>
        current
          ? response.secrets.find((secret) => secret.secret_id === current.secret_id) ?? null
          : null,
      )
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load secrets.')
    } finally {
      setLoading(false)
    }
  }, [workspaceId])

  useEffect(() => {
    void loadSecrets()
  }, [loadSecrets])

  const columns = [
    {
      key: 'name',
      title: 'Name',
      render: (_: unknown, record: SecretSummary) => (
        <Space direction="vertical" size={2}>
          <Text>{record.display_name}</Text>
          <Text code>{record.secret_ref}</Text>
        </Space>
      ),
    },
    {
      dataIndex: 'type',
      key: 'type',
      title: 'Type',
      render: (type: SecretType) => <Tag>{type}</Tag>,
    },
    {
      dataIndex: 'masked',
      key: 'masked',
      title: 'Masked',
      render: (masked: string) => <Text code>{masked}</Text>,
    },
    {
      dataIndex: 'status',
      key: 'status',
      title: 'Status',
      render: (status: SecretSummary['status']) => <Tag color={statusColor(status)}>{status}</Tag>,
    },
    {
      dataIndex: 'updated_at',
      key: 'updated_at',
      title: 'Updated',
      render: (updatedAt: string) => <Text type="secondary">{updatedAt}</Text>,
    },
    {
      key: 'actions',
      title: 'Actions',
      render: (_: unknown, record: SecretSummary) => (
        <Space wrap>
          <Button
            data-testid={`secret-select-${record.secret_id}`}
            onClick={() => handleSelectSecret(record)}
          >
            Select
          </Button>
          <Popconfirm
            cancelText="Cancel"
            description={secretToggleConfirmDescription(record)}
            okText={record.status === 'disabled' ? 'Enable secret' : 'Disable secret'}
            onConfirm={() => void handleToggleSecret(record)}
            title={record.status === 'disabled' ? 'Enable this secret?' : 'Disable this secret?'}
          >
            <Button data-testid={`secret-toggle-${record.secret_id}`}>
              {record.status === 'disabled' ? 'Enable' : 'Disable'}
            </Button>
          </Popconfirm>
          <Button
            data-testid={`secret-references-${record.secret_id}`}
            onClick={() => void handleReferences(record)}
          >
            References
          </Button>
          <Popconfirm
            cancelText="Cancel"
            description="This may break any model, database, or MCP configuration that still references the secret."
            okText="Delete secret"
            onConfirm={() => void handleDeleteSecret(record)}
            title="Delete this secret?"
          >
            <Button danger data-testid={`secret-delete-${record.secret_id}`}>
              Delete
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card title="Secrets">
        <div className={styles.toolbar}>
          <Button loading={loading} onClick={() => void loadSecrets()}>
            Refresh
          </Button>
          <Text type="secondary">{secrets.length} configured</Text>
        </div>

        {errorMessage && <Alert message={errorMessage} showIcon type="error" />}

        <Form
          form={createForm}
          initialValues={{ type: 'model_api_key' }}
          layout="vertical"
          onFinish={handleCreateSecret}
        >
          <div className={styles.formGrid}>
            <Form.Item label="Type" name="type" rules={[{ required: true }]}>
              <Select
                options={SECRET_TYPES.map((type) => ({ label: type, value: type }))}
              />
            </Form.Item>
            <Form.Item label="Name" name="display_name" rules={[{ required: true }]}>
              <Input placeholder="Main chat API key" />
            </Form.Item>
            <Form.Item label="Value" name="plaintext" rules={[{ required: true }]}>
              <Input.Password data-testid="secret-plaintext" placeholder="stored encrypted" />
            </Form.Item>
          </div>
          <Button data-testid="secret-create" htmlType="submit" loading={submitting}>
            Create
          </Button>
        </Form>

        <Table<SecretSummary>
          columns={columns}
          dataSource={secrets}
          loading={loading}
          pagination={{ pageSize: 8 }}
          rowKey="secret_id"
          style={{ marginTop: 16 }}
        />
      </Card>

      <Card title="Selected secret">
        {selectedSecret ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Space wrap>
              <Text code>{selectedSecret.secret_id}</Text>
              <Tag color={statusColor(selectedSecret.status)}>{selectedSecret.status}</Tag>
              <Text code>{selectedSecret.masked}</Text>
            </Space>
            <Space wrap>
              <Input
                data-testid="secret-rename-input"
                onChange={(event) => setRenameValue(event.target.value)}
                placeholder="Display name"
                style={{ width: 260 }}
                value={renameValue}
              />
              <Button
                data-testid="secret-rename"
                disabled={!renameValue.trim()}
                loading={submitting}
                onClick={() => void handleRenameSecret()}
              >
                Rename
              </Button>
            </Space>
            <Space wrap>
              <Input.Password
                data-testid="secret-rotate-plaintext"
                onChange={(event) => setRotatePlaintext(event.target.value)}
                placeholder="new encrypted value"
                style={{ width: 260 }}
                value={rotatePlaintext}
              />
              <Popconfirm
                cancelText="Cancel"
                description="The previous value will stop being used after rotation succeeds."
                disabled={!rotatePlaintext}
                okText="Rotate secret"
                onConfirm={() => void handleRotateSecret()}
                title="Rotate this secret?"
              >
                <Button
                  data-testid="secret-rotate"
                  disabled={!rotatePlaintext}
                  loading={submitting}
                >
                  Rotate
                </Button>
              </Popconfirm>
            </Space>
            <Space direction="vertical" size={4}>
              <Text strong>References</Text>
              {(references?.references ?? []).length === 0 ? (
                <Text type="secondary">No references.</Text>
              ) : (
                references?.references.map((reference, index) => (
                  <Text code key={index}>
                    {Object.entries(reference)
                      .map(([key, value]) => `${key}=${value}`)
                      .join(' ')}
                  </Text>
                ))
              )}
            </Space>
          </Space>
        ) : (
          <Text type="secondary">No secret selected.</Text>
        )}
      </Card>
    </Space>
  )

  async function handleCreateSecret(values: CreateSecretInput) {
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const created = await createSecret(values, workspaceId)
      upsertSecret(created)
      createForm.resetFields()
      createForm.setFieldValue('type', 'model_api_key')
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to create secret.')
    } finally {
      setSubmitting(false)
    }
  }

  function handleSelectSecret(secret: SecretSummary) {
    setSelectedSecret(secret)
    setRenameValue(secret.display_name)
    setRotatePlaintext('')
    setReferences(null)
  }

  async function handleRenameSecret() {
    if (!selectedSecret || !renameValue.trim()) {
      return
    }
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const updated = await updateSecret(
        selectedSecret.secret_id,
        { display_name: renameValue.trim() },
        workspaceId,
      )
      upsertSecret(updated)
      setSelectedSecret(updated)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to rename secret.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleRotateSecret() {
    if (!selectedSecret || !rotatePlaintext) {
      return
    }
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const updated = await rotateSecret(
        selectedSecret.secret_id,
        { plaintext: rotatePlaintext },
        workspaceId,
      )
      upsertSecret(updated)
      setSelectedSecret(updated)
      setRotatePlaintext('')
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to rotate secret.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleToggleSecret(secret: SecretSummary) {
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const updated =
        secret.status === 'disabled'
          ? await updateSecret(secret.secret_id, { status: 'active' }, workspaceId)
          : await disableSecret(secret.secret_id, workspaceId)
      upsertSecret(updated)
      if (selectedSecret?.secret_id === updated.secret_id) {
        setSelectedSecret(updated)
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to update secret status.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleDeleteSecret(secret: SecretSummary) {
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const deleted = await deleteSecret(secret.secret_id, workspaceId)
      setSecrets((current) => current.filter((item) => item.secret_id !== deleted.secret_id))
      if (selectedSecret?.secret_id === deleted.secret_id) {
        setSelectedSecret(null)
        setReferences(null)
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to delete secret.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleReferences(secret: SecretSummary) {
    setLoading(true)
    setErrorMessage(null)
    try {
      handleSelectSecret(secret)
      const response = await getSecretReferences(secret.secret_id, workspaceId)
      setReferences(response)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load secret references.')
    } finally {
      setLoading(false)
    }
  }

  function upsertSecret(secret: SecretSummary) {
    setSecrets((current) => [
      secret,
      ...current.filter((item) => item.secret_id !== secret.secret_id),
    ])
  }
}

function statusColor(status: SecretSummary['status']) {
  if (status === 'active') {
    return 'green'
  }
  if (status === 'disabled') {
    return 'orange'
  }
  if (status === 'soft_deleted') {
    return 'red'
  }
  return 'blue'
}

function secretToggleConfirmDescription(secret: SecretSummary) {
  if (secret.status === 'disabled') {
    return 'The secret can be referenced by model, database, or MCP configuration again.'
  }
  return 'Any model, database, or MCP configuration using this secret may fail until it is enabled again.'
}
