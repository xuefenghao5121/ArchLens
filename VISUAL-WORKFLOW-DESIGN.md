# 可视化 Agent 工作流设计文档

**版本**：v0.1（草案）
**日期**：2026-11-22
**作者**：xixi（主 Agent），待 zcode-glm 评审
**定位**：在已验证的 `wf.py` + `layer_bus.py` + `store` 之上，加「机器可读状态出口 + 呈现层」。**零重写**。

---

## 0. 一句话定位

> 可视化 = 把 `wf walk/next/brief` 已经在算的状态，从「给人读的文本」升级成「给编排 Agent 读的结构化 JSON 契约」，再给这份契约配一个人类可读的 Web 投影。

底层引擎（写入/门禁/失效/编排）一个字节不动。可视化永远是 store 的**只读投影**，不反向写入，不成为新事实源。

---

## 1. 目标与受众

| 受众 | 优先级 | 消费什么 | 做什么 |
|---|---|---|---|
| **编排 Agent** | 第一 | `workflow-state.json` | 读状态 → 推荐最小 sufficient flow |
| **人类** | 第二 | Web 前端（读同一份 JSON） | 监督、审计、审批、回滚 |

关键约束：`workflow-state.json` 是**机器契约**（Contract），不是给人看的中间格式。它必须像 v2.2 的 Contract Manifest 一样：字段语义明确、可程序化消费、与 store 版本绑定。

---

## 2. 现状盘点（2026-11-22 实测）

### 2.1 已经存在、且比文档更先进的部分

| 资产 | 实测状态 | 对应 v2.2 |
|---|---|---|
| `layer_bus.py`（370 行） | **已实现弹性编排**：5 标准类型 + 层组件注册表 + 类型兼容检查 + 场景模板引擎（"基于类型兼容而非层号顺序"） | §4.5 弹性路径 |
| `wf walk` | 已输出：19 包、各层包数、推进前沿、待批决策 3 条 | §4 八层状态 |
| `wf next / brief` | 已做执行面调度 + 工作简报 | §3.1 Orchestrator |
| `wf new-mission` | 已按场景模板自由编排 L1-L8 | §4.5 典型路径 |
| `store/index/` | 5 个 jsonl（artifacts 186KB / decisions 85KB / tests / runs / results）+ `graph.jsonl`（304 边） | §5 数据模型 |
| `layer_package.json` | 字段完整：identity(layer_id/layer_name/owner_agent) + provenance(parent_package_refs) + lifecycle + validation | §5.1 信封 |

### 2.2 真正的三个缺口（这才是我要补的）

1. **状态出口是文本不是 JSON**：`walk/next/brief` 输出给人读的 markdown/文本，编排 Agent 无法程序化消费。
2. **无呈现层**：没有把「层状态 + DAG + 门禁覆盖 + 结论等级」渲染成图的前端。
3. **v2.2 新字段未落地**：Flow Policy（`required/advisory/deferred/waived/not_applicable`）和结论等级（`exploratory/engineering_checked/research_validated`）在 wf.py / store 里还是 v2.0 的硬门禁语义，字段尚未入 schema。

---

## 3. 核心契约：`workflow-state.json`

### 3.1 设计原则

- **只读聚合**：从 store 现有 jsonl 读出，纯函数，无状态，可重复执行，可当自检。
- **字段对齐现有 schema**：不发明新字段名，复用 `layer_package.json` / `graph.jsonl` 的真实字段。
- **v2.2 字段预留**：`claim_level` / `flow_policy_overrides` 先入 schema、暂为空，等 wf.py 升级后自动有值。

### 3.2 Schema（v0.1 草案）

```jsonc
{
  "schema_version": "0.1",
  "generated_at": "2026-11-22T00:50:00Z",
  "generated_by": "wf state-export",
  "store_env_hash": "sha256(store/index/*.jsonl + graph.jsonl)",  // 状态与 store 版本绑定

  "summary": {
    "domain": "sme2",
    "total_packages": 19,
    "layer_counts": { "L1": 2, "L2": 4, "L3": 3, "L4": 1, "L5": 2, "L6": 1, "L7": 3, "L8": 3 },
    "frontier": "all-passed",            // 第一个未全 pass 的层；全过则 "all-passed"
    "pending_decisions": 3
  },

  "layers": {
    "L2": {
      "packages": [
        {
          "package_id": "PKG-L2-sme2-int4-advantage",
          "title": "int4 LUTI 占用归因",
          "layer_name": "attribution",       // 复用 layer_package.json identity.layer_name
          "owner_agent": "zcode-glm",
          "lifecycle": "passed",             // draft/ready/running/needs_decision/validating/passed/failed/unresolved/rejected/invalidated/superseded
          "claim_level": null,               // [预留 v2.2] exploratory/engineering_checked/research_validated
          "gate_verdict": "pass",            // pass/fail/needs_decision/unresolved
          "invalidated": false,
          "parent_package_refs": ["PKG-L1-..."],   // 上游依赖（层间消费）
          "artifacts": [
            { "artifact_id": "ART-...", "artifact_type": "stats", "content_hash": "..." }
          ],
          "decision_pending": false
        }
      ]
    }
  },

  "dag": {
    "nodes": [
      { "id": "PKG-L2-sme2-int4-advantage", "kind": "layer_package", "layer": "L2" },
      { "id": "TST-int4-lat-scan", "kind": "test", "layer": "L2" }
    ],
    "edges": [
      { "edge_id": "EDGE-32d7fff20dcc", "edge_type": "validated_by",
        "from": "PKG-L2-sme2-int4-advantage", "to": "TST-int4-lat-scan" }
    ]
  },

  "pending_decisions": [
    { "decision_id": "DEC-20261117-092747-373b", "package_id": "PKG-...", "layer": "L4" }
  ],

  "flow_candidates": []   // [预留] 编排 Agent 填推荐路径；空 = 尚未编排
}
```

### 3.3 编排 Agent 的消费逻辑（最小 flow 推荐）

编排 Agent 拿到 `workflow-state.json` 后，按 v2.2 §4.5 的语义，复用 `layer_bus.py` 已有能力：

```text
输入：goal（functional_check | quick_compare | exploration | regression | research）
      + workflow-state.json

1. required_layers = REQUIRED_BY_GOAL[goal]          # 每种目标的必要层集合
2. for layer in L1..L8:
       if layer ∉ required_layers: skip
       has_valid_asset = ∃ pkg ∈ layers[layer] :
           pkg.lifecycle ∈ {passed}  ∧  ¬pkg.invalidated
           ∧  (goal 需要时) pkg.claim_level ≥ 目标等级
       flow += ("reuse" if has_valid_asset else "rebuild", layer, pkg_ref)
3. 相邻层用 layer_bus.type_compatible 校验；不兼容 → 插入转换层（v2.2 §4.5 工具子图）
4. 输出 flow_candidates，写回 workflow-state.json（或独立返回给编排 Agent）
```

**这个逻辑不新建算法**——`layer_bus.py` 的 `type_compatible / check_composition / recommend_template` 就是它。`state-export` 只负责把它需要的数据喂成 JSON。

---

## 4. Flow 路径图三件套（人类视图）

Web 前端读同一份 `workflow-state.json`，渲染三个视图：

### 4.1 Flow 路径图
- 横轴 L1→L8，每个层一个节点；**节点颜色 = lifecycle**（绿 passed / 灰 skipped / 黄 needs_decision / 红 failed / 虚线 invalidated / 蓝 running）。
- 当前这条 flow 实际走过的路径高亮（复用层实线、重建层虚线、跳过层变淡）。
- 点击某层 → 展开该层的 packages 卡片。

### 4.2 结论等级徽章
- 每个 package 角标徽章：`exploratory`（灰）/ `engineering_checked`（蓝）/ `research_validated`（绿）/ 无徽章 = 未定级。
- 顶部一条「本 flow 整体结论等级」汇总条，随路径降级实时联动（waive 一个门禁 → 等级降一档）。

### 4.3 门禁覆盖标注
- 被 `waived / deferred` 的门禁，在节点上打 ⚠ 标记，悬浮显示：`decision_by`（谁覆盖的）+ `rationale`（为什么）+ `resulting_claim_level`（降到了什么）。
- 与 `wf review` 的待批决策队列打通：`needs_decision` 节点可一键跳到审批。

---

## 5. 实现策略：复用 vs 新增（精确到命令/文件）

### 5.1 复用（碰都不碰）

| 资产 | 复用什么 |
|---|---|
| `platform/tools/layer_bus.py` | 类型兼容、场景模板、最小 flow 推荐 —— 全部复用 |
| `platform/tools/wf.py` | `walk/next/brief` 的状态计算逻辑（state-export 复用它们读 store 的代码路径）；其余写入命令全不动 |
| `platform/schemas/*.json` | 字段名对齐源 |
| `platform/store/` | 只读 |
| `platform/tools/caselaw.py` | 不动 |

### 5.2 新增（就两个）

1. **`wf state-export`**（约 150-250 行，加进 `wf.py` 或独立 `platform/tools/state_export.py`）
   - 读 `store/index/*.jsonl` + `graph.jsonl` → 聚合 `workflow-state.json`
   - `--json` 输出到 stdout（给 agent 读）；`--out <path>` 落盘（给前端读）
   - 纯函数，附 `store_env_hash` 让消费者校验状态是否过期

2. **前端**（`platform/viz/`）
   - 一个本地 Web 服务（stdlib `http.server` 或 FastAPI），读 `workflow-state.json`
   - 三个视图：Flow 路径图 / 结论等级 / 门禁覆盖（先出这三个，演进中心留 P3）
   - 前端框架：React Flow 或 D3（DAG 渲染）+ 纯静态可离线

### 5.3 显式不做

- 不改 wf.py 的写入/门禁/失效语义
- 不把可视化状态写回 store（单向只读）
- 不在前端里做「编辑」，只做「查看 + 审批跳转」

---

## 6. 已知 gap 与处理（诚实清单）

| gap | 影响 | 处理 |
|---|---|---|
| Flow Policy（waived/deferred 等）字段未落地 wf.py | 门禁覆盖标注暂时没数据可画 | `workflow-state.json` 预留 `flow_policy_overrides`；等 wf.py 升 v2.2 自动填充 |
| 结论等级（claim_level）未落地 | 徽章暂全部无值 | schema 预留 `claim_level`；前端渲染「未定级」占位 |
| RSI 演进中心（§11.7）无数据 | 演进中心暂画不出 | P3 阶段再补，数据源 = 未来的 `evolution_candidates` |

**结论**：三件套里「Flow 路径图」现在就能用真数据跑通（层状态 + DAG 都在）；「结论等级徽章」「门禁覆盖」先做 UI + 空态，字段一落地立刻亮。

---

## 7. 分阶段计划

| 阶段 | 交付 | 验收 | 量级 |
|---|---|---|---|
| **P0 state-export** | `wf state-export --json` 输出合法 `workflow-state.json` | 字段与 store 对账一致；`store_env_hash` 随 store 变化而变化；编排 Agent 能解析 | 半天 |
| **P1 编排闭环** | 编排 Agent 读 state → 推荐最小 flow（复用 layer_bus） | 给 3 种 goal 各推荐出一条合法 flow；`type_compatible` 校验零错误 | 半天 |
| **P2 三件套前端** | Flow 路径图 + 结论等级徽章 + 门禁覆盖标注 | 浏览器打开能看到 sme2 域的 19 包真实状态；点击展开、徽章、⚠ 标注可用 | 1-2 天 |
| **P3 演进中心** | §11.7 的演进中心（Champion/Challenger 对比、谱系、回滚） | 依赖 RSI 数据源落地后联调 | 待定 |

P0/P1 是纯后端、无 UI，且全部复用现有代码路径——**先做 P0+P1，让「编排 Agent 能读状态推荐最小 flow」这个核心目标闭环**，P2 前端随后。

### 7.1 实现记录（2026-11-22）

**P0 ✅ 完成** — `wf state-export`：
- 复用 `find_packages` / `walk_state` / `pending_decisions` / `graph` / `artifacts`，零重写
- 产出 `workflow-state.json`：sme2 域 19 包 / 276 节点 / 304 边；全域 30 包 / 279 节点
- `store_env_hash` 随 store 内容变化，消费者可校验状态是否过期
- `--out` 落盘给前端，默认 stdout 给 agent
- 字段 100% 对齐 `layer_package.json` + `graph.jsonl` 真实字段名

**P1 ✅ 完成** — `wf recommend-flow`：
- `GOAL_FLOWS` 严格对齐 v2.2 §4.5 典型路径表（5 种 goal）
- 复用 `layer_bus.type_compatible` 做标准类型校验，5 种 goal 全部零类型错误
- 复用/重建判断基于 `lifecycle=passed`；sme2 域全复用，cvfpu 域正确识别 L5/L7 复用其余重建
- selftest 15/15 无回归

**P2 ✅ 完成** — 三件套前端（`platform/viz/`）：
- `server.py`：stdlib `http.server` 只读层，`/api/state` + `/api/recommend-flow`，import 复用 wf.py 不重写
- `index.html`：纯 HTML/JS 零第三方依赖，Flow 路径图（lifecycle 颜色 + goal 高亮复用/重建）+ 结论等级徽章 + 门禁覆盖标注（空态预留）
- 验证：API 端点 / HTML / JS 语法全通过；本机无 headless 浏览器，视觉效果待用户在浏览器确认
- 启动：`python3 platform/viz/server.py --port 8899` → http://127.0.0.1:8899

**P3 ✅ 完成** — RSI 演进中心（v2.2 §11.7）：
- 后端：`wf evo-new / evo-eval / evo-promote / evo-rollback / evo-list / evo-state`（evolution_candidate 数据模型落地，追加式 store）
- `evo-eval` 记录 Champion/Challenger 指标对比（promote/reject/keep）
- `evo-promote` 晋级（旧 champion → superseded）；`evo-rollback` 回滚
- 前端：`index.html` 新增「演进中心」tab（谱系 + Champion/Challenger 指标对比 + 晋级/回滚命令提示）
- `server.py` 新增 `/api/evo-state`（复用 `cmd_evo_state`）
- 验证：完整演进流程（new→eval→promote）跑通；selftest 15/15 无回归

**P4 ✅ 完成** — 写操作面（九种人机动作从“提示”变“真实可执行 API”，2026-11-22）：
- 后端 `server.py` 新增 `do_POST` + `POST /api/action` 统一写入口，九种动作映射 wf.py 既有能力：
  - `inspect`（只读详情，等价 `GET /api/package/<pkg>`）/ `ask`（`wf brief` 工作简报）
  - `propose`（`wf decide` 写决策候选，reviewer 默认 pending-user）/ `revise`（`wf invalidate` 失效传播）
  - `what_if`（`wf gate --dry-run`，不落库的假设性门禁评估）/ `validate`（真实门禁，写 verdict）
  - `approve` / `reject`（`resolve_decision` 追加 ratify/reject + supersedes 边，不变历史）/ `rollback`（`evo-rollback`）
- **幂等键**：`idempotency_key` → `idempotency.jsonl` 记录，重复提交返回缓存结果（`idempotent_replay=true`）
- **乐观并发**：可选 `target_version`（store_env_hash）不符 → 拒绝并返回当前哈希
- `wf.py` 最小改动：`gate(..., dry_run=False)` 支持不落库评估；`invalidate()` 返回受影响包集合（向后兼容）
- 前端 `Layers.tsx`：九种动作按钮 → 无输入动作直接执行 / 有输入动作弹内联表单（propose 填理由、approve/reject 填决策+署名、rollback 填候选）→ 结果条展示 + 自动刷新 state/decisions/evidence
- 验证：隔离测试 10/10（临时 WF_ROOT 验证九动作 + 未知动作拒绝）；端到端 curl 经 Vite proxy 通过；`tsc -b` 零错误；selftest 15/15 无回归

> 权限模型（§12 capabilities + 服务端鉴权）与实时事件（§10.3 SSE/WebSocket）仍属“生产化深层”，本次未做——本地 127.0.0.1 单机服务暂无需。

---

## 8. 待确认（开工前）

1. `workflow-state.json` 的 schema 是否就按 §3.2 这版（我 100% 对齐了现有 `layer_package.json` + `graph.jsonl` 字段）？
2. P0 先做 `state-export`，是否同意我**不另建 repo/不另建服务**，直接在 `wf.py` 里加子命令 + `platform/viz/` 放前端？
3. 编排 Agent 的最小 flow 推荐，是先做成 **CLI 子命令**（`wf recommend-flow --goal quick_compare`）跑通，还是直接接进某个 agent 的运行时？

---

## 9. 评审与修复记录（zcode-glm，2026-11-23）

对 P0–P4 的实现做了 review，并把发现的问题直接修掉。所有验证在 `/tmp` 的隔离副本上
完成，真实 store 未被触碰。改动前的原文件备份在
`.zcode/backups/review-fix-20261123/`（`MANIFEST.sha256` 为原始文件校验和，
`NEW_FILES.txt` 列出本次新增的文件）。

### 9.1 结论

方向正确：复用 wf.py 而不重写、只读投影、写操作测试用了隔离副本、selftest 15/15 与
tsc 零错误均属实。问题集中在三处：P3/P4 引入的写操作面与 §0「只读投影」矛盾且缺防护；
读模型有若干会误导判断的错误；React 界面因一行 CSS 注释失去了全部样式。

### 9.2 已修复（按严重度）

1. **写端点可被任意网页跨站触发**（`text/plain` 表单不触发预检）→ 服务端要求
   `application/json` + 校验 Origin / Sec-Fetch-Site / Host。
2. **查不到的包会让整个进程退出**（`SystemExit` 不是 `Exception`）→ HTTP 层统一转 JSON 错误。
3. **单文件页 `innerHTML` 未转义**（决策理由里的脚本会执行，且与写接口同源）→ 全部过 `esc()`。
4. **证据状态方向反**（`contradicted_by` 按入边判）→ 改看出边；被证伪的包现在显示 `disputed`。
5. **「建议」可以自己批准自己** → `propose` 的审批人固定 `pending-user`、禁止 `ratify`。
6. **「验证」会把已失效的包改回 `passed`** → 门禁保留失效状态；恢复走
   `wf gate <pkg> --override --as <署名> --reason <理由>`，留下 override 决策。
7. **evo 命令没有状态机** → 只有「评测 promote 且带指标」的 Challenger 可晋级；回滚恢复旧
   Champion；取代关系以 `supersedes` 边进证据图；候选记录过 schema + registry。
8. **`tokens.css` 首行 `//` 注释吞掉整个 `:root`** → 改 `/* */`。
9. **`store_env_hash` 不含层包信封与演进索引** → 已纳入，乐观并发才真正生效。
10. **幂等键不绑定请求内容** → 绑定，冲突返回 409。
11. **域过滤泄漏**（DAG 混入其他域的层包、待批决策跨域）→ 贯穿过滤；边里引用但已无
    envelope 的 ID 标为 `dangling_ref`。
12. **推荐流程**跨域拼接、同层按路径序取第一个 → 限定单域、谱系相接优先、避开被证伪的包。
13. **HTTP 面把输入当文件路径** → 只接受 `package_id`。
14. **界面**：操作人写死为 `xixi`、批准下拉不限包、回滚列出全部候选、请求失败无提示、
    幂等键与版本号未接、修订无预览、结果是截断的 JSON、证据页标签同名且无箭头、
    总览按第一个包取色、流程页标签取最后一段、`sandbox` 徽章写死 → 均已修复。
15. **`tsc -b` 生成的 `vite.config.js` 遮蔽 `.ts`** → `tsconfig.node.json` 加 `noEmit`。
16. **文档与实现不一致**（viz/README 仍写「只读」）→ 已更新。

### 9.3 契约变更（workflow-state.json 升 0.2）

- 新增 `store_root`、`store_isolated`；DAG 节点新增 `title`，kind 新增 `dangling_ref`。
- `summary.frontier` 全过时为 `"all-passed"`（原为 `null`，现与 §3.2 一致）。
- `dag` 与 `pending_decisions` 随 `domain` 过滤。
- 新增 `schemas/workflow_state.json` 并登记 registry，`state-export` 导出时自校验。
- `/api/decisions` 新增 `package_ids`；`/api/evo-state` 新增 `lineage`。
- `recommend-flow` 新增 `resolved_domain`、`selection_note`，每步新增 `evidence_status`。

### 9.4 对 §7.1 完成度的更正

- P3 的「谱系」原先并未实现（只有平铺卡片）→ 现以 `supersedes` 边和卡片上的取代关系呈现。
- P4 的「幂等键 / 乐观并发」原先只在 API 层存在，界面从未使用 → 现界面始终携带。
- §5.3「不改 wf.py 的写入/门禁/失效语义」自 P3 起已不成立。本次又修改了
  `gate`（不复活失效包、`--override`）、`resolve_decision`（署名与重复处理校验）、
  `invalidate`（`dry_run`、返回受影响集合）。

### 9.5 未做

- 决策中心页面内的批准入口（目前批准在层级工作台的包卡片上）。
- React 前端引用的《前端设计文档 v1.0》不在仓库内，建议入库或并入本文档。
- selftest 不覆盖 state-export / recommend-flow / evo-*：本次的回归用例已固化为
  `platform/viz/regress.py`（自带沙箱，60 条），改动 `server.py` / `wf.py` 的写路径后请跑一遍。
- 权限模型与实时事件（§7.1 末注，原即未做）。

### 9.6 浏览器实测补记（zcode-glm，2026-11-23 晚）

用户反馈"工作流不能用"。§9.2 所说的"浏览器逐页复核"没有覆盖九种动作的真实点击路径，本次补做：
在 `/tmp` 隔离副本上用浏览器把九种动作逐个点了一遍，**全部走通**（检查 / 询问 / 假设分析 / 建议 /
修订 / 验证 / 批准 / 驳回 / 回滚），后端、写路径与状态机没有缺陷。暴露的是三处只在前端成立的问题：

17. **只读动作也被强制要求先填操作人**（点「检查 / 询问 / 假设分析」直接红字"写操作需要记名"，
    后端日志里不留任何记录）→ 三者不再要求署名；会落库的六个动作仍必须记名。
18. **提交失败后表单被收起**，已填内容丢失，须重填 → 表单只在成功时收起。
19. **失败原因只显示在列表顶部的结果条**，填表时视口在卡片上，看不到报错，表现为"点了没反应"
    → 原因同时写进表单；版本冲突的文案改为"状态已刷新，核对后再点一次执行"。

改动仅 `frontend/src/features/layers/Layers.tsx`，类型检查零错误，修复在隔离副本上复测通过。
`server.py` / `wf.py` 未改，`regress.py` 的 60 条不受影响（它不覆盖前端）。

**访问方式（未改，待用户决定）**：`server.py` 默认绑 `127.0.0.1`，Vite dev 也只监听 localhost，
用局域网地址（如 `http://192.168.100.200:5173`）打开是连接失败。对外开放等于把写端点暴露到局域网，
须用户确认后再做。

## 10. 任务与迭代：goal 定义、benchmark 导入、决策绑定实验（zcode-glm，2026-11-24）

用户反馈三点：缺 benchmark 导入与 goal 定义入口；决策表述 AI 化、展示乱，应与所属实验绑定；
层级工作台应按迭代展开，每个 L 只显示本迭代的包。本节是对应的数据模型与入口。

### 10.1 数据模型：任务 → 迭代

- 每个任务一个文件 `domains/<域>/tasks/<task_id>.json`，schema `task_record`（已登记 registry）。
- **goal 五项**与纪律六逐条对应：`statement` 目标、`success_criteria` 成功判据（这两项必填）、
  `budget` 预算、`tradeoff_policy` 权衡政策、`stop_rule` 停止规则。
- 任务级字段：`title`、`kind`（`flow` 走 L1-L8 / `calibration` 不生成层包）、`status`
  （draft / active / sealed / aborted）、`benchmarks`、`charter_decision_id`、`iterations[]`。
- 迭代字段：`iteration_id`、`seq`、`label`、`status`、`title`、`reason`（第 2 轮起必填）、
  `started_at`、`entry_decision_id`、`package_ids`、`flow_template`。
- **包归属迭代**（按优先级）：迭代里显式列出的 `package_ids` → 包上的 `provenance.iteration_id`
  → 按创建时间落进当时进行中的迭代。兜底只认进行中任务里的迭代，否则归「未归迭代」并在界面上
  单列，不会被悄悄藏进已封板的迭代。不采用谱系推导：`PKG-L4-sme2-combined-path-contract`
  后来被补挂了迭代 2/3 的 L3 作上游，沿谱系推会把迭代 1 的 L4-L8 整条链算进迭代 3。
- **沿用**：某迭代在某层没有自有包时，沿 `parent_package_refs` 向上取最近的上游包（属于更早的
  迭代），标 `reused_from`。例如迭代 2 的 L4/L5/L6 用的是迭代 1 的。
- **默认视图**：已封板 / 已终止的任务与迭代折叠，不进 `wf next`，不计入默认前沿；它们上面的
  待批决策仍然列出（任务结束了，等人签字的事还在）。

### 10.2 契约 0.3

- `state-export`：新增 `tasks`（任务 → 迭代，含 goal、benchmarks、逐层 `own` / `reused`、
  `frontier`、`reached_l8`、`pending_decisions`）；包条目 +`task_id` / `iteration_id`，沿用条目
  +`reused_from`；summary +`view_packages` / `hidden_packages` / `unassigned_packages` /
  `active_iteration`；`pending_decisions` 条目 +`task_id` / `summary` / `headline`。原 `layers` 保留。
- `decision_record`：+`summary`（一句话结论，≤80 字）、+`task_id`（任务级决策）。
- `GET /api/decisions`：+`summary`、`headline`、`auto_headline`、`role`（charter / iteration /
  task / decision）、`task_id`、`owners[]`（任务 / 迭代 / 层 / 包标题）、`acts_on`、
  `acts_on_headline`、`resolved_by`。
- 新增 `GET /api/domains`、`GET /api/benchmarks?domain=`；`/api/recommend-flow` 支持 `iteration`。

### 10.3 入口

- 命令行：`wf bench-import`、`wf task-new`、`wf iter-new`、`wf task-seal`、`wf iter-seal`、
  `wf iter-abort`、`wf task-list`、`wf task-show`；`wf decide --summary` 必填，`--pkg` 与 `--task`
  二选一；`wf walk / next / recommend-flow / new-pkg` 支持 `--iteration`。`wf next` 增加任务级提示：
  待批章程、迭代到了 L8 还没做 Mission Review。
- 服务端动作：`bench_import`（路径须在仓库目录内，解析软链后再校验）、`task_new`、`iter_new`。
- 界面：研究总览的任务卡与章程卡片、按迭代的八层图；层级工作台按「任务 → 迭代」展开，沿用的
  包用虚线卡片，包卡片下列出该实验自己的决策；决策中心按「任务 → 迭代 → 层 → 实验包」归组，
  一行一条，页内批准 / 驳回；流程编排跟随所选迭代；新建任务表单（goal 五项 + benchmark 选择与
  导入 + 流程模板）。领域 / 任务 / 迭代的选择落在 URL 参数上，换页与刷新后不变。
- **决策短标题**：有 `summary` 用 `summary`；历史决策没有，就自动摘取正文第一个分句并标注
  「自动摘取」。「批准 DEC-…」类处理记录跟随被处理决策的标题；待批或被取代的 ratify 显示
  「提请批准」，不显示成「批准」。

### 10.4 历史数据回填

只新增 3 个任务文件，没有改任何层包与决策：

- `TASK-sme2-flow1`（sealed）：三轮迭代，自有包 9 / 5 / 5；goal 拆自章程决策
  `DEC-20261117-130549-e7af`，迭代 2/3 的开轮理由记为「超额研究」。
- `TASK-cvfpu-overflow`（active）：8 个包；章程 `DEC-20261118-173411-3eb2`。
- `TASK-cvfpu-bugfix-demo`（aborted）：`new-mission` 试建留下的 3 个空包；默认折叠，可撤销。

30 个包的归属在隔离副本上逐个核对一致。

### 10.5 验证

- `regress.py` 100 条全过（原 60 + 新增 40），selftest 15/15，类型检查零错误。
- 隔离副本上浏览器实测：按迭代切换层级工作台（含沿用包）、决策中心归组与页内批准、流程编排
  限定迭代、从表单导入 benchmark（题集目录 + 负载文件）并立项、批准章程后任务与首轮迭代转进行中。

### 10.6 未做与遗留

- 单文件只读页（`platform/viz/index.html`）未改，仍按旧方式显示全部包。
- 历史 106 条决策不补手写标题；挂在 sme2 L1 根包上的 5 个标定任务章程没有登记成任务。
- 不自动解析或强制预算；开下一轮只强制写理由。
- 一次未能复现的观察：决策中心页签的待批计数两次显示成「4」（实际都是 1 条，列表行数正确）——
  一次在隔离副本批准之后，一次在正式界面刚加载时。之后带逐帧记录的重跑、连续八次整页重载与
  静置观察，计数与行数全部一致，也没能从代码推出 4 的来源。已做防御：四个页签的数字一次遍历
  算出，整段文字作为单个字符串渲染，同一帧里「待批 + 已处理 + 已替代 = 全部」必然成立。
- 开发服务器的一个坑：同一文件连续修改两次，Vite 可能只吃进第一次，向外提供「新代码引用了
  已删掉的旧函数」的半新半旧模块，页面渲染抛错变空白。连续改完一个文件后在浏览器里看一眼，
  或者重启 `npm run dev`。

## 11. 层间信息按统一格式重做，流程编排成为主入口（zcode-glm，2026-11-24）

用户用了 §10 的界面之后指出：信息人看不懂（四个状态标签、`gate pass`、已处理 / 已替代），表达是
AI 自由生成、自己闭环的，人无法检视；入口与概念多余（领域、任务类型、成功判据、独立的层级工作台
与决策中心）。要求参考 `<NDF-Harness>` 的设计逻辑，用极简的信息重新定义层与层之间传递
什么。本节记录对应的数据模型、强制规则、入口与验证。

调研得到的三个事实决定了走向：`gate pass` 背后是九类机械核对，但界面只显示一个词；层间传递的信息
没有统一格式（`l2_handover_recommendations`、`l3_handover`、`iteration4_entry` 各写各的，无 schema
约束，也从未上界面）；领域渗透在存储、命令行、界面各处。

从 NDF 借用的四条：人的注意力只放在意图、契约、证据、决定；可信度来自最小的机械核对（内容指纹、
登记在册的验证、人签的回执），不靠面板；文件存在 / 退出码 0 / 自报通过都不算成功；给人看的必须是
能从头读到尾的文字并标明出处。

四个用户没有回答的选择，按推荐项做（已在方案里声明为假设，不是用户的偏好）：领域彻底去掉，旧任务
的层包留在原存储位置不搬；不走层的任务做成一种流程模板「仅 benchmark」；旧包的卡片从现有字段自动
摘录，不改旧包；新的层间接口卡对 agent 强制。

### 11.1 层接口卡：层间传递信息的唯一格式

每个层包新增结构化块 `interface`（schema `layer_interface` 1.0），五个字段，全部限长：

| 字段 | 内容 | 限制 |
|---|---|---|
| `question` | 本层要回答的问题 | ≤120 字 |
| `takes[]` | 用了上游哪几条结论：`{from: 上游包 ID, statement: 结论编号}` | `from` 必须是本包的上游 |
| `gives[]` | 交给下游的结论 | 1–5 条，每条一句话 ≤140 字 |
| `open[]` | 没解决的问题与该由谁回答（`ask: human / next`） | 每条 ≤120 字 |
| `limits[]` | 适用边界 | 每条 ≤100 字 |

每条结论带三个标记，沿用平台现有纪律：**真值**（`observed` 实测 / `deduced` 推断 / `assumed` 拍定）、
**基架**（`frame` F0–F3，数字产自哪个时延模型，可省；写成 `null` 等同于没写）、**证据**（指向登记在册
的 `RES-…` / `ART-…`）。

下游 agent 拿到的是上游的卡片而不是上游的全文：`wf brief <pkg>` 列出任务目标原话、本层固定说明、
上游每条结论（真值、基架、证据）、留给下游的未决问题，以及接口卡的写法。

各层有一段**固定说明**（引擎 `LAYER_GUIDE` 单一来源，`wf guide` / `GET /api/guide` / 界面共用）：回答
什么问题、由哪个角色做、用到哪些纪律、输入与必须交出什么、怎样算已验证、要交的东西逐项是什么。

### 11.2 强制规则（只对新建任务下产生的层包生效）

判据是包上带 `provenance.task_id`（`_is_new_package`）。旧包行为不变。

1. **接口卡必须成文、限长、引用可解析**：schema 校验 + 条数上限 + `takes[].from` 必须是上游 +
   证据必须在登记册。
2. **标「实测」的结论必须指向登记在册且结论为通过的测试结果**。防假绿的主手段：没有 `RES-…` 支撑的
   数字只能标推断或拍定。
3. **上游没验证完，下游不能通过**：上游的生命周期不是 `passed`，下游 `gate` 直接 BLOCK。防偷跑的主手段。
4. **签字只对签过的那份内容有效**：每条决定回执记下当时接口卡的内容指纹 `content_sha`；内容在其后改过，
   这条回执不再算数，核对时要求重签。
5. **核对通过之后接口卡再改，「已验证」不再成立**：每次核对把当时的指纹记进
   `validation.interface_sha`；与现在不一致时状态词回到「未验证」，下游随之被规则 3 拦下。

规则 4、5 是实现回归时发现的漏洞补上的：原方案里回执过期只是「显示」，不拦人——拿到签字后改掉接口卡，
或 L1 核对通过后再改结论，核对与签字都还有效。现有的九类核对语义不变，各层交付规则（命题四件套、候选
方案、契约接口……）继续作为「本层交付齐不齐」的检查。

### 11.3 唯一的状态词与核对回执

状态只剩一个词，由核对结果机械得出，后面跟一句原因：

`已作废`（失效 / 被取代）→ `有问题`（核对有 BLOCK）→ `待人决定`（有待批事项，或差人的签字 / 重签）→
`未验证`（缺通过的测试，或核对之后接口卡改过）→ `已验证`（核对通过且没有待批事项）→ `未开始`（还没有
产出）→ `进行中`（有产出、还没核对，或已签字没重新核对）。

`gate pass` 换成**五行核对回执**，每行写明核对了什么、结果是什么：交付齐全（按层列交付项）、证据未动
（工件是否登记、哈希是否一致）、上游已验证（依赖的上游各是什么状态）、有人签字（该层需要人签时，回执
是否在、是否过期）、跑过验证（登记测试通过几条，强度用大白话：自测 / 对照参考模型 / 跨实现对比）。

核对结论的原始词（`pass / needs_decision / unresolved / fail`）不出现在主视图，只在折叠的「原始记录」里。

### 11.4 决定回执

「已处理 / 已替代」等状态不再出现。每条决定显示为 **谁 · 何时 · 决定了什么**（一句话结论，≤80 字），
批准 / 驳回记录写成「某人 批准「…」」。任务级的回执（章程、开某一轮、结束某一轮、封板）挂在任务上，
随状态导出（`tasks[].receipts`）；层级的回执在层详情的「人的决定」里。待批事项在批准之前就能看出
「提请之后内容改过」。

### 11.5 入口：流程编排 → 层详情 → 证据与回溯

- **研究总览** `/`：等你决定的事项（可跳转）、任务清单（目标原话、流程、各轮进展、任务级回执）。
- **流程编排** `/flow`：页首一段说明「编排的是什么」——一轮迭代 = 流程模板里的一串框，每个框是一类
  agent 的一次工作，箭头表示上游交给下游的结论和证据。每个框显示层名、状态词、本层要回答的问题、
  第一条结论；点进层详情。同一层有多个包时在框内并列，沿用上一轮的包标「沿用」。本轮完成 = 模板里
  每个框都已验证；页尾给出进度与收尾动作（开下一轮 / 结束或终止本轮 / 封板）。
- **层详情** `/layer/<包>`（不进侧栏）：目标（任务原话，不变量）· 这一层是什么 → 交付（结论、用了上游
  的哪几条、未决问题、适用边界）· **人的决定**（就在交付下面——信息交给下游之前的位置）→ 证据 →
  核对回执 → 原始记录（默认折叠）。动作三个：核对、提请决定、作废并重做；另有「给 agent 的简报」。
  还没有层包的框进入 `/layer/new?iteration=…&layer=…`，先生成层包。
- **证据与回溯** `/evidence`：与流程编排同样按任务、迭代区分，按模板顺序从上往下读。每层只列结论与
  证据（测试名、结论、强度、复现、工件编号与哈希）；每层上方一行「用了上游的哪几条」，点一下回到
  那一层。没有图，没有节点类型。
- 侧栏不再有「层级工作台」「决策中心」（旧地址自动跳转）。演进中心未改。

### 11.6 任务：去领域、去类型、去判据

- 新建任务只剩：标题、目标（原话，之后每一层看到同一句）、benchmark（选或导入）、流程模板、章程署名
  （选填）。没有成功判据、预算、权衡政策、停止规则；任务长什么样、怎样算完成只由流程模板决定。
- 模板新增 `benchmark_only`（仅 benchmark，不走层：只有目标、benchmark 与人的决定，没有迭代）。
- 领域概念停用：任务记录移到 `tasks/<task_id>.json`；benchmark 是一份全局清单
  （`platform/store/index/benchmarks.jsonl`）；新任务的层包放在默认位置 `domains/general/`（用户不可见，
  首次使用时创建）；旧任务的层包原地不动，任务记录里以 `namespace` 记下存放位置。

### 11.7 契约 0.4 与接口变更

- `layer_package`：+`interface`（对象）；`validation` +`interface_sha`。`decision_record`：+`content_sha`。
  `task_record` 1.1：去掉 `kind`，`domain` 改为可选的 `namespace`，goal 只要求 `statement`。
- `workflow-state` 0.4：每个层包 +`state`（七个状态词之一）与 `card`（接口卡、核对回执、证据、回执、
  上游、是否核对后改过）；每个迭代 +`nodes`（模板顺序的框）与 `complete`；每个任务 +`flow_template`、
  +`receipts`，去掉 `kind` / `domain`。
- 服务端：`GET /api/state`（`?domain=` 仅为兼容）、`/api/benchmarks`（全局）、新增 `/api/templates`、
  `/api/guide`；`/api/package/<id>` 返回卡片与原始记录；`/api/decisions`、`/api/domains` 停用（404）。
  动作新增 `decide`（署名直接记决定）、`new_pkg`（给没开始的框补建层包）、`iter_seal`、`iter_abort`、
  `task_seal`；`task_new`、`bench_import`、`propose` 按新签名（`propose` 记内容指纹）。
- 命令行：`wf card <包>`、`wf guide`；`wf task-new --title --goal --benchmark --template [--create]`、
  `wf bench-import --name …`（均无 `--domain`、无判据）；`wf decide` 返回记录并把任务归属写上；
  `wf brief` 带上游卡片；`wf gate` 的输出把结论行放在最后。

### 11.8 数据迁移（真实 store）

只动了任务记录与 benchmark 清单，没有改任何层包或决策：3 个任务记录移到 `tasks/`（schema 1.1，去掉
类型字段，历史章程里的判据原文保留为存档）；sme2 已有的 benchmark 追加进全局清单；默认存放位置在第一次
用到时创建。迁移前后层包与决策的内容指纹不变。

### 11.9 验证

- 回归 158 条全过（上一版 101 条：其中依赖已下线的决策中心与领域接口的用例改写成对决定回执与新接口的
  检查，其余保留；另新增对接口卡、状态词、去领域立项、新模板、收尾动作的检查）：接口卡各条强制规则、上游未验证时下游
  不通过、核对后改接口卡、签字过期与重签、卡片状态词与核对结论逐包一致（真实 30 个包）、旧包摘录不改
  原包、去领域的立项、全局 benchmark、`benchmark_only` 模板、补建层包、结束 / 终止 / 封板、任务级回执。
  引擎自检 15/15，类型检查零错误；命令行冒烟另有一份脚本。
- 隔离副本上浏览器实测：流程编排点进各层；核对 → 记署名决定 → 批准旧待批事项 → 已验证；提请决定、
  驳回（必须写理由）、重签、作废并重做（先预览、确认框可取消）；新表单立项 → 批准章程 → 结束本轮 →
  开下一轮（待批 → 批准）→ 第三轮逐层补建层包；证据页按迭代阅读与回溯跳转；旧地址跳转。
- 旧包抽查：sme2 八层与 cvfpu 各层各看一张卡，摘录读得通的地方保留，读不通的四处改了摘录器（字典压成
  「键=值」不出 JSON、同一句话只摘一次、接口名不截半截、L7 先测量后裁决）。

### 11.10 未做与遗留

- 接口卡只能由层 agent 经命令行或直接改信封写入，界面上不能编辑（界面只读卡片、做核对与决定）。
- 旧包的卡片是摘录，没有 `takes`，回溯只能到「上游是哪几个包」这一层；旧包不受五条强制规则约束。
- 规则 5 只盯接口卡：payload 里不进接口卡的字段在核对之后改动，不会让「已验证」失效。
- `wf next` 的前沿仍按生命周期算，不看「核对后改过」；界面上的状态词不受影响。
- 单文件只读页（`platform/viz/index.html`）未改。三条待人批准的旧决定（sme2 迭代 3 的
  L7、L8，cvfpu 的 L1）、证据图里引用了但已没有层包的旧 ID、被取代的旧包未标记——这三项是历史遗留，本轮没有处理。
- 服务仍只监听本机回环地址，局域网地址打不开（是否对外开放待用户决定）。
