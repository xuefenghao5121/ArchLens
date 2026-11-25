import type { ReactNode } from 'react'
import type { StateWord } from '../../types'
import { STATE_META } from '../../status'
import './shared.css'

/** 唯一的状态词。后面跟的那句原因由调用方决定要不要显示。 */
export function StateChip({ word, why, small }: { word: StateWord; why?: string; small?: boolean }) {
  const m = STATE_META[word] ?? { color: '#9a9aab', meaning: '' }
  return (
    <span
      className={'state-chip' + (small ? ' small' : '')}
      style={{ color: m.color, borderColor: m.color }}
      title={why || m.meaning}
      data-state={word}
    >
      {word}
    </span>
  )
}

/** 状态词的解释。给第一次看的人：一句话说清每个词是什么意思。 */
export function StateLegend() {
  const order: StateWord[] = ['未开始', '进行中', '待人决定', '未验证', '有问题', '已验证', '已作废']
  return (
    <details className="legend">
      <summary>状态词是什么意思</summary>
      <dl className="legend-list">
        {order.map((w) => (
          <div key={w} className="legend-row">
            <dt><StateChip word={w} small /></dt>
            <dd>{STATE_META[w].meaning}</dd>
          </div>
        ))}
      </dl>
    </details>
  )
}

/** 一次写操作的结果：成功绿色、失败红色；版本冲突时提示直接重试 */
export function ActionNotice({ children, kind }: { children: ReactNode; kind: 'ok' | 'err' | 'info' }) {
  if (children == null || (Array.isArray(children) && children.length === 0)) return null
  return <div className={'notice ' + kind}>{children}</div>
}

export function PageLoad({ text = '加载中…' }: { text?: string }) {
  return <div className="page-load">{text}</div>
}

export function PageErr({ error }: { error: unknown }) {
  return <div className="page-err">加载失败：{(error as Error).message}</div>
}

export function EmptyState({ title, sub, children }: { title: ReactNode; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="empty-state">
      <div>{title}</div>
      {sub && <div className="empty-sub">{sub}</div>}
      {children}
    </div>
  )
}
