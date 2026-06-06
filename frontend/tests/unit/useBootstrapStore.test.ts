import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { BootstrapResponse } from '@/api/schemas/bootstrap'
import { useBootstrapStore } from '@/stores/useBootstrapStore'

vi.mock('@/api/agentApiClient', () => ({
  getBootstrap: vi.fn(),
}))

const { getBootstrap } = await import('@/api/agentApiClient')

function createBootstrap(workspaceId: string): BootstrapResponse {
  return {
    feature_flags: {
      login_enabled: false,
      workspace_switch_enabled: false,
    },
    user: { role: 'owner', user_id: 'default_user' },
    workspace: { workspace_id: workspaceId, workspace_role: 'owner' },
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((innerResolve) => {
    resolve = innerResolve
  })

  return { promise, resolve }
}

describe('useBootstrapStore', () => {
  beforeEach(() => {
    vi.mocked(getBootstrap).mockReset()
    useBootstrapStore.getState().reset()
  })

  it('keeps stale bootstrap responses from overwriting newer state', async () => {
    const slow = deferred<BootstrapResponse>()
    const fast = deferred<BootstrapResponse>()

    vi.mocked(getBootstrap)
      .mockReturnValueOnce(slow.promise)
      .mockReturnValueOnce(fast.promise)

    const firstRequest = useBootstrapStore.getState().loadBootstrap()
    const secondRequest = useBootstrapStore.getState().loadBootstrap()

    fast.resolve(createBootstrap('fresh'))
    await secondRequest

    slow.resolve(createBootstrap('stale'))
    await firstRequest

    expect(useBootstrapStore.getState()).toMatchObject({
      bootstrap: expect.objectContaining({
        workspace: expect.objectContaining({ workspace_id: 'fresh' }),
      }),
      status: 'ready',
    })
  })
})
