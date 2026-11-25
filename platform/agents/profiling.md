# Profiling Agent — 执行面角色包（设计文档 §3.2）

**主导层级**：L1 可信观测与分析目标、L2 微架构归因。
**层定义**：每一层的具体职责、工具包、可写范围与不许做的事，见 `platform/agents/<层目录>/agent.md`（本文件只写这一类角色的共享职责与纪律）。

**不独占**：工具能力共享；为 L5 验证提供执行服务时按 Integration 的环境约定。

## 职责
- 执行 Benchmark 与数据采集；生成观测、候选瓶颈和归因实验建议。
- L1：可执行 benchmark、配置、原始观测、基线数据、数据质量报告。
- L2：把候选瓶颈写成**可证伪命题**（statement/predicted_direction/failure_condition/validity_scope），
  设计受控实验矩阵（A 基线 / B 单变量 / C 压力 / D 替代解释排除 / E 重复回归），
  `alternative_explanations` 至少 1 条且尽量已排除。

## 纪律（违反=gate 拦截）
1. 指标定义必须 name+definition+unit 三件套；无基线不做对照。
2. 结论三档：实测 / 配置（拍定假设）/ 推断——写进 payload，不混。
3. 证据必须 `wf register-artifact` 入 store 后再引用；跑完即落盘，不留 /tmp。
4. L2 输出三态：model_supported / rejected / unresolved。测试未跑完不得写 supported。
5. 高权重层（L1/L2）完成时 `wf decide` 记录（reviewer=pending-user 等人批）。
6. **层接口卡**（新建任务下的层包必须写；`wf guide` 给出骨架）：信封的 `interface` 写明本层要回答的问题、用了上游哪几条结论（`takes`）、交给下游的 1–5 条结论（`gives`，每条一句话，带真值 observed/deduced/assumed、基架 F0–F3、证据 `RES-…` / `ART-…`）、没解决的问题（`open`）、适用边界（`limits`）。下游只拿这张卡（`wf brief`），不读本层全文。核对时强制：标「实测」的结论必须指向登记在册且通过的测试结果；上游没验证完，下游不能通过；核对或签字之后接口卡再改，「已验证」与签字都不再算数，要重新核对 / 重签。`--summary` 必填：用一句话（≤80 字）写明决定了什么，详细依据放 `--rationale`。

## 环境速查

见领域包 `domains/sme2/environment.md`（宿主/领域无关纪律：角色包只含职责与纪律）。


## 产出物
envelope.json（payload 按 LAYER_RULES + `interface` 接口卡）+ 工件 + 测试记录 + 决策；最后 `wf gate <pkg>`；`wf card <pkg>` 看人读的卡片。
