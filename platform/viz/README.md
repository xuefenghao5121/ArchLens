# viz — 可视化 agent 工作流（只读投影 + 受控写操作面）

把 `wf.py` 已经算出的工作流状态渲染成人类可读的界面，并提供人机动作的受控写入口。
**服务端与引擎零第三方依赖**（stdlib `http.server` + 纯 HTML/JS；`wf.py` stdlib-only）。可选的 LangGraph
运行时 v2 需要单独装依赖（`requirements-runtime.txt` + `.venv-runtime`），只影响 L1 的 agent 执行，
不影响读模型、核对与 store。底层引擎（门禁/失效/编排）不重写，全部 import 复用 `wf.py`。

## 启动

```bash
# 连真实 store（默认）：写操作直接落在真实证据上，界面显示「真实 store」并常驻提示
python3 platform/viz/server.py --port 8899

# 只想试用界面：连一份隔离副本
WF_ROOT=/path/to/sandbox python3 platform/viz/server.py --port 8899

# 前端跑在别的机器上、要经它发写请求时，追加允许的 Origin（回环地址默认允许）
python3 platform/viz/server.py --port 8899 --allow-origin http://192.168.1.10:5173
```

浏览器打开 http://127.0.0.1:8899（单文件版，只读）。可操作的 React 版见 `frontend/README.md`
（`cd frontend && npm run dev`，Vite 把 `/api` 代理到本服务）。两个服务都是手动启动的进程，
机器重启后需要重新拉起。

## 请求防护

写端点落在证据 store 上，所以服务端强制：

| 防护 | 做法 |
|---|---|
| 跨站写请求 | `Content-Type` 必须是 `application/json`（跨站 fetch 需预检，本服务不应答 CORS）；`Origin` 须为回环地址或 `--allow-origin` 白名单；`Sec-Fetch-Site: cross-site` 一律拒绝 |
| DNS rebinding 读响应 | `Host` 只接受回环地址或 IP 字面量，拒绝任意域名 |
| 路径透传 | 层包只按 `package_id` 解析，不接受文件路径 |
| 导入 benchmark 的路径 | 必须是仓库目录内的普通文件或目录；解析软链之后再校验 |
| 进程稳定性 | `wf.py` 用 `SystemExit` 报错，HTTP 层统一转成 JSON 错误（接不住会让整个进程退出） |
| 并发写 | 单线程，请求串行 |

被拒绝的写请求与每次写动作都记一行到 stderr（时间、动作、操作人、结果）。

## 端点

```
GET  /                                     → API 跳转页（React 工作台在 5173）
GET  /api/state                           → workflow-state.json（schema 0.5）：每个层包带 state 状态词与 card 卡片，
                                           每个迭代带模板顺序的 nodes 与每框的 agent，每个任务带 receipts 任务级决定回执。
                                           ?domain= 仅为兼容旧调用，界面上不再有领域
GET  /api/recommend-flow?goal=research&iteration=ITER-…
                                           → 最小 flow 推荐；给 iteration 则整条 flow 限定在该迭代
GET  /api/evo-state                        → 演进状态（含谱系 lineage）
GET  /api/evidence                         → 证据 DAG（兼容保留；界面上的证据页读的是 /api/state 里的卡片）
GET  /api/benchmarks                       → benchmark 清单（全局一份）
GET  /api/artifact?pkg=&id=                → 本包引用工件的文本内容（只读；编辑走 artifact_save）
GET  /api/archived-tasks                   → 已归档任务摘要（状态导出不含归档任务）
GET  /api/templates                        → 流程模板：每个模板的原始节点、门禁覆盖、被哪些任务使用、
                                           可选组件清单与文件指纹（另有 layers 兼容字段）
GET  /api/agents                           → 九份 agent 定义（含正文与当前指纹）+ 整组校验错误
GET  /api/skills                           → 技能货架：用途、正文、是否 GENERATED、方法论源、被哪些 agent 使用
GET  /api/tools                            → 工具包货架：定义、使用关系、清单文件指纹
GET  /api/config/history?kind=&target=      → 配置版本历史；再给 &sha= 取该版本原文
GET  /api/runtime                            → 运行时：模型配置是否齐全、正在跑的作业、最近作业
GET  /api/jobs                               → 作业列表；GET /api/job/<job_id> 取状态与日志
GET  /api/guide                            → 每层的固定说明与这一层的 agent 定义（带正文）、调度 agent，
                                           以及标记（实测 / 推断 / 拍定、F0–F3）的人读含义
GET  /api/package/<package-id>             → 层包详情：card（人读的卡片）+ raw（原始记录）
POST /api/action                           → 写操作（application/json）
```

`/api/decisions` 与 `/api/domains` 已停用（404）：决定回执随卡片与任务导出，领域概念已取消。

## 三十三种动作（POST /api/action）

`body = { action, …, agent, idempotency_key?, target_version? }`

| 动作 | 作用 | 约束 |
|---|---|---|
| `inspect` | 层包详情 | 只读 |
| `ask` | 工作简报（`wf brief`） | 只读 |
| `propose` | 补记一个已经发生/已经产出的决定：写一条**待批**事项 | 必须带 `summary`（一句话结论，≤80 字）；审批人固定 `pending-user`；回执记下此刻接口卡的内容指纹。不是预先批准未知的工作 |
| `decide` | 记一个署名的决定：`{pkg, summary, as, rationale?, confidence?, type?}` | `as` 与 `summary` 必填，署名不能是 `pending-user`；驳回某个待批事项走 `reject` |
| `revise` | 作废并重做（`wf invalidate`） | `dry_run: true` 只预览影响范围；界面要求先预览再确认 |
| `what_if` | dry-run 门禁 | 不落库 |
| `validate` | 真实门禁 | 不会解除已失效/被取代的状态（恢复见下）；内容改过时会换掉旧待签、按当前内容生成新待签 |
| `approve` / `reject` | 批准/驳回待批决策 | 必须署名 `as`（不能是 `pending-user`）；`approve` 必须带批注 `note`（一句话：核对了什么），回执原样记下；同一条只能处理一次；待签指纹与当前接口卡不一致时 `approve` 拒绝，要求先重新核对 |
| `rollback` | 回滚当前 Champion | 恢复它取代的旧 Champion；非 Champion 不可回滚 |
| `bench_import` | 导入 benchmark：`{name, kind?, paths[], note?}` | 负载文件哈希入册；题集目录按文件清单入册；写进全局清单；路径须在仓库目录内 |
| `task_new` | 立项：`{title, goal, benchmarks[], template?, create?, as?, slug?}` | 标题、目标、benchmark 缺一不立；没有成功判据（怎样算完成只由流程模板决定）；写成任务级章程决策，`as` 署名则视为已批，否则待批 |
| `iter_new` | 开下一轮迭代：`{task, reason, template?, create?, as?}` | `reason` 必填；上一轮须已结束；「仅 benchmark」模板的任务没有下一轮 |
| `new_pkg` | 给流程里还没开始的框建层包：`{iteration, layer}` | 迭代须在进行中；同一层只能建一次 |
| `iter_seal` / `iter_abort` | 结束 / 终止一轮：`{iteration, reason, as}` | `iteration` 可以是 `ITER-…` 或 `TASK-…/<序号>`；署名与理由必填 |
| `task_seal` | 封板：`{task, reason, as}` | 章程须已批准；进行中的迭代一并结束；署名与理由必填 |
| `task_archive` | 归档 / 恢复任务：`{task, reason?, restore?}` | 归档后任务与层包从总览/流程/证据/待批里消失，状态冻结；`restore:true` 恢复 |
| `task_delete` | 彻底删除已归档任务：`{task, confirm, agent}` | `confirm` 必须原样填任务 ID；删任务记录、层包、附属决定/测试/运行/结果/图边；工件对象保留 |
| `brief_out` | 开工简报留档：`{pkg, by}` | 写成 `<包目录>/brief.md`，信封 `provenance.brief` 记时间、署名、指纹、所含上游 |
| `run_log` | 记一条执行：`{pkg, agent, model?, duration?, note?}` | 追加到 `provenance.runs`；只记事实 |
| `agent_save` | 保存一层 agent 定义：`{target, meta, body, agent, base_sha}` | 写前整组校验；`base_sha` 防止覆盖别人的改动；写后重投影、留快照 |
| `agent_restore` | 回滚 agent 定义：`{target, sha, agent}` | 先快照当前再写回，回滚本身可再回滚 |
| `toolpack_save` | 新建 / 编辑工具包：`{name, what, commands?, ref?, agent, base_sha}` | 名字不可改；`what` 必填；`commands` 与 `ref` 至少一个；写后重投影 |
| `toolpack_delete` | 删除工具包：`{name, agent, base_sha}` | 被任何 agent 引用时拒绝 |
| `template_save` | 新建 / 编辑流程模板：`{id, label, description, nodes, gate_overrides, create?, agent, base_sha}` | id 创建后不可改；组件、别名、`per_case`、门禁字段都校验 |
| `template_duplicate` | 复制模板：`{from, id, label?, agent, base_sha}` | 新 id 须未被占用 |
| `reproject_hosts` | 重跑 `.zcode/agents` 投影：`{agent}` | 手动改过文件但没走保存时用 |
| `redo_layer` | 重做已作废/被取代的层包：`{pkg, agent}` | 新建同层新包（同任务/迭代），旧包保留并记 `replaced_by`；payload/证据/决定从头开始 |
| `artifact_save` | 人工编辑工件：`{pkg, artifact, content, base_hash, by}` | 不覆盖共享输入；写到 `designs/<pkg>/edits/…` 生成新 ART，本包引用切到新版本；保存后重跑 gate，直接下游作废 |
| `dispatch` | 派发层包给平台运行时：`{pkg, auto?, as?}` | 读 agent.md、调模型、执行工具；跑完平台自己 gate；同一时刻只允许一个作业 |
| `stop_job` | 请求停止当前作业：`{job}` | 当前工具调用结束后停下 |
| `runtime_config` | 保存运行时配置：`{base_url, model, api_key?, api_key_env?, max_turns?}` | 写本机 `platform/runtime/config.json`（0600）；不回显密钥 |

- **幂等键**：`idempotency_key` 与请求内容绑定——同 key 换内容返回 409。界面自动按
  「动作 + 目标 + 当前 store 版本」生成，所以重复点击/网络重试不会落两条记录。
- **乐观并发**：带上 `target_version`（`store_env_hash`，覆盖索引、证据图、层包信封与任务记录），
  store 变过就返回 409 并附当前哈希，不会在调用方没见过的状态上写。界面在冲突后自动刷新状态并
  保留表单内容，核对后再提交一次即可。
- **操作人**：`agent` 写进证据图与生命周期历史；界面要求先填写署名。

### 已失效的包怎么恢复

`validate` 对 `invalidated`/`superseded` 的包返回 `fail` 且保留原状态。确需恢复：

```bash
wf gate <pkg> --override --as <署名> --reason <理由>
```

它会先落一条带理由的 `override` 决策并挂到层包，再以该署名重新门禁——谁、为什么解除失效
都有记录（v2.2 §5.3）。

## 层 agent（设计文档 §12）

每一层一个 agent 定义，在 `platform/agents/<层目录>/agent.md`：frontmatter 声明这层的 `skills`、`tools`
（只能引用 `platform/agents/toolpacks.json` 里的包）、`writes`（可写范围）、`must_not`（不许做的事），正文是
职责、输入、工作步骤、完成标准与纪律。另有一份调度 agent（`orchestrator/agent.md`）：只派工与核对，不做
层内工作。`platform/tools/bind_hosts.py` 把它们投影成 `.zcode/agents/<host_name>.md`：

- 层 agent 的宿主工具是 `Read, Write, Edit, Bash, Glob, Grep`；
- 调度 agent 只有 `Read, Bash, Glob, Grep`（没有写文件的能力）；
- 宿主对项目级定义不允许设权限模式。

定义加载时逐项校验（schema、skills 在磁盘上、tools 在清单里、role 与 `LAYER_AGENT` 一致、host_name 不重复），
不一致直接报错。平台内部运行时每次派发都读 `platform/agents` 原文件，保存即生效；`.zcode/agents/*.md` 只是
给外部宿主的兼容投影，外部宿主在会话启动时载入它，所以那份副本要新会话才载入。

上下文边界留三样记录，都写在信封的 `provenance` 里：

```bash
wf brief <包> --out --by <署名>            # 开工简报落盘 + 回执（指纹、上游）
wf show <上游包> --for <本包> --by <署名>   # 接口卡之外另读上游，留痕
wf run-log --pkg <包> --agent <谁> [--model 名] [--duration 秒]
```

层 agent 开工前 `export WF_ACTOR=layer:<层>`。在这个变量下 `wf approve / reject / gate --override /
invalidate` 一律被拒，`wf decide` 的 reviewer 被强制为 `pending-user`——层 agent 只能准备，不能替人签字。
这是防误操作的护栏，不是硬隔离（直接改文件绕得过去）。

## 配置管理（Agent 管理 / 技能与工具 / 编排配置）

界面侧栏另有三个入口：

- **Agent 管理**（`/agents`）：九份定义的表单化编辑。frontmatter 的 name / host_name / skills / tools /
  writes / must_not 用表单，正文用 textarea；保存前服务端按整组规则校验，失败不写盘；保存后更新
  `.zcode/agents/` 兼容投影。保存是两步：先弹确认框说明「写回 / 快照 / 兼容投影」，确认后才写。平台读取
  agent 定义时看的是 `platform/agents/<层>/agent.md` 原文件（按 mtime 重新加载），保存后下一次派发即生效，
  **不需要新开对话，也不经过 ZCode 界面**。右侧是版本历史：查看、与当前做行对比、回滚。
- **技能与工具**（`/shelf`）：skills 只读，可打开看用途、正文、方法论源与被哪些 agent 使用；工具包可新建、
  编辑、删除（仅未被引用时）。工具包改完会重投影宿主定义。
- **编排配置**（`/templates`）：流程模板的可视化编辑。模板数据在 `platform/agents/flow_templates.json`；
  节点顺序就是流程顺序，`repeat: per_case` 展开成 `case_1..N`，`gate_overrides` 按别名给额外必填字段。
  id 创建后不可改（任务记录引用它），可复制成新 id；任务创建时的模板下拉会立刻看到新模板。

**版本快照**：`platform/agents/.versions/index.jsonl` + `objects/<sha256>.<md|json>`（内容寻址）。每条记时间、
署名、指纹、行数与 `add / del`；保存前先快照旧内容。配置写用 `base_sha` 做乐观并发——配置文件不在
`store_env_hash` 的覆盖范围，`target_version` 对它们无效。

**技能只读**：`.agents/skills/*/SKILL.md` 是 `platform/methodology/*.md` 的投影；改内容请改方法论源后重跑
`python3 platform/tools/bind_hosts.py`，否则下一次投影会覆盖。

## 平台内部运行时（层 agent 在平台内执行）

`platform/tools/runtime.py`（v1）与 `runtime_v2.py`（LangGraph + 领域工具）是平台自己的层 agent 运行时，
不把用户送到外部对话。v1 是通用工具循环；v2 接 L1–L8，按层生成领域工具面。

v1：
- **定义**：每次派发读 `platform/agents/<层>/agent.md`，把正文、skills、工具包、writes / must_not、
  任务目标与 `wf brief` 简报拼成系统提示；
- **模型**：OpenAI 兼容接口 + function calling，工具是 `bash` / `read_file` / `write_file` / `list_dir` / `finish`；
- **执行**：bash 在仓库根目录，`WF_ROOT` 指向当前 store，`WF_ACTOR=layer:<层>`（不能替人签字）；
  文件读写限制在仓库内，命令有超时与输出截断；
- **核对**：agent 收工后平台自己跑 `wf gate`，通过写 `wf run-log`；agent 自报完成不算；
- **预算**：最多 `max_turns` 步模型调用，剩 5 步会提醒收敛；最后一步只给 `finish` 工具并强制收工总结，
  预算用尽也会继续跑 `wf gate` 并记录 verdict，不会没有结论地停住；
- **后台**：作业在 server 独立线程里跑，界面导航不影响；日志写入 `platform/runtime/jobs/<JOB>.jsonl`。
  层详情重新进入时会从 `/api/jobs` 接回这一包最近的作业；顶栏在作业运行中显示「作业运行中 · Lx」。
  重启 server 会中断作业，不会自动续跑；
- **待签**：`wf gate` 返回 `needs_decision`（只差人签字）时，平台按本层已产出的接口卡自动生成一条
  `ratify` 待签事项，挂在本层回执上；人直接在「人的决定」里批准/驳回。决定发生在结果之后，不要求人
  在开工前提请；
- **串联**：`auto=true` 且核对通过时派模板里下一个**可开工**的框；下一层无包、已作废/被取代、已通过或
  待人决定就停，不会在死包上继续跑；核对未过也停；同一时刻只允许一个作业；
- **记录**：`platform/runtime/jobs/<JOB>.json` + `.jsonl`（作业日志与工具输出）。

v2（`engine=langgraph`，L1–L8）：
- **图**：`StateGraph`：prepare → agent ⇄ tools → reflect → agent → finish；有 tool_calls 去 tools，否则收工；
  tools 执行后先经过 reflect（不带工具，只产 1–2 句 Thought，写 `thought` 事件）再回 agent；外层 v1 仍负责
  `wf gate`、签字、状态与日志，避免双重门禁。
- **ReAct 可见性**：作业面板把 `thought` 显示为“思考（ReAct）”、`tool_call` 为“行动”、`tool_result` 为“观察”；
  `prepare` 另写 `plan` 事件。Thought 不冒充已执行动作，避免模型把“打算调用”当成“已经调用”。
- **领域工具**：公共 `wf_brief / wf_previous / wf_experience / wf_register_artifact / wf_payload_patch /
  wf_interface_patch / wf_test_register / wf_gate / case_* / environment_read / finish`；L2/L5/L6/L7 另有
  `repo_read`、`repo_grep`、`repo_write`、受控 `run_command`；L4 另有 `wf_contract_patch`。L1 仍无 bash、
  无通用仓库读。
- **跨作业经验**：`prepare` 会把同包最近完成作业的摘要/失败命令/关键 Thought/遗留证据注入 system prompt
  （上限 9000 字），并写 `experience_loaded`；这是经验蒸馏，不恢复旧对话或旧 checkpoint。
- **边界**：L1 payload 白名单严格校验，baseline_refs 必须是已登记 ART 且路径在 missions/benchmarks/domains/
  designs；L2–L8 的 payload 由 `wf.LAYER_RULES` 在写入时给 warnings、由 gate 最终裁决。命令拒绝签名/作废/
  平台配置写入；写文件只允许各层声明的目录（L5/L6 的 designs、L6 的 src/gem5/src/rtl、L7 的实验输出等）。
- **测试**：`wf_test_register` 同时登记测试定义、运行证据工件、RUN 与 RES；truth=observed 的接口卡结论必须
  挂登记在册且 pass 的 RES。
- **收工**：没跑过 `wf_gate` 就调用 `finish` 时，平台自动补跑一次 gate 并把结果写进对话，避免模型反复 finish 空转；外层 v1 仍会再跑一次 gate。
- **检查点**：LangGraph `SqliteSaver` 在 `platform/runtime/checkpoints.sqlite`，`thread_id=job_id`；作业 JSON/JSONL
  契约不变，新增 `engine/node`。
- **启动**：`python3 -m venv --system-site-packages .venv-runtime`，
  `.venv-runtime/bin/pip install -r requirements-runtime.txt`，用 `.venv-runtime/bin/python` 启动 server；
  在 Agent 管理 → 运行时把 engine 选 `langgraph`。

配置在 `platform/runtime/config.json`（0600，不进库），或在启动服务时给 `RUNTIME_BASE_URL /
RUNTIME_MODEL / RUNTIME_API_KEY / RUNTIME_ENGINE`。界面：Agent 管理页顶部「运行时」；层详情页「平台内部运行」
有派发、停止、自动推进开关与实时日志；层详情的「补记决定」只在本层已有产出时可用，打开时用接口卡预填一句话。

```bash
python3 platform/tools/runtime.py check
python3 platform/tools/runtime.py run --pkg PKG-L1-… [--auto] --agent <署名>
python3 platform/tools/runtime.py jobs
```

边界：bash 不是硬沙箱（能碰仓库内文件）；`writes / must_not` 是给模型的边界；模型必须支持 function calling；
人工签字层会在核对后停下等签名。

## 层接口卡与强制规则（设计文档 §11）

新建任务下产生的层包（信封上带 `provenance.task_id`）必须写 `interface`：本层要回答的问题、用了上游哪
几条结论、交给下游的 1–5 条结论（每条带 真值 / 基架 / 证据）、没解决的问题、适用边界。`wf gate` 对这类
包强制五条规则，违反即不通过：

1. 接口卡成文、限长、引用可解析（`takes[].from` 必须是上游，证据必须在登记册）；
2. 标「实测」的结论必须指向登记在册且结论为通过的测试结果；
3. 上游没验证完，下游不能通过；
4. 签字只对签过的那份内容有效——内容在其后改过，要重签；
5. 核对通过之后接口卡再改，「核对通过」不再成立，需要重新核对。

旧包不受这五条约束；它们的卡片是从原有字段摘录的，每条注明摘自哪里。层 agent 写接口卡的格式见
`wf guide`；下游 agent 用 `wf brief <pkg>` 拿上游卡片。

```bash
wf card <包>            # 一个层包的人读卡片：状态词、这一层是什么、接口卡、核对回执、决定回执
wf guide [L1..L8]       # 每层的固定说明与接口卡的写法
wf brief <下游包>       # 给这一层 agent 的工作简报：目标原话、本层说明、上游卡片、接口卡要求
```

## 任务与迭代

任务只需要三样：一句目标（原话，之后每一层看到的都是这同一句）、benchmark、流程模板。没有任务类型，
也没有另写的成功判据——任务长什么样、怎样算完成只由流程模板决定（本轮每个框都核对通过）。迭代是任务下
的一轮；模板 `benchmark_only`（仅 benchmark）不走层，没有迭代。

```bash
wf bench-import --name N --kind K <文件或目录>…     # 负载文件哈希入册；题集目录按清单入册；写进全局清单
wf task-new    --title T --goal "…" --benchmark N [--template T --create] [--as 署名]
wf task-list / wf task-show <task>
wf iter-new    --task T --reason "…" [--template T --create] [--as 署名]   # 开下一轮，理由必填
wf iter-seal   <iteration> --as 署名 --reason "…"     # 结束一轮
wf iter-abort  <iteration> --as 署名 --reason "…"     # 终止一轮不再推进的迭代（数据保留，默认折叠）
wf task-seal   --task T --as 署名 --reason "…"        # 封板（SHIP），留署名决策
wf next [--iteration ITER-…]                           # 前沿 + 任务级提示（待批章程、Mission Review）
wf task-list [--all]                                   # 列出任务；默认不含已归档的
wf task-archive --task T [--reason "…"]                # 归档：不在导出 / next / 界面呈现，store 里的记录保留
wf task-archive --task T --restore                     # 恢复归档
```

**归档**是呈现开关，与任务状态正交。归档之后：任务与它的层包不进 `state-export`（`/api/state` 不再有它们），
`wf next` 不再提示，界面看不到；它的层包与决策仍在 store 里，`wf task-show` / `wf card` 照样按 ID 查得到，
它上面的待批事项也不进待批队列（要处理直接 `wf approve <DEC-id> --as <署名> --note <核对了什么>`）。归档任务不能再开迭代或建层包。

- 任务记录在 `tasks/<task_id>.json`；新任务的层包放在默认位置 `domains/general/`（首次用到时创建）；
  旧任务的层包留在原来的目录，任务记录里以 `namespace` 记着。
- 包归属迭代：迭代里显式列出的 `package_ids` → 包上的 `provenance.iteration_id` → 按创建时间落进当时
  进行中的迭代。`wf new-pkg --iteration ITER-… --layer Lx` 可以给流程里还没开始的框补建层包。
- 已封板 / 已终止的任务与迭代默认折叠，不进 `wf next`；它们上面的待批事项仍然列出。
- `wf decide` 的 `--summary`（一句话结论，≤80 字）必填，详细依据放 `--rationale`；`--pkg` 与 `--task`
  二选一。回执记下所审接口卡的内容指纹（`content_sha`）。

## 回归

```bash
python3 platform/viz/regress.py      # 退出码 0 = 全部通过
```

178 条用例，每条对应一个真实出过的问题或新立的契约：跨站写请求被拒、查不到的包不再打挂进程、路径透传
被拒、证据状态方向、自批准被拒、已失效包不被验证复活、署名覆盖留痕、乐观并发、幂等键、演进状态机；
迭代归属与沿用、模板节点与完成标志、卡片状态词与核对结论逐包一致（真实 30 个包）、旧包摘录不改原包；
接口卡五条强制规则、决定回执与过期重签、去领域的立项、全局 benchmark、`benchmark_only` 模板、补建层包、
结束 / 终止 / 封板、任务级回执；
层 agent 定义（schema / skills / 工具包 / role 一致 / host_name 不重复、坏定义被拒）、开工简报留档与指纹、
回读留痕、执行记录、层 agent 不能替人签字（四种动作被拒、decide 强制待批）、宿主投影九份、状态导出 0.5；立项 → 批准章程 → 结束首轮 → 开下一轮 → 封板的全流程与 Mission Review
提示。全程在临时沙箱里跑（复制 store / domains / tasks，以 `WF_ROOT` 启动服务），开跑前先确认服务端连的
是沙箱，结束后核对真实 store 指纹未变。`wf.py` 的 `selftest` 只覆盖门禁的故障注入，两者互补。

命令行路径另有一份冒烟，与回归互补（回归走 HTTP 面，它走 `wf` 命令行本身）：

```bash
bash platform/viz/cli_smoke.sh    # 末行 ALL CLI SMOKE CHECKS PASSED = 通过
```

覆盖 `wf guide / card / brief`、`bench-import`、`task-new` 的各项拒绝、接口卡各条强制规则、`decide` 回执的
内容指纹、核对后改接口卡与重签、章程批准与迭代收尾、仅 benchmark 模板、状态导出的一致性。同样只在
`/tmp` 下的隔离副本上跑。

## 演进中心（RSI §11）

```bash
wf evo-new    --component <组件> --change-type <类型> --change-set "…" [--scope …] [--eval-plan EVAL-…] [--by agent|human]
wf evo-eval   --candidate EVO-xxx --verdict promote|reject|keep [--champion '{…}'] [--challenger '{…}']
wf evo-promote --candidate EVO-xxx      # 仅评测结论为 promote 且带指标的 Challenger 可晋级
wf evo-rollback --candidate EVO-xxx [--reason …]   # 仅当前 Champion；恢复它取代的旧 Champion
wf evo-list / wf evo-state              # 谱系 / 导出状态
```

状态机：`draft/evaluating --evo-eval promote--> challenger --evo-promote--> champion
--evo-rollback--> rolled_back`（被取代者 `superseded`；`rejected` 为终态）。晋级与回滚的
取代关系以 `supersedes` 边写入证据图。候选记录受 `schemas/evolution_candidate.json` 约束并
在 registry 登记。

## 架构（复用，不重写）

```
wf.py（state-export / card / guide / brief / 任务与迭代命令 / 各写入命令）
      ↑ import 复用
server.py（HTTP 层：请求防护、参数校验、幂等、错误转换）
      ↓ JSON
frontend/（React 版：研究总览 / 流程编排 / 层详情 / 证据与回溯 / 演进中心）
.zcode/agents/*.md（宿主子代理定义，由 bind_hosts.py 从 platform/agents/*/agent.md 投影，生成物）
index.html（单文件只读版，未跟进任务 / 迭代与卡片的结构）
```

server.py 不重新实现任何聚合/编排/门禁逻辑，只做 HTTP 面该做的事：校验请求、调 wf.py、
把结果序列化成 JSON。

## 透明化：让人看懂证据到底证明了什么（2026-11-28）

起因：cvfpu 那一轮八层全绿，但 L5 交付的 RTL 是案例目录里早就存在的 `candidate.sv` 的逐字节拷贝，
登记为 pass 的等价检查拿的是这份文件和它自己的拷贝比。页面当时只显示「已验证」，
这个事实要翻作业日志、比对 md5 才知道。流程可编排是为了透明，所以做了五处改动：

1. **状态词不替人下结论**：核对通过的包，状态词由「已验证」改为「核对通过」；卡片 `state.scope` 写明
   核对了什么、没核对什么，页头与人读报告都显示。状态导出升到 0.6。
2. **交付来源**（`evidence[].origin`）：登记册按内容寻址，同一份内容第二次登记返回的是第一次的记录，
   所以「内容在本包建立之前就已登记」可以机械判断。只陈述事实（`own` 本层首次登记 / `input` 与输入相同 /
   `upstream` 与上游登记的相同 / `preexisting` 本包建立前已登记），不写「拷贝」——拷贝与重跑后逐字节相同
   登记册分不出来。只标注，不拦截。
3. **测试一句话**（`test_definition.comparison`：`under_test` / `reference` / `stimulus`，加上原有的 `control`）：
   `wf_test_register` 与 `wf test-register --file` 都可记录；页面与报告渲染成「拿 A 和 B 比，激励 C；对照组 …」。
   没记录就如实写「比较对象没有记录」。同样是先显示、不拦截。
4. **判据来源**（`card.criteria`）：上游的 `acceptance_criteria` / `preregistered_criteria` 原文，
   并列本页登记的同编号测试；按编号对应，不判断内容是否一致。放弃的判据写 `payload.waived_criteria`。
5. **签字留判断**：`approve` 与 `wf approve` 的 `--note` 必填，回执 `note` 原样带出；旧记录没留人的判断的，
   显示「没有写明核对了什么」。

**没有做**：输入集与判分材料的分离（需要人先决定哪些文件算任务输入，以及 shell 级的隔离方式）；
对拷贝、对没有比较对象的测试，gate 暂不拦截。
