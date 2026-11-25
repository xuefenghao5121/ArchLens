# 判例库（caselaw）——仿真平台的持久判断资产

> 70 题实测标定蒸馏的 15 条泛化判例。**适用于任意工作场景**（RTL 开发/调试/交付/
> 仿真基础设施/benchmark/工作流设计），不绑定任何具体 benchmark。

## 为什么是判例库而不是规则清单

RSI 配对 A/B 实验实测（20 对同题同模型）：把经验写成规则清单下发给新代理 =
**零增益 + 30% 额外预算**。经验的判断成分在"情境匹配的检索 + 挖掘过程参与"里。
详见 CL-015 判例本身（它就是这次实验的沉淀）。

## 用法（在两个时刻检索）

```bash
# 任务开始时：用情境描述查（不是关键词堆砌，写你面对的局面）
python3 platform/tools/caselaw.py search "要交付一个 FIFO 模块，文档没写满/空时的行为"

# 失败时刻：用失败签名查（报错信息/症状）
python3 platform/tools/caselaw.py search "测试失败但没有报错信息"

# 看完整判例 / 列库 / 沉淀新判例
python3 platform/tools/caselaw.py show CL-001
python3 platform/tools/caselaw.py list --domain rtl
python3 platform/tools/caselaw.py add --title "..." --situation "情境1;情境2" \
    --signals "信号,线索" --action "做法" --lesson "教训" --evidence "证据路径"
```

## 判例结构

每条判例 = **情境签名**（什么时候适用）+ **信号线索**（什么报错/关键词出现）+
**做法**（具体怎么做）+ **教训**（为什么）+ **实测结果**（不是理论）+ **证据指针**
（可回溯到原始数据）。

## 飞轮：工作 → 判例 → 未来工作

任何工作结束（尤其失败或险胜）后 `caselaw add` 沉淀一条。判例库是平台唯一
跨会话持久的"判断力"载体——会话内的迁移会随上下文消失，判例不会。

## 维护

- `cases.jsonl`：判例数据（每行一条 JSON）
- `../tools/caselaw.py`：检索/沉淀工具（stdlib-only）
- 检索算法：情境/信号/标题加权的关键词评分 + 中文 2-gram 兜底
