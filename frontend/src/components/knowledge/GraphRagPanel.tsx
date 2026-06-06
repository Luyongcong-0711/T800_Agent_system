'use client'

import {
  Alert,
  Button,
  Card,
  Descriptions,
  Input,
  InputNumber,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { createStyles } from 'antd-style'
import { useCallback, useEffect, useState } from 'react'

import {
  expandGraphEntity,
  findGraphRelationship,
  findGraphPaths,
  getChunk,
  getGraphEvidence,
  getGraphSchema,
  searchGraphEntities,
  searchGraphRag,
} from '@/api/agentApiClient'
import type {
  ChunkResponse,
  GraphEntity,
  GraphEvidence,
  GraphPath,
  GraphRagTextEvidence,
  GraphRelationship,
  GraphSchemaResponse,
  WorkspaceId,
} from '@/api/schemas/workspace'

const { Text } = Typography

const useStyles = createStyles(({ css, token }) => ({
  evidenceBox: css`
    background: ${token.colorFillQuaternary};
    border: 1px solid ${token.colorBorderSecondary};
    border-radius: 6px;
    padding: 10px;
  `,
  resultGrid: css`
    display: grid;
    gap: 12px;
    grid-template-columns: repeat(2, minmax(0, 1fr));

    @media (max-width: 960px) {
      grid-template-columns: 1fr;
    }
  `,
  pathBox: css`
    border: 1px solid ${token.colorBorderSecondary};
    border-radius: 6px;
    padding: 10px;
  `,
  pathNodes: css`
    color: ${token.colorTextSecondary};
    margin-top: 6px;
    word-break: break-word;
  `,
  sourceChunkText: css`
    background: ${token.colorFillQuaternary};
    border: 1px solid ${token.colorBorderSecondary};
    border-radius: 6px;
    max-height: 420px;
    overflow: auto;
    padding: 10px;
    white-space: pre-wrap;
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

interface GraphRagPanelProps {
  knowledgeBaseId?: string | null
  workspaceId: WorkspaceId
}

export function GraphRagPanel({ knowledgeBaseId, workspaceId }: GraphRagPanelProps) {
  const { styles } = useStyles()
  const [entityQuery, setEntityQuery] = useState('')
  const [entities, setEntities] = useState<GraphEntity[]>([])
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [evidence, setEvidence] = useState<GraphEvidence[]>([])
  const [graphQuery, setGraphQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [maxDepth, setMaxDepth] = useState(2)
  const [paths, setPaths] = useState<GraphPath[]>([])
  const [schema, setSchema] = useState<GraphSchemaResponse | null>(null)
  const [sourceEntity, setSourceEntity] = useState('')
  const [sourceChunk, setSourceChunk] = useState<ChunkResponse | null>(null)
  const [sourceChunkLoading, setSourceChunkLoading] = useState(false)
  const [sourceChunkOpen, setSourceChunkOpen] = useState(false)
  const [directRelationships, setDirectRelationships] = useState<GraphRelationship[]>([])
  const [targetEntity, setTargetEntity] = useState('')
  const [textEvidence, setTextEvidence] = useState<GraphRagTextEvidence[]>([])
  const [warnings, setWarnings] = useState<string[]>([])

  useEffect(() => {
    setDirectRelationships([])
    setEntities([])
    setErrorMessage(null)
    setEvidence([])
    setPaths([])
    setSourceChunk(null)
    setSourceChunkOpen(false)
    setTextEvidence([])
    setWarnings([])
  }, [knowledgeBaseId])

  const loadSchema = useCallback(async () => {
    if (!knowledgeBaseId) {
      setSchema(null)
      return
    }
    try {
      const response = await getGraphSchema(knowledgeBaseId, workspaceId)
      setSchema(response)
      if (response.warnings?.length) {
        setWarnings((current) => mergeWarnings(current, response.warnings ?? []))
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load graph schema.')
    }
  }, [knowledgeBaseId, workspaceId])

  useEffect(() => {
    void loadSchema()
  }, [loadSchema])

  const entityColumns = [
    {
      dataIndex: 'name',
      key: 'name',
      title: 'Entity',
      render: (_: unknown, record: GraphEntity) => (
        <Space direction="vertical" size={2}>
          <Text>{record.name || record.entity_id}</Text>
          <Text code>{record.entity_id}</Text>
        </Space>
      ),
    },
    {
      dataIndex: 'entity_type',
      key: 'entity_type',
      title: 'Type',
      render: (type?: string | null) => (type ? <Tag>{type}</Tag> : <Text type="secondary">-</Text>),
    },
    {
      dataIndex: 'score',
      key: 'score',
      title: 'Score',
      render: (score?: number) => (score == null ? '-' : score.toFixed(2)),
    },
    {
      key: 'actions',
      title: 'Actions',
      render: (_: unknown, record: GraphEntity) => (
        <Button
          data-testid={`graph-expand-${safeTestId(record.entity_id)}`}
          onClick={() => void handleExpand(record)}
        >
          Expand
        </Button>
      ),
    },
  ]

  return (
    <Card title="GraphRAG evidence">
      <div className={styles.toolbar}>
        <Space wrap>
          <Input
            data-testid="graph-entity-query"
            onChange={(event) => setEntityQuery(event.target.value)}
            onPressEnter={() => void handleSearchEntities()}
            placeholder="Entity query"
            style={{ width: 260 }}
            value={entityQuery}
          />
          <Button
            data-testid="graph-search-entities"
            disabled={!knowledgeBaseId || !entityQuery.trim()}
            loading={loading}
            onClick={() => void handleSearchEntities()}
          >
            Search
          </Button>
        </Space>
        <Space wrap>
          {schema?.readonly && <Tag color="green">readonly</Tag>}
          {schema?.allowed_depth && <Tag>depth {schema.allowed_depth}</Tag>}
          {(schema?.labels ?? []).slice(0, 4).map((label) => (
            <Tag key={label}>{label}</Tag>
          ))}
        </Space>
      </div>

      {errorMessage && <Alert message={errorMessage} showIcon type="error" />}
      {warnings.length > 0 && (
        <Alert
          message={warnings.join(' | ')}
          showIcon
          style={{ marginBottom: 12 }}
          type="warning"
        />
      )}

      <Space wrap style={{ marginBottom: 12 }}>
        <Input
          data-testid="graphrag-query"
          onChange={(event) => setGraphQuery(event.target.value)}
          onPressEnter={() => void handleGraphRagSearch()}
          placeholder="Ask the knowledge graph"
          style={{ width: 360 }}
          value={graphQuery}
        />
        <Button
          data-testid="graphrag-search"
          disabled={!knowledgeBaseId || !graphQuery.trim()}
          loading={loading}
          onClick={() => void handleGraphRagSearch()}
        >
          GraphRAG search
        </Button>
      </Space>

      <Table<GraphEntity>
        columns={entityColumns}
        dataSource={entities}
        loading={loading}
        pagination={{ pageSize: 5 }}
        rowKey="entity_id"
        size="small"
      />

      <Space direction="vertical" size={12} style={{ marginTop: 12, width: '100%' }}>
        <Space wrap>
          <Input
            data-testid="graph-source-entity"
            onChange={(event) => setSourceEntity(event.target.value)}
            placeholder="Source entity"
            style={{ width: 220 }}
            value={sourceEntity}
          />
          <Input
            data-testid="graph-target-entity"
            onChange={(event) => setTargetEntity(event.target.value)}
            placeholder="Target entity"
            style={{ width: 220 }}
            value={targetEntity}
          />
          <InputNumber
            data-testid="graph-max-depth"
            max={2}
            min={1}
            onChange={(value) => setMaxDepth(Number(value || 1))}
            value={maxDepth}
          />
          <Button
            data-testid="graph-find-paths"
            disabled={!knowledgeBaseId || !sourceEntity.trim() || !targetEntity.trim()}
            loading={loading}
            onClick={() => void handleFindPaths()}
          >
            Find paths
          </Button>
          <Button
            data-testid="graph-find-relationships"
            disabled={!knowledgeBaseId || !sourceEntity.trim() || !targetEntity.trim()}
            loading={loading}
            onClick={() => void handleFindRelationships()}
          >
            Find direct relations
          </Button>
        </Space>

        <div className={styles.resultGrid}>
          <div>
            <Text strong>Text evidence</Text>
            <Space direction="vertical" size={8} style={{ marginTop: 8, width: '100%' }}>
              {textEvidence.length === 0 && <Text type="secondary">No text evidence.</Text>}
              {textEvidence.map((item, index) => (
                <div className={styles.evidenceBox} key={item.chunk_id || index}>
                  <Space wrap>
                    {item.chunk_id && <Text code>{item.chunk_id}</Text>}
                    {item.doc_id && <Tag>{item.doc_id}</Tag>}
                    {typeof item.score === 'number' && <Tag>{item.score.toFixed(2)}</Tag>}
                    {typeof item.source?.source_file_name === 'string' && (
                      <Tag>{item.source.source_file_name}</Tag>
                    )}
                    {item.chunk_id && (
                      <Button
                        loading={sourceChunkLoading}
                        onClick={() =>
                          void handleOpenSourceChunk(String(item.chunk_id), item.doc_id)
                        }
                        size="small"
                      >
                        Open source chunk
                      </Button>
                    )}
                  </Space>
                  <div className={styles.pathNodes}>{item.text || 'No text.'}</div>
                </div>
              ))}
            </Space>
          </div>

          <div>
            <Text strong>Paths</Text>
            <Space direction="vertical" size={8} style={{ marginTop: 8, width: '100%' }}>
              {paths.length === 0 && <Text type="secondary">No paths.</Text>}
              {paths.map((path) => (
                <div className={styles.pathBox} key={path.path_id}>
                  <Space wrap>
                    <Text code>{path.path_id}</Text>
                    <Tag>depth {path.depth}</Tag>
                    {path.relationships.map((relationship) => (
                      <Tag key={relationship.fact_id || relationship.type || path.path_id}>
                        {relationship.type || 'relationship'}
                      </Tag>
                    ))}
                  </Space>
                  <div className={styles.pathNodes}>{nodeLine(path)}</div>
                </div>
              ))}
            </Space>
          </div>

          <div>
            <Text strong>Direct relationships</Text>
            <Space direction="vertical" size={8} style={{ marginTop: 8, width: '100%' }}>
              {directRelationships.length === 0 && (
                <Text type="secondary">No direct relationships.</Text>
              )}
              {directRelationships.map((relationship, index) => (
                <div
                  className={styles.pathBox}
                  key={relationship.fact_id || `${relationship.type || 'relationship'}-${index}`}
                >
                  <Space wrap>
                    {relationship.fact_id && <Text code>{relationship.fact_id}</Text>}
                    <Tag>{relationship.type || 'relationship'}</Tag>
                    {relationship.confidence != null && (
                      <Tag>{Number(relationship.confidence).toFixed(2)}</Tag>
                    )}
                  </Space>
                  <div className={styles.pathNodes}>
                    {relationship.source_entity_id || sourceEntity}
                    {' -> '}
                    {relationship.target_entity_id || targetEntity}
                  </div>
                </div>
              ))}
            </Space>
          </div>

          <div>
            <Text strong>Graph evidence</Text>
            <Space direction="vertical" size={8} style={{ marginTop: 8, width: '100%' }}>
              {evidence.length === 0 && <Text type="secondary">No evidence.</Text>}
              {evidence.map((item, index) => (
                <div className={styles.evidenceBox} key={item.evidence_id || item.fact_id || index}>
                  <Space wrap>
                    {item.fact_id && <Tag>{item.fact_id}</Tag>}
                    {item.evidence_id && <Tag>{item.evidence_id}</Tag>}
                    {(item.source_chunk_id || item.chunk_id) && (
                      <Text code>{item.source_chunk_id || item.chunk_id}</Text>
                    )}
                    {(item.source_chunk_id || item.chunk_id) && (
                      <Button
                        loading={sourceChunkLoading}
                        onClick={() =>
                          void handleOpenSourceChunk(
                            item.source_chunk_id || item.chunk_id || '',
                            evidenceDocId(item),
                          )
                        }
                        size="small"
                      >
                        Open source chunk
                      </Button>
                    )}
                  </Space>
                  <div className={styles.pathNodes}>
                    {item.evidence_text || item.chunk_text || 'No evidence text.'}
                  </div>
                </div>
              ))}
            </Space>
          </div>
        </div>
      </Space>

      <Modal
        footer={<Button onClick={() => setSourceChunkOpen(false)}>Close</Button>}
        onCancel={() => setSourceChunkOpen(false)}
        open={sourceChunkOpen}
        title="Source chunk"
        width={820}
      >
        {sourceChunk ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions
              column={1}
              items={[
                { key: 'chunk_id', label: 'Chunk', children: <Text code>{sourceChunk.chunk_id}</Text> },
                { key: 'doc_id', label: 'Document', children: <Text code>{sourceChunk.doc_id}</Text> },
                {
                  key: 'object_key',
                  label: 'Object key',
                  children: sourceChunk.object_key ? (
                    <Text code style={{ wordBreak: 'break-all' }}>
                      {sourceChunk.object_key}
                    </Text>
                  ) : (
                    <Text type="secondary">none</Text>
                  ),
                },
              ]}
              size="small"
            />
            <div className={styles.sourceChunkText}>{sourceChunk.text}</div>
          </Space>
        ) : (
          <Text type="secondary">No source chunk loaded.</Text>
        )}
      </Modal>
    </Card>
  )

  async function handleSearchEntities() {
    if (!knowledgeBaseId || !entityQuery.trim()) {
      return
    }
    setLoading(true)
    setErrorMessage(null)
    try {
      const response = await searchGraphEntities(
        {
          include_aliases: true,
          knowledge_base_id: knowledgeBaseId,
          limit: 10,
          query: entityQuery.trim(),
        },
        workspaceId,
      )
      setEntities(response.entities)
      setWarnings(response.warnings ?? [])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to search graph entities.')
    } finally {
      setLoading(false)
    }
  }

  async function handleGraphRagSearch() {
    if (!knowledgeBaseId || !graphQuery.trim()) {
      return
    }
    setLoading(true)
    setErrorMessage(null)
    try {
      const response = await searchGraphRag(
        {
          final_top_k: 5,
          graph_depth: 2,
          include_sources: true,
          knowledge_base_id: knowledgeBaseId,
          query: graphQuery.trim(),
          top_k: 20,
        },
        workspaceId,
      )
      setTextEvidence(response.text_evidence)
      setEvidence(response.graph_evidence.filter(isGraphEvidence))
      setPaths(response.graph_evidence.filter(isGraphPath))
      setDirectRelationships([])
      setWarnings(response.warnings ?? [])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to search GraphRAG.')
    } finally {
      setLoading(false)
    }
  }

  async function handleExpand(entity: GraphEntity) {
    if (!knowledgeBaseId) {
      return
    }
    setLoading(true)
    setErrorMessage(null)
    try {
      const response = await expandGraphEntity(
        entity.entity_id,
        {
          depth: 2,
          include_evidence: true,
          knowledge_base_id: knowledgeBaseId,
          limit: 20,
        },
        workspaceId,
      )
      setPaths(response.paths)
      setDirectRelationships([])
      await loadEvidenceForPaths(response.paths, response.warnings ?? [])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to expand graph entity.')
    } finally {
      setLoading(false)
    }
  }

  async function handleFindPaths() {
    if (!knowledgeBaseId || !sourceEntity.trim() || !targetEntity.trim()) {
      return
    }
    setLoading(true)
    setErrorMessage(null)
    try {
      const response = await findGraphPaths(
        {
          knowledge_base_id: knowledgeBaseId,
          limit: 10,
          max_depth: Math.min(maxDepth, 2),
          source_entity: sourceEntity.trim(),
          target_entity: targetEntity.trim(),
        },
        workspaceId,
      )
      setPaths(response.paths)
      setDirectRelationships([])
      await loadEvidenceForPaths(response.paths, response.warnings ?? [])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to find graph paths.')
    } finally {
      setLoading(false)
    }
  }

  async function handleFindRelationships() {
    if (!knowledgeBaseId || !sourceEntity.trim() || !targetEntity.trim()) {
      return
    }
    setLoading(true)
    setErrorMessage(null)
    try {
      const response = await findGraphRelationship(
        {
          include_evidence: true,
          knowledge_base_id: knowledgeBaseId,
          source_entity: sourceEntity.trim(),
          target_entity: targetEntity.trim(),
        },
        workspaceId,
      )
      setPaths([])
      setDirectRelationships(response.relationships)
      await loadEvidenceForRelationships(response.relationships, response.warnings ?? [])
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Failed to find graph relationships.',
      )
    } finally {
      setLoading(false)
    }
  }

  async function loadEvidenceForPaths(nextPaths: GraphPath[], baseWarnings: string[] = []) {
    if (!knowledgeBaseId) {
      return
    }
    const { evidenceIds, factIds } = collectEvidenceRefs(nextPaths)
    if (factIds.length === 0 && evidenceIds.length === 0) {
      setEvidence([])
      setWarnings(baseWarnings)
      return
    }
    const response = await getGraphEvidence(
      {
        evidence_ids: evidenceIds,
        fact_ids: factIds,
        include_chunk_text: true,
        knowledge_base_id: knowledgeBaseId,
        max_chars_per_chunk: 1200,
      },
      workspaceId,
    )
    setEvidence(response.evidence)
    setWarnings(mergeWarnings(baseWarnings, response.warnings ?? []))
  }

  async function loadEvidenceForRelationships(
    relationships: GraphRelationship[],
    baseWarnings: string[] = [],
  ) {
    if (!knowledgeBaseId) {
      return
    }
    const { evidenceIds, factIds } = collectRelationshipEvidenceRefs(relationships)
    if (factIds.length === 0 && evidenceIds.length === 0) {
      setEvidence([])
      setWarnings(baseWarnings)
      return
    }
    const response = await getGraphEvidence(
      {
        evidence_ids: evidenceIds,
        fact_ids: factIds,
        include_chunk_text: true,
        knowledge_base_id: knowledgeBaseId,
        max_chars_per_chunk: 1200,
      },
      workspaceId,
    )
    setEvidence(response.evidence)
    setWarnings(mergeWarnings(baseWarnings, response.warnings ?? []))
  }

  async function handleOpenSourceChunk(chunkId: string, docId?: string | null) {
    if (!knowledgeBaseId || !chunkId) {
      return
    }
    setSourceChunkLoading(true)
    setErrorMessage(null)
    try {
      const chunk = await getChunk(
        chunkId,
        {
          doc_id: docId || undefined,
          knowledge_base_id: knowledgeBaseId,
          max_chars: 4000,
        },
        workspaceId,
      )
      setSourceChunk(chunk)
      setSourceChunkOpen(true)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Failed to load source chunk.')
    } finally {
      setSourceChunkLoading(false)
    }
  }
}

function collectEvidenceRefs(paths: GraphPath[]) {
  const factIds = new Set<string>()
  const evidenceIds = new Set<string>()
  paths.forEach((path) => {
    path.relationships.forEach((relationship) => {
      if (relationship.fact_id) {
        factIds.add(relationship.fact_id)
      }
      for (const evidenceId of relationship.evidence_ids ?? []) {
        evidenceIds.add(evidenceId)
      }
    })
  })
  return { evidenceIds: [...evidenceIds], factIds: [...factIds] }
}

function collectRelationshipEvidenceRefs(relationships: GraphRelationship[]) {
  const factIds = new Set<string>()
  const evidenceIds = new Set<string>()
  relationships.forEach((relationship) => {
    if (relationship.fact_id) {
      factIds.add(relationship.fact_id)
    }
    for (const evidenceId of relationship.evidence_ids ?? []) {
      evidenceIds.add(evidenceId)
    }
  })
  return { evidenceIds: [...evidenceIds], factIds: [...factIds] }
}

function nodeLine(path: GraphPath) {
  return path.nodes
    .map((node) => String(node.name || node.entity_id || 'unknown'))
    .join(' -> ')
}

function safeTestId(value: string) {
  return value.replace(/[^a-zA-Z0-9_-]/g, '-')
}

function isGraphEvidence(value: GraphEvidence | GraphPath): value is GraphEvidence {
  return 'evidence_id' in value || 'fact_id' in value || 'chunk_text' in value
}

function isGraphPath(value: GraphEvidence | GraphPath): value is GraphPath {
  return 'path_id' in value && 'relationships' in value
}

function mergeWarnings(...groups: string[][]) {
  return [...new Set(groups.flat().filter(Boolean))]
}

function evidenceDocId(item: GraphEvidence) {
  if (typeof item.doc_id === 'string') {
    return item.doc_id
  }
  if (typeof item.source?.doc_id === 'string') {
    return item.source.doc_id
  }
  return undefined
}
