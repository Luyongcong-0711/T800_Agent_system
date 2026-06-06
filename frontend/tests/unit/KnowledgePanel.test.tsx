import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ActiveEmbeddingResponse,
  DocumentSummary,
  EmbeddingReindexResponse,
  GraphBuildResponse,
  GraphEntitySearchResponse,
  GraphEvidenceResponse,
  GraphExpandEntityResponse,
  GraphFindRelationshipResponse,
  GraphRagSearchResponse,
  GraphSchemaResponse,
  KnowledgeBaseResponse,
  ListChunksResponse,
  ListDocumentsResponse,
  ListKnowledgeBasesResponse,
} from '@/api/schemas/workspace'
import { KnowledgePanel } from '@/components/knowledge/KnowledgePanel'

vi.mock('@/api/agentApiClient', () => ({
  buildDocumentGraph: vi.fn(),
  createEmbeddingReindexJob: vi.fn(),
  createKnowledgeBase: vi.fn(),
  expandGraphEntity: vi.fn(),
  findGraphRelationship: vi.fn(),
  findGraphPaths: vi.fn(),
  getChunk: vi.fn(),
  getActiveEmbedding: vi.fn(),
  getGraphEvidence: vi.fn(),
  getGraphSchema: vi.fn(),
  getKnowledgeBase: vi.fn(),
  listDocumentChunks: vi.fn(),
  listDocuments: vi.fn(),
  listKnowledgeBases: vi.fn(),
  searchGraphEntities: vi.fn(),
  searchGraphRag: vi.fn(),
  uploadDocumentFileToKnowledgeBase: vi.fn(),
  uploadDocumentToKnowledgeBase: vi.fn(),
}))

const api = await import('@/api/agentApiClient')

const now = '2026-05-31T00:00:00.000Z'

function kb(overrides: Partial<KnowledgeBaseResponse> = {}): KnowledgeBaseResponse {
  return {
    knowledge_base_id: 'kb_default',
    name: 'Default KB',
    status: 'active',
    updated_at: now,
    workspace_id: 'default',
    ...overrides,
  }
}

function document(overrides: Partial<DocumentSummary> = {}): DocumentSummary {
  return {
    chunk_embedded: 2,
    chunk_failed: 0,
    chunk_total: 2,
    doc_id: 'doc_001',
    doc_version_id: 'docv_001',
    embedding_status: 'indexed',
    graph_status: 'pending',
    graphrag_available: false,
    ingestion_status: 'indexed',
    knowledge_base_id: 'kb_default',
    last_job_id: 'job_ingest_001',
    mime_type: 'text/markdown',
    parse_status: 'parsed',
    search_available: true,
    source_file_name: 'seed.md',
    title: 'Seed',
    updated_at: now,
    workspace_id: 'default',
    ...overrides,
  }
}

describe('KnowledgePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        addEventListener: vi.fn(),
        addListener: vi.fn(),
        dispatchEvent: vi.fn(),
        matches: false,
        media: query,
        onchange: null,
        removeEventListener: vi.fn(),
        removeListener: vi.fn(),
      })),
    })
    vi.stubGlobal(
      'ResizeObserver',
      class {
        disconnect() {}
        observe() {}
        unobserve() {}
      },
    )
    vi.stubGlobal('crypto', { randomUUID: () => 'request-001' })
    vi.mocked(api.listKnowledgeBases).mockResolvedValue({
      knowledge_bases: [kb()],
      workspace_id: 'default',
    } satisfies ListKnowledgeBasesResponse)
    vi.mocked(api.getKnowledgeBase).mockResolvedValue(kb())
    vi.mocked(api.listDocuments).mockResolvedValue({
      documents: [document()],
      knowledge_base_id: 'kb_default',
      workspace_id: 'default',
    } satisfies ListDocumentsResponse)
    vi.mocked(api.getActiveEmbedding).mockResolvedValue({
      collection: 'kb_default_mimo_v1',
      dimension: 3,
      model: 'text-embedding-test',
      provider: 'openai_compatible',
      status: 'active',
      version_id: 'embv_001',
      workspace_id: 'default',
    } satisfies ActiveEmbeddingResponse)
    vi.mocked(api.listDocumentChunks).mockResolvedValue({
      chunks: [
        {
          chunk_id: 'chk_001',
          doc_id: 'doc_001',
          doc_version_id: 'docv_001',
          knowledge_base_id: 'kb_default',
          section_path: ['Seed'],
          text: 'Party B must deliver equipment.',
          workspace_id: 'default',
        },
      ],
      doc_id: 'doc_001',
      knowledge_base_id: 'kb_default',
      workspace_id: 'default',
    } satisfies ListChunksResponse)
    vi.mocked(api.createEmbeddingReindexJob).mockResolvedValue({
      active_embedding: {},
      job_id: 'job_reindex_001',
      job_status: 'queued',
      job_type: 'embedding_reindex_job',
      knowledge_base_id: 'kb_default',
      workspace_id: 'default',
    } satisfies EmbeddingReindexResponse)
    vi.mocked(api.uploadDocumentToKnowledgeBase).mockResolvedValue(
      document({ doc_id: 'doc_uploaded', job_id: 'job_ingest_002' }),
    )
    vi.mocked(api.getGraphSchema).mockResolvedValue({
      allowed_depth: 2,
      labels: ['Entity', 'RelationFact', 'Evidence'],
      readonly: true,
      relationships: ['RELATION_SUPPORTED_BY'],
    } satisfies GraphSchemaResponse)
    vi.mocked(api.searchGraphEntities).mockResolvedValue({
      entities: [
        {
          entity_id: 'ent_party_b',
          entity_type: 'Organization',
          evidence_count: 1,
          name: 'Party B',
          score: 1,
        },
      ],
      warnings: [],
    } satisfies GraphEntitySearchResponse)
    vi.mocked(api.expandGraphEntity).mockResolvedValue({
      entity_id: 'ent_party_b',
      paths: [
        {
          depth: 1,
          direction_preserved: true,
          nodes: [
            { entity_id: 'ent_party_b', name: 'Party B' },
            { entity_id: 'ent_equipment', name: 'Equipment' },
          ],
          path_id: 'path_fact_001',
          relationships: [
            {
              evidence_ids: ['ev_001'],
              fact_id: 'fact_001',
              source_entity_id: 'ent_party_b',
              target_entity_id: 'ent_equipment',
              type: 'DELIVERS',
            },
          ],
        },
      ],
      warnings: [],
    } satisfies GraphExpandEntityResponse)
    vi.mocked(api.findGraphRelationship).mockResolvedValue({
      empty: false,
      relationships: [
        {
          evidence_ids: ['ev_001'],
          fact_id: 'fact_001',
          source_entity_id: 'ent_party_b',
          target_entity_id: 'ent_equipment',
          type: 'DELIVERS',
        },
      ],
      source: { entity_id: 'ent_party_b', name: 'Party B' },
      target: { entity_id: 'ent_equipment', name: 'Equipment' },
      warnings: [],
    } satisfies GraphFindRelationshipResponse)
    vi.mocked(api.getGraphEvidence).mockResolvedValue({
      evidence: [
        {
          evidence_id: 'ev_001',
          evidence_text: 'Party B must deliver equipment.',
          fact_id: 'fact_001',
          source_chunk_id: 'chk_001',
        },
      ],
      warnings: [],
    } satisfies GraphEvidenceResponse)
    vi.mocked(api.searchGraphRag).mockResolvedValue({
      graph_evidence: [
        {
          chunk_text: 'Party B must deliver equipment.',
          evidence_id: 'ev_001',
          fact_id: 'fact_001',
          source_chunk_id: 'chk_001',
        },
      ],
      text_evidence: [
        {
          chunk_id: 'chk_001',
          doc_id: 'doc_001',
          score: 0.91,
          source: { source_file_name: 'seed.md' },
          text: 'Party B must deliver equipment.',
        },
      ],
      warnings: ['using_object_store_lexical_fallback'],
    } satisfies GraphRagSearchResponse)
    vi.mocked(api.buildDocumentGraph).mockResolvedValue({
      doc_id: 'doc_001',
      job_id: 'job_graph_001',
      knowledge_base_id: 'kb_default',
      ok: true,
      result: { status: 'queued' },
      workspace_id: 'default',
    } satisfies GraphBuildResponse)
  })

  it('loads knowledge data and opens chunks', async () => {
    render(<KnowledgePanel workspaceId="default" />)

    await screen.findByText('seed.md', {}, { timeout: 10000 })
    expect(api.listDocuments).toHaveBeenCalledWith('kb_default', { limit: 100 }, 'default')
    expect(api.getActiveEmbedding).toHaveBeenCalledWith('kb_default', 'default')

    fireEvent.click(screen.getByRole('button', { name: 'Chunks' }))
    await screen.findByText('Party B must deliver equipment.', {}, { timeout: 10000 })
    expect(api.listDocumentChunks).toHaveBeenCalledWith('kb_default', 'doc_001', 'default')
  }, 25000)

  it('clears selected chunks when switching knowledge bases', async () => {
    vi.mocked(api.listKnowledgeBases).mockResolvedValue({
      knowledge_bases: [
        kb(),
        kb({ knowledge_base_id: 'kb_archive', name: 'Archive KB' }),
      ],
      workspace_id: 'default',
    } satisfies ListKnowledgeBasesResponse)
    vi.mocked(api.listDocuments).mockImplementation((knowledgeBaseId = 'kb_default') =>
      Promise.resolve({
        documents:
          knowledgeBaseId === 'kb_archive'
            ? [
                document({
                  doc_id: 'doc_archive',
                  knowledge_base_id: 'kb_archive',
                  source_file_name: 'archive.md',
                }),
              ]
            : [document()],
        knowledge_base_id: knowledgeBaseId,
        workspace_id: 'default',
      } satisfies ListDocumentsResponse),
    )

    render(<KnowledgePanel workspaceId="default" />)

    await screen.findByText('seed.md', {}, { timeout: 10000 })
    fireEvent.click(screen.getByRole('button', { name: 'Chunks' }))
    await screen.findByText('Party B must deliver equipment.', {}, { timeout: 10000 })

    fireEvent.mouseDown(screen.getByRole('combobox'))
    fireEvent.click(await screen.findByText('Archive KB'))

    await screen.findByText('archive.md', {}, { timeout: 10000 })
    expect(screen.queryByText('Party B must deliver equipment.')).not.toBeInTheDocument()
    expect(screen.getByText('No chunks selected.')).toBeInTheDocument()
  }, 25000)

  it('queues graph build jobs', async () => {
    render(<KnowledgePanel workspaceId="default" />)

    await screen.findByText('seed.md', {}, { timeout: 10000 })

    fireEvent.click(screen.getByTestId('build-graph-doc_001'))
    await waitFor(() =>
      expect(api.buildDocumentGraph).toHaveBeenCalledWith('kb_default', 'doc_001', 'default'),
    )
    await screen.findByText('job_graph_001', {}, { timeout: 10000 })

    expect(screen.getByRole('link', { name: 'Jobs' })).toHaveAttribute(
      'href',
      '/jobs?job_id=job_graph_001',
    )
  }, 15000)

  it('queues embedding reindex jobs', async () => {
    render(<KnowledgePanel workspaceId="default" />)

    await screen.findByText('seed.md', {}, { timeout: 10000 })

    fireEvent.click(screen.getByTestId('embedding-reindex'))
    await waitFor(() =>
      expect(api.createEmbeddingReindexJob).toHaveBeenCalledWith(
        'kb_default',
        expect.objectContaining({
          dimension: 3,
          model: 'text-embedding-test',
          provider: 'openai_compatible',
        }),
        'default',
      ),
    )
    await screen.findByText('job_reindex_001', {}, { timeout: 10000 })

    expect(screen.getByRole('link', { name: 'Jobs' })).toHaveAttribute(
      'href',
      '/jobs?job_id=job_reindex_001',
    )
  }, 15000)

  it('does not present local fallback embedding as Milvus-searchable', async () => {
    vi.mocked(api.getActiveEmbedding).mockResolvedValue({
      collection: 'object_store_lexical_fallback',
      dimension: 0,
      model: 'object_store_lexical_fallback',
      provider: 'local_fallback',
      status: 'active',
      version_id: 'embv_fallback',
      workspace_id: 'default',
    } satisfies ActiveEmbeddingResponse)
    vi.mocked(api.listDocuments).mockResolvedValue({
      documents: [document({ search_available: true })],
      knowledge_base_id: 'kb_default',
      workspace_id: 'default',
    } satisfies ListDocumentsResponse)

    render(<KnowledgePanel workspaceId="default" />)

    await screen.findByText('seed.md', {}, { timeout: 10000 })
    expect(screen.getAllByText('Milvus vector search pending').length).toBeGreaterThan(0)
    expect(screen.queryByText('searchable')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('embedding-reindex'))
    await waitFor(() =>
      expect(api.createEmbeddingReindexJob).toHaveBeenCalledWith(
        'kb_default',
        expect.not.objectContaining({
          dimension: expect.any(Number),
          model: expect.any(String),
          provider: expect.any(String),
        }),
        'default',
      ),
    )
  }, 15000)

  it('searches graph entities, expands paths, and loads evidence', async () => {
    render(<KnowledgePanel workspaceId="default" />)

    await screen.findByText('seed.md', {}, { timeout: 10000 })
    await waitFor(() => expect(api.getGraphSchema).toHaveBeenCalledWith('kb_default', 'default'))

    fireEvent.change(screen.getByTestId('graph-entity-query'), {
      target: { value: 'Party B' },
    })
    fireEvent.click(screen.getByTestId('graph-search-entities'))

    await waitFor(() =>
      expect(api.searchGraphEntities).toHaveBeenCalledWith(
        expect.objectContaining({
          knowledge_base_id: 'kb_default',
          query: 'Party B',
        }),
        'default',
      ),
    )
    await screen.findByText('ent_party_b', {}, { timeout: 10000 })

    fireEvent.click(screen.getByTestId('graph-expand-ent_party_b'))

    await waitFor(() =>
      expect(api.expandGraphEntity).toHaveBeenCalledWith(
        'ent_party_b',
        expect.objectContaining({
          depth: 2,
          include_evidence: true,
          knowledge_base_id: 'kb_default',
        }),
        'default',
      ),
    )
    await waitFor(() =>
      expect(api.getGraphEvidence).toHaveBeenCalledWith(
        expect.objectContaining({
          evidence_ids: ['ev_001'],
          fact_ids: ['fact_001'],
          knowledge_base_id: 'kb_default',
        }),
        'default',
      ),
    )
    expect(screen.getByText('path_fact_001')).toBeInTheDocument()
    expect(screen.getByText('Party B must deliver equipment.')).toBeInTheDocument()
  }, 15000)

  it('finds direct graph relationships and loads evidence', async () => {
    render(<KnowledgePanel workspaceId="default" />)

    await screen.findByText('seed.md', {}, { timeout: 10000 })

    fireEvent.change(screen.getByTestId('graph-source-entity'), {
      target: { value: 'Party B' },
    })
    fireEvent.change(screen.getByTestId('graph-target-entity'), {
      target: { value: 'Equipment' },
    })
    fireEvent.click(screen.getByTestId('graph-find-relationships'))

    await waitFor(() =>
      expect(api.findGraphRelationship).toHaveBeenCalledWith(
        expect.objectContaining({
          include_evidence: true,
          knowledge_base_id: 'kb_default',
          source_entity: 'Party B',
          target_entity: 'Equipment',
        }),
        'default',
      ),
    )
    await waitFor(() =>
      expect(api.getGraphEvidence).toHaveBeenCalledWith(
        expect.objectContaining({
          evidence_ids: ['ev_001'],
          fact_ids: ['fact_001'],
          knowledge_base_id: 'kb_default',
        }),
        'default',
      ),
    )
    expect(screen.getByText('Direct relationships')).toBeInTheDocument()
    expect(screen.getAllByText('fact_001').length).toBeGreaterThan(0)
    expect(screen.getByText('DELIVERS')).toBeInTheDocument()
  }, 15000)

  it('runs natural-language GraphRAG search and renders text plus graph evidence', async () => {
    render(<KnowledgePanel workspaceId="default" />)

    await screen.findByText('seed.md', {}, { timeout: 10000 })

    fireEvent.change(screen.getByTestId('graphrag-query'), {
      target: { value: 'What must Party B deliver?' },
    })
    fireEvent.click(screen.getByTestId('graphrag-search'))

    await waitFor(() =>
      expect(api.searchGraphRag).toHaveBeenCalledWith(
        expect.objectContaining({
          final_top_k: 5,
          graph_depth: 2,
          knowledge_base_id: 'kb_default',
          query: 'What must Party B deliver?',
        }),
        'default',
      ),
    )
    expect(screen.getByText('Text evidence')).toBeInTheDocument()
    expect(screen.getByText('Graph evidence')).toBeInTheDocument()
    expect(screen.getByText('using_object_store_lexical_fallback')).toBeInTheDocument()
    expect(screen.getAllByText('seed.md').length).toBeGreaterThan(0)
    expect(screen.getByText('ev_001')).toBeInTheDocument()
  }, 15000)
})
