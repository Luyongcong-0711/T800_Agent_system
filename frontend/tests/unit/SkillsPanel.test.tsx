import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ListThreadsResponse,
  ListSkillsResponse,
  SkillActivationResponse,
  SkillDetailResponse,
  SkillProposalResponse,
  SkillSearchResponse,
  SkillSummary,
  ThreadSummary,
} from '@/api/schemas/workspace'
import { SkillsPanel } from '@/components/skills/SkillsPanel'

vi.mock('@/api/agentApiClient', () => ({
  activateSkill: vi.fn(),
  createSkillFromProposal: vi.fn(),
  disableSkill: vi.fn(),
  getSkill: vi.fn(),
  listThreads: vi.fn(),
  listSkills: vi.fn(),
  proposeSkill: vi.fn(),
  searchSkills: vi.fn(),
  validateSkill: vi.fn(),
}))

const api = await import('@/api/agentApiClient')

const now = '2026-05-31T00:00:00.000Z'

function skill(overrides: Partial<SkillSummary> = {}): SkillSummary {
  return {
    description: 'Normalize contract text and extract metadata.',
    display_name: 'Contract cleaner',
    enabled: true,
    entrypoint_count: 1,
    requires_activation: true,
    requires_validation: false,
    risk_level: 'low',
    skill_id: 'contract_cleaner',
    status: 'enabled',
    updated_at: now,
    version: '0.1.0',
    when_to_use: ['Contracts need cleanup'],
    workspace_id: 'default',
    ...overrides,
  }
}

function detail(overrides: Partial<SkillDetailResponse> = {}): SkillDetailResponse {
  return {
    ...skill(),
    created_at: now,
    entrypoints: [
      {
        args_schema_summary: { type: 'object' },
        name: 'normalize_contract',
        risk_level: 'low',
        tool_name_when_activated: 'skill_contract_cleaner_normalize_contract',
        type: 'prompt_workflow',
        write_mode: 'none',
      },
    ],
    knowledge_sections: [{ section_id: 'notes', title: 'Knowledge notes' }],
    permissions: { database_read: ['minio'], file_read: ['workspace'], network: false },
    summary: 'Normalize contract text and extract metadata.',
    validation_status: 'validated',
    workflow_summary: ['Normalize headings.', 'Extract metadata.'],
    ...overrides,
  }
}

function thread(overrides: Partial<ThreadSummary> = {}): ThreadSummary {
  return {
    created_at: now,
    current_run_id: 'run_auto',
    current_run_status: 'running',
    last_message_at: now,
    last_message_id: 'msg_001',
    last_message_preview: 'Working...',
    message_count: 2,
    pinned: false,
    run_count: 1,
    status: 'active',
    thread_id: 'thread_auto',
    title: 'Active run',
    updated_at: now,
    user_id: 'default_user',
    workspace_id: 'default',
    ...overrides,
  }
}

describe('SkillsPanel', () => {
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
    vi.mocked(api.listSkills).mockResolvedValue({
      skills: [skill()],
      workspace_id: 'default',
    } satisfies ListSkillsResponse)
    vi.mocked(api.listThreads).mockResolvedValue({
      threads: [],
      workspace_id: 'default',
    } satisfies ListThreadsResponse)
    vi.mocked(api.searchSkills).mockResolvedValue({
      items: [skill()],
      workspace_id: 'default',
    } satisfies SkillSearchResponse)
    vi.mocked(api.getSkill).mockResolvedValue(detail())
    vi.mocked(api.disableSkill).mockResolvedValue(
      detail({ enabled: false, status: 'disabled' }),
    )
    vi.mocked(api.activateSkill).mockResolvedValue({
      activated_entrypoint_tools: ['skill_contract_cleaner_normalize_contract'],
      context_block_object_key:
        'workspaces/default/runs/run_001/skills/contract_cleaner/context_block.json',
      created_at: now,
      reason: 'Activate for current run.',
      run_id: 'run_001',
      skill_id: 'contract_cleaner',
      thread_id: 'thread_001',
      version: '0.1.0',
      workspace_id: 'default',
    } satisfies SkillActivationResponse)
    vi.mocked(api.proposeSkill).mockResolvedValue({
      approval_id: 'approval_001',
      approval_required: true,
      created_at: now,
      description: 'Normalize contract text.',
      display_name: 'Contract cleaner',
      entrypoints: [],
      knowledge_notes: [],
      permissions: {},
      proposal_id: 'skillprop_001',
      risk_level: 'low',
      schema_version: 1,
      script_required: false,
      source: {},
      status: 'pending_approval',
      updated_at: now,
      when_to_use: [],
      workflow_steps: ['Normalize headings.'],
      workspace_id: 'default',
    } satisfies SkillProposalResponse)
    vi.mocked(api.createSkillFromProposal).mockResolvedValue(detail())
    vi.mocked(api.validateSkill).mockResolvedValue(detail())
  })

  it('loads, searches, opens, activates, and disables skills', async () => {
    render(
      <SkillsPanel
        runtimeContext={{
          run_id: 'run_001',
          run_status: 'running',
          thread_id: 'thread_001',
        }}
        workspaceId="default"
      />,
    )

    await screen.findByText('Contract cleaner')
    expect(api.listSkills).toHaveBeenCalledWith('default', { limit: 100 })

    fireEvent.change(screen.getByPlaceholderText('Search skills'), {
      target: { value: 'contract' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(api.searchSkills).toHaveBeenCalledWith('contract', 10, 'default'))

    fireEvent.click(screen.getByTestId('skill-open-contract_cleaner'))
    await waitFor(() =>
      expect(api.getSkill).toHaveBeenCalledWith('contract_cleaner', undefined, 'default'),
    )
    await waitFor(() => expect(document.body.textContent).toContain('Normalize headings.'))

    fireEvent.click(screen.getByRole('button', { name: 'Activate' }))
    await waitFor(() =>
      expect(api.activateSkill).toHaveBeenCalledWith(
        'contract_cleaner',
        {
          reason: 'Activate for current run.',
          run_id: 'run_001',
          thread_id: 'thread_001',
          version: '0.1.0',
        },
        'default',
      ),
    )
    await waitFor(() =>
      expect(document.body.textContent).toContain('skill_contract_cleaner_normalize_contract'),
    )

    fireEvent.click(screen.getByTestId('skill-disable-contract_cleaner'))
    fireEvent.click(await screen.findByRole('button', { name: 'Disable skill' }))
    await waitFor(() =>
      expect(api.disableSkill).toHaveBeenCalledWith(
        'contract_cleaner',
        { reason: 'Disabled from Skills page.' },
        'default',
      ),
    )
  }, 15000)

  it('allows manual run and thread entry when no running chat context is detected', async () => {
    render(<SkillsPanel workspaceId="default" />)

    await screen.findByText('Contract cleaner')
    fireEvent.click(screen.getByTestId('skill-open-contract_cleaner'))
    await waitFor(() =>
      expect(api.getSkill).toHaveBeenCalledWith('contract_cleaner', undefined, 'default'),
    )

    expect(screen.getByText('No running chat run detected.')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('run_id'), {
      target: { value: 'manual_run' },
    })
    fireEvent.change(screen.getByPlaceholderText('thread_id'), {
      target: { value: 'manual_thread' },
    })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Activate' })).not.toBeDisabled(),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Activate' }))

    await waitFor(() =>
      expect(api.activateSkill).toHaveBeenCalledWith(
        'contract_cleaner',
        {
          reason: 'Activate for current run.',
          run_id: 'manual_run',
          thread_id: 'manual_thread',
          version: '0.1.0',
        },
        'default',
      ),
    )
  }, 15000)

  it('auto-fills activation runtime from a running thread when chat runtime is absent', async () => {
    vi.mocked(api.listThreads).mockResolvedValue({
      threads: [thread()],
      workspace_id: 'default',
    } satisfies ListThreadsResponse)

    render(<SkillsPanel workspaceId="default" />)

    await screen.findByText('Contract cleaner')
    await waitFor(() => expect(screen.getByPlaceholderText('run_id')).toHaveValue('run_auto'))
    expect(screen.getByPlaceholderText('thread_id')).toHaveValue('thread_auto')

    fireEvent.click(screen.getByTestId('skill-open-contract_cleaner'))
    await waitFor(() =>
      expect(api.getSkill).toHaveBeenCalledWith('contract_cleaner', undefined, 'default'),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Activate' }))

    await waitFor(() =>
      expect(api.activateSkill).toHaveBeenCalledWith(
        'contract_cleaner',
        {
          reason: 'Activate for current run.',
          run_id: 'run_auto',
          thread_id: 'thread_auto',
          version: '0.1.0',
        },
        'default',
      ),
    )
  }, 15000)

  it('validates a disabled script skill from the Skills table', async () => {
    vi.mocked(api.listSkills).mockResolvedValue({
      skills: [
        skill({
          enabled: false,
          requires_validation: true,
          risk_level: 'high',
          status: 'disabled',
        }),
      ],
      workspace_id: 'default',
    } satisfies ListSkillsResponse)
    vi.mocked(api.validateSkill).mockResolvedValue(
      detail({
        enabled: true,
        requires_validation: true,
        risk_level: 'high',
        status: 'enabled',
        validation_status: 'validated',
      }),
    )

    render(<SkillsPanel workspaceId="default" />)

    await screen.findByText('Contract cleaner')
    fireEvent.click(screen.getByTestId('skill-validate-contract_cleaner'))

    await waitFor(() =>
      expect(api.validateSkill).toHaveBeenCalledWith(
        'contract_cleaner',
        { version: null },
        'default',
      ),
    )
    await waitFor(() => expect(api.listSkills).toHaveBeenCalledTimes(2))
  }, 15000)

  it('can propose a skill and create it after approval id is visible', async () => {
    render(<SkillsPanel workspaceId="default" />)

    await screen.findByText('Contract cleaner')
    fireEvent.change(screen.getByPlaceholderText('Contract cleanup workflow'), {
      target: { value: 'Contract cleaner' },
    })
    fireEvent.change(
      screen.getByPlaceholderText('Normalize contract text and extract reusable metadata.'),
      {
        target: { value: 'Normalize contract text.' },
      },
    )
    fireEvent.change(screen.getByPlaceholderText('One step per line'), {
      target: { value: 'Normalize headings.' },
    })

    fireEvent.click(screen.getByRole('button', { name: 'Propose' }))
    await waitFor(() =>
      expect(api.proposeSkill).toHaveBeenCalledWith(
        expect.objectContaining({
          description: 'Normalize contract text.',
          display_name: 'Contract cleaner',
          script_required: false,
          workflow_steps: ['Normalize headings.'],
        }),
        'default',
      ),
    )
    await screen.findByText('skillprop_001')
    await screen.findByText('approval_001')

    fireEvent.click(screen.getByTestId('skill-use-latest-proposal'))
    fireEvent.click(screen.getByTestId('skill-create-latest-proposal'))
    await waitFor(() =>
      expect(api.createSkillFromProposal).toHaveBeenCalledWith(
        {
          approval_id: 'approval_001',
          proposal_id: 'skillprop_001',
          skill_id: 'contract_cleaner',
          version: '0.1.0',
        },
        'default',
      ),
    )
  }, 10000)
})
