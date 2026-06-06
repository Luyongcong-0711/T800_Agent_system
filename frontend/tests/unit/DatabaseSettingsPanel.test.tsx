import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  DatabaseConfigResponse,
  DatabaseHealthSnapshotResponse,
  DatabaseTargetConfig,
  ListSecretsResponse,
  SecretSummary,
} from '@/api/schemas/workspace'
import { DatabaseSettingsPanel } from '@/components/settings/DatabaseSettingsPanel'

vi.mock('@/api/agentApiClient', () => ({
  checkDatabaseHealth: vi.fn(),
  getDatabaseConfig: vi.fn(),
  getDatabaseHealth: vi.fn(),
  listSecrets: vi.fn(),
  updateDatabaseConfig: vi.fn(),
}))

const api = await import('@/api/agentApiClient')

const now = '2026-05-31T00:00:00.000Z'

function target(
  name: DatabaseTargetConfig['target'],
  overrides: Partial<DatabaseTargetConfig> = {},
): DatabaseTargetConfig {
  const defaults: Record<DatabaseTargetConfig['target'], string> = {
    milvus: 'http://localhost:19530',
    minio: 'http://localhost:9000',
    neo4j: 'bolt://localhost:7687',
    redis: 'redis://localhost:6379/0',
  }
  return {
    bucket: name === 'minio' ? 'agent-system' : null,
    credential_refs: {},
    enabled: true,
    endpoint: defaults[name],
    mode: 'local',
    options: name === 'redis' ? { role: 'cache_only' } : {},
    target: name,
    tls: false,
    ...overrides,
  }
}

function config(overrides: Partial<DatabaseConfigResponse> = {}): DatabaseConfigResponse {
  return {
    revision: 1,
    targets: [target('minio'), target('milvus'), target('neo4j'), target('redis')],
    updated_at: now,
    workspace_id: 'default',
    ...overrides,
  }
}

function health(overrides: Partial<DatabaseHealthSnapshotResponse> = {}): DatabaseHealthSnapshotResponse {
  return {
    checked_at: now,
    ok: true,
    services: [
      {
        checked_at: now,
        latency_ms: 1,
        message: 'ok',
        status: 'healthy',
        target: 'minio',
      },
      {
        checked_at: now,
        latency_ms: 1,
        message: 'ok',
        status: 'healthy',
        target: 'milvus',
      },
      {
        checked_at: now,
        latency_ms: 1,
        message: 'ok',
        status: 'healthy',
        target: 'neo4j',
      },
      {
        checked_at: now,
        latency_ms: 1,
        message: 'ok',
        status: 'healthy',
        target: 'redis',
      },
    ],
    source: 'live_check',
    workspace_id: 'default',
    ...overrides,
  }
}

function secret(overrides: Partial<SecretSummary> = {}): SecretSummary {
  return {
    display_name: 'MinIO access key',
    last_used_at: null,
    masked: 'ak-****1111',
    secret_id: 'minio_access',
    secret_ref: 'minio_access',
    status: 'active',
    type: 'minio_access_key',
    updated_at: now,
    ...overrides,
  }
}

describe('DatabaseSettingsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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
    vi.mocked(api.getDatabaseConfig).mockResolvedValue(config())
    vi.mocked(api.getDatabaseHealth).mockResolvedValue(health({ source: 'unknown' }))
    vi.mocked(api.listSecrets).mockResolvedValue({
      secrets: [
        secret(),
        secret({
          display_name: 'MinIO secret key',
          masked: 'sk-****2222',
          secret_id: 'minio_secret',
          secret_ref: 'minio_secret',
          type: 'minio_secret_key',
        }),
        secret({
          display_name: 'Disabled Milvus token',
          secret_id: 'milvus_disabled',
          secret_ref: 'milvus_disabled',
          status: 'disabled',
          type: 'milvus_token',
        }),
      ],
      workspace_id: 'default',
    } satisfies ListSecretsResponse)
    vi.mocked(api.updateDatabaseConfig).mockResolvedValue(
      config({
        revision: 2,
        targets: [
          target('minio', {
            credential_refs: { primary: 'secret_ref://minio-primary' },
            endpoint: 'https://minio.example.com',
            mode: 'remote',
            tls: true,
          }),
          target('milvus'),
          target('neo4j'),
          target('redis'),
        ],
      }),
    )
    vi.mocked(api.checkDatabaseHealth).mockResolvedValue(health())
  })

  it('loads database config, saves remote secret refs, and runs health checks', async () => {
    render(<DatabaseSettingsPanel workspaceId="default" />)

    await waitFor(() => expect(api.getDatabaseConfig).toHaveBeenCalledWith('default'))
    expect(api.getDatabaseConfig).toHaveBeenCalledWith('default')
    expect(api.getDatabaseHealth).toHaveBeenCalledWith('default')
    expect(api.listSecrets).toHaveBeenCalledWith('default')
    expect(screen.getByText('cache_only')).toBeInTheDocument()

    fireEvent.change(screen.getByTestId('database-endpoint-minio'), {
      target: { value: 'https://minio.example.com' },
    })
    fireEvent.change(screen.getByTestId('database-credential-refs-minio'), {
      target: { value: '{"primary":"secret_ref://minio-primary"}' },
    })
    fireEvent.click(screen.getByTestId('database-save'))
    expect(api.updateDatabaseConfig).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByRole('button', { name: 'Save database config' }))

    await waitFor(() =>
      expect(api.updateDatabaseConfig).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({
            credential_refs: { primary: 'secret_ref://minio-primary' },
            endpoint: 'https://minio.example.com',
            target: 'minio',
          }),
        ]),
        'default',
      ),
    )
    await screen.findByText('Database config saved. Revision 2.')

    fireEvent.click(screen.getByTestId('database-health-check'))
    await waitFor(() => expect(api.checkDatabaseHealth).toHaveBeenCalledWith('default'))
    expect(screen.getAllByText('healthy').length).toBeGreaterThan(0)
  }, 20000)

  it('keeps invalid JSON drafts visible and blocks save', async () => {
    render(<DatabaseSettingsPanel workspaceId="default" />)

    await waitFor(() => expect(api.getDatabaseConfig).toHaveBeenCalledWith('default'))

    fireEvent.change(screen.getByTestId('database-options-minio'), {
      target: { value: '{' },
    })

    expect(screen.getByTestId('database-options-minio')).toHaveValue('{')
    expect(screen.getByText('minio options must be valid JSON.')).toBeInTheDocument()
    expect(screen.getByTestId('database-save')).toBeDisabled()
    expect(api.updateDatabaseConfig).not.toHaveBeenCalled()
  })

  it('rejects plaintext credential refs before saving', async () => {
    render(<DatabaseSettingsPanel workspaceId="default" />)

    await waitFor(() => expect(api.getDatabaseConfig).toHaveBeenCalledWith('default'))

    fireEvent.change(screen.getByTestId('database-credential-refs-minio'), {
      target: { value: '{"primary":"raw-token-value"}' },
    })

    expect(
      screen.getByText('minio credential_refs.primary must use secret_ref://...'),
    ).toBeInTheDocument()
    expect(screen.getByTestId('database-save')).toBeDisabled()
    expect(api.updateDatabaseConfig).not.toHaveBeenCalled()
  })

  it('rejects redis role overrides before saving', async () => {
    render(<DatabaseSettingsPanel workspaceId="default" />)

    await waitFor(() => expect(api.getDatabaseConfig).toHaveBeenCalledWith('default'))

    fireEvent.change(screen.getByTestId('database-options-redis'), {
      target: { value: '{"role":"queue"}' },
    })

    expect(screen.getByText('redis options.role is fixed to cache_only.')).toBeInTheDocument()
    expect(screen.getByTestId('database-save')).toBeDisabled()
    expect(api.updateDatabaseConfig).not.toHaveBeenCalled()
  })

  it('writes selected database secrets into credential_refs without plaintext', async () => {
    render(<DatabaseSettingsPanel workspaceId="default" />)

    await waitFor(() => expect(api.getDatabaseConfig).toHaveBeenCalledWith('default'))

    openSelect('database-secret-minio-access_key')
    fireEvent.click(await screen.findByText(/MinIO access key/))

    openSelect('database-secret-minio-secret_key')
    fireEvent.click(await screen.findByText(/MinIO secret key/))

    fireEvent.click(screen.getByTestId('database-save'))
    fireEvent.click(await screen.findByRole('button', { name: 'Save database config' }))

    await waitFor(() =>
      expect(api.updateDatabaseConfig).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({
            credential_refs: {
              access_key: 'secret_ref://minio_access',
              secret_key: 'secret_ref://minio_secret',
            },
            target: 'minio',
          }),
        ]),
        'default',
      ),
    )
    expect(document.body.textContent).not.toContain('sk-test-secret')
    expect(screen.queryByText('Disabled Milvus token')).not.toBeInTheDocument()
  })
})

function openSelect(testId: string) {
  const select = screen.getByTestId(testId)
  fireEvent.mouseDown(select.querySelector('.ant-select-selector') ?? select)
}
