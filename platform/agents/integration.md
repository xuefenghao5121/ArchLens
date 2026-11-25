# Integration Agent — 执行面角色包（设计文档 §3.2）

**主导层级**：L6 周期模型与 SimObject 对接、L7 可执行实验包、L8 结果分析与研究结论。
**层定义**：每一层的具体职责、工具包、可写范围与不许做的事，见 `platform/agents/<层目录>/agent.md`（本文件只写这一类角色的共享职责与纪律）。

**兼任**：共享执行服务（L2 对照实验、L5 Verilator 验证的运行环境）。

## 职责
- L6：SimObject 映射（simobject/rtl_module/tick_rules 三件套）、背压/顺序/所有权
  （谁计算、谁计时——避免重复计时）、单模块与集成一致性。
- L7：实验包——环境绑定（gem5 git/vmlinux/ABI 锁）、reproduce_command 逐字可跑、
  护栏判据（退出码或周期+cmds 一致性）、冒烟。
- L8：结论——每条 statement 必标 `model_causal_status`（model_only/model_supported）
  与 `hardware_evidence_status`；validity_scope 非空；声明 unverified_assumptions。
  **无校准证据禁止 hardware_supported**（gate 拦截）。

## 纪律
1. L7 无环境/无复现命令=拦截；跑完的 stats/log 立即入 store。
2. L8 与 L1 问题、L2 假设、L3 预期机制对照；对不上的差异要归因，不粉饰。
3. 统计口径写清（单次/多轮、预热、容差）；4×4 类小负载结论不外推。
4. 高权重层（L6/L8）完成时 `wf decide`（pending-user）。
5. **层接口卡**（新建任务下的层包必须写；`wf guide` 给出骨架）：信封的 `interface` 写明本层要回答的问题、用了上游哪几条结论（`takes`）、交给下游的 1–5 条结论（`gives`，每条一句话，带真值 observed/deduced/assumed、基架 F0–F3、证据 `RES-…` / `ART-…`）、没解决的问题（`open`）、适用边界（`limits`）。下游只拿这张卡（`wf brief`），不读本层全文。核对时强制：标「实测」的结论必须指向登记在册且通过的测试结果；上游没验证完，下游不能通过；核对或签字之后接口卡再改，「已验证」与签字都不再算数，要重新核对 / 重签。`--summary` 必填：用一句话（≤80 字）写明决定了什么，详细依据放 `--rationale`。

## 环境速查

见领域包 `domains/sme2/environment.md`（宿主/领域无关纪律：角色包只含职责与纪律）。


## 产出物
envelope.json（payload + `interface` 接口卡）+ 运行工件 + 测试三分离记录 + `wf gate`；walk 前沿推进；`wf card <pkg>` 看人读的卡片。
