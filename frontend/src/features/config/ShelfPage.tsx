import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { SkillShelfEntry, ToolPackEntry } from '../../types'
import { actionError, useAction } from '../shared/useAction'
import { useOperator } from '../tasks/useOperator'
import { ActionNotice, PageErr, PageLoad } from '../shared/ui'
import './config.css'

type ToolDraft = { name: string; what: string; commands: string; ref: string }

/** 货架：skills 只读（方法论投影），tools 可新建 / 编辑 / 删除（未被引用时）。 */
export default function ShelfPage() {
  const [tab, setTab] = useState<'skills' | 'tools'>('skills')
  const skillsQ = useQuery({ queryKey: ['skills'], queryFn: api.skills })
  const toolsQ = useQuery({ queryKey: ['tools'], queryFn: api.tools })
  const [operator] = useOperator()
  const { run, busy } = useAction()
  const [skillView, setSkillView] = useState<SkillShelfEntry | null>(null)
  const [toolDraft, setToolDraft] = useState<ToolDraft | null>(null)
  const [editingName, setEditingName] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  if (skillsQ.isError) return <PageErr error={skillsQ.error} />
  if (toolsQ.isError) return <PageErr error={toolsQ.error} />
  if (!skillsQ.data || !toolsQ.data) return <PageLoad />

  const skills = skillsQ.data.skills
  const tools = toolsQ.data.packs

  const openTool = (t?: ToolPackEntry) => {
    setMsg(null)
    setEditingName(t ? t.name : null)
    setToolDraft(t
      ? { name: t.name, what: t.what, commands: t.commands.join('\n'), ref: t.ref }
      : { name: '', what: '', commands: '', ref: '' })
  }

  const saveTool = async () => {
    setMsg(null)
    if (!toolDraft) return
    if (!operator.trim()) return setMsg({ kind: 'err', text: '请先在右上角填写操作人——保存工具包要记名' })
    if (!toolDraft.name.trim() || !toolDraft.what.trim()) return setMsg({ kind: 'err', text: '工具包名与用途不能为空' })
    const commands = toolDraft.commands.split('\n').map((s) => s.trim()).filter(Boolean)
    const resp = await run({
      action: 'toolpack_save', name: toolDraft.name.trim(), what: toolDraft.what.trim(),
      commands, ref: toolDraft.ref.trim(), agent: operator.trim(), base_sha: toolsQ.data?.sha,
    })
    if (!resp.ok) return setMsg({ kind: 'err', text: actionError(resp) })
    setToolDraft(null)
    setEditingName(null)
    setMsg({ kind: 'ok', text: `已保存 ${toolDraft.name.trim()}，并重投影宿主定义（运行中的会话需重启）。` })
  }

  const deleteTool = async (t: ToolPackEntry) => {
    setMsg(null)
    if (!operator.trim()) return setMsg({ kind: 'err', text: '请先在右上角填写操作人' })
    if (t.used_by.length) return setMsg({ kind: 'err', text: `${t.name} 仍被 agent 使用，先在定义里去掉它` })
    if (!window.confirm(`删除工具包 ${t.name}？历史快照会保留，可回滚。`)) return
    const resp = await run({
      action: 'toolpack_delete', name: t.name, agent: operator.trim(), base_sha: toolsQ.data?.sha,
    })
    if (!resp.ok) return setMsg({ kind: 'err', text: actionError(resp) })
    setMsg({ kind: 'ok', text: `已删除 ${t.name}，并重投影宿主定义。` })
  }

  return (
    <div>
      <div className="page-head">
        <h1>技能与工具</h1>
        <div className="head-actions">
          <div className="cfg-tabs">
            <button className={'cfg-tab' + (tab === 'skills' ? ' active' : '')} onClick={() => setTab('skills')}>skills（{skills.length}）</button>
            <button className={'cfg-tab' + (tab === 'tools' ? ' active' : '')} onClick={() => setTab('tools')}>工具包（{tools.length}）</button>
          </div>
          {tab === 'tools' && <button className="tf-btn primary" onClick={() => openTool()}>新建工具包</button>}
        </div>
      </div>

      {msg && <ActionNotice kind={msg.kind}>{msg.text}</ActionNotice>}

      {tab === 'skills' && (
        <>
          <p className="cfg-lede">
            技能是方法论的功能投影：<code>platform/methodology/*.md</code> 生成 <code>.agents/skills/*/SKILL.md</code>。
            这里只读——改内容请改方法论源，再重跑 <code>python3 platform/tools/bind_hosts.py</code>，否则会被下一次投影覆盖。
          </p>
          {skillsQ.data.errors.length > 0 && (
            <ActionNotice kind="err"><ul>{skillsQ.data.errors.map((e) => <li key={e}>{e}</li>)}</ul></ActionNotice>
          )}
          <div className="cfg-grid">
            {skills.map((s) => (
              <button type="button" className="cfg-card cfg-card-btn" key={s.name} onClick={() => setSkillView(s)}>
                <h3>
                  <span className="mono">{s.name}</span>
                  <span className={'cfg-badge' + (s.generated ? ' gen' : '')}>{s.generated ? 'GENERATED' : '手写'}</span>
                </h3>
                <div className="cfg-meta">{s.what || '（无描述）'}</div>
                <div className="cfg-tags">
                  {s.used_by.length
                    ? s.used_by.map((u) => <span className="tag" key={u.target}>{u.layer || u.target}</span>)
                    : <span className="muted small">未被任何 agent 使用</span>}
                </div>
              </button>
            ))}
          </div>
        </>
      )}

      {tab === 'tools' && (
        <>
          <p className="cfg-lede">
            工具包是 agent 定义里 <code>tools</code> 能引用的全部命令集合。改用途或命令后，会重新投影宿主定义；
            仍被 agent 引用的包不能删。
          </p>
          {toolsQ.data.errors.length > 0 && (
            <ActionNotice kind="err"><ul>{toolsQ.data.errors.map((e) => <li key={e}>{e}</li>)}</ul></ActionNotice>
          )}
          <div className="cfg-grid">
            {tools.map((t) => (
              <div className="cfg-card" key={t.name}>
                <h3><span className="mono">{t.name}</span></h3>
                <div className="cfg-meta">{t.what}</div>
                <div className="cfg-tags">
                  {t.used_by.length
                    ? t.used_by.map((u) => <span className="tag" key={u.target}>{u.layer || u.target}</span>)
                    : <span className="muted small">未被使用</span>}
                </div>
                {t.commands.length > 0 && (
                  <div className="cfg-code">{t.commands.map((c) => <div key={c} className="mono small">{c}</div>)}</div>
                )}
                {t.ref && <div className="cfg-code mono small">ref: {t.ref}</div>}
                <div className="cfg-entry-btns">
                  <button className="tf-btn mini" onClick={() => openTool(t)}>编辑</button>
                  <button className="tf-btn mini danger" disabled={busy || t.used_by.length > 0}
                    title={t.used_by.length ? '仍被 agent 使用' : '删除（历史快照保留）'}
                    onClick={() => deleteTool(t)}>删除</button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {skillView && (
        <div className="modal-mask" onClick={() => setSkillView(null)}>
          <div className="cfg-viewer" onClick={(e) => e.stopPropagation()}>
            <div className="tf-head">
              <h2>{skillView.name}</h2>
              <button className="tf-close" onClick={() => setSkillView(null)}>关闭</button>
            </div>
            <div className="cfg-tags" style={{ marginBottom: 8 }}>
              <span className={'cfg-badge' + (skillView.generated ? ' gen' : '')}>{skillView.generated ? 'GENERATED' : '手写'}</span>
              {skillView.used_by.map((u) => <span className="tag" key={u.target}>{u.name || u.target}</span>)}
            </div>
            <p className="cfg-lede" style={{ margin: '0 0 10px' }}>{skillView.description}</p>
            <div className="cfg-hint">
              {skillView.generated
                ? <>方法论源：<code>{skillView.source}</code>。改源后重跑 <code>python3 platform/tools/bind_hosts.py</code>；技能页只读。</>
                : <>手写 skill：<code>.agents/skills/{skillView.dir}/SKILL.md</code>。本页只读。</>}
            </div>
            <pre className="cfg-pre" style={{ marginTop: 10 }}>{skillView.body}</pre>
          </div>
        </div>
      )}

      {toolDraft && (
        <div className="modal-mask" onClick={() => setToolDraft(null)}>
          <div className="task-form" onClick={(e) => e.stopPropagation()}>
            <div className="tf-head">
              <h2>{editingName ? `编辑工具包 ${editingName}` : '新建工具包'}</h2>
              <button className="tf-close" onClick={() => setToolDraft(null)}>关闭</button>
            </div>
            <p className="tf-sub">名字创建后不可改（agent 定义按名字引用）；用途会进 agent 的悬浮提示与宿主投影。</p>
            <div className="cfg-form">
              <div className="cfg-two">
                <label>名字
                  <input className="mono" value={toolDraft.name} disabled={!!editingName}
                    placeholder="如 test:pack" onChange={(e) => setToolDraft({ ...toolDraft, name: e.target.value })} />
                </label>
                <label>用途（一句话）
                  <input value={toolDraft.what} placeholder="如 判例库情境检索"
                    onChange={(e) => setToolDraft({ ...toolDraft, what: e.target.value })} />
                </label>
              </div>
              <label>命令（每行一条；与 ref 至少给一个）
                <textarea className="mono" rows={4} value={toolDraft.commands}
                  placeholder={'python3 platform/tools/wf.py guide'}
                  onChange={(e) => setToolDraft({ ...toolDraft, commands: e.target.value })} />
              </label>
              <label>领域引用（可选；领域相关的命令指向 environment.md）
                <input value={toolDraft.ref} placeholder="domains/<领域>/environment.md"
                  onChange={(e) => setToolDraft({ ...toolDraft, ref: e.target.value })} />
              </label>
              <div className="form-btns">
                <button className="tf-btn primary" disabled={busy} onClick={saveTool}>{busy ? '保存中…' : '保存'}</button>
                <button className="tf-btn" onClick={() => setToolDraft(null)}>取消</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
