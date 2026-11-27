import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import { actionError, useAction } from '../shared/useAction'
import { useOperator } from '../tasks/useOperator'
import { ActionNotice, PageErr, PageLoad } from '../shared/ui'
import './config.css'

/** 运行时配置：平台自己调模型执行层 agent。密钥只落在本机 platform/runtime/config.json（0600）。 */
function RuntimeCard() {
  const { data, isError, error } = useQuery({ queryKey: ['runtime'], queryFn: api.runtime })
  const [operator] = useOperator()
  const { run, busy } = useAction()
  const [form, setForm] = useState({ base_url: '', model: '', api_key: '', api_key_env: 'RUNTIME_API_KEY', max_turns: '40', engine: 'v1' })
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  useEffect(() => {
    if (!data) return
    setForm((f) => ({
      ...f, base_url: data.config.base_url, model: data.config.model,
      api_key_env: data.config.api_key_env, max_turns: String(data.config.max_turns),
      engine: data.config.engine || 'v1',
    }))
  }, [data])

  if (isError) return <div className="cfg-section"><div className="form-error">运行时状态读取失败：{(error as Error).message}</div></div>
  if (!data) return <div className="cfg-section"><span className="muted small">读取运行时状态…</span></div>

  const save = async () => {
    setMsg(null)
    if (!operator.trim()) return setMsg({ kind: 'err', text: '请先在右上角填写操作人' })
    if (!form.base_url.trim() || !form.model.trim()) return setMsg({ kind: 'err', text: 'base_url 与 model 不能为空' })
    const resp = await run({
      action: 'runtime_config', base_url: form.base_url.trim(), model: form.model.trim(),
      api_key: form.api_key.trim() || undefined, api_key_env: form.api_key_env.trim(),
      max_turns: Number(form.max_turns) || 40, engine: form.engine, agent: operator.trim(),
    })
    if (!resp.ok) return setMsg({ kind: 'err', text: actionError(resp) })
    setForm((f) => ({ ...f, api_key: '' }))
    setMsg({ kind: 'ok', text: '运行时配置已保存。派发层 agent 时平台会用它调用模型。' })
  }

  return (
    <div className="cfg-section">
      <div className="cfg-section-title">
        运行时
        <span className={'cfg-badge' + (data.config.configured ? ' gen' : '')}>
          {data.config.configured ? '已配置' : '未配置'}
        </span>
        <span className="muted small">平台内部执行层 agent，不经过 ZCode 对话</span>
      </div>
      <div className="cfg-meta">
        {data.config.configured
          ? <>模型 <span className="mono">{data.config.model}</span> · 接口 <span className="mono">{data.config.base_url}</span> · 密钥 {data.config.has_key ? '已保存' : '来自环境变量'}</>
          : <>还没配置模型。派发会失败；先在下面填 OpenAI 兼容接口、模型名与密钥。</>}
        {data.active_job && (
          <span>正在跑：<Link to={`/layer/${encodeURIComponent(data.active_job.package_id)}`}>{data.active_job.job_id}（{data.active_job.package_id}）</Link></span>
        )}
      </div>
      <details open={!data.config.configured}>
        <summary className="cfg-hint">模型配置</summary>
        <div className="cfg-form" style={{ marginTop: 8 }}>
          <div className="cfg-two">
            <label>base_url（OpenAI 兼容）
              <input className="mono" value={form.base_url} placeholder="https://api.deepseek.com/v1"
                onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
            </label>
            <label>model
              <input className="mono" value={form.model} placeholder="如 deepseek-chat"
                onChange={(e) => setForm({ ...form, model: e.target.value })} />
            </label>
          </div>
          <div className="cfg-two">
            <label>api_key（留空则用环境变量）
              <input className="mono" type="password" value={form.api_key}
                placeholder={data.config.has_key ? '已保存，留空不改' : 'sk-…'}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })} />
            </label>
            <label>密钥环境变量名
              <input className="mono" value={form.api_key_env}
                onChange={(e) => setForm({ ...form, api_key_env: e.target.value })} />
            </label>
          </div>
          <div className="cfg-two">
            <label>运行时引擎
              <select value={form.engine} onChange={(e) => setForm({ ...form, engine: e.target.value })}>
                <option value="v1">v1（通用工具循环）</option>
                <option value="langgraph">langgraph（L1–L8 领域工具图）</option>
              </select>
            </label>
            <label>最多模型步数
              <input type="number" min={1} max={200} value={form.max_turns}
                onChange={(e) => setForm({ ...form, max_turns: e.target.value })} />
            </label>
          </div>
          <div className="cfg-hint">
            密钥写进本机 <code>platform/runtime/config.json</code>（权限 0600，不进库）；也可以用环境变量
            <code>RUNTIME_API_KEY</code> 覆盖。作业日志在 <code>platform/runtime/jobs/</code>。
          </div>
          {msg && <ActionNotice kind={msg.kind}>{msg.text}</ActionNotice>}
          <div className="form-btns">
            <button className="tf-btn primary" disabled={busy} onClick={save}>{busy ? '保存中…' : '保存运行时配置'}</button>
          </div>
        </div>
      </details>
    </div>
  )
}

/** Agent 管理：每层一个定义 + 调度 agent。点进去改 frontmatter（表单）与正文。 */
export default function AgentsPage() {
  const { data, isError, error } = useQuery({ queryKey: ['agents'], queryFn: api.agents })

  if (!data && !isError) return <PageLoad />
  if (isError) return <PageErr error={error} />

  return (
    <div>
      <div className="page-head">
        <h1>Agent 管理</h1>
        <span className="muted small">九份定义 · 单一来源</span>
      </div>
      <RuntimeCard />
      <p className="cfg-lede">
        每层一个 agent：做什么、用什么 skills 与工具包、能写哪里、不许做什么，都只在
        <code>platform/agents/&lt;层&gt;/agent.md</code> 这一份定义里。改完保存前整组校验；
        保存后自动重投影 <code>.zcode/agents/</code>，并留一份带署名的版本快照。
        定义里的 skills 来自技能货架，tools 来自工具包货架。
      </p>
      {data.errors.length > 0 && (
        <ActionNotice kind="err">
          <strong>当前定义有错误（看板与层页可能读不出来）</strong>
          <ul>{data.errors.map((e) => <li key={e}>{e}</li>)}</ul>
        </ActionNotice>
      )}
      <div className="cfg-grid">
        {data.agents.map((a) => (
          <Link className="cfg-card" to={`/agents/${a.target}`} key={a.target}>
            <h3>
              <span className="cfg-badge">{a.layer}</span>
              {a.name || '（未命名）'}
            </h3>
            <div className="cfg-meta">
              <span className="mono">{a.host_name}</span>
              <span>{a.role_label || a.role}</span>
            </div>
            <div className="cfg-tags">
              {a.skills.map((s) => <span className="tag" key={s}>{s}</span>)}
              {a.tools.map((t) => <span className="tag" key={t}>{t}</span>)}
            </div>
            <div className="cfg-meta" style={{ marginTop: 8 }}>
              <span>可写 {a.writes.length} 条</span>
              <span>禁区 {a.must_not.length} 条</span>
              <span className="mono">{a.sha.slice(0, 10)}</span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
