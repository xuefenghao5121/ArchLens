# 交接文档：仿真平台的按层编排

> 写于：2026-11-25 上午。文中的现状核对于当天 10:58。
> 当天下午更新过一次：记入用户对方案目的的确认（HO-I-003，回执在第 8 节），补记仓库已经公开（HO-S-004），补上方案没有覆盖的两点（HO-Q-003）。
> 2026-11-25 14:26：用户说「开始执行」，方案获批（回执见第 8 节）。当天下午实施完成，本文已按实施结果更新。
> 唯一没做完的是自动派工的冒烟：宿主的子代理定义在会话启动时载入，要下一次新会话才能派。
> 作者：zcode-glm（ZCode 会话 sess_29ed8703-420a-4eec-969a-e6d038b88e6c）
> 交接给：下一个接手的 agent 会话，或者人
> 事实源：待执行方案是同目录的 `orchestration-plan-20261125.md`；平台事实在 store 与 `SPEC.md`。本文只把它们编排成一篇，自己不立新事实。本文与事实源不一致时，以事实源为准，并请修本文。
> 格式：照 NDF（`<NDF-Harness>`，版本 1.4.1）给人审的「分层散文」写法。正文是从头读到尾的文章，机器核对用的数字都在文末附录。每个条款标题带一个稳定的 ID，标题下一行写明出处。本仓没有安装 NDF 的索引与图检查工具，这些 ID 只用一段临时脚本核对过两件事：格式合规、互不重复。

## 0. 任务前言

你接手的是一件事的中间状态。用户要平台呈现出详细的编排能力，并让我在两种做法里分析哪种更好。分析做完了，方案写好了，但用户**没有批准执行**，而是让我把方案写到本地，再写这份交接。

所以，批准之前，平台上什么都没有为这份方案改动过。

用户在 2026-11-25 14:26 批准了执行，当天下午实施完成。方案的意图用户已确认过（HO-I-003），实施的完整记录在
`VISUAL-WORKFLOW-DESIGN.md` §12。下文写的是实施结果与仍然开着的事情。

**读这份交接的时候可以动什么**：只读。要改方案，改方案文件，然后重新写这份交接。

**方案获批后的可写范围**：`platform/agents/`、`platform/methodology/`、`platform/schemas/`、`platform/tools/wf.py`、`platform/tools/bind_hosts.py`、`platform/viz/`、`frontend/src/`、`.zcode/agents/`（生成物）、`.agents/skills/` 与 `host-bindings/`（生成物）、各份文档与 `SPEC.md`。

**禁写**：已有的层包、决策、证据图；`<NDF-Harness>`。

## 1. 意图

### 用户要什么 {#HO-I-001}
> 源：用户 2026-11-25 的两条消息，原话逐字收在方案 ORCH-I-001

用户的原话里有两句是这份交接的出发点：

> 接下来需要呈现出详细的可编排性。

> 把目前的待执行方案写到本地，并且按ndf哲学输出一份handoff交接文档

用户给了两个方案让我比较。方案 1 是每层一个 agent，每层有一份独属于这层职责的 `agent.md` 和它要调用的 skills、tools 包，并且使用者能看到。方案 2 是用 skills 把各层连起来，由一个 agent 统一管理。用户自己点出了两者的区别：方案 1 的难点在层与层之间的记忆和上下文管理，方案 2 是大家共用上下文。

### 我的判断，以及它有多可靠 {#HO-I-002}
> 源：方案 ORCH-I-002、ORCH-I-003

我推荐方案 1。用户在 2026-11-25 先确认了我对方案目的的理解（HO-I-003），随后在 14:26 批准执行。这个判断里的话分量不一样，分三类：

**文件里写明的事实。**

- 设计文档 v2 的 §2.6 定的是「不共享隐式上下文，但共享显式公共契约」。§3.1 说控制面不参与微架构判断。§3.3 说独立验证面是为了降低「生成者与验证者共享同一误解」的风险。
- NDF-Harness 自己就是分角色的做法。三个角色分开绑定，各有可写范围；每一跳默认先重置会话；成功只认磁盘上的回执。
- 11-13 的复盘记录了一次判据错误从 L2b 传到 L7 才被用户发现。
- 30 个层包里 26 个出自同一个 agent。

**推断。**

- 「共用上下文装不下」是从文件大小推出来的。sme2 迭代 1 的 9 个层包合计约 284 KB，我粗估它是六万到十万 token。这个 token 数没有测过。
- 「方案 1 更高效」是从上下文规模和上面的事故推出来的，没有两种做法的对比数据。

**没有确立的。**

- 宿主（ZCode）子代理定义的格式。它支持哪些字段、对 tools 的限制是否生效，我都没有核实。这是方案的第 0 步。

### 方案的目的（经用户确认）{#HO-I-003}
> 源：方案 ORCH-I-004；用户 2026-11-25 的回执「你的理解是对的」

这份方案的目的不是让流程更自动，而是把「谁做、用什么做、带着什么上下文做」从 agent 的脑子里拿出来，变成和层边界重合的、人能读、机器能查的定义。一句话：让上下文的边界和契约的边界重合。

它接在平台自己的主旨上。设计文档 v2 的 §13 说，平台的价值不在于让 agent 多做步骤，而在于每次转换都留下可检查的工件、可证伪的实验、可追责的决策；成立靠显式契约、独立验证、结论边界三件事。前两轮把层间传的信息做成了显式契约，但「谁在做」还是隐式的：契约到层为止，上下文贯穿全程。这份方案补的就是这一段。

由此得出方案里的几个关键选择：

- 每层一个定义。层已经是契约、核对、人签、界面上的框这四样东西的单位，把 agent 定义也挂在层上，几个边界就合成一个。
- 调度 agent 很薄，规则放在确定的地方。对应「控制面不做微架构判断」，不让编排逻辑留在某个 agent 的脑子里。
- 开工简报落盘、回读留痕。这是「方案 1 的上下文管理」的实体：共享的记忆是 store，不是会话。

对「可编排性」的理解：编排是明确的、看得见的、可核对的；其次是可调整，改定义文件和模板就能调。不包括在界面上直接给某一层换 agent 或换工具。

**这一句确认的范围要说准。** 用户确认的是目的与理解。它不是对执行的批准，也不是对五项假设的确认。

## 2. 此刻什么是真的

### 平台现状，每一条都能复核 {#HO-S-001}
> 源：2026-11-25 10:58 在仓库根目录 `<repo>` 核对；复核命令见本节与附录 A

回归 158 条全过，失败 0 条。跑一次十几秒，它在 `/tmp` 下的副本上跑，不碰真实 store：

```bash
python3 platform/viz/regress.py
```

引擎自检 15 项全符合预期：

```bash
python3 platform/tools/wf.py selftest
```

方法论源与它的两份镜像一致，没有漂移：

```bash
python3 platform/tools/bind_hosts.py --check
```

类型检查零错误。仓库自带的 `tsconfig.json` 带一条 reference，直接查会报 TS6310，要用一份临时的配置，内容在附录 C。

两个服务在跑，都只监听本机回环地址。8899 是后端，连的是真实 store；5173 是前端。

```bash
ss -ltnp | grep -E ':(8899|5173)'
curl -s http://127.0.0.1:8899/api/state
```

后端报告：状态导出是 0.4 版，30 个层包，3 个任务，3 条待人批准。30 个包的状态是 19 个已验证、7 个待人决定、3 个未开始、1 个进行中。

真实 store 没有为了这份方案改过。30 个层包和决策、证据图的最后修改时间都停在 2026-11-19 11:57；任务记录与 benchmark 清单在 2026-11-25 16:45 改过一次（cvfpu 任务的目标修订，见 SPEC 的同日条目），层包与索引不受影响。指纹在附录 A。

### 上一轮做成了什么 {#HO-S-002}
> 源：`VISUAL-WORKFLOW-DESIGN.md` §11；`SPEC.md` 里 2026-11-24 21:00 的条目

上一轮按用户的七点意见重做了层间的信息。层与层之间现在只传一张接口卡：本层要回答的问题、用了上游哪几条、交给下游的几条结论、没解决的问题、适用边界。每条结论标明实测、推断还是拍定。

核对时对新建任务下的层包强制五条规则：接口卡必须成文；标实测的结论必须指向登记在册且通过的测试；上游没验证完，下游不能通过；签字之后内容再改，签字作废；核对通过后接口卡再改，回到未验证。

界面只有一个状态词和五行核对回执。侧栏是研究总览、流程编排、证据与回溯、演进中心；层详情从流程编排点进去。领域、任务类型、成功判据、层级工作台、决策中心都去掉了。

这一轮的细节本文没有重写，在上面两个出处里。

### 现状缺口 {#HO-S-003}
> 源：方案 ORCH-F-001

平台现在其实是方案 2 的做法，有五处缺口：

- 层与角色的对应、每层的 skills 写死在 `platform/tools/wf.py` 里。角色包只有 3 份，没有声明任何 tools。
- 30 个层包里 26 个出自同一个 agent（`zcode-glm`），1 个是 `cursor-grok域-design-agent`，3 个为空。这是没有派工的下游症状。
- 没有派工机制，`.zcode/agents/` 不存在。`wf next` 只打印该哪个角色做，然后由同一个长会话接着做。
- `rtl-generation-discipline` 这个 skill 没挂到 L5 上。
- `wf brief` 只打印不落盘，事后查不到某一层开工时拿到了什么。

### 仓库已经公开 {#HO-S-004}
> 源：2026-11-25 11:40 前后上库；14:11 核对远端

本仓在写完本文初版之后做了三件事，接手的人要知道：

- 本地目录初始化成了 git 仓库，并推到了公开仓库 `xuefenghao5121/ArchLens`，分支 `main`。首发提交是 `8c8f5d2`，90 个文件，约 1 MB。
- 公开的只有平台本体。`.gitignore` 排除了研究数据（`domains/`、`tasks/`、`platform/store/`）、`SPEC.md`、含研究数字的复盘、全部第三方仿真器与负载。所以往仓库提交时，`git add -A` 不会把这些带进去。
- 仓库没有加许可证。那是替用户向第三方授权，没有替他做这个决定。
- `.gitignore` 也排除了整个 `.zcode/`。方案第 4 步要往 `.zcode/agents/` 生成九份定义，它们不会进 git。要不要给这个目录开例外，到那一步再定。

为了公开，文档里的本机路径换成了 `<repo>` 和 `<NDF-Harness>` 两个占位符；引擎、冒烟脚本、层详情页各改了一处文案（没有接口卡的包不再自称「旧包」）。附录 B 的指纹是改完之后重算的。

## 3. 机制：平台怎么组织的

### 四样东西各管一段 {#HO-M-001}
> 源：`通用微架构混合仿真Agent设计文档_v2.md` §3；`platform/CROSS-PLATFORM-ARCHITECTURE.md` §2

**内核**是 `wf.py`、schemas 和 store。模板定每一轮有哪些框、什么顺序；核对规则定一个框何时算完成；失效和决定怎么留痕也在这里。这些是确定的规则，不依赖任何 agent 的记性。

**方法论源**在 `platform/methodology/*.md`。`bind_hosts.py` 把它们投影成 ZCode 的 skill 和一份通用手册。改内容要改源，再重跑投影器。

**角色包**在 `platform/agents/`，现在三份，按角色分而不是按层分。

**界面**是 store 的只读投影，加上一个受控的写入口。它不立新事实。

层与层之间传的是接口卡。下游 agent 用 `wf brief` 拿到任务目标原话、本层的固定说明、上游的接口卡和接口卡的写法。

### 按什么顺序读 {#HO-M-002}
> 源：本次交接的读序

1. 本文。
2. `handoff/orchestration-plan-20261125.md`，方案全文。其中第 1、2 节是要读给用户的部分。
3. `VISUAL-WORKFLOW-DESIGN.md` 的 §11，上一轮做了什么。
4. `SPEC.md` 进展日志最上面的三条：2026-11-24 17:30、2026-11-24 21:00，以及指向本文的那一条。
5. `platform/methodology/layer-discipline.md`，层输出与交接的纪律。接口卡在纪律二下面。
6. `通用微架构混合仿真Agent设计文档_v2.md` 的 §2.6 和 §3，原则与三个面。
7. 需要对照 NDF 的做法时：`<NDF-Harness>/skill/ndf-workflow/` 下的 `OVERVIEW.md`、`delegate.md`，以及 `workflow/ndf.workflow.yaml`。

### 东西在哪 {#HO-M-003}
> 源：2026-11-25 核对的行号

- `platform/tools/wf.py`：流程引擎，4369 行。`LAYER_GUIDE` 在第 1153 行，是每层的固定说明和 skills。`LAYER_AGENT` 在第 2029 行，是层到角色的对应。`cmd_next` 在第 2048 行，是调度输出。`cmd_brief` 在第 2106 行，是工作简报。`layer_card` 生成给人读的卡片。
- `platform/agents/`：`profiling.md`、`design.md`、`integration.md`。
- `.agents/skills/`：三个 skill。`flow-layer-discipline` 和 `l3-criteria-guard` 是生成物。
- `platform/viz/`：`server.py` 是后端，`regress.py` 是回归，`cli_smoke.sh` 是命令行冒烟。
- `frontend/src/features/`：`flow/` 是流程编排，`layers/` 是层详情，`evidence/` 是证据与回溯，`dashboard/` 是研究总览。
- `tasks/` 是任务记录，`domains/` 是层包，`platform/store/` 是证据。
- `.zcode/backups/`：每一轮改动前的原文件。

## 4. 怎么接着做

### 批准与指纹 {#HO-X-001}
> 源：方案 ORCH-D-001、ORCH-D-002；用户 2026-11-25 的「开始执行」

用户在 2026-11-25 14:26 说「开始执行」。批准的是方案文件 sha256 `2d902a153df4977f861bbca7d7c44623ad3b8da660bce3eebef0e8923f92ae72` 那一版全文，含三点修正和五项假设。方案没有覆盖的两点（HO-Q-003）不在批准范围内，这一版不处理。

批准之后，方案文件只改了条款状态（`tbd` 改为已决定），当前指纹见第 8 节。之后方案文件再变，把改了什么给用户看，再要一次批准。

用户批准的那一刻，记下方案文件的 sha256。本文写作时它是附录 B 里的值。之后方案文件变了，把改了什么给用户看，再要一次批准。

### 实施记录（已完成）{#HO-X-002}
> 源：方案 ORCH-S-000 到 ORCH-S-008；`VISUAL-WORKFLOW-DESIGN.md` §12

1. **ORCH-S-000 已核实**：宿主从 `.zcode/agents/*.md` 读 `name` / `description` / `tools` /
   `disallowedTools` / `skills` / `model` / `thoughtLevel` 等；项目级定义不允许 `permissionMode`；
   **定义在会话启动时载入**（当前会话里放探针，Agent 工具报 not found）。
2. **ORCH-S-001 已建**：`platform/agents/` 下八份层定义、一份调度定义、`toolpacks.json`、
   `layer_agent.json`（已登记 registry）。
3. **ORCH-S-002 已接**：引擎加载并校验定义；`guide` / 卡片 / `brief` / `next` 都带 agent；
   L5 补上 `rtl-generation-discipline` 与 `caselaw`；新增 `brief --out`、`show --for`、`run-log`；
   状态导出升 0.5。
4. **ORCH-S-003 已做**：`WF_ACTOR=layer:*` 下四种人动作被拒、`decide` 强制待批。
5. **ORCH-S-004 已做**：`platform/methodology/orchestration.md` → skill `flow-orchestration` 与手册；
   投影器生成九份 `.zcode/agents/<host_name>.md`，层 agent 给读写工具，调度 agent 只给读与 Bash。
6. **ORCH-S-005 已做**：`/api/guide` 带 agent 正文；动作 `brief_out`、`run_log`；流程页的
   「每一层由谁做、用什么」表与框上的 agent 名；层详情的 agent 栏、开工简报 / 另读 / 上次执行；
   证据页的「另读」。
7. **ORCH-S-006 已做**：回归 178 条（新增 20）、命令行冒烟新增第 10 步。
8. **ORCH-S-007 部分完成**：类型检查零错误；隔离副本浏览器实测通过；**派工冒烟待新会话**。
9. **ORCH-S-008 已做**：设计文档 §12、两个 README、三个角色包指向层定义、SPEC、本文件、
   备份目录的 `NEW_FILES.txt`；两个服务重启后只读复核通过。

可以直接拿来用的代码位置，方案收在 ORCH-R-001。

### 如果用户选的是方案 2 {#HO-X-005}
> 源：方案 ORCH-I-002 的 `level=tbd`；本次会话

方案 2 没有被否掉，它只是不被推荐。用户如果说「走方案 2，一个 agent 用 skills 串起来」，**不要**把方案 1 的步骤套上去，那份方案要重写。

重写时至少要处理这几件事：

- 方案 1 里用来保证可信的三样东西会一齐失效：每层上下文独立、层 agent 不能替人签字、上游没验证就不派下游。方案 2 里同一个上下文既写判据又做裁决，事故复盘的教训（ORCH-I-002 第 3 条）会重新进来。
- 方案 ORCH-I-003 里列的「方案 1 的代价」，在方案 2 里不再成立；反过来，方案 2 的代价要写成约束：每一步都带着全部历史，上下文满了之后压缩掉什么没人看得到。
- 能保留的是那些与上下文无关的部分：模板定顺序、核对规则定完成、接口卡、决定回执、`wf brief` 落盘、每层执行记录。它们本来就属于内核，不属于某个 agent 的记性。
- 方案 1 的第 0 步（核实宿主子代理格式）在方案 2 里不需要；但「一个 agent 管到底」仍需一份能被人读到的编排规程，否则同样是人看不到它为什么走了某一步。

### 怎么向用户汇报 {#HO-X-006}
> 源：用户偏好（个人 AGENTS.md）；本次会话

用户可能通过微信读你的回复。超过一屏的汇报，MUST 在对话里给一段纯文本摘要，不要用 markdown 表格。附录 E 有一段可以直接用的。

事实与判断要分开写：哪个是文件里写明的，哪个是推出来的，哪个没测过。这个平台的用户对这三者的区分很敏感，过去正是因为把推断当成结论，才出了 11-13 那次事故。

### 怎样算做完 {#HO-X-003}
> 源：方案 ORCH-V-001；括号里是 2026-11-25 的实测结果

agent 说做完了不算。下面每一条都要有落盘的结果：

1. 回归退出码 0，末行写「真实 store 指数未变」。（178 通过 / 0 失败，末行符合）
2. 命令行冒烟的末行是 `ALL CLI SMOKE CHECKS PASSED`，引擎自检 15 项全符合。（两项都符合）
3. 投影器的检查退出码 0。（`bind_hosts.py --check` 退出码 0）
4. 类型检查零错误。（退出码 0）
5. 沙箱里用浏览器走一遍新增的界面。（agent 表 8 行、框上 agent 名、层详情 agent 栏与完整定义、
   留档后页头出现简报时间、执行记录、证据页「另读」——都过了）
6. 派工冒烟的结果如实汇报。**（未做：宿主定义要新会话才载入；见 HO-Q-001）**
7. 真实 store 的层包、决策、证据图按修改时间确认没有被写过。（仍停在 2026-11-19 11:57，指纹未变）
8. 重启两个服务，只读复核正式界面。（已重启并复核）

### 环境怎么起 {#HO-X-004}
> 源：`platform/viz/README.md`、`frontend/README.md`

两个服务是手动起的进程，机器重启之后要重新拉起：

```bash
python3 platform/viz/server.py --port 8899          # 后端，默认连真实 store
cd frontend && npm run dev                          # 前端，/api 代理到 8899
```

做试验用隔离副本：把 `platform/` 下的 `tools`、`schemas`、`store`，连同 `domains/`、`tasks/` 复制到 `/tmp` 下的一个目录，用 `WF_ROOT` 指向它再起后端。`platform/viz/regress.py` 的 `build_sandbox` 是现成的参考。

## 5. 边界：接手的人可以动什么

### 永远不要做 {#HO-B-001}
> 源：方案 ORCH-N-001；`platform/README.md`；`SPEC.md` 的协作规范

- 没有用户的批准，不实施方案的任何一步。
- 不替用户签字。不批准、不驳回任何待批事项，不以用户的名字记决定。第 6 节里那三条旧的待批决定也一样。
- 不直接改 `platform/store/` 下的记录，不手改已有层包里结论性的字段。写操作只走 `wf` 命令或界面。
- 不在真实 store 上做试验。
- 不动 `<NDF-Harness>`。
- 不编辑、不重排 `SPEC.md` 里别人的条目。

### 可以做 {#HO-B-002}
> 源：同上

- 读仓库里的任何文件。
- 在 `/tmp` 下的隔离副本上跑回归、冒烟和浏览器实测。
- 用户批准之后，在方案列出的可写范围里改动。
- 用户改了主意时改方案文件，改完重新写这份交接。

### 动手前后应该跑 {#HO-B-003}
> 源：`platform/viz/README.md` 的回归一节

回归、命令行冒烟、引擎自检、投影器的检查、类型检查。改动之前跑一遍记下结果，改动之后再跑一遍对比。

## 6. 等用户一句话的事

### 还剩这几件事 {#HO-G-001}
> 源：本次会话；`SPEC.md` 里 2026-11-24 21:00 条目的「未做 / 遗留」

1. **派工用哪种方式**：两种都支持。人工路径已走通；自动路径要下一次新会话才能冒烟。
2. **是否对局域网开放服务**。现在只能在本机打开。
3. **三条旧的待批决定**，和这份方案无关，但一直挂着：sme2 迭代 3 的 L7（`PKG-L7-sme2-i3-chainform-verdict`）和 L8（`PKG-L8-sme2-i3-conclusion`），cvfpu 的 L1（`PKG-L1-cvfpu-cast-overflow`）。看它们的内容用 `python3 platform/tools/wf.py card <包名>`，或者在界面上打开对应层，在「人的决定」里看。**提醒用户一句就够了，不要替他批。**
4. **独立验证 agent 要不要做**（HO-Q-003 第 1 条）。这一版没纳入，是一份独立的设计。

## 7. 没解决的、没测的、本文没写进来的

### 没有确立的 {#HO-Q-001}
> 源：本次会话

- 宿主子代理定义的格式，以及它对 tools 的限制是否生效。方案的第 0 步就是去核实它。
- 两种做法哪个效率更高，没有实测。方案里「每层记录耗时和模型」做完之后才有数据可以比。
- **自动派工还没冒烟。** 宿主的子代理定义在会话启动时载入，实施它的那次会话里看不到新定义。下一次开新会话后，
  用 `sim-layer-l1` 走一层（`wf next` → `wf brief --out` → 派给它 → `wf gate` + `wf run-log`），把结果如实补记。
- `.zcode/agents/` 生成的定义要不要进 git。整个 `.zcode/` 现在被 `.gitignore` 排除，见 HO-S-004。

### 本文没有写进来的 {#HO-Q-002}
> 源：本次会话

- 上一轮的全过程。只写了它做成了什么和去处。
- 用户在 2026-11-24 那一轮的七点意见原文。它在 `SPEC.md` 的 2026-11-24 21:00 条目里。
- NDF-Harness 没有通读。我自己读了条款格式、语言规范、关于分层散文和人读面的两份提案、`ndf-workflow` 的总览与关闭两页。委派那页是一个子代理代读的，我只拿到了它摘出的几行。
- 这次会话很长，更早的过程已经被压缩成了摘要。摘要里的话，凡是没有在本文或方案里重新核对过的，不要当作事实。

### 方案没有覆盖的两点 {#HO-Q-003}
> 源：方案 ORCH-Q-001。这是我把方案和设计文档对照出来的，用户确认过我的理解，但没有说要不要把它们纳入方案

- **独立验证面。** 方案给了八个执行 agent 和一个调度 agent，没有给验证 agent。测试仍由做这一层的 agent 自己写。设计文档 v2 §6.2 说的 V0 风险（生成者自测容易和实现共享误解）还在，§13 把独立验证列为平台成立的必要条件。按设计文档，验证者应该独立于生成者，也就是独立于它的上下文。
- **演进中心。** v2.2 §11.1 说演进对象包括 prompt、工具选择策略、agent 与模型路由、编排 agent 自身。这些现在都不是文件，演进中心没有东西可以版本化。方案把它们变成文件，等于铺了地基，但没有碰演进中心。

## 8. 回执

本文写作时，和这份方案有关的决定只有下面几件。「口令」一栏是用户原话；没有原话的写明情况。

| 事件 | 口令 | 谁 | 时间 | 对象 | 状态 |
|---|---|---|---|---|---|
| 给出分析与推荐 | 没有口令 | zcode-glm | 2026-11-25 上午，未记精确时间 | 两个方案的比较 | 已给出，用户没有表态 |
| 提交方案 | 没有批准 | 用户 | 2026-11-25 | `orchestration-plan-20261125.md` | 未批准，未实施 |
| 要求写交接 | 「把目前的待执行方案写到本地，并且按ndf哲学输出一份handoff交接文档」 | 用户 | 2026-11-25 | 本文与方案文件 | 已收到 |
| 批准「写两份文档」 | 没有口令，在计划模式下点了批准 | 用户 | 2026-11-25，10:53 到 10:58 之间 | 本文与方案文件 | 已执行 |
| 确认对方案目的的理解 | 「你的理解是对的」 | 用户 | 2026-11-25，14:11 之前，未记精确时间 | 方案的目的、设计理由、对「可编排性」的理解（HO-I-003、ORCH-I-004） | 已确认。不含对执行的批准，不含五项假设 |
| 批准执行 | 「开始执行」 | 用户 | 2026-11-25 14:26 | 方案全文，sha256 `2d902a153df4977f861bbca7d7c44623ad3b8da660bce3eebef0e8923f92ae72`；含 ORCH-D-001 三点修正、ORCH-D-002 五项假设 | 已批准 |
| 实施完成 | 无新口令（按执行批准） | zcode-glm | 2026-11-25 14:54 | 方案 ORCH-S-000…S-008 | 除自动派工冒烟外全部完成；回归 178 / 0，冒烟、自检、投影、类型检查全过 |

用户批准的是 sha256 `2d902a153df4977f861bbca7d7c44623ad3b8da660bce3eebef0e8923f92ae72` 那一版。批准后方案文件只把条款状态从 `tbd` 改为已决定，当前指纹是 `ed546e7488b61f03c79c9eb2512764ec4c3fe4e88233f365ca994522e50ce720`。

## 9. 本文与 NDF 做法的对应

> 源：`<NDF-Harness>/skill/ndf-workflow/OVERVIEW.md`、`norms/meta/language.md`、`norms/meta/open/proposal-meta-compiler-layered-prose.md`

- NDF 的事实源是带稳定 ID 的条款树。这里对应方案文件，它的条款 ID 是 `ORCH-…`。
- NDF 给人审的是从条款编出来的分层散文，不是节点表。这里对应本文：按意图、现状、机制、怎么做分层，数字都放附录。
- NDF 每条标题下有溯源行。这里对应每个标题下面的「源」。
- NDF 的 pack 带写根。这里对应第 0 节的可写范围与禁写。
- NDF 的指挥面先造 pack、等人审、再派发。这里对应「第一步不是动手」。
- NDF 只认磁盘上的完成回执。这里对应「怎样算做完」里的八条。
- NDF 的截断要写成人话。这里对应第 7 节。

## 附录 A：store 指纹

> 机器核对用。2026-11-25 10:58 在 `<repo>` 下算。

算法：把文件按路径排序，依次拼接每个文件的字节，取 sha256 的前 16 位。

| 范围 | 文件数 | 指纹 |
|---|---|---|
| 层包信封 `domains/*/*/PKG-*/envelope.json` | 30 | `038508277a933aac` |
| store 索引（不含 benchmarks）+ 层包信封 + `pack.json` | 6 + 30 + 2 | `87156577970fa1b9` |
| `tasks/*.json` + `benchmarks.jsonl` | 3 + 1 | `df71428de031a8d7`（16:45 任务记录修订后；旧值 `ac34ebb003114409`） |
| `regress.store_fingerprint()`，另一种算法，完整值 | — | `b8adb5f7cf67f8421270a4c99972f0079cd5fb1a1b56afc83a6e3b53f7859bf0`（旧值 `8de06b81…`） |

复现前三行：

```bash
python3 - <<'EOF'
import hashlib, glob
from pathlib import Path
def cat(files):
    h = hashlib.sha256()
    for p in sorted(files): h.update(Path(p).read_bytes())
    return h.hexdigest()[:16]
env = glob.glob('domains/*/*/PKG-*/envelope.json'); pack = glob.glob('domains/*/pack.json')
store = [p for p in glob.glob('platform/store/**/*.jsonl', recursive=True) if 'benchmarks' not in p]
tasks = glob.glob('tasks/*.json')
print(len(env), cat(env))
print(cat(store + env + pack))
print(cat(tasks + ['platform/store/index/benchmarks.jsonl']))
EOF
```

复现修改时间：

```bash
ls -lt --time-style=long-iso domains/*/*/PKG-*/envelope.json | head -1
ls -lt --time-style=long-iso platform/store/index/*.jsonl platform/store/graph.jsonl tasks/*.json | head -3
```

接手时先算一遍。和上表不一样，说明有人动过 store，先弄清楚是谁、为什么，再往下做。

## 附录 B：改动前的文件指纹

> 机器核对用。sha256 的前 16 位。多数在 2026-11-25 10:58 算；`platform/tools/wf.py`、`platform/viz/cli_smoke.sh`、`frontend/src/features/layers/LayerDetail.tsx` 三项在上库前改过一处文案，是改完后重算的。13:18 核对，26 项全部与当前文件一致。方案获批之后，这些文件大多会被改到；动手之前先核对。

```
147dac6f1b391a55  platform/tools/wf.py
22cd4a2f2a0acaf2  platform/tools/layer_bus.py
867c8c702c9a1e05  platform/tools/bind_hosts.py
e93435a855ad88d4  platform/tools/caselaw.py
aff7a2a0364fbe69  platform/agents/profiling.md
8d663057f08c2b67  platform/agents/design.md
58a8cbd9972d5ddb  platform/agents/integration.md
e4967ebc316a6ef8  platform/methodology/layer-discipline.md
59a6fcf6637eb65d  platform/methodology/l3-criteria.md
bc1a23a9cc70b774  platform/schemas/registry.json
3610fbf58bbeb785  platform/schemas/layer_package.json
698fa9f74f4be2ba  platform/schemas/workflow_state.json
a9e382f9272bcb32  platform/viz/server.py
a33bdb225ec7663c  platform/viz/regress.py
49d45ea3e361afe7  platform/viz/cli_smoke.sh
6a646d820e9dd7d6  frontend/src/App.tsx
98fdc74ed31e7816  frontend/src/types/index.ts
54c5bac02f0e9c3b  frontend/src/api/client.ts
a196e275472740cc  frontend/src/features/tasks/useWork.ts
0348361c74d5be37  frontend/src/features/flow/Flow.tsx
c16da66a508be969  frontend/src/features/layers/LayerDetail.tsx
5c69215a926b9448  frontend/src/features/evidence/Evidence.tsx
328557a674c87043  frontend/src/features/shared/useAction.ts
c09a6500d804b832  frontend/src/features/shared/ui.tsx
aa4c52629b051ccb  frontend/src/status.ts
993769d8ee70065f  VISUAL-WORKFLOW-DESIGN.md
```

复现：

```bash
sha256sum platform/tools/wf.py | cut -c1-16
```

本仓现在是 git 仓库（见 HO-S-004）。「改动之前」的原件有三重保障：git 里的首发提交 `8c8f5d2`、`.zcode/backups/` 里的备份、上面这张表。

## 附录 C：类型检查用的临时配置

> 机器核对用。

把下面的内容存成仓库之外的任意位置，比如 `/tmp/tscheck/tsconfig.json`，然后在 `frontend/` 下跑 `npx tsc -p /tmp/tscheck/tsconfig.json`。退出码 0 就是零错误。

```json
{
  "compilerOptions": {
    "target": "ES2020", "lib": ["ES2020", "DOM", "DOM.Iterable"], "module": "ESNext",
    "moduleResolution": "bundler", "jsx": "react-jsx", "strict": true, "noEmit": true,
    "skipLibCheck": true, "isolatedModules": true, "resolveJsonModule": true,
    "allowImportingTsExtensions": true, "noUnusedLocals": true, "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true, "useDefineForClassFields": true,
    "typeRoots": ["<repo>/frontend/node_modules/@types"],
    "types": ["react", "react-dom"]
  },
  "include": ["<repo>/frontend/src"]
}
```

## 附录 D：已经踩过的坑

> 源：本次会话与上一轮；`frontend/README.md` 的「工程注意事项」

- 前端开发服务器偶尔漏掉同一个文件的第二次修改，交出一个半新半旧的模块，页面变成空白。类型检查照样通过，因为磁盘上的源码是对的。出现空白就重启 `npm run dev`。
- 用 `pkill -f` 按命令行找进程，会把执行它的那个 shell 一起杀掉。按端口找到进程号再结束它。
- `wf gate` 在「待人决定」和「未验证」时退出码不是 0，这是设计如此。
- 浏览器自动化里，点击偶尔超时。改成在页面内触发点击。`window.confirm` 会卡住页面内的调用，要把点击推迟，再接住弹出的确认框。
- 隔离副本的后端在启动时载入引擎代码。改了 `wf.py` 要重启那个后端才生效。
- 计划模式下，命令行只放行只读的命令。列目录、看时间可以；跑 python、访问网络、往 `/tmp` 写文件都不行。
- store 没有并发锁。多个 agent 同时写要错开。

## 附录 E：一段可以直接发给用户的纯文本

> 源：用户可能通过微信阅读，长汇报要给纯文本摘要。下面是接手的 agent 第一次向用户汇报时可以直接用的一段，按实际情况改。

```
方案已经写到本地了，没有动任何平台代码和层包。

两个文件：
handoff/orchestration-plan-20261125.md    待执行方案，未批准、一步未实施
handoff/orchestration-handoff-20261125.md 交接文档

方案的结论是走方案 1：每层一个 agent 定义，调度规则放在确定的地方，层与层共享的是
store 里的接口卡而不是会话。依据是设计文档早定的「不共享隐式上下文、只共享显式契约」，
NDF-Harness 自己也是这个形态，还有 11-13 那次判据错误跨了五层才被你发现。

要说清楚的：效率「方案 1 更高」是按文件大小推出来的，没有两种做法的实测数据。
token 数也是粗估的。宿主子代理定义的格式我还没核实，这是方案的第 0 步。

方案里有五项假设是我按推荐项做的，不是你的偏好，麻烦你确认或改：
1. 八份层定义加一份调度 agent 定义，三个角色包保留
2. 派工人工和自动都支持
3. 默认按顺序派工，不并行
4. 工具边界先做声明加尽力拦截，不是硬隔离
5. 这一版不接按层选模型

现在核过一遍：回归 158 条全过，自检 15/15，类型检查零错误，两个服务在跑，
store 没有被写过（层包和决策的修改时间还停在 11-19）。

要我开始做，说一声就行；要改哪一条，报条款号（比如「去掉 ORCH-S-003」）。
另外还有三条旧的待批决定挂着，和这次无关，sme2 迭代 3 的 L7、L8 和 cvfpu 的 L1，
要不要一起看你说。
```

