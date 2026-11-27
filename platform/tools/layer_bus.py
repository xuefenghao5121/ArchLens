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
import re
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


# ============================================================ 场景模板（数据文件驱动）
#
# 模板从代码搬到 platform/agents/flow_templates.json：id、label、description、节点顺序与
# 门禁覆盖都是数据；edges 由节点顺序推导。已有任务的 flow_template 引用这些 id，所以
# id 创建后不可改——要试新流程就复制成新 id。

TEMPLATES_PATH = Path(__file__).resolve().parents[1] / "agents" / "flow_templates.json"
_TPL_CACHE = {"sig": None, "data": None}


def _templates_sig():
    try:
        st = TEMPLATES_PATH.stat()
    except OSError:
        return None
    return (str(TEMPLATES_PATH), st.st_mtime_ns, st.st_size)


def load_templates():
    """读模板数据（按 mtime 缓存）。文件缺失或结构不对直接报错——模板是流程的单一来源，
    不能静默退化成空模板。"""
    sig = _templates_sig()
    if sig is not None and _TPL_CACHE["sig"] == sig and _TPL_CACHE["data"] is not None:
        return _TPL_CACHE["data"]
    if sig is None:
        raise ValueError(f"找不到流程模板数据：{TEMPLATES_PATH}")
    raw = json.loads(TEMPLATES_PATH.read_text(encoding="utf-8"))
    templates = raw.get("templates")
    if not isinstance(templates, dict) or not templates:
        raise ValueError(f"流程模板数据里没有模板：{TEMPLATES_PATH}")
    data = {
        "schema_version": raw.get("schema_version", ""),
        "default": raw.get("default") or next(iter(templates)),
        "templates": templates,
    }
    _TPL_CACHE.update(sig=sig, data=data)
    return data


def template_ids():
    return list(load_templates()["templates"])


def default_template():
    data = load_templates()
    d = data.get("default") or ""
    return d if d in data["templates"] else next(iter(data["templates"]))


def template_label(name):
    tpl = load_templates()["templates"].get(name)
    return (tpl or {}).get("label", name)


def get_template(name):
    templates = load_templates()["templates"]
    if name not in templates:
        raise ValueError(f"未知模板：{name}（可选：{list(templates)}）")
    return templates[name]


def validate_template(tid, tpl):
    """模板的静态校验：id、人读字段、组件、别名、逐例标记、门禁覆盖。
    返回错误列表（空 = 通过）。不看类型兼容——现有模板里就有跨类型直连（comparative_study），
    引擎只用节点顺序，类型检查只适合给建议，不适合拦。"""
    errs = []
    if not isinstance(tid, str) or not re.match(r"^[a-z][a-z0-9_]*$", tid or ""):
        errs.append(f"模板 id {tid!r} 不合法（小写字母开头，只含小写字母/数字/下划线）")
    if not isinstance(tpl, dict):
        return errs + [f"模板 {tid}: 须是对象"]
    for key in ("label", "description"):
        if not isinstance(tpl.get(key), str) or not tpl.get(key, "").strip():
            errs.append(f"模板 {tid}: {key} 不能为空")
    nodes = tpl.get("nodes")
    if not isinstance(nodes, list):
        errs.append(f"模板 {tid}: nodes 须是列表")
        nodes = []
    aliases, repeats = [], 0
    for i, nd in enumerate(nodes):
        where = f"模板 {tid} 第 {i + 1} 个节点"
        if not isinstance(nd, dict):
            errs.append(f"{where}: 须是对象")
            continue
        comp = nd.get("component")
        if comp not in LAYER_COMPONENTS:
            errs.append(f"{where}: 未知组件 {comp!r}（可选：{list(LAYER_COMPONENTS)}）")
        alias = nd.get("alias")
        if not isinstance(alias, str) or not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", alias or ""):
            errs.append(f"{where}: 别名 {alias!r} 不合法（字母开头，只含字母/数字/下划线）")
        elif alias in aliases:
            errs.append(f"{where}: 别名 {alias!r} 重复")
        else:
            aliases.append(alias)
        repeat = nd.get("repeat")
        if repeat not in (None, "per_case"):
            errs.append(f"{where}: repeat 只支持 per_case（收到 {repeat!r}）")
        elif repeat == "per_case":
            repeats += 1
    if repeats > 1:
        errs.append(f"模板 {tid}: 最多一个 per_case 节点（现在 {repeats} 个）")
    overrides = tpl.get("gate_overrides") or {}
    if not isinstance(overrides, dict):
        errs.append(f"模板 {tid}: gate_overrides 须是对象")
        overrides = {}
    for alias, ov in overrides.items():
        if alias not in aliases:
            errs.append(f"模板 {tid}: gate_overrides 的 {alias!r} 不是模板里的节点别名")
        if not isinstance(ov, dict):
            errs.append(f"模板 {tid}: gate_overrides.{alias} 须是对象")
            continue
        fields = ov.get("required_fields", [])
        if not isinstance(fields, list) or not all(isinstance(x, str) and x.strip() for x in fields):
            errs.append(f"模板 {tid}: gate_overrides.{alias}.required_fields 须是非空字符串列表")
    return errs


def expand_template(name, case_count=1):
    """展开模板为具体的包列表和边列表（per_case 节点按 case_count 展开为 case_1..N）。
    边由节点顺序推导：相邻节点相连；逐例节点与前后节点扇出 / 扇入。"""
    tpl = get_template(name)
    count = max(1, int(case_count or 1))
    raw_nodes = tpl.get("nodes") or []
    nodes, groups, template_aliases = [], [], []
    for node in raw_nodes:
        template_aliases.append(node.get("alias", ""))
        if node.get("repeat") == "per_case":
            aliases = [f"case_{i + 1}" for i in range(count)]
        else:
            aliases = [node.get("alias", "")]
        groups.append(aliases)
        for alias in aliases:
            nodes.append({"component": node["component"], "alias": alias})
    edges = []
    for left, right in zip(groups, groups[1:]):
        for a in left:
            for b in right:
                edges.append({"from": a, "to": b})
    overrides = {}
    raw_over = tpl.get("gate_overrides") or {}
    for alias, group in zip(template_aliases, groups):
        if alias in raw_over:
            for expanded in group:
                overrides[expanded] = raw_over[alias]
    return {"nodes": nodes, "edges": edges, "gate_overrides": overrides}


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
