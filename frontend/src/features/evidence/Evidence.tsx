import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { FlowNode, LayerCard, LayerPackageView } from '../../types'
import { FRAME_LABEL, TRUTH_LABEL, fmtTime } from '../../status'
import { GiveRow, verdictWord } from '../shared/Give'
import { EmptyState, PageErr, PageLoad, StateChip, StateLegend } from '../shared/ui'
import { flowHref, layerHref, useWork } from '../tasks/useWork'
import WorkPicker from '../tasks/WorkPicker'
import './evidence.css'

const INTRO = '按这一轮的流程顺序，从上往下读。每一层先写它交出的结论，再列支撑这些结论的证据；' +
  '结论上面一行是「用了上游的哪几条」，点一下回到那一层——这就是回溯。'

const TRUTH_MEANING: Record<string, string> = {
  observed: '跑出来的数：必须有登记在册且通过的测试结果',
  deduced: '从实测推出来的',
  assumed: '人拍的板，没有测量支撑',
}

/** 回溯：这一层的结论建立在谁的哪几条结论上 */
function Trace({ card, pkgOf }: { card: LayerCard; pkgOf: (id: string) => LayerPackageView | undefined }) {
  const name = (id: string) => {
    const p = pkgOf(id)
    return p ? `${p.card.layer} ${p.card.layer_name}` : id
  }
  const takes = card.interface.takes ?? []
  const ups = takes.length
    ? takes.map((t) => ({ id: t.from, text: `${name(t.from)} 的 ${t.statement}` }))
    : card.parents.map((id) => ({ id, text: name(id) }))
  if (!ups.length && !card.context.pulls.length) return null
  return (
    <div className="evb-trace">
      {ups.length > 0 && (
        <>
          <span className="ev-k">用了上游的{!takes.length && card.parents_derived ? '（按流程顺序推得）' : ''}：</span>
          {ups.map((u) => (
            <Link key={u.id} to={layerHref(u.id)} className="evb-up">
              {u.text}
              {pkgOf(u.id) && <StateChip word={pkgOf(u.id)!.state} small />}
            </Link>
          ))}
        </>
      )}
      {card.context.pulls.length > 0 && (
        <>
          <span className="ev-k">另读：</span>
          {card.context.pulls.map((p) => (
            <Link key={p.pkg + p.at} to={layerHref(p.pkg)} className="evb-up" title={`${p.by} ${fmtTime(p.at)}`}>
              {p.pkg.replace(/^PKG-L\dd?-[^-]+-/, '')}
              <span className="muted small">{p.by} {fmtTime(p.at)}</span>
            </Link>
          ))}
        </>
      )}
    </div>
  )
}

function LayerBlock({ node, pkgOf, reusedOf, truth, frames }: {
  node: FlowNode
  pkgOf: (id: string) => LayerPackageView | undefined
  reusedOf: (id: string) => string | undefined
  truth: Record<string, string>
  frames: Record<string, string>
}) {
  const pkgs = node.own.concat(node.reused).map(pkgOf).filter((p): p is LayerPackageView => !!p)
  return (
    <div className="evb">
      <div className="evb-head">
        <span className="evb-layer">{node.layer} {node.name}</span>
        {pkgs.length > 0 && <StateChip word={pkgs.length === 1 ? pkgs[0].state : node.state} />}
        <span className="muted small">{pkgs[0]?.card.guide.question ?? ''}</span>
      </div>
      {pkgs.length === 0 && <div className="muted small">这一轮没有这一层的包。</div>}
      {pkgs.map((p) => {
        const c = p.card
        const ev = new Map(c.evidence.map((e) => [e.id, e]))
        return (
          <div key={p.package_id} className="evb-pkg">
            {pkgs.length > 1 && (
              <div className="evb-pkg-head">
                <StateChip word={p.state} small />
                <Link to={layerHref(p.package_id)}>{reusedOf(p.package_id) ? `沿用 ${reusedOf(p.package_id)} · ` : ''}{p.title}</Link>
              </div>
            )}
            <Trace card={c} pkgOf={pkgOf} />
            <div className="block-k">结论</div>
            {c.interface.gives.length
              ? c.interface.gives.map((g) => <GiveRow key={g.id + g.text} g={g} ev={ev} truth={truth} frames={frames} />)
              : <div className="muted small">（还没有产出）</div>}
            <div className="block-k">证据</div>
            {c.evidence.length === 0 && <div className="muted small">还没有登记任何证据。</div>}
            {c.evidence.map((e) => (
              <div key={e.id} className="ev-line">
                {e.kind === 'test' ? (
                  <>
                    <div>
                      <span className="ev-k">测试 </span>{e.title}
                      <span className={'ev-' + (e.verdict === 'pass' ? 'pass' : 'fail')}> {verdictWord(e.verdict)}</span>
                      <span className="ev-k">（{e.strength}）</span>
                    </div>
                    <div className="ev-k">
                      {e.executor ? `${e.executor} · ` : ''}{fmtTime(e.at)} · {e.id}
                      {e.for_statement && e.for_statement.length ? ` · 支撑 ${e.for_statement.join('、')}` : ''}
                    </div>
                  </>
                ) : (
                  <>
                    <div><span className="ev-k">工件 </span><span className="mono">{e.id}</span> <span className="ev-k">{e.artifact_type}</span></div>
                    <div className="ev-k mono ev-path">{e.path || ''}</div>
                    {e.hash && <div className="ev-k">哈希 {e.hash}{e.cited === false ? ' · 没有被结论引用' : ''}</div>}
                  </>
                )}
              </div>
            ))}
            {c.reproduce_command && (
              <>
                <div className="block-k">复现</div>
                <div className="ev-cmd">{c.reproduce_command}</div>
              </>
            )}
            <Link className="evb-more" to={layerHref(p.package_id)}>这一层的详情（核对回执、人的决定）→</Link>
          </div>
        )
      })}
    </div>
  )
}

export default function Evidence() {
  const work = useWork()
  const { state: data, task, iteration, pkgOf, reusedOf } = work
  const { data: guide } = useQuery({ queryKey: ['guide'], queryFn: api.guide })

  if (work.isLoading) return <PageLoad />
  if (work.error) return <PageErr error={work.error} />
  if (!data) return null

  const truth = guide?.truth ?? TRUTH_LABEL
  const frames = guide?.frames ?? FRAME_LABEL
  const nodes = iteration?.nodes ?? []

  return (
    <div className="evp">
      <div className="page-head"><h1>证据与回溯</h1></div>
      <WorkPicker
        work={work}
        right={<Link className="tf-link" to={flowHref(task?.task_id, iteration?.iteration_id)}>流程编排 →</Link>}
      />
      <p className="ev-intro">{INTRO}</p>

      {!task && <EmptyState title="还没有任务" />}
      {task && !iteration && (
        <EmptyState title="这个任务用的是「仅 benchmark」模板，不走层" sub="没有层包，也就没有逐层的证据。" />
      )}
      {iteration && nodes.length > 0 && (
        <div className="ev-chain">
          {nodes.map((n, i) => (
            <div key={n.layer}>
              {i > 0 && <div className="evb-down" aria-hidden>↓ 交给下一层</div>}
              <LayerBlock node={n} pkgOf={pkgOf} reusedOf={reusedOf} truth={truth} frames={frames} />
            </div>
          ))}
        </div>
      )}

      {/* 几个标记是什么意思 */}
      <div className="block" style={{ marginTop: 14 }}>
        <div className="block-title"><strong>标记是什么意思</strong></div>
        <dl className="legend-list">
          {(Object.keys(truth) as (keyof typeof truth)[]).map((k) => (
            <div key={k} className="legend-row">
              <dt><span className={`tag ${k}`}>{truth[k]}</span></dt>
              <dd>{TRUTH_MEANING[k] ?? ''}</dd>
            </div>
          ))}
          {(Object.keys(frames) as (keyof typeof frames)[]).map((k) => (
            <div key={k} className="legend-row">
              <dt><span className="tag">{k}</span></dt>
              <dd>数字产自：{frames[k]}</dd>
            </div>
          ))}
        </dl>
      </div>
      <StateLegend />
      <div className="dash-foot mono">
        store：{data.store_isolated ? '隔离副本' : '真实 store'} · {data.store_root}
        {data.summary.hidden_packages > 0 ? ` · 另有 ${data.summary.hidden_packages} 个包属于已结束的迭代` : ''}
      </div>
    </div>
  )
}
