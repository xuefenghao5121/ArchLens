# 仿真 Agent 跨平台复用架构方案（v1.2）

> **架构维度修正（2026-11-17 用户指令）**：多 agent（ZCode/Cursor 等）是实践分工方式，
> 不是架构维度。**跨平台 = 跨目标硬件平台（ISA）× 跨仿真器（+环境可迁移）**。
> 方法论本身是文件（任何 agent/人可读），故"宿主绑定层"收缩为最小：方法论源 + 当前
> 实践载体（ZCode skills）投影，无多宿主产品化目标。

> **产品定位（已确认 2026-11-17）**：内部工具产品为主（A），对外交付为自然衍生（B）。
>
> **产品三层定义**：
> 1. **单次产品 = 评估交付包**（输入：负载+微架构优化设想；输出：使用层[软件/硬件分析+
>    定制仿真器二进制+运行指导+benchmark] + 开发层[RTL+patch+编译+复现]）——FLOW-1 为首个
>    下线样例；B 场景下此包可直接外发
> 2. **生产线 = 仿真 Agent 工作台**（本架构的五层）——A 场景的内部工具：可安装、可版本化
>    发布、有出厂质检（selftest/护栏/纪律）、章程→L1-L8→收敛全流程
> 3. **复利资产 = 域资产库**（benchmark+护栏表、RTL 单元库、归因库、校准证据）——第 N 次
>    评估远便宜于第 1 次的核心价值
>
> **产品边界（说明书里的"不做什么"）**：不承诺真机结论（无校准止步 model 级）｜不做签核｜
> 不做无限迭代（章程收敛）｜跨基架数字不做裁决——克制即可信度。
>
> 背景：FLOW-1 三轮迭代实战后提出。目标：让这套"归因→设计→RTL 仿真验证"的仿真 agent 能在
> **不同目标硬件平台**（ARM/RISC-V/…）、**不同仿真器**（gem5/QEMU/自研/商业）、**不同 agent
> 宿主**（ZCode/Cursor/CI/人类）之间复用。原则与《通用微架构混合仿真Agent设计文档_v2.2》
> §3.5/§10 一致，本文是它的落地细化，并吸收三轮实战新增资产（纪律 skills、两层交付结构、
> 任务收敛机制）。

## 1. 复用的分层模型（先想清楚什么复用、什么必须换）

FLOW-1 实测告诉我们，一个仿真 agent 其实捆着五层东西，耦合度完全不同：

| 层 | 内容 | 换平台时要不要换 |
|---|---|---|
| **方法论** | 八层纪律、判据锚点、基架分级、因果链表述、任务收敛 | **永不换**（这是最值钱的资产） |
| **流程内核** | wf.py + schemas + store + 门禁/失效/决策机制 | **永不换**（已零领域语义） |
| **领域知识** | benchmark、护栏、归因库、方案候选、RTL 单元 | **必换**（换平台=换问题本身） |
| **仿真器** | gem5+Verilator+pyCircuit+注入机制 | **可插拔**（按能力选配） |
| **宿主环境** | agent 跑在哪（ZCode/Cursor/CI）、工具链/主机 | **可插拔**（文件协议已宿主无关） |

当前实现的耦合点（要修的）：角色包里的"环境速查"混着 gem5/SME2 命令；skills 是 ZCode 私有
格式；L7 的复现命令散在包字段里没有抽象成适配器；domain 结构没有模板化。

## 2. 五层架构

```
┌─────────────────────────────────────────────────────────┐
│ H 宿主绑定层  host-bindings/{zcode,cursor,ci,manual}/    │  ← 方法论分发成各宿主格式
├─────────────────────────────────────────────────────────┤
│ M 方法论层    platform/methodology/（宿主无关 md 源）      │  ← 八层纪律+收敛（现 skills 的本体）
├─────────────────────────────────────────────────────────┤
│ K 流程内核    platform/{tools,schemas,store}/            │  ← 已通用，冻结语义
├─────────────────────────────────────────────────────────┤
│ D 领域包层    domains/<name>/（模板化 + 特定领域包）        │  ← benchmark/护栏/RTL/契约
├─────────────────────────────────────────────────────────┤
│ A 仿真适配层  adapters/<sim>/（统一五接口 + 能力声明）      │  ← gem5 参考实现
├─────────────────────────────────────────────────────────┤
│ E 环境绑定    env/（版本锁 + 容器/nix 描述 + 构建配方）     │
└─────────────────────────────────────────────────────────┘
```

## 3. 核心抽象一：Simulator Adapter（换仿真器只写一份清单）

每个仿真器一个适配器清单（YAML），实现五个接口：

```yaml
# adapters/gem5-arm/adapter.yaml （参考实现=FLOW-1 的 gem5）
identity: {name: gem5-arm, version: v26+ARM_glm}
capabilities:            # 能力声明——上层按此选配注入机制与实验形态
  cycle_accurate: true
  injection: [scoreboard, fu_occupancy, timing_suspend]   # 三种占用注入
  modes: [se, fs]
  checkpoint: true
  functional_ref: false   # 功能裁判要配 qemu 适配器
interfaces:
  build: {cmd: "scons build/ARM_glm/gem5.opt", env: "env/toolchain.sh", fingerprint: [git-HEAD, diff-hash]}
  run: {cmd: "./build/ARM_glm/gem5.opt --outdir={outdir} configs/…{adapter_args}", timeout_hint: 600}
  stats: {window: "system.roi_cpu.numCycles|system.cpu.numCycles", parse: "regex-table",
          counters: [attn.cmds, attn.beats, attn.tail_ticks, zpath.luti_cmds]}
  guard: {pattern: "m5_fail instruction encountered code (?P<code>-?\\d+)", binding: "domain.benchmarks[].guard"}
  inject: {params: {luti_wide: "--luti-wide", stream_fmla_lat: "--stream-fmla-lat", …},
           semantics_ref: "domains/<d>/contracts/injection.md"}
```

规则：**领域包只面向接口编程**（L7 的 reproduce_command 由 adapter 渲染），禁止在层包里
硬写仿真器命令。换 QEMU 做功能裁判、换自研仿真器做周期，各写一份 adapter.yaml + 五个
接口实现脚本即可；能力声明不够的实验形态（如无注入能力）在 L4 契约期就被发现，而不是实验期。

## 4. 核心抽象二：Domain Pack 模板（换目标平台从模板起）

`platform/templates/domain/` 固化 domains/sme2 的结构：

```
domains/<name>/
├── pack.json            身份/benchmark/指标/校准槽位（schema 已有）
├── benchmarks/          负载定义 + **护栏表**（退出码——跨仿真器的正确性锚）
├── contracts/           微架构契约（含 injection.md：本域注入参数语义）
├── rtl/                 或指向 designs/<name>/ 的资产索引
├── environment.md       本域环境速查（从角色包里迁出来的 gem5/工具命令）
└── L1..L8/              层包（wf new-pkg 生成）
```

新平台起手式：`wf new-domain --from-template` → 填 benchmark+护栏 → 选/写 adapter → L1 开跑。
护栏表是关键设计：它是**仿真器无关的正确性锚**（QEMU/gem5/自研都用同一退出码对账）——
FLOW-1 的护栏三次抓 bug 证明这个锚值得成为一等公民。

## 5. 方法论源与绑定（最小化）

- **方法论源**：`platform/methodology/*.md`——八层纪律、判据锚点、基架、收敛、角色职责。
  方法论就是文件，**任何 agent/人直接可读，本无宿主耦合**。
- **投影器**：`platform/tools/bind_hosts.py` 把源投影到当前实践载体（ZCode skills，
  沿用历史名）+ 通用手册副本。换实践载体时改投影器即可，架构无感。
- **多 agent 协作**（实践层，非架构层）：SPEC.md 文件协议 + 角色按层认领，谁用哪个工具
  跑都行。

## 6. 环境与可移植

- 版本锁（COMPILER_VERSION.txt 模式）+ `env/` 目录：toolchain.sh（现 env.sh 泛化）、
  `container/Dockerfile`（或 nix）把 g++/Verilator/pyCircuit/gem5 构建链封装成可分发票据
- 交付物两层结构（usage/dev）固化为 `platform/templates/delivery/`——FLOW-1 踩出来的形状
  直接成为下次的模板

## 7. 复用场景与复用率（验收这个架构的标尺）

| 场景 | 复用 | 重写 | 预估工作量 |
|---|---|---|---|
| **换目标 ISA**（如 RISC-V RVV 评估） | 方法论+内核+宿主绑定+gem5 adapter（部分） | 领域包全部（benchmark/护栏/RTL/契约） | 主体=领域工作（三轮量级） |
| **换仿真器**（如自研周期模型/QEMU 功能裁判） | 方法论+内核+领域包+护栏表 | 一份 adapter.yaml+五接口实现 | 1-2 天/仿真器 |
| 换实践工具/协作方式（非架构维度） | 全部实质资产 | 方法论是文件，直接读/改投影器 | 分钟级 |
| **换主机/环境** | 全部 | 容器/版本锁重建 | 小时级 |

## 8. 实施计划（从现状到目标架构）

| 阶段 | 内容 | 量级 |
|---|---|---|
| P1 解耦 | 角色包环境速查迁入 domain environment.md；skills 内容迁 methodology 源+bind_hosts.py 投影（ZCode+通用手册） | 半天 |
| P2 适配器 | 从 L7 包字段抽取 gem5-arm adapter.yaml+五接口脚本；int4_se/fs_sme 参数表入 injection.md；回头验证：L7 reproduce_command 可由 adapter 渲染 | 半天-1 天 |
| P3 模板 | domain 模板 + delivery 模板；wf new-domain --from-template；smoke：用模板空跑一个 mini 域 | 1 天 |
| P4 容器 | env/ 容器化 + 跨机重建验证（复现指南的阶段 0-2 作为验收） | 1 天 |
| P5 试炼 | 选一个真实小场景（如 QEMU 功能裁判 adapter 或一个新 benchmark 的 L1-L2）走通全链 | 按场景 |

## 9. 与设计文档 v2 的对应关系

- §3.5 通用内核+领域包 → 本文 K/D/A 三层的具体化；适配器把 §3.5 的"仿真适配器"从概念
  变成五接口契约
- §9 平台故障注入验收 → 内核已过（selftest 15/15）；跨平台后每新增 adapter/域模板需补
  该层自检
- 三轮新增资产归位：纪律→M 层、两层交付→模板、任务收敛→M 层（纪律六已入）
