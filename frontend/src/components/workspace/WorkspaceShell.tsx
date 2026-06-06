'use client'

import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Layout,
  Menu,
  Row,
  Segmented,
  Skeleton,
  Space,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { createStyles } from 'antd-style'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useMemo, useState } from 'react'

import { getAgentApiBaseUrl } from '@/api/agentApiClient'
import { ChatPanel, type ChatRuntimeContext } from '@/components/chat/ChatPanel'
import { JobTaskCenter } from '@/components/jobs/JobTaskCenter'
import { KnowledgePanel } from '@/components/knowledge/KnowledgePanel'
import { SystemLogsPanel } from '@/components/logs/SystemLogsPanel'
import { MemoryPanel } from '@/components/memory/MemoryPanel'
import { McpToolsPanel } from '@/components/mcp/McpToolsPanel'
import { SettingsPanel } from '@/components/settings/SettingsPanel'
import { SkillsPanel } from '@/components/skills/SkillsPanel'
import { SubAgentsPanel } from '@/components/subagents/SubAgentsPanel'
import {
  getLocalizedPanelSpecs,
  getLocalizedWorkspaceMenuItems,
  getWorkspaceCopy,
} from '@/i18n/workspace'
import { P0ReadinessPanel } from '@/components/readiness/P0ReadinessPanel'
import {
  getWorkspaceSectionFromPathname,
  getWorkspaceSectionPath,
  panelSpecs,
  type SectionKey,
  workspaceSections,
} from '@/components/workspace/routes'
import { useBootstrapStore } from '@/stores/useBootstrapStore'
import {
  useUiPreferencesStore,
  type AppLanguage,
  type ThemeMode,
} from '@/stores/useUiPreferencesStore'

const { Content, Header, Sider } = Layout
const { Text, Title } = Typography

const useStyles = createStyles(({ css, token }) => ({
  content: css`
    padding: 24px;
  `,
  header: css`
    align-items: center;
    border-bottom: 1px solid ${token.colorBorderSecondary};
    display: flex;
    gap: 16px;
    min-height: 56px;
    justify-content: space-between;
    padding: 8px 24px;
  `,
  headerLeft: css`
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    min-width: 0;
  `,
  headerRight: css`
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    justify-content: flex-end;
  `,
  preferenceControls: css`
    align-items: center;
    background: ${token.colorFillQuaternary};
    border: 1px solid ${token.colorBorderSecondary};
    border-radius: 8px;
    display: flex;
    gap: 6px;
    padding: 4px;
  `,
  preferenceSegment: css`
    .ant-segmented-item {
      min-width: 54px;
    }
  `,
  overview: css`
    margin-bottom: 16px;
  `,
  routeMarker: css`
    border: 0;
    clip: rect(0 0 0 0);
    clip-path: inset(50%);
    height: 1px;
    margin: -1px;
    overflow: hidden;
    padding: 0;
    position: absolute;
    white-space: nowrap;
    width: 1px;
  `,
  sectionCard: css`
    height: 100%;
    min-height: 148px;
  `,
  shell: css`
    min-height: 100vh;
  `,
  sider: css`
    border-right: 1px solid ${token.colorBorderSecondary};
    padding: 16px 12px;
  `,
}))

interface WorkspaceShellProps {
  initialSection?: SectionKey
}

export function WorkspaceShell({ initialSection = 'chat' }: WorkspaceShellProps) {
  const { styles } = useStyles()
  const pathname = usePathname()
  const router = useRouter()
  const routeSection = getWorkspaceSectionFromPathname(pathname)
  const [activeKey, setActiveKey] = useState<SectionKey>(routeSection ?? initialSection)
  const [chatRuntimeContext, setChatRuntimeContext] = useState<ChatRuntimeContext>({
    run_id: null,
    run_status: null,
    thread_id: null,
  })
  const language = useUiPreferencesStore((state) => state.language)
  const setLanguage = useUiPreferencesStore((state) => state.setLanguage)
  const setThemeMode = useUiPreferencesStore((state) => state.setThemeMode)
  const themeMode = useUiPreferencesStore((state) => state.themeMode)
  const bootstrap = useBootstrapStore((state) => state.bootstrap)
  const errorMessage = useBootstrapStore((state) => state.errorMessage)
  const loadBootstrap = useBootstrapStore((state) => state.loadBootstrap)
  const status = useBootstrapStore((state) => state.status)

  useEffect(() => {
    if (status === 'idle') {
      void loadBootstrap()
    }
  }, [loadBootstrap, status])

  useEffect(() => {
    setActiveKey(routeSection ?? initialSection)
  }, [initialSection, routeSection])

  const workspaceId = bootstrap?.workspace.workspace_id ?? 'default'
  const userRole = bootstrap?.user.role ?? 'owner'
  const localizedPanelSpecs = useMemo(
    () => getLocalizedPanelSpecs(language, panelSpecs),
    [language],
  )
  const menuItems = useMemo(
    () => getLocalizedWorkspaceMenuItems(language, workspaceSections),
    [language],
  )
  const copy = useMemo(() => getWorkspaceCopy(language), [language])
  const activePanel = localizedPanelSpecs[activeKey]
  const routeMarkerPanel = panelSpecs[activeKey]
  const apiBaseUrl = useMemo(() => getAgentApiBaseUrl(), [])
  const routeRenderMarker =
    activeKey === 'settings'
      ? `${routeMarkerPanel.title} ${routeMarkerPanel.description} Model APIs Databases Secrets`
      : `${routeMarkerPanel.title} ${routeMarkerPanel.description}`

  const navigateToSection = (section: SectionKey) => {
    setActiveKey(section)
    router.push(getWorkspaceSectionPath(section))
  }

  return (
    <Layout className={styles.shell}>
      <Sider className={styles.sider} theme={themeMode} width={232}>
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <div>
            <Title level={4} style={{ margin: 0 }}>
              Agent System
            </Title>
            <Text type="secondary">{copy.workspaceConsole}</Text>
          </div>
          <Menu
            items={menuItems}
            theme={themeMode}
            mode="inline"
            onClick={({ key }) => navigateToSection(key as SectionKey)}
            selectedKeys={[activeKey]}
          />
        </Space>
      </Sider>
      <Layout>
        <Header className={styles.header}>
          <div className={styles.headerLeft}>
            <Text strong>{copy.workspace}</Text>
            <Tag color="blue">{workspaceId}</Tag>
            <Text type="secondary">{apiBaseUrl}</Text>
          </div>
          <div className={styles.headerRight}>
            <Text type="secondary">{copy.role}</Text>
            <Tag>{userRole}</Tag>
            <div className={styles.preferenceControls}>
              <Tooltip title={copy.themeTooltip}>
                <Segmented
                  aria-label={copy.themeTooltip}
                  className={styles.preferenceSegment}
                  onChange={(value) => setThemeMode(value as ThemeMode)}
                  options={[
                    { label: copy.light, value: 'light' },
                    { label: copy.dark, value: 'dark' },
                  ]}
                  size="small"
                  value={themeMode}
                />
              </Tooltip>
              <Tooltip title={copy.languageTooltip}>
                <Segmented
                  aria-label={copy.languageTooltip}
                  className={styles.preferenceSegment}
                  onChange={(value) => setLanguage(value as AppLanguage)}
                  options={[
                    { label: 'EN', value: 'en' },
                    { label: 'ZH', value: 'zh' },
                  ]}
                  size="small"
                  value={language}
                />
              </Tooltip>
            </div>
          </div>
        </Header>
        <Content className={styles.content}>
          <span className={styles.routeMarker} data-testid="workspace-route-marker">
            Agent System {routeRenderMarker}
          </span>

          {status === 'loading' && <Skeleton active paragraph={{ rows: 6 }} />}

          {status === 'error' && (
            <Alert
              action={
                <Button onClick={() => void loadBootstrap()} size="small">
                  {copy.retry}
                </Button>
              }
              description={errorMessage}
              message={copy.bootstrapFailed}
              showIcon
              type="error"
            />
          )}

          {status === 'ready' && bootstrap && (
            <>
              <Card className={styles.overview}>
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  <Space align="start" direction="vertical" size={4}>
                    <Title level={3} style={{ margin: 0 }}>
                      {activePanel.title}
                    </Title>
                    <Text type="secondary">{activePanel.description}</Text>
                  </Space>
                  <Descriptions
                    column={{ lg: 3, md: 2, xs: 1 }}
                    items={[
                      {
                        key: 'identity',
                        label: copy.user,
                        children: bootstrap.user.user_id,
                      },
                      {
                        key: 'workspace_role',
                        label: copy.workspaceRole,
                        children: bootstrap.workspace.workspace_role,
                      },
                      {
                        key: 'login',
                        label: copy.loginPage,
                        children: bootstrap.feature_flags.login_enabled ? copy.enabled : copy.disabled,
                      },
                    ]}
                    size="small"
                  />
                </Space>
              </Card>

              {activeKey === 'chat' && (
                <ChatPanel
                  onRuntimeContextChange={setChatRuntimeContext}
                  workspaceId={workspaceId}
                />
              )}
              {activeKey === 'jobs' && <JobTaskCenter workspaceId={workspaceId} />}
              {activeKey === 'knowledge' && (
                <KnowledgePanel workspaceId={workspaceId} />
              )}
              {activeKey === 'memory' && <MemoryPanel workspaceId={workspaceId} />}
              {activeKey === 'skills' && (
                <SkillsPanel
                  runtimeContext={chatRuntimeContext}
                  workspaceId={workspaceId}
                />
              )}
              {activeKey === 'subagents' && (
                <SubAgentsPanel
                  runtimeContext={chatRuntimeContext}
                  workspaceId={workspaceId}
                />
              )}
              {activeKey === 'mcp' && <McpToolsPanel workspaceId={workspaceId} />}
              {activeKey === 'logs' && <SystemLogsPanel workspaceId={workspaceId} />}
              {activeKey === 'readiness' && <P0ReadinessPanel workspaceId={workspaceId} />}
              {activeKey === 'settings' && <SettingsPanel workspaceId={workspaceId} />}

              {activeKey !== 'chat' &&
                activeKey !== 'jobs' &&
                activeKey !== 'knowledge' &&
                activeKey !== 'memory' &&
                activeKey !== 'skills' &&
                activeKey !== 'subagents' &&
                activeKey !== 'mcp' &&
                activeKey !== 'logs' &&
                activeKey !== 'readiness' &&
                activeKey !== 'settings' && (
                <Row gutter={[16, 16]}>
                  <Col lg={8} md={12} xs={24}>
                    <Card className={styles.sectionCard} title={copy.apiSurface}>
                      <Space direction="vertical" size={8}>
                        {activePanel.endpoints.map((endpoint) => (
                          <Text code key={endpoint}>
                            {endpoint}
                          </Text>
                        ))}
                      </Space>
                    </Card>
                  </Col>
                  <Col lg={8} md={12} xs={24}>
                    <Card className={styles.sectionCard} title={copy.p0Checks}>
                      <Space wrap>
                        {activePanel.checks.map((check) => (
                          <Tag color="green" key={check}>
                            {check}
                          </Tag>
                        ))}
                      </Space>
                    </Card>
                  </Col>
                  <Col lg={8} md={12} xs={24}>
                    <Card className={styles.sectionCard} title={copy.featureFlags}>
                      <Space wrap>
                        {Object.entries(bootstrap.feature_flags).map(([key, enabled]) => (
                          <Tag color={enabled ? 'green' : 'default'} key={key}>
                            {key}
                          </Tag>
                        ))}
                      </Space>
                    </Card>
                  </Col>
                </Row>
              )}
            </>
          )}
        </Content>
      </Layout>
    </Layout>
  )
}
