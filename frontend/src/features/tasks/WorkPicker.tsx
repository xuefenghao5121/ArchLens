import type { ReactNode } from 'react'
import { ITER_STATUS_META, TASK_STATUS_META, iterationTitle } from '../../status'
import type { Work } from './useWork'
import './tasks.css'

interface Props {
  work: Work
  // 是否显示迭代选择
  iteration?: boolean
  right?: ReactNode
}

/** 任务 → 迭代 两级选择器。各页共用，选择落在 URL 参数上。 */
export default function WorkPicker({ work, iteration = true, right }: Props) {
  const it = work.iteration
  const its = work.task?.iterations ?? []
  return (
    <div className="wp">
      <label>
        任务
        <select value={work.task?.task_id ?? ''} onChange={(e) => work.setTask(e.target.value)}>
          {!work.tasks.length && <option value="">（还没有任务）</option>}
          {work.tasks.map((t) => (
            <option key={t.task_id} value={t.task_id}>
              {t.title}（{TASK_STATUS_META[t.status].label}）
            </option>
          ))}
        </select>
      </label>
      {iteration && (
        <label>
          迭代
          <select
            value={it?.iteration_id ?? ''}
            onChange={(e) => work.setIteration(e.target.value)}
            disabled={!its.length}
          >
            {!its.length && <option value="">（这个任务不走层）</option>}
            {its.map((i) => (
              <option key={i.iteration_id} value={i.iteration_id}>
                {i.label} · {ITER_STATUS_META[i.status].label}{iterationTitle(i) ? ` · ${iterationTitle(i)}` : ''}
              </option>
            ))}
          </select>
        </label>
      )}
      {it && (
        <span
          className="wp-status"
          style={{ color: ITER_STATUS_META[it.status].color, borderColor: ITER_STATUS_META[it.status].color }}
        >
          {it.label} · {ITER_STATUS_META[it.status].label}
        </span>
      )}
      {right}
    </div>
  )
}
