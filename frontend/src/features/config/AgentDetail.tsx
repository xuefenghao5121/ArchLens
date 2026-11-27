import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { AgentConfigEntry } from '../../types'
import { actionError, useAction } from '../shared/useAction'
import { useOperator } from '../tasks/useOperator'
import { ActionNotice, EmptyState, PageErr, PageLoad } from '../shared/ui'
import HistoryPanel from './HistoryPanel'
import './config.css'

type Draft = {
  name: string
  host_name: string
  skills: string[]
  tools: string[]
  writes: string
  must_not: string
  body: string
}

function lines(text: string): string[] {
  return text.split('\n').map((s) => s.trim()).filter(Boolean)
}

function draftOf(a: AgentConfigEntry): Draft {
  return {
    name: a.name, host_name: a.host_name,
    skills: [...a.skills], tools: [...a.tools],
    writes: a.writes.join('\n'), must_not: a.must_not.join('\n'),
    body: a.body,
  }
}

/** 单个 agent 的定义：表单化 frontmatter + 正文编辑；保存前服务端整组校验；右侧版本历史。 */
export default function AgentDetail() {
  const { target = '' } = useParams()
  const [operator] = useOperator()
  const { run, busy } = useAction()
  const agentsQ = useQuery({ queryKey: ['agents'], queryFn: api.agents })
  const skillsQ = useQuery({ queryKey: ['skills'], queryFn: api.skills })
  const toolsQ = useQuery({ queryKey: ['tools'], queryFn: api.tools })
  const [draft, setDraft] = useState<Draft | null>(null)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  // 保存确认：先把「会做什么」摆清楚，再写盘
  const [confirming, setConfirming] = useState(false)

  if (agentsQ.isError) return <PageErr error={agentsQ.error} />
  if (!agentsQ.data) return <PageLoad />
  const agent = agentsQ.data.agents.find((a) => a.target === target)
  if (!agent) {
    return (
      <EmptyState title="没有这个 agent" sub={`target=${target}`}>
        <Link className="tf-btn" to="/agents">返回列表</Link>
      </EmptyState>
    )
  }

  const skills = skillsQ.data?.skills ?? []
  const tools = toolsQ.data?.packs ?? []

  const toggle = (key: 'skills' | 'tools', name: string) => {
    setDraft((d) => {
      if (!d) return d
      const list = d[key]
      return { ...d, [key]: list.includes(name) ? list.filter((x) => x !== name) : [...list, name] }
    })
  }

  /** 保存前的本地检查。通过后不立刻写盘，先弹确认：写盘 + 快照 + 宿主兼容投影。 */
  const requestSave = () => {
    setMsg(null)
    if (!draft) return
    if (!draft.name.trim() || !draft.host_name.trim()) return setMsg({ kind: 'err', text: '名字与宿主名不能为空' })
    if (!draft.skills.length) return setMsg({ kind: 'err', text: '至少挂一个 skill' })
    if (!draft.tools.length) return setMsg({ kind: 'err', text: '至少挂一个工具包' })
    if (!draft.body.trim()) return setMsg({ kind: 'err', text: '正文不能为空——它是人读的职责、步骤、完成标准与纪律' })
    if (!operator.trim()) return setMsg({ kind: 'err', text: '请先在右上角填写操作人——保存定义要记名' })
    setConfirming(true)
  }

  const confirmSave = async () => {
    setMsg(null)
    if (!draft) return
    const meta = {
      layer: agent.layer, name: draft.name.trim(), host_name: draft.host_name.trim(),
      role: agent.role, skills: draft.skills, tools: draft.tools,
      writes: lines(draft.writes), must_not: lines(draft.must_not),
    }
    const resp = await run({
      action: 'agent_save', target, meta, body: draft.body,
      base_sha: agent.sha, agent: operator.trim(),
    })
    if (!resp.ok) {
      setConfirming(false)
      return setMsg({ kind: 'err', text: actionError(resp) })
    }
    const r = resp.result ?? {}
    const proj = (r.projection as { ok?: boolean; written?: number; error?: string } | undefined) ?? {}
    const snap = (r.snapshot as { add?: number; del?: number } | null | undefined)
    setConfirming(false)
    setDraft(null)
    setMsg({
      kind: proj.ok === false ? 'err' : 'ok',
      text: [
        `已保存，新指纹 ${String(r.sha ?? '').slice(0, 12)}。`,
        snap ? `快照：+${snap.add ?? 0} / −${snap.del ?? 0} 行。` : '内容没有变化。',
        proj.ok === false
          ? `宿主兼容投影失败：${proj.error}`
          : `宿主兼容投影 ${proj.written ?? 0} 份。平台读取的是 platform/agents 原文件（按 mtime 重新加载），保存即生效。`,
      ].join(' '),
    })
  }

  const reproject = async () => {
    setMsg(null)
    if (!operator.trim()) return setMsg({ kind: 'err', text: '请先在右上角填写操作人' })
    const resp = await run({ action: 'reproject_hosts', agent: operator.trim() })
    if (!resp.ok) return setMsg({ kind: 'err', text: actionError(resp) })
    const r = resp.result ?? {}
    setMsg(r.ok === false
      ? { kind: 'err', text: `投影失败：${String(r.error ?? '')}` }
      : { kind: 'ok', text: `已重投影 ${Number(r.written ?? 0)} 份宿主定义。` })
  }

  return (
    <div>
      <div className="page-head">
        <h1>{agent.name || agent.target}</h1>
        <div className="head-actions">
          <Link className="tf-btn" to="/agents">返回列表</Link>
          <button className="tf-btn" disabled={busy} onClick={reproject}
            title="只重新生成 .zcode/agents 副本，不保存；手动改过文件没走保存时用">仅重投影</button>
          {!draft
            ? <button className="tf-btn primary" onClick={() => { setMsg(null); setDraft(draftOf(agent)) }}>编辑定义</button>
            : <button className="tf-btn" onClick={() => { setDraft(null); setMsg(null); setConfirming(false) }}>取消编辑</button>}
        </div>
      </div>
      <div className="cfg-meta" style={{ marginBottom: 10 }}>
        <span className="cfg-badge">{agent.layer}</span>
        <span className="mono">{agent.file}</span>
        <span>宿主名 <span className="mono">{agent.host_name}</span></span>
        <span>{agent.role_label || agent.role}</span>
        <span className="mono">sha {agent.sha.slice(0, 12)}</span>
      </div>

      {msg && (
        <ActionNotice kind={msg.kind}>
          <span style={{ whiteSpace: 'pre-wrap' }}>{msg.text}</span>
        </ActionNotice>
      )}

      <div className="cfg-detail">
        <div>
          {draft ? (
            <div className="cfg-section">
              <div className="cfg-section-title">编辑定义 <span className="muted small">保存前会整组校验，失败不写盘</span></div>
              <div className="cfg-form">
                <div className="cfg-two">
                  <label>名字
                    <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                  </label>
                  <label>宿主名（.zcode/agents/&lt;名&gt;.md）
                    <input className="mono" value={draft.host_name} onChange={(e) => setDraft({ ...draft, host_name: e.target.value })} />
                  </label>
                </div>
                <div className="cfg-hint">
                  role 由层决定（{agent.role}），不在界面里改；layer 与目录绑定。
                  宿主名改了，旧的投影文件会在保存时清掉。
                </div>
                <div>
                  <div className="cfg-section-title" style={{ marginBottom: 6 }}>skills（来自技能货架）</div>
                  <div className="cfg-checks">
                    {skills.map((s) => (
                      <label key={s.name} title={s.what}>
                        <input type="checkbox" checked={draft.skills.includes(s.name)}
                          onChange={() => toggle('skills', s.name)} />
                        {s.name}
                      </label>
                    ))}
                  </div>
                </div>
                <div>
                  <div className="cfg-section-title" style={{ marginBottom: 6 }}>tools（来自工具包货架）</div>
                  <div className="cfg-checks">
                    {tools.map((t) => (
                      <label key={t.name} title={t.what}>
                        <input type="checkbox" checked={draft.tools.includes(t.name)}
                          onChange={() => toggle('tools', t.name)} />
                        {t.name}
                      </label>
                    ))}
                  </div>
                </div>
                <label>可写范围（每行一条）
                  <textarea rows={3} value={draft.writes} onChange={(e) => setDraft({ ...draft, writes: e.target.value })} />
                </label>
                <label>不许做的事（每行一条）
                  <textarea rows={4} value={draft.must_not} onChange={(e) => setDraft({ ...draft, must_not: e.target.value })} />
                </label>
                <label>正文（职责、步骤、完成标准与纪律）
                  <textarea className="mono" rows={20} value={draft.body}
                    onChange={(e) => setDraft({ ...draft, body: e.target.value })} />
                </label>
                <div className="form-btns">
                  <button className="tf-btn primary" disabled={busy} onClick={requestSave}>{busy ? '保存中…' : '保存并重投影'}</button>
                  <button className="tf-btn" onClick={() => { setDraft(null); setMsg(null); setConfirming(false) }}>取消</button>
                </div>
              </div>
            </div>
          ) : (
            <>
              <div className="cfg-section">
                <div className="cfg-section-title">边界</div>
                <div className="cfg-meta" style={{ marginBottom: 6 }}>可写</div>
                <ul className="cfg-list">{agent.writes.map((x) => <li key={x}>{x}</li>)}</ul>
                <div className="cfg-meta" style={{ marginBottom: 6, marginTop: 10 }}>不许做</div>
                <ul className="cfg-list">{agent.must_not.map((x) => <li key={x}>{x}</li>)}</ul>
              </div>
              <div className="cfg-section">
                <div className="cfg-section-title">定义正文</div>
                <pre className="cfg-pre">{agent.body}</pre>
              </div>
            </>
          )}
        </div>
        <HistoryPanel target={target} />
      </div>

      {confirming && (
        <div className="modal-mask" onClick={() => setConfirming(false)}>
          <div className="task-form" onClick={(e) => e.stopPropagation()}>
            <div className="tf-head">
              <h2>确认保存定义？</h2>
              <button className="tf-close" onClick={() => setConfirming(false)}>关闭</button>
            </div>
            <p className="tf-sub">这次保存会做三件事：</p>
            <ul className="cfg-list">
              <li>写回 <span className="mono">{agent.file}</span>（服务端再按整组规则校验一次）；</li>
              <li>给当前版本留一份快照（时间、署名、指纹、增删行数）；</li>
              <li>更新 .zcode/agents 兼容投影（仅供外部宿主；平台内部读取 platform/agents 原文件）。</li>
            </ul>
            <div className="notice info" style={{ marginTop: 10 }}>
              平台读取 agent 定义时按文件重新加载，保存后下一次派发就使用新定义；不需要新开对话，
              也不经过 ZCode 界面。
            </div>
            <div className="form-btns">
              <button className="tf-btn primary" disabled={busy} onClick={confirmSave}>
                {busy ? '保存中…' : '确认保存'}
              </button>
              <button className="tf-btn" onClick={() => setConfirming(false)}>返回修改</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
