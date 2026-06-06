'use client'

import { ConfigProvider } from 'antd'
import enUS from 'antd/locale/en_US'
import zhCN from 'antd/locale/zh_CN'
import { ThemeProvider } from 'antd-style'
import { useEffect, useMemo } from 'react'
import type { ReactNode } from 'react'

import { startDomTranslationObserver } from '@/i18n/domTranslations'
import { useUiPreferencesStore } from '@/stores/useUiPreferencesStore'
import { getAntdTheme } from '@/styles/theme'

export function AppProviders({ children }: { children: ReactNode }) {
  const language = useUiPreferencesStore((state) => state.language)
  const themeMode = useUiPreferencesStore((state) => state.themeMode)
  const theme = useMemo(() => getAntdTheme(themeMode), [themeMode])
  const locale = language === 'zh' ? zhCN : enUS

  useEffect(() => {
    document.documentElement.dataset.theme = themeMode
    document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en'
    document.documentElement.style.colorScheme = themeMode
  }, [language, themeMode])

  useEffect(() => startDomTranslationObserver(language), [language])

  return (
    <ConfigProvider locale={locale} theme={theme}>
      <ThemeProvider theme={theme}>{children}</ThemeProvider>
    </ConfigProvider>
  )
}
