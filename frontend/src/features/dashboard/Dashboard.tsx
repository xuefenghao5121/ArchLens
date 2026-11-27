import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { ArchivedTask, LayerPackageView, TaskView } from '../../types'
import { STATE_META, TASK_STATUS_META } from '../../status'
import { actionError, useAction } from '../shared/useAction'
import { ActionNotice, EmptyState, PageErr, PageLoad } from '../shared/ui'
import { flowHref, layerHref, useWork } from '../tasks/useWork'
import { useOperator } from '../tasks/useOperator'
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

function TaskCard({ t, onArchive }: { t: TaskView; onArchive?: (t: TaskView) => void }) {
  const pending = t.receipts.filter((r) => r.pending)
  return (
    <div className="tcard">
      <div className="tcard-head">
        <span className="tcard-title">{t.title}</span>
        {statusChip(t)}
        <Link className="tf-link" to={flowHref(t.task_id, t.default_iteration)}>打开流程编排 →</Link>
        {onArchive && (
          <button className="tf-link muted" onClick={() => onArchive(t)}>归档</button>
        )}
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
  const [agent] = useOperator()
  const { run, busy } = useAction()
  const [archiveFor, setArchiveFor] = useState<TaskView | null>(null)
  const [archiveReason, setArchiveReason] = useState('')
  const [deleteFor, setDeleteFor] = useState<ArchivedTask | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState('')
  const [modalError, setModalError] = useState('')
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const { data: archivedData } = useQuery({ queryKey: ['archived-tasks'], queryFn: api.archivedTasks })
  const archived = archivedData?.tasks ?? []

  if (work.isLoading) return <PageLoad />
  if (work.error) return <PageErr error={work.error} />
  if (!data) return null

  const needAgent = () => {
    if (agent.trim()) return true
    setMsg({ kind: 'err', text: '请先在右上角填写操作人——归档 / 恢复 / 删除都要记名' })
    return false
  }

  const doArchive = async () => {
    if (!archiveFor || !needAgent()) return
    setMsg(null)
    setModalError('')
    const resp = await run({ action: 'task_archive', task: archiveFor.task_id, reason: archiveReason.trim(), agent: agent.trim() })
    if (!resp.ok) return setModalError(actionError(resp))
    setMsg({ kind: 'ok', text: `已归档 ${archiveFor.task_id}。任务和层包不在总览/流程呈现；可在「已归档任务」里恢复。` })
    setArchiveFor(null); setArchiveReason('')
  }

  const doRestore = async (t: ArchivedTask) => {
    if (!needAgent()) return
    setMsg(null)
    const resp = await run({ action: 'task_archive', task: t.task_id, restore: true, agent: agent.trim() })
    if (!resp.ok) return setMsg({ kind: 'err', text: actionError(resp) })
    setMsg({ kind: 'ok', text: `已恢复 ${t.task_id}。` })
  }

  const doDelete = async () => {
    if (!deleteFor || !needAgent()) return
    setMsg(null)
    setModalError('')
    if (deleteConfirm.trim() !== deleteFor.task_id) {
      return setModalError(`请把任务 ID「${deleteFor.task_id}」原样填进确认框`)
    }
    const resp = await run({ action: 'task_delete', task: deleteFor.task_id, confirm: deleteConfirm.trim(), agent: agent.trim() })
    if (!resp.ok) return setModalError(actionError(resp))
    setMsg({ kind: 'ok', text: `已删除 ${deleteFor.task_id}（层包 ${String(resp.result?.packages ?? 0)} 个）。` })
    setDeleteFor(null); setDeleteConfirm('')
  }

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

      {msg && <ActionNotice kind={msg.kind}>{msg.text}</ActionNotice>}

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
          {live.map((t) => <TaskCard key={t.task_id} t={t}
            onArchive={(x) => { setArchiveFor(x); setArchiveReason(''); setModalError(''); setMsg(null) }} />)}
          {tasks.length > 0 && live.length === 0 && <div className="muted small">没有进行中的任务。</div>}
        </div>
        {ended.length > 0 && (
          <details className="ended">
            <summary>已结束的任务（{ended.length}）</summary>
            <div className="tlist">
              {ended.map((t) => <TaskCard key={t.task_id} t={t}
                onArchive={(x) => { setArchiveFor(x); setArchiveReason(''); setModalError(''); setMsg(null) }} />)}
            </div>
          </details>
        )}
      </section>

      {archived.length > 0 && (
        <section style={{ marginTop: 18 }}>
          <details className="ended">
            <summary>已归档任务（{archived.length}）</summary>
            <div className="tlist">
              {archived.map((a) => (
                <div className="tcard" key={a.task_id}>
                  <div className="tcard-head">
                    <span className="tcard-title">{a.title}</span>
                    <span className="state-chip" style={{ color: 'var(--muted)', borderColor: 'var(--muted)' }}>{a.status}</span>
                    <button className="tf-link" onClick={() => doRestore(a)}>恢复</button>
                    <button className="tf-link" style={{ color: 'var(--danger)' }}
                      onClick={() => { setDeleteFor(a); setDeleteConfirm(''); setModalError(''); setMsg(null) }}>
                      删除
                    </button>
                  </div>
                  <div className="tcard-meta">
                    <span className="mono">{a.task_id}</span>
                    <span>归档于 {a.archived_at || '—'}</span>
                    <span>{a.iteration_count} 轮 · {a.package_count} 个层包</span>
                    {a.archived_reason && <span>原因：{a.archived_reason}</span>}
                  </div>
                </div>
              ))}
            </div>
          </details>
        </section>
      )}

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

      {archiveFor && (
        <div className="modal-mask" onClick={() => setArchiveFor(null)}>
          <div className="task-form" style={{ width: 540 }} onClick={(e) => e.stopPropagation()}>
            <div className="tf-head">
              <h2>归档任务</h2>
              <button className="tf-close" onClick={() => setArchiveFor(null)}>关闭</button>
            </div>
            <p className="tf-sub">
              归档后任务状态冻结：任务和它的层包不再出现在研究总览、流程编排、证据与回溯里；
              随时可以在「已归档任务」里恢复，数据不删。
            </p>
            <div className="cfg-meta" style={{ marginBottom: 8 }}>
              <span className="mono">{archiveFor.task_id}</span>
              <span>{archiveFor.title}</span>
            </div>
            <label className="tf-row tf-block">
              <span>原因（选填）</span>
              <textarea rows={2} value={archiveReason} placeholder="如：这一轮暂停，等新 benchmark"
                onChange={(e) => setArchiveReason(e.target.value)} />
            </label>
            {modalError && <div className="form-error">{modalError}</div>}
            <div className="tf-foot">
              <button className="tf-btn primary" disabled={busy} onClick={doArchive}>确认归档</button>
              <button className="tf-btn" onClick={() => setArchiveFor(null)}>取消</button>
            </div>
          </div>
        </div>
      )}

      {deleteFor && (
        <div className="modal-mask" onClick={() => setDeleteFor(null)}>
          <div className="task-form" style={{ width: 580 }} onClick={(e) => e.stopPropagation()}>
            <div className="tf-head">
              <h2>彻底删除任务</h2>
              <button className="tf-close" onClick={() => setDeleteFor(null)}>关闭</button>
            </div>
            <p className="tf-sub">
              不可恢复。会删除任务记录、它的层包目录，以及附属的决定 / 测试 / 运行 / 结果 / 图边。
              内容寻址的工件对象保留（可能被别的任务共用）。
            </p>
            <div className="notice err">
              任务：<span className="mono">{deleteFor.task_id}</span> · {deleteFor.package_count} 个层包
            </div>
            <label className="tf-row">
              <span>确认任务 ID</span>
              <input className="mono" value={deleteConfirm} placeholder={deleteFor.task_id}
                onChange={(e) => setDeleteConfirm(e.target.value)} />
            </label>
            <div className="form-hint">把任务 ID 原样填进来才会执行删除。</div>
            {modalError && <div className="form-error">{modalError}</div>}
            <div className="tf-foot">
              <button className="tf-btn danger" disabled={busy} onClick={doDelete}>彻底删除</button>
              <button className="tf-btn" onClick={() => setDeleteFor(null)}>取消</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
