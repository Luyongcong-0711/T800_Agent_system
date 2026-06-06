import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { BootstrapResponse } from '@/api/schemas/bootstrap'
import { WorkspaceShell } from '@/components/workspace/WorkspaceShell'
import { useBootstrapStore } from '@/stores/useBootstrapStore'
import { useUiPreferencesStore } from '@/stores/useUiPreferencesStore'

const push = vi.fn()
let pathname = '/settings'

vi.mock('next/navigation', () => ({
  usePathname: () => pathname,
  useRouter: () => ({ push }),
}))

vi.mock('@/api/agentApiClient', () => ({
  getAgentApiBaseUrl: vi.fn(() => 'http://api.test'),
  getBootstrap: vi.fn(),
}))

vi.mock('@/components/chat/ChatPanel', () => ({
  ChatPanel: () => <div>chat panel</div>,
}))

vi.mock('@/components/jobs/JobTaskCenter', () => ({
  JobTaskCenter: () => <div>jobs panel</div>,
}))

vi.mock('@/components/knowledge/KnowledgePanel', () => ({
  KnowledgePanel: () => <div>knowledge panel</div>,
}))

vi.mock('@/components/logs/SystemLogsPanel', () => ({
  SystemLogsPanel: () => <div>logs panel</div>,
}))

vi.mock('@/components/mcp/McpToolsPanel', () => ({
  McpToolsPanel: () => <div>mcp panel</div>,
}))

vi.mock('@/components/memory/MemoryPanel', () => ({
  MemoryPanel: () => <div>memory panel</div>,
}))

vi.mock('@/components/readiness/P0ReadinessPanel', () => ({
  P0ReadinessPanel: () => <div>readiness panel</div>,
}))

vi.mock('@/components/settings/SettingsPanel', () => ({
  SettingsPanel: () => <div>settings panel</div>,
}))

vi.mock('@/components/skills/SkillsPanel', () => ({
  SkillsPanel: () => <div>skills panel</div>,
}))

vi.mock('@/components/subagents/SubAgentsPanel', () => ({
  SubAgentsPanel: () => <div>subagents panel</div>,
}))

function bootstrap(): BootstrapResponse {
  return {
    feature_flags: {
      login_enabled: false,
      workspace_switch_enabled: false,
    },
    user: { role: 'owner', user_id: 'default_user' },
    workspace: { workspace_id: 'default', workspace_role: 'owner' },
  }
}

describe('WorkspaceShell route navigation', () => {
  beforeEach(() => {
    push.mockReset()
    pathname = '/settings'
    window.localStorage.clear()
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
    useBootstrapStore.getState().reset()
    useBootstrapStore.setState({
      bootstrap: bootstrap(),
      errorMessage: null,
      status: 'ready',
    })
    useUiPreferencesStore.setState({
      language: 'en',
      themeMode: 'light',
    })
  })

  it('opens the routed section and pushes URL changes from the side navigation', async () => {
    render(<WorkspaceShell initialSection="settings" />)

    expect(screen.getByText('settings panel')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('menuitem', { name: 'Jobs' }))

    await waitFor(() => expect(push).toHaveBeenCalledWith('/jobs'))
    expect(screen.getByText('jobs panel')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('menuitem', { name: 'SubAgents' }))

    await waitFor(() => expect(push).toHaveBeenCalledWith('/subagents'))
    expect(screen.getByText('subagents panel')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('menuitem', { name: 'P0 Readiness' }))

    await waitFor(() => expect(push).toHaveBeenCalledWith('/readiness'))
    expect(screen.getByText('readiness panel')).toBeInTheDocument()
  })

  it('renders top-right theme and language controls and switches workspace copy', async () => {
    render(<WorkspaceShell initialSection="settings" />)

    expect(screen.getByLabelText('Theme')).toBeInTheDocument()
    expect(screen.getByLabelText('Language')).toBeInTheDocument()
    expect(screen.getByText('Light')).toBeInTheDocument()
    expect(screen.getByText('Dark')).toBeInTheDocument()
    expect(screen.getByText('EN')).toBeInTheDocument()
    expect(screen.getByText('ZH')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Dark'))
    await waitFor(() => {
      expect(useUiPreferencesStore.getState().themeMode).toBe('dark')
    })

    fireEvent.click(screen.getByText('ZH'))
    await waitFor(() => {
      expect(useUiPreferencesStore.getState().language).toBe('zh')
    })
    expect(screen.getByRole('menuitem', { name: 'Job' })).toBeInTheDocument()
    expect(screen.getByText('深色')).toBeInTheDocument()
    expect(screen.getByText('浅色')).toBeInTheDocument()
  })
})
