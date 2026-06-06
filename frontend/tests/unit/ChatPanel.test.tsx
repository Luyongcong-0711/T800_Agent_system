import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ConversationMessage,
  ListMessagesResponse,
  ListRunEventsResponse,
  ListThreadsResponse,
  RunDetailResponse,
  RunEvent,
  ThreadDetailResponse,
  ThreadSummary,
} from '@/api/schemas/workspace'
import { ChatPanel } from '@/components/chat/ChatPanel'

vi.mock('@/api/agentApiClient', () => ({
  approveRunApproval: vi.fn(),
  cancelRun: vi.fn(),
  createRun: vi.fn(),
  createThread: vi.fn(),
  getRunEventsStreamUrl: vi.fn(
    (runId: string) => `http://localhost:8010/runs/${runId}/events/stream`,
  ),
  listMessages: vi.fn(),
  listRunEvents: vi.fn(),
  listThreads: vi.fn(),
  patchThread: vi.fn(),
  recoverStaleRuns: vi.fn(),
  rejectRunApproval: vi.fn(),
  rollbackRunOperation: vi.fn(),
}))

const api = await import('@/api/agentApiClient')

class MockEventSource {
  static instances: MockEventSource[] = []

  readonly listeners = new Map<
    string,
    Array<(event: MessageEvent<string>) => void>
  >()
  readonly url: string
  closed = false
  onerror: ((event: Event) => void) | null = null

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: EventListener) {
    const listeners = this.listeners.get(type) ?? []
    listeners.push(listener as (event: MessageEvent<string>) => void)
    this.listeners.set(type, listeners)
  }

  close() {
    this.closed = true
  }

  emit(type: string, payload: RunEvent) {
    const event = { data: JSON.stringify(payload) } as MessageEvent<string>
    this.listeners.get(type)?.forEach((listener) => listener(event))
  }
}

const now = '2026-05-31T00:00:00.000Z'

function thread(overrides: Partial<ThreadSummary> = {}): ThreadSummary {
  return {
    created_at: now,
    current_run_id: null,
    current_run_status: null,
    last_message_at: null,
    last_message_id: null,
    last_message_preview: null,
    message_count: 0,
    pinned: false,
    run_count: 0,
    status: 'active',
    thread_id: 'thread_001',
    title: 'Validation chat',
    updated_at: now,
    user_id: 'default_user',
    workspace_id: 'default',
    ...overrides,
  }
}

function message(
  role: ConversationMessage['role'],
  content: string,
): ConversationMessage {
  return {
    content,
    created_at: now,
    message_id: `msg_${role}_${content.length}`,
    role,
    run_id: role === 'assistant' ? 'run_001' : null,
    thread_id: 'thread_001',
    workspace_id: 'default',
  }
}

function run(overrides: Partial<RunDetailResponse> = {}): RunDetailResponse {
  return {
    created_at: now,
    idempotency_key: 'ui_request',
    last_event_id: null,
    last_event_seq: 0,
    leaf_state: {},
    model_error: null,
    run_id: 'run_001',
    status: 'running',
    thread_id: 'thread_001',
    updated_at: now,
    user_message_id: 'msg_user_5',
    workspace_id: 'default',
    ...overrides,
  }
}

function runEvent(
  type: string,
  eventSeq: number,
  overrides: Partial<RunEvent> = {},
): RunEvent {
  return {
    created_at: now,
    event_id: `evt_${eventSeq}`,
    event_seq: eventSeq,
    payload: {},
    run_id: 'run_001',
    thread_id: 'thread_001',
    type,
    workspace_id: 'default',
    ...overrides,
  }
}

function listMessagesResponse(
  messages: ConversationMessage[],
): ListMessagesResponse {
  return {
    messages,
    thread_id: 'thread_001',
    workspace_id: 'default',
  }
}

describe('ChatPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    MockEventSource.instances = []
    vi.stubGlobal('EventSource', MockEventSource)
    vi.stubGlobal('crypto', { randomUUID: () => 'request-001' })

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
  })

  it('sends a run through SSE and refreshes messages after stream close', async () => {
    const userMessage = message('user', 'hello')
    const assistantMessage = message(
      'assistant',
      'Runtime smoke completed with 1 tool result(s).',
    )

    vi.mocked(api.listThreads).mockResolvedValue({
      threads: [thread()],
      workspace_id: 'default',
    } satisfies ListThreadsResponse)
    vi.mocked(api.listMessages)
      .mockResolvedValueOnce(listMessagesResponse([]))
      .mockResolvedValueOnce(listMessagesResponse([userMessage]))
      .mockResolvedValueOnce(
        listMessagesResponse([userMessage, assistantMessage]),
      )
    vi.mocked(api.createThread).mockResolvedValue(
      thread() satisfies ThreadDetailResponse,
    )
    vi.mocked(api.createRun).mockResolvedValue(run())
    vi.mocked(api.listRunEvents).mockResolvedValue({
      events: [runEvent('run_started', 1)],
      next_after_event_id: 'evt_1',
      run_id: 'run_001',
      run_status: 'running',
      workspace_id: 'default',
    } satisfies ListRunEventsResponse)

    render(<ChatPanel workspaceId="default" />)

    await screen.findByText('Validation chat')

    fireEvent.change(screen.getByPlaceholderText('Ask the agent...'), {
      target: { value: 'hello' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(api.createRun).toHaveBeenCalledTimes(1))
    expect(api.createRun).toHaveBeenCalledWith(
      'thread_001',
      expect.objectContaining({
        idempotency_key: 'ui-request-001',
        stream: true,
        user_message: 'hello',
      }),
      'default',
    )
    const runningButtonLabel = await screen.findByText('Running')
    expect(runningButtonLabel.closest('button')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled()

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1))
    const source = MockEventSource.instances[0]

    await act(async () => {
      source.emit('assistant_message', runEvent('assistant_message', 2))
      source.emit('stream_closed', runEvent('stream_closed', 3))
    })

    await screen.findByText('Runtime smoke completed with 1 tool result(s).')
    expect(screen.getByText('Send').closest('button')).toBeEnabled()
    expect(source.closed).toBe(true)
    expect(screen.getByText('run_started')).toBeInTheDocument()
    expect(screen.getByText('assistant_message')).toBeInTheDocument()
    expect(screen.getByText('stream_closed')).toBeInTheDocument()
  })

  it('keeps streamed assistant text when final history is not visible yet', async () => {
    const userMessage = message('user', 'hello')

    vi.mocked(api.listThreads).mockResolvedValue({
      threads: [thread()],
      workspace_id: 'default',
    } satisfies ListThreadsResponse)
    vi.mocked(api.listMessages)
      .mockResolvedValueOnce(listMessagesResponse([]))
      .mockResolvedValueOnce(listMessagesResponse([userMessage]))
      .mockResolvedValueOnce(listMessagesResponse([userMessage]))
    vi.mocked(api.createThread).mockResolvedValue(
      thread() satisfies ThreadDetailResponse,
    )
    vi.mocked(api.createRun).mockResolvedValue(run())
    vi.mocked(api.listRunEvents).mockResolvedValue({
      events: [runEvent('run_started', 1)],
      next_after_event_id: 'evt_1',
      run_id: 'run_001',
      run_status: 'running',
      workspace_id: 'default',
    } satisfies ListRunEventsResponse)

    render(<ChatPanel workspaceId="default" />)

    await screen.findByText('Validation chat')

    fireEvent.change(screen.getByPlaceholderText('Ask the agent...'), {
      target: { value: 'hello' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1))
    const source = MockEventSource.instances[0]

    await act(async () => {
      source.emit(
        'assistant_delta',
        runEvent('assistant_delta', 2, { payload: { delta: 'streamed answer' } }),
      )
    })

    await screen.findByText('streamed answer')

    await act(async () => {
      source.emit(
        'stream_closed',
        runEvent('stream_closed', 3, { payload: { status: 'completed' } }),
      )
    })

    expect(await screen.findByText('streamed answer')).toBeInTheDocument()
    expect(screen.getByText('Send').closest('button')).toBeEnabled()
  })

  it('resumes SSE when the selected thread already has a running run', async () => {
    const assistantMessage = message(
      'assistant',
      'Recovered from the running stream.',
    )
    const runningThread = thread({
      current_run_id: 'run_resume',
      current_run_status: 'running',
    })

    vi.mocked(api.listThreads).mockResolvedValue({
      threads: [runningThread],
      workspace_id: 'default',
    } satisfies ListThreadsResponse)
    vi.mocked(api.listMessages).mockResolvedValue(listMessagesResponse([assistantMessage]))
    vi.mocked(api.listRunEvents).mockResolvedValue({
      events: [runEvent('run_started', 1, { run_id: 'run_resume' })],
      next_after_event_id: 'evt_1',
      run_id: 'run_resume',
      run_status: 'running',
      workspace_id: 'default',
    } satisfies ListRunEventsResponse)

    render(<ChatPanel workspaceId="default" />)

    await screen.findByText('Validation chat')
    const runningButtonLabel = await screen.findByText('Running')
    expect(runningButtonLabel.closest('button')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled()
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1))
    expect(api.createRun).not.toHaveBeenCalled()

    const source = MockEventSource.instances[0]
    expect(source.url).toContain('run_resume')

    await act(async () => {
      source.emit(
        'assistant_message',
        runEvent('assistant_message', 2, { run_id: 'run_resume' }),
      )
      source.emit(
        'stream_closed',
        runEvent('stream_closed', 3, { run_id: 'run_resume' }),
      )
    })

    await screen.findByText('Recovered from the running stream.')
    expect(screen.getByText('Send').closest('button')).toBeEnabled()
  })

  it('recovers a likely stale running run and refreshes chat state', async () => {
    const runningThread = thread({
      current_run_id: 'run_stale',
      current_run_status: 'running',
    })
    const recoveredThread = thread({
      current_run_id: 'run_stale',
      current_run_status: 'failed',
      last_message_preview: 'stale recovered',
    })
    const recoveredRun = run({
      model_error: 'stale_running_recovered',
      run_id: 'run_stale',
      status: 'failed',
    })

    vi.mocked(api.listThreads)
      .mockResolvedValueOnce({
        threads: [runningThread],
        workspace_id: 'default',
      } satisfies ListThreadsResponse)
      .mockResolvedValueOnce({
        threads: [recoveredThread],
        workspace_id: 'default',
      } satisfies ListThreadsResponse)
    vi.mocked(api.listMessages).mockResolvedValue(listMessagesResponse([]))
    vi.mocked(api.listRunEvents)
      .mockResolvedValueOnce({
        events: [runEvent('run_started', 1, { run_id: 'run_stale' })],
        next_after_event_id: 'evt_1',
        run_id: 'run_stale',
        run_status: 'running',
        workspace_id: 'default',
      } satisfies ListRunEventsResponse)
      .mockResolvedValueOnce({
        events: [
          runEvent('run_started', 1, { run_id: 'run_stale' }),
          runEvent('run_recovery_started', 2, { run_id: 'run_stale' }),
          runEvent('run_failed', 3, {
            payload: { error_type: 'stale_running_recovered' },
            run_id: 'run_stale',
          }),
        ],
        next_after_event_id: 'evt_3',
        run_id: 'run_stale',
        run_status: 'failed',
        workspace_id: 'default',
      } satisfies ListRunEventsResponse)
    vi.mocked(api.recoverStaleRuns).mockResolvedValue({
      recovered_count: 1,
      recovered_runs: [recoveredRun],
      workspace_id: 'default',
    })

    render(<ChatPanel workspaceId="default" />)

    await screen.findByText('Validation chat')
    const recoverButton = await screen.findByRole('button', {
      name: 'Recover stale',
    })
    fireEvent.click(recoverButton)

    await waitFor(() =>
      expect(api.recoverStaleRuns).toHaveBeenCalledWith('default'),
    )
    await screen.findByText('run_failed')
    expect(screen.getByText('Recovered 1 stale run(s).')).toBeInTheDocument()
    expect(screen.getByText('Send').closest('button')).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Recover stale' })).not.toBeInTheDocument()
  })

  it('cancels a running run, closes SSE, and refreshes the thread', async () => {
    const userMessage = message('user', 'please stop')
    const runningThread = thread({
      current_run_id: 'run_cancel',
      current_run_status: 'running',
    })
    const cancelledThread = thread({
      current_run_id: 'run_cancel',
      current_run_status: 'cancelled',
      last_message_preview: 'please stop',
    })

    vi.mocked(api.listThreads)
      .mockResolvedValueOnce({
        threads: [runningThread],
        workspace_id: 'default',
      } satisfies ListThreadsResponse)
      .mockResolvedValueOnce({
        threads: [cancelledThread],
        workspace_id: 'default',
      } satisfies ListThreadsResponse)
    vi.mocked(api.listMessages)
      .mockResolvedValueOnce(listMessagesResponse([]))
      .mockResolvedValueOnce(listMessagesResponse([userMessage]))
    vi.mocked(api.listRunEvents)
      .mockResolvedValueOnce({
        events: [runEvent('run_started', 1, { run_id: 'run_cancel' })],
        next_after_event_id: 'evt_1',
        run_id: 'run_cancel',
        run_status: 'running',
        workspace_id: 'default',
      } satisfies ListRunEventsResponse)
      .mockResolvedValueOnce({
        events: [
          runEvent('run_started', 1, { run_id: 'run_cancel' }),
          runEvent('run_cancelled', 2, { run_id: 'run_cancel' }),
        ],
        next_after_event_id: 'evt_2',
        run_id: 'run_cancel',
        run_status: 'cancelled',
        workspace_id: 'default',
      } satisfies ListRunEventsResponse)
    vi.mocked(api.cancelRun).mockResolvedValue({
      run_id: 'run_cancel',
      status: 'cancelled',
      thread_id: 'thread_001',
      workspace_id: 'default',
    })

    render(<ChatPanel workspaceId="default" />)

    await screen.findByText('Validation chat')
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1))
    const source = MockEventSource.instances[0]

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    await waitFor(() =>
      expect(api.cancelRun).toHaveBeenCalledWith('run_cancel', 'default'),
    )
    expect(source.closed).toBe(true)
    await screen.findByText('run_cancelled')
    expect(screen.getByText('Send').closest('button')).toBeEnabled()
    expect((await screen.findAllByText('please stop')).length).toBeGreaterThan(
      0,
    )
  })

  it('loads approval events when the selected thread is waiting for approval', async () => {
    vi.mocked(api.listThreads).mockResolvedValue({
      threads: [
        thread({
          current_run_id: 'run_waiting',
          current_run_status: 'waiting_approval',
        }),
      ],
      workspace_id: 'default',
    } satisfies ListThreadsResponse)
    vi.mocked(api.listMessages).mockResolvedValue(listMessagesResponse([]))
    vi.mocked(api.listRunEvents).mockResolvedValue({
      events: [
        runEvent('skill_entrypoint_approval_required', 1, {
          run_id: 'run_waiting',
          payload: {
            approval_id: 'approval_waiting',
            artifacts: {
              operation_plan_object_key:
                'workspaces/default/runs/run_waiting/skill_runs/skillrun_001/operation_plan.json',
            },
          },
        }),
        runEvent('run_waiting_approval', 2, {
          payload: { status: 'waiting_approval' },
          run_id: 'run_waiting',
        }),
      ],
      next_after_event_id: 'evt_2',
      run_id: 'run_waiting',
      run_status: 'waiting_approval',
      workspace_id: 'default',
    } satisfies ListRunEventsResponse)

    render(<ChatPanel workspaceId="default" />)

    await screen.findByText('Validation chat')
    await screen.findByText('skill_entrypoint_approval_required')
    expect(screen.getByText('run_waiting_approval')).toBeInTheDocument()
    expect(screen.queryByText('Running')).not.toBeInTheDocument()
    expect(
      screen.getByText('Waiting approval').closest('button'),
    ).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled()
    expect(screen.getByText('Skill staged patch approval')).toBeInTheDocument()
    expect(MockEventSource.instances).toHaveLength(0)
  })

  it('cancels a run that is waiting for approval', async () => {
    const waitingThread = thread({
      current_run_id: 'run_waiting',
      current_run_status: 'waiting_approval',
    })
    const cancelledThread = thread({
      current_run_id: 'run_waiting',
      current_run_status: 'cancelled',
      last_message_preview: 'approval cancelled',
    })

    vi.mocked(api.listThreads)
      .mockResolvedValueOnce({
        threads: [waitingThread],
        workspace_id: 'default',
      } satisfies ListThreadsResponse)
      .mockResolvedValueOnce({
        threads: [cancelledThread],
        workspace_id: 'default',
      } satisfies ListThreadsResponse)
    vi.mocked(api.listMessages)
      .mockResolvedValueOnce(listMessagesResponse([]))
      .mockResolvedValueOnce(
        listMessagesResponse([message('user', 'approval cancelled')]),
      )
    vi.mocked(api.listRunEvents)
      .mockResolvedValueOnce({
        events: [
          runEvent('skill_entrypoint_approval_required', 1, {
            run_id: 'run_waiting',
            payload: { approval_id: 'approval_waiting' },
          }),
          runEvent('run_waiting_approval', 2, {
            payload: { status: 'waiting_approval' },
            run_id: 'run_waiting',
          }),
        ],
        next_after_event_id: 'evt_2',
        run_id: 'run_waiting',
        run_status: 'waiting_approval',
        workspace_id: 'default',
      } satisfies ListRunEventsResponse)
      .mockResolvedValueOnce({
        events: [
          runEvent('skill_entrypoint_approval_required', 1, {
            run_id: 'run_waiting',
            payload: { approval_id: 'approval_waiting' },
          }),
          runEvent('run_cancelled', 3, {
            payload: { status: 'cancelled' },
            run_id: 'run_waiting',
          }),
        ],
        next_after_event_id: 'evt_3',
        run_id: 'run_waiting',
        run_status: 'cancelled',
        workspace_id: 'default',
      } satisfies ListRunEventsResponse)
    vi.mocked(api.cancelRun).mockResolvedValue({
      run_id: 'run_waiting',
      status: 'cancelled',
      thread_id: 'thread_001',
      workspace_id: 'default',
    })

    render(<ChatPanel workspaceId="default" />)

    await screen.findByText('Waiting approval')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    await waitFor(() =>
      expect(api.cancelRun).toHaveBeenCalledWith('run_waiting', 'default'),
    )
    await screen.findByText('run_cancelled')
    expect(screen.getByText('Send').closest('button')).toBeEnabled()
    expect(
      (await screen.findAllByText('approval cancelled')).length,
    ).toBeGreaterThan(0)
  })

  it('approves a waiting approval event and refreshes run events', async () => {
    const waitingThread = thread({
      current_run_id: 'run_waiting',
      current_run_status: 'waiting_approval',
    })
    vi.mocked(api.listThreads).mockResolvedValue({
      threads: [waitingThread],
      workspace_id: 'default',
    } satisfies ListThreadsResponse)
    vi.mocked(api.listMessages).mockResolvedValue(listMessagesResponse([]))
    vi.mocked(api.listRunEvents)
      .mockResolvedValueOnce({
        events: [
          runEvent('skill_entrypoint_approval_required', 1, {
            run_id: 'run_waiting',
            payload: {
              approval_id: 'approval_waiting',
              artifacts: {
                operation_plan_object_key:
                  'workspaces/default/runs/run_waiting/skill_runs/skillrun_001/operation_plan.json',
              },
            },
          }),
          runEvent('run_waiting_approval', 2, {
            payload: { status: 'waiting_approval' },
            run_id: 'run_waiting',
          }),
        ],
        next_after_event_id: 'evt_2',
        run_id: 'run_waiting',
        run_status: 'waiting_approval',
        workspace_id: 'default',
      } satisfies ListRunEventsResponse)
      .mockResolvedValueOnce({
        events: [
          runEvent('skill_entrypoint_approval_required', 1, {
            run_id: 'run_waiting',
            payload: { approval_id: 'approval_waiting' },
          }),
          runEvent('approval_approved', 3, {
            payload: {
              approval_id: 'approval_waiting',
              status: 'approved_pending_execution',
            },
            run_id: 'run_waiting',
          }),
        ],
        next_after_event_id: 'evt_3',
        run_id: 'run_waiting',
        run_status: 'waiting_approval',
        workspace_id: 'default',
      } satisfies ListRunEventsResponse)
    vi.mocked(api.approveRunApproval).mockResolvedValue({
      approval_id: 'approval_waiting',
      artifacts: {},
      decision: 'approved',
      operation_plan_object_key:
        'workspaces/default/runs/run_waiting/skill_runs/skillrun_001/operation_plan.json',
      run_id: 'run_waiting',
      run_status: 'waiting_approval',
      skill_run_id: 'skillrun_001',
      status: 'approved_pending_execution',
      thread_id: 'thread_001',
      updated_at: now,
      workspace_id: 'default',
    })

    render(<ChatPanel workspaceId="default" />)

    await screen.findByText('skill_entrypoint_approval_required')
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    await waitFor(() =>
      expect(api.approveRunApproval).toHaveBeenCalledWith(
        'run_waiting',
        'approval_waiting',
        expect.objectContaining({
          idempotency_key: 'ui-request-001',
          reason: 'Approved from chat run event.',
        }),
        'default',
      ),
    )
    await screen.findByText('approval_approved')
    expect(screen.getByRole('button', { name: /Approve/ })).toBeDisabled()
    expect(api.rejectRunApproval).not.toHaveBeenCalled()
  })

  it('renders run event payload artifacts for approval inspection', async () => {
    vi.mocked(api.listThreads).mockResolvedValue({
      threads: [
        thread({
          current_run_id: 'run_001',
          current_run_status: 'running',
        }),
      ],
      workspace_id: 'default',
    } satisfies ListThreadsResponse)
    vi.mocked(api.listMessages).mockResolvedValue(listMessagesResponse([]))
    vi.mocked(api.listRunEvents).mockResolvedValue({
      events: [
        runEvent('skill_entrypoint_approval_required', 1, {
          payload: {
            approval_id: 'approval_001',
            artifacts: {
              diff_object_key:
                'workspaces/default/runs/run_001/skill_runs/skillrun_001/diff.patch',
              operation_plan_object_key:
                'workspaces/default/runs/run_001/skill_runs/skillrun_001/operation_plan.json',
            },
          },
        }),
      ],
      next_after_event_id: 'evt_1',
      run_id: 'run_001',
      run_status: 'completed',
      workspace_id: 'default',
    } satisfies ListRunEventsResponse)

    render(<ChatPanel workspaceId="default" />)

    await screen.findByText('Validation chat')
    await screen.findByText('skill_entrypoint_approval_required')
    expect(screen.getByText('Skill staged patch approval')).toBeInTheDocument()
    expect(screen.getByText(/Plan:/)).toHaveTextContent('operation_plan.json')
    expect(screen.getByText(/Diff:/)).toHaveTextContent('diff.patch')
    expect(screen.getByTestId('run-event-payload-evt_1')).toHaveTextContent(
      'operation_plan_object_key',
    )
    expect(screen.getByTestId('run-event-payload-evt_1')).toHaveTextContent(
      'diff.patch',
    )
  })

  it('sorts pinned threads first and toggles pin from the thread actions menu', async () => {
    const normalThread = thread({
      last_message_preview: 'normal preview',
      thread_id: 'thread_normal',
      title: 'Normal chat',
      updated_at: '2026-05-31T00:00:02.000Z',
    })
    const pinnedThread = thread({
      pinned: true,
      thread_id: 'thread_pinned',
      title: 'Pinned chat',
      updated_at: '2026-05-31T00:00:01.000Z',
    })

    vi.mocked(api.listThreads).mockResolvedValue({
      threads: [normalThread, pinnedThread],
      workspace_id: 'default',
    } satisfies ListThreadsResponse)
    vi.mocked(api.listMessages).mockResolvedValue(listMessagesResponse([]))
    vi.mocked(api.patchThread).mockResolvedValue({
      ...normalThread,
      pinned: true,
    } satisfies ThreadDetailResponse)

    render(<ChatPanel workspaceId="default" />)

    const pinnedTitle = await screen.findByText('Pinned chat')
    const normalTitle = await screen.findByText('Normal chat')
    expect(
      pinnedTitle.compareDocumentPosition(normalTitle) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()

    fireEvent.click(
      screen.getByRole('button', { name: 'Thread actions for Normal chat' }),
    )
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Pin' }))

    await waitFor(() =>
      expect(api.patchThread).toHaveBeenCalledWith(
        'thread_normal',
        { pinned: true },
        'default',
      ),
    )
    expect(await screen.findAllByText('Pinned')).toHaveLength(2)
  })

  it('renames a thread through the thread actions menu', async () => {
    const originalThread = thread({ title: 'Original chat' })

    vi.mocked(api.listThreads).mockResolvedValue({
      threads: [originalThread],
      workspace_id: 'default',
    } satisfies ListThreadsResponse)
    vi.mocked(api.listMessages).mockResolvedValue(listMessagesResponse([]))
    vi.mocked(api.patchThread).mockResolvedValue({
      ...originalThread,
      title: 'Renamed chat',
    } satisfies ThreadDetailResponse)

    render(<ChatPanel workspaceId="default" />)

    await screen.findByText('Original chat')
    fireEvent.click(
      screen.getByRole('button', { name: 'Thread actions for Original chat' }),
    )
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Rename' }))
    fireEvent.change(await screen.findByLabelText('Thread title'), {
      target: { value: 'Renamed chat' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'OK' }))

    await waitFor(() =>
      expect(api.patchThread).toHaveBeenCalledWith(
        'thread_001',
        { title: 'Renamed chat' },
        'default',
      ),
    )
    await screen.findByText('Renamed chat')
    expect(screen.queryByText('Original chat')).not.toBeInTheDocument()
  })

  it('archives the active thread and switches to the next active thread', async () => {
    const firstThread = thread({
      thread_id: 'thread_first',
      title: 'First chat',
    })
    const secondThread = thread({
      thread_id: 'thread_second',
      title: 'Second chat',
      updated_at: '2026-05-30T00:00:00.000Z',
    })

    vi.mocked(api.listThreads).mockResolvedValue({
      threads: [firstThread, secondThread],
      workspace_id: 'default',
    } satisfies ListThreadsResponse)
    vi.mocked(api.listMessages).mockResolvedValue(listMessagesResponse([]))
    vi.mocked(api.patchThread).mockResolvedValue({
      ...firstThread,
      status: 'archived',
    } satisfies ThreadDetailResponse)

    render(<ChatPanel workspaceId="default" />)

    await screen.findByText('First chat')
    fireEvent.click(
      screen.getByRole('button', { name: 'Thread actions for First chat' }),
    )
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Archive' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Archive' }))

    await waitFor(() =>
      expect(api.patchThread).toHaveBeenCalledWith(
        'thread_first',
        { status: 'archived' },
        'default',
      ),
    )
    expect(screen.queryByText('First chat')).not.toBeInTheDocument()
    expect(screen.getByText('Second chat')).toBeInTheDocument()
  })

  it('keeps blocked threads from being archived or deleted while a run is active', async () => {
    vi.mocked(api.listThreads).mockResolvedValue({
      threads: [
        thread({
          current_run_id: 'run_blocked',
          current_run_status: 'running',
          title: 'Blocked chat',
        }),
      ],
      workspace_id: 'default',
    } satisfies ListThreadsResponse)
    vi.mocked(api.listMessages).mockResolvedValue(listMessagesResponse([]))
    vi.mocked(api.listRunEvents).mockResolvedValue({
      events: [runEvent('run_started', 1, { run_id: 'run_blocked' })],
      next_after_event_id: 'evt_1',
      run_id: 'run_blocked',
      run_status: 'running',
      workspace_id: 'default',
    } satisfies ListRunEventsResponse)

    render(<ChatPanel workspaceId="default" />)

    await screen.findByText('Blocked chat')
    fireEvent.click(
      screen.getByRole('button', { name: 'Thread actions for Blocked chat' }),
    )

    expect(
      await screen.findByRole('menuitem', { name: 'Archive' }),
    ).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByRole('menuitem', { name: 'Delete' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    expect(api.patchThread).not.toHaveBeenCalled()
  })
})
