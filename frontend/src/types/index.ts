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
export type StateWord = '未开始' | '进行中' | '待人决定' | '未验证' | '有问题' | '核对通过' | '已作废'

export type Truth = 'observed' | 'deduced' | 'assumed'
export type Frame = 'F0' | 'F1' | 'F2' | 'F3'

export interface ArtifactRef {
  artifact_id: string
  artifact_type: string
  content_hash: string
}

// ---- 层接口卡：层与层之间传递信息的唯一格式 ----

/** 交给下游的一条结论。source=旧包摘录时的来源字段 */
export type GiveKind = 'root_cause' | 'measurement' | 'exclusion' | 'scope' | 'decision' | 'other'

export interface Give {
  id: string
  text: string
  truth: Truth
  frame?: Frame | null
  evidence: string[]
  source?: string
  // 可选：agent 在接口卡里标注结论类型；没有时界面按文字推断
  kind?: GiveKind
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
  // 人写的那句话：批准时核对了什么、为什么驳回。旧记录没有留，就是空串
  note?: string
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
  // 这条测试结果登记在哪个包（上游引用的 RES 会显示 L7 等来源）
  package_ref?: string
  for_statement?: string[]
  log?: string
  raw?: string
  // 比的是什么：拿什么和什么比、在什么激励下。没记录时为 null/undefined，页面如实写「没有记录」
  comparison?: { under_test?: string; reference?: string; stimulus?: string } | null
  control?: string
  intervention?: string
  // artifact
  artifact_type?: string
  hash?: string
  path?: string
  role?: string
  /** 这份内容是哪来的：own 本层首次登记 / upstream 与上游登记的相同 / input 与输入相同 / preexisting 本包建立前就已登记 */
  origin?: { kind: 'own' | 'upstream' | 'input' | 'preexisting'; path?: string; at?: string; note?: string }
  cited?: boolean
  // 该工件是哪些 RES 的运行证据（测试原始日志/统计）
  for_test?: string[]
}

/** 层 agent 定义：谁做这一层、用什么、能写哪里、不许做什么 */
export interface AgentSkill { name: string; what: string }

export interface LayerAgent {
  name: string
  host_name: string
  role: string
  role_label: string
  // platform/agents/<层>/agent.md
  file: string
  skills: AgentSkill[]
  tools: AgentSkill[]
  writes: string[]
  must_not: string[]
  // 完整定义正文（只有 /api/guide 带）
  body?: string
}

/** 开工简报的回执：wf brief --out 写下时记 */
export interface BriefReceipt { at: string; by: string; sha256: string; upstream: string[] }
/** 接口卡之外另读上游的记录 */
export interface PullRecord { pkg: string; at: string; by: string }
/** 执行记录：谁、用什么模型、跑了多久 */
export interface RunRecord { at: string; agent: string; model?: string; duration_s?: number | null; note?: string }

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
  // 这一层由哪个 agent 做
  agent?: LayerAgent
}

export interface CriterionRow {
  id: string
  text: string
  from_pkg: string
  from_layer: string
  tests: { id: string; title: string; verdict?: string; package_ref?: string }[]
  /** 本层不测这条判据时写明的理由；空串 = 没写 */
  waived: string
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
  state: {
    word: StateWord; why: string; lifecycle: Lifecycle; verdict: string
    /** 核对查了什么、没查什么。状态词不替人下结论，范围要写出来 */
    scope: { checked: string[]; unchecked: string[] }
  }
  guide: LayerGuide
  interface: InterfaceCard
  checks: CheckRow[]
  checked_at: string
  gate_report: string
  receipts: Receipt[]
  evidence: EvidenceLine[]
  /** 判据来源：上游定下的判据原文，对照本页登记的同编号测试。按编号对应，编号相同不代表内容相同 */
  criteria: CriterionRow[]
  reproduce_command: string
  parents: string[]
  parents_derived: boolean
  // 开工简报、另读上游、执行记录
  context: { brief: BriefReceipt | null; pulls: PullRecord[] }
  runs: RunRecord[]
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
  // 这一层由哪个 agent 做
  agent?: string | null
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
  // 人写的那句话：批准时核对了什么、为什么驳回。旧记录没有留，就是空串
  note?: string
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
  // 本轮完成 = 模板里每个框都核对通过
  complete: boolean
  // 第一个没到「核对通过」的框；null = 全部核对通过
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

export interface TemplateNode { component: string; alias: string; repeat?: 'per_case' }

export interface TemplateInfo {
  id: string
  label: string
  description: string
  layers: { layer: string; name: string; alias: string }[]
  // 编排配置页用：原始节点、门禁覆盖、被哪些任务使用
  nodes?: TemplateNode[]
  gate_overrides?: Record<string, { required_fields?: string[] }>
  used_by?: { tasks: number; task_ids: string[] }
}

export interface TemplateComponent {
  id: string
  name: string
  input_types: string[]
  output_type: string
  human_weight: string
  requires_test_evidence: boolean
}

export interface TemplatesResponse {
  templates: TemplateInfo[]
  default: string
  components?: TemplateComponent[]
  sha?: string
  errors?: string[]
}

// ---- 配置管理：Agent / 技能 / 工具包 / 版本历史 ----

export interface AgentConfigEntry {
  target: string
  layer: string
  name: string
  host_name: string
  role: string
  role_label: string
  file: string
  skills: string[]
  tools: string[]
  writes: string[]
  must_not: string[]
  body: string
  sha: string
}
export interface AgentsConfigResponse { agents: AgentConfigEntry[]; errors: string[] }

export interface ShelfUser { target: string; layer: string; name: string }

export interface SkillShelfEntry {
  name: string
  dir: string
  what: string
  description: string
  body: string
  generated: boolean
  source: string
  used_by: ShelfUser[]
}
export interface SkillsResponse { skills: SkillShelfEntry[]; errors: string[] }

export interface ToolPackEntry {
  name: string
  what: string
  commands: string[]
  ref: string
  used_by: ShelfUser[]
}
export interface ToolsResponse { packs: ToolPackEntry[]; errors: string[]; sha: string }

export interface VersionEntry {
  at: string
  by: string
  kind: string
  target: string
  sha: string
  bytes: number
  lines: number
  add: number | null
  del: number | null
  prev_sha: string | null
  note: string
}
export interface HistoryResponse {
  kind: string
  target: string
  entries: VersionEntry[]
  current_sha: string
  current_lines: number
  current_bytes: number
  current: string
}
export interface VersionContent { entry: VersionEntry; text: string }

// ---- 平台内部运行时：派发层 agent 与作业 ----

export interface RuntimeConfig {
  configured: boolean
  base_url: string
  model: string
  api_key_env: string
  has_key: boolean
  max_turns: number
  command_timeout_s: number
  // v1=通用工具循环；langgraph=领域工具图（目前 L1）
  engine?: string
}
export interface JobEvent { at: string; event: string; [k: string]: unknown }
export interface JobInfo {
  job_id: string
  created_at: string
  updated_at: string
  package_id: string
  layer: string
  auto: boolean
  by: string
  agent: string
  model: string
  status: string
  turns: number
  gate_verdict: string
  summary: string
  error: string
  duration_s: number | null
  chain: string[]
  signoff_decision_id?: string
  // v2 图节点：prepare / agent / tools / finish
  node?: string
  engine?: string
  log_path?: string
  log?: JobEvent[]
  active?: boolean
  finished_at?: string
}
export interface RuntimeStatus { config: RuntimeConfig; active_job: JobInfo | null; jobs: JobInfo[] }
export interface JobsResponse { active: string | null; jobs: JobInfo[] }

/** 已归档任务（不在 /api/state 里，总览单独取） */
export interface ArchivedTask {
  task_id: string
  title: string
  status: string
  archived_at: string
  archived_reason: string
  iteration_count: number
  package_count: number
}
export interface ArchivedTasksResponse { tasks: ArchivedTask[] }

export interface GuideResponse {
  layers: (LayerGuide & { layer: LayerId; name: string })[]
  truth: Record<Truth, string>
  frames: Record<Frame, string>
  strength: Record<string, string>
  states: StateWord[]
  interface: { limits: Record<string, number>; gives_max: number }
  // 调度 agent：只派工与核对，不做层内工作
  orchestrator: LayerAgent
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

/** 工作台打开一个登记工件：文本内容 + 元数据；编辑总是生成新 ART，不改共享输入 */
export interface ArtifactContent {
  ok: boolean
  artifact_id: string
  package_id: string
  artifact_type: string
  title: string
  path: string
  content_hash: string
  registered_hash: string
  stale: boolean
  size: number
  binary: boolean
  truncated: boolean
  editable: boolean
  text: string | null
  source: string
}

export type ActionId =
  | 'ask' | 'propose' | 'decide' | 'revise' | 'what_if' | 'validate'
  | 'approve' | 'reject' | 'rollback'
  | 'bench_import' | 'task_new' | 'iter_new' | 'new_pkg'
  | 'iter_seal' | 'iter_abort' | 'task_seal'
  | 'brief_out' | 'run_log'
  | 'agent_save' | 'agent_restore'
  | 'toolpack_save' | 'toolpack_delete'
  | 'template_save' | 'template_duplicate'
  | 'reproject_hosts'
  | 'redo_layer'
  | 'task_archive' | 'task_delete'
  | 'dispatch' | 'stop_job' | 'runtime_config'
  | 'artifact_save' | 'layer_report'

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
  // ---- 配置管理：agent_save / toolpack_save / template_save 等 ----
  target?: string
  id?: string
  label?: string
  meta?: Record<string, unknown>
  body?: string
  base_sha?: string
  sha?: string
  what?: string
  commands?: string[]
  ref?: string
  description?: string
  nodes?: TemplateNode[]
  gate_overrides?: Record<string, { required_fields?: string[] }>
  from?: string
  // ---- 平台运行时 ----
  auto?: boolean
  job?: string
  base_url?: string
  api_key?: string
  api_key_env?: string
  max_turns?: number
  command_timeout_s?: number
  // 运行时引擎：v1 / langgraph
  engine?: string
  // artifact_save：工件编号、编辑后的全文、打开时的内容哈希
  artifact?: string
  content?: string
  base_hash?: string
  // task_delete：必须原样填任务 ID
  confirm?: string
  // task_archive：true=恢复
  restore?: boolean
  // brief_out：造包人署名；run_log：模型与耗时（秒）
  by?: string
  model?: string
  duration?: number
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
