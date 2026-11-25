#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""layer_bus — 层间数据总线：5 标准类型 + 层组件注册表 + 类型兼容检查 + 场景模板引擎。

设计原则：
1. 每层组件有类型签名（输入类型 → 输出类型）
2. 编排规则基于类型兼容而非层号顺序
3. 场景模板定义组件选取+依赖边+门禁覆盖
4. 归因不可省：bug-fix 模板的 L5d 必填 root_cause + trigger_condition + stimulus_design
"""

import json
from pathlib import Path

# ============================================================ 标准类型定义

LAYER_TYPES = {
    "ProblemStatement": {
        "required": ["problem_id", "description", "metrics", "expected_outcome"],
        "optional": ["hypothesis", "root_cause", "trigger_condition",
                     "evidence_refs", "attribution_level"],
        "description": "问题陈述——L1/L2 产出",
    },
    "SolutionSpec": {
        "required": ["solution_id", "problem_ref", "approach", "acceptance_criteria"],
        "optional": ["equivalence_spec", "resource_requirements"],
        "description": "方案规范——L3/L4 产出",
    },
    "VerifiedArtifact": {
        "required": ["artifact_ref", "verification_method", "verification_result", "scope"],
        "optional": ["solution_ref", "guards", "auto_solution_spec",
                     "root_cause", "trigger_condition", "fix_location", "stimulus_design"],
        "description": "已验证工件——L5/L5d/L6 产出",
    },
    "ExperimentResult": {
        "required": ["experiment_id", "artifact_refs", "measurements", "criteria_evaluation"],
        "optional": ["reproduce_command", "guard_status"],
        "description": "实验结果——L7 产出",
    },
    "Conclusion": {
        "required": ["conclusion_id", "status", "boundary"],
        "optional": ["evidence_chain", "unverified_assumptions"],
        "description": "结论——L8 产出",
    },
}

# ============================================================ 层组件注册表

LAYER_COMPONENTS = {
    "L1": {
        "name": "observation",
        "input_types": [],                  # 无上游
        "output_type": "ProblemStatement",
        "enriches": ["description", "trigger_condition", "metrics", "expected_outcome"],
        "requires_from_input": None,
        "human_weight": "high",
        "requires_test_evidence": False,
    },
    "L2": {
        "name": "attribution",
        "input_types": ["ProblemStatement"],
        "output_type": "ProblemStatement",
        "enriches": ["hypothesis", "root_cause", "trigger_condition"],
        "requires_from_input": None,
        "human_weight": "high",
        "requires_test_evidence": True,
    },
    "L3": {
        "name": "solution",
        "input_types": ["ProblemStatement"],
        "output_type": "SolutionSpec",
        "enriches": None,                   # 转换型
        "requires_from_input": ["root_cause"],
        "human_weight": "high",
        "requires_test_evidence": False,
    },
    "L4": {
        "name": "contract",
        "input_types": ["SolutionSpec"],
        "output_type": "SolutionSpec",
        "enriches": ["equivalence_spec"],
        "requires_from_input": None,
        "human_weight": "high",
        "requires_test_evidence": False,
    },
    "L5": {
        "name": "rtl_implementation",
        "input_types": ["SolutionSpec", "ProblemStatement"],
        "output_type": "VerifiedArtifact",
        "enriches": None,
        "requires_from_input": None,
        "human_weight": "medium",
        "requires_test_evidence": True,
    },
    "L5d": {
        "name": "rtl_direct_fix",
        "input_types": ["ProblemStatement"],
        "output_type": "VerifiedArtifact",
        "enriches": None,
        "requires_from_input": None,
        "human_weight": "medium",
        "requires_test_evidence": True,
        # bug-fix 直达模式的额外必填字段（归因不可省）
        "bug_fix_required": ["root_cause", "trigger_condition",
                             "fix_location", "stimulus_design"],
    },
    "L6": {
        "name": "integration",
        "input_types": ["VerifiedArtifact"],
        "output_type": "VerifiedArtifact",
        "enriches": ["scope"],
        "requires_from_input": None,
        "human_weight": "high",
        "requires_test_evidence": True,
    },
    "L7": {
        "name": "experiment",
        "input_types": ["VerifiedArtifact"],
        "output_type": "ExperimentResult",
        "enriches": None,
        "requires_from_input": ["verification_result"],
        "human_weight": "low",
        "requires_test_evidence": True,
    },
    "L8": {
        "name": "conclusion",
        "input_types": ["ExperimentResult", "VerifiedArtifact"],
        "output_type": "Conclusion",
        "enriches": None,
        "requires_from_input": None,
        "human_weight": "high",
        "requires_test_evidence": True,
    },
}

# ============================================================ 类型兼容矩阵

TYPE_COMPAT = {
    ("ProblemStatement", "L2"): True,   ("ProblemStatement", "L3"): True,
    ("ProblemStatement", "L5"): True,   ("ProblemStatement", "L5d"): True,
    ("SolutionSpec", "L4"): True,       ("SolutionSpec", "L5"): True,
    ("VerifiedArtifact", "L6"): True,   ("VerifiedArtifact", "L7"): True,
    ("VerifiedArtifact", "L8"): True,
    ("ExperimentResult", "L8"): True,
}


def type_compatible(from_type: str, to_layer: str) -> bool:
    return TYPE_COMPAT.get((from_type, to_layer), False)


def check_composition(edges: list) -> list:
    """检查类型流中每条边的兼容性。返回错误列表（空=通过）。"""
    errors = []
    for edge in edges:
        ft, tl = edge.get("from_type", ""), edge.get("to_layer", "")
        if not type_compatible(ft, tl):
            errors.append(f"类型不兼容：{ft} → {tl}")
    return errors


# ============================================================ 归因强度分级

ATTRIBUTION_LEVELS = {
    0: {"name": "symptom_only", "required": ["description"],
        "template": "bug_fix_with_attribution"},
    1: {"name": "root_cause_identified", "required": ["description", "root_cause"],
        "template": "bug_fix_simple"},
    2: {"name": "mechanism_understood",
        "required": ["description", "root_cause", "hypothesis"],
        "template": "bug_fix_simple"},
    3: {"name": "alternative_excluded",
        "required": ["description", "root_cause", "hypothesis", "evidence_refs"],
        "template": "microarch_optimize"},
}


def assess_attribution_level(problem: dict) -> int:
    """根据 ProblemStatement 的字段完整度评估归因等级。"""
    level = 0
    if problem.get("root_cause"):
        level = 1
    if problem.get("hypothesis"):
        level = 2
    if problem.get("evidence_refs"):
        level = 3
    return level


def recommend_template(problem: dict) -> str:
    level = assess_attribution_level(problem)
    return ATTRIBUTION_LEVELS[level]["template"]


# ============================================================ 场景模板

SCENARIO_TEMPLATES = {
    "bug_fix_simple": {
        "description": "issue 已含根因（归因 ≥1），直达修复+验证",
        "nodes": [
            {"component": "L1", "alias": "anchor"},
            {"component": "L5d", "alias": "fix"},
            {"component": "L8", "alias": "conclude"},
        ],
        "edges": [
            {"from": "anchor", "to": "fix"},
            {"from": "fix", "to": "conclude"},
        ],
        "gate_overrides": {
            "fix": {"required_fields": ["root_cause", "trigger_condition",
                                         "fix_location", "stimulus_design"]},
        },
    },
    "bug_fix_with_attribution": {
        "description": "issue 只有症状（归因 0），需轻量归因后再修",
        "nodes": [
            {"component": "L1", "alias": "anchor"},
            {"component": "L2", "alias": "attribute"},
            {"component": "L5d", "alias": "fix"},
            {"component": "L8", "alias": "conclude"},
        ],
        "edges": [
            {"from": "anchor", "to": "attribute"},
            {"from": "attribute", "to": "fix"},
            {"from": "fix", "to": "conclude"},
        ],
        "gate_overrides": {
            "fix": {"required_fields": ["root_cause", "trigger_condition",
                                         "fix_location", "stimulus_design"]},
        },
    },
    "microarch_optimize": {
        "description": "完整八层——微架构优化（FLOW-1 形态）",
        "nodes": [
            {"component": "L1", "alias": "l1"}, {"component": "L2", "alias": "l2"},
            {"component": "L3", "alias": "l3"}, {"component": "L4", "alias": "l4"},
            {"component": "L5", "alias": "l5"}, {"component": "L6", "alias": "l6"},
            {"component": "L7", "alias": "l7"}, {"component": "L8", "alias": "l8"},
        ],
        "edges": [
            {"from": f"l{i}", "to": f"l{i+1}"} for i in range(1, 8)
        ],
        "gate_overrides": {},
    },
    "batch_calibration": {
        "description": "批量标定——共享 L1 + 每例 L5d + 共享 L8",
        "nodes": [
            {"component": "L1", "alias": "shared_anchor", "shared": True},
            {"component": "L5d", "alias": "case_N", "repeat": "per_case"},
            {"component": "L8", "alias": "aggregate", "shared": True},
        ],
        "edges": [
            {"from": "shared_anchor", "to": "case_N"},
            {"from": "case_N", "to": "aggregate"},
        ],
        "gate_overrides": {
            "case_N": {"required_fields": ["root_cause", "trigger_condition",
                                            "fix_location", "stimulus_design"]},
        },
    },
    "comparative_study": {
        "description": "对比研究——跳过设计层",
        "nodes": [
            {"component": "L1", "alias": "anchor"},
            {"component": "L2", "alias": "attribute"},
            {"component": "L7", "alias": "measure"},
            {"component": "L8", "alias": "conclude"},
        ],
        "edges": [
            {"from": "anchor", "to": "attribute"},
            {"from": "attribute", "to": "measure"},
            {"from": "measure", "to": "conclude"},
        ],
        "gate_overrides": {},
    },
    "component_design": {
        "description": "新组件设计——跳过 L1/L2",
        "nodes": [
            {"component": "L3", "alias": "design"},
            {"component": "L4", "alias": "contract"},
            {"component": "L5", "alias": "implement"},
            {"component": "L6", "alias": "integrate"},
            {"component": "L7", "alias": "verify"},
        ],
        "edges": [
            {"from": "design", "to": "contract"},
            {"from": "contract", "to": "implement"},
            {"from": "implement", "to": "integrate"},
            {"from": "integrate", "to": "verify"},
        ],
        "gate_overrides": {},
    },
    # 不走层的任务（只带目标与 benchmark）：任务长什么样只由模板决定，所以它也是一种模板。
    # 没有层包；完成与否由人封板。
    "benchmark_only": {
        "description": "仅 benchmark（不走层）——只有目标、benchmark 与人的决定",
        "nodes": [],
        "edges": [],
        "gate_overrides": {},
    },
}


def get_template(name: str) -> dict:
    if name not in SCENARIO_TEMPLATES:
        raise ValueError(f"未知模板：{name}（可选：{list(SCENARIO_TEMPLATES)}）")
    return SCENARIO_TEMPLATES[name]


def expand_template(name: str, case_count: int = 1) -> dict:
    """展开模板为具体的包列表和边列表（batch 模板按 case_count 展开）。"""
    tpl = get_template(name)
    nodes, edges = [], []
    case_aliases = []
    for node in tpl["nodes"]:
        if node.get("repeat") == "per_case":
            for i in range(case_count):
                alias = f"case_{i+1}"
                nodes.append({"component": node["component"], "alias": alias})
                case_aliases.append(alias)
        else:
            nodes.append(node)
    for edge in tpl["edges"]:
        if edge["to"] == "case_N":
            for alias in case_aliases:
                edges.append({"from": edge["from"], "to": alias})
        elif edge["from"] == "case_N":
            for alias in case_aliases:
                edges.append({"from": alias, "to": edge["to"]})
        else:
            edges.append(edge)
    return {"nodes": nodes, "edges": edges, "gate_overrides": tpl["gate_overrides"]}


# ============================================================ 验证

def validate_output(type_name: str, output: dict) -> list:
    """验证输出是否符合标准类型的必填字段。"""
    spec = LAYER_TYPES.get(type_name)
    if not spec:
        return [f"未知输出类型：{type_name}"]
    errors = []
    for field in spec["required"]:
        if field not in output or not output[field]:
            errors.append(f"{type_name} 缺必填字段：{field}")
    return errors


def validate_layer_gate(component_id: str, payload: dict,
                        gate_overrides: dict = None) -> list:
    """验证层包的 gate 规则（含模板覆盖）。"""
    comp = LAYER_COMPONENTS.get(component_id)
    if not comp:
        return [f"未知层组件：{component_id}"]
    errors = []

    # 1. 输出类型必须存在且符合标准
    output_type = payload.get("output_type", "")
    if output_type not in LAYER_TYPES:
        errors.append(f"缺或未知 output_type：{output_type}")
        return errors

    # 2. 输出必须通过类型验证
    output = payload.get("output", {})
    errors += validate_output(output_type, output)

    # 3. 模板覆盖的必填字段
    if gate_overrides:
        for field in gate_overrides.get("required_fields", []):
            if field not in output or not output[field]:
                errors.append(f"模板必填：{field}（归因不可省）")

    # 4. 组件自带的必填字段（如 L5d 的 bug_fix_required）
    for field in comp.get("bug_fix_required", []):
        if field not in output or not output[field]:
            errors.append(f"L5d 必填：{field}")

    return errors
