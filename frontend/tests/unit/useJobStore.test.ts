import { beforeEach, describe, expect, it } from 'vitest'

import type { JobEvent, JobSummary } from '@/api/schemas/workspace'
import { useJobStore } from '@/stores/useJobStore'

const now = '2026-05-31T00:00:00.000Z'

function job(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    created_at: now,
    current_stage: 'queued',
    job_id: 'job_001',
    job_type: 'document_ingestion_job',
    last_event_id: null,
    last_event_seq: 0,
    priority: 'normal',
    progress_percent: 0,
    status: 'queued',
    target_scope: { doc_id: 'doc_001', scope_type: 'document' },
    title: 'Ingest document',
    updated_at: now,
    workspace_id: 'default',
    ...overrides,
  }
}

function event(overrides: Partial<JobEvent> = {}): JobEvent {
  return {
    created_at: now,
    event_id: 'evt_001',
    event_seq: 1,
    job_id: 'job_001',
    payload: { stage: 'claimed', status: 'running' },
    type: 'job_started',
    workspace_id: 'default',
    ...overrides,
  }
}

describe('useJobStore', () => {
  beforeEach(() => {
    useJobStore.getState().reset()
  })

  it('indexes jobs and keeps the active job stable', () => {
    useJobStore.getState().setJobs([job(), job({ job_id: 'job_002', title: 'Refresh MCP' })])
    useJobStore.getState().setActiveJobId('job_002')

    expect(useJobStore.getState().jobOrder).toEqual(['job_001', 'job_002'])
    expect(useJobStore.getState().jobsById.job_002.title).toBe('Refresh MCP')
    expect(useJobStore.getState().activeJobId).toBe('job_002')
  })

  it('applies job events, updates status and deduplicates replayed events', () => {
    useJobStore.getState().setJobs([job()])

    useJobStore.getState().applyJobEvent(event())
    useJobStore.getState().applyJobEvent(event())
    useJobStore.getState().applyJobEvent(
      event({
        event_id: 'evt_002',
        event_seq: 2,
        payload: { percent: 100, stage: 'indexed', status: 'succeeded' },
        type: 'job_succeeded',
      }),
    )

    const state = useJobStore.getState()
    expect(state.eventsByJobId.job_001).toHaveLength(2)
    expect(state.jobsById.job_001.status).toBe('succeeded')
    expect(state.jobsById.job_001.current_stage).toBe('indexed')
    expect(state.jobsById.job_001.progress_percent).toBe(100)
    expect(state.lastEventIdByJob.job_001).toBe('evt_002')
  })
})
