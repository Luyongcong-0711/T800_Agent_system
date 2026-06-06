import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { P0ReadinessResponse } from '@/api/schemas/workspace'
import { P0ReadinessPanel } from '@/components/readiness/P0ReadinessPanel'

vi.mock('@/api/agentApiClient', () => ({
  getP0Readiness: vi.fn(),
}))

const api = await import('@/api/agentApiClient')

const readiness: P0ReadinessResponse = {
  categories: [
    {
      blocked_count: 1,
      category: 'acceptance',
      checks: [],
      fail_count: 0,
      pass_count: 0,
      status: 'blocked',
      warn_count: 0,
    },
  ],
  checks: [
    {
      category: 'acceptance',
      check_id: 'external.browser_smoke',
      details: {
        report_summary: { fail: 1, pass: 4, total: 5 },
        source_check: {
          check_id: 'runtime.frontend_route_smoke',
          command: ['pnpm.cmd', 'exec', 'vitest'],
          cwd: 'C:/agent-system/frontend',
          duration_ms: 120,
          next_action: 'Inspect failed route.',
          status: 'fail',
          stderr_tail: 'route failed',
          stdout_tail: '4 passed, 1 failed',
          summary: 'Frontend route smoke failed.',
        },
        source_check_id: 'runtime.frontend_route_smoke',
      },
      evidence: ['source_status=fail'],
      next_actions: ['Inspect failed route.'],
      required: true,
      status: 'blocked',
      summary: 'Frontend route smoke failed.',
      title: 'Browser E2E smoke',
    },
  ],
  environment: 'development',
  generated_at: '2026-05-31T12:00:00Z',
  ok: false,
  remaining_blockers: ['external.browser_smoke: Frontend route smoke failed.'],
  runtime_instance_id: 'rt_local',
  status: 'blocked',
  summary: { blocked: 1, fail: 0, pass: 0, total: 1, warn: 0 },
  workspace_id: 'default',
}

describe('P0ReadinessPanel', () => {
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
  })

  it('shows required blockers and expandable acceptance report details', async () => {
    vi.mocked(api.getP0Readiness).mockResolvedValue(readiness)

    const { container } = render(<P0ReadinessPanel workspaceId="default" />)

    await screen.findByText('Browser E2E smoke')
    expect(api.getP0Readiness).toHaveBeenCalledWith('default')
    expect(screen.getByText('action required')).toBeInTheDocument()
    expect(screen.getByText('required')).toBeInTheDocument()
    expect(screen.getAllByText('external.browser_smoke')).toHaveLength(2)
    expect(screen.getByRole('link', { name: 'Logs' })).toHaveAttribute('href', '/logs')

    const expandButton = container.querySelector<HTMLButtonElement>('.ant-table-row-expand-icon')
    expect(expandButton).not.toBeNull()
    fireEvent.click(expandButton as HTMLButtonElement)

    await screen.findByText('Route smoke evidence')
    await screen.findByText('stdout tail')
    expect(screen.getByText('4 passed, 1 failed')).toBeInTheDocument()
    expect(screen.getByText('route failed')).toBeInTheDocument()
    expect(screen.getByText('fail: 1')).toBeInTheDocument()
    expect(screen.getByText('total: 5')).toBeInTheDocument()
  })

  it('shows direct route smoke details from the acceptance report', async () => {
    vi.mocked(api.getP0Readiness).mockResolvedValue({
      ...readiness,
      checks: [
        {
          category: 'acceptance',
          check_id: 'runtime.frontend_route_smoke',
          details: {
            check_id: 'runtime.frontend_route_smoke',
            duration_ms: 250,
            next_action: 'Start the frontend service and inspect /memory.',
            status: 'fail',
            stdout_tail:
              'PASS / Chat: HTTP 200, bytes=2048\nFAIL /memory Memory: HTTP 500, bytes=512',
          },
          evidence: ['report=p0_acceptance_report.json'],
          next_actions: [],
          required: true,
          status: 'blocked',
          summary: 'Frontend workspace route smoke failed: 1 route(s) failed.',
          title: 'Browser E2E smoke',
        },
      ],
    })

    const { container } = render(<P0ReadinessPanel workspaceId="default" />)

    await screen.findByText('Browser E2E smoke')
    const expandButton = container.querySelector<HTMLButtonElement>('.ant-table-row-expand-icon')
    expect(expandButton).not.toBeNull()
    fireEvent.click(expandButton as HTMLButtonElement)

    await screen.findByText('Route smoke evidence')
    expect(screen.getByText('Start the frontend service and inspect /memory.')).toBeInTheDocument()
    expect(screen.getByText('/memory')).toBeInTheDocument()
    expect(screen.getByText('Memory: HTTP 500, bytes=512')).toBeInTheDocument()
  })

  it('shows final handoff summary and non-passing checks', async () => {
    vi.mocked(api.getP0Readiness).mockResolvedValue({
      ...readiness,
      checks: [
        {
          category: 'acceptance',
          check_id: 'external.final_handoff',
          details: {
            final_handoff: {
              missing_check_ids: [],
              non_passing_checks: [
                {
                  check_id: 'runtime.mcp_live_smoke',
                  next_action: 'Configure a real MCP server.',
                  status: 'skipped',
                  summary: 'No MCP server name was provided.',
                },
              ],
              ready: false,
              recommended_command:
                'conda activate py313\npython scripts/p0_acceptance.py --include-runtime-http --include-model-smoke --include-docker --mcp-server-name <configured-server-name>',
            },
            report_summary: { pass: 8, skipped: 1, total: 9 },
            stale_required_check_ids: ['runtime.model_config.embedding_smoke'],
            stale_required_flags: ['--include-model-smoke'],
          },
          evidence: ['final_handoff_ready=false'],
          next_actions: ['Run final acceptance.'],
          required: true,
          status: 'blocked',
          summary: 'Final P0 handoff checks are incomplete.',
          title: 'Final handoff completeness',
        },
      ],
    })

    const { container } = render(<P0ReadinessPanel workspaceId="default" />)

    await screen.findByText('Final handoff completeness')
    const expandButton = container.querySelector<HTMLButtonElement>('.ant-table-row-expand-icon')
    expect(expandButton).not.toBeNull()
    fireEvent.click(expandButton as HTMLButtonElement)

    await screen.findByText('Final handoff')
    expect(screen.getByText('ready: false')).toBeInTheDocument()
    expect(screen.getByText('runtime.mcp_live_smoke')).toBeInTheDocument()
    expect(screen.getByText('stale checks: 1')).toBeInTheDocument()
    expect(screen.getByText('stale flags: 1')).toBeInTheDocument()
    expect(screen.getByText('runtime.model_config.embedding_smoke')).toBeInTheDocument()
    expect(screen.getByText('--include-model-smoke')).toBeInTheDocument()
    expect(screen.getByText('No MCP server name was provided.')).toBeInTheDocument()
    expect(screen.getByText('Configure a real MCP server.')).toBeInTheDocument()
  })

  it('shows model smoke targets and degraded database health without implying success', async () => {
    vi.mocked(api.getP0Readiness).mockResolvedValue({
      ...readiness,
      categories: [
        {
          blocked_count: 1,
          category: 'models',
          checks: [],
          fail_count: 0,
          pass_count: 0,
          status: 'blocked',
          warn_count: 0,
        },
        {
          blocked_count: 1,
          category: 'database',
          checks: [],
          fail_count: 0,
          pass_count: 0,
          status: 'blocked',
          warn_count: 1,
        },
      ],
      checks: [
        {
          category: 'models',
          check_id: 'models.config',
          details: {},
          evidence: [
            'main_chat:configured:openai_compatible:default_env:model=set:base_url=set:api_key_ref=set',
            'graphrag_llm:configured:openai_compatible:default_env:model=set:base_url=set:api_key_ref=set',
            'embedding:configured:openai_compatible:default_env:model=set:base_url=set:api_key_ref=set',
          ],
          next_actions: [],
          required: true,
          status: 'pass',
          summary: 'Required model config slots are ready.',
          title: 'Required model configs',
        },
        {
          category: 'acceptance',
          check_id: 'external.main_chat_model_smoke',
          details: {},
          evidence: ['source_status=pass'],
          next_actions: [],
          required: true,
          status: 'pass',
          summary: 'Main chat smoke passed.',
          title: 'Main chat model smoke',
        },
        {
          category: 'acceptance',
          check_id: 'external.graphrag_llm_model_smoke',
          details: {},
          evidence: ['source_status=blocked'],
          next_actions: ['Run GraphRAG smoke.'],
          required: true,
          status: 'blocked',
          summary: 'GraphRAG LLM smoke has not passed.',
          title: 'GraphRAG LLM smoke',
        },
        {
          category: 'database',
          check_id: 'external.database_live_health',
          details: {},
          evidence: ['source_status=fail', 'http_status=401'],
          next_actions: ['Fix database credentials and rerun live health.'],
          required: true,
          status: 'fail',
          summary: 'Database live health returned 401.',
          title: 'Database live health',
        },
        {
          category: 'database',
          check_id: 'database.health_snapshot',
          details: {},
          evidence: ['source=snapshot', 'ok=false', 'unhealthy=milvus'],
          next_actions: ['Run database health check from Settings.'],
          required: true,
          status: 'blocked',
          summary: 'Latest database health snapshot is degraded.',
          title: 'Database health snapshot',
        },
      ],
      summary: { blocked: 2, fail: 0, pass: 2, total: 4, warn: 0 },
    })

    render(<P0ReadinessPanel workspaceId="default" />)

    await screen.findByText('Model config smoke')
    expect(screen.getByText('main_chat')).toBeInTheDocument()
    expect(screen.getByText('graphrag_llm')).toBeInTheDocument()
    expect(screen.getByText('embedding')).toBeInTheDocument()
    expect(screen.getAllByText('runtime smoke')).toHaveLength(2)
    expect(screen.getByText('config readiness')).toBeInTheDocument()
    expect(screen.getByText(/No runtime smoke check is recorded yet. embedding:/)).toBeInTheDocument()

    expect(screen.getByText('Database health')).toBeInTheDocument()
    expect(screen.getAllByText('blocked').length).toBeGreaterThan(0)
    expect(screen.getAllByText('ok=false').length).toBeGreaterThan(0)
    expect(screen.getAllByText('unhealthy=milvus').length).toBeGreaterThan(0)
    expect(screen.getAllByText('http_status=401').length).toBeGreaterThan(0)
    expect(screen.queryByText('healthy')).not.toBeInTheDocument()
    expect(screen.queryByText('required checks ok')).not.toBeInTheDocument()
  })
})
