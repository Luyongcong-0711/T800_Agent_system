import { create } from 'zustand'

import type { JobDetailResponse, JobEvent, JobSummary } from '@/api/schemas/workspace'

type StreamStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'error'

interface JobStoreState {
  activeJobId: string | null
  eventsByJobId: Record<string, JobEvent[]>
  jobDetailsById: Record<string, JobDetailResponse>
  jobOrder: string[]
  jobsById: Record<string, JobSummary>
  lastEventIdByJob: Record<string, string>
  streamStatusByJob: Record<string, StreamStatus>
  applyJobEvent: (event: JobEvent) => void
  reset: () => void
  setActiveJobId: (jobId: string | null) => void
  setJobDetail: (job: JobDetailResponse) => void
  setJobEvents: (jobId: string, events: JobEvent[]) => void
  setJobs: (jobs: JobSummary[]) => void
  setStreamStatus: (jobId: string, status: StreamStatus) => void
}

const initialState = {
  activeJobId: null,
  eventsByJobId: {},
  jobDetailsById: {},
  jobOrder: [],
  jobsById: {},
  lastEventIdByJob: {},
  streamStatusByJob: {},
}

export const useJobStore = create<JobStoreState>((set) => ({
  ...initialState,
  applyJobEvent: (event) =>
    set((state) => {
      const currentEvents = state.eventsByJobId[event.job_id] ?? []
      const nextEvents = dedupeEvents([...currentEvents, event])
      const currentJob = state.jobsById[event.job_id]
      const currentDetail = state.jobDetailsById[event.job_id]
      const payloadStatus = typeof event.payload.status === 'string' ? event.payload.status : null
      const payloadStage = typeof event.payload.stage === 'string' ? event.payload.stage : null
      const payloadPercent = numberPayload(event.payload.percent)
      const nextJobsById = currentJob
        ? {
            ...state.jobsById,
            [event.job_id]: {
              ...currentJob,
              current_stage: payloadStage ?? currentJob.current_stage,
              last_event_id: event.event_id,
              last_event_seq: event.event_seq,
              progress_percent: payloadPercent ?? currentJob.progress_percent,
              status: payloadStatus ?? currentJob.status,
              updated_at: event.created_at,
            },
          }
        : state.jobsById
      const nextJobDetailsById = currentDetail
        ? {
            ...state.jobDetailsById,
            [event.job_id]: {
              ...currentDetail,
              current_stage: payloadStage ?? currentDetail.current_stage,
              last_event_id: event.event_id,
              last_event_seq: event.event_seq,
              progress_percent: payloadPercent ?? currentDetail.progress_percent,
              status: payloadStatus ?? currentDetail.status,
              updated_at: event.created_at,
            },
          }
        : state.jobDetailsById

      return {
        eventsByJobId: { ...state.eventsByJobId, [event.job_id]: nextEvents },
        jobDetailsById: nextJobDetailsById,
        jobsById: nextJobsById,
        lastEventIdByJob: { ...state.lastEventIdByJob, [event.job_id]: event.event_id },
      }
    }),
  reset: () => set(initialState),
  setActiveJobId: (jobId) => set({ activeJobId: jobId }),
  setJobDetail: (job) =>
    set((state) => ({
      jobDetailsById: { ...state.jobDetailsById, [job.job_id]: job },
      jobsById: { ...state.jobsById, [job.job_id]: job },
    })),
  setJobEvents: (jobId, events) =>
    set((state) => ({
      eventsByJobId: { ...state.eventsByJobId, [jobId]: dedupeEvents(events) },
      lastEventIdByJob: {
        ...state.lastEventIdByJob,
        [jobId]: events.at(-1)?.event_id ?? state.lastEventIdByJob[jobId] ?? '',
      },
    })),
  setJobs: (jobs) =>
    set((state) => {
      const jobsById = { ...state.jobsById }
      jobs.forEach((job) => {
        jobsById[job.job_id] = job
      })

      return {
        jobOrder: jobs.map((job) => job.job_id),
        jobsById,
      }
    }),
  setStreamStatus: (jobId, status) =>
    set((state) => ({
      streamStatusByJob: { ...state.streamStatusByJob, [jobId]: status },
    })),
}))

function dedupeEvents(events: JobEvent[]) {
  const byId = new Map<string, JobEvent>()
  events.forEach((event) => byId.set(event.event_id, event))
  return [...byId.values()].sort((left, right) => left.event_seq - right.event_seq)
}

function numberPayload(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}
