import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { FlowNode, GuideResponse as Guides, LayerPackageView } from '../../types'
import { ITER_STATUS_META, iterationTitle } from '../../status'
import { layerHref, useWork } from '../tasks/useWork'
import { useOperator } from '../tasks/useOperator'
import { actionError, useAction } from '../shared/useAction'
import { ActionNotice, EmptyState, PageErr, PageLoad, StateChip, StateLegend } from '../shared/ui'
import TaskForm, { ReasonForm } from '../tasks/TaskForm'
import TaskReceipts from '../tasks/TaskReceipts'
import WorkPicker from '../tasks/WorkPicker'
import './flow.css'

// 编排的是什么：先说清楚，再让人看图
const LEGEND = '一轮迭代 = 流程模板里的一串框。每个框是一类 agent 的一次工作（观测、归因、方案……），' +
  '箭头表示上游交给下游的东西：不是上游的全文，而是上游卡片上写明的几条结论和它们的证据。点任一框进入那一层。'

type Form = 'iter_new' | 'iter_seal' | 'iter_abort' | 'task_seal' | null

const FORM_TEXT: Record<Exclude<Form, null>, { title: string; hint: string; verb: string; done: string; signerHint?: string }> = {
  iter_new: {
    title: '开下一轮迭代', verb: '开下一轮',
    hint: '写上一轮的结论：为什么还要再来一轮。新一轮的层包按同一条流程模板建好。',
    signerHint: '不填则「开下一轮」先记为待人决定，批准后才开始。',
    done: '已开下一轮：每一层的层包已按模板建好。',
  },
  iter_seal: { title: '结束这一轮', verb: '结束本轮', hint: '这一轮到这里为止，内容保留，可以接着开下一轮。', done: '这一轮已结束（已署名）。' },
  iter_abort: { title: '终止这一轮', verb: '终止本轮', hint: '这一轮不再推进，内容保留但默认折叠。', done: '这一轮已终止（已署名）。' },
  task_seal: { title: '封板任务', verb: '封板', hint: '任务到此为止（SHIP），还在进行的迭代一并结束。', done: '任务已封板（已署名）。' },
}

/** 流程里的一个框：这一层在本轮只有一个包时整个框可点；有多个包时在框内并列 */
function NodeBox({ node, question, pkgOf, reusedOf, iterationId }: {
  node: FlowNode
  question: string
  pkgOf: (id: string) => LayerPackageView | undefined
  reusedOf: (id: string) => string | undefined
  iterationId: string
}) {
  const pkgs = node.own.concat(node.reused).map(pkgOf).filter((p): p is LayerPackageView => !!p)
  const single = node.own.length === 1 && node.reused.length === 0 ? pkgs[0] : undefined
  const firstGive = (p: LayerPackageView) => p.card.interface.gives[0]?.text ?? ''
  // 「还没有产出」只写一次：框里已经用它占位时，不再重复状态原因
  const whyOf = (p: LayerPackageView) => (firstGive(p) || p.card.state.why !== '还没有产出' ? p.card.state.why : '')

  return (
    <div
      className={'fnode' + (single ? ' one' : '') + (node.reused.length ? ' has-reuse' : '') + (!pkgs.length ? ' empty' : '')}
      data-state={node.state}
    >
      <div className="fnode-head">
        <span className="fnode-layer">
          {node.layer} {node.name}
          {node.agent && <span className="fnode-agent" title="这一层由一个独立的 agent 做，只拿开工简报">{node.agent}</span>}
        </span>
        {pkgs.length ? <StateChip word={node.state} small /> : <span className="muted small">还没有层包</span>}
      </div>
      <div className="fnode-q">{question}</div>
      {single ? (
        <Link to={layerHref(single.package_id)} className="fnode-link" title={single.title}>
          <span className={'fnode-give' + (firstGive(single) ? '' : ' muted')}>
            {firstGive(single) || (single.state === '未开始' ? '还没有产出' : '看这一层交出的结论')}
          </span>
          {whyOf(single) && <span className="fnode-why">{whyOf(single)}</span>}
        </Link>
      ) : pkgs.length ? (
        <ul className="fnode-list">
          {pkgs.map((p) => (
            <li key={p.package_id}>
              <Link to={layerHref(p.package_id)} title={p.title}>
                <StateChip word={p.state} small />
                <span className="fnode-item">{firstGive(p) || p.title}</span>
                {reusedOf(p.package_id) && <span className="fnode-reuse">沿用 {reusedOf(p.package_id)}</span>}
              </Link>
              {whyOf(p) && <div className="fnode-why">{whyOf(p)}</div>}
            </li>
          ))}
        </ul>
      ) : (
        <Link className="fnode-new" to={`/layer/new?iteration=${encodeURIComponent(iterationId)}&layer=${node.layer}`}>
          点这里生成本层的层包
        </Link>
      )}
    </div>
  )
}

function Chain({ nodes, questions, pkgOf, reusedOf, iterationId }: {
  nodes: FlowNode[]
  questions: Record<string, string>
  pkgOf: (id: string) => LayerPackageView | undefined
  reusedOf: (id: string) => string | undefined
  iterationId: string
}) {
  // 一行最多放四个框；超过就分成两行，行与行之间用「↓」表示结论接着往下交
  const n = nodes.length
  const sizes = n <= 4 ? [n] : [Math.ceil(n / 2), n - Math.ceil(n / 2)]
  const rows: FlowNode[][] = []
  let at = 0
  for (const size of sizes) {
    rows.push(nodes.slice(at, at + size))
    at += size
  }
  return (
    <div className="fchain">
      {rows.map((row, ri) => (
        <div key={ri}>
          {ri > 0 && <div className="fbreak" aria-hidden>↓</div>}
          <div className="frow" style={{ gridTemplateColumns: `repeat(${row.length}, minmax(0, 1fr))` }}>
            {row.map((node, i) => (
              <div key={node.layer} className="fcell">
                {i > 0 && <span className="farrow" aria-hidden>→</span>}
                <NodeBox node={node} question={questions[node.layer] ?? ''} pkgOf={pkgOf} reusedOf={reusedOf} iterationId={iterationId} />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

/** 每一层由谁做、用什么、怎样算做完。改定义文件后重跑投影器，这里跟着变。 */
function AgentTable({ nodes, guide }: { nodes: FlowNode[]; guide: Guides | undefined }) {
  if (!nodes.length) return null
  const layerOf = (L: string) => guide?.layers.find((x) => x.layer === L)
  return (
    <section className="block flow-agents">
      <div className="block-title">
        <strong>每一层由谁做、用什么</strong>
        <span className="note">定义在 platform/agents/&lt;层&gt;/agent.md；改完重跑 platform/tools/bind_hosts.py</span>
      </div>
      <table className="agent-table">
        <thead>
          <tr><th>层</th><th>agent</th><th>skills</th><th>tools</th><th>怎样算做完</th></tr>
        </thead>
        <tbody>
          {nodes.map((n) => {
            const g = layerOf(n.layer)
            const a = g?.agent
            const done = g ? [g.human ? '人签字' : '', g.needs_test ? '需测试' : ''].filter(Boolean).join(' · ') : ''
            return (
              <tr key={n.layer}>
                <td className="mono">{n.layer} {n.name}</td>
                <td>{a ? <><span className="agent-name">{a.name}</span><span className="muted small"> {a.host_name}</span></> : '—'}</td>
                <td>{a?.skills.length ? a.skills.map((x) => <span key={x.name} className="tag" title={x.what}>{x.name}</span>) : '—'}</td>
                <td>{a?.tools.length ? a.tools.map((x) => <span key={x.name} className="tag" title={x.what}>{x.name}</span>) : '—'}</td>
                <td className="small muted">{done || '无'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}

export default function Flow() {
  const work = useWork()
  const { state: data, task, iteration, pkgOf, reusedOf } = work
  const [agent] = useOperator()
  const { run, busy } = useAction()
  const [showNewTask, setShowNewTask] = useState(false)
  const [form, setForm] = useState<Form>(null)
  const [more, setMore] = useState(false)
  const [notice, setNotice] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const { data: guide } = useQuery({ queryKey: ['guide'], queryFn: api.guide })
  const questions: Record<string, string> = {}
  for (const l of guide?.layers ?? []) questions[l.layer] = l.question

  if (work.isLoading) return <PageLoad />
  if (work.error) return <PageErr error={work.error} />
  if (!data) return null

  const open = (f: Form) => { setNotice(null); setForm(f) }

  // ReasonForm 的提交：成功返回 null（表单收起），失败返回一句话（表单留在原地）
  const submitForm = async (which: Exclude<Form, null>, reason: string, signer: string): Promise<string | null> => {
    if (!task) return null
    const who = agent.trim()
    const sign = signer || who
    let resp
    if (which === 'iter_new') {
      resp = await run({ action: 'iter_new', task: task.task_id, reason, create: true, as: signer || undefined, agent: who })
    } else if (which === 'iter_seal') {
      resp = await run({ action: 'iter_seal', iteration: iteration?.iteration_id, reason, as: sign, agent: who })
    } else if (which === 'iter_abort') {
      resp = await run({ action: 'iter_abort', iteration: iteration?.iteration_id, reason, as: sign, agent: who })
    } else {
      resp = await run({ action: 'task_seal', task: task.task_id, reason, as: sign, agent: who })
    }
    if (!resp.ok) return actionError(resp)
    if (which === 'iter_new') work.select({ task: task.task_id, iteration: String(resp.result?.iteration_id ?? '') })
    setForm(null)
    setMore(false)
    setNotice({ kind: 'ok', text: FORM_TEXT[which].done })
    return null
  }

  const live = task?.status === 'draft' || task?.status === 'active'
  const roundLive = iteration?.status === 'draft' || iteration?.status === 'active'
  const pending = task?.receipts.filter((r) => r.pending) ?? []
  const nodes = iteration?.nodes ?? []
  const done = nodes.filter((n) => n.state === '核对通过').length
  const stuck = nodes.filter((n) => n.state !== '核对通过')

  return (
    <div className="flowp">
      <div className="page-head">
        <h1>流程编排</h1>
        <div className="head-actions">
          <button className="tf-btn" onClick={() => setShowNewTask(true)}>新建任务</button>
        </div>
      </div>

      <WorkPicker work={work} right={<Link className="tf-link" to="/evidence">证据与回溯 →</Link>} />

      {!task && (
        <EmptyState title="还没有任务" sub="新建一个任务：一句目标、一个 benchmark、一条流程模板。">
          <button className="tf-btn primary" onClick={() => setShowNewTask(true)}>新建任务</button>
        </EmptyState>
      )}

      {task && (
        <>
          <p className="flow-legend">{LEGEND}</p>

          <div className="block flow-goal">
            <div className="block-k">目标（不变量）——每一层看到的都是这同一句</div>
            <div className="flow-goal-text">{task.goal.statement}</div>
            <div className="flow-meta">
              <span>流程：{task.template_label || task.flow_template}</span>
              <span>benchmark：{task.benchmarks.map((b) => b.name).join('、') || '—'}</span>
            </div>
          </div>

          {/* 每一层由谁做、用什么：编排在这里被摊开给人看 */}
          <AgentTable nodes={nodes} guide={guide} />

          {/* 任务级的人的决定：章程没批、开某一轮没批……在这里处理 */}
          {(pending.length > 0 || task.status === 'draft') && (
            <div className="block flow-decisions">
              <div className="block-title">
                <strong>人的决定</strong>
                <span className="note">待批的事项处理完，这一轮才会开始</span>
              </div>
              <TaskReceipts receipts={task.receipts} />
            </div>
          )}

          {notice && <ActionNotice kind={notice.kind}>{notice.text}</ActionNotice>}

          {!iteration && (
            <>
              <EmptyState
                title="这个任务用的是「仅 benchmark」模板，不走层"
                sub="它只有目标、benchmark 与人的决定。记下 benchmark 的结果之后，由人封板。"
              />
              {live && !form && (
                <div className="flow-foot">
                  <span />
                  <button className="tf-btn" onClick={() => open('task_seal')}>封板任务</button>
                </div>
              )}
            </>
          )}

          {iteration && (
            <>
              <div className="flow-round">
                <span className="flow-round-label">
                  {iteration.label}{iterationTitle(iteration) ? ` · ${iterationTitle(iteration)}` : ''}
                  <span style={{ color: ITER_STATUS_META[iteration.status].color }}> · {ITER_STATUS_META[iteration.status].label}</span>
                </span>
                {iteration.seq > 1 && iteration.reason && (
                  <span className="muted small">开这一轮的理由：{iteration.reason}</span>
                )}
              </div>

              <Chain nodes={nodes} questions={questions} pkgOf={pkgOf} reusedOf={reusedOf} iterationId={iteration.iteration_id} />

              <div className="flow-foot">
                <div className="flow-progress" data-complete={iteration.complete}>
                  {iteration.complete
                    ? `本轮完成：${nodes.length} 个框都核对通过。`
                    : `本轮进度：${done} / ${nodes.length} 个框核对通过。`}
                </div>
                {live && !form && !roundLive && (
                  <div className="head-actions">
                    <button className="tf-btn primary" onClick={() => open('iter_new')}>开下一轮</button>
                    <button className="tf-btn" onClick={() => open('task_seal')}>封板任务</button>
                  </div>
                )}
                {live && !form && roundLive && (iteration.complete ? (
                  <div className="head-actions">
                    <button className="tf-btn primary" onClick={() => open('iter_new')}>开下一轮</button>
                    <button className="tf-btn" onClick={() => open('task_seal')}>封板任务</button>
                  </div>
                ) : more ? (
                  <div className="head-actions">
                    <button className="tf-btn" onClick={() => open('iter_seal')}>结束本轮（内容保留）</button>
                    <button className="tf-btn danger" onClick={() => open('iter_abort')}>终止本轮（不再推进）</button>
                    <button className="tf-link muted" onClick={() => setMore(false)}>收起</button>
                  </div>
                ) : (
                  <div className="head-actions">
                    <button className="tf-btn" onClick={() => open('task_seal')}>封板任务</button>
                    <button className="tf-link muted" onClick={() => setMore(true)}>提前结束这一轮…</button>
                  </div>
                ))}
              </div>
              {stuck.length > 0 && (
                <div className="muted small flow-stuck">
                  还没走完的框：{stuck.map((n) => `${n.layer} ${n.name}（${n.state}）`).join('、')}
                </div>
              )}

              {form && (
                <ReasonForm
                  title={FORM_TEXT[form].title}
                  hint={FORM_TEXT[form].hint}
                  verb={FORM_TEXT[form].verb}
                  signerHint={FORM_TEXT[form].signerHint}
                  onSubmit={(v) => submitForm(form, v.reason, v.signer)}
                  onCancel={() => setForm(null)}
                />
              )}
              {busy && !form && <div className="muted small">提交中…</div>}
            </>
          )}
        </>
      )}

      <StateLegend />

      {showNewTask && (
        <TaskForm
          onClose={() => setShowNewTask(false)}
          onCreated={(taskId, iterationId) => {
            setShowNewTask(false)
            work.select({ task: taskId, iteration: iterationId })
          }}
        />
      )}
    </div>
  )
}
