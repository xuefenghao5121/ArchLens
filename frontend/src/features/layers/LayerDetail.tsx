import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { GateReport, LayerCard, LayerGuide, Receipt, StateWord } from '../../types'
import { FRAME_LABEL, LAYER_NAME, TRUTH_LABEL, fmtTime, receiptAction } from '../../status'
import { GiveRow, verdictWord } from '../shared/Give'
import { actionError, useAction } from '../shared/useAction'
import { ActionNotice, PageErr, PageLoad, StateChip } from '../shared/ui'
import { flowHref, layerHref, useWork } from '../tasks/useWork'
import { useOperator } from '../tasks/useOperator'
import './layer.css'

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

/** 这一层的固定说明：回答什么、由谁做、怎样算已验证 */
function GuideBlock({ g }: { g: LayerGuide }) {
  return (
    <>
      <div className="block-k">这一层是什么</div>
      <div className="guide">
        <div><span className="guide-k">由谁做：</span>{g.role_label}</div>
        <div><span className="guide-k">用到的纪律：</span>{g.skills.join('、') || '—'}</div>
        <div><span className="guide-k">输入：</span>{g.consumes}</div>
        <div><span className="guide-k">必须交出：</span>{g.produces}</div>
        <div>
          <span className="guide-k">怎样算已验证：</span>
          {g.human ? '有人签字；' : ''}{g.needs_test ? '有登记在册的测试通过' : '不要求测试'}
        </div>
        <div><span className="guide-k">要交的东西：</span>{g.deliverables.join('、')}</div>
      </div>
    </>
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
          <div key={r.decision_id} className="receipt pending">
            待人决定：{r.summary || '（没有写一句话结论）'}
            {r.stale && <span className="receipt-flags">提请之后内容改过</span>}
            <span className="receipt-btns">
              <button className="tf-btn mini" disabled={busy} onClick={() => approve(r)}>批准</button>
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
                    <button className="tf-btn mini" disabled={busy} onClick={() => { setSummary(r.summary); setDeciding(true); setError('') }}>重签</button>
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

  useEffect(() => {
    setPanel(null); setNotice(null); setFormError(''); setPreview(null); setBrief('')
  }, [pkg])

  if (isLoading) return <PageLoad />
  if (error || !data) return <PageErr error={error ?? new Error('没有取到这一层的数据')} />

  const c = data.card
  const g = c.guide
  const iface = c.interface
  const truth = guide?.truth ?? TRUTH_LABEL
  const frames = guide?.frames ?? FRAME_LABEL
  const ev = new Map(c.evidence.map((e) => [e.id, e]))
  const taskTitle = state?.tasks.find((t) => t.task_id === c.task_id)?.title ?? ''
  const taskPkgs = new Map<string, { layer: string; state: StateWord }>()
  for (const grp of Object.values(state?.layers ?? {})) {
    for (const p of grp.packages) taskPkgs.set(p.package_id, { layer: p.card.layer, state: p.state })
  }
  const open = (p: Panel) => { setNotice(null); setFormError(''); setPreview(null); setPanel(p) }
  const needAgent = () => {
    if (agent.trim()) return true
    setFormError('请先在右上角填写操作人——写操作需要记名')
    return false
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

  const onPropose = async () => {
    setFormError('')
    if (!summary.trim()) return setFormError('请用一句话写明决定了什么')
    if (!needAgent()) return
    const text = summary.trim()
    const resp = await run({ action: 'propose', pkg, summary: text, rationale: rationale.trim(), agent: agent.trim() })
    if (!resp.ok) return setFormError(actionError(resp))
    setPanel(null); setSummary(''); setRationale('')
    setNotice({ kind: 'ok', title: `已提请：「${text}」。等人在这里批准。` })
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

  const onBrief = async () => {
    if (panel === 'brief') { setPanel(null); return }
    open('brief')
    const resp = await api.action({ action: 'ask', pkg })
    if (resp.ok && typeof resp.result?.brief === 'string') setBrief(resp.result.brief)
    else setFormError(resp.ok ? '没有取到简报' : actionError(resp))
  }

  const takes = iface.takes ?? []
  const upstream = takes.length
    ? takes.map((t) => ({ id: t.from, text: `${taskPkgs.get(t.from)?.layer ?? ''} ${LAYER_NAME[taskPkgs.get(t.from)?.layer ?? ''] ?? ''} 的 ${t.statement}` }))
    : c.parents.map((id) => ({ id, text: `${taskPkgs.get(id)?.layer ?? ''} ${LAYER_NAME[taskPkgs.get(id)?.layer ?? ''] ?? ''}` }))
  const tests = c.evidence.filter((e) => e.kind === 'test')
  const arts = c.evidence.filter((e) => e.kind === 'artifact')
  const raw = data.raw

  return (
    <div className="ld">
      <div className="ld-top">
        <Link className="back" to={flowHref(c.task_id, c.iteration_id)}>‹ 返回流程编排</Link>
        <div className="head-actions">
          <button className="tf-btn primary" disabled={busy} onClick={onCheck}>核对</button>
          <button className="tf-btn" disabled={busy} onClick={() => open(panel === 'propose' ? null : 'propose')}>提请决定</button>
          <button className="tf-btn danger" disabled={busy} onClick={() => open(panel === 'revise' ? null : 'revise')}>作废并重做</button>
          <button className="tf-btn" onClick={onBrief}>{panel === 'brief' ? '收起简报' : '给 agent 的简报'}</button>
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
          <span className="mono">{c.package_id}</span>
        </div>
      </header>

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

      {panel === 'propose' && (
        <div className="inline-form">
          <strong className="small">提请决定</strong>
          <div className="form-hint">写一句话说清要决定什么。它会出现在下面的「人的决定」里，等人批准。</div>
          <label>决定了什么（一句话）
            <input value={summary} maxLength={80} placeholder="如：基线按虚拟原型口径拍定" onChange={(e) => setSummary(e.target.value)} />
          </label>
          <label>详细依据（可省略）
            <textarea rows={2} value={rationale} onChange={(e) => setRationale(e.target.value)} />
          </label>
          <div className="form-btns">
            <button className="tf-btn primary mini" disabled={busy} onClick={onPropose}>提请</button>
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
          <div className="form-hint">含任务目标原话、这一层要交什么、上游卡片上的结论和证据、接口卡的写法。复制给做这一层的 agent。</div>
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

      <div className="ld-grid">
        {/* 目标 */}
        <section className="block ld-goal">
          <div className="block-title"><strong>目标</strong><span className="note">不变量：每一层看到的都是这同一句</span></div>
          <div className="goal-text">{c.goal || '（这个包没有归到任务，没有目标原话）'}</div>
          <div className="block-k">这一层要回答的问题</div>
          <div className="qa">{iface.question || g.question}</div>
          <div style={{ marginTop: 16 }}>
            <GuideBlock g={g} />
          </div>
        </section>

        {/* 交付 */}
        <section className="block ld-out">
          <div className="block-title">
            <strong>交付</strong><span className="note">交给下游的结论{c.layer === 'L8' ? '' : '：下游拿到的不是全文，只有这里写的几条'}</span>
            {iface.derived && <span className="note">没有接口卡：从原有字段摘录，每条注明摘自哪里</span>}
          </div>
          {iface.gives.length
            ? iface.gives.map((x) => <GiveRow key={x.id + x.text} g={x} ev={ev} truth={truth} frames={frames} />)
            : <div className="muted">（还没有产出）</div>}

          {upstream.length > 0 && (
            <>
              <div className="block-k">用了上游的{c.parents_derived ? '（按流程顺序推得）' : ''}</div>
              {upstream.map((u) => (
                <div key={u.id} className="take">
                  <Link to={layerHref(u.id)}>{u.text || u.id}</Link>
                  {taskPkgs.has(u.id) && <StateChip word={taskPkgs.get(u.id)!.state} small />}
                </div>
              ))}
            </>
          )}

          {(iface.open ?? []).length > 0 && (
            <>
              <div className="block-k">还没解决的问题</div>
              {iface.open!.map((q, i) => (
                <div key={i} className="qa">
                  <span className="tag">{q.ask === 'human' ? '等人回答' : '留给下一层'}</span> {q.question}
                </div>
              ))}
            </>
          )}

          {(iface.limits ?? []).length > 0 && (
            <>
              <div className="block-k">适用边界</div>
              {iface.limits!.map((l, i) => <div key={i} className="qa">· {l}</div>)}
            </>
          )}
        </section>

        {/* 证据 */}
        <section className="block ld-ev">
          <div className="block-title"><strong>证据</strong><span className="note">登记在册的测试与工件</span></div>
          {tests.length === 0 && arts.length === 0 && <div className="muted small">还没有登记任何证据。</div>}
          {tests.map((e) => (
            <div key={e.id} className="ev-line">
              <div>
                <span className="ev-k">测试 </span>{e.title}
                <span className={'ev-' + (e.verdict === 'pass' ? 'pass' : 'fail')}> {verdictWord(e.verdict)}</span>
                <span className="ev-k">（{e.strength}）</span>
              </div>
              {e.proposition && <div className="ev-k">{e.proposition}</div>}
              <div className="ev-k">
                {e.executor ? `${e.executor} · ` : ''}{fmtTime(e.at)} · {e.id}
                {e.for_statement && e.for_statement.length > 0 ? ` · 支撑 ${e.for_statement.join('、')}` : ''}
              </div>
            </div>
          ))}
          {arts.map((e) => (
            <div key={e.id} className="ev-line">
              <div><span className="ev-k">工件 </span><span className="mono">{e.id}</span> <span className="ev-k">{e.artifact_type}</span></div>
              <div className="ev-k mono ev-path">{e.path || ''}</div>
              {e.hash && <div className="ev-k">哈希 {e.hash}{e.cited === false ? ' · 没有被结论引用' : ''}</div>}
              {e.title && e.title !== e.artifact_type && <div className="ev-k">{e.title}</div>}
            </div>
          ))}
          {c.reproduce_command && (
            <>
              <div className="block-k">复现</div>
              <div className="ev-cmd">{c.reproduce_command}</div>
            </>
          )}
        </section>

        <DecisionWindow pkg={pkg} card={c} onNotice={setNotice} />
      </div>

      {/* 核对回执 */}
      <section className="block">
        <div className="block-title">
          <strong>核对回执</strong>
          <span className="note">上次核对：{c.checked_at ? fmtTime(c.checked_at) : '从未'}。点右上角「核对」重新核对</span>
        </div>
        <div className="checks">
          {c.checks.map((ch) => (
            <div key={ch.id} className="check">
              <span className={'check-mark ' + (ch.ok === true ? 'ok' : ch.ok === false ? 'bad' : 'na')}>{ch.mark}</span>
              <span className="check-label">{ch.label}</span>
              <span>{ch.detail}</span>
            </div>
          ))}
        </div>
      </section>

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
          <div style={{ marginTop: 16 }}>
            <GuideBlock g={g} />
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
