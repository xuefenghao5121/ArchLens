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

## 12. 按层编排 agent：每层一个定义、确定的调度、看得见的上下文边界（zcode-glm，2026-11-25）

用户提出「接下来要呈现出详细的可编排性」，并给了两个方案：方案 1 是每层一个 agent（一份独属于这层职责的
`agent.md`，加上它要调用的 skills 和 tools 包，使用者看得见）；方案 2 是一个 agent 用 skills 把各层连起来、
共用上下文。用户自己点出了区别：方案 1 的难点在层与层之间的记忆与上下文管理，方案 2 是大家共用上下文。

先做了分析（结论见 §12.1），用户确认理解后批准执行。方案全文在 `handoff/orchestration-plan-20261125.md`
（条款 ID `ORCH-…`），交接文档在同目录。本节记录实施结果。

### 12.1 为什么走方案 1，以及「可编排性」指什么

**目的不是让流程更自动，而是让上下文的边界和契约的边界重合。** 平台的价值不在于让 agent 多做步骤，而在于
每次转换都留下可检查的工件、可证伪的实验、可追责的决策（设计文档 v2 §13）；它成立靠显式契约、独立验证、
结论边界三件事。前两轮把层间传的**信息**做成了显式契约（接口卡 + 五条核对规则），但「谁在做」还是隐式的：
层包上写了 owner，却没有一份东西定义这个 owner 的职责、工具和边界。契约到层为止，上下文却贯穿全程——30 个
层包里 26 个出自同一个 agent。同一个上下文写判据又做裁决，「独立验证」与「结论边界」在实践中落空，11-13
那次判据事故就是这么来的。

**依据。**【实测】设计文档 v2 的 §2.6（不共享隐式上下文，只共享显式契约）、§3.1（控制面不参与微架构判断）、
§3.3（独立验证面防止生成者与验证者共用同一误解）；NDF-Harness 自身是分角色、每跳重置会话、只认磁盘回执的
形态；sme2 迭代 1 的 9 个层包合计 284 KB，三轮 19 个包 554 KB。【推断】「共用上下文装不下」与「方案 1 更
高效」是从这些规模与事故推出来的，没有两种做法的对比数据——所以加了每层的执行记录，让这个问题以后能用数据
回答。

**「可编排性」的含义**：编排是明确的、看得见的、可核对的；其次是可调整（改定义文件和流程模板就能调，不用
重新 prompt）。不包括在界面上直接给某一层换 agent 或换工具。

### 12.2 九个定义：每层一个，加一个调度 agent

`platform/agents/` 下每层一个目录，各一份 `agent.md`：

```
platform/agents/
├── L1_observation/agent.md … L8_analysis/agent.md   每层一个
├── orchestrator/agent.md                             调度 agent
├── toolpacks.json                                    工具包清单
└── {profiling,design,integration}.md                 角色包（能力分组，被层定义引用）
```

frontmatter 是机器读的部分，字段与约束见 `platform/schemas/layer_agent.json`（已登记 registry）：

| 字段 | 内容 |
|---|---|
| `layer` | L1–L8 或 orchestrator |
| `name` | 人读的名字（如「观测 agent」） |
| `host_name` | 宿主里的名字（`sim-layer-l1`），投影成 `.zcode/agents/<host_name>.md` 的文件名 |
| `role` | profiling / design / integration / orchestrator，必须与引擎的 `LAYER_AGENT` 一致 |
| `skills` | 要用的 skill，必须是 `.agents/skills/<名>/SKILL.md` 存在的 |
| `tools` | 工具包名，必须出现在 `toolpacks.json` 里 |
| `writes` | 可写范围（层包目录、与这一层有关的工程目录） |
| `must_not` | 不许做的事（不替人签字、不改上游、不隐藏失败等） |

正文是人读的职责、输入、工作步骤、完成标准与纪律要点，内容从 `LAYER_GUIDE`、`LAYER_RULES`、角色包与
`domains/sme2/environment.md` 归纳，不新编。工具包只收已经在环境速查、判例库、skills 里出现过的东西：
`wf:core`、`wf:read`、`wf:dispatch`、`caselaw`、`sim:gem5-run`、`sim:gem5-build`、`kernel:toolchain`、
`rtl:pycircuit-verilator`（领域相关的命令指向 `domains/<d>/environment.md`，不在清单里硬写）。

**加载与校验**：引擎每次用到定义时读九份文件与工具包清单，按 schema 校验，并核对「skills 在磁盘上存在、
tools 在清单里存在、role 与 `LAYER_AGENT` 一致、host_name 不重复」。任何一条不满足直接报错，不静默降级。
按文件修改时间缓存，改完定义下一次调用就生效。

顺带补上一个旧缺口：`rtl-generation-discipline` 与判例库 `caselaw` 此前没挂到 L5，现在挂上了。

### 12.3 调度：规则放在确定的地方，调度 agent 很薄

顺序由流程模板定，完成由核对规则定，派发按一份写下来的规程
（`platform/methodology/orchestration.md`，投影成 skill `flow-orchestration` 与手册镜像）。调度 agent
只做四类事：定位前沿（`wf next`）、造包（`wf brief --out`）、派发、收工核对（`wf gate` + `wf run-log`），
再加把要人决定的事端出来。它不做任何一层的层内工作，也不对微架构问题下判断——对应设计文档 §3.1「控制面
负责流程一致性，不参与具体微架构判断」。

这条与 NDF 的对应：Command 造 pack、等人审、派发 ↔ 造开工简报、等人签字、派给层 agent；每跳重置会话 ↔
每层一个 agent 只拿简报；成功只认磁盘回执 ↔ 只认 `wf gate` 写进信封的核对结论。

### 12.4 上下文留痕：三层写入同一个信封

方案 1 的弱点是接口卡有损、边界处会丢上下文。对策不是让 agent 自己记，而是把「拿到什么、额外读了什么、
谁跑了多久」都记在层包信封的 `provenance` 里：

```bash
wf brief <包> --out --by <署名>      # 简报写成 <包目录>/brief.md；记 at / by / sha256 / upstream
wf show <上游包> --for <本包> --by <署名>   # 接口卡之外另读上游；记一条 context_pulls
wf run-log --pkg <包> --agent <谁> [--model 名] [--duration 秒]   # 记一条执行
```

卡片与界面据此回答三个问题：这一层开工时拿到的是什么（简报的指纹）、它额外读了什么（回读记录）、上次是谁
用什么模型跑了多久（执行记录）。简报的指纹能对上磁盘上的 `brief.md`，改过就对不上。

### 12.5 边界：层 agent 不能替人签字

层 agent 开工前在简报首行被要求 `export WF_ACTOR=layer:<层>`。在这个变量下，`wf` 拒绝 `approve`、
`reject`、`gate --override`、`invalidate`，并把 `decide` 的 reviewer 强制为 `pending-user`（层 agent 只能
准备，不能签）。

这是防误操作的护栏，**不是硬隔离**：直接改文件或走 HTTP 绕得过去。文档与定义里都照实写。宿主侧另有一层
可以真正生效的限制：投影出来的 `.zcode/agents/<host_name>.md` 在 frontmatter 里写了 `tools:`——层 agent 给
读写与 Bash，调度 agent 只给 `Read, Bash, Glob, Grep`（没有写文件的能力）。宿主对项目级定义不允许设置
`permissionMode`，所以权限模式没有用。

### 12.6 界面：编排摊开给人看

- **流程编排页**：目标块下方新增「每一层由谁做、用什么」——按模板顺序一行一层，列出 agent（含宿主名）、
  skills、tools、怎样算做完（人签字 / 需测试）。每个框的标题旁标出这一层的 agent 名。只列这个模板涉及的层。
- **层详情**：左栏在「这一层是什么」之后新增「这一层的 agent」：名字、宿主名、所属角色、skills 与 tools
  （悬浮显示一句用途）、可写范围、不许做的事，可展开完整定义正文。页头多三项：开工简报时间（或「还没有
  留档」）、另读上游几处、上次执行（agent · 模型 · 时长）。「给 agent 的简报」改名为「开工简报」，旁边
  新增「留档」按钮。
- **证据与回溯**：每层「用了上游的」一行后面列出「另读」的包，点回那一层。

### 12.7 契约 0.5 与接口

- `layer_package.provenance` +`brief` / +`context_pulls` / +`runs`（都只由上面三条命令写入）。
- 新增 `layer_agent` 1.0（registry 已登记）。
- `workflow_state` 0.5：每个迭代的 `nodes` 每项 +`agent`；层包条目的 `card` +`context` / +`runs`，
  `card.guide` +`agent`。
- 服务端：`/api/guide` 每层的 `agent` 带完整正文，另有 `orchestrator`；新动作 `brief_out`（pkg、by）与
  `run_log`（pkg、agent、model?、duration?、note?）。动作总数 19。
- 命令行：`wf guide` 每层打印 agent、纪律、工具包、可写范围；`wf next` 在前沿包旁给出开工命令；
  `wf brief --out/--by`、`wf show --for/--by`、`wf run-log`。

### 12.8 宿主子代理定义（第 0 步的核实结果）

【实测】宿主从 `~/.zcode/agents` 与 `<工作目录>/.zcode/agents` 读 `*.md`，frontmatter 认 `name`、
`description`（必填）、`tools`、`disallowedTools`、`skills`、`model`、`thoughtLevel`、`maxTurns`、
`memory`、`background`、`injectAgentsMd`、`mcpServers`；项目级定义不允许设 `permissionMode`。**定义在会话
启动时载入**，当前会话里新放的定义看不到（实测：放一个探针定义后 Agent 工具报「Agent type not found」）。

所以投影器生成九份 `.zcode/agents/<host_name>.md`，但**自动派工的冒烟要等下一次新会话**才能做。这一版先把
人工派工路径走通：`wf next` → `wf brief --out` → 把简报交给层 agent → `wf gate` + `wf run-log` → 给人看卡片。

### 12.9 验证

- 【实测】回归 178 条全过（原 158 + 新增 20）：定义通过 schema 与交叉校验、guide 带 agent 正文、L5 的
  rtl skill 与 caselaw、宿主投影九份且调度 agent 无写权限、投影与源一致、简报留档与指纹、回读留痕、执行
  记录、层 agent 被拒的四种人动作、层 agent 的 decide 被强制待批、状态导出 0.5 的 agent、坏定义被拒。
- 【实测】命令行冒烟通过（新增第 10 步，含留档、回读、执行记录、拦截）；引擎自检 15/15；类型检查零错误。
- 【实测】隔离副本上浏览器实测：流程页的 agent 表（8 行，L5 显示 rtl skill 与 caselaw）、框上的 agent 名、
  层详情的 agent 栏与完整定义展开、点「留档」页头出现简报时间、执行记录出现在页头、证据页显示「另读」。
- 【实测】真实 store 没有被写过：层包与决策的修改时间仍停在 2026-11-19 11:57，指纹与动手前一致。

### 12.10 未做与遗留

- **自动派工没冒烟**：宿主定义要新会话才载入，下一次开新会话时用 `sim-layer-l1` 走一层。
- **独立验证面仍未纳入**：方案给了八个执行 agent 与一个调度 agent，没有验证 agent；测试仍由做这一层的
  agent 自己写，V0 风险（生成者自测容易与实现共享误解）还在。这是用户批准时明确不纳入的两点之一
  （方案 ORCH-Q-001），留作后续独立设计。
- **演进中心仍缺可版本化的对象**：现在 agent 定义成了文件，演进中心有了可以对版本化的东西，但这一版没接。
- **按层选模型没接**：宿主支持 `model` 与 `thoughtLevel`，但方案第 5 条假设把这一项留到以后。
- **工具边界只是「声明 + 尽力拦截」加宿主 tools 限制**，不是硬隔离。
- 旧包没有开工简报、回读记录与执行记录，界面如实显示「还没有留档」。

## 13. Agent 管理、技能与工具货架、编排配置（zcode-glm，2026-11-25）

### 13.1 目标与四项决定

【配置】用户要求：左侧有 Agent 管理，能进入每一层（含调度 agent）维护 `agent.md`，配置多种 skills 与 tools；
skills 与 tools 要有货架界面，能打开看定义；最后支持完备的编排配置。四项选择：

1. `agent.md` 编辑用「表单 + 正文，保存前校验」；
2. 改动留痕用「版本快照 + 历史（对比、回滚）」，不引入 Champion / Challenger；
3. 技能只读（可打开、介绍、看被谁使用），工具包可新建与编辑；
4. 流程模板从代码搬到数据文件，界面新建 / 编辑 / 复制，任务创建时选模板。

### 13.2 数据与文件

【实测】新增 `platform/agents/flow_templates.json`：`default` + `templates{id: {label, description, nodes, gate_overrides}}`。
七个模板原样迁移；`nodes` 的顺序就是流程顺序，**edges 由顺序推导**（相邻节点相连；`repeat: per_case` 展开成
`case_1..N` 并扇出 / 扇入），`gate_overrides` 按展开后的别名映射——顺手修掉了旧代码里 `case_N` 覆盖对不上
`case_1` 的问题。新增 `platform/schemas/flow_template.json`（registry 1.0）。

【实测】版本快照存 `platform/agents/.versions/`：`index.jsonl`（追加式） + `objects/<sha256>.<md|json>`（内容寻址）。
每条记 `at / by / kind / target / sha / bytes / lines / add / del / prev_sha / note`。保存前先给当前内容补一条
「保存前快照」，写入后记本次「保存」；回滚前再快照当前，回滚记录写「回滚自 <sha>」。agent / 工具包 / 模板共用。

【实测】写回 `agent.md` 时沿用原文件的「frontmatter 后是否有空行」与结尾空行习惯，避免只改一个字段却把整份
文件格式重排（历史 diff 只出现真正改的那一行）。

### 13.3 引擎（`wf.py` / `layer_bus.py` / `bind_hosts.py`）

【实测】`layer_bus.py` 删除静态 `SCENARIO_TEMPLATES`，改为按 mtime 读数据；新增 `template_ids / default_template /
template_label / validate_template`。`wf.py` 的 `_load_layer_bus()` 也按 mtime 缓存模块——否则每次调用都重执行，
数据缓存无效。模块级 `FLOW_TEMPLATES / TEMPLATE_LABEL` 删除，改函数；`cmd_task_new` / `cmd_iter_new` 把字面量
`"benchmark_only"` 换成「模板没有框」判断（`is_layered_template`），新模板只要没有框，行为与仅 benchmark 一致。

【实测】配置读写函数：`collect_agent_config / collect_skills / collect_toolpacks / collect_flow_templates` 都返回
`{items, errors, sha}`，**坏文件不抛异常**——定义写坏时配置页仍能打开去修。保存函数：`save_agent_definition /
save_toolpack / delete_toolpack / save_flow_template / duplicate_flow_template / restore_config_version`；共同规则：
记名、`base_sha` 防并发覆盖、写前整组校验、写前快照、写后重投影。`bind_hosts.project_agents(check=False)` 改为
可直接调用并返回 `{written, drift}`；agent 改 `host_name` 时删掉旧投影，避免留下孤儿文件。

【实测】校验规则：目录与 `layer` 一致；`role` 与 `LAYER_AGENT` 一致；`host_name` 合法且不与其他 agent 重复；
skills 在磁盘存在；tools 在工具包清单；正文非空；writes / must_not 非空。工具包被任何 agent 引用时不能删；
模板校验组件、别名唯一、`per_case` 至多一个、门禁字段是字符串列表。

### 13.4 服务端

【实测】新增只读端点：

- `GET /api/agents` → 九份定义（含正文、当前文件 `sha`）+ 整组校验错误；
- `GET /api/skills` → 四个 skill 的用途、正文、是否 GENERATED、方法论源、被哪些 agent 使用；
- `GET /api/tools` → 八个工具包 + 使用关系 + 清单文件 `sha`；
- `GET /api/config/history?kind=&target=[&sha=]` → 历史列表 / 单版本原文；
- `GET /api/templates` 扩展：保留 `layers`（TaskForm 与回归依赖），新增原始 `nodes`、`gate_overrides`、`used_by`、
  `components`、文件 `sha`。`used_by` 只数未归档任务。

【实测】`HANDLERS` 新增七个动作：`agent_save`、`agent_restore`、`toolpack_save`、`toolpack_delete`、
`template_save`、`template_duplicate`、`reproject_hosts`；动作总数 19 → 26。所有写动作要求 `agent` 记名；
`agent_save / toolpack_save / template_save / agent_restore` 用 `base_sha` 拒绝覆盖别人的改动（配置文件不在
`store_env_hash` 覆盖范围内，`target_version` 对它们无效，所以另加内容指纹）。

### 13.5 界面

【实测】侧栏新增三个入口（总 7 个）：`/agents` Agent 管理、`/shelf` 技能与工具、`/templates` 编排配置。
新增 `frontend/src/features/config/`：`AgentsPage`（九张卡）、`AgentDetail`（表单 + 正文 + 右侧历史）、
`HistoryPanel`（查看 / LCS 行对比 / 回滚）、`ShelfPage`（skills / 工具包两个 tab）、`TemplatesPage`
（模板卡片与节点编辑器）、`config.css`。表单复用任务模态与通用按钮样式；写动作复用 `useAction` / `useOperator` /
`ActionNotice`。TaskForm 不变，模板下拉从 `/api/templates` 自动看到新模板。

【实测】保存流程是「确认 → 写盘」：点「保存并重投影」先校验本地字段，再弹确认框，明确写出这次会做三件事
（写回 agent.md、留快照、更新 .zcode/agents 兼容投影），并说明平台读取的是 `platform/agents` 原文件、按 mtime
重新加载，保存后下一次派发就使用新定义——**不需要新开对话，也不经过 ZCode 界面**。顶部原「重投影宿主定义」
改为「仅重投影」，只给手动改过文件、没走保存的情况用；宿主投影只是外部宿主兼容副本，平台内部派发不依赖它。

### 13.6 验证

【实测】回归 216 条全过（原 193 + R18 新增 23）：九份定义、四个技能、八个工具包、模板端点带组件与指纹；
保存 agent 后投影与 `/api/guide` 立刻更新、历史两条且指纹对得上；旧 `base_sha` / 非法工具包 / 不记名都被拒且
不写盘；回滚恢复文件与投影；工具包新建 / 编辑 / 被引用拒删 / 未引用可删；新模板可建任务（迭代正好 L1→L8 两个框）、
改 label 立刻反映到任务、可复制、非法组件被拒；`skill_save` 是未知动作。

【实测】`wf selftest` 15/15；`cli_smoke.sh` 全过（含仅 benchmark 模板不开迭代）；`bind_hosts --check` 九份无漂移；
前端临时 tsconfig `tsc --noEmit` 零错误。

【实测】整仓副本浏览器实测（`AGENTS_DIR` 由脚本位置推出、不认 `WF_ROOT`，所以必须整仓复制）：Agent 列表 → L5
详情 → 改名字保存（新指纹、+1/−1 行、重投影 9 份）→ 历史出现「保存前快照」与「保存」→ 对比当前显示 name 行
增删 → 回滚恢复旧名字与旧指纹 → 技能详情显示方法论源、工具包与只读提示 → 新建 `ui:probe` 工具包 → 新建
`ui_probe` 模板 → 任务表单下拉出现「浏览器探针流程」。测后真实 `platform/agents/` 与 `.zcode/agents/` 指纹未变，
真实 store 未写。

### 13.7 边界与遗留

- 不新建 / 删除 agent：仍是每层一个 + 调度一个，共九份。
- 技能只读；改内容要改 `platform/methodology/*.md` 再重跑 `bind_hosts.py`，否则投影会覆盖。
- 模板不指定「换哪个 agent」：框按层绑定该层的 `agent.md`；一层多候选 agent 需要先扩展加载器与派工，本轮不做。
- 工具包与模板也有快照历史，但界面暂只提供 agent 的回滚入口（工具包删除、模板 id 不可改已足够防止误操作）。
- 定义保存后平台读取即生效（按文件 mtime）。平台内部运行时见 §14：`runtime.py` 读定义、调模型、执行工具、
  跑 `wf gate`，不需要「新开对话 + 粘贴开场白」。当前一个作业只跑一层并可选自动推下一层；遇到待人决定就停。
- 配置写是「单线程服务 + base_sha 乐观并发」，不是硬锁；两个浏览器同时保存时后一个会被拒并提示刷新。

## 14. 平台内部运行时：层 agent 不再交给外部对话（zcode-glm，2026-11-26）

### 14.1 为什么

【配置】用户纠正了上一版的设计：通用微架构混合仿真工作流应在平台内自动串联（提交 L1 → 调 L1 的 agent 开工），
不应把用户送到 ZCode 界面做交付，更不该用「新开会话 + 开场白」刷新 `agent.md`。所以运行时的归属必须是平台：
定义由平台读，模型由平台调，工具由平台执行，核对由平台做；界面只出现平台的派发与日志。

### 14.2 结构

【实测】新增 `platform/tools/runtime.py`（只依赖标准库）：

- **定义**：每次派发读 `platform/agents/<层>/agent.md`（`wf.agent_of`），把正文、skills 正文、工具包命令、
  writes / must_not、任务目标、`wf brief` 简报拼成系统提示。没有「开场白」这一步。
- **模型**：OpenAI 兼容 `POST <base_url>/chat/completions`，带 function calling 工具：
  `bash` / `read_file` / `write_file` / `list_dir` / `finish`。模型与密钥来自
  `platform/runtime/config.json`（0600，不进库）或环境变量 `RUNTIME_BASE_URL / RUNTIME_MODEL / RUNTIME_API_KEY`。
- **工具执行**：bash 在仓库根目录、`WF_ROOT` 指向当前 store、`WF_ACTOR=layer:<层>`（层 agent 不能替人签字）；
  读写文件限制在仓库内；输出截断；命令超时；作业可请求停止。
- **核对**：agent 调 `finish` 或给出最终文本后，平台自己跑 `wf gate <包>`，把 verdict / 报告写进作业；
  通过后写 `wf run-log`。agent 自报完成不算。
- **串联**：`auto=true` 时，核对通过后找模板里的下一个框；只有该框已有自有层包才继续派，遇到待人决定 /
  核对未过 / 没有下一个包就停。同一时刻只允许一个作业（store 是追加式，没有并发锁）。
- **作业**：`platform/runtime/jobs/<JOB>.json` + `.jsonl` 日志；状态
  queued / running / passed / needs_decision / gate_failed / error / stopped / max_turns。

### 14.3 服务端与界面

【实测】端点：`GET /api/runtime`（配置是否齐全 + 正在跑的作业 + 最近作业）、`GET /api/jobs`、
`GET /api/job/<job_id>`（含最近 300 条日志）。动作：`dispatch {pkg, auto?}`、`stop_job {job}`、
`runtime_config {base_url, model, api_key?, api_key_env?, max_turns?}`；动作总数 26 → 29。

界面：Agent 管理页顶部是「运行时」配置（未配置时展开表单）；层详情页有「平台内部运行」：一个开关
「核对通过后自动派下一个有包的框（遇到待人决定就停）」、派发按钮、停止按钮、状态与最近 16 条日志。
没有跳转 ZCode 的链接或提示词。

【实测】人的决定发生在结果之后：开工前不知道 agent 会产出什么，所以平台不要求人提前提决定。运行时在
`wf gate` 返回 `needs_decision`（只差签字）时，用本层已产出的接口卡自动生成一条待签事项（`ratify`，
标题是「确认 Lx 层名：第一条 gives 或本层问题」），挂在本包回执上；人直接在「人的决定」里批准/驳回。
手动流程不再在 `validate` 里偷偷建决定；层详情页的按钮改为「补记决定」，本层「未开始」时禁用，打开时
用本层接口卡预填一句话，用于把已经发生、已经产出的选择补成记录。

### 14.4 验证

【实测】回归 225 条全过（原 216 + R19 9 条）：用本机假 OpenAI 接口，派发一个 L1 包后作业日志里依次出现
`tool_call → tool_result → finish → gate`，工具输出 `regress-ok` 可见，作业状态来自平台核对，作业 JSON/JSONL
落盘，运行时配置可保存且不回显密钥；对一个只差签字的高权重层，运行时自动生成待签事项并挂在本层回执上。
另用 `runtime.py run` 做过同样的假模型端到端（bash → finish → gate）。前端 `tsc --noEmit` 零错误。

### 14.5 模型步预算、强制收工与后台可见性（补充，2026-11-26）

【实测】真实作业日志暴露了两个问题：模型在 40 步模型调用里反复读代码、没有调用 finish，最终 `max_turns` 且
没有总结；用户切出流程编排后，后台线程其实还在跑（日志连续 127.7s），但页面丢了 `jobId`，看起来像停了。

**术语**：平台“迭代”指一条任务下的 L1–L8 一轮（`ITER-…`）；一次派发是一个“作业”；作业内部的每次模型调用
是一“步”（可能带多个工具调用）。作业里的 40 步不是 40 次迭代。

- **预算**：系统提示里写明最多 `max_turns` 步模型调用、目标是本层最小可交付、不要反复读同一文件；剩 5 步时
  插入一条「停止扩大探索、优先补齐交付并 finish」的提醒。
- **强制收工**：最后一步只给 `finish` 工具，并设 `tool_choice=finish`；模型必须总结做了什么、改了什么、
  跑了什么核对、还差什么。预算用尽也会继续跑 `wf gate` 并把 verdict 记进作业，不再停在半路没有结论。
- **后台**：作业跑在 server 的独立线程里，界面导航不影响；日志逐条写 `platform/runtime/jobs/<JOB>.jsonl`。
  层详情重新挂载时从 `/api/jobs` 找回这一包最近的作业并接回日志；顶栏在作业运行中显示「作业运行中 · Lx」，
  点它回到对应层。
- **边界**：后台线程属于当前 server 进程；重启 server 会中断正在跑的作业，不会自动续跑（作业状态会保持
  最后一次写入的值）。要跨重启恢复需要另做作业守护/重启恢复，本轮不做。
- **开工前的核对**：`dispatch` 只检查包存在、未作废、运行时配置齐全、当前没有别的作业；本层交付缺项不在开工前
  拦——那是 agent 要补的，完整 `wf gate` 放在收工后。只有运行时未配置、包已作废、上游未验证这类前置问题才需要
  开工前处理。

### 14.6 边界

- **权限是「声明 + 尽力拦截」，不是硬沙箱**：bash 仍能碰仓库内任何文件；`writes / must_not` 是给模型的边界，
  `WF_ACTOR` 只拦人的签字动作。要硬隔离需要容器/系统账号，本轮不做。
- **模型必须支持 function calling**；不支持的工具调用协议会直接报错，不静默降级成纯文本。
- **人工签字层会停**：L1/L2/L3/L4/L6/L8 需要人签，自动串联在这些层核对后停下，等人处理；这是纪律，不是 bug。
- **一个作业一层（可选自动下一层）**，不是无限跑完整任务；作业失败/停止后从当前包重新派发。
- 运行时配置与作业日志在本机 `platform/runtime/`，不进公开仓库；密钥不会通过 `/api/runtime` 回显。

## 15. 层详情信息架构：按“少了它会丢什么”砍模块（zcode-glm，2026-11-26）

### 15.1 判断原则

【配置】用户要求逐模块反推价值：少了这个模块，对使用者看到的信息有没有关键影响？没有就删；重复的移到
单一来源；低价值的一行带过。按这个原则重排后，层详情只保留三栏：目标、交付、证据；核对压成一行；
人的决定移出页面，只留页头入口。

### 15.2 改了什么

【实测】
- **目标**：只留任务目标原话和“这一层回答的问题”。角色、skills、tools、writes、must_not、agent 正文删除——
  这些在 Agent 管理里有单一来源；这里只留一句“在 Agent 管理里维护”的链接，不重复展示。
- **交付**：
  - 顶部加“本层交付”（`guide.produces`）与“交付项”（`guide.deliverables`），先回答这一层到底交什么；
  - 加“交付文件”：登记工件的实际保存路径、类型、哈希；没有路径就如实写“没有记录路径”；
  - “交给下游的结论”改为以“结论 1 / 结论 2”为主标题，S1/S2 缩成小号等宽编号放在旁边（它只是下游引用 ID，
    不是内容本身）；
  - “用了上游的什么”改为“本层依据的上游”：显示上游层 + 具体引用的结论正文；只记了包号没写结论的，
    明确写“只记了包号，没有写具体用了哪条结论”；
  - 原来的“还没解决的问题”按 `ask` 拆开：`next` 改成“给下一层的输入（不是遗留问题，下一层要据此工作）”，
    `human` 单独放在“待人回答”；不再把 L2 的方案输入误标成遗留问题；
  - `limits` 保留为“适用边界”。
- **证据**：从“登记在册的测试与工件列表”改成“结论 → 依据”：
  - 顶部一句 `结论依据：N/M 条有登记依据`；
  - 没有写依据的结论只汇总一行（`没有写依据：S1、S2、S3`），不再每条重复；
  - 有依据的结论逐条列出测试通过/未通过、工件路径与哈希；不在登记册的引用直接标红；
  - 没被任何结论引用的登记物折叠到“未被结论引用的登记物”；其他测试也折叠。这样证据模块只回答
    “每条结论有据可依吗”，不再堆清单。
- **核对回执**：从整块五行列表压成一行 `<details>`：`核对：只差签字 · 2/5 项通过 · 上次 …（展开看逐项）`。
- **人的决定**：不再作为页面中的一栏；页头保留“人的决定（N）”按钮，点开弹窗才能批准/驳回/补记。
  这样页面主体只呈现这一层的目标、交付与依据，签字仍可完成，但不占信息空间。
- **平台内部运行**：从带边框的整块面板压成一行操作条（模型 · 自动下一层 · 派发/停止），日志只在有作业时出现。
- **原始记录**：仍默认折叠，不改。

### 15.3 刻意保留的东西

- **S1/S2 编号保留**：下游接口卡的 `takes` 按这些编号引用本层结论；去掉编号会让下游无法指认“用了哪一条”。
  所以编号不删，只降为次要信息，正文做主标题。
- **人的决定保留入口**：签字是平台硬规则，不能因为移出页面就失去入口；弹窗是折中。若要更彻底，可再做
  一个全局“人的决定”收件箱页面，把这些回执从层详情彻底移走。
- **交付文件路径按实际登记显示**：没有登记文件就写“还没有登记交付文件”，不编造“归因报告 + summary”。

### 15.4 作废后的页面（补充，2026-11-26）

【实测】用户作废一层后，下游整链会被 `invalidated`；原先层详情仍把旧交付/结论/证据按正常内容展示，看起来像
“作废了但信息还在”。现在作废/被取代的包（lifecycle=invalidated/superseded 或状态词已作废）：

- 页头只留一句「本包已作废：不再提供核对/签字/派发；作废前的内容只在原始记录里」；
- 显示一条作废说明（原因与根包），不再渲染交付、证据、核对回执；
- 目标原话保留（属于任务，不属于死包）；
- 人的决定、补记决定、作废并重做、简报、留档按钮全部不显示；
- 「平台内部运行」只显示“本包已作废，不能派发”；
- 原始记录仍默认折叠保留（payload / 接口卡 / 工件 / 核对报告），审计需要时能查；
- 运行时与 `dispatch` 也拒绝在作废/被取代的包上开工，防止在死包上继续跑。

回归夹具补充：真实 store 可能已被用户作废一批包；`regress.py` 在**沙箱副本**里按信封 history 的最后一条
`invalidated` 记录恢复到作废前状态（只动副本，不碰真实 store），并断言作废包不能派发。回归 232/0。

### 15.5 一键重做、证据页与总览的联动（补充，2026-11-26）

【实测】
- **一键重做**：作废包详情页有「重做本层」。它新建一个同层新包（同一任务/迭代），继承设计意图、约束与上游
  引用，payload/证据/决定从头开始；旧包不删，`lifecycle.replaced_by` 记新包号。新包是 draft，旧包仍是
  invalidated，审计可见。
- **同一层有活包时旧死包不参与呈现**：`iteration_layers` 在“同层已有非 invalidated/superseded 包”时过滤掉
  死包，所以重做后这一框用新包，不会被旧包拖成「已作废」；死包仍可按包号查。
- **证据与回溯**：作废包不再展示旧结论/证据，只显示“本包已作废 + 原因 + 查作废包详情”；若同一轮里存在被
  重做替代的历史包，在该层顶部显示“历史：…（已作废，仅存档）”链接。活的包正常显示结论与依据。
- **研究总览**：`pending_decisions` 收集时跳过 invalidated/superseded 包，作废包上的待批事项不再出现在
  「等你决定」，任务卡片的待批数也随之减少；作废包本身仍可用 `wf approve` 直接处理（审计）。
- **运行时**：作废/被取代的包不能派发（前端不显示按钮，后端 `JobManager.start` 也拒绝）。

### 15.6 验证

【实测】前端 `tsc --noEmit` 零错误；浏览器在 L2 页确认：目标栏不再出现 agent/skills/tools 详情；交付栏有
交付摘要、文件路径、结论 1/2/3、上游依据、给下一层的输入；证据栏显示 `0/3 条有登记依据` 与未写依据的编号；
核对是一行；人的决定只在页头按钮与弹窗里；平台内部运行是一行。真实 store 未被写。

## 16. 任务归档、恢复与彻底删除（zcode-glm，2026-11-26）

### 16.1 语义

【配置】用户要求：归档把任务状态冻结、随时激活；删除完全删除、都清空。

- **归档**：任务记录打 `archived`，任务与它的层包从研究总览、流程编排、证据与回溯、待批队列里消失；
  状态、层包、决定、证据都还在 store 里，随时 `恢复` 回到归档前。归档不改变任务/迭代的 status。
- **删除**：只允许删**已归档**任务（防止误删进行中的研究）。需要把任务 ID 原样填进确认框；删除任务记录、
  层包目录、附属决定 / 测试 / 运行 / 结果 / 图边。内容寻址的工件对象保留在 `platform/store/objects/`
  （可能被别的任务共用）；被删任务的记录另写一份清单到 `.zcode/backups/task-delete-<TASK>-<时间>/`，
  便于事后知道删了什么（不是可恢复的数据备份）。

### 16.2 入口与接口

【实测】研究总览：
- 进行中/已结束任务卡上多一个「归档」；弹窗填原因（选填）。
- 底部「已归档任务（N）」折叠区：恢复 / 删除。
- 删除弹窗必须输入任务 ID；确认后调用 `task_delete`。
- 后端新增 `GET /api/archived-tasks`（状态导出不含归档任务，所以单独列摘要）与动作
  `task_archive {task, reason?, restore?}`、`task_delete {task, confirm}`；动作总数 30 → 32。

### 16.3 删除的边界与拒绝条件

【实测】
- 只删已归档任务；未归档先归档。
- 仍有其它任务的层包 `parent_package_refs` 指向被删层包时拒绝（否则别的任务回溯会断）。
- 仍被其它包/任务引用的决定不删。
- 工件只删“任务层包目录内”的登记项，且被其它包/测试/运行引用时保留；`objects/` 不物理清理。
- 删除清单写到 `.zcode/backups`；UI 不提供“回收站恢复”，删除是不可逆操作。

### 16.4 验证

【实测】回归 242/0（R20 6 条）：建临时任务 → 归档 → 已归档清单可见、状态导出消失 → 恢复 → 再归档 →
确认框填错拒绝 → 正确确认后删除 → 任务与层包目录都不在、状态里没有、删除清单存在。浏览器确认总览的
归档弹窗、已归档区的删除确认框（输入框提示任务 ID）正常；真实 store 未被回归改动。

## 17. LangGraph 编排骨架 + CHIA 式领域工具：L1 垂直切片（zcode-glm，2026-11-27）

### 17.1 为什么不是“再调一下 prompt”

【实测】v1 在 L1 的真实作业：
- `JOB-20261126-200454-d227`：40 步模型调用、56 次工具调用、约 8 万字符工具输出、0 条 assistant 正文、
  没有 finish；模型用通用 bash 读 RTL 仓库/子模块/宏定义，最后 `max_turns`。
- `JOB-20261127-102406-9e5a`：40 步、178.6s、`gate_failed`；模型甚至改了 `drive.sv` 的枚举名，但 payload/
  interface/gate 仍未完成。

问题不是模型没有推理，而是工具面给了它一个无限大的仓库和一把 bash。LangGraph 单独也修不了；必须同时换
工具面与状态契约。

### 17.2 结构

【实测】新增 `platform/tools/runtime_v2.py`（可选依赖 LangGraph）：
- `StateGraph` 节点：`prepare` → `agent` ⇄ `tools` → `reflect` → `agent` → `finish`；条件边：agent 有
  tool_calls 去 tools，否则去 finish；tools 执行完先经过 reflect 再回 agent。`reflect` 是不带工具的模型
  调用，只生成 1–2 句 Thought，写 `thought` 事件；`prepare` 写 `plan` 事件（图 + 检查清单）。
- Thought 不冒充 assistant 已执行的动作：reflect 的指令只放在临时消息里，回注时标成“系统记录，这不是
  已执行动作”，否则模型会把“打算调用工具”当成“已经调用”，下一轮直接结束。
- v1 `JobManager` 仍是作业生命周期、`wf gate`、签字、状态、日志、单作业锁的负责人；v2 只替换 L1 的
  “这一层 agent 怎么干活”，避免双重门禁。
- `SqliteSaver` checkpointer 在 `platform/runtime/checkpoints.sqlite`，`thread_id=job_id`；作业 JSON/JSONL
  契约不变，新增 `engine/node` 字段。
- 预算：`max_turns` 仍指模型调用步数；剩 5 步提醒；最后一步只给 finish 并强制收工；预算用尽也跑外层 gate。
- 引擎选择：`RUNTIME_ENGINE` / 运行时配置 `engine`；L1–L8 都走 v2（分层工具面见 §19）。
- 写入容错：`wf_interface_patch` 接受 `patch` 别名、`limits` 字符串归一为列表；工件类型别名归一为
  schema enum；`prepare` 从任务目标补齐空 `design_spec.intent`，避免模型被 gate 的脚手架问题卡死。

### 17.3 CHIA 式领域工具（没有 bash）

工具执行节点只暴露：
- `wf_brief`、`wf_card`、`wf_gate`、`wf_register_artifact`、`wf_payload_patch`、`wf_interface_patch`、
  `wf_show_for`；`l1_previous`、`benchmark_list`、`case_list`、`case_inspect`、`case_read`、`environment_read`；
  `finish`。
- 写入工具做字段/路径校验：L1 payload 只允许 benchmarks / metric_definitions / baseline_refs 等；接口卡按
  `layer_interface` schema 校验；baseline_refs 必须是已登记 ART，且路径在 missions/benchmarks/domains/designs，
  不能是 golden / RESULT / case.json / 平台文档。
- 领域输入红线：`case_inspect` 只读 meta/issue；`case_read` 拒绝 golden、RESULT、case.json、patch；
  与 `missions/l5calibration/README.md` 的“只给 base_commit + issue 文本，golden patch 仅判分用”一致。
- 工具结果截断为结构化小块，避免上下文被 8 万字工具输出塞满。

### 17.4 实测对比

【实测】同一真实模型（glm-5.3）：
- 旧 v1：40 步、56 次工具调用、0 正文、无交付、`max_turns`/`gate_failed`。
- 新 v2（沙箱副本，真实模型）：8 步，工具为 wf_brief / l1_previous / benchmark_list / case_list /
  environment_read / case_inspect / case_read / wf_register_artifact / wf_payload_patch / wf_interface_patch /
  wf_gate / finish；写出真实 benchmark、3 个指标定义、2 个已登记基线工件（drive.sv、base_src_fpnew_cast_multi.sv）
  与 S1–S4 接口卡；`wf gate` 返回 `needs_decision`、blocks 为空，只差人签字。没有 bash，没有改案例文件。
- 显式 ReAct 后（沙箱副本，真实模型）：`JOB-20261127-123115-ae6c` 6 步 / 5 条 `thought` / 10 次工具调用 /
  约 2 分钟；Thought 覆盖“复用失败包基线 → 确认案例 → 写 payload → 写接口卡 → gate 只剩签字 → finish”，
  gate 仍为 `needs_decision`、blocks 为空。作业面板按“思考（ReAct）→ 行动 → 观察”展示。
- v2 假模型 HTTP 冒烟：6 步，payload/interface 落盘，gate needs_decision，checkpoint 存在。
- 旧回归 245/0（`RUNTIME_ENGINE=v1`），真实 store 未被 v2 测试写入。

### 17.5 依赖与启动

- `requirements-runtime.txt`：`langgraph` 1.2.x、`langgraph-checkpoint-sqlite`；不是 wf.py/平台核心依赖。
- 由于系统 Python 有 PEP 668 限制，v2 用 `.venv-runtime`（带 `--system-site-packages`）：
  `python3 -m venv --system-site-packages .venv-runtime && .venv-runtime/bin/pip install -r requirements-runtime.txt`；
  启动 server 用 `.venv-runtime/bin/python platform/viz/server.py --port 8899`。
- Agent 管理 → 运行时把 engine 切到 `langgraph`；L1–L8 都走 v2（见 §19）。v1 始终可用。

### 17.6 边界与后续

- 这一段当时只做了 L1；L2–L8 的分层工具面见 §19。gate→human→下一层的图边、LangGraph interrupt/resume、重启恢复仍未做。
- 目前外层 `wf gate` 仍由 v1 执行；v2 图内的 `wf_gate` 只是 agent 自检，避免双重状态处理。
- 没有引入 Ray/Docker/FastMCP；CHIA 只复用“领域工具 + 程序化边 + 检查点/中断”的思想。
- 真实 store 的 L1 r1 包已由用户派发产出 payload/接口卡，当前停在等人签 `DEC-20261127-110113-cbd8`；
  显式 ReAct 的验证在沙箱副本完成，没有替用户签字、没有写真实 store。

## 18. 工作台可读性与工件审阅（zcode-glm，2026-11-27）

### 18.1 作业时间线

层详情不再把作业 JSONL 原样铺开。`JobTimeline` 按 `turn` 分组，把 `thought` / `tool_call` / `tool_result`
投影成“思考 / 行动 / 观察”；工具名翻成人话，结果只留摘要（登记为 ART、写入哪些字段、核对 verdict），
原始日志放在 `<details>` 里。顶部“本层结果”显示状态、步数、verdict、finish 摘要，并跳到下方交付。
默认只显示最近 8 步，避免长作业把页面撑满。

### 18.2 交付文件查看 / 审核 / 编辑

- 打开：`GET /api/artifact?pkg=…&id=ART-…`。只允许本包 artifacts、interface.gives.evidence、payload
  中引用过的已登记 ART；文本返回完整内容，二进制或超过 512KB 只返回元数据。
- 编辑：`artifact_save` 不覆盖共享输入。修改写到 `designs/<pkg>/edits/<时间>/<文件名>`，登记为新 ART，
  本包 envelope 中对旧 ART 的引用（artifacts / interface / payload / evidence）切到新版本；旧文件、
  旧 ART、旧签字保留。保存后自动重跑 gate，并把直接依赖本包的下游层作废。
- 边界：编辑不是“静默改证据”。页面明确提示会生成新版本、下游会作废、需要重新核对和签字；共享输入
  原文件不会被覆盖。二进制/超大文件只能看元数据。

### 18.3 证据因果

接口卡里每个 `S` 的 `evidence` 数组是它自己声明的依据；不同 S 引用不同 ART 是正常的，平台不替 agent
推断因果，只核对“引用已登记、哈希一致”。证据页现在为每条依据显示“类型 + 文件 + 用途 + 哈希”，
并把“不同结论为什么挂不同 ART”写成常驻说明；ART 可直接点开审核。

### 18.4 待签事项与内容指纹

待签事项不是“一次批准永久有效”：它绑定生成时的 `interface_hash`。内容改过之后：

- `ensure_signoff_pending` 不返回旧待签，而是按当前内容生成新待签，并用 `supersedes` 边把旧待签移出队列；
- `approve` 若发现待签指纹与当前接口卡不一致，直接拒绝，提示先“核对”，不再写一条过期批准；
- 手动“核对”也调用同一逻辑：内容改过则换新待签，已经通过则清掉遗留待签；
- 前端对过期待签只给“重新核对生成新待签”，不显示“批准”。

这样“批准”永远只对用户当时看到的那份内容生效；旧回执保留作审计，但不会挡住当前内容的新签字。

## 19. L2–L8 接入 LangGraph + 分层领域工具（zcode-glm，2026-11-27）

### 19.1 为什么不是复制八份 L1

L1 的“无 bash”是因为观测层不该读 RTL、改案例；L2/L5/L6/L7 必须跑实验/构建，L4 需要写 contract_manifest。
所以沿用同一张图，但按层生成工具面：

| 层 | 写入 | 命令 | 额外工具 |
|---|---|---|---|
| L1 | 无文件写 | 无 | 案例输入、接口卡、payload 白名单 |
| L2 | `designs/` | `run_command`（受控） | 测试登记 |
| L3 | 无 | 无 | 候选方案 payload |
| L4 | 无 | 无 | `wf_contract_patch` |
| L5 | `designs/` | `run_command` | 测试登记、RTL/testbench 工件 |
| L6 | `designs/`、`src/gem5/src/rtl/`、`sim_build/` | `run_command` | 测试登记 |
| L7 | `designs/`、`sim_build/`、`deliverables/` | `run_command` | 测试登记 |
| L8 | 无 | 无 | 结论 payload、测试登记 |

所有层共用：`wf_brief`、`wf_previous`、`repo_read/list`（L1 除外）、`wf_register_artifact`、
`wf_payload_patch`、`wf_interface_patch`、`wf_test_register`、`wf_gate`、`finish`、ReAct reflect。
`run_command` 仍带策略：拒绝签名/作废/平台配置/store 写入；`WF_ACTOR=layer:<层>` 继续阻止层 agent 替人签字。

### 19.2 领域动作

- `wf_payload_patch`：L1 继续严格白名单；L2–L8 先写、按 `wf.LAYER_RULES` 返回 warnings，最终由 gate 裁决。
  嵌套对象深合并，列表覆盖。
- `wf_test_register`：输入测试定义 + 运行证据文件，平台同时登记 TST / RUN / RES，挂到本包 validation；
  这是 L2/L5/L6/L7/L8 满足“需要 pass 测试”的统一入口。
- `wf_contract_patch`：只给 L4，写 `contract_manifest`（manifest_id/interfaces/validity_scope），不混进 payload。
- `case_read` / `repo_read`：支持 offset/limit，并回 `total_lines`；避免模型在长文件上反复盲读。
- `finish` 前没跑过 `wf_gate` 时，平台自动补跑一次 gate 并把结果写进对话，避免模型反复 finish 空转。

### 19.3 验证

- `runtime_v2_smoke.py`：L1 11 项检查（含“L1 没有通用仓库/命令工具”“有 ReAct thought”“工件可打开/编辑”）。
- `runtime_v2_layers_smoke.py`：临时副本里逐层派发 L1–L8；每层都 `engine=langgraph`、有 Thought、有 finish；
  L2/L5/L6/L7 走出 `run_command → repo_write → wf_test_register`，L4 走出 `wf_contract_patch`，L1 没有 repo_read /
  run_command。gate 是否 pass 不在冒烟范围（真实交付由模型完成）。
- 真实模型 L2 沙箱（24 步）：最终写出了 payload 与接口卡草稿，但没登记 pass 测试，未达到 pass；过程中暴露过
  接口卡 `takes` 写成字符串、S 超长，以及模型反复想跳过 gate 直接 finish。已加 `takes` 单上游无歧义归一、
  接口格式提示、finish 前自动补 gate；真实 L2 再验仍建议观察。
- 旧回归 245/0（`RUNTIME_ENGINE=v1`），v2 改动不碰 v1 工具循环。

## 20. L2 40 步不出结果的定位：领域 harness 优先（zcode-glm，2026-11-27）

真实 L2 作业 `JOB-20261127-173814-622a`：40 步、43 次工具调用（run_command 15 / repo_read 10 / repo_list 9），
payload 与接口卡草稿已写出，但 `wf_test_register=0`、ART=0、RES=0，gate 只能 `needs_decision`。
第 10–39 步主要耗在 `equiv_check.py` 的依赖闭包和 verilator 环境，不是 LangGraph 路由，也不是上下文窗口爆掉
（工具输出约 56.8k 字符）。

### 20.1 harness 修复

- `--extra-repo` 兼容 `repo:commit` 与 `repo@commit`；
- cvfpu 的 `src/common_cells` 是 submodule（commit 树只有 gitlink）：自动作为 extra repo，并自动加 include；
- commit 模式下 include/模块找不到时回退工作树读取，`materialize` 也回退；
- 修 `commit=None` 崩溃、include root 层级算错导致 `-I` 不命中。
- 验证：简单命令直接 `EQUIV_PASS`；base 作 candidate 得到 `MISMATCH status_o @10 / EQUIV_FAIL bad=6`。

### 20.2 上下文与停止规则

- 新增 `repo_grep`（正则、path:line），让 agent 定位代码而不是反复整文件读；
- `_compact_messages`：消息总字符超过 60k 时，把较老工具正文压成 600 字摘要，保留最近 12 条和 tool_call 结构；
- 提示明确“同一 harness 连续失败 3 次就停，把环境阻塞写进 open/notes，finish”，不把 30 步耗在修环境。

### 20.3 结论

LangGraph 不是这次的原因；它是路由骨架，不负责 harness 和上下文压缩。L2 当前包仍是 `unresolved` 草稿，
0 RES；harness 已修通，下一步重跑 L2 补 RES 和签字，而不是换架构。

## 21. 跨作业经验：蒸馏上一轮，不恢复旧对话（zcode-glm，2026-11-27）

重新派发不是“接着旧 thread”，而是新 job、新 LangGraph thread。为了让新 agent 少走弯路，平台从同包最近完成的
作业日志里蒸馏经验，注入新作业的 system prompt：

- 结论摘要、工具使用次数、失败命令（error/非零 exit_code）、关键 Thought 尾部、上一轮 gate 报告、
  当前包遗留 ART/RES/lifecycle；
- 最多 2 个作业，文本上限 9000 字，写 `experience_loaded` 事件；
- `wf_experience` 工具可再读结构化经验；
- 明确提示旧 harness/命令策略问题可能已被平台修复，先按当前 environment 重试最小命令。

这不等于恢复旧上下文：旧 Thought 不会逐条回放，旧 checkpoint 不会续跑；新作业只是带着“上一轮哪些路走不通、
哪些结论已确认、哪些工件已留下”起步。真实 L2 40 步作业的摘要/失败/Thought 已能注入到新 job 的 prompt。

## 22. 交付结论按类型归组（zcode-glm，2026-11-27）

层接口卡里的 `gives` 不是一个“总结”，而是给下游的多条独立 claim。L2 典型有四类：根因、单变量干预实测、
替代解释排除、随机等价回归；每条都要保留 S 编号供下游引用，所以不能为了页面好看强行合并。

展示层改为：

- `gives` 支持可选 `kind`：`root_cause / measurement / exclusion / scope / decision / other`；
- 交付页先显示总览：`本层共 4 条：根因/机制 1、实测证据 2、排除项 1`；
- 再按类型折叠：根因组默认展开，其他组可展开；每条仍显示 S 编号、truth、证据和工件；
- 旧接口卡没写 kind 时按文字推断，保证历史包也能分组；
- 证据区每条结论也显示类型标签。

这不是“减少结论”，而是把同质展示改成“摘要 + 分类 + 按需展开”。结论数量仍受接口卡上限和 gate 约束。

## 23. L2 交付为什么只有 log（zcode-glm，2026-11-27）

L2 的主交付是 `payload`（命题四件套、实验矩阵、替代解释、归因 verdict）和接口卡，不是文件；`agent.md` 的
deliverables 也是这些结构化内容。测试运行日志是证据，不是主交付。因此页面只看到两个 log 并不代表层失败，
而是 agent 只登记了测试证据、没有额外生成报告。

展示与证据链修正：

- 交付区标题改为“登记文件（证据/输入）”，避免把证据文件误当主交付；
- 测试结果的 `raw_result_artifact` / `log_artifact` 现在算被结论引用，页面显示“支撑测试 RES-…”；
- `wf_test_register` 自动把运行证据工件挂进包 artifacts，不再要求 agent 额外登记；
- 若需要人读报告，应由 L2 agent 生成并登记一个 `report` 工件；平台不把 payload 伪装成文件。

## 24. L2 人读归因报告（zcode-glm，2026-11-27）

L2 的结构化交付仍是 payload + 接口卡；另外增加一份确定性生成的人读报告：

- `_l2_report_markdown`：命题四件套、实验矩阵、替代解释、归因 verdict/root cause、S1–S4 及依据、
  测试列表、适用边界、下一层/人的问题、登记文件；
- `ensure_layer_report`：写 `designs/<pkg>/L2-attribution-report.md`，登记 report 工件（role=layer-report），
  替换同角色旧报告并挂进 artifacts；
- 运行时在 L2 agent 收工后、平台 gate 前自动生成；UI L2 页提供“生成/重新生成归因报告”；
- 报告是交付物，不是测试证据；测试日志仍由 RES 关联，日志工件不再显示“未被结论引用”；
- 报告按 title+verdict 去重测试，避免同一测试重复登记导致重复段落。

这使 L2 同时具备“机器可读 payload/接口卡”和“人读报告”，而不是把报告伪装成 log。

## 25. L3 人读方案报告（zcode-glm，2026-11-27）

L3 的主交付是 payload 里的候选方案与验收判据；它不要求测试，也不要求文件，所以“登记文件”为空并不代表没产出。
为了让 L3 也有一份人读交付，扩大 `ensure_layer_report`：

- L2 生成 `L2-attribution-report.md`；L3 生成 `L3-solution-report.md`；
- L3 报告内容：候选方案（candidate_id/targets_bottleneck/expected_mechanism/change_location/参数）、
  BR 判据与 frame、measurement_frames、明确排除、S1–S4、适用边界、下一层/人的问题、登记文件；
- 运行时在 L3 agent 收工后、平台 gate 前自动生成；L3 页提供“生成/重新生成方案报告”；
- 报告是 report 工件（role=layer-report），替换旧的同角色报告，挂进本包 artifacts；
- 报告内容确定性生成，以 payload/接口卡/登记证据为准，不调用额外模型。

L3 的机器可读交付仍是 payload + 接口卡；报告是同一事实的人读视图，不替代接口卡。
