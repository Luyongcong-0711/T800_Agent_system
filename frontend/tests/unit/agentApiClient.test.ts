import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  AgentApiError,
  activateSkill,
  approveRunApproval,
  buildDocumentGraph,
  cancelRun,
  checkDatabaseHealth,
  createSecret,
  createSkillFromProposal,
  createDiagnosticBundle,
  createEmbeddingReindexJob,
  createLogArchiveJob,
  createMemory,
  createMemorySyncJob,
  createMemorySnapshot,
  createRun,
  createThread,
  deleteMemory,
  getActiveEmbedding,
  getBootstrap,
  getDatabaseConfig,
  getDatabaseHealth,
  getDocument,
  getGraphEvidence,
  getGraphSchema,
  getLogTail,
  getSecret,
  getSecretReferences,
  getJobEventsStreamUrl,
  getJobWorkerStatus,
  getMemory,
  getMemorySnapshot,
  getMemorySyncState,
  getMcpServer,
  getMcpServerHealth,
  getP0Readiness,
  getRunEventsStreamUrl,
  getSkill,
  getSubAgentTask,
  getSystemLogs,
  listSkills,
  listMessages,
  listDocumentChunks,
  listJobs,
  listModelConfigs,
  listRunEvents,
  listSecrets,
  listThreads,
  listSubAgentTasks,
  listMcpTools,
  processNextJob,
  patchMemory,
  recoverStaleRuns,
  deleteSecret,
  disableSecret,
  expandGraphEntity,
  findGraphRelationship,
  findGraphPaths,
  proposeSkill,
  refreshMcpServer,
  rejectRunApproval,
  reviewSubAgentResult,
  searchGraphEntities,
  searchMemories,
  searchSkills,
  disableSkill,
  saveMcpServer,
  setMcpToolPolicy,
  testModelConfig,
  rotateSecret,
  startJobWorker,
  searchGraphRag,
  stopJobWorker,
  updateSecret,
  updateDatabaseConfig,
  updateModelConfig,
  uploadDocumentFileToKnowledgeBase,
  validateSkill,
} from '@/api/agentApiClient'

describe('agentApiClient', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('requests bootstrap from the configured API base URL', async () => {
    const bootstrap = {
      feature_flags: {
        login_enabled: false,
        workspace_switch_enabled: false,
      },
      user: { role: 'owner', user_id: 'default_user' },
      workspace: { workspace_id: 'default', workspace_role: 'owner' },
    }

    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => bootstrap,
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(getBootstrap()).resolves.toEqual(bootstrap)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/bootstrap',
      expect.objectContaining({
        headers: expect.objectContaining({ Accept: 'application/json' }),
      }),
    )
  })

  it('maps backend ErrorResponse into AgentApiError', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({
        ok: false,
        error_type: 'validation_failed',
        message_for_user: 'Invalid request.',
        retryable: false,
        trace_id: 'trace_001',
      }),
      ok: false,
      status: 400,
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(getBootstrap()).rejects.toMatchObject({
      errorType: 'validation_failed',
      message: 'Invalid request.',
      retryable: false,
      status: 400,
      traceId: 'trace_001',
    } satisfies Partial<AgentApiError>)
  })

  it('builds workspace query URLs for P0 resources', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ jobs: [], workspace_id: 'default' }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await listJobs('default', { job_type: 'diagnostic_bundle_job', limit: 20 })
    await getJobWorkerStatus('team/a')
    await getP0Readiness('team/a')
    await startJobWorker('team/a', {
      job_type: ['document_ingestion_job', 'graph_build_job'],
      max_jobs_per_tick: 5,
      poll_interval_ms: 1000,
    })
    await stopJobWorker('team/a')
    await processNextJob('team/a', { job_type: ['graph_build_job'] })

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/workspaces/default/jobs?job_type=diagnostic_bundle_job&limit=20',
      expect.objectContaining({
        headers: expect.objectContaining({ Accept: 'application/json' }),
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/workspaces/team%2Fa/jobs/worker/status',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'http://localhost:8000/workspaces/team%2Fa/readiness',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      'http://localhost:8000/workspaces/team%2Fa/jobs/worker/start?job_type=document_ingestion_job&job_type=graph_build_job&max_jobs_per_tick=5&poll_interval_ms=1000',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      5,
      'http://localhost:8000/workspaces/team%2Fa/jobs/worker/stop',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      6,
      'http://localhost:8000/workspaces/team%2Fa/jobs/process-next?job_type=graph_build_job',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('posts diagnostic bundle requests as JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({
        bundle_id: 'diag_001',
        job_id: 'job_001',
        job_status: 'succeeded',
        manifest_object_key:
          'system/logs/2026-05-30/rt/diagnostic_bundles/diag_001/manifest.json',
        object_key: 'system/logs/2026-05-30/rt/diagnostic_bundles/diag_001/bundle.json',
        redacted: true,
        workspace_id: 'default',
      }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await createDiagnosticBundle({
      components: ['api'],
      trace_id: 'trace_001',
    })

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/workspaces/default/logs/diagnostic-bundles',
      expect.objectContaining({
        body: JSON.stringify({ components: ['api'], trace_id: 'trace_001' }),
        headers: expect.objectContaining({
          Accept: 'application/json',
          'Content-Type': 'application/json',
        }),
        method: 'POST',
      }),
    )
  })

  it('posts run stale recovery through workspace-scoped API', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({
        recovered_count: 1,
        recovered_runs: [],
        workspace_id: 'team/a',
      }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await recoverStaleRuns('team/a')

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/workspaces/team%2Fa/runs/recover-stale',
      expect.objectContaining({
        headers: expect.objectContaining({
          Accept: 'application/json',
          'Content-Type': 'application/json',
        }),
        method: 'POST',
      }),
    )
  })

  it('posts log archive job requests as JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({
        date: '2026-05-31',
        job_id: 'job_archive_001',
        job_status: 'queued',
        manifest_object_key: null,
        redacted: true,
        related_job_id: 'job_archive_001',
        runtime_instance_id: 'rt_local',
        workspace_id: 'default',
      }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await createLogArchiveJob({
      date: '2026-05-31',
      request_id: 'request-001',
      runtime_instance_id: 'rt_local',
    })

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/workspaces/default/logs/archive-jobs',
      expect.objectContaining({
        body: JSON.stringify({
          date: '2026-05-31',
          request_id: 'request-001',
          runtime_instance_id: 'rt_local',
        }),
        headers: expect.objectContaining({
          Accept: 'application/json',
          'Content-Type': 'application/json',
        }),
        method: 'POST',
      }),
    )
  })

  it('requests redacted system log streams', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({
        items: [],
        redacted: true,
        truncated: false,
        workspace_id: 'default',
      }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await getSystemLogs('default', 'errors', { trace_id: 'trace_001' })
    await getLogTail('team/a', { limit: 25 })

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/workspaces/default/logs/system/errors?trace_id=trace_001',
      expect.objectContaining({
        headers: expect.objectContaining({ Accept: 'application/json' }),
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/workspaces/team%2Fa/logs/tail?limit=25',
      expect.objectContaining({
        headers: expect.objectContaining({ Accept: 'application/json' }),
      }),
    )
  })

  it('encodes dynamic MCP path segments and posts refresh/config payloads', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ server_name: 'local/tools', tools: [], workspace_id: 'team/alpha' }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await getMcpServer('local/tools', 'team/alpha')
    await listMcpTools('local/tools', 'team/alpha')
    await getMcpServerHealth('local/tools', 'team/alpha', { live_probe: true })
    await refreshMcpServer('local/tools', { refresh_reason: 'manual check' }, 'team/alpha')
    await saveMcpServer(
      'local/tools',
      {
        enabled: true,
        timeout_ms: 5000,
        transport: 'streamable_http',
        url: 'http://localhost:3939/mcp',
      },
      'team/alpha',
    )

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/workspaces/team%2Falpha/mcp/servers/local%2Ftools',
      expect.objectContaining({
        headers: expect.objectContaining({ Accept: 'application/json' }),
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/workspaces/team%2Falpha/mcp/servers/local%2Ftools/tools',
      expect.objectContaining({
        headers: expect.objectContaining({ Accept: 'application/json' }),
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'http://localhost:8000/workspaces/team%2Falpha/mcp/servers/local%2Ftools/health?live_probe=true',
      expect.objectContaining({
        headers: expect.objectContaining({ Accept: 'application/json' }),
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      'http://localhost:8000/workspaces/team%2Falpha/mcp/servers/local%2Ftools/refresh',
      expect.objectContaining({
        body: JSON.stringify({ refresh_reason: 'manual check' }),
        method: 'POST',
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      5,
      'http://localhost:8000/workspaces/team%2Falpha/mcp/servers/local%2Ftools',
      expect.objectContaining({
        body: JSON.stringify({
          enabled: true,
          timeout_ms: 5000,
          transport: 'streamable_http',
          url: 'http://localhost:3939/mcp',
        }),
        method: 'PUT',
      }),
    )
  })

  it('builds memory and skill query URLs from backend contracts', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ hits: [], workspace_id: 'default' }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await searchMemories('prefers concise answers', ['user_preference', 'project_rule'])
    await searchSkills('summarize docs', 3)
    await getMemory('mem/user name')

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/workspaces/default/memories/search?memory_type=user_preference&memory_type=project_rule&query=prefers+concise+answers',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/workspaces/default/skills/search?query=summarize+docs&top_k=3',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'http://localhost:8000/workspaces/default/memories/mem%2Fuser%20name',
      expect.any(Object),
    )
  })

  it('builds skill lifecycle calls from backend contracts', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ skill_id: 'contract_cleaner', workspace_id: 'default' }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await listSkills('team/a', { limit: 100 })
    await proposeSkill({
      description: 'Normalize contract text.',
      display_name: 'Contract cleaner',
      workflow_steps: ['Normalize headings.'],
    })
    await createSkillFromProposal({
      approval_id: 'approval/001',
      proposal_id: 'skillprop/001',
      skill_id: 'contract_cleaner',
      version: '0.1.0',
    })
    await getSkill('contract/cleaner', '0.1.0')
    await validateSkill('contract/cleaner', { version: '0.1.0' })
    await activateSkill('contract/cleaner', {
      reason: 'Use in this run.',
      run_id: 'run/001',
      thread_id: 'thread/001',
    })
    await disableSkill('contract/cleaner', { reason: 'No longer needed.' })

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/workspaces/team%2Fa/skills?limit=100',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/workspaces/default/skill-proposals',
      expect.objectContaining({
        body: JSON.stringify({
          description: 'Normalize contract text.',
          display_name: 'Contract cleaner',
          workflow_steps: ['Normalize headings.'],
        }),
        method: 'POST',
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'http://localhost:8000/workspaces/default/skills/from-proposal',
      expect.objectContaining({
        body: JSON.stringify({
          approval_id: 'approval/001',
          proposal_id: 'skillprop/001',
          skill_id: 'contract_cleaner',
          version: '0.1.0',
        }),
        method: 'POST',
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      'http://localhost:8000/workspaces/default/skills/contract%2Fcleaner/versions/0.1.0',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      5,
      'http://localhost:8000/workspaces/default/skills/contract%2Fcleaner/validate',
      expect.objectContaining({
        body: JSON.stringify({ version: '0.1.0' }),
        method: 'POST',
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      6,
      'http://localhost:8000/workspaces/default/skills/contract%2Fcleaner/activate',
      expect.objectContaining({
        body: JSON.stringify({
          reason: 'Use in this run.',
          run_id: 'run/001',
          thread_id: 'thread/001',
        }),
        method: 'POST',
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      7,
      'http://localhost:8000/workspaces/default/skills/contract%2Fcleaner/disable',
      expect.objectContaining({
        body: JSON.stringify({ reason: 'No longer needed.' }),
        method: 'POST',
      }),
    )
  })

  it('builds database config and health calls', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ targets: [], workspace_id: 'default' }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await getDatabaseConfig('team/a')
    await updateDatabaseConfig(
      [
        {
          bucket: 'agent-system-prod',
          credential_refs: { primary: 'secret_ref://minio-primary' },
          enabled: true,
          endpoint: 'https://minio.example.com',
          mode: 'remote',
          options: {},
          target: 'minio',
          tls: true,
        },
      ],
      'team/a',
    )
    await getDatabaseHealth('team/a')
    await checkDatabaseHealth('team/a')

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/workspaces/team%2Fa/database/config',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/workspaces/team%2Fa/database/config',
      expect.objectContaining({
        body: JSON.stringify({
          targets: [
            {
              bucket: 'agent-system-prod',
              credential_refs: { primary: 'secret_ref://minio-primary' },
              enabled: true,
              endpoint: 'https://minio.example.com',
              mode: 'remote',
              options: {},
              target: 'minio',
              tls: true,
            },
          ],
        }),
        method: 'PUT',
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'http://localhost:8000/workspaces/team%2Fa/database/health',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      'http://localhost:8000/workspaces/team%2Fa/database/health/check',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('builds memory create, patch, delete and snapshot calls', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ memory_id: 'mem_001', workspace_id: 'default' }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await createMemory({
      content: 'The user prefers concise answers.',
      summary: 'User prefers concise answers.',
      type: 'user_preference',
    })
    await patchMemory('mem/001', { enabled_for_model_context: false, status: 'disabled' })
    await deleteMemory('mem/001')
    await createMemorySnapshot('thread/001', 'concise')
    await getMemorySnapshot('snapshot/001')
    await getMemorySyncState()
    await createMemorySyncJob({ limit: 50 })

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/workspaces/default/memories',
      expect.objectContaining({
        body: JSON.stringify({
          content: 'The user prefers concise answers.',
          summary: 'User prefers concise answers.',
          type: 'user_preference',
        }),
        method: 'POST',
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/workspaces/default/memories/mem%2F001',
      expect.objectContaining({ method: 'PATCH' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'http://localhost:8000/workspaces/default/memories/mem%2F001',
      expect.objectContaining({ method: 'DELETE' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      'http://localhost:8000/workspaces/default/memory-snapshots?query=concise&thread_id=thread%2F001',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      5,
      'http://localhost:8000/workspaces/default/memory-snapshots/snapshot%2F001',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      6,
      'http://localhost:8000/workspaces/default/memories/sync-state',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      7,
      'http://localhost:8000/workspaces/default/jobs',
      expect.objectContaining({
        body: expect.stringContaining('"job_type":"memory_sync_job"'),
        method: 'POST',
      }),
    )
  })

  it('builds knowledge document and chunk URLs with encoded ids', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ doc_id: 'doc/1', workspace_id: 'default' }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await getDocument('kb/main', 'doc/1')
    await listDocumentChunks('kb/main', 'doc/1')

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/workspaces/default/knowledge-bases/kb%2Fmain/documents/doc%2F1',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/workspaces/default/knowledge-bases/kb%2Fmain/documents/doc%2F1/chunks',
      expect.any(Object),
    )
  })

  it('builds active embedding and reindex job calls', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ job_id: 'job_reindex_001', workspace_id: 'default' }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await getActiveEmbedding('kb/main')
    await createEmbeddingReindexJob(
      'kb/main',
      {
        config_id: 'embedding',
        dimension: 3,
        idempotency_key: 'reindex-001',
        model: 'text-embedding-test',
        provider: 'openai_compatible',
      },
    )

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/workspaces/default/knowledge-bases/kb%2Fmain/active-embedding',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/workspaces/default/knowledge-bases/kb%2Fmain/embedding/reindex',
      expect.objectContaining({
        body: JSON.stringify({
          config_id: 'embedding',
          dimension: 3,
          idempotency_key: 'reindex-001',
          model: 'text-embedding-test',
          provider: 'openai_compatible',
        }),
        method: 'POST',
      }),
    )
  })

  it('builds graph query, evidence, and build job calls', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ warnings: [], workspace_id: 'default' }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await getGraphSchema('kb/main', 'team/a')
    await searchGraphEntities(
      {
        include_aliases: true,
        knowledge_base_id: 'kb/main',
        limit: 5,
        query: 'Party B',
      },
      'team/a',
    )
    await expandGraphEntity(
      'ent/party b',
      {
        depth: 2,
        include_evidence: true,
        knowledge_base_id: 'kb/main',
        limit: 10,
      },
      'team/a',
    )
    await findGraphRelationship(
      {
        include_evidence: true,
        knowledge_base_id: 'kb/main',
        relationship_allowlist: ['KNOWS'],
        source_entity: 'Party A',
        target_entity: 'Party B',
      },
      'team/a',
    )
    await findGraphPaths(
      {
        knowledge_base_id: 'kb/main',
        max_depth: 2,
        source_entity: 'Party A',
        target_entity: 'Party B',
      },
      'team/a',
    )
    await getGraphEvidence(
      {
        evidence_ids: ['ev/1'],
        fact_ids: ['fact/1'],
        include_chunk_text: true,
        knowledge_base_id: 'kb/main',
      },
      'team/a',
    )
    await searchGraphRag(
      {
        final_top_k: 5,
        graph_depth: 2,
        knowledge_base_id: 'kb/main',
        query: 'How is Party A related to Party B?',
        top_k: 20,
      },
      'team/a',
    )
    await buildDocumentGraph('kb/main', 'doc/1', 'team/a')

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/workspaces/team%2Fa/graph/schema?knowledge_base_id=kb%2Fmain',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/workspaces/team%2Fa/graph/entities/search',
      expect.objectContaining({
        body: JSON.stringify({
          include_aliases: true,
          knowledge_base_id: 'kb/main',
          limit: 5,
          query: 'Party B',
        }),
        method: 'POST',
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'http://localhost:8000/workspaces/team%2Fa/graph/entities/ent%2Fparty%20b/expand',
      expect.objectContaining({
        body: JSON.stringify({
          depth: 2,
          include_evidence: true,
          knowledge_base_id: 'kb/main',
          limit: 10,
        }),
        method: 'POST',
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      'http://localhost:8000/workspaces/team%2Fa/graph/relationships/find',
      expect.objectContaining({
        body: JSON.stringify({
          include_evidence: true,
          knowledge_base_id: 'kb/main',
          relationship_allowlist: ['KNOWS'],
          source_entity: 'Party A',
          target_entity: 'Party B',
        }),
        method: 'POST',
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      5,
      'http://localhost:8000/workspaces/team%2Fa/graph/paths/find',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      6,
      'http://localhost:8000/workspaces/team%2Fa/graph/evidence',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      7,
      'http://localhost:8000/workspaces/team%2Fa/graph/search',
      expect.objectContaining({
        body: JSON.stringify({
          final_top_k: 5,
          graph_depth: 2,
          knowledge_base_id: 'kb/main',
          query: 'How is Party A related to Party B?',
          top_k: 20,
        }),
        method: 'POST',
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      8,
      'http://localhost:8000/workspaces/team%2Fa/graph/build/kb%2Fmain/documents/doc%2F1',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('uploads document files through multipart form data', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ doc_id: 'doc_001', workspace_id: 'default' }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)
    const file = new File(['hello'], 'contract.md', { type: 'text/markdown' })

    await uploadDocumentFileToKnowledgeBase('kb/main', file, 'team/a')

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/workspaces/team%2Fa/knowledge-bases/kb%2Fmain/documents',
      expect.objectContaining({
        body: expect.any(FormData),
        headers: expect.not.objectContaining({ 'Content-Type': expect.any(String) }),
        method: 'POST',
      }),
    )
  })

  it('builds REST + SSE URLs for Job and Run event replay', () => {
    expect(
      getJobEventsStreamUrl('job/001', { after_event_id: 'evt/001', limit: 50 }, 'team/a'),
    ).toBe(
      'http://localhost:8000/workspaces/team%2Fa/jobs/job%2F001/events/stream?after_event_id=evt%2F001&limit=50',
    )
    expect(getRunEventsStreamUrl('run 001', { limit: 10 })).toBe(
      'http://localhost:8000/workspaces/default/runs/run%20001/events/stream?limit=10',
    )
    expect(
      getRunEventsStreamUrl('run/中文 #1', { after_event_id: 'evt/run #1' }, 'team/a'),
    ).toBe(
      'http://localhost:8000/workspaces/team%2Fa/runs/run%2F%E4%B8%AD%E6%96%87%20%231/events/stream?after_event_id=evt%2Frun+%231',
    )
  })

  it('posts MCP tool policy changes through workspace-scoped API', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ enabled: false }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await setMcpToolPolicy({
      enabled: false,
      risk_level: 'high',
      server_name: 'fs',
      tool_name: 'write_file',
    })

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/workspaces/default/mcp/tools/policy',
      expect.objectContaining({
        body: JSON.stringify({
          enabled: false,
          risk_level: 'high',
          server_name: 'fs',
          tool_name: 'write_file',
        }),
        method: 'POST',
      }),
    )
  })

  it('builds model config list, update and test calls', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ configs: [], workspace_id: 'default' }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await listModelConfigs('team/a')
    await updateModelConfig(
      'main_chat',
      {
        api_key_ref: 'secret_main',
        base_url: 'https://token-plan-cn.xiaomimimo.com/v1',
        context_window_tokens: 200000,
        enabled: true,
        max_output_tokens: 8192,
        model: 'mimo-v2.5-pro',
        provider: 'openai_compatible',
        supports_tool_calling: true,
        timeout_ms: 60000,
      },
      'team/a',
    )
    await testModelConfig('main_chat', { max_output_tokens: 16, prompt: 'ping' }, 'team/a')

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/workspaces/team%2Fa/model-configs',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/workspaces/team%2Fa/model-configs/main_chat',
      expect.objectContaining({ method: 'PUT' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'http://localhost:8000/workspaces/team%2Fa/model-configs/main_chat/test',
      expect.objectContaining({
        body: JSON.stringify({ max_output_tokens: 16, prompt: 'ping' }),
        method: 'POST',
      }),
    )
  })

  it('builds secret lifecycle calls without exposing plaintext in URLs', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ secret_id: 'secret/001', workspace_id: 'default' }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await listSecrets('team/a')
    await createSecret(
      {
        display_name: 'Main key',
        plaintext: 'sk-test-secret',
        type: 'model_api_key',
      },
      'team/a',
    )
    await getSecret('secret/001', 'team/a')
    await updateSecret('secret/001', { display_name: 'Renamed key' }, 'team/a')
    await disableSecret('secret/001', 'team/a')
    await rotateSecret('secret/001', { plaintext: 'sk-rotated-secret' }, 'team/a')
    await getSecretReferences('secret/001', 'team/a')
    await deleteSecret('secret/001', 'team/a')

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/workspaces/team%2Fa/secrets',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/workspaces/team%2Fa/secrets',
      expect.objectContaining({
        body: JSON.stringify({
          display_name: 'Main key',
          plaintext: 'sk-test-secret',
          type: 'model_api_key',
        }),
        method: 'POST',
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'http://localhost:8000/workspaces/team%2Fa/secrets/secret%2F001',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      'http://localhost:8000/workspaces/team%2Fa/secrets/secret%2F001',
      expect.objectContaining({ method: 'PATCH' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      5,
      'http://localhost:8000/workspaces/team%2Fa/secrets/secret%2F001/disable',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      6,
      'http://localhost:8000/workspaces/team%2Fa/secrets/secret%2F001/rotate',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      7,
      'http://localhost:8000/workspaces/team%2Fa/secrets/secret%2F001/references',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      8,
      'http://localhost:8000/workspaces/team%2Fa/secrets/secret%2F001',
      expect.objectContaining({ method: 'DELETE' }),
    )
    expect(fetchMock.mock.calls.map((call) => call[0]).join('\n')).not.toContain('sk-test-secret')
  })

  it('builds thread, message and run calls for chat runtime', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ threads: [], workspace_id: 'default' }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await listThreads('team/a')
    await createThread({ title: 'New chat' }, 'team/a')
    await listMessages('thread/1', { limit: 50 }, 'team/a')
    await createRun('thread/1', { stream: true, user_message: 'hello' }, 'team/a')
    await listRunEvents('run/1', { after_event_id: 'evt/1' }, 'team/a')
    await cancelRun('run/1', 'team/a')
    await approveRunApproval('run/1', 'approval/1', { reason: 'ok' }, 'team/a')
    await rejectRunApproval('run/1', 'approval/1', { reason: 'no' }, 'team/a')

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/workspaces/team%2Fa/threads',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/workspaces/team%2Fa/threads',
      expect.objectContaining({
        body: JSON.stringify({ title: 'New chat' }),
        method: 'POST',
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'http://localhost:8000/workspaces/team%2Fa/threads/thread%2F1/messages?limit=50',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      'http://localhost:8000/workspaces/team%2Fa/threads/thread%2F1/runs',
      expect.objectContaining({
        body: JSON.stringify({ stream: true, user_message: 'hello' }),
        method: 'POST',
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      5,
      'http://localhost:8000/workspaces/team%2Fa/runs/run%2F1/events?after_event_id=evt%2F1',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      6,
      'http://localhost:8000/workspaces/team%2Fa/runs/run%2F1/cancel',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      7,
      'http://localhost:8000/workspaces/team%2Fa/runs/run%2F1/approvals/approval%2F1/approve',
      expect.objectContaining({
        body: JSON.stringify({ reason: 'ok' }),
        method: 'POST',
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      8,
      'http://localhost:8000/workspaces/team%2Fa/runs/run%2F1/approvals/approval%2F1/reject',
      expect.objectContaining({
        body: JSON.stringify({ reason: 'no' }),
        method: 'POST',
      }),
    )
  })

  it('builds SubAgent task list, detail and review calls', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({ task_id: 'subtask/1', workspace_id: 'team/a' }),
      ok: true,
    })
    vi.stubGlobal('fetch', fetchMock)

    await listSubAgentTasks('team/a', {
      parent_run_id: 'run/1',
      status: 'completed',
    })
    await getSubAgentTask('subtask/1', 'team/a')
    await reviewSubAgentResult(
      'subtask/1',
      {
        decision: 'accepted',
        reviewer_notes: 'ok',
      },
      'team/a',
    )

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/workspaces/team%2Fa/subagents/tasks?parent_run_id=run%2F1&status=completed',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/workspaces/team%2Fa/subagents/tasks/subtask%2F1',
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'http://localhost:8000/workspaces/team%2Fa/subagents/tasks/subtask%2F1/review',
      expect.objectContaining({
        body: JSON.stringify({
          decision: 'accepted',
          reviewer_notes: 'ok',
        }),
        method: 'POST',
      }),
    )
  })
})
