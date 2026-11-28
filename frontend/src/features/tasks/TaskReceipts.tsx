import { useState } from 'react'
import { fmtTime, receiptAction } from '../../status'
import type { TaskReceipt } from '../../types'
import { actionError, useAction } from '../shared/useAction'
import { useOperator } from './useOperator'

/** 任务级的决定回执：章程、开某一轮、结束某一轮、封板——谁 · 何时 · 决定了什么。
 *  待人决定的事项可以直接批准或驳回（署名 = 操作人）。 */
export default function TaskReceipts({ receipts }: { receipts: TaskReceipt[] }) {
  const [agent] = useOperator()
  const { run, busy } = useAction()
  const [rejecting, setRejecting] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const [approving, setApproving] = useState<string | null>(null)
  const [approveNote, setApproveNote] = useState('')
  const [error, setError] = useState('')

  const approve = async (r: TaskReceipt) => {
    setError('')
    if (!agent.trim()) return setError('请先在右上角填写操作人——批准要署名')
    if (!approveNote.trim()) return setError('批准要写一句你核对了什么——只记「谁点的」，事后看不出依据什么批的')
    const resp = await run({ action: 'approve', dec: r.decision_id, as: agent.trim(), agent: agent.trim(), note: approveNote.trim() })
    if (!resp.ok) setError(actionError(resp))
    else { setApproving(null); setApproveNote('') }
  }
  const reject = async (r: TaskReceipt) => {
    setError('')
    if (!reason.trim()) return setError('驳回要写明理由')
    if (!agent.trim()) return setError('请先在右上角填写操作人——驳回要署名')
    const resp = await run({ action: 'reject', dec: r.decision_id, reason: reason.trim(), as: agent.trim(), agent: agent.trim() })
    if (!resp.ok) setError(actionError(resp))
    else { setRejecting(null); setReason('') }
  }

  if (!receipts.length) return <div className="muted small">没有记录</div>
  // 一句话结论常常已经以「开迭代 2：」开头：这时不再重复写类别
  const kindOf = (r: TaskReceipt) => (r.kind && !r.summary.startsWith(r.kind) ? `${r.kind} · ` : '')
  return (
    <div className="receipts">
      {receipts.map((r) => (
        <div key={r.decision_id} className={'receipt ' + (r.pending ? 'pending' : 'signed')}>
          {r.pending ? (
            <>
              <span className="receipt-kind">{kindOf(r)}</span>待人决定：{r.summary || '（没有写一句话结论）'}
              <span className="receipt-btns">
                <button className="tf-btn mini" disabled={busy}
                  onClick={() => { setApproving(r.decision_id); setApproveNote(''); setError('') }}>批准</button>
                <button className="tf-btn mini danger" disabled={busy} onClick={() => { setRejecting(r.decision_id); setReason(''); setError('') }}>驳回</button>
              </span>
              {approving === r.decision_id && (
                <div className="inline-form">
                  <label>核对了什么（一句话，必填）
                    <input value={approveNote} placeholder="如：看过目标、benchmark 与流程模板，和要做的事一致"
                      onChange={(e) => setApproveNote(e.target.value)} />
                  </label>
                  <div className="form-btns">
                    <button className="tf-btn mini primary" disabled={busy} onClick={() => approve(r)}>确认批准</button>
                    <button className="tf-btn mini" onClick={() => setApproving(null)}>取消</button>
                  </div>
                </div>
              )}
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
            </>
          ) : (
            <>
              <span className="receipt-at">{fmtTime(r.at)}</span>
              <span className="receipt-by">{r.by}</span>
              {kindOf(r)}{receiptAction(r)}
              {r.outcome && (r.note
                ? <span className="receipt-note">{r.outcome === '驳回' ? '理由' : '批注'}：{r.note}</span>
                : <span className="receipt-flags">{r.outcome === '驳回' ? '没有写明理由' : '没有写明核对了什么'}</span>)}
            </>
          )}
        </div>
      ))}
      {error && <div className="form-error">{error}</div>}
    </div>
  )
}
