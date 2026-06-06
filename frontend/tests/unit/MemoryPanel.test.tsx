import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ListMemoriesResponse,
  MemoryDetailResponse,
  MemorySnapshotResponse,
  MemorySummary,
  MemorySyncStateResponse,
} from '@/api/schemas/workspace'
import { MemoryPanel } from '@/components/memory/MemoryPanel'

vi.mock('@/api/agentApiClient', () => ({
  approveMemory: vi.fn(),
  createMemory: vi.fn(),
  createMemorySyncJob: vi.fn(),
  createMemorySnapshot: vi.fn(),
  deleteMemory: vi.fn(),
  getMemory: vi.fn(),
  getMemorySyncState: vi.fn(),
  listMemorySyncJobs: vi.fn(),
  listMemories: vi.fn(),
  patchMemory: vi.fn(),
  rejectMemory: vi.fn(),
  searchMemories: vi.fn(),
}))

const api = await import('@/api/agentApiClient')

const now = '2026-05-31T00:00:00.000Z'

function memory(overrides: Partial<MemorySummary> = {}): MemorySummary {
  return {
    confidence: 0.95,
    created_at: now,
    enabled_for_model_context: true,
    field: 'answer_style',
    frontend_visible: true,
    memory_id: 'mem_001',
    requires_approval: false,
    scope: 'global',
    sensitive: false,
    status: 'active',
    summary: 'User prefers concise Chinese answers.',
    type: 'user_preference',
    updated_at: now,
    user_id: 'default_user',
    workspace_id: null,
    ...overrides,
  }
}

function memoryDetail(overrides: Partial<MemoryDetailResponse> = {}): MemoryDetailResponse {
  const base = memory(overrides)
  return {
    ...base,
    content: 'The user prefers concise Chinese answers.',
    content_object_key: 'memory/default/mem_001.json',
    revision: 1,
    source: {},
    value: 'concise',
    visibility: 'user_visible',
    ...overrides,
  }
}

describe('MemoryPanel', () => {
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
    vi.mocked(api.listMemories).mockResolvedValue({
      memories: [memory()],
      workspace_id: 'default',
    } satisfies ListMemoriesResponse)
    vi.mocked(api.listMemorySyncJobs).mockResolvedValue({
      jobs: [],
      workspace_id: 'default',
    })
    vi.mocked(api.getMemorySyncState).mockResolvedValue({
      last_event_id: null,
      last_event_seq: 0,
      pending_targets: [],
      revision: 0,
      schema_version: 1,
      updated_at: null,
      workspace_id: 'default',
    } satisfies MemorySyncStateResponse)
    vi.mocked(api.createMemorySyncJob).mockResolvedValue({
      created_at: now,
      current_stage: null,
      idempotency_key: 'memory-sync-default-test',
      job_id: 'job_memory_sync',
      job_type: 'memory_sync_job',
      leaf_state: {},
      manifest: {},
      priority: 'normal',
      progress_percent: 0,
      status: 'queued',
      target_scope: { scope_type: 'memory_sync', sync_stream: 'default:memory_sync' },
      title: 'Memory sync',
      updated_at: now,
      workspace_id: 'default',
    })
    vi.mocked(api.getMemory).mockResolvedValue(memoryDetail())
    vi.mocked(api.patchMemory).mockResolvedValue(
      memory({ enabled_for_model_context: false, status: 'disabled' }) as MemoryDetailResponse,
    )
    vi.mocked(api.approveMemory).mockResolvedValue(
      memory({ requires_approval: false, status: 'active' }) as MemoryDetailResponse,
    )
    vi.mocked(api.rejectMemory).mockResolvedValue(
      memory({
        enabled_for_model_context: false,
        requires_approval: false,
        status: 'rejected',
      }) as MemoryDetailResponse,
    )
    vi.mocked(api.deleteMemory).mockResolvedValue(
      memory({ enabled_for_model_context: false, status: 'deleted' }) as MemoryDetailResponse,
    )
    vi.mocked(api.createMemory).mockResolvedValue(
      memory({ memory_id: 'mem_new', summary: 'User likes tables.' }) as MemoryDetailResponse,
    )
    vi.mocked(api.createMemorySnapshot).mockResolvedValue({
      created_at: now,
      included_memory_ids: ['mem_001'],
      memory_snapshot_id: 'memsnap_001',
      preferences: ['User prefers concise Chinese answers.'],
      profile: {},
      project_facts: [],
      project_rules: [],
      thread_id: 'thread_001',
      user_id: 'default_user',
      workspace_id: 'default',
    } satisfies MemorySnapshotResponse)
  })

  it('loads visible memories', async () => {
    render(<MemoryPanel workspaceId="default" />)

    await waitFor(() => expect(api.listMemories).toHaveBeenCalled(), { timeout: 1000 })
    expect(screen.getByText('User prefers concise Chinese answers.')).toBeInTheDocument()
    expect(api.listMemories).toHaveBeenCalledWith('default', { include_deleted: false })
  })

  it('can disable model context injection for a memory', async () => {
    render(<MemoryPanel workspaceId="default" />)

    await waitFor(() => expect(api.listMemories).toHaveBeenCalled(), { timeout: 1000 })
    fireEvent.click(screen.getByTestId('memory-toggle-mem_001'))
    fireEvent.click(await screen.findByRole('button', { name: 'Disable memory' }))
    await waitFor(() =>
      expect(api.patchMemory).toHaveBeenCalledWith(
        'mem_001',
        { enabled_for_model_context: false, status: 'disabled' },
        'default',
      ),
      { timeout: 1000 },
    )
  }, 10000)

  it('can open memory detail and save editable fields', async () => {
    vi.mocked(api.getMemory).mockResolvedValue(
      memoryDetail({
        content: 'The user prefers concise Chinese answers.',
        value: 'concise',
      }),
    )
    vi.mocked(api.patchMemory).mockResolvedValue(
      memoryDetail({
        content: 'The user wants compact Chinese responses.',
        enabled_for_model_context: false,
        field: 'answer_format',
        summary: 'User wants compact Chinese responses.',
        value: 'compact',
      }),
    )

    render(<MemoryPanel workspaceId="default" />)

    await screen.findByText('User prefers concise Chinese answers.')
    fireEvent.click(screen.getByTestId('memory-edit-mem_001'))

    await waitFor(() => expect(api.getMemory).toHaveBeenCalledWith('mem_001', 'default'))
    const drawer = await screen.findByRole('dialog', { name: 'Memory detail' })
    await within(drawer).findByDisplayValue('The user prefers concise Chinese answers.')

    fireEvent.change(within(drawer).getByDisplayValue('User prefers concise Chinese answers.'), {
      target: { value: 'User wants compact Chinese responses.' },
    })
    fireEvent.change(within(drawer).getByDisplayValue('The user prefers concise Chinese answers.'), {
      target: { value: 'The user wants compact Chinese responses.' },
    })
    fireEvent.change(within(drawer).getByDisplayValue('concise'), {
      target: { value: 'compact' },
    })
    fireEvent.change(within(drawer).getByDisplayValue('answer_style'), {
      target: { value: 'answer_format' },
    })
    fireEvent.change(within(drawer).getByLabelText('Confidence'), {
      target: { value: '0.8' },
    })
    fireEvent.click(within(drawer).getByRole('switch'))
    fireEvent.click(within(drawer).getByRole('button', { name: 'Save changes' }))

    await waitFor(() =>
      expect(api.patchMemory).toHaveBeenCalledWith(
        'mem_001',
        expect.objectContaining({
          content: 'The user wants compact Chinese responses.',
          enabled_for_model_context: false,
          field: 'answer_format',
          confidence: 0.8,
          summary: 'User wants compact Chinese responses.',
          value: 'compact',
        }),
        'default',
      ),
    )
    await waitFor(() => expect(api.listMemories).toHaveBeenCalledTimes(2))
  }, 10000)

  it('can delete a memory so it stops future model injection', async () => {
    render(<MemoryPanel workspaceId="default" />)

    await waitFor(() => expect(api.listMemories).toHaveBeenCalled(), { timeout: 1000 })
    fireEvent.click(screen.getByTestId('memory-delete-mem_001'))
    fireEvent.click(await screen.findByRole('button', { name: 'Delete memory' }))
    await waitFor(
      () => expect(api.deleteMemory).toHaveBeenCalledWith('mem_001', 'default'),
      { timeout: 1000 },
    )
  }, 10000)

  it('can create memories and preview model snapshots', async () => {
    render(<MemoryPanel workspaceId="default" />)

    await waitFor(() => expect(api.listMemories).toHaveBeenCalled(), { timeout: 1000 })
    expect(screen.getByText('User prefers concise Chinese answers.')).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('Memory summary'), {
      target: { value: 'User likes tables.' },
    })
    fireEvent.change(screen.getByPlaceholderText('Full memory content'), {
      target: { value: 'The user likes answers formatted as tables.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save memory' }))
    await waitFor(() =>
      expect(api.createMemory).toHaveBeenCalledWith(
        expect.objectContaining({
          content: 'The user likes answers formatted as tables.',
          summary: 'User likes tables.',
          type: 'user_preference',
        }),
        'default',
      ),
    )

    fireEvent.change(screen.getByPlaceholderText('thread_id'), {
      target: { value: 'thread_001' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Snapshot' }))
    await waitFor(() =>
      expect(api.createMemorySnapshot).toHaveBeenCalledWith('thread_001', undefined, 'default'),
    )
    await screen.findByText('memsnap_001')
  }, 10000)

  it('can review pending memory candidates without hiding normal memories', async () => {
    vi.mocked(api.listMemories).mockResolvedValue({
      memories: [
        memory({
          field: 'name',
          memory_id: 'mem_profile',
          summary: 'User name is Zhang San.',
          type: 'user_profile',
        }),
        memory({
          enabled_for_model_context: false,
          memory_id: 'mem_pending',
          requires_approval: true,
          scope: 'workspace',
          status: 'pending_approval',
          summary: 'Project rule candidate needs review.',
          type: 'project_rule',
          workspace_id: 'default',
        }),
      ],
      workspace_id: 'default',
    } satisfies ListMemoriesResponse)
    vi.mocked(api.approveMemory).mockResolvedValue(
      memory({
        memory_id: 'mem_pending',
        requires_approval: false,
        status: 'active',
        summary: 'Project rule candidate needs review.',
        type: 'project_rule',
      }) as MemoryDetailResponse,
    )

    render(<MemoryPanel workspaceId="default" />)

    await screen.findByText('User name is Zhang San.')
    expect(screen.getAllByText('Project rule candidate needs review.').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByTestId('memory-approve-mem_pending'))
    await waitFor(() =>
      expect(api.approveMemory).toHaveBeenCalledWith('mem_pending', 'default'),
    )
    await waitFor(() => expect(api.listMemories).toHaveBeenCalledTimes(2))

    fireEvent.click(screen.getByTestId('memory-reject-mem_pending'))
    fireEvent.click(await screen.findByRole('button', { name: 'Reject memory' }))
    await waitFor(() =>
      expect(api.rejectMemory).toHaveBeenCalledWith('mem_pending', 'default'),
    )
  }, 10000)
})
