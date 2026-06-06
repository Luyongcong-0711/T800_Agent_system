import { theme } from 'antd'
import type { ThemeConfig } from 'antd'

import type { ThemeMode } from '@/stores/useUiPreferencesStore'

const baseToken = {
  borderRadius: 6,
  colorPrimary: '#1677ff',
  fontFamily:
    'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
}

export function getAntdTheme(themeMode: ThemeMode): ThemeConfig {
  if (themeMode === 'dark') {
    return {
      algorithm: theme.darkAlgorithm,
      token: {
        ...baseToken,
        colorBgLayout: '#101318',
        colorText: '#eef2ff',
      },
      components: {
        Card: {
          colorBgContainer: '#171b23',
        },
        Layout: {
          bodyBg: '#101318',
          headerBg: '#141821',
          siderBg: '#141821',
        },
        Menu: {
          itemBorderRadius: 6,
        },
      },
    }
  }

  return {
    algorithm: theme.defaultAlgorithm,
    token: {
      ...baseToken,
      colorBgLayout: '#f5f7fb',
      colorText: '#1f2430',
    },
    components: {
      Layout: {
        bodyBg: '#f5f7fb',
        headerBg: '#ffffff',
        siderBg: '#ffffff',
      },
      Menu: {
        itemBorderRadius: 6,
      },
    },
  }
}

export const antdTheme: ThemeConfig = {
  token: {
    ...baseToken,
    colorBgLayout: '#f5f7fb',
    colorText: '#1f2430',
  },
  components: {
    Layout: {
      bodyBg: '#f5f7fb',
      headerBg: '#ffffff',
      siderBg: '#ffffff',
    },
    Menu: {
      itemBorderRadius: 6,
    },
  },
}
