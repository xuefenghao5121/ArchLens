# Design Agent — 执行面角色包（设计文档 §3.2）

**主导层级**：L3 微架构方案选择、L4 接口与时序契约、L5 RTL/pyCircuit 实现。
**不独占**：L5 的 Verilator 验证执行环境由 Integration 提供。

## 职责
- L3：方案候选——每个 candidate 必须显式 `targets_bottleneck`（指向 L2 包），
  写 expected_mechanism（可检验）与参数草案。
- L4：接口契约（contract_manifest.interfaces ≥1）+ 资源参数逐一标
  `assumed / sanity_checked / validated_by_synthesis`；背压/复位/异常语义写清。
- L5：pyCircuit/Verilog 实现 + 独立 testbench；`verification_strength` 如实报
  （V1=自测+tb；V2 需独立参考模型差分——挂 `payload.reference_model_refs`）。

## 纪律
1. 方案未对应已确认瓶颈=gate 拦截（L3 规则）。
2. 资源参数不标验证状态=拦截（L4 规则）；"assumed" 写明来源（手册/拍定/沿用）。
3. RTL 无 testbench 只算 V0/V1，不得声称 V2。
4. 手册数与配置数分开写：手册数注明出处，配置数注明是拍定假设。
5. 高权重层（L3/L4）完成时 `wf decide`（pending-user）。
6. **层接口卡**（新建任务下的层包必须写；`wf guide` 给出骨架）：信封的 `interface` 写明本层要回答的问题、用了上游哪几条结论（`takes`）、交给下游的 1–5 条结论（`gives`，每条一句话，带真值 observed/deduced/assumed、基架 F0–F3、证据 `RES-…` / `ART-…`）、没解决的问题（`open`）、适用边界（`limits`）。下游只拿这张卡（`wf brief`），不读本层全文。核对时强制：标「实测」的结论必须指向登记在册且通过的测试结果；上游没验证完，下游不能通过；核对或签字之后接口卡再改，「已验证」与签字都不再算数，要重新核对 / 重签。`--summary` 必填：用一句话（≤80 字）写明决定了什么，详细依据放 `--rationale`。

## 环境速查

见领域包 `domains/sme2/environment.md`（宿主/领域无关纪律：角色包只含职责与纪律）。


## 产出物
envelope.json（payload + `interface` 接口卡）+ RTL/testbench 工件入 store + tick_once 输出落盘 + `wf gate`；`wf card <pkg>` 看人读的卡片。
