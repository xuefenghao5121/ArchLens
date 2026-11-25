# ArchLens

**微架构混合仿真的研究工作台。** 把「观测 → 归因 → 方案 → 契约 → RTL → 集成 → 实验 → 结论」这条链做成可核对、可回溯、可追责的流水线，而不是一个跑完就出结论的黑盒。

平台面向的问题不是「自动跑实验」，而是：结论为什么可信。所以每一层都必须交出一张**接口卡**（这一层回答了什么、用了上游哪几条、交给下游哪几条、哪里没解决、适用边界在哪），每一条数字都带真值与基架标注，每一次核对都留下机械回执，每一个人的决定都留下署名回执。

## 它用几条硬规则替代「相信 agent」

- **没有证据不算实测。** 标「实测」的结论必须指向登记在册、结论为通过的测试结果，否则核对不通过。
- **上游没验证完，下游不能通过。** 层与层之间只传接口卡；上游未验证或核对后内容又改过，下游直接被拦。
- **签字只对签过的那份内容有效。** 每条决定回执记下当时接口卡的内容指纹；之后内容再改，回执显示为过期，要重签。
- **核对通过后内容再改，「已验证」不再成立。** 自动回到「未验证」，下游随之不能通过。
- **工件漂移会被抓到。** 登记过的工件每次核对重算哈希；文件被改过、丢失、哈希不符，都算证据问题。
- **状态只有一个词。** 未开始 / 进行中 / 待人决定 / 未验证 / 有问题 / 已验证 / 已作废，由核对结果机械得出，不靠人描述。

## 组成

| 组件 | 位置 | 作用 |
|---|---|---|
| 流程引擎 | `platform/tools/wf.py` | 层包、门禁、失效传播、决策、测试三分离、任务与迭代、状态导出。只依赖标准库 |
| 层间总线 | `platform/tools/layer_bus.py` | 层组件注册表、类型兼容检查、流程模板（完整八层 / 缺陷修复 / 对比研究 / 新组件 / 批量标定 / 仅 benchmark） |
| 判例库 | `platform/tools/caselaw.py` | 按情境检索积累的 RTL 与验证教训 |
| 契约 | `platform/schemas/` | 12 份 JSON Schema：层包、接口卡、测试定义/运行/结果、决策、领域包、状态导出等 |
| 方法论 | `platform/methodology/` | 层输出与交接纪律、L3 判据有效性；`bind_hosts.py` 把它们投影成宿主 skill |
| 服务端 | `platform/viz/server.py` | 只读状态投影 + 受控写操作面（请求来源校验、幂等键、乐观并发） |
| 界面 | `frontend/` | React + TypeScript：研究总览、流程编排、层详情、证据与回溯、演进中心 |
| 适配器 | `adapters/` | 仿真器适配（gem5 ARM、QEMU user-mode），换仿真器只写一份清单 |
| 角色与规范 | `platform/agents/`、`.agents/skills/` | 执行面三类角色的职责与纪律 |

## 快速开始

只依赖 Python 3 标准库；界面需要 Node 18+。

```bash
git clone https://github.com/xuefenghao5121/ArchLens.git
cd ArchLens

# 1. 初始化证据存储（在仓库根目录下生成 platform/store/）
python3 platform/tools/wf.py init-store

# 2. 看看每层要回答什么、由谁做、怎样算已验证
python3 platform/tools/wf.py guide

# 3. 引擎自检：15 个已知真值故障必须全部被拦截
python3 platform/tools/wf.py selftest
```

起一个任务，走第一轮：

```bash
# 导入一个 benchmark：可以是负载文件，也可以是题集目录
python3 platform/tools/wf.py bench-import --name my-kernel --kind 负载 path/to/kernel.S

# 立项：标题 + 目标原话 + benchmark + 流程模板（--create 会按模板生成本轮各层的层包）
python3 platform/tools/wf.py task-new \
  --title "第一个任务" \
  --goal "在这条负载上把窗口周期降下来，并说清楚降在哪" \
  --benchmark my-kernel --template microarch_optimize --create

python3 platform/tools/wf.py task-list
python3 platform/tools/wf.py next            # 当前前沿：该由哪个角色、做哪个包
python3 platform/tools/wf.py brief <包名>     # 给这一层 agent 的开工简报
python3 platform/tools/wf.py card  <包名>     # 这一层的人读卡片
```

起界面：

```bash
# 后端（默认连仓库里的 store；只想试用可以 WF_ROOT 指向一份副本）
python3 platform/viz/server.py --port 8899

# 前端（另一个终端）
cd frontend && npm install && npm run dev
# 打开 http://localhost:5173
```

两个服务都只监听本机回环地址。

## 目录

```
platform/
├── tools/        wf.py（引擎）、layer_bus.py、caselaw.py、bind_hosts.py
├── schemas/      12 份 JSON Schema
├── methodology/  方法论单一事实源（宿主无关的 markdown）
├── agents/       执行面角色包：profiling / design / integration
├── templates/    领域包与交付物的模板
├── caselaw/      判例库
└── viz/          服务端、回归、命令行冒烟、单文件只读页
frontend/         React 界面
adapters/         仿真器适配器
env/              工具链与构建配方
handoff/          设计交接文档
```

## 一些设计取舍

- **只依赖标准库**：引擎不引入第三方 Python 包，克隆下来就能跑。
- **追加式存储**：决策与证据只追加，不覆盖历史；工件按内容哈希存放。
- **界面是只读投影**：界面不立新事实，所有内容来自 store；写操作走受控入口并留痕。
- **方法论可投影**：方法论是宿主无关的 markdown，通过 `bind_hosts.py` 投影成具体宿主的 skill，换宿主不用改内容。

## 这个仓库里没有什么

公开的是**平台**，不是任何一次具体研究的数据与结论。因此以下内容不在仓库里：

- 层包、任务记录、证据存储（`domains/`、`tasks/`、`platform/store/`）——属于具体研究过程；
- 研究进展日志（`SPEC.md`）与含具体测量数字的复盘；
- 第三方仿真器源码与负载（gem5、pyCircuit、Verilator、benchmark 题集等，合计数十 GB）；
- 交付物与容器镜像。

所以 `platform/viz/regress.py` 这份回归套件在空数据上跑不起来——它断言的是具体研究数据里的包与结论，属于内部开发工具。

## 许可证

本仓库暂未附加开源许可证，版权归作者所有。如需使用、引用或合作，请联系作者。
