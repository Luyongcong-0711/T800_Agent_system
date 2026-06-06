import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  JobSummary,
  McpReconnectResponse,
  McpRefreshResponse,
  McpServerDetailResponse,
  McpServerHealthResponse,
  McpServersResponse,
  McpToolsResponse,
  ListSecretsResponse,
  SecretSummary,
  ToolInventoryResponse,
} from '@/api/schemas/workspace'
import { McpToolsPanel } from '@/components/mcp/McpToolsPanel'

vi.mock('@/api/agentApiClient', () => ({
  getMcpServer: vi.fn(),
  getMcpServerHealth: vi.fn(),
  getToolInventory: vi.fn(),
  listSecrets: vi.fn(),
  listMcpServers: vi.fn(),
  listMcpTools: vi.fn(),
  reconnectMcpServer: vi.fn(),
  refreshMcpServer: vi.fn(),
  saveMcpServer: vi.fn(),
  setMcpToolPolicy: vi.fn(),
}))

const api = await import('@/api/agentApiClient')

const now = '2026-05-31T00:00:00.000Z'

function job(): JobSummary {
  return {
    created_at: now,
    current_stage: null,
    job_id: 'job_mcp_refresh_001',
    job_type: 'mcp_capability_refresh_job',
    last_event_id: null,
    last_event_seq: 0,
    priority: 'normal',
    progress_percent: 0,
    status: 'queued',
    target_scope: { scope_type: 'mcp_server', server_name: 'filesystem' },
    title: 'MCP capability refresh (filesystem)',
    updated_at: now,
    workspace_id: 'default',
  }
}

function secret(overrides: Partial<SecretSummary> = {}): SecretSummary {
  return {
    display_name: 'MCP headers',
    masked: 'json-****ders',
    secret_id: 'mcp_headers_primary',
    secret_ref: 'mcp_headers/primary',
    status: 'active',
    type: 'mcp_headers',
    updated_at: now,
    ...overrides,
  }
}

describe('McpToolsPanel', () => {
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
    vi.mocked(api.listMcpServers).mockResolvedValue({
      servers: [
        {
          enabled: true,
          last_seen: now,
          server_name: 'filesystem',
          stale: false,
          status: 'connected',
          tool_count: 1,
          transport: 'stdio',
          updated_at: now,
        },
      ],
      workspace_id: 'default',
    } satisfies McpServersResponse)
    vi.mocked(api.listSecrets).mockResolvedValue({
      secrets: [
        secret(),
        secret({
          display_name: 'MCP OAuth',
          masked: 'oauth-****',
          secret_id: 'mcp_oauth_primary',
          secret_ref: 'mcp_oauth_credential/primary',
          type: 'mcp_oauth_credential',
        }),
      ],
      workspace_id: 'default',
    } satisfies ListSecretsResponse)
    vi.mocked(api.listMcpTools).mockResolvedValue({
      server_name: 'filesystem',
      tools: [
        {
          args_schema: {
            properties: { path: { type: 'string' } },
            required: ['path'],
            type: 'object',
          },
          args_schema_hash: 'sha256:read',
          description: 'Read a file from the configured workspace scope.',
          enabled: true,
          input_schema_hash: 'sha256:read',
          model_name: 'mcp_filesystem_read_file',
          name: 'mcp_filesystem_read_file',
          risk_level: 'medium',
          server_name: 'filesystem',
          tool_name: 'read_file',
        },
      ],
      workspace_id: 'default',
    } satisfies McpToolsResponse)
    vi.mocked(api.getToolInventory).mockResolvedValue({
      created_at: now,
      tools: [
        {
          description: 'Read a file from the configured workspace scope.',
          enabled: true,
          name: 'mcp_filesystem_read_file',
          original_tool_name: 'read_file',
          risk_level: 'medium',
          server_name: 'filesystem',
          source: 'mcp',
        },
      ],
      workspace_id: 'default',
    } satisfies ToolInventoryResponse)
    vi.mocked(api.getMcpServerHealth).mockResolvedValue({
      connected: true,
      enabled: true,
      last_error: null,
      last_seen: now,
      next_action: 'none',
      reconnect: {
        mode: 'queued_mcp_capability_refresh_job',
        refresh_reason: 'reconnect',
        supported: true,
        uses_sse_job_progress: true,
      },
      runtime_configured: true,
      server_name: 'filesystem',
      stale: false,
      status: 'connected',
      tool_count: 1,
      transport: 'stdio',
      workspace_id: 'default',
    } satisfies McpServerHealthResponse)
    vi.mocked(api.getMcpServer).mockResolvedValue({
      server: {
        args: ['--scope', 'workspace'],
        command: 'npx',
        enabled: true,
        env: {},
        last_error: null,
        manifest_object_key: 'workspaces/default/mcp/servers/filesystem/manifest.json',
        public_headers: {},
        server_name: 'filesystem',
        status: 'connected',
        timeout_ms: 30000,
        transport: 'stdio',
      },
      server_name: 'filesystem',
      snapshot: {
        object_keys: {
          capability_snapshot:
            'workspaces/default/mcp/servers/filesystem/capability_snapshot.json',
        },
        snapshot_hash: 'sha256:snapshot',
        stale: false,
        status: 'connected',
        tools: [],
        transport: 'stdio',
        updated_at: now,
      },
      workspace_id: 'default',
    } satisfies McpServerDetailResponse)
    vi.mocked(api.saveMcpServer).mockResolvedValue({
      server: {
        args: ['--scope', 'workspace'],
        command: 'npx',
        enabled: true,
        env: {},
        headers_ref: 'secret_ref://mcp_headers/custom_headers',
        last_error: null,
        oauth_credential_ref: 'secret_ref://mcp_oauth_credential/custom_oauth',
        public_headers: {},
        secret_env_refs: { GITHUB_TOKEN: 'secret_ref://mcp_headers/github_token' },
        server_name: 'filesystem',
        status: 'configured',
        timeout_ms: 30000,
        transport: 'stdio',
      },
      server_name: 'filesystem',
      snapshot: null,
      workspace_id: 'default',
    } satisfies McpServerDetailResponse)
    vi.mocked(api.refreshMcpServer).mockResolvedValue({
      job_id: 'job_mcp_refresh_001',
      refresh_job: job(),
      server: {},
      server_name: 'filesystem',
      snapshot: {},
      workspace_id: 'default',
    } satisfies McpRefreshResponse)
    vi.mocked(api.reconnectMcpServer).mockResolvedValue({
      health: {
        connected: false,
        enabled: true,
        last_error: null,
        last_seen: now,
        next_action: 'reconnect',
        reconnect: {
          mode: 'queued_mcp_capability_refresh_job',
          refresh_reason: 'reconnect',
          supported: true,
          uses_sse_job_progress: true,
        },
        runtime_configured: true,
        server_name: 'filesystem',
        stale: true,
        status: 'restarting',
        tool_count: 1,
        transport: 'stdio',
        workspace_id: 'default',
      },
      job_id: 'job_mcp_reconnect_001',
      refresh_job: { ...job(), job_id: 'job_mcp_reconnect_001' },
      server: {},
      server_name: 'filesystem',
      snapshot: {},
      workspace_id: 'default',
    } satisfies McpReconnectResponse)
    vi.mocked(api.setMcpToolPolicy).mockResolvedValue({
      enabled: false,
      model_name: 'mcp_filesystem_read_file',
      policy_version: 2,
      risk_level: 'medium',
      server_name: 'filesystem',
      tool_name: 'read_file',
      updated_at: now,
      updated_by: 'default_user',
      workspace_id: 'default',
    })
  })

  it('loads MCP servers, toggles one tool policy, and queues a refresh job', async () => {
    render(<McpToolsPanel workspaceId="default" />)

    await screen.findByText('read_file')
    expect(api.listMcpServers).toHaveBeenCalledWith('default')
    expect(api.listSecrets).toHaveBeenCalledWith('default')
    expect(api.listMcpTools).toHaveBeenCalledWith('filesystem', 'default')
    expect(api.getMcpServer).toHaveBeenCalledWith('filesystem', 'default')
    expect(screen.getAllByText('mcp_filesystem_read_file').length).toBeGreaterThan(0)
    expect(screen.getByText('Server details')).toBeInTheDocument()
    expect(screen.getByText(/manifest_object_key:/)).toBeInTheDocument()
    expect(screen.getByText(/capability_snapshot:/)).toBeInTheDocument()
    expect(screen.getByText(/sha256:snapshot/)).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('mcp-tool-toggle-filesystem-read_file'))
    expect(api.setMcpToolPolicy).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByRole('button', { name: 'Disable tool' }))
    await waitFor(() =>
      expect(api.setMcpToolPolicy).toHaveBeenCalledWith(
        {
          enabled: false,
          input_schema_hash: 'sha256:read',
          risk_level: 'medium',
          server_name: 'filesystem',
          tool_name: 'read_file',
        },
        'default',
      ),
    )

    fireEvent.click(screen.getByTestId('mcp-refresh-server'))
    expect(api.refreshMcpServer).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByRole('button', { name: 'Queue refresh' }))
    await waitFor(() =>
      expect(api.refreshMcpServer).toHaveBeenCalledWith(
        'filesystem',
        { refresh_reason: 'manual_frontend' },
        'default',
      ),
    )
    await screen.findByText('job_mcp_refresh_001')

    fireEvent.click(screen.getByTestId('mcp-reconnect-server'))
    expect(api.reconnectMcpServer).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByRole('button', { name: 'Reconnect server' }))
    await waitFor(() =>
      expect(api.reconnectMcpServer).toHaveBeenCalledWith('filesystem', {}, 'default'),
    )
    await screen.findByText('job_mcp_reconnect_001')

    expect(screen.getByRole('link', { name: 'Jobs' })).toHaveAttribute(
      'href',
      '/jobs?job_id=job_mcp_reconnect_001',
    )
  }, 10000)

  it('saves server transport config through the API adapter', async () => {
    render(<McpToolsPanel workspaceId="default" />)

    await screen.findByText('read_file')
    fireEvent.click(screen.getByTestId('mcp-save-server-config'))
    expect(api.saveMcpServer).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm save' }))

    await waitFor(() =>
      expect(api.saveMcpServer).toHaveBeenCalledWith(
        'filesystem',
        expect.objectContaining({
          args: ['--scope', 'workspace'],
          command: 'npx',
          enabled: true,
          env: {},
          public_headers: {},
          scope: 'workspace',
          timeout_ms: 30000,
          transport: 'stdio',
          url: null,
        }),
        'default',
      ),
    )
  }, 10000)

  it('loads mcpServers JSON into the existing server form', async () => {
    render(<McpToolsPanel workspaceId="default" />)

    await screen.findByText('read_file')
    fireEvent.change(screen.getByTestId('mcp-json-input'), {
      target: {
        value: JSON.stringify({
          mcpServers: {
            github: {
              args: ['-y', '@modelcontextprotocol/server-github'],
              command: 'npx',
              env: { GITHUB_TOKEN: '${env:GITHUB_TOKEN}' },
              timeout: 45000,
            },
          },
        }),
      },
    })
    fireEvent.click(screen.getByTestId('mcp-load-json'))

    expect(await screen.findByText(/Loaded github from MCP JSON/)).toBeInTheDocument()
    expect(api.saveMcpServer).not.toHaveBeenCalled()

    fireEvent.click(screen.getByTestId('mcp-save-server-config'))
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm save' }))

    await waitFor(() =>
      expect(api.saveMcpServer).toHaveBeenCalledWith(
        'github',
        expect.objectContaining({
          args: ['-y', '@modelcontextprotocol/server-github'],
          command: 'npx',
          enabled: true,
          env: { GITHUB_TOKEN: '${env:GITHUB_TOKEN}' },
          public_headers: {},
          scope: 'workspace',
          timeout_ms: 45000,
          transport: 'stdio',
          url: null,
        }),
        'default',
      ),
    )
  }, 10000)

  it('imports every server from mcpServers JSON', async () => {
    vi.mocked(api.saveMcpServer).mockImplementation(
      async (serverName, input, workspaceId) => ({
        server: {
          ...input,
          server_name: serverName,
          status: 'configured',
        },
        server_name: serverName,
        snapshot: null,
        workspace_id: workspaceId ?? 'default',
      }),
    )

    render(<McpToolsPanel workspaceId="default" />)

    await screen.findByText('read_file')
    fireEvent.change(screen.getByTestId('mcp-json-input'), {
      target: {
        value: JSON.stringify({
          mcpServers: {
            filesystem: {
              args: ['-y', '@modelcontextprotocol/server-filesystem', 'C:\\Users\\Administrator\\Desktop'],
              command: 'npx',
            },
            remoteTools: {
              headers: { 'X-Agent-System': 'p0' },
              url: 'http://localhost:3939/mcp',
            },
          },
        }),
      },
    })
    fireEvent.click(screen.getByTestId('mcp-import-json'))
    expect(api.saveMcpServer).toHaveBeenCalledTimes(0)
    const confirmButtons = await screen.findAllByRole('button', { name: 'Import and refresh' })
    fireEvent.click(confirmButtons.at(-1) as HTMLElement)

    await waitFor(() => expect(api.saveMcpServer).toHaveBeenCalledTimes(2))
    expect(api.saveMcpServer).toHaveBeenNthCalledWith(
      1,
      'filesystem',
      expect.objectContaining({
        args: ['-y', '@modelcontextprotocol/server-filesystem', 'C:\\Users\\Administrator\\Desktop'],
        command: 'npx',
        transport: 'stdio',
      }),
      'default',
    )
    expect(api.saveMcpServer).toHaveBeenNthCalledWith(
      2,
      'remoteTools',
      expect.objectContaining({
        public_headers: { 'X-Agent-System': 'p0' },
        transport: 'streamable_http',
        url: 'http://localhost:3939/mcp',
      }),
      'default',
    )
    await waitFor(() => expect(api.refreshMcpServer).toHaveBeenCalledTimes(2))
    expect(api.refreshMcpServer).toHaveBeenNthCalledWith(
      1,
      'filesystem',
      { refresh_reason: 'mcp_json_import' },
      'default',
    )
    expect(api.refreshMcpServer).toHaveBeenNthCalledWith(
      2,
      'remoteTools',
      { refresh_reason: 'mcp_json_import' },
      'default',
    )
    expect(await screen.findByText(/Queued 2 snapshot refresh jobs/)).toBeInTheDocument()
    expect(await screen.findByText(/Imported 2 MCP servers from MCP JSON/)).toBeInTheDocument()
  }, 10000)

  it('normalizes MCP secret references before saving server config', async () => {
    render(<McpToolsPanel workspaceId="default" />)

    await screen.findByText('read_file')
    fireEvent.change(screen.getByPlaceholderText('{"GITHUB_TOKEN":"secret_ref://mcp_headers/github"}'), {
      target: { value: '{"GITHUB_TOKEN":"github_token"}' },
    })
    fireEvent.change(screen.getByPlaceholderText('secret_ref://mcp_headers/name'), {
      target: { value: 'custom_headers' },
    })
    fireEvent.change(screen.getByPlaceholderText('secret_ref://mcp_oauth_credential/name'), {
      target: { value: 'mcp_oauth_credential/custom_oauth' },
    })
    fireEvent.click(screen.getByTestId('mcp-save-server-config'))
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm save' }))

    await waitFor(() =>
      expect(api.saveMcpServer).toHaveBeenCalledWith(
        'filesystem',
        expect.objectContaining({
          headers_ref: 'secret_ref://mcp_headers/custom_headers',
          oauth_credential_ref: 'secret_ref://mcp_oauth_credential/custom_oauth',
          secret_env_refs: {
            GITHUB_TOKEN: 'secret_ref://mcp_headers/github_token',
          },
        }),
        'default',
      ),
    )
  }, 10000)

  it('loads the first configured server instead of assuming filesystem', async () => {
    vi.mocked(api.listMcpServers).mockResolvedValue({
      servers: [
        {
          enabled: true,
          last_seen: now,
          server_name: 'gitlab',
          stale: false,
          status: 'connected',
          tool_count: 0,
          transport: 'streamable_http',
          updated_at: now,
        },
      ],
      workspace_id: 'default',
    } satisfies McpServersResponse)
    vi.mocked(api.listMcpTools).mockResolvedValue({
      server_name: 'gitlab',
      tools: [],
      workspace_id: 'default',
    } satisfies McpToolsResponse)
    vi.mocked(api.getMcpServerHealth).mockResolvedValue({
      connected: true,
      enabled: true,
      last_error: null,
      last_seen: now,
      next_action: 'none',
      reconnect: {
        mode: 'queued_mcp_capability_refresh_job',
        refresh_reason: 'reconnect',
        supported: true,
        uses_sse_job_progress: true,
      },
      runtime_configured: true,
      server_name: 'gitlab',
      stale: false,
      status: 'connected',
      tool_count: 0,
      transport: 'streamable_http',
      workspace_id: 'default',
    } satisfies McpServerHealthResponse)
    vi.mocked(api.getMcpServer).mockResolvedValue({
      server: {
        enabled: true,
        public_headers: {},
        server_name: 'gitlab',
        status: 'connected',
        timeout_ms: 30000,
        transport: 'streamable_http',
        url: 'http://localhost:3939/mcp',
      },
      server_name: 'gitlab',
      snapshot: null,
      workspace_id: 'default',
    } satisfies McpServerDetailResponse)

    render(<McpToolsPanel workspaceId="default" />)

    await waitFor(() => expect(screen.getAllByText('gitlab').length).toBeGreaterThan(0))
    await waitFor(() => expect(api.listMcpTools).toHaveBeenCalledWith('gitlab', 'default'))
    expect(api.listMcpTools).not.toHaveBeenCalledWith('filesystem', 'default')
  }, 10000)
})
