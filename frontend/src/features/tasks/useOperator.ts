import { useCallback, useSyncExternalStore } from 'react'

// 操作人身份存在浏览器本地：写操作全部记名。顶栏一个输入框，各页共用同一个值。
export const AGENT_KEY = 'okcomputer.agent'

let cached = (() => {
  try {
    return localStorage.getItem(AGENT_KEY) ?? ''
  } catch {
    return ''
  }
})()
const listeners = new Set<() => void>()

function emit() {
  try {
    cached = localStorage.getItem(AGENT_KEY) ?? ''
  } catch {
    cached = ''
  }
  listeners.forEach((l) => l())
}
// 另一个标签页里改了署名，这边跟着变
window.addEventListener('storage', (e) => {
  if (e.key === AGENT_KEY) emit()
})

function subscribe(cb: () => void) {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}

export function useOperator(): [string, (value: string) => void] {
  const agent = useSyncExternalStore(subscribe, () => cached, () => cached)
  const setAgent = useCallback((value: string) => {
    try {
      localStorage.setItem(AGENT_KEY, value)
    } catch {
      // 存不下（隐私模式等）：本次会话内仍然生效
    }
    emit()
  }, [])
  return [agent, setAgent]
}
