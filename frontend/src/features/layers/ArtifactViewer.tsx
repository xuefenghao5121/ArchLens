import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { ArtifactContent } from '../../types'
import { actionError, useAction } from '../shared/useAction'
import { artifactPurpose } from '../shared/Give'
import { useOperator } from '../tasks/useOperator'

interface SaveResult {
  changed?: boolean
  artifact_id?: string
  path?: string
  message?: string
  invalidated?: string[]
  gate?: { verdict?: string; blocks?: string[]; warns?: string[] }
}

/**
 * 工件查看 / 审核 / 编辑。
 *
 * 编辑不会覆盖共享输入：平台把修改写成 designs/<pkg>/edits/… 下的新文件，登记成新 ART，
 * 再把本包对旧 ART 的引用切到新版本。旧文件、旧 ART 和旧签字都保留，gate 会重新核对。
 */
export function ArtifactViewer({ pkg, artifactId, onClose, onSaved }: {
  pkg: string
  artifactId: string
  onClose: () => void
  onSaved: (msg: string) => void
}) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['artifact', pkg, artifactId],
    queryFn: () => api.artifact(pkg, artifactId),
    enabled: !!pkg && !!artifactId,
  })
  const [agent] = useOperator()
  const { run, busy } = useAction()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {
    if (data && !editing) setDraft(data.text ?? '')
  }, [data, editing])

  const save = async () => {
    setErr('')
    if (!agent.trim()) return setErr('请先在右上角填写操作人——编辑要署名')
    if (!draft.trim()) return setErr('内容为空，不能保存')
    const resp = await run({
      action: 'artifact_save', pkg, artifact: artifactId,
      content: draft, base_hash: data?.content_hash, agent: agent.trim(), by: agent.trim(),
    })
    if (!resp.ok) return setErr(actionError(resp))
    const result = (resp.result ?? {}) as SaveResult
    if (result.changed === false) {
      setEditing(false)
      onSaved('内容没有变化，未生成新工件。')
      return
    }
    const gate = result.gate?.verdict ? `；核对：${result.gate.verdict}` : ''
    const down = result.invalidated?.length ? `；下游作废 ${result.invalidated.length} 个包` : ''
    onSaved(`已保存为新工件 ${result.artifact_id ?? ''}（原文件保留）${gate}${down}。内容变了，需要重新核对/签字。`)
    onClose()
  }

  const a: ArtifactContent | undefined = data
  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="artifact-modal" onClick={(e) => e.stopPropagation()}>
        <div className="artifact-head">
          <div>
            <strong>{a?.title || '工件'}</strong>
            <span className="mono artifact-id">{artifactId}</span>
          </div>
          <button className="tf-close" onClick={onClose}>关闭</button>
        </div>

        {isLoading && <div className="muted small">载入中…</div>}
        {error && <div className="form-error">{String((error as Error).message)}</div>}
        {a && (
          <>
            <div className="artifact-meta">
              <span className="mono">{a.path}</span>
              <span>{artifactPurpose(a.artifact_type, a.path)} · {a.artifact_type} · {a.source} · {Math.ceil(a.size / 1024)}KB</span>
              <span className="mono">sha256 {a.content_hash.slice(0, 16)}…</span>
            </div>
            {a.stale && (
              <div className="form-error">
                文件内容和登记哈希不一致：登记的是 {a.registered_hash.slice(0, 16)}…，
                当前文件是 {a.content_hash.slice(0, 16)}…。请先确认是谁改的，再决定是否覆盖。
              </div>
            )}
            {a.binary && <div className="cfg-hint">二进制文件只能看元数据，不能在文本编辑器里改。</div>}
            {a.truncated && <div className="cfg-hint">文件超过 512KB，只显示元数据，不能在工作台编辑。</div>}

            {editing ? (
              <>
                <textarea
                  className="artifact-editor"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  spellCheck={false}
                />
                <div className="artifact-edit-hint">
                  保存不会覆盖共享输入：会写到 <span className="mono">designs/{pkg}/edits/…</span>，
                  生成新 ART，并把本包引用切到新版本。旧工件与旧签字保留；保存后自动重跑 gate，
                  直接依赖本包的下游层会作废（不能用旧结论继续）。
                </div>
                {err && <div className="form-error">{err}</div>}
                <div className="artifact-actions">
                  <button className="tf-btn primary mini" disabled={busy} onClick={save}>
                    {busy ? '保存中…' : '保存为新版本'}
                  </button>
                  <button className="tf-btn mini" disabled={busy} onClick={() => { setEditing(false); setDraft(a.text ?? '') }}>
                    取消编辑
                  </button>
                  <button className="tf-link muted" onClick={() => refetch()}>重新读取</button>
                </div>
              </>
            ) : (
              <>
                <pre className="artifact-text">{a.text ?? '（没有可显示的文本内容）'}</pre>
                <div className="artifact-actions">
                  <button className="tf-btn mini" disabled={!a.editable || a.stale} onClick={() => setEditing(true)}>
                    编辑
                  </button>
                  <span className="muted small">
                    {a.editable && !a.stale ? '编辑会生成新版本，不改原文件' : '当前文件不可编辑'}
                  </span>
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}
