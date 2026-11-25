# 待执行方案：按层编排 agent —— 每层一个 agent 定义、确定的调度规则、看得见的上下文边界

> track: platform（仿真平台的编排）
> status: draft —— 2026-11-25 提交给用户，**未获批准执行**；下列步骤一步都没有实施
> reviewed: 未审核
> author: zcode-glm（ZCode 会话 sess_29ed8703-420a-4eec-969a-e6d038b88e6c）
> since: 2026-11-25
> 事实源: 本文是这份方案的唯一事实源。同目录的 `orchestration-handoff-20261125.md` 是它的投影；两者不一致，以本文为准。
> 格式: 条款骨架照 NDF（`<NDF-Harness>/norms/CLAUSE-FORMAT.md`、`norms/meta/language.md`）。本仓没有安装 `ndf_index` / `ndf_graphcheck`，条款 ID 与依赖边**没有**被工具校验过。
> 怎么改: 直接改本文，在条款 ID 上讨论（例如「去掉 ORCH-S-003」「ORCH-D-002 的第 3 条改成……」）。改过之后，交接文档要重新投影。

## 0. 一页读懂

这次要做的事，是把「每一层由谁来做、用什么做、层与层之间传什么」在平台里写成看得见、可核对的东西：每层一个 agent 定义，调度规则放在确定的地方，上下文的边界留下记录。

**现在的状态**：方案已写好，未获批准，一步未实施。平台代码、层包、决策都没有为它改动过。

**获批后的可写范围**：`platform/agents/`、`platform/methodology/`、`platform/schemas/`、`platform/tools/wf.py`、`platform/tools/bind_hosts.py`、`platform/viz/`、`frontend/src/`、`.zcode/agents/`（生成物）、`.agents/skills/` 与 `host-bindings/`（生成物）、各份文档与 `SPEC.md`。

**禁写**：任何已有的层包、决策、证据图；`<NDF-Harness>`（只读的参考）。

## 1. 意图与依据（L0）

### 用户要什么 {#ORCH-I-001}
<!-- ndf: kind=req level=must layer=L0 status=draft since=2026-11-25 source=observed -->

用户 2026-11-25 的原话，逐字：

> 平台已经清晰很多了，接下来需要呈现出详细的可编排性。两个方案，1.编排agent，每一层的agent定义，每一层Agent应该可以被使用者观测到，这个Agent定义应该是一个独属于这层工作职责的agent.md，以及他所需要调用的各种skills和tools包。2.编排tools，通过skills连接每一层，由一个agent统一管理。帮我分析这两个方案，哪个清晰明了，效率更高。这里的区别应该是，方案1的各层之间记忆和上下文管理，方案2大家共用上下文。

同一天，用户看完分析与方案后说：

> 把目前的待执行方案写到本地，并且按ndf哲学输出一份handoff交接文档

本方案 MUST 满足：每一层的 agent 定义能被使用者看到；定义是一份独属于这一层工作职责的 `agent.md`，连同它要调用的 skills 和 tools 包。

### 结论：走方案 1（待用户确认）{#ORCH-I-002}
<!-- ndf: kind=option level=tbd layer=L0 status=draft since=2026-11-25 source=deduced -->
<!-- ndf: depends-on=ORCH-I-001 -->

这是我的推荐，不是用户的决定。`level=tbd` 的意思是：用户说一句「走方案 1」，它才成为决定。

四条依据。前三条是文件里写明的事实，第四条是按文件大小做的推算：

1. 设计文档早就定了方案 1 的上下文模型。`通用微架构混合仿真Agent设计文档_v2.md` §2.6 是「不共享隐式上下文，但共享显式公共契约」；§3.1 说控制面只管流程一致性、不参与微架构判断；§3.3 说独立验证面是为了降低「生成者与验证者共享同一误解」的风险。方案 2（一个 agent 从头做到尾）是这三条的反面。
2. NDF-Harness 自己就是方案 1 的形态。它把 Command / Control / Implementation 三个角色分开绑在 `workflow/ndf.workflow.yaml` 里，每个角色有自己的可写范围；派工靠一个 pack，每一跳默认先重置会话（`skill/ndf-workflow/delegate.md`）；成功只认磁盘上的完成回执，不认 agent 说「做完了」。
3. 事故复盘是同一类问题。`platform/lessons/2026-11-13-l3-comparative-fidelity.md`：判据错误从 L2b 一路带到 L7 才被用户发现。同一个上下文里，下游天然信任上游，因为那都是「我刚写的」。
4. 规模上共用上下文装不下。sme2 迭代 1 的 9 个层包合计约 284 KB，三轮 19 个包约 554 KB（按文件大小实测）；粗估一轮就是六万到十万 token，还没算日志、代码和工具输出。接口卡每层只有几百字。

### 两个方案逐项比较 {#ORCH-I-003}
<!-- ndf: kind=info level=may layer=L0 status=draft since=2026-11-25 source=deduced -->
<!-- ndf: depends-on=ORCH-I-002 -->

**清晰度。** 方案 1 每层一个 `agent.md`，谁做什么、用哪些 skills 和 tools 一眼可见，和界面上的框一一对应，工具范围可以按层收窄。方案 2 的行为由一堆 skill 的触发条件决定，编排逻辑在它的上下文里，人看不到，事后也查不到它当时为什么走了某一步。

**效率。** 方案 2 起步快，不用派工；但每一步都带着全部历史，越往后越慢越贵，上下文一满就得压缩，压丢的信息会造成返工。方案 1 每层多一次「读开工简报」的开销，但每层上下文小而稳定，可以按层换模型，批量标定这类模板还能并行。

**方案 1 的代价，要正视：**

- 接口卡是有损的，写得差下游就拿得少。对策是允许按需回读上游，并留下记录。
- store 没有并发锁（`platform/README.md` 写明），并行 agent 同时写要串行化。对策是默认按顺序派工。
- 要多维护 8 份定义，定义和实际会漂移。对策是定义做成单一来源，由机器校验。

**「哪个效率更高」没有实测数据。** 上面的效率判断是从上下文规模推出来的。所以方案里加了一项：每层执行后记录耗时和模型，界面上一层一行，之后用数据比。

### 现状缺口 {#ORCH-F-001}
<!-- ndf: kind=info level=must layer=L1 status=draft since=2026-11-25 source=observed -->

平台现在其实是方案 2 的做法（2026-11-25 核对）：

- 层与角色的对应、每层的 skills 写死在 `platform/tools/wf.py`（`LAYER_AGENT` 第 2029 行，`LAYER_GUIDE` 第 1153 行）。角色包 `platform/agents/{profiling,design,integration}.md` 只有 3 份，没有声明任何 tools。
- 30 个层包里 26 个的 `owner_agent` 是同一个 agent（`zcode-glm`），1 个是 `cursor-grok域-design-agent`，3 个为空。
- 没有派工机制，`.zcode/agents/` 不存在。`wf next` 只打印「该哪个角色做」，然后由同一个长会话接着做。
- `rtl-generation-discipline` 这个 skill 没挂到 L5 上（`LAYER_GUIDE` 的 L5 只列了 `flow-layer-discipline`）。
- `wf brief` 只打印不落盘，事后查不到某一层开工时拿到了什么。

## 2. 决定与假设

### 决定：方案 1 的骨架，加三点修正 {#ORCH-D-001}
<!-- ndf: kind=decision level=tbd layer=L0 status=draft since=2026-11-25 source=deduced -->
<!-- ndf: depends-on=ORCH-I-002 -->

待用户确认。三点修正是：

1. 调度规则放在确定的地方：顺序由模板定，何时算完由核对规则定，怎么派工写成一份调度规程。不靠某个 agent 自己记。
2. 共享的是 store 里的显式契约，不是会话。
3. 保留一个很薄的调度 agent：只拿着任务级状态，按规程派工、收工、核对，把要人决定的事端出来，自己不做任何一层的工作。

### 按推荐项做的假设（待用户确认）{#ORCH-D-002}
<!-- ndf: kind=option level=tbd layer=L1 status=draft since=2026-11-25 source=deduced -->
<!-- ndf: depends-on=ORCH-D-001 -->

下面五项用户没有回答。它们是我的假设，不是用户的偏好；不同意直接改。

1. 每层一个 agent 定义（8 份）加一份调度 agent 定义；三个角色包保留，作为共享能力说明被层定义引用。
2. 派工两种方式都支持：人工（界面出开工简报，另开会话）与自动（调度 agent 用宿主子代理）。自动派工以宿主支持为前提，先核实。
3. 默认按顺序派工，不做并行。
4. 工具边界先做成「声明 + 尽力拦截」，不是硬隔离。
5. 这一版不接「按层选模型」，等宿主子代理的字段格式核实后再加。

## 3. 实施步骤（L2）

每一步都依赖前一步。`level=must` 的条目是这一步的完成条件。

### 第 0 步：先核实宿主的子代理定义格式（检查点）{#ORCH-S-000}
<!-- ndf: kind=req level=must layer=L2 status=draft since=2026-11-25 source=deduced -->
<!-- ndf: depends-on=ORCH-D-001 -->

放一个最小的 `.zcode/agents/` 定义，用 Agent 工具发起一次，确认两件事：它能被发现；frontmatter 里限制 tools 的写法是否生效。

- 若当前会话里发现不了新定义：MUST 照常生成定义文件，把派工冒烟留到用户开新会话后的第一步，并在汇报里写明。
- 若宿主不支持限制 tools：工具边界 MUST 只靠声明与简报约束，并如实写进文档。

### 第 1 步：每层一个 agent 定义（单一来源）{#ORCH-S-001}
<!-- ndf: kind=req level=must layer=L2 status=draft since=2026-11-25 source=deduced -->
<!-- ndf: depends-on=ORCH-S-000 -->

新增 `platform/agents/L1_observation/agent.md` … `L8_analysis/agent.md`，以及 `platform/agents/orchestrator/agent.md`。每份文件里，YAML frontmatter 是机器读的部分，正文是人读的部分。

- frontmatter：`layer`、`name`、`host_name`、`role`（指向角色包）、`skills[]`、`tools[]`（工具包名）、`writes[]`（可写范围）、`must_not[]`。
- 正文：职责、输入、工作步骤、完成标准、纪律要点。内容 MUST 从现有的 `LAYER_GUIDE`、`LAYER_RULES`、角色包、`domains/sme2/environment.md` 归纳，不新编。

新增 `platform/agents/toolpacks.json`（工具包清单：名字、用途、包含的命令）。初始集合只收 `environment.md`、lessons、skills 里已经出现的东西：

- `wf:core`：register-artifact、register-git、test-register、gate、card、show
- `wf:read`：brief、next、status、walk
- `caselaw`：`platform/tools/caselaw.py search`
- 仿真与构建：gem5 运行、gem5 构建、内核工具链、pyCircuit/Verilator（具体命令指向领域的 `environment.md`）

新增 `platform/schemas/layer_agent.json` 并登记 registry。

### 第 2 步：引擎读取定义 {#ORCH-S-002}
<!-- ndf: kind=req level=must layer=L2 status=draft since=2026-11-25 source=deduced -->
<!-- ndf: depends-on=ORCH-S-001 -->

改动在 `platform/tools/wf.py`：

- 新增加载函数：读八份定义与工具包清单，按 schema 校验；启动时核对「skills 在磁盘上存在、tools 在清单里存在、`role` 与 `LAYER_AGENT` 一致」，不一致直接报错。
- `LAYER_GUIDE[L]["skills"]` 改为从定义读；L5 补上 `rtl-generation-discipline` 与 `caselaw`。
- `layer_card` 的 `guide` 增加 `agent` 摘要；卡片增加 `context`（开工简报、回读记录）与 `runs`（执行记录）。
- `wf guide`：每层打印 agent 名、skills、tools、可写范围、不许做的事。
- `wf brief`：首行写明本层 agent 与定义文件路径；新增 `--out [--by 名]`，把简报写成 `<包目录>/brief.md`，并在信封 `provenance.brief` 记时间、署名、sha256、所含上游包。
- `wf show <上游包> --for <本包> [--by 名]`：查看上游详情的同时，在本包 `provenance.context_pulls` 追加一条。这是「接口卡不够用」的正规通道，回读留痕。
- 新增 `wf run-log --pkg … --agent … [--model] [--duration 秒] [--note]`，追加到 `provenance.runs`。
- `wf next`：前沿包旁边打印该层的 agent 与开工命令。
- schema：`layer_package.provenance` 增加上述三项；`workflow_state` 升 0.5（卡片与节点带 agent 摘要、卡片带 context 与 runs）；registry 同步。

### 第 3 步：尽力拦截，层 agent 不能替人签字 {#ORCH-S-003}
<!-- ndf: kind=req level=must layer=L2 status=draft since=2026-11-25 source=deduced -->
<!-- ndf: depends-on=ORCH-S-002 -->

简报首行要求层 agent 开工前 `export WF_ACTOR=layer:Lx`。`wf` 在这个变量以 `layer:` 开头时拒绝 `approve`、`reject`、`gate --override`、`invalidate`，并强制 `decide` 的 reviewer 为 `pending-user`。

这是防误操作的护栏，不是硬隔离：直接改文件或走 HTTP 绕得过去。文档里 MUST 照实写。

### 第 4 步：调度规程与投影 {#ORCH-S-004}
<!-- ndf: kind=req level=must layer=L2 status=draft since=2026-11-25 source=deduced -->
<!-- ndf: depends-on=ORCH-S-002 -->

- 新增 `platform/methodology/orchestration.md`，经 `platform/tools/bind_hosts.py` 投影成 skill `flow-orchestration` 与手册镜像。规程照 NDF 的 Command 角色：定位前沿框 → `wf brief --out` 造包 → 派发本层 agent，只给简报 → 子代理返回不算完成，跑 `wf gate` 并 `wf run-log` → 把卡片端给人，待人决定就停 → 禁止自己做层内工作、替人签字、上游没验证就派下游。
- `bind_hosts.py` 增加一条投影：`platform/agents/*/agent.md` → `.zcode/agents/<host_name>.md`（带 GENERATED 标记；frontmatter 按第 0 步核实的格式写）。`--check` 覆盖新产物。

### 第 5 步：服务端与界面 {#ORCH-S-005}
<!-- ndf: kind=req level=must layer=L2 status=draft since=2026-11-25 source=deduced -->
<!-- ndf: depends-on=ORCH-S-002 -->

`platform/viz/server.py`：`/api/guide` 每层带 `agent`（含定义正文）；新动作 `brief_out`（pkg、by）与 `run_log`。

前端：

- **流程编排页**：目标块下方加「每一层由谁做、用什么」，按模板顺序一行一层，四列是层、agent、skills、tools，另标完成标准（要不要人签字、要不要测试）。只列这个模板涉及的层。每个框的标题下加 agent 名。
- **层详情**：左栏「这一层是什么」扩成「这一层的 agent」，列出 agent 名、所属角色、skills（名字加一句用途）、tools（名字加一句用途）、可写范围、不许做的事，可展开完整定义。页头元信息加三项：开工简报时间、另读上游几处、上次执行（agent、模型、时长）。「给 agent 的简报」改名「开工简报」，加「留档」按钮并显示启动方式。
- **证据与回溯页**：「用了上游的」一行后面列出另读的内容。

### 第 6 步：回归与冒烟用例 {#ORCH-S-006}
<!-- ndf: kind=verif level=must layer=L3 status=draft since=2026-11-25 source=deduced -->
<!-- ndf: depends-on=ORCH-S-005 -->

`platform/viz/regress.py` 新增：八份定义通过 schema；skills 与工具包都存在；L5 含 rtl skill；guide / brief / next 输出带 agent；`brief --out` 落盘且指纹一致；`show --for` 留痕并在卡片上出现；`run-log` 留痕；`WF_ACTOR=layer:*` 下签字类命令被拒；`bind_hosts --check` 通过。现有 158 条随契约调整后 MUST 保持全过。

`platform/viz/cli_smoke.sh` 补对应命令行路径。

### 第 7 步：类型检查、浏览器实测、派工冒烟 {#ORCH-S-007}
<!-- ndf: kind=verif level=must layer=L3 status=draft since=2026-11-25 source=deduced -->
<!-- ndf: depends-on=ORCH-S-006 -->

- 类型检查零错误。
- 隔离副本上浏览器实测三个页面的新增部分与「留档」。
- 派工冒烟：在隔离副本的任务上，按规程派一个层子代理走完一层，核对结果、执行记录是否落盘，并从子代理里试一次越界动作，把实际结果写进汇报。

### 第 8 步：文档与收尾 {#ORCH-S-008}
<!-- ndf: kind=req level=must layer=L2 status=draft since=2026-11-25 source=deduced -->
<!-- ndf: depends-on=ORCH-S-007 -->

- `VISUAL-WORKFLOW-DESIGN.md` 新增 §12（含两个方案的比较与依据）；`platform/viz/README.md`、`frontend/README.md`；三个角色包各加一行指向层定义；`SPEC.md` 新条目；备份目录写 `NEW_FILES.txt`。
- 重启 8899 与 5173，只读复核正式界面。

## 4. 复用的现成件 {#ORCH-R-001}
<!-- ndf: kind=info level=should layer=L2 status=draft since=2026-11-25 source=observed -->

- `platform/tools/wf.py`：`LAYER_GUIDE`（第 1153 行）、`LAYER_AGENT`（第 2029 行）、`cmd_brief`（第 2106 行）、`cmd_next`（第 2048 行）、`layer_card`、`interface_errors` 旁的校验写法、`_store_env_hash`。
- `platform/tools/bind_hosts.py`：`split_frontmatter`、`build_frontmatter`、`ZCODE_NAME`、`--check`。
- 界面：`frontend/src/features/layers/LayerDetail.tsx` 的 `GuideBlock`、`features/flow/Flow.tsx` 的 `Chain` / `NodeBox`、`features/evidence/Evidence.tsx` 的 `Trace`、`features/shared/useAction.ts`。
- 与 NDF 的做法对照：pack 对应开工简报，`writable` 对应 `writes`，磁盘回执对应 `wf gate` 写入的核对结果。

## 5. 不做的 {#ORCH-N-001}
<!-- ndf: kind=constraint level=must layer=L1 status=draft since=2026-11-25 source=deduced -->
<!-- ndf: depends-on=ORCH-D-001 -->

实施这份方案时 MUST NOT 做：

- 并行派工。
- 在界面上编辑 agent 定义。
- 按层指定模型。
- 改动任何旧层包。旧包没有开工简报与回读记录，界面如实显示「没有记录」。
- 另写一套调度状态机：顺序与完成仍由现有模板和核对规则决定。

## 6. 验证（L3）{#ORCH-V-001}
<!-- ndf: kind=verif level=must layer=L3 status=draft since=2026-11-25 source=deduced -->
<!-- ndf: depends-on=ORCH-S-008 -->

方案做完的判据。agent 说「做完了」不算，下面每一条都要有落盘的结果：

1. `python3 platform/viz/regress.py` 退出码 0，末行「真实 store 指数未变」。
2. `bash platform/viz/cli_smoke.sh` 末行 `ALL CLI SMOKE CHECKS PASSED`；`wf selftest` 15/15。
3. `python3 platform/tools/bind_hosts.py --check` 退出码 0。
4. 类型检查零错误（用一份不带 reference 的临时配置）。
5. 沙箱后端 8898、沙箱前端 5174 上走浏览器实测：流程页的「每一层由谁做、用什么」、层详情的 agent 栏与完整定义、开工简报留档后页头出现时间、证据页的另读。
6. 派工冒烟按第 7 步执行，结果如实汇报。
7. 改动前后核对真实 store：层包、决策、证据图按修改时间确认未被写过。
8. 重启 8899 与 5173，只读复核正式界面。

## 附录 A：涉及文件

**新增**

- `platform/agents/L1_observation/agent.md` … `L8_analysis/agent.md`、`platform/agents/orchestrator/agent.md`
- `platform/agents/toolpacks.json`
- `platform/schemas/layer_agent.json`
- `platform/methodology/orchestration.md`
- 生成物：`.zcode/agents/<host_name>.md`（九份）、`.agents/skills/flow-orchestration/SKILL.md`、`host-bindings/manual/orchestration.md`

**修改**

- `platform/tools/wf.py`、`platform/tools/bind_hosts.py`
- `platform/schemas/layer_package.json`、`platform/schemas/workflow_state.json`、`platform/schemas/registry.json`
- `platform/viz/server.py`、`platform/viz/regress.py`、`platform/viz/cli_smoke.sh`
- `frontend/src/types/index.ts`、`frontend/src/App.tsx`、`frontend/src/api/client.ts`、`frontend/src/features/tasks/useWork.ts`、`frontend/src/features/flow/Flow.tsx`、`frontend/src/features/layers/LayerDetail.tsx`、`frontend/src/features/evidence/Evidence.tsx`、`frontend/src/features/shared/useAction.ts`、`frontend/src/features/shared/ui.tsx`、`frontend/src/status.ts`
- `platform/agents/profiling.md`、`design.md`、`integration.md`（各加一行指向层定义）
- `VISUAL-WORKFLOW-DESIGN.md`、`platform/viz/README.md`、`frontend/README.md`、`SPEC.md`

## 附录 B：本文写作时的基线（2026-11-25 10:58 核对）

写方案时平台的状态。动手之前先核对，变了就先弄清楚为什么：

- 回归 158 条全过；引擎自检 15/15；绑定检查无漂移；类型检查零错误。
- `platform/tools/wf.py` 共 4369 行，sha256 前 16 位 `147dac6f1b391a55`。
- 真实 store：30 个层包、3 个任务、3 条待人批准；层包与决策的最后修改时间停在 2026-11-19 11:57:17。

更多的指纹与复核命令在交接文档的附录里。
