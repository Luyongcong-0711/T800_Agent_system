'use client'

import { Tabs } from 'antd'
import { useEffect, useState } from 'react'

import { DatabaseSettingsPanel } from '@/components/settings/DatabaseSettingsPanel'
import { ModelConfigSettings } from '@/components/settings/ModelConfigSettings'
import { SecretsPanel } from '@/components/settings/SecretsPanel'
import type { WorkspaceId } from '@/api/schemas/workspace'

interface SettingsPanelProps {
  workspaceId: WorkspaceId
}

type SettingsTabKey = 'models' | 'databases' | 'secrets'

const SETTINGS_TABS: SettingsTabKey[] = ['models', 'databases', 'secrets']

export function SettingsPanel({ workspaceId }: SettingsPanelProps) {
  const [activeTab, setActiveTab] = useState<SettingsTabKey>('models')

  useEffect(() => {
    const nextTab = new URLSearchParams(window.location.search).get('tab')
    if (isSettingsTabKey(nextTab)) {
      setActiveTab(nextTab)
    }
  }, [])

  return (
    <Tabs
      activeKey={activeTab}
      items={[
        {
          children: <ModelConfigSettings workspaceId={workspaceId} />,
          key: 'models',
          label: 'Model APIs',
        },
        {
          children: <DatabaseSettingsPanel workspaceId={workspaceId} />,
          key: 'databases',
          label: 'Databases',
        },
        {
          children: <SecretsPanel workspaceId={workspaceId} />,
          key: 'secrets',
          label: 'Secrets',
        },
      ]}
      onChange={(key) => {
        const nextTab = isSettingsTabKey(key) ? key : 'models'
        setActiveTab(nextTab)
        const url = new URL(window.location.href)
        url.searchParams.set('tab', nextTab)
        window.history.replaceState(null, '', `${url.pathname}${url.search}`)
      }}
    />
  )
}

function isSettingsTabKey(value: unknown): value is SettingsTabKey {
  return typeof value === 'string' && SETTINGS_TABS.includes(value as SettingsTabKey)
}
