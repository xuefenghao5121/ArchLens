#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime_v2 — LangGraph 编排 + CHIA 式领域工具（L1–L8）。

v1（runtime.py）仍是作业生命周期、gate、签字与 store 的负责人；v2 只替换“这一层 agent 怎么干活”：
  prepare -> agent <-> tools -> reflect -> agent -> finish
- agent 只看到领域工具（wf_brief / wf_previous / payload_patch / interface_patch / gate / 测试登记 ...）；
  L1 无 bash/文件写，L2-L8 按层开放 repo_read/repo_write/run_command 与 wf_test_register。
- LangGraph StateGraph + SQLite checkpointer；thread_id = job_id。
- 外层 v1 JobManager 仍负责 wf gate / signoff / 状态 / 日志，避免双重门禁。

依赖是可选的：没装 langgraph 时 v2 抛明确错误，v1 继续可用。
"""
import fnmatch
import json
import os
import re
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path
from typing import TypedDict

import runtime as v1
import wf

# L1 仍是严格白名单；L2-L8 允许本层自己的 payload 键（由 wf.LAYER_RULES 兜底校验）。
ALLOWED_PAYLOAD_KEYS = {
    "benchmarks", "metric_definitions", "baseline_refs", "data_quality",
    "baseline_characterization", "claim_provenance", "l2_handover_recommendations",
}

# 每层能用哪些“动作”工具。L1 保持无 bash / 无文件写；L2/L5/L6/L7 需要跑领域命令，
# 但命令仍带策略（拒绝签名、作废、平台配置/商店写入等），并且写文件只允许本层目录。
LAYER_SPEC = {
    "L1": {"write_roots": (), "commands": False, "extra": ()},
    "L2": {"write_roots": ("designs/",), "commands": True, "extra": ()},
    "L3": {"write_roots": (), "commands": False, "extra": ()},
    "L4": {"write_roots": (), "commands": False, "extra": ("contract",)},
    "L5": {"write_roots": ("designs/",), "commands": True, "extra": ()},
    "L6": {"write_roots": ("designs/", "src/gem5/src/rtl/", "sim_build/"),
           "commands": True, "extra": ()},
    "L7": {"write_roots": ("designs/", "sim_build/", "deliverables/"),
           "commands": True, "extra": ()},
    "L8": {"write_roots": (), "commands": False, "extra": ()},
}

ARTIFACT_ROOTS = {
    "L1": ("missions/", "benchmarks/", "domains/", "designs/"),
    "L2": ("missions/", "benchmarks/", "domains/", "designs/", "sim_build/"),
    "L3": ("designs/", "domains/"),
    "L4": ("designs/", "domains/"),
    "L5": ("designs/", "sim_build/"),
    "L6": ("designs/", "sim_build/", "src/"),
    "L7": ("designs/", "sim_build/", "deliverables/", "domains/"),
    "L8": ("designs/", "domains/"),
}

DENY_COMMAND = re.compile(
    r"(^|\s)(sudo|mkfs|shutdown|reboot)\b"
    r"|rm\s+-[a-z]*r[a-z]*f[a-z]*\s+/"
    r"|git\s+(reset\s+--hard|clean\s+-[a-z]*f)"
    r"|platform/tools/wf\.py\s+(approve|reject|invalidate|decide)"
    r"|gate\s+--override"
    r"|platform/runtime/config\.json|platform/store|rm\s+-[^\n]*\.git",
    re.I,
)

TOOL_SCHEMAS = []  # 兼容旧导入；实际按层生成

FINISH_TOOL = {"type": "function", "function": {
    "name": "finish", "description": "本层工作结束时调用；summary 写做了什么、跑了什么核对、还有什么没解决。",
    "parameters": {"type": "object", "required": ["summary"], "properties": {
        "summary": {"type": "string"}}}}}


def _tool_schemas(layer):
    """按层生成工具面：公共领域工具 + 本层允许的动作工具。"""
    spec = LAYER_SPEC.get(layer, LAYER_SPEC["L1"])
    schemas = [
        {"type": "function", "function": {
            "name": "wf_brief", "description": "本层开工简报：目标、本层问题、上游结论、交付清单、允许的写入范围。只读。",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "wf_previous", "description": "同一任务/迭代里上一版同层包的 payload、接口卡和工件；参考用，只读。",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "wf_experience", "description": "读取同包最近完成的作业经验：总结、失败命令、关键 Thought、遗留工件/测试。只读。",
            "parameters": {"type": "object", "properties": {
                "limit": {"type": "integer", "description": "最多读取几个作业，默认 2"}}, "required": []}}},
        {"type": "function", "function": {
            "name": "benchmark_list", "description": "全局 benchmark 清单（名字、种类、登记工件数）。",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "case_list", "description": "列出 missions 下的案例目录。",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "case_inspect", "description": "看一个案例的 meta/issue 摘要（只读，不暴露 golden/RESULT）。",
            "parameters": {"type": "object", "required": ["case"], "properties": {
                "case": {"type": "string", "description": "case_list 返回的目录名"}}}}},
        {"type": "function", "function": {
            "name": "case_read", "description": "读案例目录内的输入文件（只读，限案例目录，不读 golden/RESULT/patch）。",
            "parameters": {"type": "object", "required": ["case", "path"], "properties": {
                "case": {"type": "string"}, "path": {"type": "string"},
                "offset": {"type": "integer"}, "limit": {"type": "integer"}}}}},
        {"type": "function", "function": {
            "name": "environment_read", "description": "读本领域 environment.md 的速查内容（构建/仿真命令、护栏）。只读。",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "repo_list", "description": "列仓库内一个目录（只读）；不给路径默认仓库根。",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "description": "仓库相对路径"}}, "required": []}}},
        {"type": "function", "function": {
            "name": "repo_read", "description": "读仓库内的文本文件（只读，自动截断；不读 golden/RESULT/case.json/密钥）。",
            "parameters": {"type": "object", "required": ["path"], "properties": {
                "path": {"type": "string"}, "offset": {"type": "integer"},
                "limit": {"type": "integer"}}}}},
        {"type": "function", "function": {
            "name": "repo_grep", "description": "按正则搜索仓库内文件，返回匹配行（path:line:text）；定位代码用，不要整文件读。",
            "parameters": {"type": "object", "required": ["pattern"], "properties": {
                "pattern": {"type": "string"}, "path": {"type": "string"},
                "include": {"type": "string", "description": "如 *.sv"}}}}},
        {"type": "function", "function": {
            "name": "wf_register_artifact", "description": "把一个仓库内文件登记为工件，返回 ART id、哈希、路径。",
            "parameters": {"type": "object", "required": ["path"], "properties": {
                "path": {"type": "string"}, "artifact_type": {"type": "string"}}}}},
        {"type": "function", "function": {
            "name": "wf_payload_patch", "description": "合并本层 payload；L1 白名单严格校验，L2-L8 按层规则返回 warnings，最终由 gate 裁决。",
            "parameters": {"type": "object", "required": ["patch"], "properties": {
                "patch": {"type": "object", "description": "要合并的 payload 字段"}}}}},
        {"type": "function", "function": {
            "name": "wf_interface_patch", "description": "写接口卡（question/takes/gives/open/limits），写前校验引用与长度。",
            "parameters": {"type": "object", "required": ["interface"], "properties": {
                "interface": {"type": "object"}}}}},
        {"type": "function", "function": {
            "name": "wf_test_register", "description": "登记一条测试定义；给 run_artifact 时同时登记运行与实际 verdict（pass/fail/...）。",
            "parameters": {"type": "object", "required": ["proposition", "metric", "predicted_direction",
                                                              "failure_condition", "validity_scope"],
                           "properties": {
                               "title": {"type": "string"}, "proposition": {"type": "string"},
                               "metric": {"type": "string"}, "predicted_direction": {"type": "string"},
                               "failure_condition": {"type": "string"}, "validity_scope": {"type": "string"},
                               "strength": {"type": "string", "enum": ["V1", "V2", "V3"]},
                               "control": {"type": "string"}, "intervention": {"type": "string"},
                               "run_artifact": {"type": "string", "description": "运行证据文件（仓库相对路径）"},
                               "verdict": {"type": "string", "enum": ["pass", "fail", "unresolved", "not_applicable"]},
                               "measured": {"type": "object"}, "notes": {"type": "string"},
                               "environment_hash": {"type": "string"}}}}},
        {"type": "function", "function": {
            "name": "wf_gate", "description": "跑本层核对，返回 verdict 与 blocks。用于收工前自检。",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
    ]
    if spec.get("write_roots"):
        schemas.append({"type": "function", "function": {
            "name": "repo_write", "description": "写仓库内文件（仅本层允许目录）：" + "、".join(spec["write_roots"]) + "。",
            "parameters": {"type": "object", "required": ["path", "content"], "properties": {
                "path": {"type": "string"}, "content": {"type": "string"}}}}})
    if spec.get("commands"):
        schemas.append({"type": "function", "function": {
            "name": "run_command", "description": "运行本层的构建/仿真/测试命令（受控，不是通用 bash；拒绝签名、作废、平台配置写入）。",
            "parameters": {"type": "object", "required": ["command"], "properties": {
                "command": {"type": "string"}, "timeout_s": {"type": "integer"}}}}})
    if "contract" in spec.get("extra", ()):
        schemas.append({"type": "function", "function": {
            "name": "wf_contract_patch", "description": "写 L4 的 contract_manifest（manifest_id / interfaces / validity_scope 等）。",
            "parameters": {"type": "object", "required": ["patch"], "properties": {
                "patch": {"type": "object"}}}}})
    if layer == "L1":
        # L1 只允许简报/案例输入，不给通用仓库读；这是 L1 红线的工具面实现。
        schemas = [s for s in schemas if s["function"]["name"] not in ("repo_list", "repo_read", "repo_grep")]
    schemas.append(FINISH_TOOL)
    return schemas


TOOL_SCHEMAS = _tool_schemas("L1")


class GraphState(TypedDict):
    messages: list            # OpenAI 风格消息，dict
    calls: list               # 本轮 assistant 的 tool_calls
    step: int
    summary: str
    status: str               # running / finished / max_turns / stopped
    node: str


def _w():
    return wf.WF(wf.default_root())


def _pkg_env(w, pkg):
    for _, e in w.find_packages():
        if e["identity"]["package_id"] == pkg:
            return e
    raise RuntimeError(f"找不到层包：{pkg}")


def _case_root(case):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", case or ""):
        raise ValueError("非法案例名")
    hits = sorted(Path(wf.REPO_ROOT).glob(f"missions/*/cases/{case}"))
    if not hits:
        raise ValueError(f"找不到案例：{case}")
    return hits[0]


def _text(path, limit=4000):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError as e:
        return f"（读不到：{e}）"


def _tool_brief(job, args, w):
    try:
        text = v1._brief(w, job["package_id"])
    except Exception as e:  # noqa: BLE001
        return {"error": f"简报读取失败：{e}"}
    return {"brief": v1._truncate(text, 7000)}


def _tool_previous(job, args, w):
    env = _pkg_env(w, job["package_id"])
    layer = env["identity"]["layer_id"]
    task, it = w.iteration_of(env)
    if not task or not it:
        return {"note": "本包没有归到任务/迭代"}
    cands = []
    for _, e in w.find_packages():
        if e["identity"]["package_id"] == job["package_id"]:
            continue
        if e["identity"]["layer_id"] != layer:
            continue
        t2, it2 = w.iteration_of(e)
        if t2 and t2["task_id"] == task["task_id"] and it2 and it2["iteration_id"] == it["iteration_id"]:
            cands.append(e)
    if not cands:
        return {"note": f"同一轮没有上一版 {layer} 包"}
    e = sorted(cands, key=lambda x: x.get("provenance", {}).get("updated_at", ""), reverse=True)[0]
    arts = w.artifacts()
    if layer == "L1":
        p = e.get("payload") or {}
        refs = []
        for ref in p.get("baseline_refs") or []:
            a = arts.get(ref)
            refs.append({"ref": ref, "registered": bool(a),
                         "relpath": (a or {}).get("relpath", ""),
                         "type": (a or {}).get("artifact_type", "")})
        return {
            "package_id": e["identity"]["package_id"], "lifecycle": e["lifecycle"]["status"],
            "benchmarks": p.get("benchmarks"), "metric_definitions": p.get("metric_definitions"),
            "baseline_refs": p.get("baseline_refs"), "baseline_refs_info": refs,
            "data_quality": p.get("data_quality"), "interface": e.get("interface"),
        }
    return {
        "package_id": e["identity"]["package_id"], "lifecycle": e["lifecycle"]["status"],
        "payload": e.get("payload"), "contract_manifest": e.get("contract_manifest"),
        "interface": e.get("interface"), "artifacts": e.get("artifacts"),
        "validation": e.get("validation"),
    }


def _previous_jobs(job, limit=2):
    """同包最近完成的作业（不含当前 job）。用于把上一轮经验注入新作业，而不是重放旧对话。"""
    terminal = {"passed", "needs_decision", "gate_failed", "error", "stopped", "max_turns"}
    out = []
    try:
        paths = sorted(v1.JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return out
    for p in paths:
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if j.get("job_id") == job.get("job_id") or j.get("package_id") != job.get("package_id"):
            continue
        if j.get("status") not in terminal:
            continue
        out.append(j)
        if len(out) >= limit:
            break
    return out


def _job_events(jid):
    p = v1._log_path(jid)
    events = []
    if not p.exists():
        return events
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return events


def _experience(w, job, limit_jobs=2):
    """把上一轮作业压成可执行的经验：结论、失败命令、关键 Thought、工具序列、遗留证据。"""
    prev = _previous_jobs(job, limit_jobs)
    if not prev:
        return {"jobs": [], "text": ""}
    env = _pkg_env(w, job["package_id"])
    blocks = []
    metas = []
    for pj in prev:
        events = _job_events(pj["job_id"])
        calls = [e for e in events if e.get("event") == "tool_call"]
        results = [e for e in events if e.get("event") == "tool_result"]
        failures, last_call = [], {}
        for e in events:
            if e.get("event") == "tool_call":
                last_call = e
            elif e.get("event") == "tool_result":
                raw = e.get("result") or ""
                try:
                    r = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    r = {}
                bad = bool(r.get("error")) or (isinstance(r.get("exit_code"), int) and r["exit_code"] != 0)
                if bad:
                    name = e.get("name") or last_call.get("name", "")
                    args = str(last_call.get("args") or "")[:180]
                    why = str(r.get("error") or f"exit_code={r.get('exit_code')}")[:260]
                    failures.append(f"{name} {args} → {why}")
        thoughts = [str(e.get("text") or "") for e in events if e.get("event") == "thought"]
        gate = next((e for e in reversed(events) if e.get("event") == "gate"), {})
        metas.append({
            "job_id": pj.get("job_id"), "status": pj.get("status"),
            "turns": pj.get("turns"), "gate_verdict": pj.get("gate_verdict"),
            "summary": pj.get("summary", ""), "tool_calls": len(calls),
            "failures": failures[-8:], "thoughts": thoughts[-8:],
        })
        lines = [
            f"### 上一轮作业 {pj.get('job_id')}（{pj.get('status')} / "
            f"{pj.get('turns')} 步 / gate={pj.get('gate_verdict') or '—'}）",
            f"结论摘要：{v1._truncate(str(pj.get('summary') or '（无总结）'), 1400)}",
            "工具使用：" + "、".join(f"{n}×{c}" for n, c in
                                    sorted({n: sum(1 for e in calls if e.get('name') == n)
                                            for n in {e.get('name') for e in calls}}.items())) if calls else "（无）",
        ]
        if failures:
            lines.append("失败/环境阻塞（不要原样重复）：\n" + "\n".join(f"- {x}" for x in failures[-8:]))
        if thoughts:
            lines.append("关键判断（上一轮 Thought 尾部）：\n" + "\n".join(f"- {v1._truncate(t, 260)}"
                                                                      for t in thoughts[-6:]))
        if gate:
            lines.append(f"上一轮核对：{gate.get('verdict')}；报告：{v1._truncate(str(gate.get('report') or ''), 600)}")
        blocks.append("\n".join(lines))
    artifacts = env.get("artifacts") or []
    validation = env.get("validation") or {}
    text = (
        "平台已把上一轮作业压成下面的经验。先读它，复用已确认事实与已有工件；"
        "不要重复失败的命令；仍要自己核对当前包状态。"
        "注意：旧记录里的 harness/命令策略问题可能已被平台修复，先按当前 environment/prompt "
        "重试最小命令，不要把旧失败当永久结论。\n\n" +
        "\n\n".join(blocks) +
        f"\n\n当前包遗留：工件 {len(artifacts)} 个；"
        f"测试结果 {len(validation.get('test_result_refs') or [])} 条；"
        f"lifecycle={env.get('lifecycle', {}).get('status')}。"
    )
    return {"jobs": metas, "text": v1._truncate(text, 9000)}


def _tool_experience(job, args, w):
    return _experience(w, job, limit_jobs=int(args.get("limit") or 2))


def _tool_benchmarks(job, args, w):
    out = []
    for name, rec in w.benchmarks().items():
        out.append({"name": name, "kind": rec.get("kind", ""),
                    "artifacts": len(rec.get("artifact_refs") or [])})
    return {"benchmarks": out[:80]}


def _tool_case_list(job, args, w):
    cases = sorted({p.name for p in Path(wf.REPO_ROOT).glob("missions/*/cases/*") if p.is_dir()})
    return {"cases": cases}


def _tool_case_inspect(job, args, w):
    root = _case_root(str(args.get("case") or ""))
    visible = [p.name for p in sorted(root.iterdir())
               if not ("golden" in p.name.lower() or "result" in p.name.lower()
                       or p.name.lower() == "case.json" or "patch" in p.name.lower())]
    out = {"case": root.name, "files": visible[:50]}
    # 任务输入红线：只给 base_commit 检出 + issue 文本；golden patch / RESULT 是判分材料，不暴露。
    for name in ("meta.txt", "issue.txt"):
        p = root / name
        if p.is_file():
            out[name] = _text(p, 2500)
    return out


def _slice_text(text, args):
    lines = str(text or "").splitlines()
    total = len(lines)
    off = min(max(0, int(args.get("offset") or 0)), total)
    lim = int(args.get("limit") or 0)
    shown = lines[off: off + lim if lim else None]
    return "\n".join(shown), total, off


def _tool_case_read(job, args, w):
    root = _case_root(str(args.get("case") or ""))
    rel = str(args.get("path") or "")
    low = rel.lower()
    if ("golden" in low or "result" in low or low.endswith("case.json")
            or "patch" in low or "/.git" in low):
        return {"error": "这是判分材料，不在 agent 可读范围（只允许 base/issue/meta 等输入）"}
    p = (root / rel).resolve()
    if root.resolve() not in p.parents and p != root.resolve():
        return {"error": "只能读案例目录内的文件"}
    if not p.is_file():
        return {"error": "不是文件"}
    text, total, off = _slice_text(_text(p, 20000), args)
    return {"path": str(p.relative_to(wf.REPO_ROOT)), "text": text,
            "total_lines": total, "offset": off}


def _tool_environment(job, args, w):
    env = _pkg_env(w, job["package_id"])
    ns = (env["identity"].get("domain_pack") or "").removeprefix("DOMAIN-") or "general"
    p = Path(wf.REPO_ROOT) / "domains" / ns / "environment.md"
    return {"domain": ns, "text": _text(p, 5000) if p.exists() else "（没有 environment.md）"}


def _repo_path(w, raw):
    p = (Path(w.root) / str(raw or ".")).resolve()
    root = Path(w.root).resolve()
    if p != root and root not in p.parents:
        raise ValueError(f"路径须在仓库内：{raw}")
    return p, root


def _read_forbidden(layer, rel):
    low = rel.lower()
    if (low.startswith((".git/", ".venv", "platform/runtime/", "platform/store/", "node_modules/"))
            or "/.git/" in low or (low.endswith("config.json") and "platform/runtime" in low)
            or low.endswith("case.json")):
        return True
    # 判分材料和 golden 补丁：所有层都不读 missions 下的 golden/RESULT/patch；
    # L5+ 可以读自己在 designs 下写的 golden 参考模型。
    if rel.startswith("missions/") and ("golden" in low or "result" in low or "patch" in low):
        return True
    if layer in ("L1", "L2") and "golden" in low:
        return True
    return False


def _tool_repo_list(job, args, w):
    if job["layer"] == "L1":
        return {"error": "L1 不允许通用仓库浏览；用 wf_brief / case_inspect / case_read"}
    p, root = _repo_path(w, args.get("path") or ".")
    if not p.is_dir():
        return {"error": f"不是目录：{args.get('path')}"}
    rel = p.relative_to(root).as_posix()
    entries = []
    for x in sorted(p.iterdir(), key=lambda z: (not z.is_dir(), z.name))[:300]:
        child = (rel + "/" + x.name).lstrip("./")
        if _read_forbidden(job["layer"], child):
            continue
        entries.append({"name": x.name, "dir": x.is_dir()})
    return {"path": rel, "entries": entries}


def _tool_repo_read(job, args, w):
    if job["layer"] == "L1":
        return {"error": "L1 不允许通用仓库读；用 wf_brief / case_inspect / case_read"}
    p, root = _repo_path(w, args.get("path") or "")
    if not p.is_file():
        return {"error": f"不是文件：{args.get('path')}"}
    rel = p.relative_to(root).as_posix()
    if _read_forbidden(job["layer"], rel):
        return {"error": "这个路径不在 agent 可读范围（golden/RESULT/case.json/平台配置/store）"}
    text, total, off = _slice_text(_text(p, 30000), args)
    return {"path": rel, "text": text, "total_lines": total, "offset": off}


def _tool_repo_grep(job, args, w):
    if job["layer"] == "L1":
        return {"error": "L1 不允许通用仓库搜索；用 wf_brief / case_inspect / case_read"}
    pattern = str(args.get("pattern") or "")
    if not pattern:
        return {"error": "pattern 为空"}
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return {"error": f"正则不合法：{e}"}
    base, root = _repo_path(w, args.get("path") or ".")
    include = str(args.get("include") or "*")
    matches = []
    for p in sorted(base.rglob("*")):
        if len(matches) >= 80:
            break
        if not p.is_file() or not fnmatch.fnmatch(p.name, include):
            continue
        rel = p.relative_to(root).as_posix()
        if _read_forbidden(job["layer"], rel):
            continue
        try:
            if p.stat().st_size > 2_000_000:
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    matches.append(f"{rel}:{i}:{line[:240]}")
                    if len(matches) >= 80:
                        break
        except OSError:
            continue
    return {"matches": matches, "count": len(matches), "truncated": len(matches) >= 80}


def _write_root_ok(layer, rel):
    roots = LAYER_SPEC.get(layer, {}).get("write_roots") or ()
    return any(rel == r.rstrip("/") or rel.startswith(r) for r in roots)


def _tool_repo_write(job, args, w):
    layer = job["layer"]
    p, root = _repo_path(w, args.get("path") or "")
    rel = p.relative_to(root).as_posix()
    if not _write_root_ok(layer, rel):
        return {"error": f"{layer} 不允许写这个路径；只允许：{LAYER_SPEC.get(layer, {}).get('write_roots')}"}
    if _read_forbidden(job["layer"], rel):
        return {"error": "这个路径不可写（平台配置/store/密钥）"}
    content = args.get("content")
    if not isinstance(content, str):
        return {"error": "content 须是字符串"}
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(p) + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(p)
    return {"path": rel, "bytes": len(content.encode("utf-8"))}


def _tool_run_command(job, args, w):
    layer = job["layer"]
    if not LAYER_SPEC.get(layer, {}).get("commands"):
        return {"error": f"{layer} 不开放命令执行；用结构化工具有 payload/interface/contract/测试登记"}
    cmd = str(args.get("command") or "").strip()
    if not cmd:
        return {"error": "command 为空"}
    if DENY_COMMAND.search(cmd):
        return {"error": "命令被策略拒绝（签名/作废/平台配置/危险删除类操作不允许）"}
    env = dict(os.environ)
    env["WF_ROOT"] = str(w.root)
    env["WF_ACTOR"] = f"layer:{layer}"
    timeout = max(10, min(int(args.get("timeout_s") or 180), 600))
    try:
        p = subprocess.run(cmd, shell=True, cwd=str(w.root), env=env, text=True,
                           capture_output=True, timeout=timeout)
        out = p.stdout or ""
        err = p.stderr or ""
        return {"exit_code": p.returncode,
                "stdout": v1._truncate(out, 6000),
                "stderr": v1._truncate(err, 4000)}
    except subprocess.TimeoutExpired:
        return {"exit_code": 124, "error": f"命令超时（>{timeout}s）"}


def _tool_contract(job, args, w):
    if job["layer"] != "L4":
        return {"error": "只有 L4 用 wf_contract_patch"}
    patch = args.get("patch")
    if not isinstance(patch, dict):
        return {"error": "patch 须是对象"}
    path, env = w.resolve_pkg(job["package_id"])
    trial = json.loads(json.dumps(env, ensure_ascii=False))
    cm = trial.get("contract_manifest") or {}
    cm.update(patch)
    trial["contract_manifest"] = cm
    w.update_envelope(path, lambda e: e.__setitem__("contract_manifest", cm),
                      agent=job.get("agent", ""))
    return {"ok": True, "contract_keys": sorted(cm.keys())}


def _tool_test_register(job, args, w):
    layer = job["layer"]
    required = ("proposition", "metric", "predicted_direction", "failure_condition", "validity_scope")
    missing = [k for k in required if not str(args.get(k) or "").strip()]
    if missing:
        return {"error": f"测试定义缺字段：{missing}"}
    defn = {
        "test_id": str(args.get("test_id") or
                       f"TST-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:4]}"),
        "schema_version": "2.0",
        "title": str(args.get("title") or ""),
        "layer_id": layer,
        "proposition": str(args.get("proposition")),
        "metric": str(args.get("metric")),
        "predicted_direction": str(args.get("predicted_direction")),
        "failure_condition": str(args.get("failure_condition")),
        "validity_scope": str(args.get("validity_scope")),
        "strength": str(args.get("strength") or "V1"),
        "control": str(args.get("control") or ""),
        "intervention": str(args.get("intervention") or ""),
        "package_ref": job["package_id"],
        "created_by": job.get("agent", ""),
    }
    try:
        td = w.add_test_def(defn)
    except SystemExit as e:
        return {"error": str(e)}
    env_path, env = w.resolve_pkg(job["package_id"])

    def _refs(e):
        refs = e["validation"].setdefault("test_definition_refs", [])
        if td["test_id"] not in refs:
            refs.append(td["test_id"])
    w.update_envelope(env_path, _refs, agent=job.get("agent", ""))
    w.add_edge_safe(job["package_id"], td["test_id"], "validated_by",
                    note=defn.get("title", ""), agent=job.get("agent", ""))
    out = {"test_id": td["test_id"]}
    run_artifact = str(args.get("run_artifact") or "").strip()
    if run_artifact:
        p, root = _repo_path(w, run_artifact)
        if not p.is_file():
            return {**out, "error": f"run_artifact 不存在：{run_artifact}"}
        rel = p.relative_to(root).as_posix()
        if _read_forbidden(job["layer"], rel):
            return {**out, "error": "run_artifact 路径不可登记"}
        art = w.register_artifact(str(p), artifact_type="stats",
                                  note=f"run evidence for {td['test_id']}",
                                  agent=job.get("agent", ""))

        def _attach_run(e):
            arts = e.setdefault("artifacts", [])
            if not any(a.get("artifact_id") == art["artifact_id"] for a in arts):
                arts.append({"artifact_id": art["artifact_id"], "artifact_type": "stats",
                             "content_hash": art.get("content_hash", ""),
                             "uri": art.get("relpath", ""), "role": "test-run"})
        w.update_envelope(env_path, _attach_run, agent=job.get("agent", ""))
        run_id = f"RUN-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:4]}"
        w.add_run({"run_id": run_id, "test_ref": td["test_id"], "run_status": "completed",
                   "environment_hash": str(args.get("environment_hash") or "unspecified"),
                   "started_at": wf.now(), "executor": job.get("agent", ""),
                   "raw_result_artifact": art["artifact_id"],
                   "package_ref": job["package_id"]})
        w.add_edge_safe(run_id, td["test_id"], "derived_from", agent=job.get("agent", ""))
        result_id = f"RES-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:4]}"
        w.add_result({"result_id": result_id, "run_ref": run_id,
                      "verdict": str(args.get("verdict") or "pass"),
                      "measured_effect": args.get("measured") if isinstance(args.get("measured"), dict) else {},
                      "notes": str(args.get("notes") or "")})
        w.add_edge_safe(result_id, run_id, "derived_from", agent=job.get("agent", ""))

        def _refs2(e):
            for k, r in (("test_run_refs", run_id), ("test_result_refs", result_id)):
                refs = e["validation"].setdefault(k, [])
                if r not in refs:
                    refs.append(r)
        w.update_envelope(env_path, _refs2, agent=job.get("agent", ""))
        out.update(run_id=run_id, result_id=result_id, artifact_id=art["artifact_id"])
    return out



def _allowed_artifact_path(layer, relpath):
    rel = str(relpath or "").lstrip("./")
    roots = ARTIFACT_ROOTS.get(layer, ARTIFACT_ROOTS["L1"])
    if not rel.startswith(roots):
        return False
    low = rel.lower()
    if low.endswith("case.json") or "/.git/" in low or low.startswith(("platform/", "tasks/")):
        return False
    if layer == "L1":
        if "golden" in low or "result" in low or "patch" in low:
            return False
    # 判分材料/黄金补丁即使放在 missions 下也不登记；designs 下允许 golden 参考模型（L5 需要）。
    if rel.startswith("missions/") and ("golden" in low or "result" in low or "patch" in low):
        return False
    return True


ARTIFACT_TYPES = {"rtl_source", "kernel", "config", "script", "stats", "log", "report",
                  "spec_doc", "data", "binary", "manifest", "test_artifact",
                  "environment", "other"}
ARTIFACT_ALIASES = {
    "design": "rtl_source", "rtl": "rtl_source", "source": "rtl_source",
    "stimulus": "test_artifact", "test": "test_artifact", "testbench": "test_artifact",
    "issue": "report", "issue-report": "report", "issue_report": "report",
    "baseline": "data", "measurement": "data",
    "doc": "spec_doc", "document": "spec_doc", "spec": "spec_doc",
}


def _guess_artifact_type(relpath):
    ext = Path(relpath).suffix.lower()
    return {".sv": "rtl_source", ".v": "rtl_source", ".vh": "rtl_source", ".svh": "rtl_source",
            ".s": "kernel", ".asm": "kernel", ".c": "kernel", ".cc": "kernel", ".cpp": "kernel",
            ".py": "script", ".sh": "script", ".json": "config", ".yaml": "config",
            ".yml": "config", ".toml": "config", ".csv": "data", ".tsv": "data",
            ".txt": "data", ".log": "log", ".md": "spec_doc",
            }.get(ext, "other")


def _normalize_artifact_type(raw, relpath):
    at = ARTIFACT_ALIASES.get(str(raw or "").strip(), str(raw or "").strip())
    if at in ARTIFACT_TYPES:
        return at
    # 模型给了未知类型时按扩展名兜底，避免非法 enum 写进 envelope 后 gate 才失败。
    guessed = _guess_artifact_type(relpath)
    return guessed if guessed in ARTIFACT_TYPES else "other"


def _ensure_design_spec(w, job):
    """新包脚手架常把 design_spec.intent 留空；L1 运行时从任务目标补齐，避免 gate 卡在意图为空。"""
    path, env = w.resolve_pkg(job["package_id"])
    if (env.get("design_spec") or {}).get("intent"):
        return
    task, _ = w.iteration_of(env)
    intent = ""
    if task:
        intent = str((task.get("goal") or {}).get("statement") or task.get("title") or "").strip()
    intent = intent or str(env.get("identity", {}).get("title") or "").strip() or f"{job['layer']} 层工作"
    w.update_envelope(path, lambda e: e.setdefault("design_spec", {}).__setitem__("intent", intent),
                      agent=job.get("agent", ""))
    v1._log(job, "design_spec_intent", intent=v1._truncate(intent, 300))


def _tool_register(job, args, w):
    raw = str(args.get("path") or "")
    p = (Path(w.root) / raw).resolve()
    root = Path(w.root).resolve()
    if p != root and root not in p.parents:
        return {"error": "路径须在仓库内"}
    if not p.is_file():
        return {"error": f"不是文件：{raw}"}
    rel = p.relative_to(root).as_posix()
    if not _allowed_artifact_path(job["layer"], rel):
        roots = " / ".join(ARTIFACT_ROOTS.get(job["layer"], ()))
        return {"error": f"{job['layer']} 只允许登记 {roots} 下的文件；"
                         "不允许 golden 判分材料、case.json、平台配置或 store"}
    at = _normalize_artifact_type(args.get("artifact_type"), rel)
    rec = w.register_artifact(str(p), artifact_type=at,
                              agent=job.get("agent", ""), role="evidence")
    path, _ = w.resolve_pkg(job["package_id"])

    def _attach(e):
        arts = e.setdefault("artifacts", [])
        cur = next((a for a in arts if a.get("artifact_id") == rec["artifact_id"]), None)
        if cur:
            # 同一内容可能早先以非法/旧别名登记过；envelope 里用归一后的合法类型，
            # 否则 gate 会在 schema enum 上失败，而模型无法修改已登记记录。
            cur["artifact_type"] = at
        else:
            arts.append({"artifact_id": rec["artifact_id"], "artifact_type": at,
                         "content_hash": rec.get("content_hash", ""),
                         "uri": rec.get("relpath", ""), "role": "evidence"})
    w.update_envelope(path, _attach, agent=job.get("agent", ""))
    return {"artifact_id": rec["artifact_id"], "content_hash": rec.get("content_hash", ""),
            "relpath": rec.get("relpath", ""), "artifact_type": at}


def _deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def _tool_payload(job, args, w):
    patch = args.get("patch")
    if not isinstance(patch, dict):
        return {"error": "patch 须是对象"}
    layer = job["layer"]
    if layer == "L1":
        bad = set(patch) - ALLOWED_PAYLOAD_KEYS
        if bad:
            return {"error": f"L1 不允许这些 payload 字段：{sorted(bad)}"}
    elif "contract_manifest" in patch:
        return {"error": "contract_manifest 不在 payload 里；L4 用 wf_contract_patch"}
    path, env = w.resolve_pkg(job["package_id"])
    trial = json.loads(json.dumps(env, ensure_ascii=False))
    _deep_merge(trial.setdefault("payload", {}), patch)
    if layer == "L1":
        errs = wf._rule_L1(w, trial)
        if errs:
            return {"error": "L1 payload 校验未通过", "details": errs}
        refs = trial["payload"].get("baseline_refs") or []
        arts = w.artifacts()
        for ref in refs:
            if not str(ref).startswith("ART-"):
                return {"error": f"baseline_refs 里 {ref!r} 不是登记工件（ART-…）；先把基线文件登记为工件"}
            a = arts.get(ref)
            if not a:
                return {"error": f"baseline_refs 里 {ref} 不在登记册；先 wf_register_artifact"}
            if not _allowed_artifact_path("L1", a.get("relpath", "")):
                return {"error": f"baseline_refs 里 {ref} 指向平台/判分文件（{a.get('relpath')}），不能当基线"}
    warnings = wf.LAYER_RULES[layer](w, trial) if layer in wf.LAYER_RULES else []
    w.update_envelope(path, lambda e: _deep_merge(e.setdefault("payload", {}), patch),
                      agent=job.get("agent", ""))
    return {"ok": True, "payload_keys": sorted(trial["payload"].keys()),
            "warnings": warnings}


def _tool_interface(job, args, w):
    iface = args.get("interface") if isinstance(args.get("interface"), dict) else args.get("patch")
    if not isinstance(iface, dict):
        return {"error": "interface 须是对象（给 interface 或 patch 字段）"}
    iface = json.loads(json.dumps(iface, ensure_ascii=False))
    # 常见别名归一：statement→text、G1→S1、downstream→next、limits 单字符串→列表。
    # 这些是工具层的容错，最终仍按 layer_interface schema 校验。
    if isinstance(iface.get("limits"), str):
        iface["limits"] = [iface["limits"]]
    if isinstance(iface.get("gives"), list):
        for i, g in enumerate(iface["gives"]):
            if not isinstance(g, dict):
                continue
            if not g.get("text") and g.get("statement"):
                g["text"] = g["statement"]
            if isinstance(g.get("id"), str) and re.fullmatch(r"[Gg]\d+", g["id"]):
                g["id"] = f"S{i + 1}"
    if isinstance(iface.get("open"), list):
        for o in iface["open"]:
            if isinstance(o, dict) and o.get("ask") in ("downstream", "next_layer"):
                o["ask"] = "next"
    # 常见错误：takes 写成字符串列表。只有一个上游时能无损补成 {from, statement}；
    # 多个上游时无法判断指向，交给校验报错。
    takes = iface.get("takes")
    if isinstance(takes, list) and takes and all(isinstance(x, str) for x in takes):
        try:
            pe = _pkg_env(w, job["package_id"])
            parents = list((pe.get("provenance") or {}).get("parent_package_refs") or [])
        except Exception:  # noqa: BLE001
            parents = []
        if len(parents) == 1:
            iface["takes"] = [{"from": parents[0], "statement": x} for x in takes]
    path, env = w.resolve_pkg(job["package_id"])
    trial = json.loads(json.dumps(env, ensure_ascii=False))
    trial["interface"] = iface
    errs = wf.interface_errors(w, trial)
    if errs:
        return {"error": "接口卡校验未通过", "details": errs}
    w.update_envelope(path, lambda e: e.__setitem__("interface", iface),
                      agent=job.get("agent", ""))
    return {"ok": True}


def _tool_gate(job, args, w):
    verdict, report = w.gate(job["package_id"], verbose=False)
    return {"verdict": verdict, "blocks": (report or {}).get("blocks", []),
            "warns": (report or {}).get("warns", [])}


TOOL_FUNCS = {
    "wf_brief": _tool_brief, "wf_previous": _tool_previous, "l1_previous": _tool_previous,
    "wf_experience": _tool_experience,
    "benchmark_list": _tool_benchmarks, "case_list": _tool_case_list,
    "case_inspect": _tool_case_inspect, "case_read": _tool_case_read,
    "environment_read": _tool_environment, "wf_register_artifact": _tool_register,
    "wf_payload_patch": _tool_payload, "wf_interface_patch": _tool_interface,
    "wf_contract_patch": _tool_contract, "wf_test_register": _tool_test_register,
    "repo_list": _tool_repo_list, "repo_read": _tool_repo_read, "repo_grep": _tool_repo_grep,
    "repo_write": _tool_repo_write, "run_command": _tool_run_command,
    "wf_gate": _tool_gate,
}


LAYER_HINTS = {
    "L1": "只做观测锚定：选 benchmark、定义指标、给基线；不要读 RTL 源码、不要修 RTL、不要做归因或方案。",
    "L2": "把候选瓶颈写成可证伪命题，设计受控实验并排除替代解释；归因三态如实。要登记 ≥1 条 pass 测试。",
    "L3": "把已确认瓶颈转成候选方案；每个候选写 targets_bottleneck 和 expected_mechanism；判据只锚瓶颈特征量。",
    "L4": "把方案固化成 contract_manifest（接口/时序/语义）和带 verification 三态的资源参数。",
    "L5": "按契约实现 RTL 与独立 testbench；先写 golden/参考模型再写实现；登记 RTL/testbench 和 pass 测试。",
    "L6": "把 RTL 映射成仿真对象与事件；写 simobject_mappings；跑协议/背压/复位/顺序测试并登记 pass 测试。",
    "L7": "造可复现实验包：entry_refs/scripts、environment、逐字可跑的 reproduce_command；跑测量并登记 pass 测试。",
    "L8": "把测量收成结论：model_causal_status、hardware_evidence_status、comparisons.frame、validity_scope；登记 pass 测试。",
}


def _system_prompt(w, job):
    env = _pkg_env(w, job["package_id"])
    layer = env["identity"]["layer_id"]
    agent = wf.agent_of(layer)
    guide = wf.LAYER_GUIDE.get(layer, {})
    brief = v1._truncate(v1._brief(w, job["package_id"]), 7000)
    experience = _experience(w, job, limit_jobs=2)["text"]
    body = v1._truncate(agent.get("body", ""), 4500)
    spec = LAYER_SPEC.get(layer, LAYER_SPEC["L1"])
    read_tools = ("wf_brief / wf_previous / case_inspect / environment_read" if layer == "L1"
                  else "wf_brief / wf_previous / repo_read / repo_grep / case_inspect / environment_read")
    rules = "\n".join(f"- {x}" for x in wf.LAYER_RULE_DOC.get(layer, []))
    deliverables = " / ".join(
        x[0] if isinstance(x, (list, tuple)) and x else str(x)
        for x in guide.get("deliverables", []))
    extras = []
    if spec.get("write_roots"):
        extras.append(f"- repo_write 只能写：{'、'.join(spec['write_roots'])}；写完用 wf_register_artifact 登记。")
    if spec.get("commands"):
        extras.append("- run_command 只用于构建/仿真/测试；拒绝签名、作废、平台配置/商店写入。")
    if "contract" in spec.get("extra", ()):
        extras.append("- L4 的 contract_manifest 用 wf_contract_patch 写，不放在 payload。")
    if layer in wf.REQUIRES_TEST_EVIDENCE:
        extras.append("- 本层必须有登记在册且 verdict=pass 的 RES 测试；用 wf_test_register（给 run_artifact）。")
    else:
        extras.append("- 可选：有真实测量就用 wf_test_register 登记测试与 RES。")
    return f"""你是「{agent.get('name', layer)}」（{layer} 层 {guide.get('name', '')}），在平台内部做本层工作。

## 本层定义（唯一职责来源）
{body}

## 这一层回答的问题
{guide.get('question', '')}
{LAYER_HINTS.get(layer, '')}

## 必须交出
{deliverables}
{rules}

## 工具规则
- 先用 {read_tools} 了解上下文；只读，不要重复读同一文件；需要更细的上一轮经验时调用 wf_experience。
- **硬性节奏（优先于“再读一点”）**：
  1. 前 3 步只读关键材料，确定本层要写的字段；
  2. 第 4 步前先 `wf_payload_patch` 写最小草稿（缺的证据留空/标 assumed），第 5 步前写接口卡草稿；
  3. 之后再做实验/补证据，每 2–3 次只读或命令就回到 payload/interface 更新，不要连续读超过 5 次；
  4. 最后留 2 步给 `wf_gate` + `finish`。
- 用 wf_payload_patch 写 payload；用 wf_interface_patch 写接口卡；用 wf_gate 自检；最后 finish(summary)。
{chr(10).join(extras)}
- 接口卡格式必须是：
  gives: [{{"id":"S1","kind":"root_cause|measurement|exclusion|scope|decision|other","text":"一句话结论（≤140字）","truth":"deduced|assumed|observed","evidence":["ART-…"]}}]
  takes: [{{"from":"PKG-上游包","statement":"用了上游哪条结论"}}]
  open:  [{{"question":"…","ask":"next|human"}}]
  limits: ["每条不超过 100 字"]
  字段是 `text` 不是 `statement`，编号必须是 S1/S2…；takes 必须是对象，不是字符串；
  truth=observed 必须挂登记在册且 pass 的 RES。

## 真值与边界
- 只做本层，不改上游层包、不改别人的层包，不替人签字。
- 不要声称没做过的测量；数字带 F0/F1/F2/F3 基架标签。
- 有真实文件/测量先 wf_register_artifact，再在 payload/接口卡里引用 ART。
- 每批工具后系统会让你写 1–2 句 Thought；按 Thought → Action → Observation 继续。
- 预算有限：先用少量只读材料确认关键事实，尽快写出本层必需的 payload/contract/接口卡并跑一次 gate，
  再补证据；不要只读不写、不要读完整个仓库。
- 同一个 harness/命令连续失败 3 次就停下：把“环境阻塞/缺依赖”写进 payload 的 open/notes，finish；
  不要用 30 步反复修环境。
- finish 前至少跑一次 wf_gate；如果 verdict 不是 pass，在 summary 里如实写还差什么。
- 目标是本层最小可交付，不要无限探索。

## 上一轮同包作业经验（新作业，不是继续旧对话）
{experience or '（这是该包第一次派发，没有上一轮作业经验）'}

## 开工简报
{brief}
"""


def _clean_thought(text):
    """Reflect 节点只收自然语言；模型偶尔会附上 <tool_call>，不能让它进入下一轮上下文。"""
    text = re.sub(r"<tool_call>.*?(?:</tool_call>|$)", "", str(text or ""), flags=re.S)
    text = re.sub(r"</?tool_call>", "", text)
    return text.strip()


def _compact_messages(messages, budget=60000, keep_recent=12):
    """上下文治理：总字符超预算时，把较老的 tool/user/assistant 正文压成短摘要。
    保留最近若干条与所有 tool_call 结构；压缩只改 content，不破坏消息顺序/配对。"""
    total = sum(len(str(m.get("content") or "")) for m in messages)
    if total <= budget:
        return messages, 0
    out, saved = [], 0
    cutoff = max(0, len(messages) - keep_recent)
    for i, m in enumerate(messages):
        m2 = dict(m)
        content = str(m2.get("content") or "")
        if i < cutoff and m2.get("role") != "system" and len(content) > 600:
            m2["content"] = content[:600] + "…（平台压缩：旧工具输出已截断，原始记录见作业日志）"
            saved += len(content) - len(m2["content"])
        out.append(m2)
    return out, saved


def _execute(job, name, args, w):
    if name == "finish":
        return None
    fn = TOOL_FUNCS.get(name)
    if not fn:
        return {"error": f"未知工具：{name}"}
    try:
        return fn(job, args, w)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def _build_graph(job, cfg, w, max_steps):
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.sqlite import SqliteSaver

    def prepare(state):
        node = "prepare"
        layer = job["layer"]
        v1._log(job, "graph_node", node=node)
        v1._log(job, "plan", graph="prepare → agent ⇄ tools → reflect → agent → finish",
                checklist=wf.LAYER_RULE_DOC.get(layer, []))
        try:
            _ensure_design_spec(w, job)
        except Exception as e:  # noqa: BLE001
            v1._log(job, "design_spec_error", error=f"{type(e).__name__}: {e}")
        try:
            ex = _experience(w, job, limit_jobs=2)
            if ex.get("jobs"):
                v1._log(job, "experience_loaded",
                        jobs=[x.get("job_id") for x in ex["jobs"]],
                        chars=len(ex.get("text") or ""))
        except Exception as e:  # noqa: BLE001
            v1._log(job, "experience_error", error=f"{type(e).__name__}: {e}")
        sys_prompt = _system_prompt(w, job)
        return {"messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"开始 {layer}。先读简报与上下文，然后用领域工具写本层交付，跑 gate，finish。"},
        ], "calls": [], "step": 0, "summary": "", "status": "running", "node": node}

    def agent(state):
        node = "agent"
        job["node"] = node
        v1._log(job, "graph_node", node=node)
        if v1.MANAGER.stop:
            v1._log(job, "stopped")
            return {**state, "status": "stopped", "node": node}
        step = state["step"] + 1
        job["turns"] = step
        v1._save_job(job)
        v1._log(job, "turn", turn=step)
        final = step >= max_steps
        messages = list(state["messages"])
        done_names = {m.get("name") for m in messages if m.get("role") == "tool"}
        force = None
        if step in (5, 9) and "wf_payload_patch" not in done_names:
            messages.append({"role": "user", "content":
                             "你还没有写 payload。现在必须先用 wf_payload_patch 写一个最小草稿"
                             "（缺的证据可以留空/标 assumed），不要继续只读/只跑命令。"})
            force = {"type": "function", "function": {"name": "wf_payload_patch"}}
        elif (step == 9 and "wf_payload_patch" in done_names
              and "wf_interface_patch" not in done_names):
            messages.append({"role": "user", "content":
                             "payload 已有草稿，现在必须先用 wf_interface_patch 写接口卡草稿"
                             "（question + gives S1…，takes 必须是 {from,statement} 对象，单条 ≤140 字）。"})
            force = {"type": "function", "function": {"name": "wf_interface_patch"}}
        if step == max(1, max_steps - 8):
            messages.append({"role": "user", "content":
                             f"提醒：只剩 {max_steps - step + 1} 步模型调用。停止扩大探索，"
                             "优先补齐交付、跑 gate、finish。"})
        messages, saved_chars = _compact_messages(messages)
        if saved_chars:
            v1._log(job, "context_compacted", saved_chars=saved_chars,
                    messages=len(messages))
        if final:
            v1._log(job, "budget_final", turn=step)
            messages.append({"role": "user", "content":
                             "最后一步：不要再调用其他工具。总结做了什么、改了什么、跑了什么核对、"
                             "还差什么，然后调用 finish。"})
            msg = v1._chat(cfg, messages, tools=[FINISH_TOOL],
                           tool_choice={"type": "function", "function": {"name": "finish"}})
        else:
            msg = v1._chat(cfg, messages, tools=_tool_schemas(job["layer"]),
                           tool_choice=force) if force else \
                v1._chat(cfg, messages, tools=_tool_schemas(job["layer"]))
        content = msg.get("content") or ""
        calls = msg.get("tool_calls") or []
        if content:
            v1._log(job, "assistant", text=v1._truncate(content, 3000))
        assistant = {"role": "assistant", "content": content}
        if calls:
            assistant["tool_calls"] = calls
        messages.append(assistant)
        if not calls:
            summary = content
            v1._log(job, "final_text", text=v1._truncate(content, 3000))
            return {**state, "messages": messages, "calls": [], "step": step,
                    "summary": summary, "status": "max_turns" if final else "finished", "node": node}
        return {**state, "messages": messages, "calls": calls, "step": step,
                "summary": state["summary"], "status": "running", "node": node}

    def tools(state):
        node = "tools"
        job["node"] = node
        v1._log(job, "graph_node", node=node)
        messages = list(state["messages"])
        done = False
        summary = state["summary"]
        for call in state["calls"]:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            v1._log(job, "tool_call", name=name,
                    args=v1._truncate(json.dumps(args, ensure_ascii=False), 1500))
            if name == "finish":
                # finish 前没自检：平台替它跑一次 gate 并写进对话，不让模型反复 finish 空转。
                if not any(m.get("role") == "tool" and m.get("name") == "wf_gate" for m in messages):
                    gate_result = _tool_gate(job, {}, w)
                    v1._log(job, "tool_result", name="wf_gate",
                            result=v1._truncate(json.dumps(gate_result, ensure_ascii=False), 3000))
                    messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                     "name": "wf_gate",
                                     "content": json.dumps(gate_result, ensure_ascii=False)})
                summary = str(args.get("summary") or "")
                done = True
                v1._log(job, "finish", summary=v1._truncate(summary, 3000))
                continue
            result = _execute(job, name, args, w)
            v1._log(job, "tool_result", name=name,
                    result=v1._truncate(json.dumps(result, ensure_ascii=False), 3000))
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "name": name,
                             "content": json.dumps(result, ensure_ascii=False)})
        return {**state, "messages": messages, "calls": [],
                "summary": summary, "status": "finished" if done else state["status"], "node": node}

    def reflect(state):
        node = "reflect"
        job["node"] = node
        v1._log(job, "graph_node", node=node)
        if state["status"] != "running":
            return {**state, "node": node}
        prompt_messages = list(state["messages"])
        prompt_messages.append({"role": "user", "content":
                                "请只输出 1–2 句自然语言思考：你观察到什么、下一步做什么。"
                                "这是思考阶段，不要输出工具调用、函数名、参数、代码或 XML/JSON。"})
        try:
            msg = v1._chat(cfg, prompt_messages, tools=[])
            text = _clean_thought(msg.get("content") or "")
        except Exception as e:  # noqa: BLE001
            v1._log(job, "thought_error", error=f"{type(e).__name__}: {e}")
            return {**state, "node": node}
        if text:
            v1._log(job, "thought", text=v1._truncate(text, 1200))
            # 思考只作为“系统记录”回注，不冒充 assistant 已执行的动作；
            # 否则模型会把思考里的拟调用当成已完成，下一轮直接结束。
            messages = list(state["messages"])
            messages.append({"role": "user", "content":
                             f"（系统记录：你刚才的思考是「{text}」。这只是思考，不是已执行动作；"
                             "下一步必须实际调用所需工具，不要只复述这段思考。）"})
            return {**state, "messages": messages, "node": node}
        return {**state, "node": node}

    def finish(state):
        job["node"] = "finish"
        v1._log(job, "graph_node", node="finish")
        return {**state, "status": state["status"] if state["status"] != "running" else "finished",
                "node": "finish"}

    def after_agent(state):
        if state["status"] in ("stopped", "finished", "max_turns"):
            return "finish"
        return "tools" if state["calls"] else "finish"

    def after_tools(state):
        if state["status"] in ("finished", "stopped", "max_turns"):
            return "finish"
        return "reflect"

    g = StateGraph(GraphState)
    g.add_node("prepare", prepare)
    g.add_node("agent", agent)
    g.add_node("tools", tools)
    g.add_node("reflect", reflect)
    g.add_node("finish", finish)
    g.add_edge(START, "prepare")
    g.add_edge("prepare", "agent")
    g.add_conditional_edges("agent", after_agent, {"tools": "tools", "finish": "finish"})
    g.add_conditional_edges("tools", after_tools, {"reflect": "reflect", "finish": "finish"})
    g.add_edge("reflect", "agent")
    g.add_edge("finish", END)
    v1.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(v1.RUNTIME_DIR / "checkpoints.sqlite"), check_same_thread=False)
    saver = SqliteSaver(conn)
    return g.compile(checkpointer=saver), conn


def run_agent(job, cfg):
    """给 v1 JobManager 调用的 L1–L8 runner。返回 finished / max_turns / stopped。"""
    try:
        import langgraph  # noqa: F401
    except ImportError as e:
        raise RuntimeError("未安装 LangGraph；请用 .venv-runtime/bin/python 启动，或 pip install "
                           "langgraph langgraph-checkpoint-sqlite") from e
    max_steps = max(1, int(cfg.get("max_turns") or 20))
    job["engine"] = "langgraph"
    job["node"] = "prepare"
    started = time.time()
    w = _w()
    app, conn = _build_graph(job, cfg, w, max_steps)
    initial = {"messages": [], "calls": [], "step": 0, "summary": "",
               "status": "running", "node": "prepare"}
    try:
        out = app.invoke(initial, {"configurable": {"thread_id": job["job_id"]},
                                   "recursion_limit": max(50, max_steps * 4 + 20)})
    finally:
        conn.close()
    job["summary"] = out.get("summary", "")
    job["turns"] = out.get("step", 0)
    job["node"] = out.get("node", "finish")
    job["duration_s"] = round(time.time() - started, 1)
    v1._save_job(job)
    status = out.get("status", "finished")
    return status if status in ("finished", "max_turns", "stopped") else "finished"
