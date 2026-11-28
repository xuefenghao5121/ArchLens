import type { EvidenceLine, Give, GiveKind } from '../../types'

/** 测试结果的人读说法 */
export const VERDICT_WORD: Record<string, string> = {
  pass: '通过', fail: '未通过', unresolved: '不确定', not_applicable: '不适用',
}
export const verdictWord = (v?: string) => VERDICT_WORD[v ?? ''] ?? v ?? '—'

export const GIVE_KIND_LABEL: Record<GiveKind, string> = {
  root_cause: '根因/机制',
  measurement: '实测证据',
  exclusion: '排除项',
  scope: '适用边界',
  decision: '决定/建议',
  other: '其他结论',
}

/** 结论类型：优先用接口卡 kind；旧包/未标注时按文字推断，便于分组展示。 */
export function giveKind(g: Give): GiveKind {
  if (g.kind && g.kind in GIVE_KIND_LABEL) return g.kind
  const t = g.text || ''
  if (/根因|原因|机制|判定|缺陷|根因定位/.test(t)) return 'root_cause'
  if (/替代解释|不是来自|非来自|^排除|排除项/.test(t)) return 'exclusion'
  if (/实测|对拍|验证|通过|结果|EQUIV|差异|测量/.test(t)) return 'measurement'
  if (/边界|范围|限于|其它输入|其他输入|适用/.test(t)) return 'scope'
  if (/决定|建议|选择|方案|推荐/.test(t)) return 'decision'
  return 'other'
}
const basename = (p?: string) => (p ? p.split('/').pop() ?? p : '')
/** 包号只显示层号，避免在结论行里铺一长串 PKG-... */
export const pkgShort = (p?: string) => (p ? p.replace(/^PKG-(L\d+)-.*$/, '$1') : '')

/** 工件类型的人话；文件名能更具体说明用途时优先用文件名判断。 */
export function artifactPurpose(type?: string, path?: string): string {
  const n = basename(path).toLowerCase()
  if (n.includes('drive') || n.includes('stimulus') || n.includes('tb_')) return '测试输入/定向激励'
  if (n.includes('base_src') || n.includes('golden')) return '被观测的 RTL 基线'
  if (n.includes('issue')) return '问题记录'
  return ({
    rtl_source: '被观测/修改的 RTL 源', kernel: '源文件/内核', test_artifact: '测试输入/定向激励',
    stimulus: '测试输入/定向激励', report: '问题记录/报告', spec_doc: '规格/说明文档',
    data: '基线/测量数据', stats: '统计结果', log: '运行日志', script: '复现脚本',
    config: '配置', environment: '环境记录', binary: '二进制产物', manifest: '清单', other: '登记工件',
  } as Record<string, string>)[type ?? ''] ?? (type || '登记工件')
}

/** 一条交给下游的结论：编号、真值、基架、正文；它引用的证据跟在后面。层详情与证据页共用。 */
export function GiveRow({ g, ev, truth, frames, idx, onOpenArtifact }: {
  g: Give
  ev: Map<string, EvidenceLine>
  truth: Record<string, string>
  frames: Record<string, string>
  idx?: number
  onOpenArtifact?: (id: string) => void
}) {
  return (
    <div className="give">
      <span className="give-id" title={`下游引用编号：${g.id}`}>
        {idx != null ? `结论 ${idx + 1}` : g.id}
        {idx != null && <span className="give-sid">{g.id}</span>}
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
              if (e.kind === 'artifact' && onOpenArtifact) {
                return (
                  <button key={id} type="button" className="give-ev-item give-ev-open"
                    title="在工作台打开/审核这个工件"
                    onClick={() => onOpenArtifact(id)}>
                    工件 {basename(e.path) || id}（{artifactPurpose(e.artifact_type, e.path)}）
                  </button>
                )
              }
              return (
                <span key={id} className="give-ev-item">
                  {e.kind === 'test'
                    ? `${e.package_ref ? `来自 ${pkgShort(e.package_ref)} · ` : ''}测试「${e.title}」：${verdictWord(e.verdict)}（${e.strength ?? '—'}）`
                    : `工件 ${basename(e.path) || id}（${artifactPurpose(e.artifact_type, e.path)}，哈希 ${e.hash ?? '—'}）`}
                </span>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
