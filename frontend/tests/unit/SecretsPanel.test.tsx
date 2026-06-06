import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ListSecretsResponse, SecretSummary } from '@/api/schemas/workspace'
import { SecretsPanel } from '@/components/settings/SecretsPanel'

vi.mock('@/api/agentApiClient', () => ({
  createSecret: vi.fn(),
  deleteSecret: vi.fn(),
  disableSecret: vi.fn(),
  getSecretReferences: vi.fn(),
  listSecrets: vi.fn(),
  rotateSecret: vi.fn(),
  updateSecret: vi.fn(),
}))

const api = await import('@/api/agentApiClient')

const now = '2026-05-31T00:00:00.000Z'

function secret(overrides: Partial<SecretSummary> = {}): SecretSummary {
  return {
    display_name: 'Main model key',
    last_used_at: null,
    masked: 'sk-****1111',
    secret_id: 'secret_001',
    secret_ref: 'secret_001',
    status: 'active',
    type: 'model_api_key',
    updated_at: now,
    ...overrides,
  }
}

describe('SecretsPanel', () => {
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
    vi.mocked(api.listSecrets).mockResolvedValue({
      secrets: [secret()],
      workspace_id: 'default',
    } satisfies ListSecretsResponse)
    vi.mocked(api.createSecret).mockResolvedValue(
      secret({
        display_name: 'Embedding key',
        masked: 'emb****2222',
        secret_id: 'secret_002',
        secret_ref: 'secret_002',
        type: 'embedding_api_key',
      }),
    )
    vi.mocked(api.updateSecret).mockResolvedValue(
      secret({ display_name: 'Renamed model key' }),
    )
    vi.mocked(api.disableSecret).mockResolvedValue(secret({ status: 'disabled' }))
    vi.mocked(api.rotateSecret).mockResolvedValue(secret({ masked: 'sk-****3333' }))
    vi.mocked(api.deleteSecret).mockResolvedValue(secret({ status: 'soft_deleted' }))
    vi.mocked(api.getSecretReferences).mockResolvedValue({
      references: [{ config_id: 'main_chat', source: 'model_config' }],
      secret_id: 'secret_001',
    })
  })

  it('manages secrets without rendering plaintext after submit', async () => {
    render(<SecretsPanel workspaceId="default" />)

    await screen.findByText('Main model key')
    expect(api.listSecrets).toHaveBeenCalledWith('default')

    fireEvent.change(screen.getByLabelText('Name'), {
      target: { value: 'Embedding key' },
    })
    fireEvent.change(screen.getByTestId('secret-plaintext'), {
      target: { value: 'emb-new-secret-2222' },
    })
    fireEvent.click(screen.getByTestId('secret-create'))

    await waitFor(() =>
      expect(api.createSecret).toHaveBeenCalledWith(
        expect.objectContaining({
          display_name: 'Embedding key',
          plaintext: 'emb-new-secret-2222',
          type: 'model_api_key',
        }),
        'default',
      ),
    )
    await screen.findByText('Embedding key')
    expect(screen.queryByDisplayValue('emb-new-secret-2222')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('secret-select-secret_001'))
    fireEvent.change(screen.getByTestId('secret-rename-input'), {
      target: { value: 'Renamed model key' },
    })
    fireEvent.click(screen.getByTestId('secret-rename'))
    await waitFor(() =>
      expect(api.updateSecret).toHaveBeenCalledWith(
        'secret_001',
        { display_name: 'Renamed model key' },
        'default',
      ),
    )

    fireEvent.change(screen.getByTestId('secret-rotate-plaintext'), {
      target: { value: 'sk-rotated-secret-3333' },
    })
    fireEvent.click(screen.getByTestId('secret-rotate'))
    fireEvent.click(await screen.findByRole('button', { name: 'Rotate secret' }))
    await waitFor(() =>
      expect(api.rotateSecret).toHaveBeenCalledWith(
        'secret_001',
        { plaintext: 'sk-rotated-secret-3333' },
        'default',
      ),
    )
    expect(screen.queryByDisplayValue('sk-rotated-secret-3333')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('secret-references-secret_001'))
    await screen.findByText('config_id=main_chat source=model_config')

    fireEvent.click(screen.getByTestId('secret-toggle-secret_001'))
    fireEvent.click(await screen.findByRole('button', { name: 'Disable secret' }))
    await waitFor(() => expect(api.disableSecret).toHaveBeenCalledWith('secret_001', 'default'))

    fireEvent.click(screen.getByTestId('secret-delete-secret_001'))
    fireEvent.click(await screen.findByRole('button', { name: 'Delete secret' }))
    await waitFor(() => expect(api.deleteSecret).toHaveBeenCalledWith('secret_001', 'default'))
  }, 15000)
})
