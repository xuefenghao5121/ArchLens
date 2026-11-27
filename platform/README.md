# platform — 通用微架构混合仿真平台内核（MVP-A）

实现《[通用微架构混合仿真Agent设计文档_v2.2.md](../通用微架构混合仿真Agent设计文档_v2.2.md)》的最小可行版本。
本目录是**通用内核**：不含任何 SME2/LUTI/ZT0 领域语义（设计文档 §3.5/§10），领域内容在 `domains/<name>/`。

```
platform/
├── schemas/    14 个 JSON Schema（层包信封、测试三分离、决策、领域包、DAG 边）
├── tools/wf.py 流程 CLI（stdlib-only，无第三方依赖）
├── store/      不可变工件与证据存储
│   ├── objects/          内容寻址工件副本（sha256 前 16 位目录）
│   ├── index/*.jsonl     追加式注册表（工件/决策/测试定义/运行/结果）
│   └── graph.jsonl       证据 DAG 边（追加式，不得改写历史）
└── selftest    （故障注入用例内置在 wf selftest，临时沙箱执行）
```

## 快速上手

```bash
WF="python3 platform/tools/wf.py"

$WF status --domain sme2          # 总览：包状态 + 待批决策队列 + DAG
$WF show <PKG-id>                 # 人类可读的层包视图（不用直接读 envelope.json）
$WF review                        # 待批决策审批卡片（全文理由+证据+批准命令）
$WF selftest                      # 平台自检：15 个已知真值故障必须全部被拦截
$WF gate --all --domain sme2      # 对全部层包执行门禁
```

**人机分工**：envelope.json 是机器层唯一事实源（哈希/引用/门禁都靠它），人不直接读写；
人用 `show`/`review` 看内容、用 `approve/reject` 交互，payload 编辑归执行面 agent。

## 一层工作的标准流（以 L2 归因为例）

```bash
# 1. 建包（或直接手写 envelope.json —— 它就是普通 JSON）
$WF new-pkg --domain sme2 --layer L2 --slug my-hypothesis \
    --title "..." --owner zcode-glm --intent "..."

# 2. 登记证据（小文件自动复制入 store；>8MB 默认只记哈希；git 仓库记 HEAD+diff）
$WF register-artifact fs/out/xxx/stats.txt --type stats --note "..."
$WF register-git src/gem5 --name gem5

# 3. 把证据填进 envelope：artifacts[]（带 content_hash）、payload（层规则要求的字段）、
#    provenance.parent_package_refs（上游依赖）

# 4. 挂测试（定义/运行/结果三分离；--run-artifact 一并落 run+result）
$WF test-register --pkg PKG-L2-sme2-my-hypothesis --file def.json \
    --run-artifact stats.txt --verdict pass --measured "cycles=1234"

# 5. 写决策（高权重层必须有 reviewer≠pending-user 的决策才能 pass）
$WF decide --pkg PKG-L2-sme2-my-hypothesis --type ratify \
    --rationale "..." --confidence high --reviewer pending-user

# 6. 门禁
$WF gate PKG-L2-sme2-my-hypothesis
```

## 门禁规则（§6 的 MVP-A 子集）

verdict 优先级：**fail > needs_decision > unresolved > pass**（所有 WARN 均写入 gate_report，不隐藏）。

| 检查 | 拦截条件 |
|---|---|
| schema | envelope 不符 layer_package.json |
| 工件完整性 | 未注册 / envelope 哈希与注册表不符 / 文件漂移（gate 每次重算哈希）/ 证据丢失 |
| 引用可解析 | 决策/测试定义/运行/结果引用悬空 |
| 上游生命周期 | parent_package_refs 中有 invalidated/superseded 包 |
| 层规则 | L1 指标须有 name+definition+unit；L2 须有可证伪命题+实验矩阵+**替代解释≥1**；L3 方案须显式 targets_bottleneck；L4 资源参数须标 assumed/sanity_checked/validated_by_synthesis；L5 须有 testbench；L7 须有环境+复现命令；L8 结论须标 model_causal_status |
| 人类决策 | L1/L2/L3/L4/L6/L8（高权重）须有 reviewer≠pending-user 的决策 |
| 测试证据 | L2/L5/L6/L7/L8 须至少一条 verdict=pass 的测试结果 |
| 外推门禁 | L8 声明 hardware_supported 时领域包必须有校准证据（calibration.evidence_refs 非空） |

## 失效传播（§4.4）

```bash
$WF invalidate ART-xxxxxxxxxxxx --reason "RTL 源修改"
# 沿 depends_on 边 + envelope parent refs 双通道传播，下游包 lifecycle→invalidated
# （不删历史；重新登记新工件后修订包即可恢复）
```

## 设计取舍（诚实清单）

- **stdlib-only**：机器记录用 JSON；schema 校验器只支持 type/const/enum/pattern/minLength/minItems/required（无 $ref/oneOf）。
- **漂移检测每次重算哈希**：曾用 size+mtime 快速路径，自检 F4 证明同秒篡改可绕过，已移除。大文件（vmlinux/gem5.opt 按 no-copy 登记）gate 时会重算，代价可接受。
- **verdict 屏蔽顺序**：needs_decision 会遮住 unresolved（如证据缺失+待批同时存在）——gate_report 两条 WARN 都在，看报告不看单一 verdict。
- **store 是单机目录**，无并发锁：多 agent 并发写 index/graph.jsonl 时按 SPEC.md 协作规范错峰；追加式设计使覆盖他人记录不太可能，但严格串行更稳。
- **不做**：orchestrator 状态机、schema registry 服务、自动失效触发（上游变化须人工 invalidate 或下次 gate 时被哈希漂移拦截）。这些是 MVP-B 及以后的事。

## 多 agent 协作

- 层包 owner 在 envelope.identity.owner_agent；领域级 ownership 见 `domains/sme2/pack.json` layer_ownership。
- 跨 agent 写他人层包前，按 SPEC.md 协作规范在 SPEC.md 登记条目。
- 旧证据迁移：task_gate.py 的证据目录 /tmp/hold 是易失的——建议用 `wf register-artifact` 收录入 store（见 SPEC.md 本次条目）。
