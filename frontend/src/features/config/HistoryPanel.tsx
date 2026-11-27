import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { VersionContent, VersionEntry } from '../../types'
import { actionError, useAction } from '../shared/useAction'
import { useOperator } from '../tasks/useOperator'
import { ActionNotice } from '../shared/ui'

type DiffLine = { kind: 'same' | 'add' | 'del'; text: string }

/** 行级差异（LCS）。定义文件只有几十行，直接 DP 足够。 */
function lineDiff(oldText: string, newText: string): DiffLine[] {
  const a = oldText.split('\n')
  const b = newText.split('\n')
  const dp: number[][] = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0))
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const out: DiffLine[] = []
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: 'same', text: a[i] })
      i++
      j++
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ kind: 'del', text: a[i] })
      i++
    } else {
      out.push({ kind: 'add', text: b[j] })
      j++
    }
  }
  while (i < a.length) out.push({ kind: 'del', text: a[i++] })
  while (j < b.length) out.push({ kind: 'add', text: b[j++] })
  return out
}

function DiffView({ oldText, newText }: { oldText: string; newText: string }) {
  const lines = lineDiff(oldText, newText)
  return (
    <pre className="cfg-diff">
      {lines.map((l, k) => (
        <span key={k} className={'line ' + l.kind}>
          {l.kind === 'add' ? '+ ' : l.kind === 'del' ? '- ' : '  '}
          {l.text}
        </span>
      ))}
    </pre>
  )
}

/** Agent 定义的版本历史：看历史、对比当前、回滚。回滚本身也会先快照当前。 */
export default function HistoryPanel({ target }: { target: string }) {
  const { data, isError, error } = useQuery({
    queryKey: ['history', 'agent', target],
    queryFn: () => api.history('agent', target),
  })
  const [agent] = useOperator()
  const { run, busy } = useAction()
  const [view, setView] = useState<VersionContent | null>(null)
  const [compare, setCompare] = useState<VersionContent | null>(null)
  const [note, setNote] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  if (isError) return <div className="cfg-section"><div className="form-error">历史读取失败：{(error as Error).message}</div></div>
  if (!data) return <div className="cfg-section"><span className="muted small">读取历史…</span></div>

  const show = async (sha: string, mode: 'view' | 'compare') => {
    setNote(null)
    try {
      const v = await api.version('agent', target, sha)
      if (mode === 'view') setView(v)
      else setCompare(v)
    } catch (e) {
      setNote({ kind: 'err', text: `取历史版本失败：${(e as Error).message}` })
    }
  }

  const restore = async (e: VersionEntry) => {
    setNote(null)
    if (!agent.trim()) return setNote({ kind: 'err', text: '请先在右上角填写操作人——回滚要记名' })
    if (!window.confirm(`回滚到 ${e.sha.slice(0, 10)}（${e.at}，${e.by || '未署名'}）？\n\n当前内容会先存一份快照，回滚本身也能再回滚。`)) return
    const resp = await run({ action: 'agent_restore', target, sha: e.sha, agent: agent.trim() })
    if (!resp.ok) return setNote({ kind: 'err', text: actionError(resp) })
    setNote({ kind: 'ok', text: `已回滚到 ${e.sha.slice(0, 10)}，已重新投影宿主定义。` })
  }

  return (
    <div className="cfg-section">
      <div className="cfg-section-title">
        版本历史
        <span className="muted small">当前 {data.current_sha.slice(0, 10)}</span>
      </div>
      <div className="cfg-hint">每次保存前先快照旧内容；回滚前也会快照当前内容。</div>
      {note && <ActionNotice kind={note.kind}>{note.text}</ActionNotice>}
      {data.entries.length === 0 && <div className="cfg-hint" style={{ marginTop: 8 }}>还没有通过界面保存过。</div>}
      {data.entries.map((e) => (
        <div className={'cfg-entry' + (e.sha === data.current_sha ? ' now' : '')} key={e.sha + e.at}>
          <div className="cfg-entry-head">
            <span className="mono">{e.sha.slice(0, 10)}</span>
            {e.sha === data.current_sha && <span className="cfg-badge">当前</span>}
            <span className="muted small">{e.at} · {e.by || '未署名'}</span>
          </div>
          <div className="cfg-meta">
            <span>{e.note || '保存'}</span>
            {e.add != null && (
              <span className="cfg-diffstat"><b className="add">+{e.add}</b> <b className="del">−{e.del ?? 0}</b></span>
            )}
            <span>{e.lines} 行</span>
          </div>
          <div className="cfg-entry-btns">
            <button className="tf-btn mini" onClick={() => show(e.sha, 'view')}>查看</button>
            <button className="tf-btn mini" onClick={() => show(e.sha, 'compare')}>对比当前</button>
            {e.sha !== data.current_sha && (
              <button className="tf-btn mini danger" disabled={busy} onClick={() => restore(e)}>回滚</button>
            )}
          </div>
        </div>
      ))}
      {view && (
        <div className="modal-mask" onClick={() => setView(null)}>
          <div className="cfg-viewer" onClick={(ev) => ev.stopPropagation()}>
            <div className="tf-head">
              <h2>版本 {view.entry.sha.slice(0, 10)}</h2>
              <button className="tf-close" onClick={() => setView(null)}>关闭</button>
            </div>
            <div className="cfg-meta" style={{ marginBottom: 8 }}>
              <span>{view.entry.at} · {view.entry.by || '未署名'} · {view.entry.note}</span>
            </div>
            <pre className="cfg-pre">{view.text}</pre>
          </div>
        </div>
      )}
      {compare && (
        <div className="modal-mask" onClick={() => setCompare(null)}>
          <div className="cfg-viewer" onClick={(ev) => ev.stopPropagation()}>
            <div className="tf-head">
              <h2>对比当前：{compare.entry.sha.slice(0, 10)} → {data.current_sha.slice(0, 10)}</h2>
              <button className="tf-close" onClick={() => setCompare(null)}>关闭</button>
            </div>
            <div className="cfg-hint">− 是历史版本的行，+ 是当前的行。</div>
            <DiffView oldText={compare.text} newText={data.current} />
          </div>
        </div>
      )}
    </div>
  )
}
