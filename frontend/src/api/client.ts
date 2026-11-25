import type {
  ActionRequest,
  ActionResponse,
  BenchmarkInfo,
  EvolutionState,
  GuideResponse,
  PackageDetail,
  TemplatesResponse,
  WorkflowState,
} from '../types'

function errorText(parsed: unknown, fallback: string): string {
  // 服务端出错时返回 {error: "…"}；把原因带给界面，而不是只报一个 HTTP 状态码
  if (parsed && typeof parsed === 'object' && 'error' in parsed && typeof (parsed as { error: unknown }).error === 'string') {
    return (parsed as { error: string }).error
  }
  return fallback
}

async function get<T>(url: string): Promise<T> {
  const resp = await fetch(url)
  const text = await resp.text()
  let parsed: unknown = null
  try {
    parsed = JSON.parse(text)
  } catch {
    parsed = null
  }
  if (!resp.ok) {
    throw new Error(`${errorText(parsed, `HTTP ${resp.status}`)}（HTTP ${resp.status}: ${url}）`)
  }
  return parsed as T
}

async function post<T>(url: string, body: unknown): Promise<T> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const text = await resp.text()
  let parsed: unknown = null
  try {
    parsed = JSON.parse(text)
  } catch {
    parsed = null
  }
  if (!resp.ok) {
    // 服务端对被拒/版本冲突/幂等冲突返回 4xx + {ok:false, error}：交给界面按结果展示
    if (parsed && typeof parsed === 'object' && 'ok' in parsed) {
      return parsed as T
    }
    throw new Error(`${errorText(parsed, `HTTP ${resp.status}`)}（HTTP ${resp.status}: ${url}）`)
  }
  return parsed as T
}

/** 稳定序列化（键排序），让"内容相同"的请求得到同一个幂等键 */
function stable(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(stable).join(',')}]`
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>
    return `{${Object.keys(o).sort().map((k) => `${JSON.stringify(k)}:${stable(o[k])}`).join(',')}}`
  }
  return JSON.stringify(v)
}

/**
 * 幂等键由请求内容决定：同一动作 + 同一目标 + 同一 store 版本重发时，服务端返回上次的
 * 结果而不是再写一遍（重复点击、网络重试都不会落两条记录）。store 一变键就变，所以
 * 有意再次执行同一动作不受影响。
 */
function idemKey(payload: Record<string, unknown>): string {
  const s = stable(payload)
  let h = 5381
  for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0
  return `ui-${h.toString(36)}-${s.length.toString(36)}`
}

export const api = {
  /** 全量状态：每个层包带状态词与卡片，每个迭代带模板顺序的框，每个任务带决定回执 */
  state: () => get<WorkflowState>('/api/state'),

  evoState: () => get<EvolutionState>('/api/evo-state'),

  benchmarks: () => get<BenchmarkInfo[]>('/api/benchmarks'),

  templates: () => get<TemplatesResponse>('/api/templates'),

  guide: () => get<GuideResponse>('/api/guide'),

  /** 层包详情：卡片 + 原始记录 */
  package: (ref: string) => get<PackageDetail>(`/api/package/${encodeURIComponent(ref)}`),

  action: (body: ActionRequest) => {
    const payload: Record<string, unknown> = { ...body }
    const provided = payload.idempotency_key as string | undefined
    delete payload.idempotency_key
    return post<ActionResponse>('/api/action', {
      ...payload,
      idempotency_key: provided ?? idemKey(payload),
    })
  },
}
