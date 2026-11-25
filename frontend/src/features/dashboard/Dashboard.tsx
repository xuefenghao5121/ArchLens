import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import type { LayerPackageView, TaskView } from '../../types'
import { STATE_META, TASK_STATUS_META } from '../../status'
import { EmptyState, PageErr, PageLoad } from '../shared/ui'
import { flowHref, layerHref, useWork } from '../tasks/useWork'
import TaskForm from '../tasks/TaskForm'
import TaskReceipts from '../tasks/TaskReceipts'
import './dashboard.css'

function statusChip(t: TaskView) {
  const m = TASK_STATUS_META[t.status]
  return <span className="state-chip" style={{ color: m.color, borderColor: m.color }}>{m.label}</span>
}

/** 一轮的进展一句话：完成 / 结束 / 终止 / 卡在哪个框（以及那个框的状态） */
function roundText(it: TaskView['iterations'][number]): { text: string; color: string } {
  if (it.status === 'aborted') return { text: '已终止', color: TASK_STATUS_META.aborted.color }
  if (it.complete) return { text: '已验证完成', color: STATE_META['已验证'].color }
  if (it.status === 'sealed') return { text: '已结束', color: TASK_STATUS_META.sealed.color }
  const stuck = it.nodes.find((n) => n.layer === it.frontier)
  if (!stuck) return { text: '进行中', color: TASK_STATUS_META.active.color }
  return { text: `卡在 ${stuck.layer} ${stuck.name}（${stuck.state}）`, color: STATE_META[stuck.state].color }
}

function TaskCard({ t }: { t: TaskView }) {
  const pending = t.receipts.filter((r) => r.pending)
  return (
    <div className="tcard">
      <div className="tcard-head">
        <span className="tcard-title">{t.title}</span>
        {statusChip(t)}
        <Link className="tf-link" to={flowHref(t.task_id, t.default_iteration)}>打开流程编排 →</Link>
      </div>
      <div className="tcard-goal">{t.goal.statement}</div>
      <div className="tcard-meta">
        <span>流程：{t.template_label || t.flow_template}</span>
        <span>benchmark：{t.benchmarks.map((b) => b.name).join('、') || '—'}</span>
      </div>
      <div className="tcard-iters">
        {t.iterations.map((i) => {
          const r = roundText(i)
          return (
            <Link key={i.iteration_id} className="ichip" to={flowHref(t.task_id, i.iteration_id)}
              style={{ color: r.color, borderColor: r.color }}>
              {i.label} {r.text}
            </Link>
          )
        })}
        {t.iterations.length === 0 && <span className="muted small">不走层</span>}
        {t.pending_decisions > 0 && (
          <span className="ichip" style={{ color: '#fbbf24', borderColor: '#fbbf24' }}>待人决定 {t.pending_decisions}</span>
        )}
      </div>
      {/* 任务级的人的决定：章程、开某一轮、封板。有待批事项时直接展开 */}
      {(pending.length > 0 || t.receipts.length > 0) && (
        pending.length > 0
          ? (
            <div>
              <div className="block-k">人的决定</div>
              <TaskReceipts receipts={t.receipts} />
            </div>
          )
          : (
            <details className="tcard-receipts">
              <summary>人的决定（章程、开轮、封板）{t.receipts.length} 条</summary>
              <TaskReceipts receipts={t.receipts} />
            </details>
          )
      )}
    </div>
  )
}

export default function Dashboard() {
  const work = useWork()
  const { state: data, tasks } = work
  const [showNewTask, setShowNewTask] = useState(false)
  const nav = useNavigate()

  if (work.isLoading) return <PageLoad />
  if (work.error) return <PageErr error={work.error} />
  if (!data) return null

  const live = tasks.filter((t) => t.status === 'draft' || t.status === 'active')
  const ended = tasks.filter((t) => t.status === 'sealed' || t.status === 'aborted')
  const pkgIndex = new Map<string, LayerPackageView>()
  for (const g of Object.values(data.layers)) for (const p of g.packages) pkgIndex.set(p.package_id, p)
  const pending = data.pending_decisions

  return (
    <div className="dash">
      <div className="page-head">
        <h1>研究总览</h1>
        <div className="head-actions">
          <button className="tf-btn primary" onClick={() => setShowNewTask(true)}>新建任务</button>
        </div>
      </div>

      {/* 等你决定：从哪里进 */}
      <section className="block">
        <div className="block-title">
          <strong>等你决定</strong>
          <span className="note">{pending.length ? `${pending.length} 项` : '现在没有等你处理的事项'}</span>
        </div>
        {pending.map((d) => {
          const pkg = pkgIndex.get(d.package_ids[0] ?? '')
          const task = tasks.find((t) => t.task_id === (d.task_id || pkg?.task_id))
          const where = pkg
            ? `${pkg.card.iteration_label ?? ''} ${pkg.card.layer} ${pkg.card.layer_name}`.trim()
            : task ? '任务章程' : ''
          return (
            <Link key={d.decision_id} className="pend"
              to={pkg ? layerHref(pkg.package_id) : flowHref(task?.task_id ?? null, null)}>
              <span className="pend-where">{task?.title ?? ''} · {where}</span>
              <span className="pend-text">{d.summary || d.headline}</span>
              <span className="pend-go">去处理 →</span>
            </Link>
          )
        })}
      </section>

      <section>
        <div className="block-title" style={{ margin: '18px 0 10px' }}>
          <strong>任务</strong>
          <span className="note">进行中的 {live.length} 个</span>
        </div>
        {tasks.length === 0 && (
          <EmptyState title="还没有任务" sub="新建一个任务：一句目标、一个 benchmark、一条流程模板。">
            <button className="tf-btn primary" onClick={() => setShowNewTask(true)}>新建任务</button>
          </EmptyState>
        )}
        <div className="tlist">
          {live.map((t) => <TaskCard key={t.task_id} t={t} />)}
          {tasks.length > 0 && live.length === 0 && <div className="muted small">没有进行中的任务。</div>}
        </div>
        {ended.length > 0 && (
          <details className="ended">
            <summary>已结束的任务（{ended.length}）</summary>
            <div className="tlist">
              {ended.map((t) => <TaskCard key={t.task_id} t={t} />)}
            </div>
          </details>
        )}
      </section>

      <div className="dash-foot mono">
        store：{data.store_isolated ? '隔离副本' : '真实 store'} · {data.store_root} · 哈希 {data.store_env_hash.slice(0, 16)}…
        {data.summary.hidden_packages > 0 ? ` · 另有 ${data.summary.hidden_packages} 个包属于已结束的迭代` : ''}
      </div>

      {showNewTask && (
        <TaskForm
          onClose={() => setShowNewTask(false)}
          onCreated={(taskId, iterationId) => {
            setShowNewTask(false)
            nav(flowHref(taskId, iterationId))
          }}
        />
      )}
    </div>
  )
}
