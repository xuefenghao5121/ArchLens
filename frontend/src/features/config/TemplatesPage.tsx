import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { TemplateInfo, TemplateNode } from '../../types'
import { actionError, useAction } from '../shared/useAction'
import { useOperator } from '../tasks/useOperator'
import { ActionNotice, PageErr, PageLoad } from '../shared/ui'
import './config.css'

type NodeDraft = { component: string; alias: string; repeat: boolean }
type TemplateDraft = {
  id: string
  label: string
  description: string
  nodes: NodeDraft[]
  overrides: Record<string, string>
  isNew: boolean
  usedTasks: number
}

function draftOf(t: TemplateInfo, isNew = false, idSuffix = ''): TemplateDraft {
  const overrides: Record<string, string> = {}
  for (const [alias, ov] of Object.entries(t.gate_overrides ?? {})) {
    overrides[alias] = (ov.required_fields ?? []).join(', ')
  }
  return {
    id: isNew ? `${t.id}${idSuffix}` : t.id,
    label: isNew ? `${t.label}（副本）` : t.label,
    description: t.description,
    nodes: (t.nodes ?? []).map((n) => ({
      component: n.component, alias: isNew ? `${n.alias}2` : n.alias, repeat: n.repeat === 'per_case',
    })),
    overrides: isNew ? {} : overrides,
    isNew,
    usedTasks: isNew ? 0 : (t.used_by?.tasks ?? 0),
  }
}

/** 编排配置：流程模板的可视化编辑（节点顺序、别名、逐例标记、门禁覆盖）。 */
export default function TemplatesPage() {
  const { data, isError, error } = useQuery({ queryKey: ['templates'], queryFn: api.templates })
  const [operator] = useOperator()
  const { run, busy } = useAction()
  const [draft, setDraft] = useState<TemplateDraft | null>(null)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  if (isError) return <PageErr error={error} />
  if (!data) return <PageLoad />

  const components = data.components ?? []
  const componentLabel = (id: string) => {
    const c = components.find((x) => x.id === id)
    return c ? `${id} ${c.name}` : id
  }

  const openNew = () => {
    setMsg(null)
    setDraft({ id: '', label: '', description: '', nodes: [{ component: 'L1', alias: 'l1', repeat: false }], overrides: {}, isNew: true, usedTasks: 0 })
  }

  const setNode = (i: number, patch: Partial<NodeDraft>) => {
    setDraft((d) => {
      if (!d) return d
      const old = d.nodes[i]
      const nodes = d.nodes.map((n, k) => (k === i ? { ...n, ...patch } : n))
      const overrides = { ...d.overrides }
      if (patch.alias != null && old.alias !== patch.alias && overrides[old.alias] != null) {
        overrides[patch.alias] = overrides[old.alias]
        delete overrides[old.alias]
      }
      if (patch.repeat) {
        for (let k = 0; k < nodes.length; k++) if (k !== i) nodes[k] = { ...nodes[k], repeat: false }
      }
      return { ...d, nodes, overrides }
    })
  }

  const move = (i: number, delta: number) => {
    setDraft((d) => {
      if (!d) return d
      const j = i + delta
      if (j < 0 || j >= d.nodes.length) return d
      const nodes = [...d.nodes]
      ;[nodes[i], nodes[j]] = [nodes[j], nodes[i]]
      return { ...d, nodes }
    })
  }

  const save = async () => {
    setMsg(null)
    if (!draft) return
    if (!draft.id.trim() || !draft.label.trim() || !draft.description.trim()) {
      return setMsg({ kind: 'err', text: 'id / 名称 / 说明都不能为空' })
    }
    if (!operator.trim()) return setMsg({ kind: 'err', text: '请先在右上角填写操作人——保存模板要记名' })
    const aliases = draft.nodes.map((n) => n.alias.trim()).filter(Boolean)
    if (new Set(aliases).size !== aliases.length) return setMsg({ kind: 'err', text: '节点别名不能重复' })
    const nodes: TemplateNode[] = draft.nodes.map((n) => (
      n.repeat ? { component: n.component, alias: n.alias.trim(), repeat: 'per_case' } : { component: n.component, alias: n.alias.trim() }
    ))
    const gate_overrides: Record<string, { required_fields: string[] }> = {}
    for (const [alias, text] of Object.entries(draft.overrides)) {
      const fields = text.split(',').map((s) => s.trim()).filter(Boolean)
      if (fields.length && aliases.includes(alias)) gate_overrides[alias] = { required_fields: fields }
    }
    const resp = await run({
      action: 'template_save', id: draft.id.trim(), label: draft.label.trim(),
      description: draft.description.trim(), nodes, gate_overrides,
      create: draft.isNew, base_sha: data.sha, agent: operator.trim(),
    })
    if (!resp.ok) return setMsg({ kind: 'err', text: actionError(resp) })
    const saved = draft
    setDraft(null)
    setMsg({
      kind: 'ok',
      text: saved.isNew
        ? `已新建模板 ${saved.id.trim()}。任务创建时的模板下拉列表里会立刻出现。`
        : `已保存 ${saved.id}。已有任务的框按新模板呈现（已建层包不删）。`,
    })
  }

  return (
    <div>
      <div className="page-head">
        <h1>编排配置</h1>
        <div className="head-actions">
          <span className="muted small">流程模板 · 单一来源 flow_templates.json</span>
          <button className="tf-btn primary" onClick={openNew}>新建模板</button>
        </div>
      </div>
      <p className="cfg-lede">
        模板决定一轮迭代有哪些框、按什么顺序、哪个框有额外必填字段。节点顺序就是流程顺序；边由顺序推导，
        <code>per_case</code> 节点会展开成逐例的 <code>case_1..N</code>。模板 id 创建后不可改（任务记录引用它）；
        要试新流程就复制一份。每个框用哪一层的 agent，由该层 <code>agent.md</code> 决定。
      </p>
      {msg && <ActionNotice kind={msg.kind}>{msg.text}</ActionNotice>}
      {data.errors && data.errors.length > 0 && (
        <ActionNotice kind="err">
          <strong>模板数据有错误</strong>
          <ul>{data.errors.map((e) => <li key={e}>{e}</li>)}</ul>
        </ActionNotice>
      )}
      <div className="cfg-grid">
        {data.templates.map((t) => (
          <div className="cfg-card" key={t.id}>
            <h3>
              {t.label}
              {t.id === data.default && <span className="cfg-badge">默认</span>}
            </h3>
            <div className="cfg-meta"><span className="mono">{t.id}</span><span>{t.description}</span></div>
            <div className="cfg-tags">
              {t.nodes && t.nodes.length > 0
                ? t.nodes.map((n, i) => (
                  <span className="tag" key={`${n.alias}-${i}`} title={componentLabel(n.component)}>
                    {n.component}{n.repeat ? ' ×N' : ''}·{n.alias}
                  </span>
                ))
                : <span className="muted small">不走层（只有目标、benchmark 与人的决定）</span>}
            </div>
            <div className="cfg-meta" style={{ marginTop: 8 }}>
              <span>{t.used_by?.tasks ?? 0} 个任务在用</span>
            </div>
            <div className="cfg-entry-btns">
              <button className="tf-btn mini" onClick={() => { setMsg(null); setDraft(draftOf(t)) }}>编辑</button>
              <button className="tf-btn mini" onClick={() => { setMsg(null); setDraft(draftOf(t, true, '_copy')) }}>复制</button>
            </div>
          </div>
        ))}
      </div>

      {draft && (
        <div className="modal-mask" onClick={() => setDraft(null)}>
          <div className="task-form" style={{ width: 880 }} onClick={(e) => e.stopPropagation()}>
            <div className="tf-head">
              <h2>{draft.isNew ? '新建模板' : `编辑模板 ${draft.id}`}</h2>
              <button className="tf-close" onClick={() => setDraft(null)}>关闭</button>
            </div>
            <p className="tf-sub">
              id 创建后不可改；节点从上到下就是流程顺序。先用组件下拉选层，再给这个框一个别名（同一次迭代内唯一）。
              保存前会校验组件、别名与门禁字段。
            </p>
            <div className="cfg-form">
              <div className="cfg-two">
                <label>id
                  <input className="mono" value={draft.id} disabled={!draft.isNew}
                    placeholder="如 probe_flow" onChange={(e) => setDraft({ ...draft, id: e.target.value })} />
                </label>
                <label>名称
                  <input value={draft.label} placeholder="界面上显示的名字"
                    onChange={(e) => setDraft({ ...draft, label: e.target.value })} />
                </label>
              </div>
              <label>说明
                <input value={draft.description} placeholder="一句话：这个流程适合什么情况"
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
              </label>
              {draft.usedTasks > 0 && (
                <div className="form-hint">
                  已有 {draft.usedTasks} 个任务在用这个模板。改框顺序/节点只影响它们的呈现，已建的层包不会删。
                </div>
              )}

              <div className="cfg-section-title" style={{ marginBottom: 4 }}>框（节点顺序）</div>
              <div className="cfg-nodes">
                {draft.nodes.map((n, i) => (
                  <div className="cfg-node-row" key={i}>
                    <select value={n.component} onChange={(e) => setNode(i, { component: e.target.value })}>
                      {components.map((c) => <option key={c.id} value={c.id}>{c.id} {c.name}</option>)}
                    </select>
                    <input className="mono" value={n.alias} placeholder="别名"
                      onChange={(e) => setNode(i, { alias: e.target.value })} />
                    <label className="cfg-inline">
                      <input type="checkbox" checked={n.repeat}
                        onChange={(e) => setNode(i, { repeat: e.target.checked })} />
                      逐例
                    </label>
                    <div className="cfg-node-actions">
                      <button className="tf-btn mini" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                      <button className="tf-btn mini" disabled={i === draft.nodes.length - 1} onClick={() => move(i, 1)}>↓</button>
                      <button className="tf-btn mini danger" onClick={() => setDraft({
                        ...draft, nodes: draft.nodes.filter((_, k) => k !== i),
                      })}>×</button>
                    </div>
                  </div>
                ))}
              </div>
              <div className="tf-foot" style={{ justifyContent: 'flex-start' }}>
                <button className="tf-btn mini" onClick={() => setDraft({
                  ...draft,
                  nodes: [...draft.nodes, { component: 'L1', alias: `n${draft.nodes.length + 1}`, repeat: false }],
                })}>+ 加一个框</button>
                <span className="cfg-hint">「逐例」只用于批量标定：一个节点展开成 case_1..N；最多一个。</span>
              </div>

              {draft.nodes.some((n) => n.alias.trim()) && (
                <>
                  <div className="cfg-section-title" style={{ marginBottom: 4 }}>门禁覆盖（可选）</div>
                  <div className="cfg-hint">按别名给这个框加必填字段；多个字段用逗号分隔。空 = 不加。</div>
                  <div className="cfg-nodes">
                    {draft.nodes.filter((n) => n.alias.trim()).map((n) => (
                      <div className="cfg-override-row" key={n.alias}>
                        <span className="mono">{n.alias}</span>
                        <input value={draft.overrides[n.alias] ?? ''} placeholder="如 root_cause, trigger_condition"
                          onChange={(e) => setDraft({ ...draft, overrides: { ...draft.overrides, [n.alias]: e.target.value } })} />
                      </div>
                    ))}
                  </div>
                </>
              )}

              <div className="form-btns">
                <button className="tf-btn primary" disabled={busy} onClick={save}>{busy ? '保存中…' : '保存模板'}</button>
                <button className="tf-btn" onClick={() => setDraft(null)}>取消</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
