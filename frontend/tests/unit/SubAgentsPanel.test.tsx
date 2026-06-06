import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ListSubAgentTasksResponse,
  SubAgentReviewResponse,
  SubAgentTaskDetail,
  SubAgentTaskSummary,
} from '@/api/schemas/workspace'
import { SubAgentsPanel } from '@/components/subagents/SubAgentsPanel'

vi.mock('@/api/agentApiClient', () => ({
  createSubAgentTask: vi.fn(),
  getSubAgentTask: vi.fn(),
  listSubAgentTasks: vi.fn(),
  reviewSubAgentResult: vi.fn(),
}))

const api = await import('@/api/agentApiClient')

const now = '2026-05-31T00:00:00.000Z'

function task(
  overrides: Partial<SubAgentTaskSummary> = {},
): SubAgentTaskSummary {
  return {
    agent_type: 'frontend-developer',
    allowed_tools: ['Read', 'Edit'],
    created_at: now,
    forbidden_tools: [],
    mode: 'write',
    needs_main_review: true,
    objective: 'Implement SubAgent panel.',
    output_schema: 'SubAgentResult',
    parent_run_id: 'run_001',
    parent_thread_id: 'thread_001',
    read_scope: ['frontend/src/components/subagents'],
    requires_main_review: true,
    status: 'completed',
    task_id: 'subtask_001',
    timeout_ms: 300000,
    token_budget: 12000,
    updated_at: now,
    workspace_id: 'default',
    write_scope: ['frontend/src/components/subagents/SubAgentsPanel.tsx'],
    ...overrides,
  }
}

function detail(
  overrides: Partial<SubAgentTaskDetail> = {},
): SubAgentTaskDetail {
  return {
    ...task(),
    expected_output: 'A reviewed frontend panel.',
    object_keys: {
      result: 'workspaces/default/subagents/subtask_001/result.json',
    },
    result: {
      changed_files: ['frontend/src/components/subagents/SubAgentsPanel.tsx'],
      created_job_id: 'job_subagent_001',
      findings: [],
      summary: 'Panel implemented.',
    },
    review: null,
    schema_version: 1,
    ...overrides,
  }
}

describe('SubAgentsPanel', () => {
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
    vi.mocked(api.listSubAgentTasks).mockResolvedValue({
      tasks: [task()],
      workspace_id: 'default',
    } satisfies ListSubAgentTasksResponse)
    vi.mocked(api.getSubAgentTask)
      .mockResolvedValueOnce(detail())
      .mockResolvedValue(
        detail({ review: { decision: 'accepted' }, status: 'reviewed' }),
      )
    vi.mocked(api.createSubAgentTask).mockResolvedValue(
      detail({
        objective: 'Create with advanced budget.',
        parent_thread_id: 'thread_advanced',
        task_id: 'subtask_created',
        timeout_ms: 600000,
        token_budget: 24000,
        write_scope: [],
      }),
    )
    vi.mocked(api.reviewSubAgentResult).mockResolvedValue({
      decision: 'accepted',
      parent_run_id: 'run_001',
      review_status: 'reviewed',
      reviewed_at: now,
      reviewed_subagent_result: {},
      reviewer_notes: 'Reviewed from SubAgents page: accepted.',
      schema_version: 1,
      task_id: 'subtask_001',
      workspace_id: 'default',
    } satisfies SubAgentReviewResponse)
  })

  it('loads SubAgent tasks, opens detail, reviews result, and exposes job handoff', async () => {
    render(<SubAgentsPanel workspaceId="default" />)

    await screen.findByText('frontend-developer')
    expect(api.listSubAgentTasks).toHaveBeenCalledWith('default', {
      parent_run_id: undefined,
      status: undefined,
    })
    expect(screen.getByText('run_001')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('subagent-open-subtask_001'))

    await waitFor(() =>
      expect(api.getSubAgentTask).toHaveBeenCalledWith(
        'subtask_001',
        'default',
      ),
    )
    await waitFor(() =>
      expect(document.body.textContent).toContain('Panel implemented.'),
    )
    expect(document.body.textContent).toContain('job_subagent_001')

    expect(screen.getAllByRole('link', { name: 'Jobs' })[0]).toHaveAttribute(
      'href',
      '/jobs?job_id=job_subagent_001',
    )

    fireEvent.click(screen.getByTestId('subagent-review-accept'))
    await waitFor(() =>
      expect(api.reviewSubAgentResult).toHaveBeenCalledWith(
        'subtask_001',
        {
          decision: 'accepted',
          reviewer_notes: 'Reviewed from SubAgents page: accepted.',
        },
        'default',
      ),
    )
    await waitFor(() => expect(api.listSubAgentTasks).toHaveBeenCalledTimes(2))
  }, 10000)

  it('creates a readonly SubAgent task with advanced form fields and empty write scope', async () => {
    render(<SubAgentsPanel workspaceId="default" />)

    await screen.findByText('frontend-developer')

    fireEvent.change(screen.getByTestId('subagent-create-parent-run-id'), {
      target: { value: 'run_advanced' },
    })
    fireEvent.change(screen.getByPlaceholderText('parent_thread_id'), {
      target: { value: 'thread_advanced' },
    })
    fireEvent.change(screen.getByPlaceholderText('timeout_ms'), {
      target: { value: '600000' },
    })
    fireEvent.change(screen.getByPlaceholderText('token_budget'), {
      target: { value: '24000' },
    })
    fireEvent.change(screen.getByPlaceholderText('Objective'), {
      target: { value: 'Create with advanced budget.' },
    })

    expect(
      screen.getByPlaceholderText('write_scope disabled in readonly mode'),
    ).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Create task' }))

    await waitFor(() =>
      expect(api.createSubAgentTask).toHaveBeenCalledWith(
        expect.objectContaining({
          mode: 'readonly',
          objective: 'Create with advanced budget.',
          parent_run_id: 'run_advanced',
          parent_thread_id: 'thread_advanced',
          timeout_ms: 600000,
          token_budget: 24000,
          write_scope: [],
        }),
        'default',
      ),
    )
  })

  it('requires parent_run_id and write_scope for write-mode SubAgent tasks', async () => {
    render(<SubAgentsPanel workspaceId="default" />)

    await screen.findByText('frontend-developer')

    expect(screen.getByText('parent_run_id is required.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create task' })).toBeDisabled()

    fireEvent.change(screen.getByTestId('subagent-create-parent-run-id'), {
      target: { value: 'run_write' },
    })
    fireEvent.change(screen.getByPlaceholderText('Objective'), {
      target: { value: 'Create a staged patch.' },
    })
    await selectSubAgentMode('write')

    expect(screen.getByText('write_scope is required in write mode.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create task' })).toBeDisabled()

    fireEvent.change(screen.getByTestId('subagent-create-write-scope'), {
      target: { value: 'frontend/src/components/subagents/SubAgentsPanel.tsx' },
    })

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Create task' })).not.toBeDisabled(),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Create task' }))

    await waitFor(() =>
      expect(api.createSubAgentTask).toHaveBeenCalledWith(
        expect.objectContaining({
          mode: 'write',
          objective: 'Create a staged patch.',
          parent_run_id: 'run_write',
          write_scope: ['frontend/src/components/subagents/SubAgentsPanel.tsx'],
        }),
        'default',
      ),
    )
  })
})

async function selectSubAgentMode(mode: 'readonly' | 'write') {
  fireEvent.click(within(screen.getByTestId('subagent-create-mode')).getByText(mode))
}
