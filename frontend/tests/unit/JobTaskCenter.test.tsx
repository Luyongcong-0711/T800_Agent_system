import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  JobDetailResponse,
  JobEvent,
  JobWorkerStatusResponse,
  JobSummary,
  ListJobEventsResponse,
  ListJobsResponse,
} from '@/api/schemas/workspace'
import { JobTaskCenter } from '@/components/jobs/JobTaskCenter'
import { useJobStore } from '@/stores/useJobStore'

vi.mock('@/api/agentApiClient', () => ({
  cancelJob: vi.fn(),
  getJob: vi.fn(),
  getJobWorkerStatus: vi.fn(),
  listJobEvents: vi.fn(),
  listJobs: vi.fn(),
  processNextJob: vi.fn(),
  rebuildJobsIndex: vi.fn(),
  recoverStaleJobs: vi.fn(),
  retryJob: vi.fn(),
  startJobWorker: vi.fn(),
  stopJobWorker: vi.fn(),
}))

vi.mock('@/api/jobEventStream', () => ({
  connectJobEventStream: vi.fn(() => ({ close: vi.fn() })),
}))

const api = await import('@/api/agentApiClient')
const jobEventStream = await import('@/api/jobEventStream')

const now = '2026-05-31T00:00:00.000Z'

function job(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    created_at: now,
    current_stage: 'parse_chunk_index',
    job_id: 'job_001',
    job_type: 'document_ingestion_job',
    last_event_id: 'evt_001',
    last_event_seq: 1,
    priority: 'normal',
    progress_percent: 40,
    status: 'running',
    target_scope: { doc_id: 'doc_001', knowledge_base_id: 'kb_default' },
    title: 'Ingest document',
    updated_at: now,
    workspace_id: 'default',
    ...overrides,
  }
}

function detail(overrides: Partial<JobDetailResponse> = {}): JobDetailResponse {
  return {
    ...job(),
    leaf_state: { artifacts: [] },
    manifest: { owner: { runtime_instance_id: 'rt_local' } },
    ...overrides,
  }
}

function event(overrides: Partial<JobEvent> = {}): JobEvent {
  return {
    created_at: now,
    event_id: 'evt_001',
    event_seq: 1,
    job_id: 'job_001',
    payload: { stage: 'parse_chunk_index', status: 'running' },
    type: 'job_started',
    workspace_id: 'default',
    ...overrides,
  }
}

function workerStatus(overrides: Partial<JobWorkerStatusResponse> = {}): JobWorkerStatusResponse {
  return {
    job_types: ['document_ingestion_job'],
    last_error: null,
    last_result: null,
    last_tick_at: null,
    max_jobs_per_tick: 5,
    poll_interval_seconds: 1,
    processed_count: 0,
    running: false,
    started_at: null,
    stopped_at: null,
    tick_count: 0,
    workspace_id: 'default',
    ...overrides,
  }
}

describe('JobTaskCenter', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useJobStore.getState().reset()
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
    window.history.replaceState(null, '', '/jobs')
    vi.mocked(api.getJobWorkerStatus).mockResolvedValue(workerStatus())
    vi.mocked(api.startJobWorker).mockResolvedValue(workerStatus({ running: true }))
    vi.mocked(api.stopJobWorker).mockResolvedValue(workerStatus({ running: false }))
    vi.mocked(api.processNextJob).mockResolvedValue({
      claimed: true,
      job: job({ status: 'succeeded' }),
      workspace_id: 'default',
    })
    vi.mocked(api.recoverStaleJobs).mockResolvedValue({
      recovered_count: 0,
      recovered_jobs: [],
      workspace_id: 'default',
    })
    vi.mocked(api.rebuildJobsIndex).mockResolvedValue({
      rebuilt_count: 0,
      index_object_key: 'workspaces/default/jobs_index.json',
      skipped: [],
      skipped_count: 0,
      workspace_id: 'default',
    })
  })

  it('loads jobs, opens detail drawer, and triggers worker/cancel actions', async () => {
    vi.mocked(api.listJobs).mockResolvedValue({
      jobs: [job()],
      workspace_id: 'default',
    } satisfies ListJobsResponse)
    vi.mocked(api.getJob).mockResolvedValue(detail())
    vi.mocked(api.listJobEvents).mockResolvedValue({
      events: [event()],
      job_id: 'job_001',
      job_status: 'running',
      next_after_event_id: 'evt_001',
      workspace_id: 'default',
    } satisfies ListJobEventsResponse)
    vi.mocked(api.cancelJob).mockResolvedValue({ job_id: 'job_001', status: 'cancelled' })

    render(<JobTaskCenter workspaceId="default" />)

    await screen.findByText('Ingest document')
    await screen.findByText('worker stopped')
    expect(api.getJobWorkerStatus).toHaveBeenCalledWith('default')

    fireEvent.click(screen.getByRole('button', { name: 'Start worker' }))
    await waitFor(() =>
      expect(api.startJobWorker).toHaveBeenCalledWith('default', {
        max_jobs_per_tick: 5,
        poll_interval_ms: 1000,
      }),
    )
    await waitFor(() => expect(api.listJobs).toHaveBeenCalledTimes(2))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Process next/ })).toBeEnabled(),
    )

    fireEvent.click(screen.getByRole('button', { name: /Process next/ }))
    await waitFor(() =>
      expect(api.processNextJob).toHaveBeenCalledWith('default', {}),
    )

    expect(screen.getByText('document_ingestion_job')).toBeInTheDocument()
    expect(screen.getByText('parse_chunk_index')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Ingest document'))

    await screen.findByText('job_001')
    await screen.findByText('job_started')
    expect(api.getJob).toHaveBeenCalledWith('job_001', 'default')
    expect(api.listJobEvents).toHaveBeenCalledWith('job_001', { limit: 200 }, 'default')

    expect(screen.getByRole('button', { name: 'Retry' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel job' }))
    await waitFor(() => expect(api.cancelJob).toHaveBeenCalledWith('job_001', 'default'))
  })

  it('calls stale recovery and index rebuild maintenance actions', async () => {
    vi.mocked(api.listJobs).mockResolvedValue({
      jobs: [],
      workspace_id: 'default',
    } satisfies ListJobsResponse)
    vi.mocked(api.recoverStaleJobs).mockResolvedValue({
      recovered_count: 2,
      recovered_jobs: [job({ job_id: 'job_002', status: 'unknown_outcome' })],
      workspace_id: 'default',
    })
    vi.mocked(api.rebuildJobsIndex).mockResolvedValue({
      rebuilt_count: 3,
      index_object_key: 'workspaces/default/jobs_index.json',
      skipped: [{ manifest_object_key: 'bad/manifest.json', error_type: 'ValueError' }],
      skipped_count: 1,
      workspace_id: 'default',
    })

    render(<JobTaskCenter workspaceId="default" />)

    await screen.findByText('worker stopped')

    fireEvent.click(screen.getByRole('button', { name: 'Recover stale running jobs' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Recover jobs' }))
    await waitFor(() =>
      expect(api.recoverStaleJobs).toHaveBeenCalledWith('default', {
        stale_after_seconds: 60,
      }),
    )
    await screen.findByText('Recovered stale running jobs: 2')

    let rebuildButton!: HTMLElement
    await waitFor(() => {
      rebuildButton = screen.getByRole('button', { name: /Rebuild jobs index/ })
      expect(rebuildButton).not.toHaveClass('ant-btn-loading')
    })
    fireEvent.click(rebuildButton)
    fireEvent.click(await screen.findByRole('button', { name: 'Rebuild index' }))
    await waitFor(() => expect(api.rebuildJobsIndex).toHaveBeenCalledWith('default'))
    await screen.findByText('Rebuilt jobs index: 3, skipped: 1')
  })

  it('shows Recover for unknown_outcome jobs and surfaces the latest recovery event', async () => {
    vi.mocked(api.listJobs).mockResolvedValue({
      jobs: [job({ current_stage: 'recovery', status: 'unknown_outcome' })],
      workspace_id: 'default',
    } satisfies ListJobsResponse)
    vi.mocked(api.getJob).mockResolvedValue(
      detail({ current_stage: 'recovery', status: 'unknown_outcome' }),
    )
    vi.mocked(api.listJobEvents).mockResolvedValue({
      events: [
        event({
          event_id: 'evt_002',
          event_seq: 2,
          payload: {
            error_type: 'stale_running_recovered',
            message: 'Recovered stale running job with unknown outcome.',
            recovered_at: now,
            status: 'unknown_outcome',
          },
          type: 'job_unknown_outcome',
        }),
      ],
      job_id: 'job_001',
      job_status: 'unknown_outcome',
      next_after_event_id: 'evt_002',
      workspace_id: 'default',
    } satisfies ListJobEventsResponse)
    vi.mocked(api.retryJob).mockResolvedValue(detail({ status: 'recovering' }))

    render(<JobTaskCenter workspaceId="default" />)

    await screen.findByText('Ingest document')
    fireEvent.click(screen.getByText('Ingest document'))

    await screen.findByText('job_001')
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Recover' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    expect(screen.getByText('Latest recovery event: job_unknown_outcome')).toBeInTheDocument()
    expect(screen.getByText(/stale_running_recovered/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Recover' }))
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: 'Recover' }).length).toBeGreaterThan(1),
    )
    const recoverButtons = screen.getAllByRole('button', { name: 'Recover' })
    fireEvent.click(recoverButtons[recoverButtons.length - 1])
    await waitFor(() => expect(api.retryJob).toHaveBeenCalledWith('job_001', {}, 'default'))
  })

  it('does not connect the job event stream for partial_success jobs', async () => {
    vi.mocked(api.listJobs).mockResolvedValue({
      jobs: [job({ status: 'partial_success' })],
      workspace_id: 'default',
    } satisfies ListJobsResponse)
    vi.mocked(api.getJob).mockResolvedValue(detail({ status: 'partial_success' }))
    vi.mocked(api.listJobEvents).mockResolvedValue({
      events: [event({ payload: { stage: 'parse_chunk_index', status: 'partial_success' } })],
      job_id: 'job_001',
      job_status: 'partial_success',
      next_after_event_id: 'evt_001',
      workspace_id: 'default',
    } satisfies ListJobEventsResponse)

    render(<JobTaskCenter workspaceId="default" />)

    await screen.findByText('Ingest document')
    fireEvent.click(screen.getByText('Ingest document'))

    await screen.findByText('job_001')
    expect(screen.getByRole('button', { name: 'Retry failed chunks' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    expect(jobEventStream.connectJobEventStream).not.toHaveBeenCalled()
  })

  it('rolls back an invalid job_id deep link instead of leaving stale drawer state', async () => {
    vi.mocked(api.listJobs).mockResolvedValue({
      jobs: [],
      workspace_id: 'default',
    } satisfies ListJobsResponse)
    vi.mocked(api.getJob).mockRejectedValue(new Error('Job not found'))
    vi.mocked(api.listJobEvents).mockResolvedValue({
      events: [],
      job_id: 'missing_job',
      job_status: 'failed',
      workspace_id: 'default',
    } satisfies ListJobEventsResponse)
    window.history.replaceState(null, '', '/jobs?job_id=missing_job')

    render(<JobTaskCenter workspaceId="default" />)

    await waitFor(() => expect(api.getJob).toHaveBeenCalledWith('missing_job', 'default'))
    await screen.findByText('Job not found')
    expect(window.location.search).not.toContain('job_id=')
    expect(screen.queryByText('Job detail')).not.toBeInTheDocument()
  })
})
