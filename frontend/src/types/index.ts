// 与 wf.py state-export / evo-state、viz server.py 的输出 JSON 对齐（workflow-state schema 0.4）
//
// 人读的内容都在「卡片」里：每个层包一个 state（唯一的状态词）和一张 card（接口卡、核对回执、证据、决定回执）。

export type LayerId = 'L1' | 'L2' | 'L3' | 'L4' | 'L5' | 'L6' | 'L7' | 'L8'

export type Lifecycle =
  | 'draft' | 'ready' | 'running' | 'needs_decision' | 'validating'
  | 'passed' | 'failed' | 'unresolved' | 'rejected'
  | 'invalidated' | 'superseded' | 'unknown'

// 任务 / 迭代共用的状态：draft=章程待批，active=进行中，sealed=已封板，aborted=已终止
export type TaskStatus = 'draft' | 'active' | 'sealed' | 'aborted'

/** 唯一的状态词：由核对结果机械得出，后面跟一句原因 */
export type StateWord = '未开始' | '进行中' | '待人决定' | '未验证' | '有问题' | '已验证' | '已作废'

export type Truth = 'observed' | 'deduced' | 'assumed'
export type Frame = 'F0' | 'F1' | 'F2' | 'F3'

export interface ArtifactRef {
  artifact_id: string
  artifact_type: string
  content_hash: string
}

// ---- 层接口卡：层与层之间传递信息的唯一格式 ----

/** 交给下游的一条结论。source=旧包摘录时的来源字段 */
export interface Give {
  id: string
  text: string
  truth: Truth
  frame?: Frame | null
  evidence: string[]
  source?: string
}
/** 用了上游的哪条结论 */
export interface Take { from: string; statement: string }
/** 没解决的问题，以及该由谁回答 */
export interface OpenQuestion { question: string; ask: 'human' | 'next'; options?: string[] }

export interface InterfaceCard {
  question: string
  gives: Give[]
  takes?: Take[]
  open?: OpenQuestion[]
  limits?: string[]
  // true=旧包没有接口卡，这一份是从原有字段摘录的
  derived?: boolean
}

// ---- 核对回执：五行人话 ----

export interface CheckRow {
  id: 'deliverables' | 'artifacts' | 'upstream' | 'human' | 'tests' | string
  label: string
  // null=本层不适用
  ok: boolean | null
  detail: string
  mark: '✓' | '✗' | '—'
}

// ---- 决定回执：谁 · 何时 · 决定了什么 · 所审内容的指纹 ----

export interface Receipt {
  decision_id: string
  pending: boolean
  summary: string
  selected: string[]
  by: string
  at: string
  content_sha: string
  // 所审内容在其后改过：回执已过期
  stale: boolean
  about?: string
  outcome?: '批准' | '驳回'
}

export interface EvidenceLine {
  id: string
  kind: 'test' | 'artifact'
  title: string
  // test
  verdict?: string
  strength?: string
  proposition?: string
  executor?: string
  at?: string
  for_statement?: string[]
  log?: string
  raw?: string
  // artifact
  artifact_type?: string
  hash?: string
  path?: string
  cited?: boolean
}

/** 每层的固定说明（界面与 wf brief 共用一份） */
export interface LayerGuide {
  question: string
  role: string
  role_label: string
  skills: string[]
  consumes: string
  produces: string
  // 需要人签字 / 需要登记在册的测试通过
  human: boolean
  needs_test: boolean
  deliverables: string[]
}

export interface LayerCard {
  package_id: string
  layer: LayerId
  layer_name: string
  title: string
  owner: string
  task_id: string | null
  iteration_id: string | null
  iteration_label: string | null
  // 任务目标的原话（不变量）：每一层看到同一句
  goal: string
  intent: string
  state: { word: StateWord; why: string; lifecycle: Lifecycle; verdict: string }
  guide: LayerGuide
  interface: InterfaceCard
  checks: CheckRow[]
  checked_at: string
  gate_report: string
  receipts: Receipt[]
  evidence: EvidenceLine[]
  reproduce_command: string
  parents: string[]
  parents_derived: boolean
  // 核对通过之后接口卡又改过
  interface_drift: boolean
  artifact_count: number
}

export interface LayerPackageView {
  package_id: string
  state: StateWord
  card: LayerCard
  title: string
  lifecycle: Lifecycle
  gate_verdict: string
  invalidated: boolean
  parent_package_refs: string[]
  artifacts: ArtifactRef[]
  decision_pending: boolean
  task_id: string | null
  iteration_id: string | null
  // 只出现在「沿用」条目上：这个包属于哪一轮迭代（如「迭代 1」）
  reused_from?: string | null
}

// ---- 任务 → 迭代 → 框 ----

/** 流程编排里的一个「框」：模板顺序上的一层，本轮自有 / 沿用的包，以及合成后的状态 */
export interface FlowNode {
  layer: string
  name: string
  alias: string
  own: string[]
  reused: string[]
  state: StateWord
}

export interface TaskReceipt {
  decision_id: string
  pending: boolean
  // 章程 / 开迭代 2 / 结束迭代 1 / 封板；任务上的其他决定为空串
  kind: string
  summary: string
  by: string
  at: string
  about?: string
  outcome?: '批准' | '驳回'
}

export interface IterationLayer {
  // own=本迭代自己建的包；reused=本层没有自有包时沿用的上游包（属于更早的迭代）
  own: LayerPackageView[]
  reused: LayerPackageView[]
}

export interface IterationView {
  iteration_id: string
  seq: number
  label: string
  status: TaskStatus
  title: string
  reason: string
  started_at: string
  flow_template: string
  nodes: FlowNode[]
  // 本轮完成 = 模板里每个框都已验证
  complete: boolean
  // 第一个没到「已验证」的框；null = 全部已验证
  frontier: string | null
  reached_l8: boolean
  pending_decisions: number
  layers: Record<LayerId, IterationLayer>
}

export interface TaskGoal {
  statement: string
  // 历史章程里可能带着，仅存档；不再是输入项
  success_criteria?: string[]
  budget?: string
  tradeoff_policy?: string
  stop_rule?: string
}

export interface TaskBenchmark {
  name: string
  kind?: string
  artifact_refs?: string[]
}

export interface TaskView {
  task_id: string
  title: string
  flow_template: string
  template_label: string
  status: TaskStatus
  goal: TaskGoal
  benchmarks: TaskBenchmark[]
  charter_decision_id: string
  // 任务级的决定回执（章程 / 开某一轮 / 封板）
  receipts: TaskReceipt[]
  // 默认看哪一轮：最新的进行中迭代，否则最后一轮
  default_iteration: string | null
  pending_decisions: number
  iterations: IterationView[]
}

export interface PendingDecision {
  decision_id: string
  package_ids: string[]
  task_id: string
  decision_type: string
  // 一句话结论；没有 summary 的历史决策为正文第一个分句（headline）
  summary: string
  headline: string
  rationale: string
}

export interface WorkflowSummary {
  total_packages: number
  layer_counts: Record<string, number>
  frontier: string
  archived: number
  pending_decisions: number
  consumption_warnings: number
  view_packages: number
  hidden_packages: number
  unassigned_packages: number
  active_iteration: string | null
}

export interface WorkflowState {
  schema_version: string
  generated_at: string
  generated_by: string
  store_env_hash: string
  store_root?: string
  store_isolated?: boolean
  summary: WorkflowSummary
  layers: Record<string, { packages: LayerPackageView[] }>
  tasks: TaskView[]
  dag: { nodes: unknown[]; edges: unknown[] }
  pending_decisions: PendingDecision[]
}

// ---- 流程模板 / 层说明 ----

export interface TemplateInfo {
  id: string
  label: string
  description: string
  layers: { layer: string; name: string; alias: string }[]
}
export interface TemplatesResponse { templates: TemplateInfo[]; default: string }

export interface GuideResponse {
  layers: (LayerGuide & { layer: LayerId; name: string })[]
  truth: Record<Truth, string>
  frames: Record<Frame, string>
  strength: Record<string, string>
  states: StateWord[]
  interface: { limits: Record<string, number>; gives_max: number }
}

// ---- 演进中心（不在这一轮改动范围）----

export interface EvolutionCandidate {
  candidate_id: string
  target_component: string
  parent_version: string
  change_type: string
  change_set: string
  expected_benefit: string
  generated_by: string
  agent: string
  status: string
  created_at: string
  eval_verdict: string | null
  champion_metrics: Record<string, unknown> | null
  challenger_metrics: Record<string, unknown> | null
  promoted_at: string | null
  superseded_by?: string | null
  evaluated_at?: string
  rolled_back_at?: string
  rolled_back_reason?: string
  restored_at?: string
  restored_by_rollback?: string | null
}

export interface EvolutionState {
  schema_version: string
  generated_at: string
  evolutions: EvolutionCandidate[]
  champions: Record<string, string>
  lineage?: { from: string; to: string }[]
  summary: {
    total: number
    champions: number
    challengers: number
    rejected: number
    draft?: number
    superseded?: number
    rolled_back?: number
  }
}

// ---- benchmark ----

export interface BenchmarkRef {
  artifact_id: string
  artifact_type: string
  size: number | null
  file_count: number | null
  uri: string
  registered_at: string
}
export interface BenchmarkInfo {
  name: string
  kind: string
  artifact_refs: BenchmarkRef[]
  imported_at: string
}

// ---- 写操作 ----

export type ActionId =
  | 'ask' | 'propose' | 'decide' | 'revise' | 'what_if' | 'validate'
  | 'approve' | 'reject' | 'rollback'
  | 'bench_import' | 'task_new' | 'iter_new' | 'new_pkg'
  | 'iter_seal' | 'iter_abort' | 'task_seal'

export interface ActionRequest {
  action: ActionId
  pkg?: string
  dec?: string
  candidate?: string
  // 署名：批准 / 记决定 / 封板的人
  as?: string
  reason?: string
  note?: string
  rationale?: string
  // 一句话结论（propose / decide 必填）
  summary?: string
  confidence?: string
  type?: string
  selected?: string[]
  constraints?: string[]
  evidence?: string[]
  scope?: string
  // revise 专用：只预览影响范围，不写
  dry_run?: boolean
  // ---- bench_import / task_new / iter_new ----
  title?: string
  slug?: string
  name?: string
  kind?: string
  goal?: string
  benchmarks?: string[]
  template?: string
  create?: boolean
  owner?: string
  paths?: string[]
  task?: string
  iteration?: string
  layer?: string
  // 操作人：执行这次请求的 agent 或人，写进证据图与生命周期历史
  agent?: string
  idempotency_key?: string
  target_version?: string
}

export interface GateReport {
  verdict: string
  blocks: string[]
  warns: string[]
  gated_at: string
  dry_run: boolean
}

export interface ActionResponse {
  ok: boolean
  action: string
  idempotent_replay?: boolean
  rejected?: boolean
  result?: Record<string, unknown>
  output?: string
  error?: string
  message?: string
}

// ---- 层包详情：卡片 + 原始记录 ----

export interface PackageRaw {
  identity: Record<string, unknown>
  payload: Record<string, unknown>
  interface: InterfaceCard | null
  design_spec?: Record<string, unknown>
  contract_manifest?: Record<string, unknown>
  artifacts: ArtifactRef[]
  decision_refs: string[]
  provenance: Record<string, unknown>
}

export interface PackageDetail {
  package_id: string
  card: LayerCard
  raw: PackageRaw
  // 平铺字段：服务端为兼容老调用方保留
  title: string
  layer: string
  layer_name: string
  owner_agent: string
  lifecycle: string
  verdict: string
  gate_report: string
  parent_package_refs: string[]
  artifacts: unknown[]
  decision_refs: string[]
  validation: Record<string, unknown>
}
