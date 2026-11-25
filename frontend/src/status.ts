import type { LayerId, StateWord, TaskStatus, Truth, Frame } from './types'

// 各页共用的呈现：层名、状态词、任务状态。颜色与解释只在这里写一份。

/** 回执的一句话：做了什么。批准 / 驳回记录写成「批准「…」」，不再叠一层「决定：」 */
export function receiptAction(r: { summary: string; about?: string; outcome?: string }): string {
  if (r.outcome) {
    const target = r.about || r.summary.replace(/^(批准|驳回)[：:]\s*/, '')
    return `${r.outcome}「${target}」`
  }
  return `决定：${r.summary || '（没有写一句话结论）'}`
}

export const LAYERS: LayerId[] = ['L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'L7', 'L8']

export const LAYER_NAME: Record<string, string> = {
  L1: '观测', L2: '归因', L3: '方案', L4: '契约', L5: 'RTL', L6: '集成', L7: '实验', L8: '结论',
}

export const STATE_META: Record<StateWord, { color: string; meaning: string }> = {
  未开始: { color: '#9a9aab', meaning: '这一层还没有产出' },
  进行中: { color: '#60a5fa', meaning: '有产出了，还没核对通过' },
  待人决定: { color: '#fbbf24', meaning: '需要你签字或做决定，做完才能交给下游' },
  未验证: { color: '#fb923c', meaning: '缺验证：没有通过的测试，或核对之后内容又改过' },
  有问题: { color: '#f87171', meaning: '核对没通过，原因写在核对回执里' },
  已验证: { color: '#34d399', meaning: '核对通过，下游可以用它交出的结论' },
  已作废: { color: '#64748b', meaning: '这一层的结论作废，不再传给下游' },
}

export const STATE_ORDER: StateWord[] = ['未开始', '进行中', '待人决定', '未验证', '有问题', '已验证', '已作废']

export const TASK_STATUS_META: Record<TaskStatus, { label: string; color: string }> = {
  draft: { label: '章程待批', color: '#fbbf24' },
  active: { label: '进行中', color: '#34d399' },
  sealed: { label: '已封板', color: '#9a9aab' },
  aborted: { label: '已终止', color: '#f87171' },
}

/** 迭代的状态：同一组取值，但说法不同——迭代 sealed 是「已结束」，任务 sealed 才是「已封板」 */
export const ITER_STATUS_META: Record<TaskStatus, { label: string; color: string }> = {
  draft: { label: '待批', color: '#fbbf24' },
  active: { label: '进行中', color: '#34d399' },
  sealed: { label: '已结束', color: '#9a9aab' },
  aborted: { label: '已终止', color: '#f87171' },
}

/** 三个真值标记与 F0–F3 的人读含义。正常从 /api/guide 取；取不到时用这份兜底（与引擎一致） */
export const TRUTH_LABEL: Record<Truth, string> = { observed: '实测', deduced: '推断', assumed: '拍定' }
export const FRAME_LABEL: Record<Frame, string> = {
  F0: '仿真器默认参数（未校准）', F1: '契约拍定参数', F2: 'RTL 实测背书', F3: '解析模型推导',
}

/** 迭代标题去掉与前缀重复的「迭代 N：」；标题本来就只是「迭代 N」时返回空串，页签里才不会重复 */
export function iterationTitle(i: { label: string; title: string }): string {
  const t = i.title.replace(new RegExp(`^${i.label}\\s*[：:]\\s*`), '').trim()
  return t === i.label ? '' : t
}

/** 层包去掉 PKG-Lx-位置- 前缀，留能区分包的那一段；完整 ID 放悬浮提示 */
export const shortPkg = (id: string) => id.replace(/^PKG-L\d+d?-[^-]+-/, '')

/** 「2026-11-17T18:28:31…」→「2026-11-17 18:28」 */
export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return ''
  return iso.slice(0, 16).replace('T', ' ')
}

/** 字节数 → 人能读的大小（benchmark 清单用） */
export function humanSize(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}
