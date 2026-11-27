import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { GateReport, JobEvent, JobInfo, LayerCard, Receipt, StateWord } from '../../types'
import { FRAME_LABEL, LAYER_NAME, TRUTH_LABEL, fmtTime, receiptAction } from '../../status'
import { GIVE_KIND_LABEL, GiveRow, artifactPurpose, giveKind, verdictWord } from '../shared/Give'
import { actionError, useAction } from '../shared/useAction'
import { ActionNotice, PageErr, PageLoad, StateChip } from '../shared/ui'
import { flowHref, layerHref, useWork } from '../tasks/useWork'
import { useOperator } from '../tasks/useOperator'
import { ArtifactViewer } from './ArtifactViewer'
import '../config/config.css'
import './layer.css'

const JOB_STATUS: Record<string, string> = {
  queued: '排队', running: '执行中', passed: '通过', needs_decision: '待人决定',
  gate_failed: '核对未过', error: '出错', stopped: '已停止', max_turns: '模型步数用尽',
}
const JOB_TERMINAL = new Set(['passed', 'needs_decision', 'gate_failed', 'error', 'stopped', 'max_turns'])

function jobEventText(e: JobEvent): string {
  const t = String(e.at ?? '').slice(11, 19)
  const v = (k: string) => String(e[k] ?? '')
  switch (e.event) {
    case 'created': return `${t}  作业建立（${v('package_id')}，自动推进=${e.auto ? '是' : '否'}）`
    case 'dispatch': return `${t}  派发 ${v('package_id')}（${v('layer')}）`
    case 'plan': return `${t}  计划：${v('graph')}｜${JSON.stringify(e.checklist ?? [])}`
    case 'turn': return `${t}  第 ${v('turn')} 步模型调用`
    case 'budget_final': return `${t}  预算最后一步：强制收工总结`
    case 'assistant': return `${t}  思考：${v('text').slice(0, 300)}`
    case 'thought': return `${t}  思考（ReAct）：${v('text').slice(0, 300)}`
    case 'thought_error': return `${t}  思考失败：${v('error').slice(0, 200)}`
    case 'tool_call': return `${t}  行动：${v('name')} ${v('args').slice(0, 240)}`
    case 'tool_result': return `${t}  观察：${v('name')} → ${v('result').slice(0, 300)}`
    case 'finish': return `${t}  收工：${v('summary').slice(0, 300)}`
    case 'final_text': return `${t}  模型结束：${v('text').slice(0, 300)}`
    case 'gate': return `${t}  平台核对：${v('verdict')}`
    case 'chain_next': return `${t}  自动派下一层 ${v('from_package')} → ${v('to_package')}`
    case 'chain_end': return `${t}  链结束：${v('reason')}`
    case 'stopped': return `${t}  已按请求停止`
    case 'error': return `${t}  出错：${v('error')}`
    case 'done': return `${t}  作业结束：${v('status')}`
    default: return `${t}  ${e.event}`
  }
}

type Panel = 'propose' | 'revise' | 'brief' | null
interface Notice { kind: 'ok' | 'err' | 'info'; title: string; report?: GateReport }

/** 核对的详细结论 → 一句人话。状态词与原因在页头，由卡片刷新后给出。 */
function verdictText(v: string): { kind: 'ok' | 'err' | 'info'; title: string } {
  if (v === 'pass') return { kind: 'ok', title: '核对通过：这一层已验证，下游可以用它交出的结论。' }
  if (v === 'needs_decision') return { kind: 'info', title: '只差人的决定：这一层要有人签字（或内容改过之后重签），才能交给下游。' }
  if (v === 'unresolved') return { kind: 'info', title: '还缺验证：还没有登记在册并且通过的测试。' }
  return { kind: 'err', title: '核对没通过。原因见下，改完再核对。' }
}

const strList = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])

// ---- 作业日志的人读投影：一屏看“每步想了什么、做了什么、观察到什么” ----

const TOOL_LABEL: Record<string, string> = {
  wf_brief: '读本层简报', l1_previous: '读上一版 L1', benchmark_list: '看 benchmark 清单',
  case_list: '列案例', case_inspect: '看案例', case_read: '读案例文件',
  environment_read: '读领域环境', wf_register_artifact: '登记工件',
  wf_payload_patch: '写 payload', wf_interface_patch: '写接口卡',
  wf_gate: '跑核对自检', finish: '收工',
}

const baseName = (p?: string) => (p ? p.split('/').pop() ?? p : '')

function parseObj(raw: unknown): Record<string, unknown> | null {
  if (raw && typeof raw === 'object') return raw as Record<string, unknown>
  if (typeof raw !== 'string') return null
  try {
    const v = JSON.parse(raw)
    return v && typeof v === 'object' ? v as Record<string, unknown> : null
  } catch { return null }
}

function toolActionText(name: string, rawArgs: unknown): string {
  const a = parseObj(rawArgs) ?? {}
  switch (name) {
    case 'case_inspect': return `看案例 ${String(a.case ?? '')}`
    case 'case_read': return `读案例文件 ${String(a.path ?? '')}`
    case 'wf_register_artifact': return `登记工件 ${String(a.path ?? '')}`
    case 'wf_payload_patch': {
      const patch = parseObj(a.patch) ?? {}
      return `写 payload（${Object.keys(patch).join(' / ') || '空补丁'}）`
    }
    case 'wf_interface_patch': {
      const iface = parseObj(a.interface ?? a.patch) ?? {}
      const ids = Array.isArray(iface.gives)
        ? iface.gives.map((g) => (parseObj(g)?.id as string) ?? '').filter(Boolean).join('、')
        : ''
      return `写接口卡${ids ? `（${ids}）` : ''}`
    }
    default: return TOOL_LABEL[name] ?? name
  }
}

function toolObservationText(name: string, raw: unknown): string {
  const r = parseObj(raw) ?? {}
  if (r.error) {
    const details = Array.isArray(r.details) ? `：${r.details.join('；')}` : ''
    return `失败：${String(r.error)}${details}`
  }
  if (r.verdict) {
    const blocks = Array.isArray(r.blocks) && r.blocks.length ? `；阻塞：${r.blocks.join('；')}` : ''
    return `核对结果：${verdictWord(String(r.verdict))}${blocks}`
  }
  if (r.artifact_id) return `登记为 ${String(r.artifact_id)}（${baseName(String(r.relpath ?? ''))}）`
  if (name === 'wf_payload_patch' && r.ok) {
    const keys = Array.isArray(r.payload_keys) ? r.payload_keys.join(' / ') : ''
    return `已写入${keys ? `：${keys}` : ''}`
  }
  if (name === 'wf_interface_patch' && r.ok) return '接口卡已写入'
  if (name === 'wf_brief' && r.brief) return '拿到开工简报'
  if (Array.isArray(r.cases)) return `共 ${r.cases.length} 个案例`
  if (Array.isArray(r.files)) return `案例文件：${r.files.slice(0, 5).join('、')}${r.files.length > 5 ? '…' : ''}`
  if (Array.isArray(r.benchmarks)) return `共 ${r.benchmarks.length} 个 benchmark`
  if (typeof r.text === 'string') return `读到 ${r.text.length} 字`
  if (r.ok) return '完成'
  return String(raw ?? '').slice(0, 120)
}

interface JobAction { name: string; action: string; observation: string }
interface JobStep { turn: number; thoughts: string[]; actions: JobAction[]; notes: string[] }
interface JobTimelineData { plan: string; notes: string[]; steps: JobStep[] }

function buildJobTimeline(log: JobEvent[]): JobTimelineData {
  const steps: JobStep[] = []
  const notes: string[] = []
  let plan = ''
  let cur: JobStep = { turn: 0, thoughts: [], actions: [], notes: [] }
  const flush = () => {
    if (cur.turn || cur.thoughts.length || cur.actions.length || cur.notes.length) steps.push(cur)
  }
  const thought = (text: string) => {
    const t = text.trim()
    if (t && !cur.thoughts.includes(t)) cur.thoughts.push(t)
  }
  for (const e of log) {
    const v = (k: string) => String(e[k] ?? '')
    switch (e.event) {
      case 'plan': plan = v('graph'); break
      case 'experience_loaded': notes.push(`已读取上一轮作业经验：${Array.isArray(e.jobs) ? e.jobs.join('、') : ''}`); break
      case 'turn':
        flush()
        cur = { turn: Number(e.turn ?? 0), thoughts: [], actions: [], notes: [] }
        break
      case 'thought': thought(v('text')); break
      case 'assistant': thought(v('text')); break
      case 'tool_call':
        cur.actions.push({ name: v('name'), action: toolActionText(v('name'), e.args), observation: '' })
        break
      case 'tool_result': {
        const a = [...cur.actions].reverse().find((x) => x.name === v('name') && !x.observation)
        if (a) a.observation = toolObservationText(v('name'), e.result)
        break
      }
      case 'finish': cur.notes.push('收工'); break
      case 'gate': cur.notes.push(`平台核对：${verdictWord(v('verdict'))}`); break
      case 'error': cur.notes.push(`出错：${v('error')}`); break
      case 'budget_final': cur.notes.push('预算最后一步：强制收工'); break
      case 'chain_next': notes.push(`自动派下一层：${v('from_package')} → ${v('to_package')}`); break
      case 'chain_end': notes.push(`链结束：${v('reason')}`); break
      case 'stopped': notes.push('已按请求停止'); break
      case 'done': notes.push(`作业结束：${JOB_STATUS[v('status')] ?? v('status')}`); break
      default: break
    }
  }
  flush()
  return { plan, notes, steps }
}

function JobTimeline({ job, onJump }: { job: JobInfo; onJump: () => void }) {
  const t = buildJobTimeline(job.log ?? [])
  const shown = t.steps.slice(-8)
  const [fullSummary, setFullSummary] = useState(false)
  const summary = job.summary ?? ''
  const summaryShown = fullSummary || summary.length <= 360 ? summary : `${summary.slice(0, 360)}…`
  return (
    <div className="job-result">
      <div className="job-result-head">
        <span>本层结果</span>
        <b>{JOB_STATUS[job.status] ?? job.status}</b>
        <span className="muted small">
          {job.turns} 步模型调用 · {t.steps.length} 个执行步
          {job.gate_verdict ? ` · 核对 ${verdictWord(job.gate_verdict)}` : ''}
        </span>
        <button className="tf-link" onClick={onJump}>查看下方交付</button>
      </div>
      {summary && (
        <div className="job-summary">
          {summaryShown}
          {summary.length > 360 && (
            <button className="tf-link job-summary-more" onClick={() => setFullSummary((x) => !x)}>
              {fullSummary ? '收起' : '展开全文'}
            </button>
          )}
        </div>
      )}
      {t.plan && <div className="cfg-hint">图：{t.plan}</div>}
      <div className="job-timeline">
        {shown.length === 0 && <div className="muted small">（还没有可读的执行步骤）</div>}
        {shown.length < t.steps.length && (
          <div className="muted small">只显示最近 8 步，共 {t.steps.length} 步；更早的步骤在原始日志里。</div>
        )}
        {shown.map((s, i) => (
          <div className="job-step" key={`${s.turn}-${i}`}>
            <div className="job-step-head">第 {s.turn || '?'} 步</div>
            {s.thoughts.map((x, j) => (
              <div className="job-thought" key={j}>思考：{x}</div>
            ))}
            {s.actions.map((a, j) => (
              <div className="job-action" key={j}>
                <span className="job-act">行动：{a.action}</span>
                {a.observation && <span className="job-obs">观察：{a.observation}</span>}
              </div>
            ))}
            {s.notes.map((x, j) => <div className="job-note" key={j}>{x}</div>)}
          </div>
        ))}
      </div>
      {t.notes.length > 0 && <div className="job-notes">{t.notes.join('；')}</div>}
      <details className="job-raw">
        <summary>原始日志（技术细节）</summary>
        <pre className="cfg-pre">
          {(job.log ?? []).filter((e) => e.event !== 'graph_node').slice(-40)
            .map(jobEventText).join('\n') || '（还没有日志）'}
        </pre>
      </details>
    </div>
  )
}


/** 人的决定窗口：待人决定的事项在这里批准或驳回；也可以直接记一个署名的决定 */
function DecisionWindow({ pkg, card, onNotice }: { pkg: string; card: LayerCard; onNotice: (n: Notice) => void }) {
  const [agent] = useOperator()
  const { run, busy } = useAction()
  const [deciding, setDeciding] = useState(false)
  const [summary, setSummary] = useState('')
  const [rationale, setRationale] = useState('')
  const [signer, setSigner] = useState('')
  const [rejecting, setRejecting] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const [error, setError] = useState('')
  const [more, setMore] = useState(false)

  const pending = card.receipts.filter((r) => r.pending)
  const signed = card.receipts.filter((r) => !r.pending)
  const hasValidSign = signed.some((r) => !r.stale)
  const shown = more ? signed : signed.slice(0, 4)

  const approve = async (r: Receipt) => {
    setError('')
    if (!agent.trim()) return setError('请先在右上角填写操作人——批准要署名')
    const resp = await run({ action: 'approve', dec: r.decision_id, as: agent.trim(), agent: agent.trim() })
    if (!resp.ok) return setError(actionError(resp))
    onNotice({ kind: 'ok', title: `已由 ${agent.trim()} 批准：「${r.summary}」。` })
  }
  const reject = async (r: Receipt) => {
    setError('')
    if (!reason.trim()) return setError('驳回要写明理由')
    if (!agent.trim()) return setError('请先在右上角填写操作人——驳回要署名')
    const resp = await run({ action: 'reject', dec: r.decision_id, reason: reason.trim(), as: agent.trim(), agent: agent.trim() })
    if (!resp.ok) return setError(actionError(resp))
    setRejecting(null)
    setReason('')
    onNotice({ kind: 'ok', title: `已由 ${agent.trim()} 驳回：「${r.summary}」。` })
  }
  const decide = async () => {
    setError('')
    const who = signer.trim() || agent.trim()
    if (!summary.trim()) return setError('请用一句话写明决定了什么')
    if (!who) return setError('请先填写署名（或右上角的操作人）——决定要留名')
    if (who === 'pending-user') return setError('署名要写真实姓名，不能是 pending-user')
    const resp = await run({ action: 'decide', pkg, summary: summary.trim(), rationale: rationale.trim(), as: who, agent: agent.trim() || who })
    if (!resp.ok) return setError(actionError(resp))
    setDeciding(false)
    setSummary('')
    setRationale('')
    onNotice({ kind: 'ok', title: `已记下 ${who} 的决定：「${summary.trim()}」。` })
  }
  const recheck = async () => {
    setError('')
    if (!agent.trim()) return setError('请先在右上角填写操作人——核对要署名')
    const resp = await run({ action: 'validate', pkg, agent: agent.trim() })
    if (!resp.ok) return setError(actionError(resp))
    const result = (resp.result ?? {}) as { signoff_decision_id?: string }
    onNotice({
      kind: 'info',
      title: result.signoff_decision_id
        ? `已按当前内容重新核对；新的待签事项是 ${result.signoff_decision_id}，请刷新后批准。`
        : '已按当前内容重新核对。',
    })
  }

  return (
    <section className="block ld-dec" id="decisions">
      <div className="block-title">
        <strong>人的决定</strong>
        <span className="note">信息交给下游之前，在这里签字</span>
      </div>
      {card.receipts.length === 0 && (
        <div className="muted small">
          没有记录。{card.guide.human ? '这一层需要人签字之后才能交给下游。' : '这一层不要求人签字。'}
        </div>
      )}
      <div className="receipts">
        {pending.map((r) => (
          <div key={r.decision_id} className={'receipt pending' + (r.stale ? ' stale' : '')}>
            待人决定：{r.summary || '（没有写一句话结论）'}
            {r.stale && <span className="receipt-flags">内容已改，这条待签只对应旧内容，不能直接批准</span>}
            <span className="receipt-btns">
              {r.stale
                ? <button className="tf-btn mini primary" disabled={busy} onClick={recheck}>重新核对生成新待签</button>
                : <button className="tf-btn mini" disabled={busy} onClick={() => approve(r)}>批准</button>}
              <button className="tf-btn mini danger" disabled={busy}
                onClick={() => { setRejecting(r.decision_id); setReason(''); setError('') }}>驳回</button>
            </span>
            {rejecting === r.decision_id && (
              <div className="inline-form">
                <label>驳回理由
                  <input value={reason} onChange={(e) => setReason(e.target.value)} />
                </label>
                <div className="form-btns">
                  <button className="tf-btn mini danger" disabled={busy} onClick={() => reject(r)}>确认驳回</button>
                  <button className="tf-btn mini" onClick={() => setRejecting(null)}>取消</button>
                </div>
              </div>
            )}
          </div>
        ))}
        {shown.map((r) => (
          <div key={r.decision_id} className={'receipt signed' + (r.stale ? ' stale' : '')}>
            <span className="receipt-at">{fmtTime(r.at)}</span>
            <span className="receipt-by">{r.by}</span>
            {receiptAction(r)}
            {r.stale && (
              <>
                {/* 已经有更新的有效签字时，过期的回执只留痕，不再催重签 */}
                <span className="receipt-flags">{hasValidSign ? '内容在其后改过' : '内容在其后改过，需要重签'}</span>
                {!hasValidSign && (
                  <span className="receipt-btns">
                    <button className="tf-btn mini" disabled={busy} onClick={recheck}>重新核对</button>
                  </span>
                )}
              </>
            )}
          </div>
        ))}
      </div>
      {/* 批准 / 驳回 / 记决定失败的原因：回执列表下面，离出错的那一行最近 */}
      {error && <div className="form-error" style={{ marginTop: 6 }}>{error}</div>}
      {signed.length > 4 && (
        <button className="tf-link muted" style={{ marginTop: 6 }} onClick={() => setMore((v) => !v)}>
          {more ? '收起更早的记录' : `再看更早的 ${signed.length - 4} 条`}
        </button>
      )}

      {!deciding && (
        <div style={{ marginTop: 10 }}>
          <button className="tf-btn mini" onClick={() => { setSummary(''); setRationale(''); setError(''); setDeciding(true) }}>
            记一个署名的决定
          </button>
        </div>
      )}
      {deciding && (
        <div className="inline-form">
          <label>决定了什么（一句话）
            <input value={summary} maxLength={80} placeholder="如：基线按虚拟原型口径拍定" onChange={(e) => setSummary(e.target.value)} />
          </label>
          <label>详细依据（可省略）
            <textarea rows={2} value={rationale} onChange={(e) => setRationale(e.target.value)} />
          </label>
          <label>署名
            <input value={signer} placeholder={agent.trim() || '写你的名字'} onChange={(e) => setSigner(e.target.value)} />
          </label>
          <div className="form-hint">回执会记下此刻接口卡的内容指纹。之后内容再改，这条回执就显示为过期，需要重签。</div>
          <div className="form-btns">
            <button className="tf-btn primary mini" disabled={busy} onClick={decide}>记下决定</button>
            <button className="tf-btn mini" onClick={() => setDeciding(false)}>取消</button>
          </div>
        </div>
      )}
    </section>
  )
}

function Detail({ pkg }: { pkg: string }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['package', pkg], queryFn: () => api.package(pkg) })
  const { data: guide } = useQuery({ queryKey: ['guide'], queryFn: api.guide })
  const { state } = useWork()
  const [agent] = useOperator()
  const { run, busy } = useAction()
  const nav = useNavigate()
  const [panel, setPanel] = useState<Panel>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [formError, setFormError] = useState('')
  // 提请决定
  const [summary, setSummary] = useState('')
  const [rationale, setRationale] = useState('')
  // 作废并重做：先预览影响范围，再确认
  const [reviseReason, setReviseReason] = useState('')
  const [preview, setPreview] = useState<string[] | null>(null)
  const [brief, setBrief] = useState('')
  // 平台内部运行：派给本层 agent，作业完成后平台自己核对
  const [autoDispatch, setAutoDispatch] = useState(true)
  const [jobId, setJobId] = useState('')
  const [runtimeMsg, setRuntimeMsg] = useState<{ kind: 'ok' | 'err' | 'info'; text: string } | null>(null)
  const [decisionsOpen, setDecisionsOpen] = useState(false)
  const [artifactOpen, setArtifactOpen] = useState('')
  const { data: runtime } = useQuery({ queryKey: ['runtime'], queryFn: api.runtime })
  const { data: jobsData } = useQuery({
    queryKey: ['jobs'], queryFn: api.jobs, refetchInterval: 5000,
  })
  const { data: job } = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.job(jobId),
    enabled: !!jobId,
    refetchInterval: jobId ? 2000 : false,
  })

  useEffect(() => {
    setPanel(null); setNotice(null); setFormError(''); setPreview(null); setBrief('')
    setJobId(''); setRuntimeMsg(null); setArtifactOpen('')
  }, [pkg])

  // 离开页面再回来时，jobId 是组件局部状态；从后台作业列表里接回这一包最近的作业。
  useEffect(() => {
    if (jobId || !jobsData) return
    const j = jobsData.jobs.find((x) => x.package_id === pkg)
    if (j) setJobId(j.job_id)
  }, [jobsData, pkg, jobId])

  if (isLoading) return <PageLoad />
  if (error || !data) return <PageErr error={error ?? new Error('没有取到这一层的数据')} />

  const c = data.card
  const g = c.guide
  const iface = c.interface
  const invalidated = c.state.lifecycle === 'invalidated' || c.state.lifecycle === 'superseded' || c.state.word === '已作废'
  const truth = guide?.truth ?? TRUTH_LABEL
  const frames = guide?.frames ?? FRAME_LABEL
  const ev = new Map(c.evidence.map((e) => [e.id, e]))
  const taskTitle = state?.tasks.find((t) => t.task_id === c.task_id)?.title ?? ''
  const taskPkgs = new Map<string, { layer: string; state: StateWord }>()
  for (const grp of Object.values(state?.layers ?? {})) {
    for (const p of grp.packages) taskPkgs.set(p.package_id, { layer: p.card.layer, state: p.state })
  }
  const signoffDraft = () => {
    const q = String(c.interface.question || c.title || '').trim()
    const g = String(c.interface.gives?.[0]?.text || '').trim()
    return `确认${c.layer} ${c.layer_name}：${g || q}`.slice(0, 80)
  }
  const open = (p: Panel) => {
    setNotice(null); setFormError(''); setPreview(null)
    // 补记决定是「结果之后」的动作：打开时用本层已产出的接口卡预填一句话
    if (p === 'propose' && !summary.trim()) setSummary(signoffDraft())
    setPanel(p)
  }
  const needAgent = () => {
    if (agent.trim()) return true
    setFormError('请先在右上角填写操作人——写操作需要记名')
    return false
  }

  const onDispatch = async () => {
    setRuntimeMsg(null)
    setNotice(null)
    if (!needAgent()) return
    if (!runtime?.config.configured) {
      return setRuntimeMsg({ kind: 'err', text: '运行时还没配置模型：去 Agent 管理 → 运行时填 base_url / model / api_key' })
    }
    const who = c.guide.agent?.name ?? c.layer
    if (!window.confirm(`平台会以「${who}」的身份在仓库里执行工具（bash、读写文件、wf）。\n\n只做本层；跑完平台会自动核对。确认开工？`)) return
    const resp = await run({ action: 'dispatch', pkg, auto: autoDispatch, agent: agent.trim() })
    if (!resp.ok) return setRuntimeMsg({ kind: 'err', text: actionError(resp) })
    const j = resp.result as unknown as JobInfo
    setJobId(j.job_id)
    setRuntimeMsg({ kind: 'info', text: `已派发 ${j.job_id}。平台在后台执行本层 agent，下面是实时日志。` })
  }

  const onStop = async () => {
    if (!jobId) return
    setRuntimeMsg(null)
    if (!agent.trim()) return setRuntimeMsg({ kind: 'err', text: '请先填写操作人' })
    const resp = await run({ action: 'stop_job', job: jobId, agent: agent.trim() })
    if (!resp.ok) return setRuntimeMsg({ kind: 'err', text: actionError(resp) })
    setRuntimeMsg({ kind: 'info', text: '已请求停止：当前工具调用结束后停下。' })
  }

  const onRedo = async () => {
    setFormError('')
    setNotice(null)
    if (!agent.trim()) return setFormError('请先在右上角填写操作人——重做要记名')
    if (!window.confirm('会新建一个同层的新包（旧包保留作审计），归到同一个任务/迭代。继续？')) return
    const resp = await run({ action: 'redo_layer', pkg, agent: agent.trim() })
    if (!resp.ok) return setFormError(actionError(resp))
    nav(layerHref(String(resp.result?.package_id ?? '')))
  }

  const onCheck = async () => {
    open(null)
    if (!needAgent()) return
    const resp = await run({ action: 'validate', pkg, agent: agent.trim() })
    if (!resp.ok) return setNotice({ kind: 'err', title: actionError(resp) })
    const v = String(resp.result?.verdict ?? '')
    const rep = resp.result?.report as Partial<GateReport> | undefined
    const base = verdictText(v)
    let { kind, title } = base
    if (v === 'pass') {
      // 核对通过 ≠ 这一层已验证：还有事等人决定时要说清楚
      const fresh = await api.package(pkg).catch(() => null)
      const pend = fresh?.card.receipts.filter((r) => r.pending).length ?? 0
      if (pend) {
        kind = 'info'
        title = `核对通过。但还有 ${pend} 件事等人决定——在下面「人的决定」里处理完，这一层才算已验证。`
      }
    }
    setNotice({
      kind, title,
      report: rep ? { ...rep, blocks: rep.blocks ?? [], warns: rep.warns ?? [] } as GateReport : undefined,
    })
  }

  const onReport = async () => {
    if (!needAgent()) return
    const resp = await run({ action: 'layer_report', pkg, agent: agent.trim() })
    if (!resp.ok) return setNotice({ kind: 'err', title: actionError(resp) })
    const path = String(resp.result?.path ?? '')
    setNotice({ kind: 'ok', title: `已生成 L2 归因报告${path ? `：${path}` : ''}` })
  }

  const onPropose = async () => {
    setFormError('')
    if (!summary.trim()) return setFormError('请用一句话写明决定了什么')
    if (!needAgent()) return
    const text = summary.trim()
    const resp = await run({ action: 'propose', pkg, summary: text, rationale: rationale.trim(), agent: agent.trim() })
    if (!resp.ok) return setFormError(actionError(resp))
    setPanel(null); setSummary(''); setRationale('')
    setNotice({ kind: 'ok', title: `已补记：「${text}」。它现在是待批事项，等人批准。` })
  }

  const onPreview = async () => {
    setFormError('')
    if (!reviseReason.trim()) return setFormError('请先写明作废的原因')
    if (!needAgent()) return
    const resp = await run({ action: 'revise', pkg, reason: reviseReason.trim(), dry_run: true, agent: agent.trim() })
    if (!resp.ok) return setFormError(actionError(resp))
    setPreview(strList(resp.result?.affected))
  }
  const onRevise = async () => {
    if (preview === null) return
    if (!window.confirm(`将作废本包和下游共 ${preview.length} 个包，界面上没有撤销。确定执行？`)) return
    const why = reviseReason.trim()
    const resp = await run({ action: 'revise', pkg, reason: why, agent: agent.trim() })
    if (!resp.ok) return setFormError(actionError(resp))
    const n = strList(resp.result?.affected).length
    setPanel(null); setReviseReason(''); setPreview(null)
    setNotice({ kind: 'ok', title: `已作废 ${n} 个包（含本包）。原因已留痕：${why}` })
  }

  const onArchiveBrief = async () => {
    setNotice(null)
    if (!agent.trim()) { setFormError('请先在右上角填写操作人——留档要署名'); return }
    const resp = await run({ action: 'brief_out', pkg, by: agent.trim(), agent: agent.trim() })
    if (!resp.ok) { setFormError(actionError(resp)); return }
    setFormError('')
    setNotice({ kind: 'ok', title: `简报已留档（指纹 ${String(resp.result?.sha256 ?? '')}）` })
  }

  const onBrief = async () => {
    if (panel === 'brief') { setPanel(null); return }
    open('brief')
    const resp = await api.action({ action: 'ask', pkg })
    if (resp.ok && typeof resp.result?.brief === 'string') setBrief(resp.result.brief)
    else setFormError(resp.ok ? '没有取到简报' : actionError(resp))
  }

  const takes = iface.takes ?? []
  const upstream = takes.length
    ? takes.map((t) => ({
        id: t.from,
        layer: taskPkgs.get(t.from)?.layer ?? '',
        text: t.statement ?? '',
      }))
    : c.parents.map((id) => ({ id, layer: taskPkgs.get(id)?.layer ?? '', text: '' }))
  const lastRun = c.runs[c.runs.length - 1]
  const tests = c.evidence.filter((e) => e.kind === 'test')
  const arts = c.evidence.filter((e) => e.kind === 'artifact')
  const gives = iface.gives ?? []
  const giveGroups = (() => {
    const order = ['root_cause', 'measurement', 'exclusion', 'scope', 'decision', 'other'] as const
    const m = new Map<string, { key: typeof order[number]; items: typeof gives }>()
    for (const g of gives) {
      const k = giveKind(g)
      if (!m.has(k)) m.set(k, { key: k, items: [] })
      m.get(k)!.items.push(g)
    }
    return order.filter((k) => m.has(k)).map((k) => m.get(k)!)
  })()
  const pendingCount = c.receipts.filter((r) => r.pending).length
  const applicableChecks = c.checks.filter((ch) => ch.ok !== null)
  const passedChecks = applicableChecks.filter((ch) => ch.ok).length
  const checkWord = c.checked_at
    ? (c.state.verdict === 'pass' ? '通过' : c.state.verdict === 'needs_decision' ? '只差签字' : '未通过')
    : '未核对'
  const nextInputs = (iface.open ?? []).filter((q) => q.ask !== 'human')
  const humanInputs = (iface.open ?? []).filter((q) => q.ask === 'human')
  const citedIds = new Set<string>()
  for (const give of gives) for (const id of give.evidence ?? []) citedIds.add(id)
  const withEvidence = gives.filter((g) => (g.evidence ?? []).length > 0)
  const withoutEvidence = gives.filter((g) => (g.evidence ?? []).length === 0)
  const citedTests = tests.filter((t) => (t.for_statement ?? []).some((sid) => gives.some((g) => g.id === sid)))
  const otherTests = tests.filter((t) => !citedTests.includes(t))
  const uncitedArts = arts.filter((a) => !citedIds.has(a.id))
  const raw = data.raw

  return (
    <div className="ld">
      <div className="ld-top">
        <Link className="back" to={flowHref(c.task_id, c.iteration_id)}>‹ 返回流程编排</Link>
        <div className="head-actions">
          {invalidated ? (
            <>
              <span className="muted small">本包已作废：不再提供核对 / 签字 / 派发；作废前的内容只在下方原始记录里</span>
              <button className="tf-btn primary" disabled={busy} onClick={onRedo}>重做本层</button>
            </>
          ) : (
            <>
              <button className="tf-btn primary" disabled={busy} onClick={onCheck}>核对</button>
              <button className="tf-btn" disabled={busy || c.state.word === '未开始'}
                title={c.state.word === '未开始'
                  ? '本层还没有产出；开工前的约束请写进任务目标或迭代理由'
                  : '把已经发生/已经产出的选择补成记录；不是预先批准'}
                onClick={() => open(panel === 'propose' ? null : 'propose')}>补记决定</button>
              <button className="tf-btn" onClick={() => setDecisionsOpen(true)}>
                人的决定{pendingCount > 0 ? `（${pendingCount}）` : ''}
              </button>
              <button className="tf-btn danger" disabled={busy} onClick={() => open(panel === 'revise' ? null : 'revise')}>作废并重做</button>
              <button className="tf-btn" onClick={onBrief}>{panel === 'brief' ? '收起简报' : '开工简报'}</button>
              <button className="tf-btn" disabled={busy} onClick={onArchiveBrief}>留档</button>
            </>
          )}
        </div>
      </div>

      <header className="ld-head">
        <div className="ld-title">
          <span className="ld-layer">{c.layer} {c.layer_name}</span>
          <StateChip word={c.state.word} />
          <h1>{c.title}</h1>
        </div>
        <div className="ld-why">{c.state.word}——{c.state.why}</div>
        <div className="ld-meta">
          {c.iteration_label && <span>{c.iteration_label}</span>}
          {taskTitle && <span>任务：{taskTitle}</span>}
          {c.owner && <span>负责：{c.owner}</span>}
          {c.context.brief
            ? <span>开工简报：{fmtTime(c.context.brief.at)}（{c.context.brief.by}）</span>
            : <span>开工简报：还没有留档</span>}
          {c.context.pulls.length > 0 && <span>另读上游 {c.context.pulls.length} 处</span>}
          {lastRun && (
            <span>
              上次执行：{lastRun.agent}
              {lastRun.model ? ` · ${lastRun.model}` : ''}
              {lastRun.duration_s != null ? ` · ${lastRun.duration_s}s` : ''}
            </span>
          )}
          <span className="mono">{c.package_id}</span>
        </div>
      </header>

      {invalidated && (
        <ActionNotice kind="err">
          <strong>本包已作废</strong>
          <div>{c.state.why}</div>
          <div style={{ marginTop: 4 }}>
            作废前的交付、结论与证据不再在页面上呈现，也不再参与核对或下游引用；
            需要审计时展开最下方的「原始记录」。点右上角「重做本层」会新建一个同层的新包，旧包保留作审计。
          </div>
        </ActionNotice>
      )}

      {!invalidated && (
        <details className="checks-compact">
          <summary>
            核对：{checkWord} · {passedChecks}/{applicableChecks.length} 项通过
            <span className="muted"> · 上次 {c.checked_at ? fmtTime(c.checked_at) : '从未'}（展开看逐项）</span>
          </summary>
          <div className="checks">
            {c.checks.map((ch) => (
              <div key={ch.id} className="check">
                <span className={'check-mark ' + (ch.ok === true ? 'ok' : ch.ok === false ? 'bad' : 'na')}>{ch.mark}</span>
                <span className="check-label">{ch.label}</span>
                <span>{ch.detail}</span>
              </div>
            ))}
          </div>
        </details>
      )}

      {formError && <ActionNotice kind="err">{formError}</ActionNotice>}
      {notice && (
        <ActionNotice kind={notice.kind}>
          <div>{notice.title}</div>
          {notice.report && (notice.report.blocks.length > 0 || notice.report.warns.length > 0) && (
            <details>
              <summary>核对的详细结果</summary>
              {notice.report.blocks.length > 0 && <ul>{notice.report.blocks.map((b) => <li key={b} className="why">{b}</li>)}</ul>}
              {notice.report.warns.length > 0 && <ul>{notice.report.warns.map((w) => <li key={w} className="soft">{w}</li>)}</ul>}
            </details>
          )}
        </ActionNotice>
      )}

      <div className="runtime-bar">
        <strong>平台内部运行</strong>
        {invalidated ? (
          <span className="muted small">本包已作废，不能派发</span>
        ) : !runtime?.config.configured ? (
          <span className="muted small">
            未配置模型，<Link to="/agents">去 Agent 管理 → 运行时</Link> 填 base_url / model / api_key
          </span>
        ) : (
          <>
            <span className="muted small">模型 <span className="mono">{runtime.config.model}</span></span>
            <span className="muted small">引擎 {runtime.config.engine === 'langgraph' ? 'langgraph（L1–L8 领域工具）' : 'v1'}</span>
            <label className="cfg-inline">
              <input type="checkbox" checked={autoDispatch} onChange={(e) => setAutoDispatch(e.target.checked)} />
              核对通过后自动派下一个有包的框（遇到待人决定就停）
            </label>
            <button className="tf-btn primary mini" disabled={busy || (!!job && !JOB_TERMINAL.has(job.status))} onClick={onDispatch}
              title="开工前只检查包、上游与运行时；核对是 agent 收工后的检查，缺交付由 agent 补">
              派给 agent 开工
            </button>
            {job && !JOB_TERMINAL.has(job.status) && (
              <button className="tf-btn danger mini" disabled={busy} onClick={onStop}>停止</button>
            )}
            {runtime.active_job && runtime.active_job.package_id !== pkg && (
              <span className="muted small">另一个作业在跑：{runtime.active_job.job_id}</span>
            )}
          </>
        )}
      </div>
      {!invalidated && c.state.word === '未开始' && (
        <div className="cfg-hint" style={{ margin: '-4px 0 10px' }}>
          核对是 agent 收工后的检查；开工前缺的交付由 agent 补。只有运行时未配置、包已作废、上游未验证
          这类前置问题才需要先处理。
        </div>
      )}
      {runtimeMsg && <ActionNotice kind={runtimeMsg.kind}>{runtimeMsg.text}</ActionNotice>}
      {job && !invalidated && (
        <div className="runtime-job">
          <div className="cfg-meta">
            <span>状态 <b>{JOB_STATUS[job.status] ?? job.status}</b></span>
            {job.node && <span>节点 {job.node}</span>}
            {job.engine === 'langgraph' && <span>langgraph</span>}
            <span>{job.turns} 步模型调用</span>
            {job.gate_verdict && <span>核对 {job.gate_verdict}</span>}
            {job.signoff_decision_id && <span>待签 <span className="mono">{job.signoff_decision_id}</span></span>}
            {job.duration_s != null && <span>{job.duration_s}s</span>}
            <span className="mono">{job.job_id}</span>
          </div>
          <div className="cfg-hint">
            一次派发是一个作业；“步”是模型调用/工具步，不是平台迭代。下面是每步的思考、行动和观察，
            原始日志收在折叠里。
          </div>
          <JobTimeline
            job={job}
            onJump={() => document.getElementById('ld-delivery')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
          />
          {job.error && <div className="form-error">{job.error}</div>}
        </div>
      )}

      {panel === 'propose' && (
        <div className="inline-form">
          <strong className="small">补记一个决定</strong>
          <div className="form-hint">
            这里是把已经发生、或本层已经产出的选择补成记录，不是预先批准未知的工作。
            本层结果出来后点「核对」，平台会按产出自动生成待签事项；开工前的约束请写进任务目标或迭代理由。
          </div>
          <label>决定了什么（一句话）
            <input value={summary} maxLength={80} placeholder="如：基线按虚拟原型口径拍定" onChange={(e) => setSummary(e.target.value)} />
          </label>
          <label>详细依据（可省略）
            <textarea rows={2} value={rationale} onChange={(e) => setRationale(e.target.value)} />
          </label>
          <div className="form-btns">
            <button className="tf-btn primary mini" disabled={busy} onClick={onPropose}>记下</button>
            <button className="tf-btn mini" onClick={() => setPanel(null)}>取消</button>
          </div>
        </div>
      )}

      {panel === 'revise' && (
        <div className="inline-form">
          <strong className="small">作废并重做</strong>
          <div className="form-hint">本包和它下游的包都会作废（下游用到的结论不再成立）。作废之后重新建包、重做。</div>
          <label>作废的原因
            <textarea rows={2} value={reviseReason} onChange={(e) => { setReviseReason(e.target.value); setPreview(null) }} />
          </label>
          <div className="form-btns">
            <button className="tf-btn mini" disabled={busy} onClick={onPreview}>看会影响哪些包</button>
            <button className="tf-btn danger mini" disabled={busy || preview === null} onClick={onRevise}>
              确认作废{preview ? `（${preview.length} 个包）` : ''}
            </button>
            <button className="tf-btn mini" onClick={() => setPanel(null)}>取消</button>
          </div>
          {preview !== null && (
            <div className="form-hint">
              将作废 {preview.length} 个包（含本包）：
              <div className="mono small" style={{ maxHeight: 160, overflow: 'auto' }}>{preview.join('\n')}</div>
            </div>
          )}
        </div>
      )}

      {panel === 'brief' && (
        <div className="inline-form">
          <strong className="small">给这一层 agent 的工作简报</strong>
          <div className="form-hint">
            给这一层 agent 的全部内容：任务目标原话、这一层要交什么、上游卡片上的结论和证据、接口卡的写法。
            「留档」会把它写成这个包目录下的 brief.md，并记下时间、署名、指纹与所含上游；之后就能查到这一层开工时拿到了什么。
          </div>
          {brief
            ? (
              <>
                <pre className="brief">{brief}</pre>
                <div className="form-btns">
                  <button className="tf-btn mini" onClick={() => navigator.clipboard?.writeText(brief)}>复制</button>
                </div>
              </>
            )
            : <div className="form-hint">生成中…</div>}
        </div>
      )}

      <div className={'ld-grid' + (invalidated ? ' invalidated' : '')}>
        {/* 目标：只留目标原话与本层问题；角色/skills/tools 在 Agent 管理里 */}
        <section className="block ld-goal">
          <div className="block-title"><strong>目标</strong><span className="note">不变量：每一层看到的都是这同一句</span></div>
          <div className="goal-text">{c.goal || '（这个包没有归到任务，没有目标原话）'}</div>
          <div className="block-k">这一层要回答的问题</div>
          <div className="qa">{iface.question || g.question}</div>
          <div className="cfg-hint" style={{ marginTop: 12 }}>
            谁做这一层、用什么 skills / tools、可写范围与禁区，在 <Link to="/agents">Agent 管理</Link> 里维护；
            这里不重复展示。
          </div>
        </section>

        {/* 交付 */}
        {/* 交付/证据：作废后不再呈现，只在原始记录里留档 */}
        {!invalidated && (
          <>
        <section className="block ld-out" id="ld-delivery">
          <div className="block-title">
            <strong>交付</strong>
            <span className="note">这一层交出的文件、结论，以及给下一层的输入</span>
            {iface.derived && <span className="note">没有接口卡：从原有字段摘录，每条注明摘自哪里</span>}
            {(c.layer === 'L2' || c.layer === 'L3') && (
              <button className="tf-btn mini" disabled={busy} onClick={onReport} style={{ marginLeft: 'auto' }}>
                {arts.some((a) => a.artifact_type === 'report'
                  && a.path?.includes(c.layer === 'L3' ? 'L3-solution-report' : 'L2-attribution-report'))
                  ? (c.layer === 'L3' ? '重新生成方案报告' : '重新生成归因报告')
                  : (c.layer === 'L3' ? '生成方案报告' : '生成归因报告')}
              </button>
            )}
          </div>

          <div className="block-k">本层交付</div>
          <div className="qa">{g.produces || '（未定义）'}</div>
          <div className="cfg-meta">交付项：{g.deliverables.join(' / ') || '—'}</div>

          <div className="block-k">
            登记文件（证据/输入）
            <span className="note">L2/L3/L4 的交付是 payload 与接口卡；文件只是本包登记的证据或输入</span>
          </div>
          {arts.length > 0
            ? arts.map((e) => (
                <div className="artifact-row" key={e.id}>
                  <button type="button" className="artifact-open" onClick={() => setArtifactOpen(e.id)}
                    title="在工作台打开、审核或编辑">
                    <span className="mono artifact-path">{e.path || e.id}</span>
                    <span className="artifact-open-word">打开/审核</span>
                  </button>
                  <span className="muted small">
                    {artifactPurpose(e.artifact_type, e.path)} · {e.artifact_type}
                    {e.hash ? ` · ${e.hash.slice(0, 12)}` : ''}
                    {e.for_test?.length ? ` · 支撑测试 ${e.for_test.join('、')}` : e.cited === false ? ' · 未被结论引用' : ''}
                  </span>
                </div>
              ))
            : <div className="muted small">还没有登记交付文件。</div>}

          <div className="block-k">
            交给下游的结论
            <span className="note">按类型归组；下游只读这些结论，S 编号用于引用</span>
          </div>
          {gives.length ? (
            <>
              <div className="conclusion-summary">
                <strong>本层共 {gives.length} 条结论</strong>
                {giveGroups.map((g) => (
                  <span key={g.key}>{GIVE_KIND_LABEL[g.key]} {g.items.length}</span>
                ))}
              </div>
              {giveGroups.map((group, gi) => (
                <details className="give-group" key={group.key}
                  open={group.key === 'root_cause' || gi === 0}>
                  <summary>
                    <span className="give-group-kind">{GIVE_KIND_LABEL[group.key]}</span>
                    <span className="muted">{group.items.length} 条</span>
                    <span className="give-group-ids">{group.items.map((x) => x.id).join(' / ')}</span>
                  </summary>
                  {group.items.map((x) => (
                    <GiveRow key={x.id + x.text} idx={gives.indexOf(x)} g={x} ev={ev}
                      truth={truth} frames={frames} onOpenArtifact={setArtifactOpen} />
                  ))}
                </details>
              ))}
            </>
          ) : <div className="muted">（还没有产出）</div>}

          {upstream.length > 0 && (
            <>
              <div className="block-k">
                本层依据的上游
                <span className="note">{c.parents_derived ? '按流程顺序推得' : '本层结论建立在哪些上游结论上'}</span>
              </div>
              {upstream.map((u) => (
                <div key={u.id} className="take">
                  <Link to={layerHref(u.id)}>{u.layer ? `${u.layer} ${LAYER_NAME[u.layer] ?? ''}` : u.id}</Link>
                  {u.text ? `：${u.text}` : <span className="muted">：只记了包号，没有写具体用了哪条结论</span>}
                  {taskPkgs.has(u.id) && <StateChip word={taskPkgs.get(u.id)!.state} small />}
                </div>
              ))}
            </>
          )}

          {nextInputs.length > 0 && (
            <>
              <div className="block-k">给下一层的输入 <span className="note">不是遗留问题；下一层要据此工作</span></div>
              {nextInputs.map((q, i) => <div key={i} className="qa">· {q.question}</div>)}
            </>
          )}

          {humanInputs.length > 0 && (
            <>
              <div className="block-k">待人回答 <span className="note">这些问题需要人给方向</span></div>
              {humanInputs.map((q, i) => <div key={i} className="qa">· {q.question}</div>)}
            </>
          )}

          {(iface.limits ?? []).length > 0 && (
            <>
              <div className="block-k">适用边界</div>
              {iface.limits!.map((l, i) => <div key={i} className="qa">· {l}</div>)}
            </>
          )}
        </section>

        {/* 证据：只回答一件事——每条结论有没有登记在册的依据 */}
        <section className="block ld-ev">
          <div className="block-title">
            <strong>证据</strong><span className="note">结论 → 依据：每条结论只列它自己声明的依据</span>
          </div>
          <div className="ev-why">
            不同结论下面挂不同 ART，是接口卡里每条 S 的 evidence 列表不同：S1 用“负载/激励”依据，
            S3 用“基线源 + 激励”依据，并不表示同一个工件能证明所有结论。平台只核对“引用已登记、哈希一致”，
            因果关系由结论文字和依据类型表达；如果某条依据不该出现，改接口卡。
          </div>
          {gives.length === 0 && <div className="muted small">还没有结论，因此没有可核对的依据。</div>}
          {gives.length > 0 && (
            <div className="ev-summary">
              结论依据：{withEvidence.length}/{gives.length} 条有登记依据
            </div>
          )}
          {withoutEvidence.length > 0 && (
            <div className="ev-missing">没有写依据：{withoutEvidence.map((g) => g.id).join('、')}</div>
          )}
          {gives.map((give, i) => {
            if ((give.evidence ?? []).length === 0) return null
            return (
            <div className="ev-give" key={give.id}>
              <div className="ev-give-head">
                结论 {i + 1} <span className="mono">{give.id}</span>
                <span className="muted small">{GIVE_KIND_LABEL[giveKind(give)]}</span>
              </div>
              {(give.evidence ?? []).map((id) => {
                const e = ev.get(id)
                if (!e) return <div className="ev-missing" key={id}>{id}：不在登记册</div>
                if (e.kind === 'test') {
                  return (
                    <div className="ev-line" key={id}>
                      测试「{e.title}」：{verdictWord(e.verdict)}（{e.strength ?? '—'}）
                      {e.for_statement && e.for_statement.length > 0 ? ` · 支撑 ${e.for_statement.join('、')}` : ''}
                    </div>
                  )
                }
                return (
                  <div className="ev-line ev-line-art" key={id}>
                    <span className="ev-arrow">依据</span>
                    <button type="button" className="ev-art-open" onClick={() => setArtifactOpen(id)}
                      title="打开/审核这个工件">
                      工件 <span className="mono">{baseName(e.path) || e.id}</span>
                    </button>
                    <span className="muted">
                      {artifactPurpose(e.artifact_type, e.path)} · {e.path || e.id}
                      {e.hash ? ` · ${e.hash.slice(0, 12)}` : ''}
                    </span>
                    {e.for_test?.length
                      ? <span className="muted"> · 支撑测试 {e.for_test.join('、')}</span>
                      : e.cited === false ? <span className="ev-missing"> · 未被结论引用</span> : null}
                  </div>
                )
              })}
            </div>
            )
          })}
          {otherTests.length > 0 && (
            <details className="ev-more">
              <summary>其他登记测试（{otherTests.length}）</summary>
              {otherTests.map((e) => (
                <div className="ev-line" key={e.id}>
                  测试「{e.title}」：{verdictWord(e.verdict)}（{e.strength ?? '—'}）
                  {e.for_statement && e.for_statement.length > 0 ? ` · 支撑 ${e.for_statement.join('、')}` : ' · 没有被结论引用'}
                </div>
              ))}
            </details>
          )}
          {uncitedArts.length > 0 && (
            <details className="ev-more">
              <summary>未被结论引用的登记物（{uncitedArts.length}）</summary>
              {uncitedArts.map((e) => (
                <div className="ev-line" key={e.id}>
                  工件 <span className="mono">{e.path || e.id}</span>
                  {e.hash ? ` · ${e.hash.slice(0, 12)}` : ''}
                </div>
              ))}
            </details>
          )}
          {c.reproduce_command && (
            <>
              <div className="block-k">复现</div>
              <div className="ev-cmd">{c.reproduce_command}</div>
            </>
          )}
        </section>
          </>
        )}
      </div>

      {/* 原始记录：默认折叠 */}
      <details className="raw">
        <summary>原始记录（完整 payload、接口卡、工件、核对报告）</summary>
        <div className="raw-cols">
          <div>
            <h4>payload</h4>
            <pre>{JSON.stringify(raw.payload, null, 2)}</pre>
          </div>
          <div>
            <h4>接口卡（interface）</h4>
            <pre>{raw.interface ? JSON.stringify(raw.interface, null, 2) : '（信封里没有 interface：上面显示的是从原有字段摘录的）'}</pre>
          </div>
          <div>
            <h4>工件与引用</h4>
            <pre>{JSON.stringify({ artifacts: raw.artifacts, decision_refs: raw.decision_refs, provenance: raw.provenance }, null, 2)}</pre>
          </div>
          <div>
            <h4>核对报告（原文）</h4>
            <pre>{c.gate_report || '（从未核对）'}{'\n\n'}内部结论：{c.state.verdict}{'\n'}生命周期：{c.state.lifecycle}</pre>
          </div>
        </div>
      </details>

      {decisionsOpen && !invalidated && (
        <div className="modal-mask" onClick={() => setDecisionsOpen(false)}>
          <div className="cfg-viewer decision-modal" onClick={(e) => e.stopPropagation()}>
            <div className="tf-head">
              <h2>人的决定</h2>
              <button className="tf-close" onClick={() => setDecisionsOpen(false)}>关闭</button>
            </div>
            <DecisionWindow pkg={pkg} card={c} onNotice={setNotice} />
          </div>
        </div>
      )}

      {artifactOpen && (
        <ArtifactViewer
          pkg={pkg}
          artifactId={artifactOpen}
          onClose={() => setArtifactOpen('')}
          onSaved={(msg) => setNotice({ kind: 'ok', title: msg })}
        />
      )}
    </div>
  )
}

/** 流程里还没开始的框：先看这一层的说明，点一下生成本层的层包 */
function NewLayer() {
  const [params] = useSearchParams()
  const iterationId = params.get('iteration') ?? ''
  const layer = params.get('layer') ?? ''
  const work = useWork()
  const [agent] = useOperator()
  const { run, busy } = useAction()
  const nav = useNavigate()
  const { data: guide } = useQuery({ queryKey: ['guide'], queryFn: api.guide })
  const [error, setError] = useState('')

  const task = work.state?.tasks.find((t) => t.iterations.some((i) => i.iteration_id === iterationId))
  const it = task?.iterations.find((i) => i.iteration_id === iterationId)
  const existing = it?.nodes.find((n) => n.layer === layer)?.own[0]
  useEffect(() => {
    // 这一层已经有包了：直接去那里
    if (existing) nav(layerHref(existing), { replace: true })
  }, [existing, nav])
  const g = guide?.layers.find((l) => l.layer === layer)

  if (work.isLoading) return <PageLoad />
  if (work.error) return <PageErr error={work.error} />
  if (existing) return <PageLoad />
  if (!task || !it || !g) {
    return (
      <div className="ld">
        <Link className="back" to={flowHref(task?.task_id, iterationId)}>‹ 返回流程编排</Link>
        <div className="notice err" style={{ marginTop: 12 }}>没有找到这一层所属的迭代，或层号不合法。请从流程编排页点进来。</div>
      </div>
    )
  }

  const create = async () => {
    setError('')
    if (!agent.trim()) return setError('请先在右上角填写操作人——建包要记名')
    const resp = await run({ action: 'new_pkg', iteration: iterationId, layer, agent: agent.trim() })
    if (!resp.ok) return setError(actionError(resp))
    nav(layerHref(String(resp.result?.package_id ?? '')))
  }

  return (
    <div className="ld">
      <div className="ld-top">
        <Link className="back" to={flowHref(task.task_id, iterationId)}>‹ 返回流程编排</Link>
        <div className="head-actions">
          <button className="tf-btn primary" disabled={busy} onClick={create}>生成本层的层包</button>
        </div>
      </div>
      <header className="ld-head">
        <div className="ld-title">
          <span className="ld-layer">{g.layer} {g.name}</span>
          <span className="state-chip" style={{ color: '#9a9aab', borderColor: '#9a9aab' }}>未开始</span>
        </div>
        <div className="ld-why">这一层在「{it.label}」还没有层包。生成之后，把工作交给做这一层的 agent（「给 agent 的简报」）。</div>
        <div className="ld-meta"><span>{it.label}</span><span>任务：{task.title}</span></div>
      </header>
      {error && <ActionNotice kind="err">{error}</ActionNotice>}
      <div className="ld-grid" style={{ gridTemplateAreas: '"goal"' }}>
        <section className="block ld-goal">
          <div className="block-title"><strong>目标</strong><span className="note">不变量：每一层看到的都是这同一句</span></div>
          <div className="goal-text">{task.goal.statement}</div>
          <div className="block-k">这一层要回答的问题</div>
          <div className="qa">{g.question}</div>
          <div className="block-k">本层要交出</div>
          <div className="qa">{g.produces}</div>
          <div className="cfg-meta">交付项：{g.deliverables.join(' / ') || '—'}</div>
          <div className="cfg-hint" style={{ marginTop: 12 }}>
            谁做这一层、用什么 skills / tools，在 <Link to="/agents">Agent 管理</Link> 里看。
          </div>
        </section>
      </div>
    </div>
  )
}

export default function LayerDetail() {
  const { pkg = '' } = useParams()
  return pkg === 'new' ? <NewLayer /> : <Detail pkg={pkg} />
}
