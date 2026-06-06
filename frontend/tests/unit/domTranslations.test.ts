import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  startDomTranslationObserver,
  translateUiText,
} from '@/i18n/domTranslations'

describe('domTranslations', () => {
  afterEach(() => {
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('translates common subpage UI copy to Chinese and keeps English untouched', () => {
    expect(translateUiText('Threads', 'zh')).toBe('会话')
    expect(translateUiText('Model API configuration', 'zh')).toBe('模型 API 配置')
    expect(translateUiText('Database connections', 'zh')).toBe('数据库连接')
    expect(translateUiText('Skills', 'zh')).toBe('Skill')
    expect(translateUiText('Tools', 'zh')).toBe('Tool')
    expect(translateUiText('Job', 'zh')).toBe('Job')
    expect(translateUiText('Runtime', 'zh')).toBe('Runtime')
    expect(translateUiText('Run event stream', 'zh')).toBe('Run 事件流')
    expect(translateUiText('MCP JSON import', 'zh')).toBe('MCP JSON 导入')
    expect(translateUiText('worker running', 'zh')).toBe('Worker 运行中')
    expect(translateUiText('Threads', 'en')).toBe('Threads')
  })

  it('translates DOM text and attributes without touching code or route markers', () => {
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      callback(0)
      return 0
    })

    document.body.innerHTML = `
      <button>Refresh</button>
      <input placeholder="Search memory" />
      <pre>Refresh</pre>
      <span data-testid="workspace-route-marker">Jobs</span>
    `

    const stop = startDomTranslationObserver('zh')

    expect(document.querySelector('button')).toHaveTextContent('刷新')
    expect(document.querySelector('input')).toHaveAttribute('placeholder', '搜索记忆')
    expect(document.querySelector('pre')).toHaveTextContent('Refresh')
    expect(document.querySelector('[data-testid="workspace-route-marker"]')).toHaveTextContent('Jobs')

    stop()
  })
})
