export interface BootstrapUser {
  user_id: string
  role: string
}

export interface BootstrapWorkspace {
  workspace_id: string
  workspace_role: string
}

export interface BootstrapFeatureFlags {
  login_enabled: boolean
  workspace_switch_enabled: boolean
  [key: string]: boolean
}

export interface BootstrapResponse {
  user: BootstrapUser
  workspace: BootstrapWorkspace
  feature_flags: BootstrapFeatureFlags
}

export interface ErrorResponse {
  ok: false
  error_type: string
  message_for_user: string
  retryable: boolean
  trace_id: string
  details?: Record<string, unknown>
}
