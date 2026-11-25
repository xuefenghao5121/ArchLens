import { useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import { humanSize } from '../../status'
import { actionError, useAction } from '../shared/useAction'
import { useOperator } from './useOperator'
import './tasks.css'

/** 新建任务：标题 + 目标 + benchmark + 流程模板（+ 章程署名，选填）。
 *  没有成功判据：怎样算完成只由流程模板决定——本轮每个框都已验证。 */
export default function TaskForm({ onClose, onCreated }: {
  onClose: () => void
  onCreated: (taskId: string, iterationId: string | null) => void
}) {
  const [agent, setAgent] = useOperator()
  const { run, busy } = useAction()
  const [title, setTitle] = useState('')
  const [goal, setGoal] = useState('')
  const [picked, setPicked] = useState<string[]>([])
  const [template, setTemplate] = useState('')
  const [signer, setSigner] = useState('')
  const [formError, setFormError] = useState('')
  // 目标写成了过程要求时：任务照建，但先把提醒摆出来，让人决定继续还是改
  const [created, setCreated] = useState<{ taskId: string; iterationId: string | null; warnings: string[] } | null>(null)
  // 「导入 benchmark」子表单：名字、种类、本机路径（每行一个，文件或目录）
  const [showImport, setShowImport] = useState(false)
  const [bName, setBName] = useState('')
  const [bKind, setBKind] = useState('')
  const [bPaths, setBPaths] = useState('')
  const [importError, setImportError] = useState('')
  const [importOk, setImportOk] = useState('')

  const { data: benchmarks = [], error: bmError } = useQuery({ queryKey: ['benchmarks'], queryFn: api.benchmarks })
  const { data: tpl, error: tplError } = useQuery({ queryKey: ['templates'], queryFn: api.templates })
  const templates = tpl?.templates ?? []
  const templateId = template || tpl?.default || ''
  const current = templates.find((t) => t.id === templateId)
  // 不走层的模板（仅 benchmark）没有层包可建
  const hasLayers = !!current && current.layers.length > 0

  const submitImport = async () => {
    setImportError('')
    setImportOk('')
    const name = bName.trim()
    const paths = bPaths.split('\n').map((s) => s.trim()).filter(Boolean)
    if (!name || !paths.length) { setImportError('导入需要名字和至少一个路径'); return }
    if (!agent.trim()) { setImportError('先填操作人——导入要记名'); return }
    const resp = await run({ action: 'bench_import', name, kind: bKind.trim(), paths, agent: agent.trim() })
    if (!resp.ok) { setImportError(actionError(resp)); return }
    setImportOk(`已导入 ${name}`)
    setPicked((p) => (p.includes(name) ? p : [...p, name]))
    setBPaths('')
  }

  const submit = async () => {
    setFormError('')
    if (!title.trim()) return setFormError('请填写标题')
    if (!goal.trim()) return setFormError('请填写目标')
    if (!picked.length) return setFormError('至少选一个 benchmark（没有就先在下面导入）')
    if (!current) return setFormError('请选择流程模板')
    if (!agent.trim()) return setFormError('请填写操作人——立项要记名')
    const resp = await run({
      action: 'task_new', title: title.trim(), goal: goal.trim(), benchmarks: picked, template: current.id,
      create: hasLayers, as: signer.trim() || undefined, agent: agent.trim(),
    })
    if (!resp.ok) return setFormError(actionError(resp))
    const r = resp.result ?? {}
    const its = (r.iterations as { iteration_id: string }[] | undefined) ?? []
    const warns = (r.warnings as string[] | undefined) ?? []
    if (warns.length) {
      setCreated({ taskId: String(r.task_id), iterationId: its[0]?.iteration_id ?? null, warnings: warns })
      return
    }
    onCreated(String(r.task_id), its[0]?.iteration_id ?? null)
  }

  return (
    <div className="modal-mask" onClick={onClose}>
      {/* 点遮罩关闭，点表单内部不关 */}
      <div className="task-form" onClick={(e) => e.stopPropagation()}>
        <div className="tf-head">
          <h2>新建任务</h2>
          <button className="tf-close" onClick={onClose}>关闭</button>
        </div>
        <p className="tf-sub">
          任务只需要三样：一句目标、在什么负载上做、走哪条流程。目标写原话——之后每一层看到的都是这同一句。
          怎样算完成由流程决定：本轮每个框都已验证。不填「章程署名」则先记为待人决定，批准后任务才开始。
        </p>

        <label className="tf-row">
          <span>标题</span>
          <input value={title} placeholder="这个任务要做什么" onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="tf-row tf-block">
          <span>目标</span>
          <textarea rows={3} value={goal} placeholder="要验证 / 修复 / 搞清楚什么。写原话，不写判据" onChange={(e) => setGoal(e.target.value)} />
        </label>

        <div className="tf-bench">
          <div className="tf-bench-head">
            <strong>benchmark</strong>
            <button className="tf-link" onClick={() => setShowImport((v) => !v)}>
              {showImport ? '收起导入' : '导入 benchmark'}
            </button>
          </div>
          {bmError && <div className="tf-hint">benchmark 清单读取失败：{(bmError as Error).message}</div>}
          {benchmarks.length === 0 && !bmError && <div className="tf-hint">还没有 benchmark，先导入一个。</div>}
          {benchmarks.map((b) => (
            <label key={b.name} className="tf-check">
              <input
                type="checkbox"
                checked={picked.includes(b.name)}
                onChange={(e) => setPicked((p) => (e.target.checked ? [...p, b.name] : p.filter((x) => x !== b.name)))}
              />
              <span className="tf-bench-name">{b.name}</span>
              {b.kind && <span className="tf-bench-kind">{b.kind}</span>}
              <span className="tf-bench-art">
                {b.artifact_refs.map((a) => (a.file_count != null ? `题集 ${a.file_count} 个文件` : humanSize(a.size))).join('、')}
              </span>
            </label>
          ))}
          {showImport && (
            <div className="tf-import">
              <div className="tf-grid">
                <label>名字
                  <input value={bName} placeholder="如 cvdp-agentic-easy" onChange={(e) => setBName(e.target.value)} />
                </label>
                <label>种类 / 说明
                  <input value={bKind} placeholder="如 CVDP agentic、虚拟原型" onChange={(e) => setBKind(e.target.value)} />
                </label>
              </div>
              <label>路径（每行一个；可以是负载文件，也可以是题集目录；须在仓库目录内）
                <textarea rows={2} value={bPaths} placeholder={'benchmarks/cvdp-data/agentic_packets\nfs/user/vp_int4_attn.S'} onChange={(e) => setBPaths(e.target.value)} />
              </label>
              <div className="tf-foot">
                <button className="tf-btn" disabled={busy} onClick={submitImport}>{busy ? '导入中…' : '导入'}</button>
              </div>
              {importError && <div className="form-error">{importError}</div>}
              {importOk && <div className="form-hint">{importOk}（已自动勾选）</div>}
            </div>
          )}
        </div>

        <label className="tf-row">
          <span>流程模板</span>
          <select value={templateId} disabled={!templates.length} onChange={(e) => setTemplate(e.target.value)}>
            {!templates.length && <option value="">（读取中…）</option>}
            {templates.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>
        </label>
        <div className="tf-help">
          {tplError
            ? `模板清单读取失败：${(tplError as Error).message}`
            : current
              ? (hasLayers
                  ? `立项时会按模板为迭代 1 建好每一层的层包（${current.layers.length} 个框）。`
                  : '这个模板不走层：只有目标、benchmark 与人的决定，没有层包。')
              : ''}
        </div>

        <div className="tf-grid" style={{ marginTop: 8 }}>
          <label>操作人
            <input value={agent} placeholder="署名（写入审计记录）" onChange={(e) => setAgent(e.target.value)} />
          </label>
          <label>章程署名（选填）
            <input value={signer} placeholder={agent.trim() ? `如 ${agent.trim()}（填了视为章程已批）` : '填了视为章程已批'} onChange={(e) => setSigner(e.target.value)} />
          </label>
        </div>

        {formError && <div className="form-error">{formError}</div>}
        {created && (
          <div className="tf-import">
            <strong className="small">已立项：{created.taskId}，但有一处要提醒</strong>
            {created.warnings.map((x) => <div key={x} className="tf-hint">{x}</div>)}
            <div className="tf-hint">
              任务已经建好，不会拦。建议把目标改成那个具体问题后另建一个更聚焦的任务；也可以先去流程编排看看。
            </div>
            <div className="tf-foot">
              <button className="tf-btn primary" onClick={() => onCreated(created.taskId, created.iterationId)}>仍然继续</button>
            </div>
          </div>
        )}

        <div className="tf-foot">
          <button className="tf-btn primary" disabled={busy} onClick={submit}>
            {busy ? '提交中…' : '立项'}
          </button>
          <button className="tf-btn" onClick={onClose}>取消</button>
        </div>
      </div>
    </div>
  )
}

/** 带理由的操作表单：开下一轮、结束 / 终止一轮、封板任务共用。
 *  onSubmit 返回错误文案 = 没通过（表单留在原地，内容保留）；返回 null = 成功（表单收起）。 */
export function ReasonForm({ title, hint, verb, signerHint, onSubmit, onCancel }: {
  title: string
  hint?: ReactNode
  verb: string
  // 署名一栏下面的说明（不同操作对署名的要求不一样）
  signerHint?: string
  onSubmit: (v: { reason: string; signer: string }) => Promise<string | null>
  onCancel: () => void
}) {
  const [agent] = useOperator()
  const [reason, setReason] = useState('')
  const [signer, setSigner] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setError('')
    if (!reason.trim()) return setError('请先写明理由')
    if (!agent.trim()) return setError('请先在右上角填写操作人——写操作需要记名')
    if (signer.trim() === 'pending-user') return setError('署名要写真实姓名，不能是 pending-user')
    setBusy(true)
    try {
      const why = await onSubmit({ reason: reason.trim(), signer: signer.trim() })
      if (why) setError(why)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="inline-form">
      <strong className="small">{title}</strong>
      {hint && <div className="form-hint">{hint}</div>}
      <label>理由
        <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      <label>署名（{signer.trim() ? '已填' : `默认 ${agent.trim() || '操作人'}`}）
        <input value={signer} placeholder={agent.trim() || '留空则先用操作人'} onChange={(e) => setSigner(e.target.value)} />
      </label>
      {signerHint && <div className="form-hint">{signerHint}</div>}
      {error && <div className="form-error">{error}</div>}
      <div className="form-btns">
        <button className="tf-btn primary" disabled={busy} onClick={submit}>{busy ? '提交中…' : verb}</button>
        <button className="tf-btn" onClick={onCancel}>取消</button>
      </div>
    </div>
  )
}
