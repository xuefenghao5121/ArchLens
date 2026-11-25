import { useCallback, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { ActionRequest, ActionResponse, WorkflowState } from '../../types'

/** 全量状态。所有页面共用同一个缓存条目（queryKey ['state']）。 */
export function useStateQuery() {
  return useQuery<WorkflowState>({ queryKey: ['state'], queryFn: api.state })
}

export const CONFLICT_HINT = 'store 刚被别处改动；页面已刷新到最新状态，核对了现状之后再点一次（已填的内容还在）'

export function actionError(r: { error?: string; message?: string}): string {
  if (r.error === 'store_version_conflict') return CONFLICT_HINT
  return (r.error ?? '执行失败') + (r.message ? ` — ${r.message}` : '')
}

/**
 * 发一个写请求。一律带上当前 store 版本：别处改过就拒掉，而不是在没见过的状态上写。
 * 不论成败，回来后都让缓存过期——页面始终显示 store 里的现状。
 * 从不抛异常：网络错误也包成 {ok:false, error} 交给调用方显示。
 */
export function useAction() {
  const qc = useQueryClient()
  const { data } = useStateQuery()
  const version = data?.store_env_hash
  const [pending, setPending] = useState(0)

  const run = useCallback(async (req: ActionRequest): Promise<ActionResponse> => {
    setPending((n) => n + 1)
    try {
      const resp = await api.action({ ...req, target_version: version })
      qc.invalidateQueries()
      return resp
    } catch (err) {
      return { ok: false, action: req.action, error: (err as Error).message }
    } finally {
      setPending((n) => n - 1)
    }
  }, [qc, version])

  return { run, busy: pending > 0 }
}
