import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ListModelConfigsResponse,
  ListSecretsResponse,
  ModelConfigResponse,
  SecretSummary,
} from '@/api/schemas/workspace'
import { ModelConfigSettings } from '@/components/settings/ModelConfigSettings'

vi.mock('@/api/agentApiClient', () => ({
  listModelConfigs: vi.fn(),
  listSecrets: vi.fn(),
  testModelConfig: vi.fn(),
  updateModelConfig: vi.fn(),
}))

const api = await import('@/api/agentApiClient')

const now = '2026-05-31T00:00:00.000Z'

function modelConfig(overrides: Partial<ModelConfigResponse> = {}): ModelConfigResponse {
  return {
    api_key_ref: null,
    base_url: 'https://token-plan-cn.xiaomimimo.com/v1',
    config_id: 'main_chat',
    context_window_tokens: 200000,
    display_name: 'Main chat',
    enabled: true,
    max_output_tokens: 8192,
    model: 'mimo-v2.5-pro',
    provider: 'openai_compatible',
    purpose: 'chat',
    revision: 1,
    schema_version: 1,
    source: 'default_env',
    status: 'missing_secret',
    supports_tool_calling: true,
    timeout_ms: 60000,
    updated_at: now,
    workspace_id: 'default',
    ...overrides,
  }
}

function secret(overrides: Partial<SecretSummary> = {}): SecretSummary {
  return {
    display_name: 'Main model key',
    last_used_at: null,
    masked: 'sk-****1111',
    secret_id: 'secret_main',
    secret_ref: 'secret_main',
    status: 'active',
    type: 'model_api_key',
    updated_at: now,
    ...overrides,
  }
}

describe('ModelConfigSettings', () => {
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
    vi.mocked(api.listModelConfigs).mockResolvedValue({
      configs: [modelConfig()],
      workspace_id: 'default',
    } satisfies ListModelConfigsResponse)
    vi.mocked(api.listSecrets).mockResolvedValue({
      secrets: [secret(), secret({ secret_id: 'disabled_key', status: 'disabled' })],
      workspace_id: 'default',
    } satisfies ListSecretsResponse)
    vi.mocked(api.updateModelConfig).mockResolvedValue(
      modelConfig({ api_key_ref: 'secret_main', source: 'stored', status: 'configured' }),
    )
    vi.mocked(api.testModelConfig).mockResolvedValue({
      config_id: 'main_chat',
      latency_ms: 12,
      model: 'mimo-v2.5-pro',
      ok: true,
      provider: 'openai_compatible',
      redacted: true,
      retryable: false,
      workspace_id: 'default',
    })
  })

  it('selects an active model API secret without exposing plaintext', async () => {
    render(<ModelConfigSettings workspaceId="default" />)

    await waitFor(() => expect(api.listModelConfigs).toHaveBeenCalledWith('default'))
    expect(api.listSecrets).toHaveBeenCalledWith('default')
    expect(await screen.findAllByText('default env')).not.toHaveLength(0)

    openSelect('model-api-key-secret-ref')
    fireEvent.click(await screen.findByText(/Main model key/))
    fireEvent.click(screen.getByText('Save'))
    fireEvent.click(await screen.findByRole('button', { name: 'Save model config' }))

    await waitFor(() =>
      expect(api.updateModelConfig).toHaveBeenCalledWith(
        'main_chat',
        expect.objectContaining({ api_key_ref: 'secret_main' }),
        'default',
      ),
    )
    expect(await screen.findAllByText('saved')).not.toHaveLength(0)
    fireEvent.click(screen.getByText('Test'))
    await waitFor(() =>
      expect(api.testModelConfig).toHaveBeenCalledWith(
        'main_chat',
        expect.objectContaining({
          config: expect.objectContaining({
            api_key_ref: 'secret_main',
            model: 'mimo-v2.5-pro',
          }),
          max_output_tokens: 16,
          prompt: 'Reply with pong.',
        }),
        'default',
      ),
    )
    expect(screen.queryByText('disabled_key')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain('sk-test-secret')
  })

  it('uses embedding-specific secret type and smoke prompt', async () => {
    vi.mocked(api.listModelConfigs).mockResolvedValue({
      configs: [
        modelConfig(),
        modelConfig({
          api_key_ref: null,
          config_id: 'embedding',
          display_name: 'Embedding',
          model: 'text-embedding-v4',
          purpose: 'embedding',
          status: 'missing_secret',
          supports_tool_calling: false,
        }),
      ],
      workspace_id: 'default',
    } satisfies ListModelConfigsResponse)
    vi.mocked(api.listSecrets).mockResolvedValue({
      secrets: [
        secret(),
        secret({
          display_name: 'Embedding key',
          secret_id: 'secret_embedding',
          secret_ref: 'secret_embedding',
          type: 'embedding_api_key',
        }),
      ],
      workspace_id: 'default',
    } satisfies ListSecretsResponse)
    vi.mocked(api.testModelConfig).mockResolvedValue({
      config_id: 'embedding',
      content_preview: 'embedding_dimension=1024',
      latency_ms: 18,
      model: 'text-embedding-v4',
      ok: true,
      provider: 'openai_compatible',
      redacted: true,
      retryable: false,
      workspace_id: 'default',
    })

    render(<ModelConfigSettings workspaceId="default" />)

    fireEvent.click(await screen.findByText('Embedding'))
    openSelect('model-api-key-secret-ref')
    fireEvent.click(await screen.findByText(/Embedding key/))
    fireEvent.click(screen.getByText('Test'))

    await waitFor(() =>
      expect(api.testModelConfig).toHaveBeenCalledWith(
        'embedding',
        expect.objectContaining({
          config: expect.objectContaining({
            api_key_ref: 'secret_embedding',
            supports_tool_calling: false,
          }),
          max_output_tokens: 16,
          prompt: 'embedding smoke test',
        }),
        'default',
      ),
    )
  })
})

function openSelect(testId: string) {
  const select = screen.getByTestId(testId)
  fireEvent.mouseDown(select.querySelector('.ant-select-selector') ?? select)
}
