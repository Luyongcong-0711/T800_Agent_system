import { create } from 'zustand'

import { getBootstrap } from '@/api/agentApiClient'
import type { BootstrapResponse } from '@/api/schemas/bootstrap'

export type BootstrapStatus = 'idle' | 'loading' | 'ready' | 'error'

interface BootstrapState {
  bootstrap: BootstrapResponse | null
  errorMessage: string | null
  requestId: number
  status: BootstrapStatus
  loadBootstrap: () => Promise<void>
  reset: () => void
}

let requestSequence = 0

export function createInitialBootstrapState() {
  return {
    bootstrap: null,
    errorMessage: null,
    requestId: 0,
    status: 'idle' as BootstrapStatus,
  }
}

export const useBootstrapStore = create<BootstrapState>((set, get) => ({
  ...createInitialBootstrapState(),
  loadBootstrap: async () => {
    const requestId = requestSequence + 1
    requestSequence = requestId

    set({ errorMessage: null, requestId, status: 'loading' })

    try {
      const bootstrap = await getBootstrap()

      if (get().requestId !== requestId) {
        return
      }

      set({ bootstrap, status: 'ready' })
    } catch (error) {
      if (get().requestId !== requestId) {
        return
      }

      const errorMessage =
        error instanceof Error ? error.message : 'Unable to load workspace.'

      set({ errorMessage, status: 'error' })
    }
  },
  reset: () => {
    requestSequence = 0
    set(createInitialBootstrapState())
  },
}))
