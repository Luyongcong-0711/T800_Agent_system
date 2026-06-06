'use client'

import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
} from 'antd'
import { createStyles } from 'antd-style'
import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  buildDocumentGraph,
  createEmbeddingReindexJob,
  createKnowledgeBase,
  getActiveEmbedding,
  listDocumentChunks,
  listDocuments,
  listKnowledgeBases,
  retryJob,
  uploadDocumentFileToKnowledgeBase,
  uploadDocumentToKnowledgeBase,
} from '@/api/agentApiClient'
import { newClientRequestId } from '@/api/clientRequestId'
import type {
  ActiveEmbeddingResponse,
  ChunkResponse,
  DocumentSummary,
  EmbeddingReindexInput,
  KnowledgeBaseResponse,
  UploadDocumentInput,
  WorkspaceId,
} from '@/api/schemas/workspace'
import { GraphRagPanel } from './GraphRagPanel'

const { Text } = Typography

const useStyles = createStyles(({ css, token }) => ({
  chunkText: css`
    background: ${token.colorFillQuaternary};
    border: 1px solid ${token.colorBorderSecondary};
    border-radius: 6px;
    max-height: 160px;
    overflow: auto;
    padding: 8px;
    white-space: pre-wrap;
  `,
  chunkItem: css`
    border-bottom: 1px solid ${token.colorBorderSecondary};
    padding: 10px 0;

    &:last-child {
      border-bottom: 0;
    }
  `,
  formGrid: css`
    display: grid;
    gap: 8px;
    grid-template-columns: repeat(2, minmax(0, 1fr));

    @media (max-width: 760px) {
      grid-template-columns: 1fr;
    }
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

interface KnowledgePanelProps {
  workspaceId: WorkspaceId
}

interface CreateKnowledgeBaseForm {
  knowledge_base_id: string
  name?: string
}

interface UploadFormValues {
  content?: string
  mime_type?: string
  source_file_name?: string
}

export function KnowledgePanel({ workspaceId }: KnowledgePanelProps) {
  const { styles } = useStyles()
  const [createForm] = Form.useForm<CreateKnowledgeBaseForm>()
  const [reindexForm] = Form.useForm<EmbeddingReindexInput>()
  const [uploadForm] = Form.useForm<UploadFormValues>()
  const [activeEmbedding, setActiveEmbedding] = useState<ActiveEmbeddingResponse | null>(null)
  const [activeKnowledgeBaseId, setActiveKnowledgeBaseId] = useState<string | null>(null)
  const [chunks, setChunks] = useState<ChunkResponse[]>([])
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseResponse[]>([])
  const [latestJobId, setLatestJobId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [selectedDocument, setSelectedDocument] = useState<DocumentSummary | null>(null)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const activeKnowledgeBase = useMemo(
    () => knowledgeBases.find((item) => item.knowledge_base_id === activeKnowledgeBaseId),
    [activeKnowledgeBaseId, knowledgeBases],
  )
  const activeEmbeddingIsFallback = isFallbackEmbedding(activeEmbedding)

  const loadKnowledgeBases = useCallback(async () => {
    setLoading(true)
    setErrorMessage(null)
    try {
      const response = await listKnowledgeBases(workspaceId)
      setKnowledgeBases(response.knowledge_bases)
      setActiveKnowledgeBaseId((current) => {
        if (current && response.knowledge_bases.some((item) => item.knowledge_base_id === current)) {
          return current
        }
        return response.knowledge_bases[0]?.knowledge_base_id ?? null
      })
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load knowledge bases.')
    } finally {
      setLoading(false)
    }
  }, [workspaceId])

  const loadKnowledgeDetails = useCallback(async () => {
    if (!activeKnowledgeBaseId) {
      setDocuments([])
      setActiveEmbedding(null)
      return
    }
    setLoading(true)
    setErrorMessage(null)
    try {
      const [documentsResponse, embeddingResponse] = await Promise.all([
        listDocuments(activeKnowledgeBaseId, { limit: 100 }, workspaceId),
        getActiveEmbedding(activeKnowledgeBaseId, workspaceId),
      ])
      setDocuments(documentsResponse.documents)
      setActiveEmbedding(embeddingResponse)
      reindexForm.setFieldsValue({
        collection: undefined,
        config_id: 'embedding',
        dimension:
          embeddingResponse.collection === 'object_store_lexical_fallback'
            ? undefined
            : embeddingResponse.dimension,
        model:
          embeddingResponse.model === 'object_store_lexical_fallback'
            ? ''
            : embeddingResponse.model,
        provider:
          embeddingResponse.provider === 'local_fallback'
            ? undefined
            : embeddingResponse.provider,
      })
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load knowledge data.')
    } finally {
      setLoading(false)
    }
  }, [activeKnowledgeBaseId, reindexForm, workspaceId])

  useEffect(() => {
    void loadKnowledgeBases()
  }, [loadKnowledgeBases])

  useEffect(() => {
    setChunks([])
    setSelectedDocument(null)
  }, [activeKnowledgeBaseId])

  useEffect(() => {
    void loadKnowledgeDetails()
  }, [loadKnowledgeDetails])

  const columns = [
    {
      dataIndex: 'source_file_name',
      key: 'source_file_name',
      title: 'Document',
      render: (_: unknown, record: DocumentSummary) => (
        <Space direction="vertical" size={2}>
          <Text>{record.source_file_name}</Text>
          <Text code>{record.doc_id}</Text>
        </Space>
      ),
    },
    {
      key: 'status',
      title: 'Status',
      render: (_: unknown, record: DocumentSummary) => (
        <Space wrap>
          <Tag color={statusColor(record.ingestion_status)}>{record.ingestion_status}</Tag>
          {record.embedding_status && (
            <Tag color={statusColor(record.embedding_status)}>{record.embedding_status}</Tag>
          )}
          {record.graph_status && <Tag color={statusColor(record.graph_status)}>{record.graph_status}</Tag>}
        </Space>
      ),
    },
    {
      key: 'chunks',
      title: 'Chunks',
      render: (_: unknown, record: DocumentSummary) => (
        <Space direction="vertical" size={2}>
          <Space wrap>
            <Text>{record.chunk_total}</Text>
            {record.chunk_failed > 0 && <Tag color="red">failed {record.chunk_failed}</Tag>}
            {record.chunk_embedded > 0 && <Tag color="green">embedded {record.chunk_embedded}</Tag>}
          </Space>
          <Space wrap>
            <Text type="secondary">
              {searchAvailabilityLabel(record, activeEmbeddingIsFallback)}
            </Text>
            {record.retryable && <Tag color="orange">retryable</Tag>}
          </Space>
          {record.failure_strategy && (
            <Text type="secondary">{failureStrategyLabel(record.failure_strategy)}</Text>
          )}
          {record.last_error && (
            <Text type="danger">{documentErrorSummary(record.last_error)}</Text>
          )}
        </Space>
      ),
    },
    {
      dataIndex: 'last_job_id',
      key: 'last_job_id',
      title: 'Last job',
      render: (jobId?: string | null) => (jobId ? <Text code>{jobId}</Text> : <Text type="secondary">none</Text>),
    },
    {
      key: 'actions',
      title: 'Actions',
      render: (_: unknown, record: DocumentSummary) => (
        <Space wrap>
          <Button onClick={() => void handleLoadChunks(record)}>Chunks</Button>
          {record.last_job_id && (
            <Button href={`/jobs?job_id=${encodeURIComponent(record.last_job_id)}`}>
              Open job
            </Button>
          )}
          {record.retryable && record.last_job_id && (
            <Button
              loading={submitting}
              onClick={() => void handleRetryDocumentJob(record)}
            >
              Retry failed chunks
            </Button>
          )}
          <Button
            data-testid={`build-graph-${record.doc_id}`}
            onClick={() => void handleBuildGraph(record)}
          >
            Build graph
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <div className={styles.toolbar}>
        <Space wrap>
          <Select
            loading={loading}
            onChange={setActiveKnowledgeBaseId}
            options={knowledgeBases.map((item) => ({
              label: item.name || item.knowledge_base_id,
              value: item.knowledge_base_id,
            }))}
            placeholder="Knowledge base"
            style={{ width: 240 }}
            value={activeKnowledgeBaseId ?? undefined}
          />
          <Button loading={loading} onClick={() => void loadKnowledgeBases()}>
            Refresh
          </Button>
        </Space>
        {latestJobId && (
          <Space>
            <Text>Queued job</Text>
            <Text code>{latestJobId}</Text>
            <Button href={`/jobs?job_id=${encodeURIComponent(latestJobId)}`}>
              Jobs
            </Button>
          </Space>
        )}
      </div>

      {errorMessage && <Alert message={errorMessage} showIcon type="error" />}

      <Row gutter={[16, 16]}>
        <Col lg={8} xs={24}>
          <Card title="Knowledge base">
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              {activeKnowledgeBase ? (
                <Descriptions
                  column={1}
                  items={[
                    { key: 'name', label: 'Name', children: activeKnowledgeBase.name },
                    {
                      key: 'id',
                      label: 'ID',
                      children: <Text code>{activeKnowledgeBase.knowledge_base_id}</Text>,
                    },
                    {
                      key: 'status',
                      label: 'Status',
                      children: <Tag color="green">{activeKnowledgeBase.status}</Tag>,
                    },
                  ]}
                  size="small"
                />
              ) : (
                <Text type="secondary">No knowledge base selected.</Text>
              )}
              <Form form={createForm} layout="vertical" onFinish={handleCreateKnowledgeBase}>
                <div className={styles.formGrid}>
                  <Form.Item
                    label="ID"
                    name="knowledge_base_id"
                    rules={[{ required: true }]}
                  >
                    <Input placeholder="kb_default" />
                  </Form.Item>
                  <Form.Item label="Name" name="name">
                    <Input placeholder="Default KB" />
                  </Form.Item>
                </div>
                <Button data-testid="knowledge-create" htmlType="submit" loading={submitting}>
                  Create
                </Button>
              </Form>
            </Space>
          </Card>
        </Col>

        <Col lg={8} xs={24}>
          <Card title="Active embedding">
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              {activeEmbedding ? (
                <>
                  {activeEmbeddingIsFallback && (
                    <Alert
                      message="Milvus vector search pending"
                      showIcon
                      type="warning"
                    />
                  )}
                  <Descriptions
                    column={1}
                    items={[
                      { key: 'provider', label: 'Provider', children: activeEmbedding.provider },
                      { key: 'model', label: 'Model', children: activeEmbedding.model },
                      { key: 'dimension', label: 'Dimension', children: activeEmbedding.dimension },
                      {
                        key: 'collection',
                        label: 'Collection',
                        children: <Text code>{activeEmbedding.collection}</Text>,
                      },
                    ]}
                    size="small"
                  />
                </>
              ) : (
                <Text type="secondary">No active embedding.</Text>
              )}
              <Form form={reindexForm} layout="vertical" onFinish={handleReindex}>
                <div className={styles.formGrid}>
                  <Form.Item label="Provider" name="provider">
                    <Input allowClear placeholder="Settings default" />
                  </Form.Item>
                  <Form.Item label="Model" name="model">
                    <Input allowClear placeholder="Settings embedding model" />
                  </Form.Item>
                  <Form.Item label="Dimension" name="dimension">
                    <InputNumber min={1} placeholder="Settings default" style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item label="Config" name="config_id">
                    <Input placeholder="embedding" />
                  </Form.Item>
                </div>
                <Form.Item label="Collection" name="collection">
                  <Input allowClear placeholder="optional_collection_name" />
                </Form.Item>
                <Button
                  data-testid="embedding-reindex"
                  disabled={!activeKnowledgeBaseId}
                  htmlType="submit"
                  loading={submitting}
                >
                  Reindex
                </Button>
              </Form>
            </Space>
          </Card>
        </Col>

        <Col lg={8} xs={24}>
          <Card title="Upload">
            <Form form={uploadForm} layout="vertical" onFinish={handleUploadDocument}>
              <Form.Item label="File">
                <Upload
                  beforeUpload={(file) => {
                    setSelectedFile(file)
                    uploadForm.setFieldValue('source_file_name', file.name)
                    return false
                  }}
                  fileList={
                    selectedFile
                      ? [{ name: selectedFile.name, uid: selectedFile.name }]
                      : []
                  }
                  maxCount={1}
                  onRemove={() => {
                    setSelectedFile(null)
                    return true
                  }}
                >
                  <Button>Select file</Button>
                </Upload>
              </Form.Item>
              <Form.Item label="File name" name="source_file_name">
                <Input placeholder="document.md" />
              </Form.Item>
              <Form.Item label="MIME type" name="mime_type">
                <Input placeholder="text/markdown" />
              </Form.Item>
              <Form.Item label="Content" name="content">
                <Input.TextArea autoSize={{ maxRows: 6, minRows: 3 }} />
              </Form.Item>
              <Button
                data-testid="knowledge-upload"
                disabled={!activeKnowledgeBaseId}
                htmlType="submit"
                loading={submitting}
              >
                Upload
              </Button>
            </Form>
          </Card>
        </Col>
      </Row>

      <Card title="Documents">
        <Table<DocumentSummary>
          className={styles.table}
          columns={columns}
          dataSource={documents}
          loading={loading}
          pagination={{ pageSize: 8 }}
          rowKey="doc_id"
        />
      </Card>

      <GraphRagPanel knowledgeBaseId={activeKnowledgeBaseId} workspaceId={workspaceId} />

      <Card title={selectedDocument ? `Chunks: ${selectedDocument.source_file_name}` : 'Chunks'}>
        {loading && chunks.length === 0 ? (
          <Text type="secondary">Loading chunks.</Text>
        ) : chunks.length === 0 ? (
          <Text type="secondary">No chunks selected.</Text>
        ) : (
          <Space direction="vertical" size={0} style={{ width: '100%' }}>
            {chunks.map((chunk) => (
              <div className={styles.chunkItem} key={chunk.chunk_id}>
                <Space direction="vertical" size={6} style={{ width: '100%' }}>
                  <Space wrap>
                    <Text code>{chunk.chunk_id}</Text>
                    {chunk.section_path.map((section) => (
                      <Tag key={section}>{section}</Tag>
                    ))}
                  </Space>
                  <div className={styles.chunkText}>{chunk.text}</div>
                  {chunk.object_key && <Text code>{chunk.object_key}</Text>}
                </Space>
              </div>
            ))}
          </Space>
        )}
      </Card>
    </Space>
  )

  async function handleCreateKnowledgeBase(values: CreateKnowledgeBaseForm) {
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const created = await createKnowledgeBase(values, workspaceId)
      setKnowledgeBases((current) => [
        created,
        ...current.filter((item) => item.knowledge_base_id !== created.knowledge_base_id),
      ])
      setActiveKnowledgeBaseId(created.knowledge_base_id)
      createForm.resetFields()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to create knowledge base.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleUploadDocument(values: UploadFormValues) {
    if (!activeKnowledgeBaseId) {
      return
    }
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const sourceFileName = values.source_file_name || selectedFile?.name || 'document.txt'
      let uploaded: DocumentSummary
      if (selectedFile) {
        uploaded = await uploadDocumentFileToKnowledgeBase(
          activeKnowledgeBaseId,
          selectedFile,
          workspaceId,
          {
            idempotency_key: newClientRequestId('upload'),
            source_file_name: sourceFileName,
          },
        )
      } else {
        const content = values.content?.trim()
        if (!content) {
          setErrorMessage('Document content is required when no file is selected.')
          return
        }
        const payload: UploadDocumentInput = {
          content,
          idempotency_key: newClientRequestId('upload'),
          mime_type: values.mime_type || 'text/plain',
          source_file_name: sourceFileName,
        }
        uploaded = await uploadDocumentToKnowledgeBase(activeKnowledgeBaseId, payload, workspaceId)
      }
      setLatestJobId(uploaded.job_id || uploaded.last_job_id || null)
      setSelectedFile(null)
      uploadForm.resetFields()
      await loadKnowledgeDetails()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to upload document.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleLoadChunks(document: DocumentSummary) {
    setLoading(true)
    setErrorMessage(null)
    try {
      const response = await listDocumentChunks(
        document.knowledge_base_id,
        document.doc_id,
        workspaceId,
      )
      setSelectedDocument(document)
      setChunks(response.chunks)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load chunks.')
    } finally {
      setLoading(false)
    }
  }

  async function handleReindex(values: EmbeddingReindexInput) {
    if (!activeKnowledgeBaseId) {
      return
    }
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const response = await createEmbeddingReindexJob(
        activeKnowledgeBaseId,
        {
          ...values,
          collection: values.collection || undefined,
          config_id: values.config_id || 'embedding',
          dimension: values.dimension || undefined,
          idempotency_key: newClientRequestId(`reindex-${activeKnowledgeBaseId}`),
          model: values.model || undefined,
          provider: values.provider || undefined,
        },
        workspaceId,
      )
      setLatestJobId(response.job_id)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to create reindex job.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleBuildGraph(document: DocumentSummary) {
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const response = await buildDocumentGraph(
        document.knowledge_base_id,
        document.doc_id,
        workspaceId,
      )
      setLatestJobId(response.job_id)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to build graph.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleRetryDocumentJob(document: DocumentSummary) {
    if (!document.last_job_id) {
      return
    }
    setSubmitting(true)
    setErrorMessage(null)
    try {
      const response = await retryJob(
        document.last_job_id,
        {
          idempotency_key: newClientRequestId(`retry-document-${document.doc_id}`),
        },
        workspaceId,
      )
      setLatestJobId(response.job_id)
      await loadKnowledgeDetails()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to retry document job.')
    } finally {
      setSubmitting(false)
    }
  }
}

function statusColor(status: string) {
  if (status === 'indexed' || status === 'succeeded' || status === 'active') {
    return 'green'
  }
  if (status === 'failed') {
    return 'red'
  }
  if (status === 'pending' || status === 'uploaded') {
    return 'gold'
  }
  return 'blue'
}

function documentErrorSummary(error: Record<string, unknown>) {
  const errorType = typeof error.error_type === 'string' ? error.error_type : undefined
  const message = typeof error.message === 'string' ? error.message : undefined
  return [errorType, message].filter(Boolean).join(': ') || 'chunk failure'
}

function failureStrategyLabel(strategy: string) {
  const labels: Record<string, string> = {
    inspect_failed_chunks: 'inspect failed chunks',
    replace_or_skip_document: 'replace or skip document',
    retry_document_ingestion: 'retry document ingestion',
    retry_failed_chunks: 'retry failed chunks',
  }
  return labels[strategy] || strategy
}

function isFallbackEmbedding(embedding: ActiveEmbeddingResponse | null) {
  if (!embedding) {
    return false
  }
  return (
    embedding.provider === 'local_fallback' ||
    embedding.model === 'object_store_lexical_fallback' ||
    embedding.collection === 'object_store_lexical_fallback'
  )
}

function searchAvailabilityLabel(document: DocumentSummary, activeEmbeddingIsFallback: boolean) {
  if (activeEmbeddingIsFallback) {
    return 'Milvus vector search pending'
  }
  return document.search_available ? 'searchable' : 'pending'
}
