import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export type AppLanguage = 'en' | 'zh'
export type ThemeMode = 'dark' | 'light'

interface UiPreferencesState {
  language: AppLanguage
  setLanguage: (language: AppLanguage) => void
  setThemeMode: (themeMode: ThemeMode) => void
  themeMode: ThemeMode
}

export const useUiPreferencesStore = create<UiPreferencesState>()(
  persist(
    (set) => ({
      language: 'en',
      setLanguage: (language) => set({ language }),
      setThemeMode: (themeMode) => set({ themeMode }),
      themeMode: 'light',
    }),
    {
      name: 'agent-system-ui-preferences',
      partialize: ({ language, themeMode }) => ({ language, themeMode }),
    },
  ),
)
