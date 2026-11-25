import type { EvidenceLine, Give } from '../../types'

/** 测试结果的人读说法 */
export const VERDICT_WORD: Record<string, string> = {
  pass: '通过', fail: '未通过', unresolved: '不确定', not_applicable: '不适用',
}
export const verdictWord = (v?: string) => VERDICT_WORD[v ?? ''] ?? v ?? '—'

/** 一条交给下游的结论：编号、真值、基架、正文；它引用的证据跟在后面。层详情与证据页共用。 */
export function GiveRow({ g, ev, truth, frames }: {
  g: Give
  ev: Map<string, EvidenceLine>
  truth: Record<string, string>
  frames: Record<string, string>
}) {
  return (
    <div className="give">
      <span className="give-id">
        {g.id}
        <span className="tags">
          <span className={`tag ${g.truth}`}>{truth[g.truth] ?? g.truth}</span>
          {g.frame && <span className="tag" title={frames[g.frame]}>{g.frame}</span>}
        </span>
      </span>
      <div className="give-text">
        {g.text}
        {g.source && <span className="give-src">　［摘自 {g.source}］</span>}
        {g.evidence.length > 0 && (
          <div className="give-ev">
            {g.evidence.map((id) => {
              const e = ev.get(id)
              if (!e) return <span key={id} className="give-ev-item">{id}（不在登记册）</span>
              return (
                <span key={id} className="give-ev-item">
                  {e.kind === 'test'
                    ? `测试「${e.title}」：${verdictWord(e.verdict)}（${e.strength ?? '—'}）`
                    : `工件 ${id}（${e.artifact_type}，哈希 ${e.hash ?? '—'}）`}
                </span>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
