import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  DiagnosticBundleResponse,
  LogArchiveJobResponse,
  LogQueryResponse,
  LogSummaryResponse,
} from '@/api/schemas/workspace'
import { SystemLogsPanel } from '@/components/logs/SystemLogsPanel'

vi.mock('@/api/agentApiClient', () => ({
  createDiagnosticBundle: vi.fn(),
  createLogArchiveJob: vi.fn(),
  getSystemLogSummary: vi.fn(),
  getSystemLogs: vi.fn(),
}))

const api = await import('@/api/agentApiClient')

describe('SystemLogsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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

  it('loads redacted logs and queues diagnostic/archive jobs', async () => {
    vi.mocked(api.getSystemLogs).mockResolvedValue({
      items: [
        {
          component: 'api',
          event_type: 'request_completed',
          message: 'GET /workspaces/default/jobs',
          redacted: true,
          severity: 'INFO',
          timestamp: '2026-05-31T00:00:00.000Z',
          trace_id: 'trace_001',
        },
      ],
      redacted: true,
      truncated: false,
      workspace_id: 'default',
    } satisfies LogQueryResponse)
    vi.mocked(api.getSystemLogSummary).mockResolvedValue({
      items: ['2026-05-31 INFO api request_completed trace=trace_001'],
      redacted: true,
      truncated: false,
      workspace_id: 'default',
    } satisfies LogSummaryResponse)
    vi.mocked(api.createDiagnosticBundle).mockResolvedValue({
      bundle_id: 'diag_001',
      job_id: 'job_diag_001',
      job_status: 'queued',
      manifest_object_key: null,
      object_key: null,
      redacted: true,
      workspace_id: 'default',
    } satisfies DiagnosticBundleResponse)
    vi.mocked(api.createLogArchiveJob).mockResolvedValue({
      date: '2026-05-31',
      job_id: 'job_archive_001',
      job_status: 'queued',
      manifest_object_key: null,
      redacted: true,
      related_job_id: 'job_archive_001',
      runtime_instance_id: 'rt_local',
      workspace_id: 'default',
    } satisfies LogArchiveJobResponse)

    render(<SystemLogsPanel workspaceId="default" />)

    await screen.findByText('GET /workspaces/default/jobs')
    expect(api.getSystemLogs).toHaveBeenCalledWith('default', 'full', { limit: 100 })

    fireEvent.change(screen.getByPlaceholderText('trace_id / run_id / text'), {
      target: { value: 'trace_001' },
    })
    await waitFor(() =>
      expect(api.getSystemLogs).toHaveBeenLastCalledWith('default', 'full', {
        limit: 100,
        query: 'trace_001',
      }),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Diagnostic bundle' }))
    await waitFor(() =>
      expect(api.createDiagnosticBundle).toHaveBeenCalledWith(
        { components: ['api'], request_id: 'diag-request-001', trace_id: 'trace_001' },
        'default',
      ),
    )
    await screen.findByText('job_diag_001')

    fireEvent.click(screen.getByRole('button', { name: 'Archive logs' }))
    await waitFor(() =>
      expect(api.createLogArchiveJob).toHaveBeenCalledWith(
        { request_id: 'log-archive-request-001' },
        'default',
      ),
    )
    await screen.findByText('job_archive_001')
  })
})
