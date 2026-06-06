import { describe, expect, it } from 'vitest'

import {
  getWorkspaceSectionFromPathname,
  getWorkspaceSectionPath,
  isWorkspaceSection,
  workspaceSections,
} from '@/components/workspace/routes'

describe('workspace routes', () => {
  it('maps P0 workspace sections to stable routes', () => {
    expect(workspaceSections).toEqual([
      'chat',
      'jobs',
      'knowledge',
      'memory',
      'skills',
      'subagents',
      'mcp',
      'logs',
      'readiness',
      'settings',
    ])
    expect(getWorkspaceSectionPath('chat')).toBe('/')
    expect(getWorkspaceSectionPath('jobs')).toBe('/jobs')
    expect(getWorkspaceSectionPath('subagents')).toBe('/subagents')
    expect(getWorkspaceSectionPath('readiness')).toBe('/readiness')
    expect(getWorkspaceSectionPath('settings')).toBe('/settings')
  })

  it('resolves section from pathname and rejects unknown routes', () => {
    expect(getWorkspaceSectionFromPathname('/')).toBe('chat')
    expect(getWorkspaceSectionFromPathname('/mcp')).toBe('mcp')
    expect(getWorkspaceSectionFromPathname('/subagents')).toBe('subagents')
    expect(getWorkspaceSectionFromPathname('/readiness')).toBe('readiness')
    expect(getWorkspaceSectionFromPathname('/knowledge/document/foo')).toBe('knowledge')
    expect(getWorkspaceSectionFromPathname('/unknown')).toBeNull()
    expect(isWorkspaceSection('logs')).toBe(true)
    expect(isWorkspaceSection('unknown')).toBe(false)
  })
})
