import type { JobEvent, PageParams, WorkspaceId } from '@/api/schemas/workspace'
import { getJobEventsStreamUrl } from '@/api/agentApiClient'

export interface SseFrame {
  id?: string
  event?: string
  data: string
}

export interface ParsedSseFrames {
  frames: SseFrame[]
  remaining: string
}

export interface JobEventStreamOptions {
  jobId: string
  workspaceId?: WorkspaceId
  afterEventId?: string | null
  onEvent: (event: JobEvent) => void
  onClose?: () => void
  onError?: (error: Error) => void
}

export interface JobEventStreamConnection {
  close: () => void
}

export function parseSseFrames(buffer: string): ParsedSseFrames {
  const normalized = buffer.replace(/\r\n/g, '\n')
  const parts = normalized.split('\n\n')
  const remaining = parts.pop() ?? ''
  const frames = parts
    .map(parseSseFrame)
    .filter((frame): frame is SseFrame => frame !== null)

  return { frames, remaining }
}

export function connectJobEventStream(options: JobEventStreamOptions): JobEventStreamConnection {
  const controller = new AbortController()
  void readJobEventStream(options, controller)

  return {
    close: () => controller.abort(),
  }
}

function parseSseFrame(raw: string): SseFrame | null {
  const frame: SseFrame = { data: '' }

  raw.split('\n').forEach((line) => {
    if (!line || line.startsWith(':')) {
      return
    }
    const separator = line.indexOf(':')
    const field = separator === -1 ? line : line.slice(0, separator)
    const value = separator === -1 ? '' : line.slice(separator + 1).replace(/^ /, '')

    if (field === 'id') {
      frame.id = value
    }
    if (field === 'event') {
      frame.event = value
    }
    if (field === 'data') {
      frame.data = frame.data ? `${frame.data}\n${value}` : value
    }
  })

  return frame.data ? frame : null
}

async function readJobEventStream(
  options: JobEventStreamOptions,
  controller: AbortController,
) {
  try {
    const params: PageParams = options.afterEventId
      ? { after_event_id: options.afterEventId }
      : {}
    const response = await fetch(
      getJobEventsStreamUrl(options.jobId, params, options.workspaceId ?? 'default'),
      {
        headers: { Accept: 'text/event-stream' },
        signal: controller.signal,
      },
    )

    if (!response.ok || !response.body) {
      throw new Error(`Job event stream failed with status ${response.status}`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffered = ''

    while (!controller.signal.aborted) {
      const { done, value } = await reader.read()
      if (done) {
        break
      }
      buffered += decoder.decode(value, { stream: true })
      const parsed = parseSseFrames(buffered)
      buffered = parsed.remaining
      parsed.frames.forEach((frame) => emitFrame(frame, options))
    }

    buffered += decoder.decode()
    parseSseFrames(`${buffered}\n\n`).frames.forEach((frame) => emitFrame(frame, options))
    options.onClose?.()
  } catch (error) {
    if (controller.signal.aborted) {
      options.onClose?.()
      return
    }
    options.onError?.(error instanceof Error ? error : new Error(String(error)))
  }
}

function emitFrame(frame: SseFrame, options: JobEventStreamOptions) {
  const event = JSON.parse(frame.data) as JobEvent
  options.onEvent(event)
  if (event.type === 'stream_closed') {
    options.onClose?.()
  }
}
