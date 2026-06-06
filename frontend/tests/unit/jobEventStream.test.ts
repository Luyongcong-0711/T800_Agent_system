import { describe, expect, it, vi } from 'vitest'

import type { JobEvent } from '@/api/schemas/workspace'
import { connectJobEventStream, parseSseFrames } from '@/api/jobEventStream'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((innerResolve) => {
    resolve = innerResolve
  })

  return { promise, resolve }
}

function encode(value: string) {
  return new TextEncoder().encode(value)
}

describe('jobEventStream', () => {
  it('parses complete SSE frames and preserves trailing partial data', () => {
    const parsed = parseSseFrames(
      [
        ': keepalive',
        '',
        'id: evt_1',
        'event: job_started',
        'data: {"event_id":"evt_1","type":"job_started"}',
        '',
        'id: evt_2',
      ].join('\n'),
    )

    expect(parsed.frames).toEqual([
      {
        data: '{"event_id":"evt_1","type":"job_started"}',
        event: 'job_started',
        id: 'evt_1',
      },
    ])
    expect(parsed.remaining).toBe('id: evt_2')
  })

  it('connects with fetch streaming and emits parsed Job events', async () => {
    const closed = deferred<void>()
    const events: JobEvent[] = []
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encode(
            `${[
              'id: evt_1',
              'event: job_started',
              'data: {"created_at":"now","event_id":"evt_1","event_seq":1,"job_id":"job_001","payload":{"status":"running","stage":"claimed"},"type":"job_started","workspace_id":"default"}',
            ].join('\n')}\n\n`,
          ),
        )
        controller.enqueue(
          encode(
            `${[
              'id: evt_2',
              'event: stream_closed',
              'data: {"created_at":"now","event_id":"evt_2","event_seq":2,"job_id":"job_001","payload":{"status":"succeeded"},"type":"stream_closed","workspace_id":"default"}',
            ].join('\n')}\n\n`,
          ),
        )
        controller.close()
      },
    })
    const fetchMock = vi.fn().mockResolvedValue({
      body: stream,
      ok: true,
      status: 200,
    })
    vi.stubGlobal('fetch', fetchMock)

    connectJobEventStream({
      afterEventId: 'evt_0',
      jobId: 'job_001',
      onClose: () => closed.resolve(),
      onEvent: (event) => events.push(event),
      workspaceId: 'default',
    })

    await closed.promise

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/workspaces/default/jobs/job_001/events/stream?after_event_id=evt_0',
      expect.objectContaining({
        headers: expect.objectContaining({ Accept: 'text/event-stream' }),
      }),
    )
    expect(events.map((event) => event.type)).toEqual(['job_started', 'stream_closed'])
  })
})
