'use client'

import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Select,
  Skeleton,
  Space,
  Switch,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import { createStyles } from 'antd-style'
import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  listSecrets,
  listModelConfigs,
  testModelConfig,
  updateModelConfig,
} from '@/api/agentApiClient'
import type {
  ModelConfigId,
  ModelConfigResponse,
  SecretSummary,
  SecretType,
  TestModelConfigResponse,
  UpdateModelConfigInput,
  WorkspaceId,
} from '@/api/schemas/workspace'

const { Text } = Typography

const useStyles = createStyles(({ css, token }) => ({
  body: css`
    display: grid;
    gap: 16px;
    grid-template-columns: minmax(0, 1fr) minmax(280px, 360px);

    @media (max-width: 980px) {
      grid-template-columns: 1fr;
    }
  `,
  form: css`
    max-width: 720px;
  `,
  result: css`
    border: 1px solid ${token.colorBorderSecondary};
    border-radius: 8px;
    padding: 12px;
  `,
  toolbar: css`
    display: flex;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 12px;
  `,
}))

interface ModelConfigSettingsProps {
  workspaceId: WorkspaceId
}

const MODEL_SECRET_TYPES: Record<ModelConfigId, SecretType[]> = {
  compression: ['model_api_key'],
  embedding: ['embedding_api_key'],
  fallback: ['model_api_key'],
  graphrag_llm: ['model_api_key'],
  main_chat: ['model_api_key'],
  rerank: ['rerank_api_key'],
}

function statusColor(status: string) {
  if (status === 'configured') {
    return 'green'
  }
  if (status === 'disabled') {
    return 'default'
  }
  return 'gold'
}

function sourceColor(source: ModelConfigResponse['source']) {
  return source === 'stored' ? 'cyan' : 'blue'
}

function sourceLabel(source: ModelConfigResponse['source']) {
  return source === 'stored' ? 'saved' : 'default env'
}

function toFormValue(config: ModelConfigResponse): UpdateModelConfigInput {
  return {
    api_key_ref: config.api_key_ref || undefined,
    base_url: config.base_url || '',
    context_window_tokens: config.context_window_tokens,
    enabled: config.enabled,
    max_output_tokens: config.max_output_tokens,
    model: config.model,
    provider: config.provider,
    supports_tool_calling: config.supports_tool_calling,
    timeout_ms: config.timeout_ms,
  }
}

export function ModelConfigSettings({ workspaceId }: ModelConfigSettingsProps) {
  const { styles } = useStyles()
  const [form] = Form.useForm<UpdateModelConfigInput>()
  const [configs, setConfigs] = useState<ModelConfigResponse[]>([])
  const [activeConfigId, setActiveConfigId] = useState<ModelConfigId>('main_chat')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<TestModelConfigResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [secrets, setSecrets] = useState<SecretSummary[]>([])
  const [testing, setTesting] = useState(false)

  const activeConfig = useMemo(
    () => configs.find((config) => config.config_id === activeConfigId),
    [activeConfigId, configs],
  )

  const loadConfigs = useCallback(async () => {
    setLoading(true)
    setErrorMessage(null)
    try {
      const [response, secretsResponse] = await Promise.all([
        listModelConfigs(workspaceId),
        listSecrets(workspaceId),
      ])
      setConfigs(response.configs)
      setSecrets(secretsResponse.secrets)
      const nextActive = response.configs.some((config) => config.config_id === activeConfigId)
        ? activeConfigId
        : response.configs[0]?.config_id || 'main_chat'
      setActiveConfigId(nextActive)
      const nextConfig = response.configs.find((config) => config.config_id === nextActive)
      if (nextConfig) {
        form.setFieldsValue(toFormValue(nextConfig))
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load model configs.')
    } finally {
      setLoading(false)
    }
  }, [activeConfigId, form, workspaceId])

  useEffect(() => {
    void loadConfigs()
  }, [loadConfigs])

  useEffect(() => {
    if (activeConfig) {
      form.setFieldsValue(toFormValue(activeConfig))
      if (activeConfig.config_id === 'embedding') {
        form.setFieldValue('supports_tool_calling', false)
      }
      setSaveMessage(null)
      setTestResult(null)
    }
  }, [activeConfig, form])

  const updateActiveConfig = (nextConfig: ModelConfigResponse) => {
    setConfigs((current) =>
      current.map((config) =>
        config.config_id === nextConfig.config_id ? nextConfig : config,
      ),
    )
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    setSaving(true)
    setErrorMessage(null)
    setSaveMessage(null)
    try {
      const updated = await updateModelConfig(activeConfigId, values, workspaceId)
      updateActiveConfig(updated)
      setSaveMessage(`${updated.display_name} saved.`)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to save model config.')
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setErrorMessage(null)
    setTestResult(null)
    try {
      const values = await form.validateFields()
      const prompt =
        activeConfigId === 'embedding' ? 'embedding smoke test' : 'Reply with pong.'
      const result = await testModelConfig(
        activeConfigId,
        { config: values, max_output_tokens: 16, prompt },
        workspaceId,
      )
      setTestResult(result)
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Fix form validation errors before testing.',
      )
    } finally {
      setTesting(false)
    }
  }

  if (loading && configs.length === 0) {
    return <Skeleton active paragraph={{ rows: 8 }} />
  }

  return (
    <Card
      title="Model API configuration"
      extra={
        <Button loading={loading} onClick={() => void loadConfigs()}>
          Refresh
        </Button>
      }
    >
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        {errorMessage && <Alert message={errorMessage} showIcon type="error" />}
        {saveMessage && <Alert message={saveMessage} showIcon type="success" />}

        <Tabs
          activeKey={activeConfigId}
          items={configs.map((config) => ({
            key: config.config_id,
            label: (
              <Space size={6}>
                <span>{config.display_name}</span>
                <Tag color={statusColor(config.status)}>{config.status}</Tag>
                <Tag color={sourceColor(config.source)}>{sourceLabel(config.source)}</Tag>
              </Space>
            ),
          }))}
          onChange={(key) => setActiveConfigId(key as ModelConfigId)}
        />

        <div className={styles.body}>
          <Form
            className={styles.form}
            form={form}
            layout="vertical"
            requiredMark={false}
          >
            <Form.Item label="Provider" name="provider" rules={[{ required: true }]}>
              <Select
                options={providerOptionsForModelConfig(activeConfigId)}
              />
            </Form.Item>

            <Form.Item label="Model" name="model" rules={[{ required: true }]}>
              <Input placeholder="mimo-v2.5-pro" />
            </Form.Item>

            <Form.Item label="Base URL" name="base_url" rules={[{ required: true }]}>
              <Input placeholder="https://token-plan-cn.xiaomimimo.com/v1" />
            </Form.Item>

            <Form.Item label="API key secret" name="api_key_ref">
              <Select
                allowClear
                data-testid="model-api-key-secret-ref"
                optionFilterProp="label"
                options={secretOptionsForModelConfig(activeConfigId, secrets)}
                placeholder="Select stored secret"
                showSearch
              />
            </Form.Item>

            <Form.Item
              label="Context window"
              name="context_window_tokens"
              rules={[{ required: true }]}
            >
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>

            <Form.Item
              label="Max output"
              name="max_output_tokens"
              dependencies={['context_window_tokens']}
              rules={[
                { required: true },
                ({ getFieldValue }) => ({
                  validator(_, value: number | undefined) {
                    const contextWindow = Number(getFieldValue('context_window_tokens') || 0)
                    if (!value || !contextWindow || value <= contextWindow) {
                      return Promise.resolve()
                    }
                    return Promise.reject(
                      new Error('Max output must not exceed context window.'),
                    )
                  },
                }),
              ]}
            >
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>

            <Form.Item label="Timeout ms" name="timeout_ms" rules={[{ required: true }]}>
              <InputNumber min={1000} style={{ width: '100%' }} />
            </Form.Item>

            <Form.Item label="Tool calling" name="supports_tool_calling" valuePropName="checked">
              <Switch disabled={activeConfigId === 'embedding'} />
            </Form.Item>

            <Form.Item label="Enabled" name="enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Form>

          <Space direction="vertical" size={12}>
            <div className={styles.toolbar}>
              <Popconfirm
                cancelText="Cancel"
                description={modelSaveConfirmDescription(activeConfigId)}
                okText="Save model config"
                onConfirm={() => void handleSave()}
                title={`Save ${activeConfig?.display_name ?? activeConfigId} config?`}
              >
                <Button loading={saving} type="primary">
                  Save
                </Button>
              </Popconfirm>
              <Button loading={testing} onClick={() => void handleTest()}>
                Test
              </Button>
            </div>

            {activeConfig && (
              <div className={styles.result}>
                <Space direction="vertical" size={8}>
                  <Text strong>{activeConfig.display_name}</Text>
                  <Text type="secondary">Purpose: {activeConfig.purpose}</Text>
                  <Text type="secondary">Revision: {activeConfig.revision}</Text>
                  <Space size={6} wrap>
                    <Tag color={statusColor(activeConfig.status)}>{activeConfig.status}</Tag>
                    <Tag color={sourceColor(activeConfig.source)}>
                      {sourceLabel(activeConfig.source)}
                    </Tag>
                  </Space>
                </Space>
              </div>
            )}

            {testResult && (
              <Alert
                description={
                  <Space direction="vertical" size={4}>
                    <Text>Latency: {testResult.latency_ms} ms</Text>
                    {testResult.content_preview && (
                      <Text code>{testResult.content_preview}</Text>
                    )}
                    {testResult.error_type && <Text>Error: {testResult.error_type}</Text>}
                  </Space>
                }
                message={testResult.ok ? 'Model test passed' : 'Model test failed'}
                showIcon
                type={testResult.ok ? 'success' : 'warning'}
              />
            )}
          </Space>
        </div>
      </Space>
    </Card>
  )
}

function secretOptionsForModelConfig(configId: ModelConfigId, secrets: SecretSummary[]) {
  const types = MODEL_SECRET_TYPES[configId]
  return secrets
    .filter((secret) => secret.status === 'active' && types.includes(secret.type))
    .map((secret) => ({
      label: `${secret.display_name} (${secret.type}, ${secret.masked})`,
      value: secret.secret_ref,
    }))
}

function providerOptionsForModelConfig(configId: ModelConfigId) {
  if (configId === 'embedding' || configId === 'rerank') {
    return [{ label: 'OpenAI-compatible', value: 'openai_compatible' }]
  }
  return [
    { label: 'OpenAI-compatible', value: 'openai_compatible' },
    { label: 'Anthropic', value: 'anthropic' },
  ]
}

function modelSaveConfirmDescription(configId: ModelConfigId) {
  if (configId === 'embedding') {
    return 'Changing embedding config affects future query embeddings and new knowledge-base indexing jobs.'
  }
  if (configId === 'graphrag_llm') {
    return 'Changing GraphRAG LLM config affects graph extraction and GraphRAG answer generation.'
  }
  if (configId === 'compression') {
    return 'Changing compression config affects future context compaction behavior.'
  }
  return 'Changing this model config affects future runtime calls that use this slot.'
}
