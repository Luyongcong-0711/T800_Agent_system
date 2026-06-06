'use client'

import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  Popconfirm,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { createStyles } from 'antd-style'
import { useCallback, useEffect, useState } from 'react'

import {
  activateSkill,
  createSkillFromProposal,
  disableSkill,
  getSkill,
  listThreads,
  listSkills,
  proposeSkill,
  searchSkills,
  validateSkill,
} from '@/api/agentApiClient'
import type {
  RunStatus,
  SkillActivationResponse,
  SkillDetailResponse,
  SkillProposalInput,
  SkillProposalResponse,
  SkillSummary,
  ThreadSummary,
  WorkspaceId,
} from '@/api/schemas/workspace'

const { Text, Title } = Typography

const useStyles = createStyles(({ css, token }) => ({
  detailBox: css`
    background: ${token.colorFillQuaternary};
    border: 1px solid ${token.colorBorderSecondary};
    border-radius: 6px;
    max-height: 220px;
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

interface SkillsPanelProps {
  runtimeContext?: SkillRuntimeContext | null
  workspaceId: WorkspaceId
}

interface SkillRuntimeContext {
  run_id: string | null
  run_status: RunStatus | null
  thread_id: string | null
}

interface ProposalFormValues {
  database_read?: string
  description: string
  display_name: string
  entrypoint_name?: string
  entrypoint_type?: 'prompt_workflow' | 'script'
  file_read?: string
  knowledge_notes?: string
  sandbox_profile?: string
  script_content?: string
  when_to_use?: string
  workflow_steps: string
  write_mode?: 'none' | 'staged_patch'
}

interface MaterializeFormValues {
  approval_id: string
  proposal_id: string
  skill_id?: string
  version?: string
}

interface ActivationFormValues {
  reason: string
  run_id: string
  thread_id: string
}

export function SkillsPanel({ runtimeContext, workspaceId }: SkillsPanelProps) {
  const { styles } = useStyles()
  const [activationForm] = Form.useForm<ActivationFormValues>()
  const [materializeForm] = Form.useForm<MaterializeFormValues>()
  const [proposalForm] = Form.useForm<ProposalFormValues>()
  const [activation, setActivation] = useState<SkillActivationResponse | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [latestProposal, setLatestProposal] = useState<SkillProposalResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [autoRuntimeContext, setAutoRuntimeContext] =
    useState<SkillRuntimeContext | null>(null)
  const [query, setQuery] = useState('')
  const [selectedSkill, setSelectedSkill] = useState<SkillDetailResponse | null>(null)
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [submitting, setSubmitting] = useState(false)
  const runtimeRunAvailable = Boolean(
    runtimeContext?.run_status === 'running' &&
      runtimeContext.run_id &&
      runtimeContext.thread_id,
  )
  const activeRunId = runtimeRunAvailable
    ? runtimeContext?.run_id || null
    : autoRuntimeContext?.run_id || null
  const activeThreadId = activeRunId
    ? runtimeRunAvailable
      ? runtimeContext?.thread_id || null
      : autoRuntimeContext?.thread_id || null
    : null

  const loadSkills = useCallback(async () => {
    setLoading(true)
    setErrorMessage(null)
    try {
      const response = await listSkills(workspaceId, { limit: 100 })
      setSkills(response.skills)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load skills.')
    } finally {
      setLoading(false)
    }
  }, [workspaceId])

  useEffect(() => {
    void loadSkills()
  }, [loadSkills])

  useEffect(() => {
    if (runtimeRunAvailable) {
      return
    }
    let cancelled = false

    async function loadRunningThreadContext() {
      try {
        const response = await listThreads(workspaceId)
        const running = response.threads.find(isRunningThread)
        if (!cancelled) {
          setAutoRuntimeContext(
            running
              ? {
                  run_id: running.current_run_id || null,
                  run_status: running.current_run_status || null,
                  thread_id: running.thread_id,
                }
              : null,
          )
        }
      } catch {
        if (!cancelled) {
          setAutoRuntimeContext(null)
        }
      }
    }

    void loadRunningThreadContext()
    return () => {
      cancelled = true
    }
  }, [runtimeRunAvailable, workspaceId])

  useEffect(() => {
    if (activeRunId && activeThreadId) {
      activationForm.setFieldsValue({
        reason: activationForm.getFieldValue('reason') || 'Activate for current run.',
        run_id: activeRunId,
        thread_id: activeThreadId,
      })
      return
    }
  }, [activationForm, activeRunId, activeThreadId])

  const columns = [
    {
      key: 'skill',
      title: 'Skill',
      render: (_: unknown, record: SkillSummary) => (
        <Space direction="vertical" size={2}>
          <Button
            data-testid={`skill-open-${record.skill_id}`}
            onClick={() => void handleOpenSkill(record.skill_id)}
            type="link"
          >
            {record.display_name}
          </Button>
          <Text code>{record.skill_id}</Text>
          <Text type="secondary">{record.description}</Text>
        </Space>
      ),
    },
    {
      key: 'policy',
      title: 'Policy',
      render: (_: unknown, record: SkillSummary) => (
        <Space wrap>
          <Tag color={record.enabled ? 'green' : 'default'}>
            {record.enabled ? 'enabled' : 'disabled'}
          </Tag>
          <Tag color={riskColor(record.risk_level)}>{record.risk_level}</Tag>
          {record.requires_activation && <Tag>activation</Tag>}
          {record.requires_validation && <Tag color="orange">validation</Tag>}
        </Space>
      ),
    },
    {
      dataIndex: 'entrypoint_count',
      key: 'entrypoint_count',
      title: 'Entrypoints',
    },
    {
      key: 'actions',
      title: 'Actions',
      render: (_: unknown, record: SkillSummary) => (
        <Space wrap>
          <Button onClick={() => void handleOpenSkill(record.skill_id)}>View</Button>
          <Button
            data-testid={`skill-validate-${record.skill_id}`}
            disabled={!record.requires_validation || record.enabled}
            loading={submitting}
            onClick={() => void handleValidateSkill(record.skill_id)}
          >
            Validate
          </Button>
          <Popconfirm
            cancelText="Cancel"
            description="Disabling a Skill removes it from activation and model-visible use until it is re-enabled."
            disabled={!record.enabled}
            okText="Disable skill"
            onConfirm={() => void handleDisableSkill(record.skill_id)}
            title="Disable this Skill?"
          >
            <Button
              danger
              data-testid={`skill-disable-${record.skill_id}`}
              disabled={!record.enabled}
              loading={submitting}
            >
              Disable
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
            placeholder="Search skills"
            style={{ width: 260 }}
            value={query}
          />
          <Button onClick={() => void handleSearch()}>Search</Button>
        </Space>
        <Button loading={loading} onClick={() => void loadSkills()}>
          Refresh
        </Button>
      </div>

      {errorMessage && <Alert message={errorMessage} showIcon type="error" />}

      <Row gutter={[16, 16]}>
        <Col lg={15} xs={24}>
          <Card title="Skills">
            <Table<SkillSummary>
              className={styles.table}
              columns={columns}
              dataSource={skills}
              loading={loading}
              pagination={{ pageSize: 8 }}
              rowKey="skill_id"
            />
          </Card>
        </Col>

        <Col lg={9} xs={24}>
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Card title="Selected skill">
              {selectedSkill ? (
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <Space wrap>
                    <Tag>{selectedSkill.version}</Tag>
                    <Tag color={selectedSkill.enabled ? 'green' : 'default'}>
                      {selectedSkill.status}
                    </Tag>
                    <Tag color={riskColor(selectedSkill.risk_level)}>
                      {selectedSkill.risk_level}
                    </Tag>
                    {selectedSkill.requires_validation && <Tag color="orange">validation</Tag>}
                    <Tag>{selectedSkill.validation_status}</Tag>
                  </Space>
                  <Title level={5} style={{ margin: 0 }}>
                    {selectedSkill.display_name}
                  </Title>
                  <Text>{selectedSkill.summary}</Text>
                  <pre className={styles.detailBox}>
                    {JSON.stringify(
                      {
                        entrypoints: selectedSkill.entrypoints,
                        knowledge_sections: selectedSkill.knowledge_sections,
                        permissions: selectedSkill.permissions,
                        workflow_summary: selectedSkill.workflow_summary,
                      },
                      null,
                      2,
                    )}
                  </pre>
                </Space>
              ) : (
                <Text type="secondary">No skill selected.</Text>
              )}
            </Card>

            <Card title="Activate">
              <Form
                form={activationForm}
                initialValues={{
                  reason: 'Activate for current run.',
                  run_id: '',
                  thread_id: '',
                }}
                layout="vertical"
                onFinish={handleActivateSkill}
              >
                {!activeRunId && (
                  <Alert
                    message="No running chat run detected."
                    showIcon
                    style={{ marginBottom: 12 }}
                    type="info"
                  />
                )}
                <Form.Item label="Run" name="run_id" rules={[{ required: true }]}>
                  <Input placeholder="run_id" />
                </Form.Item>
                <Form.Item label="Thread" name="thread_id" rules={[{ required: true }]}>
                  <Input placeholder="thread_id" />
                </Form.Item>
                <Form.Item label="Reason" name="reason" rules={[{ required: true }]}>
                  <Input placeholder="activation reason" />
                </Form.Item>
                <Button
                  disabled={!selectedSkill || !selectedSkill.enabled}
                  htmlType="submit"
                  loading={submitting}
                >
                  Activate
                </Button>
              </Form>
              {activation && (
                <div className={styles.detailBox}>
                  <Text code>{activation.context_block_object_key}</Text>
                  <br />
                  <Text>Tools: {activation.activated_entrypoint_tools.join(', ')}</Text>
                </div>
              )}
            </Card>
          </Space>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col lg={14} xs={24}>
          <Card title="Propose skill">
            <Form
              form={proposalForm}
              initialValues={{
                database_read: 'minio',
                entrypoint_name: 'run',
                entrypoint_type: 'prompt_workflow',
                file_read: 'workspace',
                sandbox_profile: 'skill_script_readonly',
                write_mode: 'none',
              }}
              layout="vertical"
              onFinish={handleProposeSkill}
            >
              <Row gutter={12}>
                <Col md={12} xs={24}>
                  <Form.Item label="Display name" name="display_name" rules={[{ required: true }]}>
                    <Input placeholder="Contract cleanup workflow" />
                  </Form.Item>
                </Col>
                <Col md={12} xs={24}>
                  <Form.Item label="Entrypoint" name="entrypoint_name">
                    <Input placeholder="normalize_contract" />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item label="Description" name="description" rules={[{ required: true }]}>
                <Input placeholder="Normalize contract text and extract reusable metadata." />
              </Form.Item>
              <Form.Item label="Workflow steps" name="workflow_steps" rules={[{ required: true }]}>
                <Input.TextArea autoSize={{ maxRows: 6, minRows: 3 }} placeholder="One step per line" />
              </Form.Item>
              <Row gutter={12}>
                <Col md={12} xs={24}>
                  <Form.Item label="When to use" name="when_to_use">
                    <Input.TextArea autoSize={{ maxRows: 4, minRows: 2 }} placeholder="One item per line" />
                  </Form.Item>
                </Col>
                <Col md={12} xs={24}>
                  <Form.Item label="Knowledge notes" name="knowledge_notes">
                    <Input.TextArea autoSize={{ maxRows: 4, minRows: 2 }} placeholder="One note per line" />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={12}>
                <Col md={8} xs={24}>
                  <Form.Item label="Type" name="entrypoint_type">
                    <Select
                      options={[
                        { label: 'prompt_workflow', value: 'prompt_workflow' },
                        { label: 'script', value: 'script' },
                      ]}
                    />
                  </Form.Item>
                </Col>
                <Col md={8} xs={24}>
                  <Form.Item label="Sandbox" name="sandbox_profile">
                    <Input placeholder="skill_script_readonly" />
                  </Form.Item>
                </Col>
                <Col md={8} xs={24}>
                  <Form.Item label="Write mode" name="write_mode">
                    <Select
                      options={[
                        { label: 'none', value: 'none' },
                        { label: 'staged_patch', value: 'staged_patch' },
                      ]}
                    />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item label="Script content" name="script_content">
                <Input.TextArea autoSize={{ maxRows: 6, minRows: 3 }} placeholder="Optional script body" />
              </Form.Item>
              <Row gutter={12}>
                <Col md={12} xs={24}>
                  <Form.Item label="File read" name="file_read">
                    <Input placeholder="workspace" />
                  </Form.Item>
                </Col>
                <Col md={12} xs={24}>
                  <Form.Item label="Database read" name="database_read">
                    <Input placeholder="minio" />
                  </Form.Item>
                </Col>
              </Row>
              <Button htmlType="submit" loading={submitting}>
                Propose
              </Button>
            </Form>
          </Card>
        </Col>

        <Col lg={10} xs={24}>
          <Card title="Create approved skill">
            <Form form={materializeForm} layout="vertical" onFinish={handleMaterializeSkill}>
              <Form.Item label="Proposal" name="proposal_id" rules={[{ required: true }]}>
                <Input placeholder="proposal_id" />
              </Form.Item>
              <Form.Item label="Approval" name="approval_id" rules={[{ required: true }]}>
                <Input placeholder="approval_id" />
              </Form.Item>
              <Row gutter={12}>
                <Col md={12} xs={24}>
                  <Form.Item label="Skill ID" name="skill_id">
                    <Input placeholder="contract_cleaner" />
                  </Form.Item>
                </Col>
                <Col md={12} xs={24}>
                  <Form.Item label="Version" name="version">
                    <Input placeholder="0.1.0" />
                  </Form.Item>
                </Col>
              </Row>
              <Button htmlType="submit" loading={submitting}>
                Create skill
              </Button>
            </Form>

            {latestProposal && (
              <div className={styles.detailBox}>
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <Space wrap>
                    <Tag color={latestProposal.approval_required ? 'orange' : 'green'}>
                      {latestProposal.status}
                    </Tag>
                    <Tag color={riskColor(latestProposal.risk_level)}>
                      {latestProposal.risk_level}
                    </Tag>
                  </Space>
                  <Space direction="vertical" size={2}>
                    <Text strong>{latestProposal.display_name}</Text>
                    <Text type="secondary">{latestProposal.description}</Text>
                    <Text code>{latestProposal.proposal_id}</Text>
                    <Text code>{latestProposal.approval_id}</Text>
                  </Space>
                  <Space wrap>
                    <Button
                      data-testid="skill-use-latest-proposal"
                      onClick={handleUseLatestProposal}
                      size="small"
                    >
                      Use latest proposal
                    </Button>
                    <Button
                      data-testid="skill-create-latest-proposal"
                      loading={submitting}
                      onClick={() => void handleCreateLatestProposal()}
                      size="small"
                      type="primary"
                    >
                      Create latest skill
                    </Button>
                  </Space>
                </Space>
              </div>
            )}
          </Card>
        </Col>
      </Row>
    </Space>
  )

  async function handleSearch() {
    if (!query.trim()) {
      await loadSkills()
      return
    }
    setLoading(true)
    setErrorMessage(null)
    try {
      const response = await searchSkills(query.trim(), 10, workspaceId)
      setSkills(response.items)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to search skills.')
    } finally {
      setLoading(false)
    }
  }

  async function handleOpenSkill(skillId: string) {
    setLoading(true)
    setErrorMessage(null)
    try {
      const detail = await getSkill(skillId, undefined, workspaceId)
      setSelectedSkill(detail)
      setActivation(null)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load skill.')
    } finally {
      setLoading(false)
    }
  }

  async function handleProposeSkill(values: ProposalFormValues) {
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const input = buildProposalInput(values)
      const proposal = await proposeSkill(input, workspaceId)
      setLatestProposal(proposal)
      materializeForm.setFieldsValue({
        approval_id: proposal.approval_id,
        proposal_id: proposal.proposal_id,
        skill_id: slugify(proposal.display_name),
        version: '0.1.0',
      })
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to propose skill.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleMaterializeSkill(values: MaterializeFormValues) {
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const detail = await createSkillFromProposal(
        {
          approval_id: values.approval_id,
          proposal_id: values.proposal_id,
          skill_id: values.skill_id || null,
          version: values.version || '0.1.0',
        },
        workspaceId,
      )
      setSelectedSkill(detail)
      await loadSkills()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to create skill.')
    } finally {
      setSubmitting(false)
    }
  }

  function handleUseLatestProposal() {
    if (!latestProposal) {
      return
    }
    materializeForm.setFieldsValue({
      approval_id: latestProposal.approval_id,
      proposal_id: latestProposal.proposal_id,
      skill_id: slugify(latestProposal.display_name),
      version: materializeForm.getFieldValue('version') || '0.1.0',
    })
  }

  async function handleCreateLatestProposal() {
    if (!latestProposal) {
      return
    }
    const values = materializeForm.getFieldsValue()
    await handleMaterializeSkill({
      approval_id: values.approval_id || latestProposal.approval_id,
      proposal_id: values.proposal_id || latestProposal.proposal_id,
      skill_id: values.skill_id || slugify(latestProposal.display_name),
      version: values.version || '0.1.0',
    })
  }

  async function handleDisableSkill(skillId: string) {
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const detail = await disableSkill(skillId, { reason: 'Disabled from Skills page.' }, workspaceId)
      setSelectedSkill(detail)
      await loadSkills()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to disable skill.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleValidateSkill(skillId: string) {
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const detail = await validateSkill(
        skillId,
        { version: selectedSkill?.skill_id === skillId ? selectedSkill.version : null },
        workspaceId,
      )
      setSelectedSkill(detail)
      await loadSkills()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to validate skill.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleActivateSkill(values: ActivationFormValues) {
    if (!selectedSkill) {
      return
    }
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const response = await activateSkill(
        selectedSkill.skill_id,
        {
          reason: values.reason,
          run_id: values.run_id.trim(),
          thread_id: values.thread_id.trim(),
          version: selectedSkill.version,
        },
        workspaceId,
      )
      setActivation(response)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to activate skill.')
    } finally {
      setSubmitting(false)
    }
  }
}

function buildProposalInput(values: ProposalFormValues): SkillProposalInput {
  const entrypointType = values.entrypoint_type ?? 'prompt_workflow'
  const scriptRequired = entrypointType === 'script' || Boolean(values.script_content?.trim())
  return {
    description: values.description,
    display_name: values.display_name,
    entrypoints: [
      {
        args_schema: { properties: {}, type: 'object' },
        name: values.entrypoint_name || 'run',
        risk_level: scriptRequired ? 'high' : 'low',
        sandbox_profile: scriptRequired ? values.sandbox_profile || 'skill_script_readonly' : null,
        script_content: values.script_content?.trim() || null,
        script_required: scriptRequired,
        type: entrypointType,
        write_mode: values.write_mode ?? 'none',
      },
    ],
    knowledge_notes: parseLines(values.knowledge_notes),
    permissions: {
      database_read: parseCsv(values.database_read),
      database_write: [],
      file_read: parseCsv(values.file_read),
      file_write: [],
      network: false,
    },
    script_required: scriptRequired,
    when_to_use: parseLines(values.when_to_use),
    workflow_steps: parseLines(values.workflow_steps),
  }
}

function parseLines(value?: string) {
  return (value || '')
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function parseCsv(value?: string) {
  return (value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

function slugify(value: string) {
  const slug = value.toLowerCase().replace(/[^a-z0-9_.-]+/g, '_').replace(/^_+|_+$/g, '')
  return slug || 'skill_new'
}

function isRunningThread(thread: ThreadSummary) {
  return thread.current_run_status === 'running' && Boolean(thread.current_run_id)
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
