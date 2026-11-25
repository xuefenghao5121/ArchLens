import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useStateQuery } from '../shared/useAction'
import type { IterationView, LayerPackageView, TaskView, WorkflowState } from '../../types'

export interface Work {
  state: WorkflowState | undefined
  isLoading: boolean
  error: unknown
  tasks: TaskView[]
  task: TaskView | undefined
  setTask: (taskId: string) => void
  iteration: IterationView | undefined
  setIteration: (iterationId: string) => void
  // 一次改多项（如新建任务后同时切任务、迭代）；值为 null 表示清掉该参数
  select: (kv: Record<string, string | null>) => void
  // 按 ID 取层包（流程编排里的框只带 ID，卡片内容在这里取）
  pkgOf: (id: string) => LayerPackageView | undefined
  // 这个包在所选迭代里是不是沿用的上游包；是则返回它所属的那一轮（如「迭代 1」）
  reusedOf: (id: string) => string | undefined
}

function pickDefaultTask(tasks: TaskView[], activeIteration: string | null): TaskView | undefined {
  // 默认看「有进行中迭代」的任务；都结束了就看最后建的那个
  return tasks.find((t) => t.iterations.some((i) => i.iteration_id === activeIteration))
    ?? tasks[tasks.length - 1]
}

/** 任务 / 迭代的当前选择。放在 URL 参数里：换页、刷新之后看的还是同一轮。 */
export function useWork(): Work {
  const [params, setParams] = useSearchParams()
  const { data: state, isLoading, error } = useStateQuery()

  const tasks = useMemo(() => state?.tasks ?? [], [state])
  const pkgIndex = useMemo(() => {
    const m = new Map<string, LayerPackageView>()
    for (const g of Object.values(state?.layers ?? {})) for (const p of g.packages) m.set(p.package_id, p)
    return m
  }, [state])

  const taskParam = params.get('task') ?? ''
  const task = tasks.find((t) => t.task_id === taskParam)
    ?? pickDefaultTask(tasks, state?.summary.active_iteration ?? null)
  const iterationParam = params.get('iteration') ?? ''
  const iteration = task?.iterations.find((i) => i.iteration_id === iterationParam)
    ?? task?.iterations.find((i) => i.iteration_id === task.default_iteration)

  // 「沿用」只记在迭代视图里（layers[L].reused[].reused_from），顶层包条目上没有
  const reusedFrom = useMemo(() => {
    const m = new Map<string, string>()
    for (const L of Object.values(iteration?.layers ?? {})) {
      for (const p of L.reused) if (p.reused_from) m.set(p.package_id, p.reused_from)
    }
    return m
  }, [iteration])


  const patch = (kv: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(kv)) {
      if (v) next.set(k, v)
      else next.delete(k)
    }
    setParams(next, { replace: true })
  }

  return {
    state, isLoading, error, tasks, task, iteration, select: patch,
    // 换任务时迭代回到该任务的默认轮，否则会停在上一任务的迭代上
    setTask: (id) => patch({ task: id, iteration: null }),
    setIteration: (id) => patch({ task: task?.task_id ?? null, iteration: id }),
    pkgOf: (id) => pkgIndex.get(id),
    reusedOf: (id) => reusedFrom.get(id),
  }
}

/** 回到流程编排的某一轮 */
export function flowHref(taskId?: string | null, iterationId?: string | null): string {
  const q = new URLSearchParams()
  if (taskId) q.set('task', taskId)
  if (iterationId) q.set('iteration', iterationId)
  const s = q.toString()
  return `/flow${s ? `?${s}` : ''}`
}

export const layerHref = (pkg: string) => `/layer/${encodeURIComponent(pkg)}`
