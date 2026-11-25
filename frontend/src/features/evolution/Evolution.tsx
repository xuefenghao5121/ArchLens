import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import './evolution.css'

const STATUS_COLOR: Record<string, string> = {
  champion: '#34d399', challenger: '#fbbf24', rejected: '#f87171',
  draft: '#9a9aab', evaluating: '#22d3ee', superseded: '#64748b', rolled_back: '#fb923c',
}

/** Champion/Challenger 指标并排对比（两边都是对象时逐项列，否则原样展示） */
function MetricsView({ champion, challenger }: {
  champion: Record<string, unknown> | null
  challenger: Record<string, unknown> | null
}) {
  if (!champion && !challenger) return null
  const keys = [...new Set([...Object.keys(champion ?? {}), ...Object.keys(challenger ?? {})])]
  const cell = (v: unknown) => (v === undefined ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v))
  return keys.length > 0 ? (
    <table className="evo-table">
      <thead><tr><th>指标</th><th>Champion</th><th>Challenger</th></tr></thead>
      <tbody>
        {keys.map((k) => (
          <tr key={k}>
            <td>{k}</td>
            <td>{cell((champion ?? {})[k])}</td>
            <td>{cell((challenger ?? {})[k])}</td>
          </tr>
        ))}
      </tbody>
    </table>
  ) : (
    <div className="evo-metrics mono">Champion: {JSON.stringify(champion)} · Challenger: {JSON.stringify(challenger)}</div>
  )
}

export default function Evolution() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['evo-state'],
    queryFn: () => api.evoState(),
  })

  if (isLoading) return <div className="page-load">加载中…</div>
  if (error) return <div className="page-err">加载失败：{(error as Error).message}</div>
  if (!data) return null

  const s = data.summary
  const lineage = data.lineage ?? []

  return (
    <div className="evo">
      <h1>演进中心</h1>
      <p className="evo-sub">RSI 自迭代：候选 → 隔离评测（带指标）→ Challenger → 晋级 → Champion → 回滚</p>

      <div className="metrics">
        <div className="metric"><div className="m-k">候选总数</div><div className="m-v">{s.total}</div></div>
        <div className="metric"><div className="m-k">Champion</div><div className="m-v" style={{ color: '#34d399' }}>{s.champions}</div></div>
        <div className="metric"><div className="m-k">Challenger</div><div className="m-v" style={{ color: '#fbbf24' }}>{s.challengers}</div></div>
        <div className="metric"><div className="m-k">Rejected</div><div className="m-v" style={{ color: '#f87171' }}>{s.rejected}</div></div>
        <div className="metric"><div className="m-k">已回滚</div><div className="m-v" style={{ color: '#fb923c' }}>{s.rolled_back ?? 0}</div></div>
      </div>

      <div className="evo-list">
        {data.evolutions.length === 0 && (
          <div className="page-load">（无演进候选。用 <code>wf evo-new</code> 创建第一个）</div>
        )}
        {data.evolutions.map((e) => {
          // 谱系：本候选取代了谁 / 被谁取代
          const replaces = lineage.filter((x) => x.from === e.candidate_id).map((x) => x.to)
          const replacedBy = lineage.filter((x) => x.to === e.candidate_id).map((x) => x.from)
          return (
            <div key={e.candidate_id} className="evo-card" style={{ borderLeftColor: STATUS_COLOR[e.status] ?? '#9a9aab' }}>
              <div className="evo-head">
                <span className="evo-status" style={{ color: STATUS_COLOR[e.status] ?? '#9a9aab' }}>{e.status}</span>
                <span className="mono">{e.candidate_id}</span>
                <span className="evo-comp">[{e.target_component}]</span>
                <span className="evo-type">{e.change_type}</span>
                {e.eval_verdict && <span className="evo-type">评测 {e.eval_verdict}</span>}
              </div>
              <div className="evo-change">{e.change_set}</div>
              {e.expected_benefit && <div className="evo-benefit">预期收益：{e.expected_benefit}</div>}
              <MetricsView champion={e.champion_metrics} challenger={e.challenger_metrics} />
              {(replaces.length > 0 || replacedBy.length > 0) && (
                <div className="evo-lineage">
                  {replaces.length > 0 && <div>取代了：{replaces.join('、')}</div>}
                  {replacedBy.length > 0 && <div>被取代于：{replacedBy.join('、')}{e.status === 'champion' ? '（回滚后恢复）' : ''}</div>}
                </div>
              )}
              {e.status === 'challenger' && (
                <div className="evo-cmd">晋级：<code>wf evo-promote --candidate {e.candidate_id}</code></div>
              )}
              {e.status === 'draft' || e.status === 'evaluating' ? (
                <div className="evo-cmd">先评测：<code>wf evo-eval --candidate {e.candidate_id} --verdict promote --champion '…' --challenger '…'</code></div>
              ) : null}
            </div>
          )
        })}
      </div>
    </div>
  )
}
