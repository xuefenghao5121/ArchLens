# viz — 可视化 agent 工作流（只读投影 + 受控写操作面）

把 `wf.py` 已经算出的工作流状态渲染成人类可读的界面，并提供人机动作的受控写入口。
**零第三方依赖**（stdlib `http.server` + 纯 HTML/JS）。底层引擎（门禁/失效/编排）不重写，
全部 import 复用 `wf.py`。设计说明见仓库根目录 `VISUAL-WORKFLOW-DESIGN.md`（§11 是层接口卡与
流程编排这一版）。

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
GET  /                                     → index.html（单文件版，只读）
GET  /api/state                           → workflow-state.json（schema 0.4）：每个层包带 state 状态词与 card 卡片，
                                           每个迭代带模板顺序的 nodes，每个任务带 receipts 任务级决定回执。
                                           ?domain= 仅为兼容旧调用，界面上不再有领域
GET  /api/recommend-flow?goal=research&iteration=ITER-…
                                           → 最小 flow 推荐；给 iteration 则整条 flow 限定在该迭代
GET  /api/evo-state                        → 演进状态（含谱系 lineage）
GET  /api/evidence                         → 证据 DAG（兼容保留；界面上的证据页读的是 /api/state 里的卡片）
GET  /api/benchmarks                       → benchmark 清单（全局一份）
GET  /api/templates                        → 流程模板：每个模板有哪些框（层）
GET  /api/guide                            → 每层的固定说明与这一层的 agent 定义（带正文）、调度 agent，
                                           以及标记（实测 / 推断 / 拍定、F0–F3）的人读含义
GET  /api/package/<package-id>             → 层包详情：card（人读的卡片）+ raw（原始记录）
POST /api/action                           → 写操作（application/json）
```

`/api/decisions` 与 `/api/domains` 已停用（404）：决定回执随卡片与任务导出，领域概念已取消。

## 十九种动作（POST /api/action）

`body = { action, …, agent, idempotency_key?, target_version? }`

| 动作 | 作用 | 约束 |
|---|---|---|
| `inspect` | 层包详情 | 只读 |
| `ask` | 工作简报（`wf brief`） | 只读 |
| `propose` | 提请决定：写一条**待批**事项 | 必须带 `summary`（一句话结论，≤80 字）；审批人固定 `pending-user`，不能写 `ratify`；回执记下此刻接口卡的内容指纹 |
| `decide` | 记一个署名的决定：`{pkg, summary, as, rationale?, confidence?, type?}` | `as` 与 `summary` 必填，署名不能是 `pending-user`；驳回某个待批事项走 `reject` |
| `revise` | 作废并重做（`wf invalidate`） | `dry_run: true` 只预览影响范围；界面要求先预览再确认 |
| `what_if` | dry-run 门禁 | 不落库 |
| `validate` | 真实门禁 | 不会解除已失效/被取代的状态（恢复见下） |
| `approve` / `reject` | 批准/驳回待批决策 | 必须署名 `as`（不能是 `pending-user`）；同一条只能处理一次 |
| `rollback` | 回滚当前 Champion | 恢复它取代的旧 Champion；非 Champion 不可回滚 |
| `bench_import` | 导入 benchmark：`{name, kind?, paths[], note?}` | 负载文件哈希入册；题集目录按文件清单入册；写进全局清单；路径须在仓库目录内 |
| `task_new` | 立项：`{title, goal, benchmarks[], template?, create?, as?, slug?}` | 标题、目标、benchmark 缺一不立；没有成功判据（怎样算完成只由流程模板决定）；写成任务级章程决策，`as` 署名则视为已批，否则待批 |
| `iter_new` | 开下一轮迭代：`{task, reason, template?, create?, as?}` | `reason` 必填；上一轮须已结束；「仅 benchmark」模板的任务没有下一轮 |
| `new_pkg` | 给流程里还没开始的框建层包：`{iteration, layer}` | 迭代须在进行中；同一层只能建一次 |
| `iter_seal` / `iter_abort` | 结束 / 终止一轮：`{iteration, reason, as}` | `iteration` 可以是 `ITER-…` 或 `TASK-…/<序号>`；署名与理由必填 |
| `task_seal` | 封板：`{task, reason, as}` | 章程须已批准；进行中的迭代一并结束；署名与理由必填 |
| `brief_out` | 开工简报留档：`{pkg, by}` | 写成 `<包目录>/brief.md`，信封 `provenance.brief` 记时间、署名、指纹、所含上游 |
| `run_log` | 记一条执行：`{pkg, agent, model?, duration?, note?}` | 追加到 `provenance.runs`；只记事实 |

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
不一致直接报错。**宿主的子代理定义在会话启动时载入**，新加的定义要新会话才生效。

上下文边界留三样记录，都写在信封的 `provenance` 里：

```bash
wf brief <包> --out --by <署名>            # 开工简报落盘 + 回执（指纹、上游）
wf show <上游包> --for <本包> --by <署名>   # 接口卡之外另读上游，留痕
wf run-log --pkg <包> --agent <谁> [--model 名] [--duration 秒]
```

层 agent 开工前 `export WF_ACTOR=layer:<层>`。在这个变量下 `wf approve / reject / gate --override /
invalidate` 一律被拒，`wf decide` 的 reviewer 被强制为 `pending-user`——层 agent 只能准备，不能替人签字。
这是防误操作的护栏，不是硬隔离（直接改文件绕得过去）。

## 层接口卡与强制规则（设计文档 §11）

新建任务下产生的层包（信封上带 `provenance.task_id`）必须写 `interface`：本层要回答的问题、用了上游哪
几条结论、交给下游的 1–5 条结论（每条带 真值 / 基架 / 证据）、没解决的问题、适用边界。`wf gate` 对这类
包强制五条规则，违反即不通过：

1. 接口卡成文、限长、引用可解析（`takes[].from` 必须是上游，证据必须在登记册）；
2. 标「实测」的结论必须指向登记在册且结论为通过的测试结果；
3. 上游没验证完，下游不能通过；
4. 签字只对签过的那份内容有效——内容在其后改过，要重签；
5. 核对通过之后接口卡再改，「已验证」不再成立，需要重新核对。

旧包不受这五条约束；它们的卡片是从原有字段摘录的，每条注明摘自哪里。层 agent 写接口卡的格式见
`wf guide`；下游 agent 用 `wf brief <pkg>` 拿上游卡片。

```bash
wf card <包>            # 一个层包的人读卡片：状态词、这一层是什么、接口卡、核对回执、决定回执
wf guide [L1..L8]       # 每层的固定说明与接口卡的写法
wf brief <下游包>       # 给这一层 agent 的工作简报：目标原话、本层说明、上游卡片、接口卡要求
```

## 任务与迭代

任务只需要三样：一句目标（原话，之后每一层看到的都是这同一句）、benchmark、流程模板。没有任务类型，
也没有另写的成功判据——任务长什么样、怎样算完成只由流程模板决定（本轮每个框都已验证）。迭代是任务下
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
```

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
