#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wf — 通用微架构混合仿真平台的流程 CLI（MVP-A → v2 层总线版）。

实现《通用微架构混合仿真Agent设计文档_v2.md》的：
  §5  统一数据与契约模型（层包信封 / 测试三分离 / 决策记录 / 证据 DAG）
  §6  验证门禁（层规则 + 高权重人类决策 + 测试证据）
  §4.4 上游变更失效传播
  §9  平台自身故障注入自检（wf selftest）
  v2  层间数据总线（layer_bus：标准类型+自由编排+场景模板+归因分级）

约定：
  - 机器可读记录一律 JSON（本工具 stdlib-only，不依赖 PyYAML/jsonschema）。
  - store 追加式（jsonl）+ 内容寻址对象库；已登记工件不可改，内容变化=新 ART id。
  - 层包 envelope.json 是人可读、机可校验的核心文件；payload 字段由层规则表检查。
  - reviewer=pending-user 的决策视为"待人类批准"，高权重层 gate 报 needs_decision。

用法示例：
  wf init-store
  wf new-domain --name sme2 --description "SME2 vs SVE2"
  wf new-pkg --domain sme2 --layer L2 --slug int4-advantage --title "..." --owner zcode-glm --intent "..."
  wf register-artifact fs/out/scan/table.csv --type stats
  wf test-register --pkg PKG-L7-sme2-fs-scan --file def.json --run-artifact stats.txt --verdict pass
  wf decide --pkg ... --type ratify --summary "一句话：决定了什么" --rationale "详细依据" \
      --confidence medium --reviewer pending-user
  wf gate PKG-L2-sme2-int4-advantage
  wf status --domain sme2
  wf selftest

任务与迭代（纪律六：无章程不立项）：
  wf bench-import --domain sme2 --name vp-int4-attention --kind 虚拟原型 fs/user/vp_int4_attn.S
  wf task-new --domain sme2 --title "..." --goal "..." --criterion "..." --benchmark vp-int4-attention --create
  wf iter-new --task TASK-sme2-xxx --reason "上一轮 L8 的 Mission Review 结论" --create
  wf task-list / task-show / task-seal / iter-seal / iter-abort
  wf next --iteration ITER-sme2-xxx-2
"""

import argparse
import collections
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- 常量

SCHEMA_VERSION = "2.0"
COPY_LIMIT = 8 * 1024 * 1024  # 小于该值的工件默认复制入 store

LAYER_DIR = {
    "L1": "L1_observation", "L2": "L2_attribution", "L3": "L3_solution",
    "L4": "L4_contract", "L5": "L5_rtl", "L6": "L6_integration",
    "L7": "L7_experiment", "L8": "L8_analysis",
}
LAYER_NAME = {
    "L1": "observation", "L2": "attribution", "L3": "solution",
    "L4": "contract", "L5": "rtl", "L6": "integration",
    "L7": "experiment", "L8": "analysis",
}
# 设计文档 §4.2 人类权重：高权重层禁止无记录自动通过
HIGH_HUMAN = {"L1", "L2", "L3", "L4", "L6", "L8"}
# 这些层必须挂接至少一条 verdict=pass 的测试结果才能 gate 通过
REQUIRES_TEST_EVIDENCE = {"L2", "L5", "L6", "L7", "L8"}
EDGE_TYPES = ["derived_from", "generated_from", "validated_by", "decided_by",
              "depends_on", "invalidated_by", "contradicted_by", "supersedes"]
BAD_LIFECYCLE = {"invalidated", "superseded"}
VERDICTS_PASS = "pass"
# 设计文档 §2.4/§4.2：只有这三类决策构成"人类批准"；reject/defer 不放行
APPROVING_TYPES = {"select", "ratify", "override"}
# 设计文档 §6.2：各层推荐验证强度（vcheck 用）
REC_STRENGTH = {"L1": "V1", "L2": "V2", "L3": "V1", "L4": "V2",
                "L5": "V2", "L6": "V2", "L7": "V1", "L8": "V2"}
STRENGTH_ORDER = ["V0", "V1", "V2", "V3"]

# 任务与迭代（纪律六：任务章程 + Ship Gate）。任务长什么样只由流程模板决定（没有任务类型、没有成功判据）。
TASK_LIVE = ("draft", "active")           # 进行中；sealed / aborted 默认折叠，不进前沿
DECISION_SUMMARY_MAX = 80                 # 决策「一句话结论」的长度上限
BENCH_IMPORT_MAX_FILES = 20000            # 题集目录入册的文件数上限
FLOW_TEMPLATES = ("microarch_optimize", "bug_fix_simple", "bug_fix_with_attribution",
                  "comparative_study", "component_design", "batch_calibration", "benchmark_only")
TEMPLATE_LABEL = {
    "microarch_optimize": "完整八层（观测 → 归因 → 方案 → 契约 → RTL → 集成 → 实验 → 结论）",
    "bug_fix_simple": "缺陷修复直达（观测 → RTL 修复 → 结论）",
    "bug_fix_with_attribution": "缺陷修复加归因（观测 → 归因 → RTL 修复 → 结论）",
    "comparative_study": "对比研究（观测 → 归因 → 实验 → 结论）",
    "component_design": "新组件设计（方案 → 契约 → RTL → 集成 → 实验）",
    "batch_calibration": "批量标定（共享观测 → 逐例修复 → 汇总结论）",
    "benchmark_only": "仅 benchmark（不走层：只有目标、benchmark 与人的决定）",
}
# 历史章程里可能还带着的几项（不再作为输入项，只在展示时附上存档）
GOAL_FIELDS = (("budget", "预算"), ("tradeoff_policy", "权衡政策"), ("stop_rule", "停止规则"))


def now():
    return datetime.now().isoformat(timespec="seconds")


def default_root():
    env = os.environ.get("WF_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[2]


def _parse_ts(s):
    """ISO 时间串 → datetime；解析不了返回 None（比较时跳过）。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


_HEAD_SPLIT = re.compile(r"[：:—]|；")


def decision_headline(d, lookup=None):
    """决策的一句话标题 → (标题, 是否自动摘取)。
    有 summary 用 summary；历史决策没有，就摘取正文第一个分句。
    「批准/驳回 DEC-…」这类处理记录跟随被处理的那条的标题（lookup: 决策 id → 记录）。"""
    text = (d.get("rationale") or "").strip()
    s = (d.get("summary") or "").strip()
    if s:
        return s, False
    m = re.match(r"^(批准|驳回)\s+(DEC-[A-Za-z0-9-]+)", text)
    if m and lookup:
        target = lookup(m.group(2))
        if target and target.get("decision_id") != d.get("decision_id"):
            return f"{m.group(1)}：{decision_headline(target)[0]}", True
    clause = _HEAD_SPLIT.split(text, maxsplit=1)[0].strip()
    return (clause if len(clause) >= 6 else text[:50].strip()), True


def _load_layer_bus():
    import importlib.util as ilu
    spec = ilu.spec_from_file_location("layer_bus",
                                        str(Path(__file__).parent / "layer_bus.py"))
    lb = ilu.module_from_spec(spec)
    spec.loader.exec_module(lb)
    return lb


# ---------------------------------------------------------------- 精简 JSON Schema 校验器
# 支持：type/const/enum/pattern/minLength/minItems/required/properties/items
# 不支持：$ref/oneOf/anyOf/additionalProperties —— schema 文件须自包含。

_TYPE_CHECK = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _check_type(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    fn = _TYPE_CHECK.get(t)
    return fn(v) if fn else True


def validate(instance, schema, path="$", errors=None):
    """返回错误列表（空 = 通过）。"""
    if errors is None:
        errors = []
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_check_type(instance, t) for t in types):
            errors.append(f"{path}: 期望 type={schema['type']}，实际 {type(instance).__name__}")
            return errors
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: 期望 const={schema['const']!r}，实际 {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} 不在 enum {schema['enum']}")
    if isinstance(instance, str):
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: {instance!r} 不匹配 pattern {schema['pattern']}")
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: 长度 {len(instance)} < minLength {schema['minLength']}")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: {len(instance)} 项 < minItems {schema['minItems']}")
        if "items" in schema:
            for i, item in enumerate(instance):
                validate(item, schema["items"], f"{path}[{i}]", errors)
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: 缺必填字段 {key!r}")
        for key, sub in schema.get("properties", {}).items():
            if key in instance:
                validate(instance[key], sub, f"{path}.{key}", errors)
    return errors


# ---------------------------------------------------------------- 存储

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    """原子写：tmp + rename。"""
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def append_jsonl(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def load_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def sha256_file(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class WF:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.platform = self.root / "platform"
        self.schemas = self.platform / "schemas"
        self.store = self.platform / "store"
        self.objects = self.store / "objects"
        self.domains = self.root / "domains"
        self.idx_artifacts = self.store / "index" / "artifacts.jsonl"
        self.idx_decisions = self.store / "index" / "decisions.jsonl"
        self.idx_tests = self.store / "index" / "tests.jsonl"
        self.idx_runs = self.store / "index" / "runs.jsonl"
        self.idx_results = self.store / "index" / "results.jsonl"
        self.idx_evolutions = self.store / "index" / "evolutions.jsonl"
        self.idx_benchmarks = self.store / "index" / "benchmarks.jsonl"
        self.tasks_dir = self.root / "tasks"
        self.graph_path = self.store / "graph.jsonl"

    # ---------- 索引读取 ----------
    def artifacts(self):
        return {r["artifact_id"]: r for r in load_jsonl(self.idx_artifacts)}

    def decisions(self):
        return {r["decision_id"]: r for r in load_jsonl(self.idx_decisions)}

    def tests(self):
        return {r["test_id"]: r for r in load_jsonl(self.idx_tests)}

    def runs(self):
        return {r["run_id"]: r for r in load_jsonl(self.idx_runs)}

    def results(self):
        return {r["result_id"]: r for r in load_jsonl(self.idx_results)}

    def evolutions(self):
        return {r["candidate_id"]: r for r in load_jsonl(self.idx_evolutions)}

    def benchmarks(self):
        """全局 benchmark 清单：{name: 最新一条记录}。追加式索引，同名取最后一条。"""
        out = {}
        for r in load_jsonl(self.idx_benchmarks):
            if r.get("name"):
                out[r["name"]] = r
        return out

    def graph(self):
        return load_jsonl(self.graph_path)

    def schema(self, name):
        return load_json(self.schemas / f"{name}.json")

    # ---------- Schema Registry（§3.1） ----------
    def registry(self):
        p = self.schemas / "registry.json"
        return load_json(p) if p.exists() else None

    def check_version(self, kind, ver):
        """返回 None=通过；字符串=拒绝原因。无 registry 文件时放行（沙箱/迁移期）。"""
        reg = self.registry()
        if not reg:
            return None
        info = reg.get("formats", {}).get(kind)
        if not info or ver not in info.get("versions", []):
            return f"schema 版本 {ver!r} 未在 registry 登记（{kind}，合法：{info.get('versions') if info else '格式未注册'}）"
        return None

    # ---------- 节点注册 ----------
    def register_artifact(self, path, artifact_type="other", copy=None, note="",
                          role="", agent=""):
        path = Path(path).resolve()
        if not path.is_file():
            raise SystemExit(f"错误：工件不存在或不是文件：{path}")
        h = sha256_file(path)
        art_id = f"ART-{h[:12]}"
        existing = self.artifacts().get(art_id)
        if existing:
            print(f"{art_id}  （内容已登记，幂等返回） {path}")
            return existing
        size = path.stat().st_size
        store_path = None
        do_copy = (size <= COPY_LIMIT) if copy is None else copy
        if do_copy:
            dest_dir = self.objects / h[:16]
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / path.name
            if not dest.exists():
                shutil.copy2(path, dest)
            store_path = str(dest.relative_to(self.root))
        rec = {
            "artifact_id": art_id,
            "artifact_type": artifact_type,
            "content_hash": h,
            "size": size,
            "mtime": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            "uri": str(path),
            "relpath": str(path.relative_to(self.root)) if self.root in path.parents else None,
            "store_path": store_path,
            "note": note,
            "role": role,
            "registered_at": now(),
            "agent": agent,
        }
        append_jsonl(self.idx_artifacts, rec)
        print(f"{art_id}  {path}")
        return rec

    def register_manifest(self, name, manifest, uri, note="", agent=""):
        """题集目录入册：文件清单（相对路径 + 每个文件的 sha256）作为一个工件存进 store，
        目录本身不复制——目录内容变了就是另一份清单、另一个 ART id。"""
        data = json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8")
        h = hashlib.sha256(data).hexdigest()
        art_id = f"ART-{h[:12]}"
        existing = self.artifacts().get(art_id)
        if existing:
            print(f"{art_id}  （内容已登记，幂等返回） {uri}")
            return existing
        dest_dir = self.objects / h[:16]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{name}.manifest.json"
        if not dest.exists():
            dest.write_bytes(data)
        rec = {
            "artifact_id": art_id, "artifact_type": "manifest",
            "content_hash": h, "size": len(data), "mtime": now(),
            "uri": str(uri), "relpath": None, "store_path": str(dest.relative_to(self.root)),
            "note": note, "role": "benchmark-manifest",
            "registered_at": now(), "agent": agent,
            "file_count": manifest.get("file_count"), "total_size": manifest.get("total_size"),
        }
        append_jsonl(self.idx_artifacts, rec)
        print(f"{art_id}  {uri}（题集目录，{manifest.get('file_count')} 个文件）")
        return rec

    # ---------- 任务与迭代（纪律六：无章程不立项） ----------
    def tasks(self, namespace=None):
        """{task_id: 记录}。每个任务一个文件：tasks/<task_id>.json（不再按领域存放；
        领域概念已停用，旧位置 domains/<域>/tasks/ 下的记录仍会被读到）。
        namespace 给出时只看层包存放在该位置的旧任务（兼容 --domain 参数）。"""
        out = {}
        paths = sorted(self.tasks_dir.glob("*.json")) + sorted(self.domains.glob("*/tasks/*.json"))
        for p in paths:
            try:
                rec = load_json(p)
            except (OSError, ValueError) as e:
                raise SystemExit(f"错误：任务记录读不出来：{p}（{e}）")
            tid = rec.get("task_id")
            if not tid:
                raise SystemExit(f"错误：任务记录缺 task_id：{p}")
            if tid in out:
                continue      # 新位置的记录排在前面、优先；旧位置的残留副本跳过
            if namespace and rec.get("namespace") != namespace:
                continue
            out[tid] = rec
        return out

    def save_task(self, rec):
        errs = validate(rec, self.schema("task_record"))
        ver_err = self.check_version("task_record", rec["schema_version"])
        if ver_err:
            errs.append(f"schema-registry: {ver_err}")
        if errs:
            raise SystemExit("任务记录不符合 schema：\n  " + "\n  ".join(errs))
        rec["updated_at"] = now()
        path = self.tasks_dir / f"{rec['task_id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        save_json(path, rec)
        return rec

    def find_task(self, ref):
        tasks = self.tasks()
        if ref in tasks:
            return tasks[ref]
        key = ref.lower().removeprefix("task-")
        cand = [t for tid, t in tasks.items() if tid.lower().removeprefix("task-") == key]
        if len(cand) == 1:
            return cand[0]
        known = f"（可选：{', '.join(sorted(tasks))}）" if tasks else ""
        raise SystemExit(f"错误：找不到任务：{ref}{known}")

    def find_iteration(self, ref):
        """(任务, 迭代)，二者取自同一份任务记录——调用方改迭代状态后 save_task 才落得下去。
        ref = ITER-… 或 TASK-…/<seq>；只给任务则取它最新的一轮。找不到就报错退出。"""
        tasks = self.tasks()
        if ref in tasks:
            its = sorted(tasks[ref].get("iterations", []), key=lambda i: i.get("seq", 0))
            if not its:
                raise SystemExit(f"错误：{ref} 没有迭代")
            return tasks[ref], its[-1]
        for t in tasks.values():
            for it in t.get("iterations", []):
                if it.get("iteration_id") == ref:
                    return t, it
        m = re.match(r"^(TASK-[A-Za-z0-9._-]+)[/#](\d+)$", ref)
        if m and m.group(1) in tasks:
            for it in tasks[m.group(1)].get("iterations", []):
                if str(it.get("seq")) == m.group(2):
                    return tasks[m.group(1)], it
        raise SystemExit(f"错误：找不到迭代：{ref}")

    def iteration_of(self, env, tasks=None):
        """包属于哪个 (任务, 迭代)。优先级：迭代里显式列出 → 包上的 provenance.iteration_id →
        按创建时间落进当时进行中的迭代。兜底只认「层包存放在同一位置」的进行中任务：
        已封板 / 已终止的任务不再接收新包，之后建的包归「未归迭代」并浮到默认视图。"""
        tasks = self.tasks() if tasks is None else tasks
        pid = env["identity"]["package_id"]
        ns = env["identity"]["domain_pack"].removeprefix("DOMAIN-")
        for t in tasks.values():
            for it in t.get("iterations", []):
                if pid in it.get("package_ids", []):
                    return t, it
        stamp = env.get("provenance", {}).get("iteration_id")
        if stamp:
            for t in tasks.values():
                for it in t.get("iterations", []):
                    if it.get("iteration_id") == stamp:
                        return t, it
        mine = [t for t in tasks.values() if (t.get("namespace") or DEFAULT_NAMESPACE) == ns]
        created = _parse_ts(env.get("provenance", {}).get("created_at"))
        best = None
        if created is not None:
            for t in mine:
                if t.get("status") not in TASK_LIVE:
                    continue
                for it in t.get("iterations", []):
                    start = _parse_ts(it.get("started_at"))
                    if start is not None and start <= created and (best is None or start > best[0]):
                        best = (start, t, it)
        return (best[1], best[2]) if best else (None, None)

    def live_iterations(self, namespace=None):
        """默认视图里的迭代：进行中任务里进行中的迭代，按开始时间升序。"""
        out = []
        for t in self.tasks(namespace).values():
            if t.get("status") in TASK_LIVE:
                out += [(t, it) for it in t.get("iterations", [])
                        if it.get("status") in TASK_LIVE]
        return sorted(out, key=lambda ti: (ti[1].get("started_at") or "", ti[0]["task_id"]))

    def iteration_layers(self, task, it, pkgs=None):
        """本迭代逐层的自有包与沿用包。沿谱系（parent_package_refs）向上找：某层没有自有包时，
        取距离最近的上游包（属于别的迭代）记为沿用——例如迭代 2 的 L4/L5/L6 用的是迭代 1 的。"""
        ns = task.get("namespace")
        if pkgs is None:
            pkgs = self.find_packages(domain=ns) if ns else self.find_packages()
        tasks = self.tasks()
        own, others = [], []
        for _, env in pkgs:
            _, owner = self.iteration_of(env, tasks)
            (own if owner is not None and owner.get("iteration_id") == it["iteration_id"]
             else others).append(env)
        by_id = {e["identity"]["package_id"]: e for e in own + others}
        own_ids = {e["identity"]["package_id"] for e in own}
        dist, queue = {}, []
        for pid in sorted(own_ids):
            dist[pid] = 0
            queue.append(pid)
        while queue:
            cur = queue.pop(0)
            for par in by_id[cur].get("provenance", {}).get("parent_package_refs", []):
                if par in by_id and par not in dist:
                    dist[par] = dist[cur] + 1
                    queue.append(par)
        layers = {L: {"own": [], "reused": []} for L in LAYER_DIR}
        for e in own:
            layers[e["identity"]["layer_id"]]["own"].append(e)
        for L in LAYER_DIR:
            if layers[L]["own"]:
                continue
            cands = [dist[e["identity"]["package_id"]] for e in others
                     if e["identity"]["layer_id"] == L and e["identity"]["package_id"] in dist]
            if cands:
                dmin = min(cands)
                layers[L]["reused"] = sorted(
                    (e for e in others if e["identity"]["layer_id"] == L
                     and dist.get(e["identity"]["package_id"]) == dmin),
                    key=lambda e: e["identity"]["package_id"])
        return layers

    def add_decision(self, decision_type, rationale, confidence, reviewer,
                     selected=None, rejected=None, constraints=None,
                     evidence_refs=None, scope="", agent="",
                     summary="", task_id="", content_sha=""):
        """summary=一句话结论（给界面和审阅者看的标题；rationale 是详细依据）。
        task_id 给出时，决策挂在任务上（章程 / 开迭代 / 封板），而不是某个层包。
        content_sha=做决定时层包接口卡的内容指纹：内容在其后改过，这张回执就显示过期。"""
        summary = (summary or "").strip()
        if len(summary) > DECISION_SUMMARY_MAX:
            raise SystemExit(f"错误：summary 超过 {DECISION_SUMMARY_MAX} 字（现 {len(summary)}）——"
                             "一句话写决定了什么，细节放 rationale")
        dec_id = f"DEC-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
        rec = {
            "decision_id": dec_id, "schema_version": SCHEMA_VERSION,
            "decision_type": decision_type,
            "selected_refs": selected or [], "rejected_refs": rejected or [],
            "constraints": constraints or [],
            "summary": summary,
            "rationale": rationale,
            "evidence_refs": evidence_refs or [],
            "confidence": confidence, "reviewer": reviewer,
            "decided_at": now(), "scope": scope,
        }
        if task_id:
            rec["task_id"] = task_id
        if content_sha:
            rec["content_sha"] = content_sha
        errs = validate(rec, self.schema("decision_record"))
        if errs:
            raise SystemExit("决策记录不符合 schema：\n  " + "\n  ".join(errs))
        append_jsonl(self.idx_decisions, rec)
        return rec

    def add_test_def(self, rec):
        rec.setdefault("schema_version", SCHEMA_VERSION)
        errs = validate(rec, self.schema("test_definition"))
        if errs:
            raise SystemExit("测试定义不符合 schema：\n  " + "\n  ".join(errs))
        if rec["test_id"] in self.tests():
            raise SystemExit(f"错误：test_id 已存在：{rec['test_id']}")
        append_jsonl(self.idx_tests, rec)
        return rec

    def add_run(self, rec):
        rec.setdefault("schema_version", SCHEMA_VERSION)
        errs = validate(rec, self.schema("test_run"))
        if errs:
            raise SystemExit("测试运行不符合 schema：\n  " + "\n  ".join(errs))
        append_jsonl(self.idx_runs, rec)
        return rec

    def add_result(self, rec):
        rec.setdefault("schema_version", SCHEMA_VERSION)
        errs = validate(rec, self.schema("test_result"))
        if errs:
            raise SystemExit("测试结果不符合 schema：\n  " + "\n  ".join(errs))
        append_jsonl(self.idx_results, rec)
        return rec

    def add_edge(self, src, dst, edge_type, note="", agent=""):
        if edge_type not in EDGE_TYPES:
            raise SystemExit(f"错误：未知边类型 {edge_type}（可选：{', '.join(EDGE_TYPES)}）")
        known = self.known_nodes()
        for node in (src, dst):
            if node not in known:
                raise SystemExit(f"错误：节点未注册：{node}")
        rec = {
            "edge_id": f"EDGE-{uuid.uuid4().hex[:12]}",
            "edge_type": edge_type, "from": src, "to": dst,
            "created_at": now(), "agent": agent, "note": note,
        }
        append_jsonl(self.graph_path, rec)
        print(f"{rec['edge_id']}  {src} --{edge_type}--> {dst}")
        return rec

    def known_nodes(self):
        nodes = set()
        for d in (self.idx_artifacts, self.idx_decisions, self.idx_tests,
                  self.idx_runs, self.idx_results, self.idx_evolutions):
            key = {"index/artifacts.jsonl": "artifact_id",
                   "index/decisions.jsonl": "decision_id",
                   "index/tests.jsonl": "test_id",
                   "index/runs.jsonl": "run_id",
                   "index/results.jsonl": "result_id",
                   "index/evolutions.jsonl": "candidate_id"}[str(d.relative_to(self.store))]
            nodes.update(r[key] for r in load_jsonl(d))
        for env_path in self.domains.glob("*/*/*/envelope.json"):
            nodes.add(load_json(env_path)["identity"]["package_id"])
        for pack_path in self.domains.glob("*/pack.json"):
            nodes.add(load_json(pack_path)["identity"]["pack_id"])
        nodes.update(self.tasks().keys())      # 任务可作证据图节点（TASK --decided_by--> DEC）
        return nodes

    # ---------- 层包 ----------
    def find_packages(self, domain=None, layer=None):
        out = []
        for env_path in sorted(self.domains.glob("*/*/*/envelope.json")):
            env = load_json(env_path)
            ident = env["identity"]
            if domain and ident["domain_pack"] != f"DOMAIN-{domain}":
                continue
            if layer and ident["layer_id"] != layer:
                continue
            out.append((env_path, env))
        return out

    def resolve_pkg(self, ref):
        """按 package_id 或路径定位层包。"""
        ref_path = Path(ref)
        if ref_path.name == "envelope.json" and ref_path.exists():
            return ref_path, load_json(ref_path)
        for env_path, env in self.find_packages():
            if env["identity"]["package_id"] == ref:
                return env_path, env
        raise SystemExit(f"错误：找不到层包：{ref}")

    def update_envelope(self, env_path, mutate, agent=""):
        env = load_json(env_path)
        old_status = env["lifecycle"]["status"]
        mutate(env)
        new_status = env["lifecycle"]["status"]
        env["provenance"]["updated_at"] = now()
        if old_status != new_status:
            env["lifecycle"].setdefault("history", []).append(
                {"at": now(), "from": old_status, "to": new_status,
                 "by": agent or "wf", "why": "lifecycle change"})
        save_json(env_path, env)
        return env

    def domain_pack(self, domain):
        p = self.domains / domain / "pack.json"
        if not p.exists():
            raise SystemExit(f"错误：领域包不存在：{p}")
        return load_json(p)

    # ---------- 门禁（§6） ----------
    def gate(self, ref, verbose=True, dry_run=False, override=""):
        """门禁。dry_run=True 只评估不落库；override=署名 时允许解除包自身的
        invalidated/superseded（须由调用方先写入带理由的 override 决策，v2.2 §5.3）。"""
        env_path, env = self.resolve_pkg(ref)
        layer = env["identity"]["layer_id"]
        pkg_id = env["identity"]["package_id"]
        blocks, warns, needs_decision, missing_tests = [], [], False, False
        was_archived = env["lifecycle"]["status"] in BAD_LIFECYCLE

        # 1. schema 校验（含 registry 版本检查）
        errs = validate(env, self.schema("layer_package"))
        blocks += [f"schema: {e}" for e in errs]
        ver_err = self.check_version("layer_package", env.get("identity", {}).get("schema_version", ""))
        if ver_err:
            blocks.append(f"schema-registry: {ver_err}")

        # 2. 工件引用：注册表存在 + 哈希一致（漂移=BLOCK；store 副本优先，否则原 uri）
        arts = self.artifacts()
        hashed = {}
        for a in env.get("artifacts", []):
            aid = a["artifact_id"]
            rec = arts.get(aid)
            if not rec:
                blocks.append(f"artifact: {aid} 未在 store 注册")
                continue
            if rec["content_hash"] != a.get("content_hash"):
                blocks.append(f"artifact: {aid} envelope 哈希与注册表不一致（疑似手改/旧哈希）")
            # 漂移检测：优先 store 副本，其次原 uri；size+mtime 快速路径
            target = None
            if rec.get("store_path"):
                sp = self.root / rec["store_path"]
                if sp.exists():
                    target = sp
            if target is None and rec.get("uri"):
                up = Path(rec["uri"])
                if up.exists():
                    target = up
            if target is None:
                blocks.append(f"artifact: {aid} 文件既不在 store 也不在原路径（证据丢失）")
                continue
            if aid not in hashed:
                hashed[aid] = sha256_file(target)
            if hashed[aid] != rec["content_hash"]:
                blocks.append(f"artifact: {aid} 内容已变化（漂移）——请重新注册新工件并失效下游")

        # 3. 证据/决策/测试引用可解析
        decisions = self.decisions()
        tests, runs, results = self.tests(), self.runs(), self.results()
        for ref_ in env.get("decision_refs", []):
            if ref_ not in decisions:
                blocks.append(f"ref: 决策 {ref_} 不存在")
        v = env.get("validation", {})
        for r in v.get("test_definition_refs", []):
            if r not in tests:
                blocks.append(f"ref: 测试定义 {r} 不存在")
        for r in v.get("test_run_refs", []):
            if r not in runs:
                blocks.append(f"ref: 测试运行 {r} 不存在")
        for r in v.get("test_result_refs", []):
            if r not in results:
                blocks.append(f"ref: 测试结果 {r} 不存在")

        # 4. 上游生命周期（§4.4）
        new_pkg = _is_new_package(env)
        pkg_index = {e["identity"]["package_id"]: e for _, e in self.find_packages()}
        for parent in env.get("provenance", {}).get("parent_package_refs", []):
            p = pkg_index.get(parent)
            if p is None:
                blocks.append(f"upstream: 父包 {parent} 不存在")
            elif p["lifecycle"]["status"] in BAD_LIFECYCLE:
                blocks.append(f"upstream: 父包 {parent} 状态为 {p['lifecycle']['status']}，本包不得通过")
            elif new_pkg and iface_drifted(p):
                # 上游核对通过之后接口卡又改过：它现在交出的内容不是核对过的那份
                blocks.append(f"upstream: 父包 {parent} 核对之后接口卡改过，需要重新核对"
                              "——上游没验证完，下游不能通过")
            elif new_pkg and p["lifecycle"]["status"] != "passed":
                # 新建任务下的层包：上游没验证完，下游不能通过（防偷跑）
                blocks.append(f"upstream: 父包 {parent} 尚未验证（{STATE_WORD.get(p['lifecycle']['status'], p['lifecycle']['status'])}）"
                             "——上游没验证完，下游不能通过")

        # 4b. 包自身已失效/被取代：重新门禁不得解除（§5.3 不得覆盖原始失败证据）。
        #     解除只能走显式覆盖：wf gate <pkg> --override --as <署名> --reason <理由>
        if was_archived:
            if override:
                warns.append(f"lifecycle: 包原状态为 {env['lifecycle']['status']}，"
                             f"本次按 {override} 的署名覆盖重新门禁")
            else:
                blocks.append(
                    f"lifecycle: 包状态为 {env['lifecycle']['status']}，重新门禁不能解除失效/取代"
                    f"（修复后请新建层包；确需恢复用 wf gate --override --as <署名> --reason <理由>）")

        # 5. 层特定规则
        rule_errs = LAYER_RULES.get(layer, lambda w, e: [])(self, env)
        blocks += [f"layer-{layer}: {e}" for e in rule_errs]

        # 5b. 层接口卡（只对新建任务下的层包强制）：交给下游的结论必须成文、限长、可核对；
        #     标「实测」的结论必须指向登记在册且通过的测试结果
        if new_pkg:
            blocks += [f"interface: {e}" for e in interface_errors(self, env)]

        # 6. 高权重层需要已批准的人类决策（§4.2：reject/defer 不构成批准）
        if layer in HIGH_HUMAN:
            decs = [decisions[r] for r in env.get("decision_refs", []) if r in decisions]
            signed = [d for d in decs
                      if d.get("reviewer") not in (None, "", "pending-user")
                      and d.get("decision_type") in APPROVING_TYPES]
            # 新建任务下的层包：签字只对签过的那份内容有效——内容在其后改过，要重签
            stale = [d for d in signed if new_pkg and d.get("content_sha")
                     and d.get("content_sha") != interface_hash(env)]
            approved = [d for d in signed if d not in stale]
            if any(d.get("decision_type") == "reject" and d.get("reviewer") not in (None, "", "pending-user")
                   for d in decs):
                warns.append("存在已签署的 reject 决策——相关候选被驳回，通过前必须复核")
            if stale and not approved:
                needs_decision = True
                warns.append(f"层 {layer} 的签字回执是在内容改动之前签的——内容已变，需要重签")
            if not approved:
                needs_decision = True
                warns.append(f"层 {layer} 需要人签字：还没有人签（或内容改过之后要重签）")

        # 7. 测试证据充分性
        if layer in REQUIRES_TEST_EVIDENCE:
            res_refs = v.get("test_result_refs", [])
            ok = [r for r in res_refs if r in results and results[r]["verdict"] == "pass"]
            if not ok:
                missing_tests = True
                warns.append(f"层 {layer} 需要测试：还没有登记在册且通过的测试结果（当前登记了 {len(res_refs)} 条）")

        # 8. L8 硬件外推门禁（§7.1）
        if layer == "L8":
            claimed = [c for c in env["payload"].get("conclusions", [])
                       if c.get("hardware_evidence_status") == "hardware_supported"]
            if claimed:
                dom = env["identity"]["domain_pack"].replace("DOMAIN-", "", 1)
                calib = self.domain_pack(dom).get("calibration", {})
                if not calib.get("evidence_refs"):
                    blocks.append(
                        f"L8: 结论声明 hardware_supported 但领域包校准证据为空——禁止模型内部因果外推为硬件因果")

        # 汇总
        if blocks:
            verdict = "fail"
        elif needs_decision:
            verdict = "needs_decision"
        elif missing_tests:
            verdict = "unresolved"
        else:
            verdict = VERDICTS_PASS

        status_map = {"fail": "failed", "needs_decision": "needs_decision",
                      "unresolved": "unresolved", "pass": "passed"}
        report = {"verdict": verdict, "blocks": blocks, "warns": warns,
                  "gated_at": now(), "dry_run": dry_run}

        if not dry_run:
            def _mutate(e):
                e["validation"]["aggregate_verdict"] = verdict
                e["validation"]["gated_at"] = report["gated_at"]
                e["validation"]["gate_report"] = (
                    "BLOCK: " + "; ".join(blocks) if blocks else
                    "WARN: " + "; ".join(warns) if warns else "all checks passed")
                # 已失效/被取代且无署名覆盖：保留原状态，只记录本次门禁结论
                if not (was_archived and not override):
                    e["lifecycle"]["status"] = status_map[verdict]
                if _is_new_package(e):
                    # 记下这次核对的是哪份接口卡：之后内容改了，「已验证」就不再成立
                    e["validation"]["interface_sha"] = interface_hash(e)
                if override:
                    e["lifecycle"].setdefault("invalidation_reasons", []).append(
                        f"{now()} 署名覆盖解除失效：by={override}")

            self.update_envelope(env_path, _mutate, agent=override or "wf-gate")
        if verbose:
            tag = "[gate-dry]" if dry_run else "[gate]"
            for b in blocks:
                print(f"  BLOCK  {b}")
            for wn in warns:
                print(f"  WARN   {wn}")
            print(f"{tag} {pkg_id} → {verdict} (lifecycle={status_map[verdict]})")
        return verdict, report

    # ---------- 失效传播（§4.4） ----------
    def invalidate(self, ref, reason, agent="", dry_run=False):
        """失效传播。dry_run=True 只计算受影响集合、不写任何东西（供 UI 预览影响范围）。"""
        target = None
        for env_path, env in self.find_packages():
            if env["identity"]["package_id"] == ref:
                target = ("PKG", env_path, env)
                break
        if target is None:
            arts = self.artifacts()
            if ref in arts:
                target = ("ART", None, arts[ref])
        if target is None:
            raise SystemExit(f"错误：失效目标不存在：{ref}")

        # 找出所有（直接或间接）depends_on 目标的包，逆边传播
        edges = self.graph()
        affected, frontier = set(), {ref}
        changed = True
        while changed:
            changed = False
            for e in edges:
                if e["edge_type"] == "depends_on" and e["to"] in frontier and e["from"] not in affected:
                    affected.add(e["from"])
                    frontier.add(e["from"])
                    changed = True
        # depends_on 之外的隐式依赖：envelope 里的 parent_package_refs
        pkg_index = {env["identity"]["package_id"]: env for _, env in self.find_packages()}
        changed = True
        while changed:
            changed = False
            for pid, p in pkg_index.items():
                if pid in affected:
                    continue
                for parent in p.get("provenance", {}).get("parent_package_refs", []):
                    if parent in affected or parent == ref:
                        affected.add(pid)
                        changed = True

        invalidated = sorted({pid for pid in (affected | {ref})
                              if pid in pkg_index or pid == ref})
        if dry_run:
            return {"ref": ref, "affected": invalidated, "dry_run": True}

        print(f"失效根因：{ref} —— {reason}")
        if not affected:
            print("（无下游包受影响）")
        for env_path, env in self.find_packages():
            pid = env["identity"]["package_id"]
            if pid in affected or pid == ref:
                def _mutate(e):
                    e["lifecycle"]["status"] = "invalidated"
                    e["lifecycle"].setdefault("invalidation_reasons", []).append(
                        f"{now()} 上游/自身失效：{reason}（根：{ref}）")
                self.update_envelope(env_path, _mutate, agent=agent or "wf")
                self.add_edge_safe(pid, ref, "invalidated_by", note=reason, agent=agent)
                print(f"  invalidated: {pid}")
        return {"ref": ref, "affected": invalidated}

    def add_edge_safe(self, src, dst, edge_type, note="", agent=""):
        try:
            self.add_edge(src, dst, edge_type, note=note, agent=agent)
        except SystemExit as e:
            if "节点未注册" in str(e):
                append_jsonl(self.graph_path, {
                    "edge_id": f"EDGE-{uuid.uuid4().hex[:12]}",
                    "edge_type": edge_type, "from": src, "to": dst,
                    "created_at": now(), "agent": agent, "note": note})

    # ---------- 决策队列管理（§3.1 人类决策管理） ----------
    def pending_decisions(self):
        """reviewer=pending-user、未被 superseded，且仍被存活包或任务引用的决策。
        挂在任务上的章程 / 开迭代决策不挂在任何包上，但同样是待人类批准的事项。"""
        superseded = {e["to"] for e in self.graph() if e["edge_type"] == "supersedes"}
        live_refs = set()
        for _, env in self.find_packages():
            live_refs.update(env.get("decision_refs", []))
        for t in self.tasks().values():
            live_refs.add(t.get("charter_decision_id"))
            live_refs.update(it.get("entry_decision_id") for it in t.get("iterations", []))
        live_refs.discard(None)
        live_refs.discard("")
        return [d for d in self.decisions().values()
                if d.get("reviewer") == "pending-user"
                and d["decision_id"] not in superseded
                and d["decision_id"] in live_refs]

    def resolve_decision(self, dec_id, user, reject=False, reason=""):
        """批准/驳回一条待批决策：记录不可变——追加新决策 + supersedes 边，不覆盖历史。"""
        d = self.decisions().get(dec_id)
        if d is None:
            raise SystemExit(f"错误：决策不存在：{dec_id}")
        if d.get("reviewer") != "pending-user":
            raise SystemExit(f"错误：{dec_id} 不在待批队列（reviewer={d.get('reviewer')!r}）")
        if dec_id in {e["to"] for e in self.graph() if e["edge_type"] == "supersedes"}:
            raise SystemExit(f"错误：{dec_id} 已被处理过（存在 supersedes 边），不能重复批准/驳回")
        user = user.strip() if isinstance(user, str) else ""
        # 署名即批准主体：不能为空，也不能冒用待批标记（否则"批准"不构成批准，原决策却被顶掉）
        if not user or user == "pending-user":
            raise SystemExit("错误：批准/驳回必须署名，且署名不能是 pending-user")
        verb = "驳回" if reject else "批准"
        if reject:
            dtype, rationale = "reject", (reason or f"驳回 {dec_id}")
        else:
            dtype, rationale = "ratify", reason or f"批准 {dec_id}：{d.get('rationale', '')[:80]}"
        # 一句话结论跟随被处理的那条：审阅时先看到「批准了什么」，不用回去翻 ID
        summary = f"{verb}：{decision_headline(d, self.decisions().get)[0]}"[:DECISION_SUMMARY_MAX]
        new = self.add_decision(dtype, rationale, "high", user, summary=summary,
                                evidence_refs=[dec_id], scope=d.get("scope", ""),
                                task_id=d.get("task_id", ""), content_sha=d.get("content_sha", ""))
        self.add_edge_safe(new["decision_id"], dec_id, "supersedes",
                           note="驳回" if reject else "批准", agent=user)
        affected = []
        for env_path, env in self.find_packages():
            if dec_id in env.get("decision_refs", []):
                nid = new["decision_id"]

                def _m(e, nid=nid):
                    if nid not in e["decision_refs"]:
                        e["decision_refs"].append(nid)
                self.update_envelope(env_path, _m, agent=user)
                affected.append(env["identity"]["package_id"])
        self._apply_task_decision(dec_id, reject)
        return new, affected

    def _apply_task_decision(self, dec_id, reject):
        """章程获批 → 任务转 active；被驳回 → 任务终止。随章程一起建的首轮迭代没有单独的开迭代决策，
        跟着任务一起流转；之后各轮有自己的开迭代决策，按它各自流转。"""
        task = self.tasks().get(self.decisions().get(dec_id, {}).get("task_id"))
        if not task:
            return
        changed = False
        outcome = "aborted" if reject else "active"
        if dec_id == task.get("charter_decision_id") and task.get("status") == "draft":
            task["status"] = outcome
            changed = True
            for it in task.get("iterations", []):
                if it.get("status") == "draft" and not it.get("entry_decision_id"):
                    it["status"] = outcome
        for it in task.get("iterations", []):
            if dec_id == it.get("entry_decision_id") and it.get("status") == "draft":
                it["status"] = outcome
                changed = True
        if changed:
            self.save_task(task)

    # ---------- sweep：漂移自动失效（§4.4 自动触发） ----------
    def sweep(self, domain=None, agent="wf-sweep"):
        """全量哈希巡查：漂移工件 → 直接引用它的包失效（再沿依赖链传播），返回报告。"""
        drifted, missing = [], []
        for aid, rec in sorted(self.artifacts().items()):
            target = None
            if rec.get("store_path"):
                p = self.root / rec["store_path"]
                target = p if p.exists() else None
            if target is None and rec.get("uri"):
                p = Path(rec["uri"])
                target = p if p.exists() else None
            if target is None:
                missing.append(aid)
                continue
            if sha256_file(target) != rec["content_hash"]:
                drifted.append(aid)
        pkg_arts = {}
        for _, env in self.find_packages(domain=domain):
            for a in env.get("artifacts", []):
                pkg_arts.setdefault(a["artifact_id"], []).append(env["identity"]["package_id"])
        invalidated = []
        for aid in drifted:
            for pid in sorted(set(pkg_arts.get(aid, []))):
                self.invalidate(pid, f"sweep：工件 {aid} 内容漂移", agent=agent)
                invalidated.append(pid)
        return {"drifted": drifted, "missing": missing, "invalidated": invalidated}

    # ---------- walk：八层推进纪律（§3.1 Workflow Orchestrator 的 CLI 形态） ----------
    def walk_state(self, domain=None, iteration=None):
        """默认只看进行中的包：未归迭代的包，以及进行中任务里进行中迭代的包。
        已封板 / 已终止的迭代留在 store 里可查，但不再占前沿。指定 iteration 则只看它（含沿用）。"""
        pkgs = self.find_packages(domain=domain)
        total = len(pkgs)
        if iteration:
            task, it = iteration if isinstance(iteration, tuple) else self.find_iteration(iteration)
            layers_env = self.iteration_layers(task, it)
            keep = {e["identity"]["package_id"] for L in layers_env
                    for kind in ("own", "reused") for e in layers_env[L][kind]}
            pkgs = [(p, e) for p, e in pkgs if e["identity"]["package_id"] in keep]
        else:
            tasks = self.tasks(domain)

            def _live(e):
                t, i = self.iteration_of(e, tasks)
                return i is None or (t.get("status") in TASK_LIVE and i.get("status") in TASK_LIVE)
            pkgs = [(p, e) for p, e in pkgs if _live(e)]
        by_id = {e["identity"]["package_id"]: e for _, e in pkgs}
        # 归档包（superseded/invalidated）保留审计但不阻塞推进
        archived = {pid for pid, e in by_id.items()
                    if e["lifecycle"]["status"] in BAD_LIFECYCLE}
        layers = {}
        for _, env in pkgs:
            layers.setdefault(env["identity"]["layer_id"], []).append(env)
        # 层间消费规则（§4.3）：下游 pass 但上游未终验 → 警告
        consumption = []
        for pid, env in by_id.items():
            if pid in archived:
                continue
            if env["validation"].get("aggregate_verdict") == "pass":
                for parent in env.get("provenance", {}).get("parent_package_refs", []):
                    p = by_id.get(parent)
                    if p and p["validation"].get("aggregate_verdict") not in ("pass", "needs_decision"):
                        consumption.append(
                            f"{pid} 已 pass，但上游 {parent} verdict={p['validation'].get('aggregate_verdict')}")
        frontier = None
        for L in ("L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"):
            if any(env["lifecycle"]["status"] != "passed"
                   for env in layers.get(L, [])
                   if env["identity"]["package_id"] not in archived):
                frontier = L
                break
        blocking = []
        if frontier:
            for env in layers.get(frontier, []):
                pid = env["identity"]["package_id"]
                if pid in archived or env["lifecycle"]["status"] == "passed":
                    continue
                blocking.append({"pkg": pid,
                                 "verdict": env["validation"].get("aggregate_verdict"),
                                 "report": env["validation"].get("gate_report", "")})
        # 待批队列按域过滤：--domain cvfpu 时不该列出 sme2 的待批决策
        # 待批：视图内包上的 + 本域任务上的。默认视图还要算上已折叠迭代里的待批——
        # 任务封板了，等人签字的事还在，不能跟着一起藏起来
        live_refs = set()
        for _, env in (pkgs if iteration else self.find_packages(domain=domain)):
            live_refs.update(env.get("decision_refs", []))
        # 挂在任务上的待批事项（章程 / 开迭代）同样算：它们决定任务能不能开工
        for t in self.tasks(domain).values():
            if iteration is None or t["task_id"] == task["task_id"]:
                live_refs.add(t.get("charter_decision_id"))
                live_refs.update(it.get("entry_decision_id") for it in t.get("iterations", []))
        live_refs.discard(None)
        return {"packages": len(pkgs),
                "hidden": total - len(pkgs),
                "archived": sorted(archived),
                "layers": {L: len(layers.get(L, [])) for L in LAYER_DIR},
                "frontier": frontier, "blocking": blocking,
                "consumption_warnings": consumption,
                "pending_decisions": [d["decision_id"] for d in self.pending_decisions()
                                      if d["decision_id"] in live_refs]}

    # ---------- vcheck：验证强度评估（§6.2） ----------
    def vcheck(self, ref):
        _, env = self.resolve_pkg(ref)
        layer = env["identity"]["layer_id"]
        tests, runs, results = self.tests(), self.runs(), self.results()
        strengths = []
        for r in env["validation"].get("test_result_refs", []):
            res = results.get(r)
            if res and res["verdict"] == "pass":
                run = runs.get(res["run_ref"])
                td = tests.get(run["test_ref"]) if run else None
                strengths.append((td or {}).get("strength", "V1"))
        current = max(strengths, key=lambda s: STRENGTH_ORDER.index(s)) if strengths else "V0"
        rec = REC_STRENGTH.get(layer, "V1")
        gaps = []
        if STRENGTH_ORDER.index(current) < STRENGTH_ORDER.index(rec):
            need = {"V1": "补单实现测试（testbench/冒烟）",
                    "V2": "补独立参考模型/随机差分", "V3": "补跨实现对比/故障注入/校准"}[rec]
            gaps.append(f"当前 {current} < 层推荐 {rec}：{need}")
        payload = env.get("payload", {})
        if layer in ("L2", "L5") and not payload.get("reference_model_refs"):
            gaps.append(f"{layer} 未挂 payload.reference_model_refs（独立验证面 V2 的前置条件，§3.3）")
        return {"pkg": env["identity"]["package_id"], "layer": layer,
                "current": current, "recommended": rec, "gaps": gaps}


# ---------------------------------------------------------------- 层接口卡（层间传递信息的唯一格式）
#
# 借 NDF 的四条：人的注意力只放在「目标、交付、证据、人的决定」；可信度来自最小的机械核对
# （内容指纹、登记在册的验证、人签的回执）；「文件存在 / 退出码 0 / 自报通过」都不算成功；
# 给人看的必须是能从头读到尾的文字，并标明出处。

IFACE_LIMITS = {"question": 120, "text": 140, "open": 120, "limit": 100}
IFACE_GIVES_MAX = 5
TRUTH_LABEL = {"observed": "实测", "deduced": "推断", "assumed": "拍定"}
STRENGTH_LABEL = {"V0": "无验证", "V1": "自测", "V2": "对照参考模型", "V3": "跨实现对比"}
ROLE_LABEL = {"profiling": "观测与归因（profiling）", "design": "方案、契约与 RTL（design）",
              "integration": "集成、实验与结论（integration）"}
# 数字产自哪个时延模型（纪律一，与 platform/methodology/layer-discipline.md 一致）
FRAME_LABEL = {"F0": "仿真器默认参数（未校准）", "F1": "契约拍定参数", "F2": "RTL 实测背书",
               "F3": "解析模型推导"}


def _n(v):
    """条目数：列表/字典按长度，其他非空算 1。"""
    if isinstance(v, (list, dict, str)):
        return len(v)
    return 1 if v else 0


def _all_keys(items, keys):
    return (isinstance(items, list) and bool(items)
            and all(isinstance(x, dict) and all(x.get(k) for k in keys) for x in items))


# 每层的固定说明：界面与 wf brief 共用这一份。deliverables 与 _rule_L* 的必填项一一对应。
LAYER_GUIDE = {
    "L1": {"name": "观测", "question": "在什么负载上、量什么、基线是多少",
           "role": "profiling", "skills": ["flow-layer-discipline"],
           "consumes": "任务目标与 benchmark", "produces": "负载、指标定义、基线数据",
           "human": True, "needs_test": False,
           "deliverables": [
               ("负载", lambda e: _n(e["payload"].get("benchmarks")) >= 1),
               ("指标定义", lambda e: _all_keys(e["payload"].get("metric_definitions"),
                                               ("name", "definition", "unit"))),
               ("基线", lambda e: _n(e["payload"].get("baseline_refs")) >= 1)]},
    "L2": {"name": "归因", "question": "慢在哪、凭什么这么说",
           "role": "profiling", "skills": ["flow-layer-discipline"],
           "consumes": "L1 的负载、指标与基线", "produces": "确认的瓶颈与证据",
           "human": True, "needs_test": True,
           "deliverables": [
               ("可证伪的命题", lambda e: all((e["payload"].get("proposition") or {}).get(k)
                                          for k in ("statement", "predicted_direction",
                                                    "failure_condition", "validity_scope"))),
               ("受控实验", lambda e: _n(e["payload"].get("experiment_matrix")) >= 1),
               ("别的解释", lambda e: _n(e["payload"].get("alternative_explanations")) >= 1),
               ("归因结论", lambda e: e["payload"].get("attribution_verdict")
                in ("model_supported", "rejected", "unresolved"))]},
    "L3": {"name": "方案", "question": "改哪里、为什么会有用",
           "role": "design", "skills": ["flow-layer-discipline", "l3-criteria-guard"],
           "consumes": "L2 确认的瓶颈", "produces": "候选方案与预期机制",
           "human": True, "needs_test": False,
           "deliverables": [
               ("候选方案（含预期机制与针对的瓶颈）",
                lambda e: _all_keys(e["payload"].get("candidates"),
                                    ("candidate_id", "expected_mechanism", "targets_bottleneck")))]},
    "L4": {"name": "契约", "question": "接口与时序怎么约定",
           "role": "design", "skills": ["flow-layer-discipline"],
           "consumes": "L3 选定的方案", "produces": "接口契约与资源参数",
           "human": True, "needs_test": False,
           "deliverables": [
               ("接口契约", lambda e: _n((e.get("contract_manifest") or {}).get("interfaces")) >= 1),
               ("资源参数（带验证状态）",
                lambda e: _all_keys(e["payload"].get("resource_params"), ("name", "verification")))]},
    "L5": {"name": "RTL", "question": "电路实现对了没有",
           "role": "design", "skills": ["flow-layer-discipline"],
           "consumes": "L4 的契约", "produces": "RTL 模块与 testbench",
           "human": False, "needs_test": True,
           "deliverables": [
               ("RTL 模块", lambda e: _all_keys(e["payload"].get("rtl_modules"), ("name",))),
               ("独立 testbench", lambda e: _n(e["payload"].get("testbench_refs")) >= 1)]},
    "L6": {"name": "集成", "question": "接进仿真器后还对不对",
           "role": "integration", "skills": ["flow-layer-discipline"],
           "consumes": "L5 的 RTL 与 L4 的契约", "produces": "对接映射与冒烟结果",
           "human": True, "needs_test": True,
           "deliverables": [
               ("对接映射（仿真对象 / RTL 模块 / 拍规则）",
                lambda e: _all_keys(e["payload"].get("simobject_mappings"),
                                    ("simobject", "rtl_module", "tick_rules")))]},
    "L7": {"name": "实验", "question": "实测结果支不支持预期",
           "role": "integration", "skills": ["flow-layer-discipline"],
           "consumes": "L6 的对接结果", "produces": "可复现的测量与裁决",
           "human": False, "needs_test": True,
           "deliverables": [
               ("实验入口", lambda e: _n(e["payload"].get("entry_refs")) + _n(e["payload"].get("scripts")) >= 1),
               ("环境", lambda e: bool(e["payload"].get("environment"))),
               ("复现命令", lambda e: bool(e["payload"].get("reproduce_command")))]},
    "L8": {"name": "结论", "question": "结论是什么、边界在哪",
           "role": "integration", "skills": ["flow-layer-discipline"],
           "consumes": "L7 的测量与裁决", "produces": "结论与适用边界",
           "human": True, "needs_test": True,
           "deliverables": [
               ("结论（带模型 / 硬件证据状态）",
                lambda e: _all_keys(e["payload"].get("conclusions"), ("statement",))),
               ("适用范围", lambda e: bool(e["payload"].get("validity_scope")))]},
}


def interface_hash(env):
    """接口卡（旧包则是整个 payload）的内容指纹。决定回执记下它，内容改过之后回执就显示过期。"""
    part = env.get("interface") if env.get("interface") is not None else env.get("payload", {})
    return hashlib.sha256(json.dumps(part, ensure_ascii=False, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()[:16]


def iface_drifted(env):
    """新建任务下的层包：上次核对时记下了接口卡的指纹（validation.interface_sha）。
    与现在的指纹不一致 = 核对通过之后内容改过——「已验证」只对核对过的那份内容成立，改过就要重新核对。
    旧包没有这个指纹，行为不变。"""
    sha = (env.get("validation") or {}).get("interface_sha")
    return bool(sha) and sha != interface_hash(env)


def _iface_give(text, truth, evidence, source, frame=None):
    return {"id": "", "text": text, "truth": truth, "frame": frame,
            "evidence": [x for x in evidence if x], "source": source}


def _plain(v):
    """非字符串的值压成一行人话：字典 →「键=值 · 键=值」，列表 → 顿号分隔。
    给人看的卡片里不出现 JSON 花括号和引号。"""
    if v is None:
        return ""
    if isinstance(v, str):
        return " ".join(v.split())
    if isinstance(v, dict):
        return " · ".join(f"{k}={_plain(x)}" for k, x in list(v.items())[:8])
    if isinstance(v, (list, tuple)):
        return "、".join(_plain(x) for x in v[:8])
    if isinstance(v, bool):
        return "是" if v else "否"
    return str(v)


def _s(v, n=140):
    """压成一行短文本；超长截断并加省略号（原文留在包里，可展开）。"""
    t = _plain(v)
    return t if len(t) <= n else t[:n - 1] + "…"


def _project_interface(env):
    """旧包没有接口卡：从各层现有的结构化字段里摘录，每条标明摘自哪个字段（derived=True）。
    摘不出的栏位如实留空，不由模型另写摘要。"""
    p = env.get("payload", {}) or {}
    intent = (env.get("design_spec") or {}).get("intent", "")
    rq = p.get("research_question") if isinstance(p.get("research_question"), dict) else {}
    layer = env["identity"]["layer_id"]
    q = rq.get("statement") or _s(intent, IFACE_LIMITS["question"])
    gives, open_, limits = [], [], []
    vs = (env.get("validation") or {}).get("validity_scope")

    def add(text, truth, evidence, source, frame=None):
        # 字段类型不一定规范（旧包里同一字段有时是字符串、有时是字典）：非字符串先压成一行
        if text is not None and not isinstance(text, str):
            text = _s(text, 400)
        t = _s(text, IFACE_LIMITS["text"]) if text else ""
        # 旧包里同一句话可能存了两份（两行基线指向同一条）：只摘一次
        if t and not any(g["text"] == t for g in gives):
            gives.append(_iface_give(t, truth, evidence, source, frame))

    def handover(*keys):
        """下游建议（l3_recommendation / l3_handover / iterationN_entry 之类）→ 留给下一层或人的问题。"""
        for k in keys:
            v = p.get(k)
            if isinstance(v, str) and v.strip():
                open_.append({"question": _s(v, IFACE_LIMITS["open"]), "ask": "next", "options": [], "source": f"payload.{k}"})
            elif isinstance(v, dict):
                for kk in ("next_experiment_hint", "falsifiable_proposition_draft", "pending_user"):
                    if isinstance(v.get(kk), str) and v[kk].strip():
                        open_.append({"question": _s(v[kk], IFACE_LIMITS["open"]),
                                      "ask": "human" if kk == "pending_user" else "next",
                                      "options": [], "source": f"payload.{k}.{kk}"})

    if layer == "L1":
        for b in p.get("benchmarks", [])[:3]:
            if isinstance(b, dict):
                add(f"负载：{b.get('name', '')}" + (f"（{_s(b.get('kind'), 40)}）" if b.get("kind") else ""),
                    "assumed", b.get("artifact_refs", []), "payload.benchmarks")
        tbl = (p.get("baseline_characterization") or {}).get("table") if isinstance(p.get("baseline_characterization"), dict) else None
        if isinstance(tbl, list):
            for row in tbl[:4]:
                if isinstance(row, dict) and row.get("window_cycles") is not None:
                    add(f"基线 {_s(row.get('kernel'), 30)} / {_s(row.get('cpu'), 12)}：窗口 {row.get('window_cycles')} 拍，"
                        f"{row.get('insts')} 条指令", "observed", p.get("baseline_refs", []), "payload.baseline_characterization.table")
        mets = p.get("metric_definitions") or []
        if mets:
            add("量什么：" + "、".join(_s(m.get("name"), 24) for m in mets[:6] if isinstance(m, dict))
                + ("…" if len(mets) > 6 else ""), "assumed", [], "payload.metric_definitions")
        dq = p.get("data_quality") if isinstance(p.get("data_quality"), dict) else {}
        if dq.get("measurement_status"):
            limits.append(_s(dq["measurement_status"], IFACE_LIMITS["limit"]))
    elif layer == "L2":
        prop = p.get("proposition") if isinstance(p.get("proposition"), dict) else {}
        det = p.get("attribution_verdict_detail") if isinstance(p.get("attribution_verdict_detail"), dict) else {}
        supported = p.get("attribution_verdict") == "model_supported"
        if det.get("confirmed_bottleneck"):
            add(det["confirmed_bottleneck"], "observed" if supported else "deduced", det.get("evidence_refs", []),
                "payload.attribution_verdict_detail.confirmed_bottleneck")
        elif p.get("root_cause"):
            add(p["root_cause"], "observed" if supported else "deduced", [], "payload.root_cause")
        if prop.get("predicted_direction"):
            add("预期方向：" + _s(prop["predicted_direction"], 110), "deduced", [], "payload.proposition.predicted_direction")
        alts = p.get("alternative_explanations") or []
        if alts:
            add("考虑过的别的解释：" + "；".join(_s(a.get("hypothesis") if isinstance(a, dict) else a, 40) for a in alts[:4]),
                "deduced", [], "payload.alternative_explanations")
        if prop.get("validity_scope"):
            limits.append(_s(prop["validity_scope"], IFACE_LIMITS["limit"]))
        handover("l3_recommendation", "l3_handover")
    elif layer == "L3":
        for c in p.get("candidates", [])[:IFACE_GIVES_MAX]:
            if isinstance(c, dict):
                add(f"{_s(c.get('candidate_id'), 24)}：{_s(c.get('mechanism') or c.get('expected_mechanism'), 100)}",
                    "deduced", [], "payload.candidates")
        sel = p.get("selection_proposal") if isinstance(p.get("selection_proposal"), dict) else {}
        if sel.get("selected"):
            add(f"选定方案：{_s(sel['selected'], 40)}" + ("（待人确认）" if sel.get("pending_user") else ""),
                "assumed", [], "payload.selection_proposal")
        if vs:
            limits.append(_s(vs, IFACE_LIMITS["limit"]))
        handover("selection_proposal")
    elif layer == "L4":
        cm = env.get("contract_manifest") or {}
        ifs = cm.get("interfaces") or []
        if ifs:
            add("接口：" + "、".join(" ".join(x for x in (_s(i.get("interface_id"), 12), _s(i.get("name"), 40)) if x)
                                  for i in ifs[:6] if isinstance(i, dict)),
                "assumed", [], "contract_manifest.interfaces")
        rp = [r for r in p.get("resource_params", []) if isinstance(r, dict)]
        if rp:
            kinds = collections.Counter(r.get("verification") for r in rp)
            truth = ("assumed" if kinds.get("assumed") else
                     "deduced" if kinds.get("sanity_checked") else "observed")
            add(f"资源参数 {len(rp)} 项：拍定 {kinds.get('assumed', 0)} / 自检 {kinds.get('sanity_checked', 0)}"
                f" / 综合验证 {kinds.get('validated_by_synthesis', 0)}", truth, [], "payload.resource_params")
        if vs:
            limits.append(_s(vs, IFACE_LIMITS["limit"]))
        handover("acceptance_handoff")
    elif layer == "L5":
        for m in p.get("rtl_modules", [])[:IFACE_GIVES_MAX]:
            if isinstance(m, dict):
                add(f"模块 {_s(m.get('name'), 30)} 已实现" + (f"，对接 {_s(m.get('contract_interface'), 10)}" if m.get("contract_interface") else "")
                    + (f"；{_s(m.get('timing_measured'), 60)}" if m.get("timing_measured") else ""),
                    "observed", [m.get("source_artifact")] + list(p.get("testbench_refs") or []), "payload.rtl_modules")
        if p.get("verification_strength"):
            add("验证强度：" + _s(p["verification_strength"], 110), "deduced", p.get("reference_model_refs") or [],
                "payload.verification_strength")
        if vs:
            limits.append(_s(vs, IFACE_LIMITS["limit"]))
    elif layer == "L6":
        for m in p.get("simobject_mappings", [])[:4]:
            if isinstance(m, dict):
                add(f"{_s(m.get('simobject'), 40)} ↔ {_s(m.get('rtl_module'), 50)}", "observed", [], "payload.simobject_mappings")
        g = p.get("guardrail") if isinstance(p.get("guardrail"), dict) else {}
        if g.get("verdict"):
            add("护栏：" + _s(g["verdict"], 110), "observed", [], "payload.guardrail.verdict")
        sm = p.get("smoke_matrix")
        if isinstance(sm, list) and sm:
            panics = sum(x.get("panics", 0) for x in sm if isinstance(x, dict))
            add(f"冒烟 {len(sm)} 点，panic 合计 {panics}", "observed", [], "payload.smoke_matrix")
        if isinstance(p.get("integration_evidence"), (str, list, dict)):
            add("集成证据：" + _s(p["integration_evidence"], 110), "observed", [], "payload.integration_evidence")
        if vs:
            limits.append(_s(vs, IFACE_LIMITS["limit"]))
        handover("l7_handover")
    elif layer == "L7":
        vd = p.get("verdict_data") if isinstance(p.get("verdict_data"), dict) else {}
        mm = p.get("measurement_matrix") or []
        ev7 = p.get("entry_refs") or []
        # 先给测量（实测），再给裁决（推断：由测量套预写判据得出）
        if any(vd.get(k) is not None for k in ("S", "B", "F")):
            add(f"测量：S={vd.get('S')} / B={vd.get('B')} / F={vd.get('F')}"
                + (f"；{_s(vd.get('S_vs_B'), 50)}" if vd.get("S_vs_B") else ""),
                "observed", ev7, "payload.verdict_data")
        elif isinstance(mm, list) and mm:
            add(f"测量矩阵 {len(mm)} 点：" + "、".join(f"{_s(x.get('point'), 16)}={x.get('window_cycles')}"
                                                  for x in mm[:5] if isinstance(x, dict)), "observed",
                ev7, "payload.measurement_matrix")
        elif p.get("experiment_result") is not None:
            add("实验结果：" + _s(p["experiment_result"], 120), "observed", ev7, "payload.experiment_result")
        if vd.get("tier_label") or vd.get("tier_rationale"):
            add(f"裁决：{_s(vd.get('tier_label'), 30)}——{_s(vd.get('tier_rationale'), 90)}", "deduced",
                ev7, "payload.verdict_data.tier_rationale")
        elif vd.get("overall"):
            add("裁决：" + _s(vd["overall"], 120), "deduced", ev7, "payload.verdict_data.overall")
        if vs:
            limits.append(_s(vs, IFACE_LIMITS["limit"]))
        handover("l8_handover")
    elif layer == "L8":
        if p.get("verdict_summary"):
            add(p["verdict_summary"], "deduced", [], "payload.verdict_summary")
        for c in p.get("conclusions", [])[:IFACE_GIVES_MAX]:
            if isinstance(c, dict) and c.get("statement"):
                e = c.get("evidence")
                add(c["statement"], "observed" if c.get("model_causal_status") == "model_supported" else "deduced",
                    [e] if isinstance(e, str) else (e or []), "payload.conclusions")
        if p.get("explicitly_excluded"):
            limits.append("不包含：" + _s(p["explicitly_excluded"], IFACE_LIMITS["limit"] - 5))
        if vs:
            limits.append(_s(vs, IFACE_LIMITS["limit"]))
        ua = p.get("unverified_assumptions")
        if ua:
            limits.append("未验证的假设：" + _s(ua, IFACE_LIMITS["limit"] - 8))
        handover("iteration2_entry", "iteration3_entry", "iteration4_entry")

    for i, g in enumerate(gives, 1):
        g["id"] = f"S{i}"
    return {"question": _s(q, IFACE_LIMITS["question"]), "takes": [], "gives": gives[:IFACE_GIVES_MAX],
            "open": open_, "limits": limits[:4], "derived": True}


def interface_errors(w, env):
    """接口卡是否合规。只对新建任务下的层包强制（gate 调用）；返回错误列表（空 = 通过）。"""
    iface = env.get("interface")
    if not isinstance(iface, dict):
        return ["缺 interface（层接口卡）：新建任务下的层包必须写明交给下游的结论与证据"]
    # 可省的字段写成 null（agent 常这么写）等同于没写：校验时去掉，不改信封本身
    probe = json.loads(json.dumps(iface, ensure_ascii=False))
    for g in probe.get("gives") or []:
        if isinstance(g, dict) and g.get("frame") is None:
            g.pop("frame", None)
    for k in ("takes", "open", "limits"):
        if probe.get(k) is None:
            probe.pop(k, None)
    errs = [str(e) for e in validate(probe, w.schema("layer_interface"))]
    if len(_s(iface.get("question"), 10 ** 6)) > IFACE_LIMITS["question"]:
        errs.append(f"interface.question 超过 {IFACE_LIMITS['question']} 字")
    gives = iface.get("gives") if isinstance(iface.get("gives"), list) else []
    if len(gives) > IFACE_GIVES_MAX:
        errs.append(f"interface.gives 最多 {IFACE_GIVES_MAX} 条，现 {len(gives)} 条")
    results, arts = w.results(), w.artifacts()
    parents = set(env.get("provenance", {}).get("parent_package_refs", []))
    seen = set()
    for g in gives:
        if not isinstance(g, dict):
            continue
        gid = g.get("id") or "?"
        if gid in seen:
            errs.append(f"interface.gives 结论编号重复：{gid}")
        seen.add(gid)
        if len(_s(g.get("text"), 10 ** 6)) > IFACE_LIMITS["text"]:
            errs.append(f"结论 {gid} 超过 {IFACE_LIMITS['text']} 字——一句话说清")
        ev = g.get("evidence") if isinstance(g.get("evidence"), list) else []
        for ref in ev:
            if not (isinstance(ref, str) and (ref in results or ref in arts)):
                errs.append(f"结论 {gid} 的证据 {ref} 不在登记册里（只认 RES- 测试结果与 ART- 工件）")
        # 标「实测」的结论必须指向登记在册、且结论为通过的测试结果——否则不算验证
        if g.get("truth") == "observed" and not any(
                isinstance(r, str) and r in results and results[r].get("verdict") == "pass" for r in ev):
            errs.append(f"结论 {gid} 标了实测，但没有指向登记在册且通过的测试结果（RES-…）")
    for t in iface.get("takes") or []:
        if isinstance(t, dict) and t.get("from") not in parents:
            errs.append(f"interface.takes 指向的 {t.get('from')} 不是本包的上游")
    for o in iface.get("open") or []:
        if isinstance(o, dict) and len(_s(o.get("question"), 10 ** 6)) > IFACE_LIMITS["open"]:
            errs.append("interface.open 的问题超过 %d 字" % IFACE_LIMITS["open"])
    for lim in iface.get("limits") or []:
        if isinstance(lim, str) and len(lim) > IFACE_LIMITS["limit"]:
            errs.append(f"interface.limits 每条不超过 {IFACE_LIMITS['limit']} 字：{lim[:30]}…")
    return errs


def _is_new_package(env):
    """新建任务下产生的层包（带 provenance.task_id）才受接口卡与「上游先验证」约束。"""
    return bool(env.get("provenance", {}).get("task_id"))


def _receipts(w, env):
    """本包的决定回执：只列仍然有效的人签记录与待批事项；被后来回执取代的旧提议不再单列。"""
    decisions, graph = w.decisions(), w.graph()
    superseded_to = {e["to"] for e in graph if e["edge_type"] == "supersedes"}
    acts_on = {e["from"]: e["to"] for e in graph if e["edge_type"] == "supersedes"}
    cur = interface_hash(env)
    out = []
    for did in env.get("decision_refs", []):
        d = decisions.get(did)
        if not d or did in superseded_to:
            continue
        pending = d.get("reviewer") == "pending-user"
        rec = {"decision_id": did, "pending": pending,
               "summary": d.get("summary") or decision_headline(d, decisions.get)[0],
               "selected": list(d.get("selected_refs") or []),
               "by": "待人决定" if pending else d.get("reviewer", ""),
               "at": d.get("decided_at", ""), "content_sha": d.get("content_sha", ""),
               "stale": bool(d.get("content_sha")) and d.get("content_sha") != cur}
        target = decisions.get(acts_on.get(did, ""))
        if target:
            about = target.get("summary") or decision_headline(target, decisions.get)[0]
            # 回执正文已经写了被批准的那条时，不再重复一遍
            rec["about"] = "" if about and about in rec["summary"] else about
            rec["outcome"] = "驳回" if d.get("decision_type") == "reject" else "批准"
        out.append(rec)
    out.sort(key=lambda r: (not r["pending"], r["at"]))
    return out


def _evidence_lines(w, env, iface):
    """证据清单：登记在册的测试（名称、结论、强度）与工件（编号、哈希、路径）。"""
    tests, runs, results, arts = w.tests(), w.runs(), w.results(), w.artifacts()
    v = env.get("validation", {})
    out = []
    for rid in v.get("test_result_refs", []):
        res = results.get(rid)
        if not res:
            out.append({"id": rid, "kind": "test", "title": "（结果不在登记册）", "verdict": "缺"})
            continue
        run = runs.get(res.get("run_ref"), {})
        td = tests.get(run.get("test_ref"), {})
        out.append({"id": rid, "kind": "test", "title": td.get("title") or td.get("test_id") or run.get("run_id", rid),
                    "proposition": _s(td.get("proposition"), 100), "verdict": res.get("verdict"),
                    "strength": STRENGTH_LABEL.get(td.get("strength", "V1"), td.get("strength", "")),
                    "executor": run.get("executor", ""), "at": run.get("started_at", ""),
                    "log": run.get("log_artifact", ""), "raw": run.get("raw_result_artifact", ""),
                    "for_statement": [g.get("id") for g in iface.get("gives", []) if rid in (g.get("evidence") or [])]})
    cited = {x for g in iface.get("gives", []) for x in (g.get("evidence") or []) if str(x).startswith("ART-")}
    for a in env.get("artifacts", []):
        rec = arts.get(a.get("artifact_id"), {})
        out.append({"id": a.get("artifact_id"), "kind": "artifact", "title": rec.get("note") or a.get("artifact_type", ""),
                    "artifact_type": a.get("artifact_type", ""), "hash": (a.get("content_hash") or "")[:12],
                    "path": rec.get("relpath") or rec.get("store_path") or rec.get("uri", ""),
                    "cited": a.get("artifact_id") in cited})
    return out


STATE_ORDER = ["已作废", "有问题", "待人决定", "未验证", "已验证", "进行中", "未开始"]


def worst_state(words):
    """多个包的状态合成一个框的状态：越靠前越需要人关注。"""
    for w_ in STATE_ORDER:
        if w_ in words:
            return w_
    return words[0] if words else "未开始"


def task_receipts(w, task):
    """任务级的决定回执：章程、开某一轮、结束 / 终止某一轮、封板，以及批准 / 驳回它们的记录。
    与层包上的回执同一种写法（谁 · 何时 · 决定了什么）；被后来回执取代的旧提议不单列。"""
    decisions, graph = w.decisions(), w.graph()
    tid = task["task_id"]
    supersedes = {e["from"]: e["to"] for e in graph if e["edge_type"] == "supersedes"}
    superseded = set(supersedes.values())
    note_by = {e["to"]: str(e.get("note") or "") for e in graph
               if e["edge_type"] == "decided_by" and e["from"] == tid}
    ids, role = set(), {}

    def _add(did, kind):
        if did:
            ids.add(did)
            role.setdefault(did, kind)

    _add(task.get("charter_decision_id"), "章程")
    for it in task.get("iterations", []):
        _add(it.get("entry_decision_id"), f"开{it.get('label') or '迭代 ' + str(it.get('seq'))}")
    task_level = set(ids)
    for did, d in decisions.items():
        if d.get("task_id") == tid:
            ids.add(did)
    for did in list(ids):
        # 批准 / 驳回记录：跟着它所处理的那一条归属
        for new, target in supersedes.items():
            if target in ids:
                _add(new, role.get(target, ""))
    # 挂在层包上的决定是那一层的回执，在层详情里看；这里只留任务级的
    # （旧章程可能挂在某个层包上——它仍然是这个任务的章程，保留）
    pkg_level = set()
    for _, env in w.find_packages():
        pkg_level.update(env.get("decision_refs", []))
    ids = {did for did in ids if did in task_level or did not in pkg_level}
    order = {did: i for i, did in enumerate(decisions)}

    def _kind(did):
        note = note_by.get(did, "")
        if note == "seal":
            return "封板"
        if note.startswith("iteration-") and ":" in note:
            seq, outcome = note[len("iteration-"):].split(":", 1)
            return ("结束" if outcome == "sealed" else "终止") + f"迭代 {seq}"
        return role.get(did, "")

    out = []
    for did in sorted(ids, key=lambda x: order.get(x, 1 << 30)):
        d = decisions.get(did)
        if not d or did in superseded:
            continue
        pending = d.get("reviewer") == "pending-user"
        target = decisions.get(supersedes.get(did, ""))
        rec = {"decision_id": did, "pending": pending, "kind": _kind(did),
               "summary": d.get("summary") or decision_headline(d, decisions.get)[0],
               "by": "待人决定" if pending else d.get("reviewer", ""),
               "at": d.get("decided_at", "")}
        if target:
            about = target.get("summary") or decision_headline(target, decisions.get)[0]
            # 回执正文已经写了被批准的那条时，不再重复一遍
            rec["about"] = "" if about and about in rec["summary"] else about
            rec["outcome"] = "驳回" if d.get("decision_type") == "reject" else "批准"
        out.append(rec)
    out.sort(key=lambda r: (not r["pending"], r["at"]))
    return out


def template_nodes(w, task, it):
    """一轮迭代的模板节点（流程编排里的「框」），按模板顺序：
    [{layer, name, alias, own: [信封], reused: [信封]}]。同一层在模板里出现多次（批量标定的逐例修复）只算一个框。"""
    env_layers = w.iteration_layers(task, it)
    tpl = it.get("flow_template") or task.get("flow_template") or DEFAULT_TEMPLATE
    try:
        tnodes = _load_layer_bus().expand_template(tpl)["nodes"]
    except Exception:  # noqa: BLE001
        tnodes = []
    nodes, seen = [], set()
    for nd in tnodes:
        L = str(nd.get("component", "")).rstrip("d")
        if L not in LAYER_DIR or L in seen:
            continue
        seen.add(L)
        nodes.append({"layer": L, "name": LAYER_GUIDE[L]["name"], "alias": nd.get("alias", ""),
                      "own": env_layers[L]["own"], "reused": env_layers[L]["reused"]})
    return nodes


def layer_card(w, env, tasks=None, pkg_index=None):
    """一个层包的卡片：状态词 + 固定说明 + 接口卡四栏 + 核对回执 + 证据清单。只读，不改任何东西。
    pkg_index 可由批量调用方传入（包 ID → 信封），省得每张卡都重扫全部层包。"""
    ident = env["identity"]
    layer, pid = ident["layer_id"], ident["package_id"]
    guide = LAYER_GUIDE.get(layer, {})
    tasks = w.tasks() if tasks is None else tasks
    task, it = w.iteration_of(env, tasks)
    iface = dict(env["interface"]) if isinstance(env.get("interface"), dict) else _project_interface(env)
    v = env.get("validation", {})
    lc = env["lifecycle"]["status"]
    verdict = v.get("aggregate_verdict", "not_gated")
    receipts = _receipts(w, env)
    new_pkg = _is_new_package(env)
    if pkg_index is None:
        pkg_index = {e["identity"]["package_id"]: e for _, e in w.find_packages()}

    parent_ids = list(env.get("provenance", {}).get("parent_package_refs", []))
    derived_up = False
    if not parent_ids and it is not None and task is not None:
        # 旧包可能没写上游引用（cvfpu 那条链就是）：按模板顺序，上一个框里的包就是它的上游
        tnodes = template_nodes(w, task, it)
        for k, nd in enumerate(tnodes):
            if k and any(e["identity"]["package_id"] == pid for e in nd["own"] + nd["reused"]):
                prev = tnodes[k - 1]
                parent_ids = [e["identity"]["package_id"] for e in prev["own"] + prev["reused"]]
                derived_up = True
                break

    # ---- 核对回执：五行人话 ----
    marks = [(name, ok(env)) for name, ok in guide.get("deliverables", [])]
    arts = w.artifacts()
    refs = env.get("artifacts", [])
    bad_art = [a.get("artifact_id") for a in refs
               if a.get("artifact_id") not in arts or arts[a["artifact_id"]].get("content_hash") != a.get("content_hash")]
    parents = []
    for p in parent_ids:
        pe = pkg_index.get(p)
        st = STATE_WORD.get(pe["lifecycle"]["status"], pe["lifecycle"]["status"]) if pe else "不存在"
        if new_pkg and pe and st == "已验证" and iface_drifted(pe):
            st = "核对后改过"
        parents.append((p, st))
    signed = [r for r in receipts if not r["pending"] and r.get("outcome") != "驳回"]
    # 新建任务下的层包：内容在签字之后改过的回执不再算数（要重签）；旧包只提示，不拦
    signed_now = [r for r in signed if not (new_pkg and r["stale"])]
    drift = iface_drifted(env)
    results = w.results()
    passed = [r for r in v.get("test_result_refs", []) if r in results and results[r].get("verdict") == "pass"]
    strength = "V0"
    if v.get("test_result_refs"):
        runs, tests = w.runs(), w.tests()
        strengths = [(tests.get(runs.get(results[r].get("run_ref"), {}).get("test_ref"), {})
                      .get("strength", "V1")) for r in passed if r in results]
        strength = max(strengths, key=lambda s: STRENGTH_ORDER.index(s) if s in STRENGTH_ORDER else 0) if strengths else "V0"
    checks = [
        {"id": "deliverables", "label": "交付齐全", "ok": all(ok for _, ok in marks) if marks else None,
         "detail": " · ".join(f"{name} {'✓' if ok else '✗'}" for name, ok in marks) if marks else "本层没有固定的交付项"},
        {"id": "artifacts", "label": "证据未动", "ok": (not bad_art) if refs else None,
         "detail": (f"{len(refs)} 个工件，哈希全部一致" if not bad_art else f"{len(bad_art)} 个工件未登记或哈希不一致") if refs else "没有登记工件"},
        {"id": "upstream", "label": "上游已验证", "ok": (all(s == "已验证" for _, s in parents)) if parents else None,
         "detail": (" · ".join(f"{_s(p, 34)}：{s}" for p, s in parents)
                    + ("（按模板顺序推得）" if derived_up else "")) if parents else "无上游"},
        {"id": "human", "label": "有人签字", "ok": bool(signed_now) if guide.get("human") else None,
         "detail": (f"{signed_now[0]['by']} · {signed_now[0]['at'][:16].replace('T', ' ')}" if signed_now
                    else "签字之后内容改过，需要重签" if signed
                    else "还没有人签" if guide.get("human") else "本层不需要人签")},
        {"id": "tests", "label": "跑过验证", "ok": bool(passed) if guide.get("needs_test") else None,
         "detail": (f"{len(passed)} 条登记测试通过（{STRENGTH_LABEL.get(strength, strength)}）" if passed
                    else ("还没有通过的测试" if guide.get("needs_test") else "本层不要求测试"))},
    ]
    for c in checks:
        if c["ok"] is True:
            c["mark"] = "✓"
        elif c["ok"] is False:
            c["mark"] = "✗"
        else:
            c["mark"] = "—"

    # ---- 状态词：由核对结果机械得出 ----
    reasons = env["lifecycle"].get("invalidation_reasons", [])
    pending = [r for r in receipts if r["pending"]]
    if lc in BAD_LIFECYCLE:
        # 失效理由的原文以时间戳开头（2026-11-24T20:40:39 上游/自身失效：…）：给人看时去掉
        word, why = "已作废", re.sub(r"^\d{4}-\d{2}-\d{2}T[\d:]+\s*", "", reasons[-1] if reasons else "被后来的版本取代")
    elif verdict == "fail" or lc in ("failed", "rejected"):
        # gate_report 的原文形如 "BLOCK: a; b"：给人看时去掉前缀
        word, why = "有问题", _s(re.sub(r"^(BLOCK|WARN):\s*", "", v.get("gate_report") or "核对未通过"), 160)
    elif pending:
        word, why = "待人决定", "等人决定：" + pending[0]["summary"]
    elif verdict == "needs_decision" or lc == "needs_decision":
        # 上次核对差的是人的签字：现在签了没有？
        if guide.get("human") and not signed_now:
            word = "待人决定"
            why = ("内容在签字之后改过，需要重签" if new_pkg and signed
                   else "本层需要人签字后才能交给下游")
        else:
            word, why = "进行中", "已有人签字，还没重新核对"
    elif verdict == "unresolved" or lc == "unresolved":
        word = "未验证"
        miss = next((c for c in checks if c["ok"] is False), None)
        why = f"还没有：{miss['label']}" if miss else _s(v.get("gate_report") or "没有跑过的验证", 160)
    elif verdict == "pass" and lc == "passed" and drift:
        word, why = "未验证", "接口卡在上次核对之后改过，需要重新核对"
    elif verdict == "pass" and lc == "passed":
        word, why = "已验证", f"核对通过（{(v.get('gated_at') or '')[:16].replace('T', ' ')}）"
    elif lc == "draft" and not iface.get("gives"):
        word, why = "未开始", "还没有产出"
    elif lc == "draft":
        word, why = "进行中", "已有产出，还没核对"
    else:
        word, why = "进行中", f"{STATE_WORD.get(lc, lc)}，还没核对通过"
    return {
        "package_id": pid, "layer": layer, "layer_name": guide.get("name", ""),
        "title": ident.get("title", ""), "owner": ident.get("owner_agent", ""),
        "task_id": task["task_id"] if task else None,
        "iteration_id": it.get("iteration_id") if it else None,
        "iteration_label": it.get("label") if it else None,
        "goal": (task.get("goal") or {}).get("statement", "") if task else "",
        "intent": _s((env.get("design_spec") or {}).get("intent"), 200),
        "state": {"word": word, "why": why, "lifecycle": lc, "verdict": verdict},
        "guide": {"question": guide.get("question", ""), "role": guide.get("role", ""),
                  "role_label": ROLE_LABEL.get(guide.get("role", ""), guide.get("role", "")),
                  "skills": guide.get("skills", []), "consumes": guide.get("consumes", ""),
                  "produces": guide.get("produces", ""), "human": bool(guide.get("human")),
                  "needs_test": bool(guide.get("needs_test")),
                  "deliverables": [name for name, _ in guide.get("deliverables", [])]},
        "interface": iface,
        "checks": checks, "checked_at": v.get("gated_at", ""), "gate_report": v.get("gate_report", ""),
        "receipts": receipts,
        "evidence": _evidence_lines(w, env, iface),
        "reproduce_command": _s((env.get("payload") or {}).get("reproduce_command"), 400),
        "parents": parent_ids,
        "parents_derived": derived_up,
        "interface_drift": drift,
        "artifact_count": len(refs),
    }


STATE_WORD = {"passed": "已验证", "failed": "有问题", "rejected": "已否决", "invalidated": "已作废",
              "superseded": "已作废", "needs_decision": "待人决定", "unresolved": "未验证",
              "draft": "未开始", "ready": "未开始", "running": "进行中", "validating": "进行中"}


def _print_card(w, env):
    """卡片的人读版（wf card）：从上往下能读完的一段文字。"""
    c = layer_card(w, env)
    g, iface = c["guide"], c["interface"]
    print(f"# {c['layer']} {c['layer_name']}｜{c['title']}")
    print(f"状态：{c['state']['word']}——{c['state']['why']}")
    if c["iteration_label"] or c["task_id"]:
        print(f"所属：{c['iteration_label'] or ''} {c['task_id'] or ''}")
    print(f"\n## 这一层是什么")
    print(f"- 回答的问题：{g['question']}")
    print(f"- 由谁做：{g['role_label']}｜纪律：{'、'.join(g['skills'])}")
    print(f"- 输入：{g['consumes']}｜必须交出：{g['produces']}")
    print(f"- 怎样算已验证：{'需要人签字；' if g['human'] else ''}{'需要登记在册的测试通过' if g['needs_test'] else '不要求测试'}")
    print(f"\n## 目标（不变量）\n{c['goal'] or '（未归到任务）'}")
    print(f"\n## 交付（交给下游的结论）" + ("　［没有接口卡：从现有字段摘录］" if iface.get("derived") else ""))
    print(f"- 本层要回答的问题：{iface.get('question') or '—'}")
    for x in iface.get("takes") or []:
        print(f"- 用了 {_s(x.get('from'), 40)} 的 {x.get('statement')}")
    for x in iface.get("gives") or []:
        frame = f"｜{x.get('frame')}" if x.get("frame") else ""
        src = f"　［摘自 {x['source']}］" if x.get("source") else ""
        print(f"- {x.get('id')}【{TRUTH_LABEL.get(x.get('truth'), x.get('truth'))}{frame}】{x['text']}{src}")
    for x in iface.get("open") or []:
        print(f"- 待解（{'人' if x.get('ask') == 'human' else '下一层'}）：{x['question']}")
    if not iface.get("gives"):
        print("- （还没有产出）")
    if iface.get("limits"):
        print(f"\n## 适用边界")
        for x in iface["limits"]:
            print(f"- {x}")
    print(f"\n## 证据")
    shown = False
    for e in c["evidence"]:
        if e["kind"] == "test":
            shown = True
            print(f"- 测试 {e['title']}：{e.get('verdict')}（{e.get('strength')}）" + (f" → {'、'.join(e['for_statement'])}" if e.get("for_statement") else ""))
        elif e.get("cited"):
            shown = True
            print(f"- 工件 {e['id']}（{e.get('artifact_type')}，哈希 {e.get('hash')}）：{e.get('path') or e.get('title')}")
    if not shown:
        print("- （没有被结论引用的登记证据）")
    if c["reproduce_command"]:
        print(f"- 复现：{c['reproduce_command']}")
    print(f"\n## 核对回执（上次核对：{(c['checked_at'] or '从未')[:16].replace('T', ' ') or '从未'}）")
    for ch in c["checks"]:
        print(f"- {ch['mark']} {ch['label']}：{ch['detail']}")
    print(f"\n## 人的决定")
    shown = [r for r in c["receipts"] if r["pending"] or r.get("outcome") != "驳回"][:6]
    if not c["receipts"]:
        print("- 没有记录")
    for r in shown:
        if r["pending"]:
            print(f"- 待人决定：{r['summary']}")
        else:
            about = f"（对「{r['about']}」{r['outcome']}）" if r.get("about") else ""
            stale = "　［内容在其后改过，回执已过期］" if r.get("stale") else ""
            print(f"- {r['by']} · {r['at'][:16].replace('T', ' ')} 决定：{r['summary']}{about}{stale}")
    rest = len(c["receipts"]) - len(shown)
    if rest > 0:
        print(f"- 另有 {rest} 条（驳回与更早的记录）；全文见 wf show / wf status")
    return c


def cmd_card(w, args):
    env_path, env = w.resolve_pkg(args.pkg)
    return _print_card(w, env)


def cmd_guide(w, args):
    print("每一层回答什么、由谁做、必须交出什么、怎样算已验证（界面与 wf brief 共用这一份）")
    for L in ("L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"):
        if getattr(args, "layer", None) and args.layer != L:
            continue
        g = LAYER_GUIDE[L]
        print(f"\n{L} {g['name']}：{g['question']}")
        print(f"  由谁做：{ROLE_LABEL[g['role']]}｜纪律：{'、'.join(g['skills'])}")
        print(f"  输入：{g['consumes']}｜必须交出：{g['produces']}")
        print(f"  交付项：{'、'.join(n for n, _ in g['deliverables'])}")
        print(f"  怎样算已验证：{'需要人签字；' if g['human'] else '不需要人签字；'}"
              f"{'需要登记在册的测试通过' if g['needs_test'] else '不要求测试'}")
    print("\n层接口卡（每个层包交给下游的内容，五个字段）：")
    print('  {"question": "本层要回答的问题（≤120 字）",')
    print('   "takes":  [{"from": "PKG-上游", "statement": "S1"}],            // 用了上游哪几条结论')
    print(f'   "gives":  [{{"id": "S1", "text": "一句话结论（≤{IFACE_LIMITS["text"]} 字）", "truth": "observed|deduced|assumed",')
    print('               "frame": "F0|F1|F2|F3（数字产自哪个时延模型，可省）", "evidence": ["RES-…", "ART-…"]}],')
    print('   "open":   [{"question": "没解决的问题", "ask": "human|next"}],')
    print('   "limits": ["适用边界"]}')
    print("  实测 / 推断 / 拍定 三档；标「实测」的结论必须指向登记在册且通过的测试结果。")
    return LAYER_GUIDE


# ---------------------------------------------------------------- 层规则表（§4.2/§6）

def _rule_L1(w, env):
    errs = []
    p = env["payload"]
    if len(p.get("benchmarks", [])) < 1:
        errs.append("payload.benchmarks 至少 1 项")
    for m in p.get("metric_definitions", []):
        for k in ("name", "definition", "unit"):
            if not m.get(k):
                errs.append(f"metric_definitions 项缺 {k}（指标定义不完整=不可信观测）")
    if len(p.get("metric_definitions", [])) < 1:
        errs.append("payload.metric_definitions 至少 1 项")
    if len(p.get("baseline_refs", [])) < 1:
        errs.append("payload.baseline_refs 至少 1 项（无基线则无对照）")
    return errs


def _rule_L2(w, env):
    errs = []
    p = env["payload"]
    prop = p.get("proposition") or {}
    for k in ("statement", "predicted_direction", "failure_condition", "validity_scope"):
        if not prop.get(k):
            errs.append(f"payload.proposition.{k} 为空——归因必须可证伪")
    if len(p.get("experiment_matrix", [])) < 1:
        errs.append("payload.experiment_matrix 至少 1 项（受控实验）")
    if len(p.get("alternative_explanations", [])) < 1:
        errs.append("payload.alternative_explanations 至少 1 项——未排除替代解释的归因不得放行")
    if p.get("attribution_verdict") not in ("model_supported", "rejected", "unresolved"):
        errs.append("payload.attribution_verdict 必须为 model_supported/rejected/unresolved")
    return errs


def _rule_L3(w, env):
    errs = []
    p = env["payload"]
    cands = p.get("candidates", [])
    if len(cands) < 1:
        errs.append("payload.candidates 至少 1 项")
    pkg_ids = {e["identity"]["package_id"] for _, e in w.find_packages()}
    for c in cands:
        for k in ("candidate_id", "expected_mechanism"):
            if not c.get(k):
                errs.append(f"candidate 缺 {k}")
        tb = c.get("targets_bottleneck", "")
        if not tb:
            errs.append("candidate 缺 targets_bottleneck——方案必须显式对应已确认瓶颈")
        elif not tb.startswith("PKG-L2-") and tb not in pkg_ids:
            errs.append(f"targets_bottleneck 引用不存在：{tb}")
    # 对比有效性（lessons/2026-11-13 + 用户指令 2026-11-14）：
    # 1) 判据必须锚定瓶颈特征量（bottleneck_resolution 主判据段）
    # 2) 判据引用的阈值数字必须带保真度基架标注（measurement_frames）
    ac = p.get("acceptance_criteria")
    if ac is not None:
        if not (isinstance(ac, dict) and ac.get("bottleneck_resolution")):
            errs.append("acceptance_criteria 缺 bottleneck_resolution 主判据段——L3 判据只锚定上游"
                        "瓶颈特征量与自身机制参数，不得与其他方案性能数字立通过性阈值"
                        "（见 skill l3-criteria-guard 第 3 步）")
        if not (isinstance(ac, dict) and ac.get("measurement_frames")):
            errs.append("acceptance_criteria 存在但缺 measurement_frames（每个阈值数字的时延模型基架标注，"
                        "见 skill l3-criteria-guard——跨基架绝对对比不得作裁决）")
    return errs


def _rule_L4(w, env):
    errs = []
    cm = env.get("contract_manifest")
    if not cm:
        errs.append("缺 contract_manifest（L4 核心交付物）")
    else:
        if len(cm.get("interfaces", [])) < 1:
            errs.append("contract_manifest.interfaces 至少 1 项")
    p = env["payload"]
    if len(p.get("resource_params", [])) < 1:
        errs.append("payload.resource_params 至少 1 项")
    for r in p.get("resource_params", []):
        if r.get("verification") not in ("assumed", "sanity_checked", "validated_by_synthesis"):
            errs.append(f"资源参数 {r.get('name')} 缺验证状态（assumed/sanity_checked/validated_by_synthesis）")
    return errs


def _rule_L5(w, env):
    errs = []
    p = env["payload"]
    if len(p.get("rtl_modules", [])) < 1:
        errs.append("payload.rtl_modules 至少 1 项")
    for m in p.get("rtl_modules", []):
        if not m.get("name"):
            errs.append("rtl_module 缺 name")
    if len(p.get("testbench_refs", [])) < 1:
        errs.append("payload.testbench_refs 至少 1 项——无独立 testbench 的 RTL 只算 V0")
    return errs


def _rule_L6(w, env):
    errs = []
    p = env["payload"]
    if len(p.get("simobject_mappings", [])) < 1:
        errs.append("payload.simobject_mappings 至少 1 项")
    for m in p.get("simobject_mappings", []):
        for k in ("simobject", "rtl_module", "tick_rules"):
            if not m.get(k):
                errs.append(f"simobject_mapping 缺 {k}")
    return errs


def _rule_L7(w, env):
    errs = []
    p = env["payload"]
    if len(p.get("entry_refs", [])) + len(p.get("scripts", [])) < 1:
        errs.append("payload.entry_refs/scripts 至少 1 项")
    if not p.get("environment"):
        errs.append("payload.environment 为空——实验包必须绑定环境描述")
    if not p.get("reproduce_command"):
        errs.append("payload.reproduce_command 为空——不可复现的实验包不得放行")
    return errs


def _rule_L8(w, env):
    errs = []
    p = env["payload"]
    concs = p.get("conclusions", [])
    if len(concs) < 1:
        errs.append("payload.conclusions 至少 1 项")
    for c in concs:
        if not c.get("statement"):
            errs.append("conclusion 缺 statement")
        if c.get("model_causal_status") not in ("model_only", "model_supported"):
            errs.append(f"结论「{c.get('statement', '')[:20]}…」缺 model_causal_status（model_only/model_supported）")
        if c.get("hardware_evidence_status") not in ("none", "indirect", "hardware_supported"):
            errs.append(f"结论「{c.get('statement', '')[:20]}…」缺 hardware_evidence_status")
    for cmp_ in p.get("comparisons", []):
        if not cmp_.get("frame"):
            errs.append(f"comparison「{str(cmp_.get('lhs', ''))[:16]}…」缺 frame（时延模型基架标注，"
                        "跨基架对比只能作校准不得作结论）")
    if not p.get("validity_scope"):
        errs.append("payload.validity_scope 为空——结论必须声明适用范围")
    return errs


LAYER_RULES = {"L1": _rule_L1, "L2": _rule_L2, "L3": _rule_L3, "L4": _rule_L4,
               "L5": _rule_L5, "L6": _rule_L6, "L7": _rule_L7, "L8": _rule_L8}


# ---------------------------------------------------------------- 执行面调度（§3.2）
# 层 → 主导 agent 角色（platform/agents/<role>.md）
LAYER_AGENT = {"L1": "profiling", "L2": "profiling", "L3": "design", "L4": "design",
               "L5": "design", "L6": "integration", "L7": "integration", "L8": "integration"}
# brief 用的层要求清单（人读版，与 LAYER_RULES 对应）
LAYER_RULE_DOC = {
    "L1": ["payload.benchmarks >=1", "metric_definitions 每项 name+definition+unit",
           "baseline_refs >=1", "data_quality 数据质量报告"],
    "L2": ["proposition 四件套（statement/predicted_direction/failure_condition/validity_scope）",
           "experiment_matrix >=1", "alternative_explanations >=1（尽量已排除）",
           "attribution_verdict ∈ model_supported/rejected/unresolved", ">=1 条 pass 测试"],
    "L3": ["candidates >=1：candidate_id + expected_mechanism + targets_bottleneck(指向 L2 包)"],
    "L4": ["contract_manifest.interfaces >=1", "resource_params 每项带 verification 三态"],
    "L5": ["rtl_modules >=1（name + source_artifact）", "testbench_refs >=1", ">=1 条 pass 测试"],
    "L6": ["simobject_mappings 每项 simobject+rtl_module+tick_rules", ">=1 条 pass 测试"],
    "L7": ["entry_refs/scripts >=1", "environment 非空", "reproduce_command 逐字可跑", ">=1 条 pass 测试"],
    "L8": ["conclusions 每项带 model_causal_status + hardware_evidence_status",
           "validity_scope 非空", "无校准证据禁 hardware_supported", ">=1 条 pass 测试 + 决策"],
}


def cmd_next(w, args):
    """Orchestrator 调度输出：当前前沿 → 哪个角色该做哪个包；外加任务级提示（待批章程、Mission Review）。"""
    it_ref = getattr(args, "iteration", None)
    scope = w.find_iteration(it_ref) if it_ref else None
    st = w.walk_state(domain=args.domain, iteration=scope)
    if scope:
        task, it = scope
        print(f"{it.get('label')}（{it.get('status')}）｜任务：{task.get('title', '')}（{task['status']}）")
    if st["frontier"]:
        print(f"前沿层：{st['frontier']}（角色：{LAYER_AGENT.get(st['frontier'], '-')}）")
    else:
        print("前沿层：L1-L8 全部 passed" if st["packages"] else "前沿层：没有进行中的迭代")
    for b in st["blocking"]:
        layer = b["pkg"].split("-")[1]
        print(f"  [{LAYER_AGENT.get(layer)}] {b['pkg']}  verdict={b['verdict']}")
        if b["report"]:
            print(f"      {b['report'][:110]}")
        print(f"      简报：wf brief {b['pkg']}")
    if st["pending_decisions"]:
        humans = [d for d in st["pending_decisions"]]
        print(f"\n另有人类批准点 {len(humans)} 条（不阻塞起草，阻塞 pass）：wf approve <DEC-id> --as <署名>")

    # 任务级提示：待批的章程 / 开迭代，以及到了 L8 还没做 Mission Review 的迭代
    pending = {d["decision_id"] for d in w.pending_decisions()}
    if scope:
        scopes = [scope]
    else:
        scopes = list(w.live_iterations(args.domain))
        # 不走 L1-L8 的任务（标定类）没有迭代，只看章程
        scopes += [(t, None) for t in w.tasks(args.domain).values()
                   if t.get("status") in TASK_LIVE and not t.get("iterations")]
    tips, tasks_all = [], w.tasks()
    for task, it in scopes:
        if task["status"] not in TASK_LIVE:
            continue      # 已封板 / 已终止的任务没有待办；按迭代查看时也不该再催 Mission Review
        cid = task.get("charter_decision_id")
        if cid in pending:
            tips.append(f"{task['task_id']} 的章程待批：wf approve {cid} --as <署名>")
        if it is None:
            continue
        eid = it.get("entry_decision_id")
        if eid in pending:
            tips.append(f"{task['task_id']} {it.get('label')} 的开迭代决策待批：wf approve {eid} --as <署名>")
        l8 = [e for _, e in w.find_packages()
              if e["identity"]["layer_id"] == "L8"
              and (w.iteration_of(e, tasks_all)[1] or {}).get("iteration_id") == it.get("iteration_id")]
        if l8 and all(e["lifecycle"]["status"] == "passed" for e in l8):
            tips.append(f"{it.get('label')} 已到 L8：需要 Mission Review —— 判据达成则封板 "
                        f"wf task-seal {task['task_id']} --as <署名> --reason \"…\"；"
                        f"未达成且预算有余则开下一轮 wf iter-new --task {task['task_id']} --reason \"…\"")
    if tips:
        print("\n任务提示：")
        for t in tips:
            print(f"  ▶ {t}")
    elif not scope and not st["packages"]:
        print("\n没有进行中的迭代。新优化点须先立章程：wf task-new --domain <域> --title … --goal … --criterion … --benchmark …")


def cmd_brief(w, args):
    """生成给执行面 agent 的工作简报（markdown）。"""
    env_path, env = w.resolve_pkg(args.pkg)
    layer = env["identity"]["layer_id"]
    role = LAYER_AGENT.get(layer, "-")
    p = env["payload"]
    ds = env.get("design_spec", {})
    v = env.get("validation", {})
    by_id = {e["identity"]["package_id"]: e for _, e in w.find_packages()}
    print(f"# 工作简报：{env['identity']['package_id']}")
    print(f"- 层：{layer}（{env['identity'].get('layer_name')}）｜主导角色：**{role}**（platform/agents/{role}.md）")
    print(f"- 标题：{env['identity']['title']}｜owner：{env['identity'].get('owner_agent') or '待认领'}")
    print(f"- 意图：{ds.get('intent','')}")
    if ds.get("assumptions"):
        print(f"- 既定假设：{'；'.join(ds['assumptions'])}")
    if ds.get("constraints"):
        print(f"- 约束：{'；'.join(ds['constraints'])}")
    tasks = w.tasks()
    card = layer_card(w, env, tasks)
    guide = card["guide"]
    print(f"\n## 目标（不变量，原话）\n{card['goal'] or '（本包未归到任务）'}")
    print(f"\n## 这一层是什么")
    print(f"- 回答的问题：{guide['question']}")
    print(f"- 由谁做：{guide['role_label']}｜纪律：{'、'.join(guide['skills'])}")
    print(f"- 输入：{guide['consumes']}｜必须交出：{guide['produces']}")
    print(f"- 怎样算已验证：{'需要人签字；' if guide['human'] else ''}"
          f"{'需要登记在册的测试通过' if guide['needs_test'] else '不要求测试'}")
    parents = env.get("provenance", {}).get("parent_package_refs", [])
    if parents:
        # 下游拿到的是上游的接口卡（结论 / 真值 / 证据 / 留给下游的问题），不是上游的全文
        print("\n## 上游交给本层的信息（接口卡；只可消费已验证的上游）")
        for pid in parents:
            pe = by_id.get(pid)
            if not pe:
                print(f"- {pid}：上游包不存在")
                continue
            pc = layer_card(w, pe, tasks)
            print(f"### {pid}——{pc['state']['word']}｜{pc['title']}")
            for g in pc["interface"].get("gives", []):
                frame = f"｜{g['frame']}" if g.get("frame") else ""
                print(f"- {g['id']}【{TRUTH_LABEL.get(g['truth'], g['truth'])}{frame}】{g['text']}")
            for o in pc["interface"].get("open", []):
                if o.get("ask") != "human":
                    print(f"- 留给下游的问题：{o['question']}")
            for lim in pc["interface"].get("limits", []):
                print(f"- 边界：{lim}")
            if pc["interface"].get("derived"):
                print("- （该上游是旧包：以上摘自它的现有字段）")
    print(f"\n## 当前状态\n- {card['state']['word']}——{card['state']['why']}")
    try:
        vc = w.vcheck(args.pkg)
        print(f"- 验证强度：当前 {vc['current']} / 推荐 {vc['recommended']}"
              + ("；缺口：" + "；".join(vc["gaps"]) if vc["gaps"] else "（达标）"))
    except Exception:
        pass
    print("\n## 本层必须交出（核对时逐项检查）")
    for name in guide["deliverables"]:
        print(f"- [ ] {name}")
    if _is_new_package(env):
        print("\n## 本层接口卡（写进 envelope 的 `interface`；不写或不对，核对不通过）")
        print(f"- question：本层要回答的问题（≤{IFACE_LIMITS['question']} 字）")
        print("- takes：用了上游哪几条结论（写上游卡片里的编号，如 S1）")
        print(f"- gives：交给下游的结论 1–{IFACE_GIVES_MAX} 条，每条一句话（≤{IFACE_LIMITS['text']} 字）；"
              "标「实测」的必须指向登记在册且通过的测试结果（RES-…）")
        print("- open：没解决的问题，标明该由人还是下一层回答")
        print("- limits：适用边界")
        print("- 上游没验证完，本层不能通过。骨架见 `wf guide`")
    if guide["human"]:
        print("\n- 需要人签字：完成后 `wf decide`（reviewer=pending-user，`--summary` 一句话写明决定了什么）")
    if guide["needs_test"]:
        print("- 测试证据：`wf test-register --pkg … --file def.json --run-artifact <stats> --verdict pass`")
    print("\n## 交付动作")
    print("1. 证据入册：wf register-artifact <path> --type <类型>")
    print("2. 填 envelope（payload / interface / provenance.parent_package_refs）")
    print("3. 挂测试与决定（如上层要求）")
    print(f"4. 核对：wf gate {env['identity']['package_id']}（未过=未交付）；`wf card <pkg>` 可看人读的卡片")


# ---------------------------------------------------------------- 命令实现

def cmd_init_store(w, args):
    for d in (w.objects, w.store / "index", w.schemas):
        d.mkdir(parents=True, exist_ok=True)
    (w.store / "README.md").write_text(
        "# 不可变工件与证据存储\n\n"
        "- objects/：内容寻址工件副本（sha256 前 16 位目录）\n"
        "- index/*.jsonl：追加式注册表（工件/决策/测试定义/运行/结果）\n"
        "- graph.jsonl：证据 DAG 边（追加式，不得改写历史）\n", encoding="utf-8")
    print(f"store 就绪：{w.store}")


def cmd_new_domain(w, args):
    if not (w.schemas / "layer_package.json").exists():
        raise SystemExit("错误：schemas 未就绪，请先 wf init-store（并确认 platform/schemas 存在）")
    d = w.domains / args.name
    if d.exists():
        raise SystemExit(f"错误：领域已存在：{d}")
    for layer, sub in LAYER_DIR.items():
        (d / sub).mkdir(parents=True, exist_ok=True)
    pack = {
        "identity": {"pack_id": f"DOMAIN-{args.name}", "name": args.name,
                     "schema_version": SCHEMA_VERSION, "version": "0.1.0"},
        "description": args.description or "",
        "benchmarks": [], "metric_definitions": [], "rtl_units": [],
        "adapters": [], "calibration": {"status": "uncalibrated", "evidence_refs": []},
        "spec_task_links": [], "layer_ownership": {},
    }
    if getattr(args, "from_template", False):
        tpl = ROOT_TPL = Path(__file__).resolve().parents[1] / "templates" / "domain"
        for tplf, outname in [("environment.md.tpl", "environment.md"),
                              ("benchmarks-GUARDS.md.tpl", "benchmarks/GUARDS.md")]:
            content = (tpl / tplf).read_text(encoding="utf-8").replace("__NAME__", args.name)
            out = d / outname
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")
        (d / "contracts").mkdir(exist_ok=True)
        (d / "contracts/injection.md").write_text(
            "# " + args.name + " 注入参数语义（L4 契约期填充；适配器 inject 段引用此处）\n", encoding="utf-8")
        print("已从模板生成：environment.md / benchmarks/GUARDS.md / contracts/injection.md 桩")
    errs = validate(pack, w.schema("domain_pack"))
    if errs:
        raise SystemExit("领域包不符合 schema：\n  " + "\n  ".join(errs))
    save_json(d / "pack.json", pack)
    print(f"领域包已创建：{d}/pack.json（benchmarks/metrics/rtl_units 待填充）")


def cmd_new_pkg(w, args):
    if args.layer not in LAYER_DIR:
        raise SystemExit(f"错误：层号须为 {list(LAYER_DIR)}")
    dom = _ensure_namespace(w, getattr(args, "domain", None))
    pkg_id = f"PKG-{args.layer}-{dom}-{args.slug}"
    pkg_dir = w.domains / dom / LAYER_DIR[args.layer] / pkg_id
    if pkg_dir.exists():
        raise SystemExit(f"错误：层包已存在：{pkg_dir}")
    # 归到哪个任务与迭代：显式 --iteration；没给且恰有一个进行中的迭代，就归它
    iteration_id = task_id = ""
    if getattr(args, "iteration", None):
        owner, it = w.find_iteration(args.iteration)
        if (owner.get("namespace") or DEFAULT_NAMESPACE) != dom:
            raise SystemExit(f"错误：迭代 {args.iteration} 的层包存放在别的位置（{owner.get('namespace')}），"
                             f"与 --domain {dom} 不一致")
        iteration_id, task_id = it["iteration_id"], owner["task_id"]
    else:
        live = w.live_iterations(dom)
        if len(live) == 1:
            iteration_id, task_id = live[0][1]["iteration_id"], live[0][0]["task_id"]
    pkg_dir.mkdir(parents=True)
    env = {
        "identity": {"layer_id": args.layer, "package_id": pkg_id,
                     "schema_version": SCHEMA_VERSION,
                     "domain_pack": f"DOMAIN-{dom}", "title": args.title or pkg_id,
                     "owner_agent": args.owner or "", "layer_name": LAYER_NAME[args.layer]},
        "provenance": {"parent_package_refs": [], "spec_refs": [],
                       "tool_versions": {}, "environment_hash": "",
                       "created_at": now(), "updated_at": now()},
        "design_spec": {"intent": args.intent or "", "assumptions": [], "constraints": []},
        "artifacts": [],
        "evidence": {"observation_refs": [], "derived_fact_refs": [],
                     "candidate_refs": [], "recommendation_refs": []},
        "decision_refs": [],
        "validation": {"test_definition_refs": [], "test_run_refs": [],
                       "test_result_refs": [], "aggregate_verdict": "not_gated"},
        "lifecycle": {"status": "draft", "invalidation_reasons": [], "history": []},
        "payload": {},
    }
    if iteration_id:
        env["provenance"]["iteration_id"] = iteration_id
    if task_id:
        # 新建任务下的包：受接口卡与「上游先验证」约束
        env["provenance"]["task_id"] = task_id
        # 单独补建的一个框：上游 = 模板顺序里的上一个框（与按模板批量建包时一致）
        owner_task, owner_it = w.find_iteration(iteration_id)
        tnodes = template_nodes(w, owner_task, owner_it)
        for k, nd in enumerate(tnodes):
            if k and nd["layer"] == args.layer:
                env["provenance"]["parent_package_refs"] = [
                    e["identity"]["package_id"] for e in tnodes[k - 1]["own"] + tnodes[k - 1]["reused"]]
    save_json(pkg_dir / "envelope.json", env)
    print(f"层包已创建：{pkg_dir}/envelope.json")
    if iteration_id:
        print(f"  归属迭代：{iteration_id}")
    print(f"  下一步：编辑 envelope（payload/artifacts/…）→ wf register-artifact → wf gate {pkg_id}")
    return {"package_id": pkg_id, "iteration_id": iteration_id, "task_id": task_id}


def cmd_register_artifact(w, args):
    w.register_artifact(args.path, artifact_type=args.type,
                        copy=(None if args.copy is None else args.copy),
                        note=args.note, role=args.role, agent=args.agent)


def cmd_register_git(w, args):
    """把一个 git 仓库的 (HEAD + 工作区 diff) 摘要登记为环境工件。"""
    gd = Path(args.git).resolve()
    if not (gd / ".git").exists():
        raise SystemExit(f"错误：不是 git 仓库：{gd}")
    def git(*a):
        return subprocess.run(["git", "-C", str(gd)] + list(a),
                              capture_output=True, text=True, check=True).stdout
    head = git("rev-parse", "HEAD").strip()
    diff_stat = git("diff", "--stat", "HEAD")
    digest = hashlib.sha256((head + "\n" + git("diff", "HEAD")).encode()).hexdigest()
    tmp = w.store / "index" / f".gitenv-{digest[:12]}.txt"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(f"repo: {gd}\nHEAD: {head}\ndiff-stat:\n{diff_stat}\n", encoding="utf-8")
    rec = w.register_artifact(tmp, artifact_type="environment",
                              note=f"{args.name}: HEAD={head[:12]} diff={digest[:12]}",
                              role=f"environment:{args.name}", agent=args.agent, copy=True)
    print(f"  git HEAD {head[:12]}  工作区 diff 哈希 {digest[:12]}")
    return rec


def cmd_link(w, args):
    w.add_edge(args.src, args.dst, args.edge, note=args.note, agent=args.agent)


def cmd_decide(w, args):
    # 一句话结论必填：审阅者（和界面）先看「决定了什么」，长篇依据留在 rationale 里
    summary = (getattr(args, "summary", "") or "").strip()
    if not summary:
        raise SystemExit(f"错误：--summary 必填——用一句话（≤{DECISION_SUMMARY_MAX} 字）写明决定了什么；"
                         "详细依据放 --rationale")
    if bool(args.pkg) == bool(getattr(args, "task", None)):
        raise SystemExit("错误：--pkg 与 --task 二选一（决策挂在层包或任务上）")
    # 详细依据可以不写：记录里的 rationale 不允许为空，就拿一句话结论顶上
    rationale = (args.rationale or "").strip() or summary
    if getattr(args, "task", None):
        task = w.find_task(args.task)
        rec = w.add_decision(args.type, rationale, args.confidence, args.reviewer,
                             selected=args.select, rejected=args.reject,
                             constraints=args.constraint, evidence_refs=args.evidence,
                             scope=args.scope, agent=args.agent, summary=summary,
                             task_id=task["task_id"])
        w.add_edge_safe(task["task_id"], rec["decision_id"], "decided_by",
                        note=args.type, agent=args.agent)
        print(f"{rec['decision_id']} 已挂到 {task['task_id']}（reviewer={args.reviewer}）")
        return rec
    env_path, env = w.resolve_pkg(args.pkg)
    task, _ = w.iteration_of(env)
    rec = w.add_decision(args.type, rationale, args.confidence, args.reviewer,
                         selected=args.select, rejected=args.reject,
                         constraints=args.constraint, evidence_refs=args.evidence,
                         scope=args.scope, agent=args.agent, summary=summary,
                         task_id=task["task_id"] if task else "",
                         content_sha=interface_hash(env))
    pkg_id = env["identity"]["package_id"]

    def _mutate(e):
        e.setdefault("decision_refs", [])
        if rec["decision_id"] not in e["decision_refs"]:
            e["decision_refs"].append(rec["decision_id"])
    w.update_envelope(env_path, _mutate, agent=args.agent)
    w.add_edge_safe(pkg_id, rec["decision_id"], "decided_by",
                    note=args.type, agent=args.agent)
    print(f"{rec['decision_id']} 已挂到 {pkg_id}（reviewer={args.reviewer}）")
    return rec


def cmd_test_register(w, args):
    env_path, env = w.resolve_pkg(args.pkg)
    pkg_id = env["identity"]["package_id"]
    defn = json.loads(Path(args.file).read_text(encoding="utf-8"))
    defn.setdefault("test_id",
                    f"TST-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4]}")
    defn.setdefault("title", "")
    defn["package_ref"] = pkg_id
    defn.setdefault("created_by", args.agent)
    tst = w.add_test_def(defn)
    tid = tst["test_id"]

    def _mutate(e):
        refs = e["validation"].setdefault("test_definition_refs", [])
        if tid not in refs:
            refs.append(tid)
    w.update_envelope(env_path, _mutate, agent=args.agent)
    w.add_edge_safe(pkg_id, tid, "validated_by", note=defn.get("title", ""), agent=args.agent)
    print(f"{tid} 已登记并挂到 {pkg_id}")

    if args.run_artifact:
        art = w.register_artifact(args.run_artifact, artifact_type="stats",
                                  note=f"run evidence for {tid}", agent=args.agent)
        run_id = f"RUN-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4]}"
        w.add_run({"run_id": run_id, "test_ref": tid, "run_status": "completed",
                   "environment_hash": args.env_hash or "unspecified",
                   "started_at": now(), "executor": args.agent,
                   "raw_result_artifact": art["artifact_id"],
                   "package_ref": pkg_id})
        w.add_edge_safe(run_id, tid, "derived_from", agent=args.agent)
        res_id = f"RES-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4]}"
        w.add_result({"result_id": res_id, "run_ref": run_id, "verdict": args.verdict,
                      "measured_effect": dict(kv.split("=", 1) for kv in (args.measured or [])),
                      "notes": args.note or ""})
        w.add_edge_safe(res_id, run_id, "derived_from", agent=args.agent)

        def _mutate2(e):
            for k, r in (("test_run_refs", run_id), ("test_result_refs", res_id)):
                refs = e["validation"].setdefault(k, [])
                if r not in refs:
                    refs.append(r)
        w.update_envelope(env_path, _mutate2, agent=args.agent)
        print(f"{run_id} + {res_id}（verdict={args.verdict}）已挂到 {pkg_id}")


def _link_decision(w, pkg_id, env_path, rec, agent=""):
    """把决策记录挂到层包（decision_refs + decided_by 边），与 cmd_decide 同一写法。"""
    def _mutate(e):
        e.setdefault("decision_refs", [])
        if rec["decision_id"] not in e["decision_refs"]:
            e["decision_refs"].append(rec["decision_id"])
    w.update_envelope(env_path, _mutate, agent=agent)
    w.add_edge_safe(pkg_id, rec["decision_id"], "decided_by",
                    note=rec.get("decision_type", ""), agent=agent)


def _gate_override(w, args):
    """署名覆盖：解除包自身的 invalidated/superseded 后重新门禁（v2.2 §5.3）。

    先落一条带理由的 override 决策并挂到层包，再以该署名跑门禁——谁、为什么解除失效
    都有记录；没有署名和理由就什么也不做。
    """
    signer = (getattr(args, "as", "") or "").strip()
    reason = (getattr(args, "reason", "") or "").strip()
    if not signer or signer == "pending-user":
        raise SystemExit("错误：--override 必须用 --as 提供署名（不能是 pending-user）")
    if not reason:
        raise SystemExit("错误：--override 必须用 --reason 说明解除失效的理由")
    env_path, env = w.resolve_pkg(args.pkg)
    if env["lifecycle"]["status"] not in BAD_LIFECYCLE:
        raise SystemExit(f"错误：{args.pkg} 状态为 {env['lifecycle']['status']}，"
                         f"不在失效/被取代状态，无需覆盖")
    pkg_id = env["identity"]["package_id"]
    agent = getattr(args, "agent", "") or signer
    rec = w.add_decision("override", reason, "high", signer, evidence_refs=[pkg_id],
                         scope="lifecycle-override", agent=agent, content_sha=interface_hash(env))
    _link_decision(w, pkg_id, env_path, rec, agent=agent)
    print(f"{rec['decision_id']} override 已由 {signer} 签署并挂到 {pkg_id}")
    verdict, _ = w.gate(args.pkg, override=signer)
    sys.exit(0 if verdict == VERDICTS_PASS else 1)


def cmd_gate(w, args):
    if getattr(args, "override", False):
        _gate_override(w, args)
    if args.all:
        pkgs = w.find_packages(domain=args.domain, layer=args.layer)
        if args.pkg:
            pkgs = [p for p in pkgs if p[1]["identity"]["package_id"] == args.pkg
                    or str(p[0]) == args.pkg]
        if not pkgs:
            raise SystemExit("错误：没有匹配的层包")
        verdicts = [w.gate(env["identity"]["package_id"])[0] for _, env in pkgs]
        passed = sum(1 for v in verdicts if v == VERDICTS_PASS)
        print(f"\n[gate-all] {len(pkgs)} 包：pass {passed}，"
              f"其余 {[v for v in verdicts if v != VERDICTS_PASS] and '见上' }")
        sys.exit(0 if passed == len(pkgs) else 1)
    if not args.pkg:
        raise SystemExit("错误：请给层包 id，或用 --all")
    verdict, _ = w.gate(args.pkg)
    sys.exit(0 if verdict == VERDICTS_PASS else 1)


def cmd_invalidate(w, args):
    w.invalidate(args.ref, args.reason, agent=args.agent)


def cmd_status(w, args):
    pkgs = w.find_packages(domain=args.domain, layer=args.layer)
    if not pkgs:
        print("（无层包）")
        return
    print(f"{'package_id':44} {'verdict':14} {'status':14} title")
    print("-" * 110)
    order = {"L1": 1, "L2": 2, "L3": 3, "L4": 4, "L5": 5, "L6": 6, "L7": 7, "L8": 8}
    for env_path, env in sorted(pkgs, key=lambda t: (order[t[1]["identity"]["layer_id"]],
                                                     t[1]["identity"]["package_id"])):
        i, v, l = env["identity"], env["validation"], env["lifecycle"]
        print(f"{i['package_id']:44} {v.get('aggregate_verdict', '?'):14} {l['status']:14} {i['title']}")
    # 决策队列（只显示仍被存活包引用的决策；已删包的历史决策留档不出队）
    pending = w.pending_decisions()
    print(f"\n待人类批准的决策（reviewer=pending-user，未被 superseded）：{len(pending)} 条")
    for d in pending[:20]:
        print(f"  {d['decision_id']}  [{d['decision_type']}] {d['rationale'][:60]}")
    # DAG 摘要（跳过引用已删除包的历史边——store 边保留作审计）
    live_pkgs = {e["identity"]["package_id"] for _, e in w.find_packages()}
    edges = [e for e in w.graph()
             if e["from"].startswith("PKG-") and e["to"].startswith("PKG-")
             and e["from"] in live_pkgs and e["to"] in live_pkgs]
    print(f"\n包间 DAG 边：{len(edges)} 条")
    if args.mermaid:
        print("\n```mermaid")
        print("flowchart LR")
        for e in edges:
            print(f"  {e['from']} -->|{e['edge_type']}| {e['to']}")
        print("```")


# ---------------------------------------------------------------- 故障注入自检（§9）

def _mkfile(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _set_payload(w, pkg_id, payload):
    env_path, _ = w.resolve_pkg(pkg_id)

    def _mutate(e):
        e["payload"] = payload
    w.update_envelope(env_path, _mutate, agent="selftest")


def _add_art_ref(w, pkg_id, art, role="evidence"):
    env_path, _ = w.resolve_pkg(pkg_id)

    def _mutate(e):
        e.setdefault("artifacts", []).append(
            {"artifact_id": art["artifact_id"], "artifact_type": art["artifact_type"],
             "content_hash": art["content_hash"], "uri": art["uri"], "role": role})
    w.update_envelope(env_path, _mutate, agent="selftest")


def _add_parent(w, pkg_id, parent_id):
    env_path, _ = w.resolve_pkg(pkg_id)

    def _mutate(e):
        e["provenance"].setdefault("parent_package_refs", [])
        if parent_id not in e["provenance"]["parent_package_refs"]:
            e["provenance"]["parent_package_refs"].append(parent_id)
    w.update_envelope(env_path, _mutate, agent="selftest")
    w.add_edge_safe(pkg_id, parent_id, "depends_on", agent="selftest")


def _valid_L1_payload():
    return {
        "benchmarks": [{"name": "demo-bench", "kind": "synthetic"}],
        "metric_definitions": [{"name": "cycles", "definition": "窗口内 numCycles", "unit": "cycles"}],
        "baseline_refs": ["PKG-L1-demo-base"],
        "data_quality": {"exit_code_guard": "8320*K"},
    }


def cmd_selftest(w, args):
    """已知真值故障注入：gate 必须拦截；同时验证正例可放行（防门禁变成全拒）。"""
    cases = []  # (name, expected_verdict)

    def check(name, got, expected):
        ok = got == expected
        cases.append((name, expected, got, ok))
        exp_s = "通过" if expected is True else ("拦截" if expected is False else expected)
        got_s = got if not isinstance(got, bool) else str(got)
        mark = "✓" if ok else "✗"
        print(f"  {mark} {name:40} 期望={exp_s:14} 实际={got_s}")
        return ok

    with tempfile.TemporaryDirectory(prefix="wf-selftest-") as td:
        root = Path(td)
        (root / "platform" / "schemas").mkdir(parents=True)
        for s in w.schemas.glob("*.json"):
            shutil.copy(s, root / "platform" / "schemas" / s.name)
        t = WF(root)
        cmd_init_store(t, None)
        (t.domains / "demo").mkdir(parents=True, exist_ok=True)
        save_json(t.domains / "demo" / "pack.json", {
            "identity": {"pack_id": "DOMAIN-demo", "name": "demo",
                         "schema_version": SCHEMA_VERSION, "version": "0.1"},
            "description": "selftest 领域",
            "benchmarks": [{"name": "demo-bench"}],
            "metric_definitions": [{"name": "cycles", "definition": "numCycles", "unit": "cycles"}],
            "rtl_units": [], "adapters": [],
            "calibration": {"status": "uncalibrated", "evidence_refs": []},
            "spec_task_links": [],
        })
        for d in LAYER_DIR.values():
            (t.domains / "demo" / d).mkdir(parents=True)

        def newpkg(layer, slug, **kw):
            pkg_id = f"PKG-{layer}-demo-{slug}"
            pkg_dir = t.domains / "demo" / LAYER_DIR[layer] / pkg_id
            pkg_dir.mkdir(parents=True, exist_ok=True)
            env = {
                "identity": {"layer_id": layer, "package_id": pkg_id,
                             "schema_version": SCHEMA_VERSION, "domain_pack": "DOMAIN-demo",
                             "title": kw.get("title", slug), "owner_agent": "selftest",
                             "layer_name": LAYER_NAME[layer]},
                "provenance": {"parent_package_refs": [], "spec_refs": [],
                               "created_at": now(), "updated_at": now()},
                "design_spec": {"intent": kw.get("intent", "selftest"), "assumptions": [], "constraints": []},
                "artifacts": [], "evidence": {"observation_refs": [], "derived_fact_refs": [],
                                              "candidate_refs": [], "recommendation_refs": []},
                "decision_refs": [],
                "validation": {"test_definition_refs": [], "test_run_refs": [],
                               "test_result_refs": [], "aggregate_verdict": "not_gated"},
                "lifecycle": {"status": "draft", "invalidation_reasons": [], "history": []},
                "payload": kw.get("payload", {}),
            }
            save_json(pkg_dir / "envelope.json", env)
            return pkg_id

        # 公共工件
        art = t.register_artifact(_mkfile(root / "a.txt", "evidence-v1\n"), "data",
                                  agent="selftest", copy=True)

        # F1: L1 指标缺 unit → 不可信观测被阻断
        p1 = newpkg("L1", "no-unit", payload={
            "benchmarks": [{"name": "b"}],
            "metric_definitions": [{"name": "cycles", "definition": "x"}],  # 缺 unit
            "baseline_refs": ["PKG-L1-demo-base"]})
        check("F1 L1 指标缺 unit", t.gate(p1, verbose=False)[0], "fail")

        # 正例：完整 L1 + 人类已批准决策 → pass
        pbase = newpkg("L1", "base", payload=_valid_L1_payload())
        _add_art_ref(t, pbase, art)
        t.add_edge_safe(pbase, "DOMAIN-demo", "generated_from", agent="selftest")
        dec = t.add_decision("ratify", "自检正例：观测定义完备", "high", "selftest-user",
                             scope="selftest")
        def _dec_ref(e):
            e.setdefault("decision_refs", []).append(dec["decision_id"])
        t.update_envelope(t.resolve_pkg(pbase)[0], _dec_ref, agent="selftest")
        check("P1 正例 L1 完备", t.gate(pbase, verbose=False)[0], "pass")

        # F2: L2 无替代解释 → 阻断
        p2 = newpkg("L2", "no-alt", payload={
            "proposition": {"statement": "R 争用导致 M 变化", "predicted_direction": "M 下降",
                            "failure_condition": "干预 R 后 M 不变", "validity_scope": "demo"},
            "experiment_matrix": [{"exp": "A 基线"}],
            "alternative_explanations": [], "attribution_verdict": "model_supported"})
        check("F2 L2 缺替代解释", t.gate(p2, verbose=False)[0], "fail")

        # F5: 上游失效传播 → 下游 gate 阻断
        p2b = newpkg("L2", "downstream", payload={
            "proposition": {"statement": "x", "predicted_direction": "y",
                            "failure_condition": "z", "validity_scope": "demo"},
            "experiment_matrix": [{"exp": "A"}], "alternative_explanations": ["alt1"],
            "attribution_verdict": "unresolved"})
        _add_parent(t, p2b, pbase)
        # 给下游挂一条 pass 测试（L2 需要测试证据）
        tst = t.add_test_def({"test_id": f"TST-{uuid.uuid4().hex[:6]}", "proposition": "t",
                              "metric": "cycles", "predicted_direction": "down",
                              "failure_condition": "none", "validity_scope": "demo",
                              "package_ref": p2b})
        run = t.add_run({"run_id": f"RUN-{uuid.uuid4().hex[:6]}", "test_ref": tst["test_id"],
                         "run_status": "completed", "environment_hash": "selftest",
                         "started_at": now(), "package_ref": p2b})
        res = t.add_result({"result_id": f"RES-{uuid.uuid4().hex[:6]}", "run_ref": run["run_id"],
                            "verdict": "pass"})
        def _hook(e):
            e["validation"]["test_definition_refs"].append(tst["test_id"])
            e["validation"]["test_run_refs"].append(run["run_id"])
            e["validation"]["test_result_refs"].append(res["result_id"])
        t.update_envelope(t.resolve_pkg(p2b)[0], _hook, agent="selftest")
        dec2 = t.add_decision("ratify", "下游包决策", "medium", "selftest-user")
        def _dec2(e):
            e["decision_refs"].append(dec2["decision_id"])
        t.update_envelope(t.resolve_pkg(p2b)[0], _dec2, agent="selftest")
        t.invalidate(pbase, "自检：基线数据被替换")
        check("F5 上游 invalidated 传播", t.gate(p2b, verbose=False)[0], "fail")

        # F3: L4 资源参数缺验证状态 → 阻断
        p4 = newpkg("L4", "no-verif")
        _set_payload(t, p4, {"resource_params": [{"name": "luti_port", "value": 16}]})
        def _cm(e):
            e["contract_manifest"] = {"manifest_id": "MAN-demo-x", "interfaces": [{"name": "cmd"}],
                                      "validity_scope": "demo"}
        t.update_envelope(t.resolve_pkg(p4)[0], _cm, agent="selftest")
        check("F3 L4 资源参数缺验证状态", t.gate(p4, verbose=False)[0], "fail")

        # F4: 工件漂移（no-copy 登记=按引用，篡改原文件必须被拦截）
        p1b = newpkg("L1", "drift", payload=_valid_L1_payload())
        big = t.register_artifact(_mkfile(root / "big.bin", "X" * 100), "binary",
                                  agent="selftest", copy=False)
        _add_art_ref(t, p1b, big)
        _mkfile(root / "big.bin", "Y" * 100)
        check("F4 工件内容漂移", t.gate(p1b, verbose=False)[0], "fail")

        # F6: 高权重层无已批准决策 → needs_decision（不得 pass）
        p8 = newpkg("L8", "no-decision", payload={
            "conclusions": [{"statement": "模型内 SME2 更快", "model_causal_status": "model_only",
                             "hardware_evidence_status": "none"}],
            "validity_scope": "demo"})
        tst8 = t.add_test_def({"test_id": f"TST-{uuid.uuid4().hex[:6]}", "proposition": "t8",
                               "metric": "cycles", "predicted_direction": "down",
                               "failure_condition": "none", "validity_scope": "demo",
                               "package_ref": p8})
        run8 = t.add_run({"run_id": f"RUN-{uuid.uuid4().hex[:6]}", "test_ref": tst8["test_id"],
                          "run_status": "completed", "environment_hash": "selftest",
                          "started_at": now(), "package_ref": p8})
        res8 = t.add_result({"result_id": f"RES-{uuid.uuid4().hex[:6]}", "run_ref": run8["run_id"],
                             "verdict": "pass"})
        def _hook8(e):
            e["validation"]["test_definition_refs"].append(tst8["test_id"])
            e["validation"]["test_run_refs"].append(run8["run_id"])
            e["validation"]["test_result_refs"].append(res8["result_id"])
        t.update_envelope(t.resolve_pkg(p8)[0], _hook8, agent="selftest")
        dec8 = t.add_decision("ratify", "待人类批准", "medium", "pending-user")
        def _dec8(e):
            e["decision_refs"].append(dec8["decision_id"])
        t.update_envelope(t.resolve_pkg(p8)[0], _dec8, agent="selftest")
        check("F6 高权重层无人类批准", t.gate(p8, verbose=False)[0], "needs_decision")

        # F7: L7 引用旧哈希（envelope 与注册表不一致）→ 阻断
        p7 = newpkg("L7", "stale-hash", payload={
            "entry_refs": [art["artifact_id"]], "environment": {"abi": "g++13.3.0"},
            "reproduce_command": "./run.sh"})
        def _stale(e):
            e["artifacts"].append({"artifact_id": art["artifact_id"],
                                   "artifact_type": "data",
                                   "content_hash": "0" * 64, "uri": art["uri"]})
        t.update_envelope(t.resolve_pkg(p7)[0], _stale, agent="selftest")
        check("F7 旧哈希/手改引用", t.gate(p7, verbose=False)[0], "fail")

        # F8: L8 硬件外推无校准证据 → 阻断
        p8b = newpkg("L8", "over-extrapolate", payload={
            "conclusions": [{"statement": "真机 SME2 必然更快", "model_causal_status": "model_supported",
                             "hardware_evidence_status": "hardware_supported"}],
            "validity_scope": "demo"})
        check("F8 L8 无校准外推硬件因果", t.gate(p8b, verbose=False)[0], "fail")

        # F9: 缺测试证据的高证据层 → unresolved（不得 pass）
        p5 = newpkg("L5", "no-testbench", payload={
            "rtl_modules": [{"name": "u0", "source_artifact": art["artifact_id"]}],
            "testbench_refs": []})
        check("F9 L5 无测试证据", t.gate(p5, verbose=False)[0], "fail")

        # F10: approve 流转——批准后 needs_decision 翻 pass
        new10, affected10 = t.resolve_decision(dec8["decision_id"], "selftest-user")
        check("F10 批准后翻 pass", t.gate(p8, verbose=False)[0], "pass")

        # F11: reject 不构成批准——仍 needs_decision
        p3 = newpkg("L3", "rejected-scheme", payload={
            "candidates": [{"candidate_id": "C1", "targets_bottleneck": p2b,
                            "expected_mechanism": "x"}]})
        dec11 = t.add_decision("ratify", "待批", "medium", "pending-user")

        def _dec11(e):
            e["decision_refs"].append(dec11["decision_id"])
        t.update_envelope(t.resolve_pkg(p3)[0], _dec11, agent="selftest")
        t.resolve_decision(dec11["decision_id"], "selftest-user", reject=True, reason="自检驳回")
        check("F11 驳回不构成批准", t.gate(p3, verbose=False)[0], "needs_decision")

        # F12: walk 前沿——L1 有未过包（no-unit/drift），前沿应停在 L1 且给出阻塞项
        st = t.walk_state(domain="demo")
        check("F12 walk 前沿=L1", st["frontier"] == "L1" and len(st["blocking"]) >= 1, True)

        # F13: sweep 漂移自动失效——big.bin 在 F4 已被篡改，sweep 应失效引用它的 p1b
        rep = t.sweep(domain="demo")
        p1b_env = t.resolve_pkg(p1b)[1]
        check("F13 sweep 漂移自动失效", p1b_env["lifecycle"]["status"] == "invalidated"
              and p1b in rep["invalidated"], True)

        # F14: registry——未知 schema 版本被拒
        p1c = newpkg("L1", "bad-ver", payload=_valid_L1_payload())

        def _badver(e):
            e["identity"]["schema_version"] = "9.9"
        t.update_envelope(t.resolve_pkg(p1c)[0], _badver, agent="selftest")
        check("F14 未知 schema 版本", t.gate(p1c, verbose=False)[0], "fail")

    detected = sum(1 for c in cases if c[3])
    print(f"\n自检矩阵：{detected}/{len(cases)} 符合预期")
    for name, exp, got, ok in cases:
        if not ok:
            print(f"  未拦截/误判：{name} 期望={exp} 实际={got}")
    sys.exit(0 if detected == len(cases) else 1)


def cmd_approve(w, args):
    new, affected = w.resolve_decision(args.dec, getattr(args, "as"), reject=False, reason=args.note)
    print(f"{new['decision_id']} 已由 {getattr(args, 'as')} 批准（supersedes {args.dec}）")
    for pid in affected:
        verdict, _ = w.gate(pid)
        print(f"  重新门禁 {pid} → {verdict}")


def cmd_reject(w, args):
    new, affected = w.resolve_decision(args.dec, getattr(args, "as"), reject=True, reason=args.reason)
    print(f"{new['decision_id']} 已由 {getattr(args, 'as')} 驳回（supersedes {args.dec}）")
    for pid in affected:
        verdict, _ = w.gate(pid)
        print(f"  重新门禁 {pid} → {verdict}")


def cmd_sweep(w, args):
    report = w.sweep(domain=args.domain)
    print(f"漂移工件：{len(report['drifted'])} {report['drifted']}")
    print(f"丢失工件：{len(report['missing'])} {report['missing']}")
    print(f"自动失效包：{len(report['invalidated'])} {sorted(set(report['invalidated']))}")
    print("—— 重新门禁全部层包 ——")
    pkgs = w.find_packages(domain=args.domain)
    verdicts = [w.gate(env["identity"]["package_id"])[0] for _, env in pkgs]
    from collections import Counter
    print(f"[sweep+gate] {len(pkgs)} 包：{dict(Counter(verdicts))}")


def cmd_walk(w, args):
    it_ref = getattr(args, "iteration", None)
    scope = w.find_iteration(it_ref) if it_ref else None
    st = w.walk_state(domain=args.domain, iteration=scope)
    if scope:
        task, it = scope
        print(f"任务：{task.get('title', '')}（{task['status']}）｜{it.get('label')}（{it.get('status')}）")
        print(f"推进状态（本迭代 {st['packages']} 包（含沿用的上游包），归档 {len(st['archived'])} 包不阻塞）")
    else:
        print(f"领域推进状态（进行中 {st['packages']} 包，归档 {len(st['archived'])} 包不阻塞）")
        if st.get("hidden"):
            print(f"  另有 {st['hidden']} 包属于已封板 / 已终止的迭代，不在默认视图里（--iteration 可查看）")
    print("  层包数：", "  ".join(f"{k}:{v}" for k, v in st["layers"].items()))
    if st["archived"]:
        print("  归档（superseded/invalidated，仅审计）：", ", ".join(p.split('-', 2)[-1] for p in st["archived"]))
    print(f"\n推进前沿（第一个未全 pass 的层）：{st['frontier'] or 'L1-L8 全部 passed'}")
    for b in st["blocking"]:
        print(f"  ▶ {b['pkg']}  verdict={b['verdict']}")
        if b["report"]:
            print(f"      {b['report'][:100]}")
    if st["consumption_warnings"]:
        print("\n层间消费规则警告（§4.3）:")
        for c in st["consumption_warnings"]:
            print(f"  ⚠ {c}")
    if st["pending_decisions"]:
        print(f"\n待批决策 {len(st['pending_decisions'])} 条——批准任一条：")
        print(f"  wf approve {st['pending_decisions'][0]} --as <你的署名>")
    return st


def _store_env_hash(w):
    """对 store 索引 + 证据图 + 层包信封做哈希，供消费者校验状态是否过期。

    必须覆盖层包 envelope：validate / 批准后重新门禁 / 失效都只改 envelope，
    只哈希索引的话这些状态变化对持有旧视图的调用方不可见（乐观并发形同虚设）。
    """
    h = hashlib.sha256()
    paths = [w.idx_artifacts, w.idx_decisions, w.idx_tests,
             w.idx_runs, w.idx_results, w.idx_evolutions, w.idx_benchmarks, w.graph_path]
    paths += sorted(w.domains.glob("*/*/*/envelope.json"))
    paths += sorted(w.domains.glob("*/pack.json"))
    paths += sorted(w.tasks_dir.glob("*.json")) + sorted(w.domains.glob("*/tasks/*.json"))   # 任务记录
    for p in paths:
        if p.exists():
            name = str(p.relative_to(w.root)) if w.root in p.parents else p.name
            h.update(name.encode())
            h.update(sha256_file(p).encode())
    return h.hexdigest()


def cmd_state_export(w, args):
    """聚合领域工作流状态为机器可读的 workflow-state.json（只读投影，不反向写入）。

    复用 find_packages / walk_state / pending_decisions / graph / artifacts，
    字段 100% 对齐 layer_package.json 与 graph.jsonl 的真实字段名。

    domain 过滤贯穿整个输出：层包、DAG、待批决策都只含该域——其他域的层包不进 DAG，
    边里引用但已无 envelope 的包 ID 标为 dangling_ref（不冒充 layer_package）。
    """
    domain = args.domain
    tasks = w.tasks(domain)
    pkgs = w.find_packages(domain=domain)
    walk = w.walk_state(domain=domain)

    dec_to_pkg = {}
    for _, env in pkgs:
        pid = env["identity"]["package_id"]
        for d in env.get("decision_refs", []):
            dec_to_pkg.setdefault(d, []).append(pid)
    # 待批队列按域过滤：只列仍挂在本视图层包上的待批决策，以及挂在本域任务上的（章程 / 开迭代）
    task_dec = {}
    for t in tasks.values():
        task_dec.setdefault(t.get("charter_decision_id"), t["task_id"])
        for it in t.get("iterations", []):
            task_dec.setdefault(it.get("entry_decision_id"), t["task_id"])
    task_dec.pop(None, None)
    task_dec.pop("", None)
    pending = [d for d in w.pending_decisions()
               if dec_to_pkg.get(d["decision_id"]) or d["decision_id"] in task_dec]
    pending_set = {d["decision_id"] for d in pending}
    all_decisions = w.decisions()

    # *_by 边的约定：from=被作用方，to=作用方（PKG --decided_by--> DEC、PKG --validated_by--> TST）。
    # 所以"被证伪"看本包的出边 contradicted_by，入边是"本包证伪了别人"。
    graph = w.graph()
    outgoing = {}
    for e in graph:
        outgoing.setdefault(e["from"], set()).add(e["edge_type"])

    # 层包按 layer 分组
    layers = {L: {"packages": []} for L in LAYER_DIR}
    dag_nodes = {}
    pkg_index = {e["identity"]["package_id"]: e for _, e in pkgs}
    for env_path, env in pkgs:
        ident = env["identity"]
        lid = ident["layer_id"]
        lifecycle = env.get("lifecycle", {}).get("status", "unknown")
        validation = env.get("validation", {})
        pid = ident["package_id"]
        task, it = w.iteration_of(env, tasks)
        card = layer_card(w, env, tasks, pkg_index)
        entry = {
            "package_id": pid,
            "task_id": task["task_id"] if task else None,
            "iteration_id": it.get("iteration_id") if it else None,
            # 界面只用这一个状态词（由核对结果机械得出）；卡片是全部人读内容
            "state": card["state"]["word"],
            "card": card,
            "title": ident.get("title", ""),
            "layer_name": ident.get("layer_name", ""),
            "owner_agent": ident.get("owner_agent", ""),
            "lifecycle": lifecycle,
            "claim_level": (ident.get("claim_level")
                            or env.get("payload", {}).get("claim_level")),  # v2.2 预留
            "gate_verdict": validation.get("aggregate_verdict", "not_gated"),
            "invalidated": lifecycle in ("invalidated", "superseded"),
            "parent_package_refs": env.get("provenance", {}).get("parent_package_refs", []),
            "artifacts": [
                {"artifact_id": a.get("artifact_id"),
                 "artifact_type": a.get("artifact_type"),
                 "content_hash": a.get("content_hash")}
                for a in env.get("artifacts", [])
            ],
            "decision_pending": any(d in pending_set for d in env.get("decision_refs", [])),
            "evidence_status": (
                "invalidated" if lifecycle in ("invalidated", "superseded")
                else "disputed" if "contradicted_by" in outgoing.get(pid, set())
                else "unresolved" if validation.get("aggregate_verdict") == "unresolved"
                else "valid" if validation.get("aggregate_verdict") == "pass"
                else "none"
            ),
            "decision_status": (
                "pending" if any(d in pending_set for d in env.get("decision_refs", []))
                else "resolved" if env.get("decision_refs")
                else "none"
            ),
        }
        layers[lid]["packages"].append(entry)
        dag_nodes[pid] = {"id": pid, "kind": "layer_package", "layer": lid,
                          "title": ident.get("title", "")}

    # DAG：本视图的层包 + 与之相连的证据实体（测试/运行/结果/决策/工件）。
    #  - 其他域的真实层包不进本视图，连带其边一并排除
    #  - 边里引用但已无 envelope 的包 ID 记为 dangling_ref：只作叶子收进来，不再沿它扩展
    #  - 从层包出发迭代收录"至少一端已在图内"的边，直到稳定（RES→RUN→TST←PKG 多跳证据链）
    foreign_pkg = {e["identity"]["package_id"] for _, e in w.find_packages()} - set(dag_nodes)
    kind_by_prefix = [
        ("TST-", "test"), ("RUN-", "run"), ("RES-", "result"),
        ("DEC-", "decision"), ("ART-", "artifact"), ("EVO-", "evolution"),
    ]

    def _kind(node):
        if node.startswith("PKG-"):
            return "layer_package" if node in dag_nodes else "dangling_ref"
        return next((k for pre, k in kind_by_prefix if node.startswith(pre)), "entity")

    edges, seen_edges = [], set()
    expanders = set(dag_nodes)
    changed = True
    while changed:
        changed = False
        for e in graph:
            if (e["edge_id"] in seen_edges or e["from"] in foreign_pkg
                    or e["to"] in foreign_pkg):
                continue
            if e["from"] not in expanders and e["to"] not in expanders:
                continue
            seen_edges.add(e["edge_id"])
            edges.append({"edge_id": e["edge_id"], "edge_type": e["edge_type"],
                          "from": e["from"], "to": e["to"]})
            for node in (e["from"], e["to"]):
                if node not in dag_nodes:
                    kind = _kind(node)
                    dag_nodes[node] = {"id": node, "kind": kind, "layer": None}
                    if kind != "dangling_ref":
                        expanders.add(node)
                    changed = True

    # 任务 → 迭代视图：每个迭代逐层的自有包与沿用包（沿用包带 reused_from = 它所属的迭代）
    entry_by_id = {p["package_id"]: p for group in layers.values() for p in group["packages"]}
    iter_label = {it.get("iteration_id"): it.get("label") or it.get("iteration_id")
                  for t in tasks.values() for it in t.get("iterations", [])}

    def _view(env, reuse=False):
        # 迭代视图里只放状态与归属：完整卡片在 layers[L].packages 里有一份，不重复
        e = {k: v for k, v in entry_by_id[env["identity"]["package_id"]].items() if k != "card"}
        if reuse:
            e["reused_from"] = iter_label.get(e.get("iteration_id"))
        return e

    task_views = []
    for t in sorted(tasks.values(), key=lambda t: (t.get("created_at") or "", t["task_id"])):
        its = sorted(t.get("iterations", []), key=lambda i: i.get("seq", 0))
        it_views = []
        for it in its:
            env_layers = w.iteration_layers(t, it, pkgs)
            lv, own_ids, reached = {}, set(), False
            for L in LAYER_DIR:
                own = env_layers[L]["own"]
                lv[L] = {"own": [_view(e) for e in own],
                         "reused": [_view(e, reuse=True) for e in env_layers[L]["reused"]]}
                own_ids.update(e["identity"]["package_id"] for e in own)
                reached = reached or (L == "L8" and any(
                    e["lifecycle"]["status"] == "passed" for e in own))
            # 模板节点（流程编排里的「框」）按模板顺序排列：每个框列出本轮自有的包或沿用的包。
            # 本轮完成 = 每个框都已验证——完成与否只由模板决定，没有另写的成功判据
            tpl = it.get("flow_template") or t.get("flow_template") or DEFAULT_TEMPLATE
            nodes = []
            for nd in template_nodes(w, t, it):
                own_p = [e["identity"]["package_id"] for e in nd["own"]]
                reused_p = [e["identity"]["package_id"] for e in nd["reused"]]
                words = [entry_by_id[p]["state"] for p in own_p + reused_p]
                nodes.append({"layer": nd["layer"], "name": nd["name"], "alias": nd["alias"],
                              "own": own_p, "reused": reused_p,
                              "state": worst_state(words) if words else "未开始"})
            it_views.append({
                "iteration_id": it.get("iteration_id"), "seq": it.get("seq"),
                "label": it.get("label"), "status": it.get("status"),
                "title": it.get("title", ""), "reason": it.get("reason", ""),
                "started_at": it.get("started_at", ""),
                "flow_template": tpl,
                "nodes": nodes,
                "complete": bool(nodes) and all(n["state"] == "已验证" for n in nodes),
                "frontier": next((n["layer"] for n in nodes if n["state"] != "已验证"), None),
                "reached_l8": reached,
                "pending_decisions": sum(
                    1 for d in pending if own_ids & set(dec_to_pkg.get(d["decision_id"], []))),
                "layers": lv,
            })
        live_its = [i for i in its if i.get("status") in TASK_LIVE]
        task_views.append({
            "task_id": t["task_id"], "title": t.get("title", ""),
            "flow_template": t.get("flow_template") or (its[-1].get("flow_template") if its else ""),
            "template_label": TEMPLATE_LABEL.get(t.get("flow_template") or (its[-1].get("flow_template") if its else ""), ""),
            "status": t.get("status", "draft"),
            "goal": t.get("goal", {}), "benchmarks": t.get("benchmarks", []),
            "charter_decision_id": t.get("charter_decision_id", ""),
            # 任务级的决定回执（章程 / 开某一轮 / 封板）：谁 · 何时 · 决定了什么
            "receipts": task_receipts(w, t),
            "default_iteration": (live_its or its)[-1].get("iteration_id") if its else None,
            "pending_decisions": sum(i["pending_decisions"] for i in it_views) + sum(
                1 for d in pending if task_dec.get(d["decision_id"]) == t["task_id"]),
            "iterations": it_views,
        })

    layer_counts = {L: 0 for L in LAYER_DIR}
    unassigned = 0
    for _, env in pkgs:
        layer_counts[env["identity"]["layer_id"]] += 1
        if w.iteration_of(env, tasks)[1] is None:
            unassigned += 1
    live = w.live_iterations(domain)

    state = {
        # 0.3：+tasks（任务→迭代，含 goal / benchmark / 逐层自有与沿用）；包条目 +task_id/iteration_id；
        #      默认前沿只看进行中的迭代。0.2：+store_root/store_isolated；域过滤贯穿 DAG 与待批；
        #      节点 +title、+dangling_ref
        "schema_version": "0.4",
        "generated_at": now(),
        "generated_by": "wf state-export",
        "store_env_hash": _store_env_hash(w),
        # 让界面如实显示连的是哪个 store（WF_ROOT 指向的是否为非默认根）
        "store_root": str(w.root),
        "store_isolated": bool(os.environ.get("WF_ROOT")),
        "summary": {
            "domain": domain or "*",
            "total_packages": len(pkgs),
            "layer_counts": layer_counts,
            "frontier": walk["frontier"] or "all-passed",
            "archived": len(walk["archived"]),
            "pending_decisions": len(pending),
            "consumption_warnings": len(walk["consumption_warnings"]),
            # 默认视图（进行中的迭代 + 未归迭代的包）与被折叠的包数
            "view_packages": walk["packages"],
            "hidden_packages": walk["hidden"],
            "unassigned_packages": unassigned,
            "active_iteration": live[-1][1].get("iteration_id") if live else None,
        },
        "layers": layers,
        "tasks": task_views,
        "dag": {"nodes": sorted(dag_nodes.values(), key=lambda n: n["id"]),
                "edges": edges},
        "pending_decisions": [
            {"decision_id": d["decision_id"],
             "package_ids": dec_to_pkg.get(d["decision_id"], []),
             "task_id": task_dec.get(d["decision_id"], ""),
             "decision_type": d.get("decision_type", ""),
             "summary": d.get("summary", ""),
             "headline": decision_headline(d, all_decisions.get)[0],
             "rationale": d.get("rationale", "")[:200]}
            for d in pending
        ],
        "flow_candidates": [],  # 预留：编排 Agent 填推荐路径
    }

    # 机器契约自校验：导出结果必须符合已登记的 workflow_state schema（层包条目逐条过）
    ws = w.schema("workflow_state")
    errs = validate(state, ws)
    ver_err = w.check_version("workflow_state", state["schema_version"])
    if ver_err:
        errs.append(f"schema-registry: {ver_err}")
    for L, group in layers.items():
        for p in group["packages"]:
            errs += validate(p, ws["_pkg_entry"], f"$.layers.{L}.{p.get('package_id')}")
    for tv in task_views:
        for iv in tv["iterations"]:
            for L, group in iv["layers"].items():
                for p in group["own"] + group["reused"]:
                    errs += validate(p, ws["_pkg_ref"],
                                     f"$.tasks[{tv['task_id']}].{iv['iteration_id']}.{L}.{p.get('package_id')}")
    if errs:
        raise SystemExit("workflow-state 不符合 schema（state-export 自检失败）：\n  " + "\n  ".join(errs))

    if args.out:
        save_json(args.out, state)
        print(f"workflow-state.json 已写入：{args.out}（{len(dag_nodes)} 节点 / {len(edges)} 边）")
    else:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    return state


# goal → 最小层路径（v2.2 §4.5 典型路径表；路径类型连贯，可 type_compatible 校验）
GOAL_FLOWS = {
    "functional_check": ["L5", "L6", "L7", "L8"],
    "quick_compare": ["L6", "L7", "L8"],
    "exploration": ["L1", "L2", "L3", "L4"],
    "regression": ["L5", "L6", "L7", "L8"],
    "research": ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"],
}
GOAL_DESC = {
    "functional_check": "功能检查（既有 RTL 验证，不重做归因/方案）",
    "quick_compare": "快速比较（已集成模型性能对比）",
    "exploration": "方案探索（归因+方案设计，不进 RTL）",
    "regression": "回归（既有 RTL benchmark 回归）",
    "research": "研究级归因（完整八层）",
}


def cmd_recommend_flow(w, args):
    """编排 Agent 的最小 flow 推荐：读层包状态判断每层复用/重建（复用 layer_bus 类型校验）。"""
    import importlib.util as ilu
    spec = ilu.spec_from_file_location("layer_bus",
                                        str(Path(__file__).parent / "layer_bus.py"))
    lb = ilu.module_from_spec(spec)
    spec.loader.exec_module(lb)

    goal = args.goal
    if goal not in GOAL_FLOWS:
        raise SystemExit(f"错误：未知 goal（可选：{list(GOAL_FLOWS)}）")

    # 指定迭代：只在「本迭代自有的包 → 本迭代沿用的上游包」里选，不跨迭代拼一条链
    scope = None
    if getattr(args, "iteration", None):
        task, it = w.find_iteration(args.iteration)
        if it is None:
            raise SystemExit(f"错误：{args.iteration} 没有迭代")
        env_layers = w.iteration_layers(task, it)
        args.domain = task.get("namespace")      # 旧任务仍按层包的存放位置取候选；新任务为默认位置
        scope = {"task": task, "it": it,
                 "own": {L: [e for e in env_layers[L]["own"]
                             if e.get("lifecycle", {}).get("status") == "passed"] for L in LAYER_DIR},
                 "reused": {L: [e for e in env_layers[L]["reused"]
                                if e.get("lifecycle", {}).get("status") == "passed"] for L in LAYER_DIR}}

    # 可复用资产：各层 lifecycle=passed 的包（invalidated/superseded 已在 walk_state 归为归档）
    by_layer = {}
    for _, env in w.find_packages(domain=args.domain):
        ident = env["identity"]
        if env.get("lifecycle", {}).get("status") == "passed":
            by_layer.setdefault(ident["layer_id"], []).append(env)

    # 未指定 domain 时，挑"能复用层数最多"的域并把整条 flow 限定在该域——
    # 不同领域包的层包互不相干，拼在一起不是一条可执行的 flow
    flow_domain = f"DOMAIN-{args.domain}" if args.domain else None
    if flow_domain is None:
        layers_by_dom = {}
        for lid in GOAL_FLOWS[goal]:
            for dom in {e["identity"]["domain_pack"] for e in by_layer.get(lid, [])}:
                layers_by_dom.setdefault(dom, set()).add(lid)
        if layers_by_dom:
            flow_domain = max(sorted(layers_by_dom), key=lambda d: len(layers_by_dom[d]))

    flow = []
    type_errors = []
    prev_layer = prev_pkg = None
    # 被证伪的包（出边 contradicted_by）不作首选复用对象
    disputed = {e["from"] for e in w.graph() if e["edge_type"] == "contradicted_by"}
    for lid in GOAL_FLOWS[goal]:
        # 标准类型兼容校验（复用 layer_bus.type_compatible）
        if prev_layer is not None:
            out_type = lb.LAYER_COMPONENTS[prev_layer]["output_type"]
            if not lb.type_compatible(out_type, lid):
                type_errors.append(f"{prev_layer}({out_type}) → {lid} 类型不兼容")
        prev_layer = lid

        if scope:
            candidates = scope["own"].get(lid, []) + scope["reused"].get(lid, [])
        else:
            candidates = [e for e in by_layer.get(lid, [])
                          if flow_domain and e["identity"]["domain_pack"] == flow_domain]
        chosen = None
        if candidates:
            # 同层多个候选：谱系相接（上游正是上一层选中的包）优先，再避开被证伪的包。
            # 顺序：谱系相接且未证伪 → 同层未证伪 → 谱系相接 → 任意
            lineage = {c["identity"]["package_id"] for c in candidates
                       if prev_pkg and prev_pkg in c.get("provenance", {}).get("parent_package_refs", [])}
            order = ([c for c in candidates if c["identity"]["package_id"] in lineage
                      and c["identity"]["package_id"] not in disputed]
                     + [c for c in candidates if c["identity"]["package_id"] not in disputed]
                     + [c for c in candidates if c["identity"]["package_id"] in lineage]
                     + candidates)
            chosen = order[0]
            prev_pkg = chosen["identity"]["package_id"]
            step = {
                "layer": lid,
                "action": "reuse",
                "package_id": prev_pkg,
                "title": chosen["identity"].get("title", ""),
                "evidence_status": "disputed" if prev_pkg in disputed else "ok",
                "alternatives": [c["identity"]["package_id"] for c in candidates
                                 if c is not chosen],
                # 指定迭代时：本迭代自有的包不带 reused_from；沿用上游迭代的包标出来源
                "iteration_id": scope["it"]["iteration_id"] if scope else None,
                "reused_from": None,
            }
            if scope and any(c is chosen for c in scope["reused"].get(lid, [])):
                _, owner = w.iteration_of(chosen, w.tasks())
                step["reused_from"] = (owner or {}).get("label") or (owner or {}).get("iteration_id")
            flow.append(step)
        else:
            flow.append({"layer": lid, "action": "rebuild",
                         "package_id": None, "title": "", "alternatives": [],
                         "iteration_id": scope["it"]["iteration_id"] if scope else None,
                         "reused_from": None})

    result = {
        "goal": goal,
        "goal_desc": GOAL_DESC.get(goal, ""),
        "domain": args.domain or "*",
        # 指定迭代时整条 flow 限定在该迭代（自有包优先，其次沿用的上游包）
        "iteration": ({"iteration_id": scope["it"]["iteration_id"], "label": scope["it"].get("label"),
                       "task_id": scope["task"]["task_id"], "task_title": scope["task"].get("title", "")}
                      if scope else None),
        # 未指定 domain 时实际选定的域（整条 flow 在这一个域内自洽）
        "resolved_domain": flow_domain.replace("DOMAIN-", "", 1) if flow_domain else None,
        "flow": flow,
        "reuse_count": sum(1 for f in flow if f["action"] == "reuse"),
        "rebuild_count": sum(1 for f in flow if f["action"] == "rebuild"),
        "type_compatible": not type_errors,
        "type_errors": type_errors,
        "selection_note": ("复用候选=lifecycle=passed 的包；同层多候选时优先取谱系相接者。"
                           "被后续迭代取代的旧包需在 store 标为 superseded 才不会被选中。"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


# ============================================================ RSI 演进（v2.2 §11）

EVO_CHANGE_TYPES = ["behavior_policy", "flow_template", "tool_adapter",
                    "validation", "eval_route", "orchestrator"]
EVO_STATUS = ["draft", "evaluating", "challenger", "champion",
              "rejected", "superseded", "rolled_back"]
# 可（重新）评测的状态；champion / rejected / superseded / rolled_back 是终态或在线态
EVO_EVALUABLE = ("draft", "evaluating", "challenger")


def _evo_check(w, rec):
    """演进候选入册前过 schema + registry（与其他记录格式同一纪律）。"""
    errs = validate(rec, w.schema("evolution_candidate"))
    ver_err = w.check_version("evolution_candidate", rec.get("schema_version", ""))
    if ver_err:
        errs.append(f"schema-registry: {ver_err}")
    if errs:
        raise SystemExit("演进候选不符合 schema：\n  " + "\n  ".join(errs))


def _evo_latest(w, candidate):
    """取候选的最新版本记录（追加式 store：同 id 后写者为准）。"""
    rec = w.evolutions().get(candidate)
    if rec is None:
        raise SystemExit(f"错误：候选不存在：{candidate}")
    return rec


def _evo_append(w, rec):
    _evo_check(w, rec)
    append_jsonl(w.idx_evolutions, rec)
    return rec


def _evo_metrics(name, raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"错误：{name} 不是合法 JSON：{e}")


def cmd_evo_new(w, args):
    """创建 evolution_candidate（演进候选，v2.2 §11.3）。"""
    rec = {
        "candidate_id": f"EVO-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}",
        "schema_version": SCHEMA_VERSION,
        "target_component": args.component,
        "parent_version": args.parent or "VER-champion",
        "change_type": args.change_type,
        "change_set": args.change_set,
        "expected_benefit": args.expected_benefit,
        "motivation_refs": [],
        # v2.2 §11.3：候选必须记录影响范围与评测计划
        "affected_scopes": [s.strip() for s in args.scope.split(",") if s.strip()],
        "evaluation_plan_ref": args.eval_plan or "",
        "generated_by": args.by,
        "agent": args.agent or "",
        "status": "draft",
        "created_at": now(),
        "eval_verdict": None,
        "champion_metrics": None,
        "challenger_metrics": None,
        "promoted_at": None,
        "superseded_by": None,
    }
    _evo_append(w, rec)
    print(f"{rec['candidate_id']}  [{args.component}] {args.change_type}  {args.change_set[:60]}")
    return rec


def cmd_evo_eval(w, args):
    """记录 Champion/Challenger 评测对比（v2.2 §11.4 隔离评测）。"""
    rec = dict(_evo_latest(w, args.candidate))
    if rec["status"] not in EVO_EVALUABLE:
        raise SystemExit(f"错误：{args.candidate} 状态为 {rec['status']}，不可评测"
                         f"（仅 {'/'.join(EVO_EVALUABLE)} 可评测）")
    if args.verdict not in ("promote", "reject", "keep"):
        raise SystemExit("错误：verdict 须为 promote/reject/keep")
    rec["status"] = "challenger" if args.verdict == "promote" else \
        ("evaluating" if args.verdict == "keep" else "rejected")
    rec["eval_verdict"] = args.verdict
    rec["champion_metrics"] = _evo_metrics("--champion", args.champion)
    rec["challenger_metrics"] = _evo_metrics("--challenger", args.challenger)
    rec["evaluated_at"] = now()
    _evo_append(w, rec)
    print(f"{args.candidate}  verdict={args.verdict}  → status={rec['status']}")
    return rec


def cmd_evo_promote(w, args):
    """晋级候选为新 Champion；旧 Champion 标记 superseded（v2.2 §11.5）。

    §11.4：候选版本不得直接替换当前 Champion——只有评测结论为 promote 且带指标的
    Challenger 才能晋级。
    """
    evos = w.evolutions()
    rec = _evo_latest(w, args.candidate)
    if rec["status"] != "challenger" or rec.get("eval_verdict") != "promote":
        raise SystemExit(f"错误：{args.candidate} 不可晋级（status={rec['status']}，"
                         f"eval_verdict={rec.get('eval_verdict')}）；"
                         f"须先 wf evo-eval --candidate {args.candidate} --verdict promote")
    if not rec.get("challenger_metrics"):
        raise SystemExit(f"错误：{args.candidate} 没有评测指标，不能晋级（evo-eval 时给 --challenger）")
    component = rec["target_component"]
    for cid, c in evos.items():
        if c["target_component"] == component and c["status"] == "champion" and cid != args.candidate:
            c2 = dict(c)
            c2["status"] = "superseded"
            c2["superseded_by"] = args.candidate
            _evo_append(w, c2)
            # 谱系进证据图：新 Champion 取代旧 Champion（与决策的 supersedes 边同约定）
            w.add_edge_safe(args.candidate, cid, "supersedes", note="晋级取代旧 champion",
                            agent=args.agent or "wf-evo")
            print(f"  superseded 旧 champion：{cid}")
    rec = dict(rec)
    rec["status"] = "champion"
    rec["promoted_at"] = now()
    _evo_append(w, rec)
    print(f"{args.candidate} → champion（{component}）")
    return rec


def cmd_evo_rollback(w, args):
    """回滚当前 Champion，并恢复它当初取代的那个 Champion（v2.2 §11.6）。

    回滚是新的版本化事件：追加记录，不删除失败候选及其证据。
    """
    evos = w.evolutions()
    rec = dict(_evo_latest(w, args.candidate))
    if rec["status"] != "champion":
        raise SystemExit(f"错误：{args.candidate} 不是当前 champion（status={rec['status']}），无可回滚")
    rec["status"] = "rolled_back"
    rec["rolled_back_at"] = now()
    rec["rolled_back_reason"] = args.reason or ""
    restored = []
    for cid, c in evos.items():
        if c.get("superseded_by") == args.candidate and c["status"] == "superseded":
            c2 = dict(c)
            c2["status"] = "champion"
            c2["superseded_by"] = None
            c2["restored_at"] = now()
            c2["restored_by_rollback"] = args.candidate
            _evo_append(w, c2)
            w.add_edge_safe(cid, args.candidate, "supersedes", note=f"回滚恢复：{args.reason or '无理由'}",
                            agent=args.agent or "wf-evo")
            restored.append(cid)
            print(f"  恢复旧 champion：{cid}")
    _evo_append(w, rec)
    print(f"{args.candidate} → rolled_back"
          + (f"（已恢复 {', '.join(restored)}）" if restored else "（此前无 champion，该组件当前无 champion）"))
    return dict(rec, restored=restored)


def cmd_evo_list(w, args):
    evos = w.evolutions()
    if not evos:
        print("（无演进候选）")
        return
    for cid, rec in sorted(evos.items(), key=lambda x: x[1].get("created_at", "")):
        print(f"{rec['status']:12s} {cid}  [{rec['target_component']}] "
              f"{rec.get('change_type', '')}  {rec.get('change_set', '')[:50]}")


def cmd_evo_state(w, args):
    """导出演进状态（演进中心前端数据源）。"""
    evos = w.evolutions()
    champions = {}
    for cid, rec in evos.items():
        if rec["status"] == "champion":
            champions[rec["target_component"]] = cid
    state = {
        "schema_version": "0.2",
        "generated_at": now(),
        "evolutions": sorted(evos.values(), key=lambda r: r.get("created_at", "")),
        "champions": champions,
        # 谱系：晋级/回滚时写入证据图的 supersedes 边（from 取代 to）
        "lineage": [{"from": e["from"], "to": e["to"]}
                    for e in w.graph()
                    if e["edge_type"] == "supersedes"
                    and e["from"].startswith("EVO-") and e["to"].startswith("EVO-")],
        "summary": {
            "total": len(evos),
            "champions": len(champions),
            "challengers": sum(1 for r in evos.values() if r["status"] == "challenger"),
            "rejected": sum(1 for r in evos.values() if r["status"] == "rejected"),
            "draft": sum(1 for r in evos.values() if r["status"] == "draft"),
            "superseded": sum(1 for r in evos.values() if r["status"] == "superseded"),
            "rolled_back": sum(1 for r in evos.values() if r["status"] == "rolled_back"),
        },
    }
    if args.out:
        save_json(args.out, state)
        print(f"evolution-state.json 已写入：{args.out}（{len(evos)} 候选 / {len(champions)} champion）")
    else:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    return state


def cmd_vcheck(w, args):
    r = w.vcheck(args.pkg)
    print(f"{r['pkg']}（{r['layer']}）  验证强度：当前 {r['current']} / 推荐 {r['recommended']}")
    if r["gaps"]:
        for g in r["gaps"]:
            print(f"  缺口：{g}")
    else:
        print("  无缺口（达到层推荐强度）")


# ---------------------------------------------------------------- v2 层总线：按模板建层包

DEFAULT_NAMESPACE = "general"        # 新任务的层包统一放在这里（用户不可见）；旧任务的层包留在原领域目录
DEFAULT_TEMPLATE = "microarch_optimize"


def _ensure_namespace(w, namespace):
    """层包在 store 里必须挂在某个目录下（identity.domain_pack + domains/<ns>/L*/…）。
    「领域」这个概念已经停用：新任务的层包统一放在一个用户看不见的默认位置，第一次用到时创建。"""
    ns = namespace or DEFAULT_NAMESPACE
    d = w.domains / ns
    if not (d / "pack.json").exists():
        for sub in LAYER_DIR.values():
            (d / sub).mkdir(parents=True, exist_ok=True)
        pack = {
            "identity": {"pack_id": f"DOMAIN-{ns}", "name": ns,
                         "schema_version": SCHEMA_VERSION, "version": "0.1.0"},
            "description": "默认存放位置（「领域」概念已停用；任务记录见 tasks/）",
            "benchmarks": [], "metric_definitions": [], "rtl_units": [],
            "adapters": [], "calibration": {"status": "uncalibrated", "evidence_refs": []},
            "spec_task_links": [], "layer_ownership": {},
        }
        errs = validate(pack, w.schema("domain_pack"))
        if errs:
            raise SystemExit("存放位置描述不符合 schema：\n  " + "\n  ".join(errs))
        save_json(d / "pack.json", pack)
    return ns


def _create_mission_packages(w, namespace, template, cases=1, intent="", owner="",
                             iteration_id="", task_id="", tag="", agent=""):
    """按场景模板生成层包与依赖边（new-mission、task-new --create、iter-new --create 共用）。
    tag 给出时进包名（PKG-<层>-<存放位置>-<tag>-<别名>），同一模板开多个迭代也不会撞名；
    iteration_id / task_id 写进 provenance，包一建出来就有所属。返回新建的 package_id。"""
    ns = _ensure_namespace(w, namespace)
    lb = _load_layer_bus()
    expanded = lb.expand_template(template, cases)
    alias_to_pkg, created = {}, []
    for node in expanded["nodes"]:
        comp_id = node["component"]
        comp = lb.LAYER_COMPONENTS[comp_id]
        alias = node["alias"]
        pkg_id = f"PKG-{comp_id}-{ns}-{tag}-{alias}" if tag else f"PKG-{comp_id}-{ns}-{alias}"
        alias_to_pkg[alias] = pkg_id
        layer_num = comp_id.rstrip("d")
        layer_dir = LAYER_DIR.get(layer_num, "L5_rtl")
        pkg_dir = w.domains / ns / layer_dir / pkg_id
        if pkg_dir.exists():
            print(f"  已存在：{pkg_id}")
            continue
        pkg_dir.mkdir(parents=True)
        provenance = {"parent_package_refs": [], "spec_refs": [],
                      "created_at": now(), "updated_at": now(),
                      "mission_template": template}
        if iteration_id:
            provenance["iteration_id"] = iteration_id
        if task_id:
            provenance["task_id"] = task_id
        env = {
            "identity": {
                "layer_id": layer_num, "package_id": pkg_id,
                "schema_version": "2.0", "domain_pack": f"DOMAIN-{ns}",
                "title": f"[{alias}] {comp['name']}（{template}）",
                "owner_agent": owner or "", "layer_name": LAYER_NAME.get(layer_num, ""),
                "component_id": comp_id, "component_alias": alias,
            },
            "provenance": provenance,
            "design_spec": {"intent": intent or "", "assumptions": [], "constraints": []},
            "artifacts": [],
            "evidence": {"observation_refs": [], "derived_fact_refs": [],
                         "candidate_refs": [], "recommendation_refs": []},
            "decision_refs": [],
            "validation": {"test_definition_refs": [], "test_run_refs": [],
                           "test_result_refs": [], "aggregate_verdict": "not_gated"},
            "lifecycle": {"status": "draft", "invalidation_reasons": [], "history": []},
            "payload": {
                "input_type": "+".join(comp["input_types"]) or None,
                "output_type": comp["output_type"],
                "output": {},
                "_template_required": expanded["gate_overrides"].get(alias, {})
                                                       .get("required_fields", []),
            },
        }
        save_json(pkg_dir / "envelope.json", env)
        created.append(pkg_id)
        print(f"  创建：{pkg_id}（{comp['name']}）")

    for edge in expanded["edges"]:
        from_pkg = alias_to_pkg.get(edge["from"])
        to_pkg = alias_to_pkg.get(edge["to"])
        if not (from_pkg and to_pkg):
            continue
        for env_path in w.domains.glob(f"{ns}/L*/PKG-*/envelope.json"):
            e = load_json(env_path)
            if e["identity"]["package_id"] == to_pkg:
                if from_pkg not in e["provenance"]["parent_package_refs"]:
                    e["provenance"]["parent_package_refs"].append(from_pkg)
                    save_json(env_path, e)
                break
        print(f"  边：{edge['from']} -> {edge['to']}")
    return created


def cmd_new_mission(w, args):
    """按场景模板建层包（层总线 v2）。不带任务章程与迭代归属的旧入口；新任务请用 wf task-new。"""
    lb = _load_layer_bus()
    tpl = lb.get_template(args.template)
    expanded = lb.expand_template(args.template, case_count=args.cases)
    ns = _ensure_namespace(w, getattr(args, "domain", None))

    print(f"模板：{args.template}（{tpl['description']}）")
    print(f"组件数：{len(expanded['nodes'])}，边数：{len(expanded['edges'])}")

    _create_mission_packages(w, ns, args.template, cases=args.cases,
                             intent=args.intent, owner=args.owner, agent=args.agent)
    print(f"\n层包已创建。下一步：wf brief <pkg>（看上游接口卡与本层要求）→ 填 payload / interface → wf gate <pkg>")


# ---------------------------------------------------------------- 任务与迭代：benchmark 导入、立项、开迭代、封板

def _task_slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower()[:40].strip("-")


def _goal_text(goal):
    """任务章程正文：目标原话在最前（不变量）；历史章程里另有判据/预算等时附在后面存档。"""
    lines = [f"目标：{goal.get('statement', '')}"]
    if goal.get("success_criteria"):
        lines.append("历史章程里的判据（存档，不作为输入项）：")
        lines += [f"  {n}. {c}" for n, c in enumerate(goal["success_criteria"], 1)]
    for key, label in GOAL_FIELDS:
        if goal.get(key):
            lines.append(f"{label}（存档）：{goal[key]}")
    return "\n".join(lines)


def _guess_artifact_type(path):
    ext = Path(path).suffix.lower()
    return {".sv": "rtl_source", ".v": "rtl_source", ".vh": "rtl_source", ".svh": "rtl_source",
            ".s": "kernel", ".asm": "kernel", ".c": "kernel", ".cc": "kernel", ".cpp": "kernel",
            ".py": "script", ".sh": "script",
            ".json": "config", ".yaml": "config", ".yml": "config", ".toml": "config",
            ".csv": "data", ".tsv": "data", ".txt": "data", ".log": "log", ".md": "spec_doc",
            }.get(ext, "binary" if not ext else "other")


def _bench_import(w, name, kind, note, art_type, paths, agent=""):
    """benchmark 入册并写进全局清单。文件逐个哈希入册（小文件复制进 store）；
    目录只把文件清单（相对路径 + 每个文件的 sha256）入册，目录本身不复制。返回清单条目。"""
    name = (name or "").strip()
    if not name:
        raise SystemExit("错误：benchmark 名字不能为空")
    new_refs = []
    for raw in paths:
        p = Path(raw).resolve()
        if not p.exists():
            raise SystemExit(f"错误：路径不存在：{raw}")
        if p.is_dir():
            files = sorted(q for q in p.rglob("*") if q.is_file() and not q.is_symlink())
            if not files:
                raise SystemExit(f"错误：目录里没有文件：{p}")
            if len(files) > BENCH_IMPORT_MAX_FILES:
                raise SystemExit(f"错误：目录文件数 {len(files)} 超过上限 {BENCH_IMPORT_MAX_FILES}，请分批导入")
            entries, total = [], 0
            for q in files:
                size = q.stat().st_size
                total += size
                entries.append({"path": str(q.relative_to(p)), "size": size, "sha256": sha256_file(q)})
            rec = w.register_manifest(p.name, {"root": str(p), "file_count": len(entries),
                                               "total_size": total, "files": entries},
                                      p, note=f"benchmark 清单：{name}", agent=agent)
        elif p.is_file():
            rec = w.register_artifact(p, artifact_type=art_type or _guess_artifact_type(p),
                                      note=f"benchmark：{name}" + (f"（{note}）" if note else ""),
                                      role="benchmark", agent=agent)
        else:
            raise SystemExit(f"错误：不是普通文件或目录：{raw}")
        if rec["artifact_id"] not in new_refs:
            new_refs.append(rec["artifact_id"])
    prev = w.benchmarks().get(name, {})
    refs = list(prev.get("artifact_refs", []))
    for r in new_refs:
        if r not in refs:
            refs.append(r)
    entry = {"name": name, "kind": (kind or "").strip() or prev.get("kind", ""),
             "artifact_refs": refs, "imported_at": now(), "agent": agent}
    append_jsonl(w.idx_benchmarks, entry)
    return {**entry, "new_refs": new_refs}


def cmd_bench_import(w, args):
    res = _bench_import(w, args.name, args.kind, args.note, args.type, args.paths, agent=args.agent)
    print(f"\nbenchmark 已导入：{res['name']} —— {', '.join(res['new_refs']) or '（内容已登记）'}")
    print(f"  立项：wf task-new --title … --goal … --benchmark {res['name']} --template …")


def _resolve_benchmarks(w, refs):
    """--benchmark 的每一项：全局清单里的名字，或已登记的工件 id。
    （旧领域包里登记过、还没并进全局清单的 benchmark 也认。）"""
    arts = w.artifacts()
    known = dict(w.benchmarks())
    for p in sorted(w.domains.glob("*/pack.json")):
        try:
            for b in json.loads(p.read_text(encoding="utf-8")).get("benchmarks", []):
                known.setdefault(b.get("name", ""), b)
        except (OSError, ValueError):
            continue
    out, seen = [], set()
    for ref in refs:
        hit = known.get(ref)
        if hit:
            item = {"name": hit["name"], "kind": hit.get("kind", ""),
                    "artifact_refs": list(hit.get("artifact_refs", []))}
        elif ref in arts:
            item = {"name": Path(arts[ref].get("uri", "")).name or ref,
                    "kind": arts[ref].get("artifact_type", ""), "artifact_refs": [ref]}
        else:
            raise SystemExit(f"错误：benchmark 不认识：{ref}"
                             "（先 wf bench-import 导入，或给已登记的 ART- id）")
        if item["name"] not in seen:
            seen.add(item["name"])
            out.append(item)
    return out


def _signer_or_pending(args):
    """--as 给真实署名 → 章程/开迭代视为已签；不给 → 待批（pending-user）。"""
    signer = (getattr(args, "as", "") or "").strip()
    if signer == "pending-user":
        raise SystemExit("错误：--as 要么给真实署名（视为已签），要么不给（待批）")
    return signer


def cmd_task_new(w, args):
    """立项：标题 + 目标（不变量）+ benchmark + 流程模板。写成任务记录与章程决策。
    没有成功判据：任务长什么样、怎样算完成，只由流程模板决定（本轮每个框都已验证）。"""
    title = (args.title or "").strip()
    statement = (args.goal or "").strip()
    if not title or not statement:
        raise SystemExit("错误：--title 与 --goal 不能为空")
    template = args.template or DEFAULT_TEMPLATE
    if template not in FLOW_TEMPLATES:
        raise SystemExit(f"错误：未知模板（可选：{', '.join(FLOW_TEMPLATES)}）")
    benchmarks = _resolve_benchmarks(w, args.benchmark)
    if not benchmarks:
        raise SystemExit("错误：至少给一个 --benchmark（先 wf bench-import 导入）")
    signer = _signer_or_pending(args)
    existing = w.tasks()
    slug = _task_slug(args.slug) or _task_slug(title)
    if not slug:
        n = 1
        while f"t{n}" in {t["task_id"].removeprefix("TASK-") for t in existing.values()}:
            n += 1
        slug = f"t{n}"
    task_id = f"TASK-{slug}"
    if task_id in existing:
        raise SystemExit(f"错误：任务已存在：{task_id}，换 --slug")
    ts, status = now(), ("active" if signer else "draft")
    who = args.agent or signer
    task = {
        "task_id": task_id, "schema_version": "1.1", "namespace": DEFAULT_NAMESPACE,
        "title": title, "status": status, "goal": {"statement": statement},
        "benchmarks": benchmarks, "flow_template": template,
        "charter_decision_id": "", "iterations": [],
        "created_at": ts, "created_by": who, "updated_at": ts, "note": args.note or "",
    }
    if template != "benchmark_only":
        task["iterations"].append({
            "iteration_id": f"ITER-{slug}-1", "seq": 1, "label": "迭代 1",
            "status": status, "title": "迭代 1", "reason": "首轮", "started_at": ts,
            "entry_decision_id": "", "package_ids": [], "flow_template": template,
            "created_at": ts, "created_by": who,
        })
    task = w.save_task(task)
    rec = w.add_decision(
        "select", f"目标：{statement}\n模板：{template}\nbenchmark：{', '.join(b['name'] for b in benchmarks)}",
        "high", signer or "pending-user",
        summary=f"任务章程：{title}"[:DECISION_SUMMARY_MAX],
        evidence_refs=[r for b in benchmarks for r in b["artifact_refs"]],
        scope=f"{task_id} 任务章程", agent=args.agent, task_id=task_id)
    task["charter_decision_id"] = rec["decision_id"]
    w.add_edge_safe(task_id, rec["decision_id"], "decided_by", note="charter", agent=args.agent)
    created = []
    if args.create and template != "benchmark_only":
        created = _create_mission_packages(
            w, DEFAULT_NAMESPACE, template, intent=statement, owner=args.owner,
            iteration_id=task["iterations"][0]["iteration_id"], task_id=task_id,
            tag=f"{slug}-i1", agent=args.agent)
    w.save_task(task)
    print(f"已立项：{task_id}（{status}）—— {title}")
    print(f"目标（不变量）：{statement}")
    print(f"模板：{template}｜benchmark：{', '.join(b['name'] for b in benchmarks)}")
    if signer:
        print(f"  章程决策：{rec['decision_id']}（已签：{signer}）")
    else:
        print(f"  章程决策：{rec['decision_id']}（待批：wf approve {rec['decision_id']} --as <署名>）")
    if created:
        print(f"  已生成迭代 1 的 {len(created)} 个层包；下一步：wf next --iteration {task['iterations'][0]['iteration_id']}")
    elif template == "benchmark_only":
        print("  该模板不走层：没有层包；记下 benchmark 的结果后由人封板（wf task-seal）")
    elif task["iterations"]:
        print("  迭代 1 尚无层包：用 wf new-pkg --iteration " + task["iterations"][0]["iteration_id"]
              + " --layer … --slug … 逐层建包")
    return task


def cmd_iter_new(w, args):
    """开下一轮迭代。理由必填：每次 L8 后的 Mission Review 才决定要不要再迭代。"""
    task = w.find_task(args.task)
    if task["status"] not in TASK_LIVE:
        raise SystemExit(f"错误：{task['task_id']} 已 {task['status']}，重开须新章程（wf task-new）")
    reason = (args.reason or "").strip()
    if not reason:
        raise SystemExit("错误：开下一轮必须用 --reason 写明理由（上一轮的结论 / Mission Review 的结论）")
    template = args.template or task.get("flow_template") or DEFAULT_TEMPLATE
    if template == "benchmark_only":
        raise SystemExit("错误：该任务的模板不走层，没有下一轮；要再跑一轮请新建任务")
    its = sorted(task.get("iterations", []), key=lambda i: i.get("seq", 0))
    running = [it for it in its if it.get("status") in TASK_LIVE]
    if running:
        raise SystemExit("错误：" + "、".join(it["label"] for it in running)
                         + " 还在进行中，先结束它再开下一轮（wf iter-seal 或 wf iter-abort）")
    seq = (its[-1].get("seq", 0) + 1) if its else 1
    slug = task["task_id"].removeprefix("TASK-")
    signer = _signer_or_pending(args)
    ts, status, who = now(), ("active" if signer else "draft"), (args.agent or signer)
    it = {"iteration_id": f"ITER-{slug}-{seq}", "seq": seq, "label": f"迭代 {seq}",
          "status": status, "title": args.title or f"迭代 {seq}", "reason": reason,
          "started_at": ts, "entry_decision_id": "", "package_ids": [],
          "flow_template": template, "created_at": ts, "created_by": who}
    rec = w.add_decision("select", reason, "medium", signer or "pending-user",
                         summary=f"开迭代 {seq}：{reason}"[:DECISION_SUMMARY_MAX],
                         scope=f"{task['task_id']} 迭代 {seq}", agent=args.agent,
                         task_id=task["task_id"])
    it["entry_decision_id"] = rec["decision_id"]
    task["iterations"].append(it)
    w.save_task(task)
    w.add_edge_safe(task["task_id"], rec["decision_id"], "decided_by",
                    note=f"iteration-{seq}", agent=args.agent)
    created = []
    if args.create:
        created = _create_mission_packages(
            w, task.get("namespace"), template, intent=reason, owner=args.owner,
            iteration_id=it["iteration_id"], task_id=task["task_id"],
            tag=f"{slug}-i{seq}", agent=args.agent)
    print(f"已开 {it['iteration_id']}（{status}）：{reason}")
    print(f"  开迭代决策：{rec['decision_id']}" + (f"（已签：{signer}）" if signer
                                                else f"（待批：wf approve {rec['decision_id']} --as <署名>）"))
    if created:
        print(f"  已生成 {len(created)} 个层包；下一步：wf next --iteration {it['iteration_id']}")
    return it


def _signed(w, args, verb):
    signer = (getattr(args, "as", "") or "").strip()
    reason = (getattr(args, "reason", "") or "").strip()
    if not signer or signer == "pending-user":
        raise SystemExit(f"错误：{verb} 必须用 --as 署名（不能是 pending-user）——这是人的判断，要留名")
    if not reason:
        raise SystemExit(f"错误：{verb} 必须用 --reason 写明理由")
    return signer, reason


def cmd_task_seal(w, args):
    """封板（SHIP）：任务及其进行中的迭代一并结束，留一条署名决策。"""
    signer, reason = _signed(w, args, "封板")
    task = w.find_task(args.task)
    if task["status"] == "sealed":
        raise SystemExit(f"错误：{task['task_id']} 已封板")
    if task["status"] not in TASK_LIVE:
        raise SystemExit(f"错误：{task['task_id']} 已 {task['status']}，不能封板")
    if task.get("charter_decision_id") in {d["decision_id"] for d in w.pending_decisions()}:
        raise SystemExit(f"错误：{task['task_id']} 的章程尚未批准，不能封板")
    rec = w.add_decision("ratify", reason, "high", signer,
                         summary=f"封板：{reason}"[:DECISION_SUMMARY_MAX],
                         scope=f"{task['task_id']} 封板（SHIP）", agent=args.agent,
                         task_id=task["task_id"])
    for it in task["iterations"]:
        if it.get("status") in TASK_LIVE:
            it["status"] = "sealed"
    task["status"] = "sealed"
    w.save_task(task)
    w.add_edge_safe(task["task_id"], rec["decision_id"], "decided_by", note="seal", agent=args.agent)
    print(f"{task['task_id']} 已封板（{rec['decision_id']}，签署 {signer}）")


def _close_iteration(w, args, outcome):
    verb = "结束" if outcome == "sealed" else "终止"
    signer, reason = _signed(w, args, f"{verb}迭代")
    task, it = w.find_iteration(args.iteration)
    if it.get("status") not in TASK_LIVE:
        raise SystemExit(f"错误：{args.iteration} 已结束或不存在")
    rec = w.add_decision("ratify" if outcome == "sealed" else "reject", reason, "high", signer,
                         summary=f"{verb}{it['label']}：{reason}"[:DECISION_SUMMARY_MAX],
                         scope=f"{task['task_id']} {it['label']}", agent=args.agent,
                         task_id=task["task_id"])
    it["status"] = outcome
    w.save_task(task)
    w.add_edge_safe(task["task_id"], rec["decision_id"], "decided_by",
                    note=f"iteration-{it.get('seq')}:{outcome}", agent=args.agent)
    print(f"{it['iteration_id']} 已{verb}（{rec['decision_id']}，签署 {signer}）")


def cmd_iter_seal(w, args):
    _close_iteration(w, args, "sealed")


def cmd_iter_abort(w, args):
    _close_iteration(w, args, "aborted")


def cmd_task_list(w, args):
    tasks = sorted(w.tasks(args.domain).values(), key=lambda t: (t.get("created_at") or "", t["task_id"]))
    if not tasks:
        print("没有任务。立项：wf task-new --title … --goal \"目标原话\" --benchmark … --template …")
        return tasks
    for t in tasks:
        its = sorted(t.get("iterations", []), key=lambda i: i.get("seq", 0))
        it_desc = "、".join(f"{i.get('label')}({i.get('status')})" for i in its) or "不走层"
        print(f"{t['task_id']}  [{t.get('status')}｜{t.get('flow_template', '')}]  {t.get('title', '')}")
        print(f"    {it_desc}｜benchmark：{', '.join(b['name'] for b in t.get('benchmarks', [])) or '—'}")
    return tasks


def cmd_task_show(w, args):
    task = w.find_task(args.task)
    print(f"# {task.get('title', '')}（{task['task_id']}｜{task.get('status')}｜模板：{task.get('flow_template', '')}）")
    print(_goal_text(task.get("goal", {})))
    for b in task.get("benchmarks", []):
        print(f"  ▸ benchmark：{b.get('name')} —— {b.get('kind') or '—'} {' '.join(b.get('artifact_refs', []))}")
    pending = {d["decision_id"] for d in w.pending_decisions()}
    decisions = w.decisions()
    tasks = w.tasks()
    for it in sorted(task.get("iterations", []), key=lambda i: i.get("seq", 0)):
        print(f"\n{it.get('label')}（{it.get('status')}，{it.get('started_at', '')}）"
              + (f" —— {it.get('reason')}" if it.get("reason") else ""))
        layers = w.iteration_layers(task, it)
        for L in ("L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"):
            for kind in ("own", "reused"):
                for e in layers[L][kind]:
                    c = layer_card(w, e, tasks)
                    pend = sum(1 for r in c["receipts"] if r["pending"])
                    print(f"  {L} {'沿用' if kind == 'reused' else '自有'}  [{c['state']['word']}]  "
                          f"{e['identity']['package_id']}  {e['identity'].get('title', '')[:44]}"
                          f"｜交付 {len(c['interface'].get('gives', []))} 条｜待人决定 {pend}")
        if it.get("entry_decision_id") in pending:
            print(f"  ⚠ 开迭代决策 {it['entry_decision_id']} 待批")
    print("\n人的决定（任务级：章程 / 开迭代 / 封板）：")
    for d in sorted(decisions.values(), key=lambda d: d.get("decided_at", "")):
        if d.get("task_id") == task["task_id"]:
            mark = "待人决定" if d["decision_id"] in pending else d.get("reviewer", "")
            print(f"  {d['decision_id']} [{mark}] {decision_headline(d, decisions.get)[0]}")
    return task


# ---------------------------------------------------------------- 人类视图（show / review）

def _trunc(s, n=110):
    s = str(s).replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


def _show_L1(p):
    for b in p.get("benchmarks", []):
        print(f"  ▸ benchmark：{b.get('name')} —— {_trunc(b.get('kind', ''), 80)}")
    for m in p.get("metric_definitions", []):
        print(f"  ▸ 指标：{m.get('name')}（{m.get('unit')}）{_trunc(m.get('definition',''), 60)}")
    for k, val in (p.get("data_quality") or {}).items():
        print(f"  ▸ 数据质量·{k}：{_trunc(val)}")


def _show_L2(p):
    pr = p.get("proposition") or {}
    print(f"  ▸ 命题：{_trunc(pr.get('statement',''), 160)}")
    print(f"    预测方向：{_trunc(pr.get('predicted_direction',''), 90)}")
    print(f"    失败条件：{_trunc(pr.get('failure_condition',''), 90)}")
    print(f"  ▸ 归因判定：{p.get('attribution_verdict')}")
    for k, val in (p.get("measured") or {}).items():
        if not isinstance(val, (dict, list)):
            print(f"    {k} = {val}")


def _show_L3(p):
    for c in p.get("candidates", []):
        print(f"  ▸ 候选 {c.get('candidate_id')} → 对应瓶颈 {c.get('targets_bottleneck','')}")
    ac = p.get("acceptance_criteria") or {}
    for k, br in (ac.get("bottleneck_resolution") or {}).items():
        if isinstance(br, dict):
            print(f"  ▸ 判据 {k}：[{br.get('status','?')}] {_trunc(br.get('criterion',''), 90)}")
    for k, mf in (ac.get("mechanism_fulfillment") or {}).items():
        print(f"  ▸ 机制兑现 {k}：{_trunc(mf if isinstance(mf, str) else mf.get('criterion', mf), 90)}")
    if ac.get("explicitly_excluded"):
        print(f"  ▸ 明确不设：{_trunc(ac['explicitly_excluded'])}")


def _show_L4(p):
    cm = None
    for k in ("contract_manifest",):
        cm = _show_env_cache.get(k)
    print(f"  ▸ 契约接口 {len((cm or {}).get('interfaces', []))} 个；"
          f"资源参数 {len(p.get('resource_params', []))} 项"
          f"（assumed {sum(1 for r in p.get('resource_params', []) if r.get('verification')=='assumed')}"
          f"/sanity {sum(1 for r in p.get('resource_params', []) if r.get('verification')=='sanity_checked')}）")


def _show_L5(p):
    for m in p.get("rtl_modules", []):
        print(f"  ▸ RTL 模块：{m.get('name')} {_trunc(m.get('note',''), 70)}")
    print(f"  ▸ 验证强度：{p.get('verification_strength','?')}")


def _show_L6(p):
    for m in p.get("simobject_mappings", []):
        print(f"  ▸ 对接：{m.get('simobject')} ↔ {m.get('rtl_module')}")


def _show_L7(p):
    env = p.get("environment") or {}
    print(f"  ▸ 环境绑定 {len(env)} 项；护栏：{_trunc(str(p.get('guards','')), 80)}")
    print(f"  ▸ 复现命令：{_trunc(p.get('reproduce_command',''), 100)}")


def _show_L8(p):
    for c in p.get("conclusions", []):
        print(f"  ▸ 结论：[{c.get('model_causal_status')}/{c.get('hardware_evidence_status')}] "
              f"{_trunc(c.get('statement',''), 130)}")
    for cmp_ in p.get("comparisons", []):
        print(f"  ▸ 对比：{cmp_.get('lhs')} vs {cmp_.get('rhs')} = {cmp_.get('delta')}"
              f"（{cmp_.get('frame','?')}）")
    print(f"  ▸ 边界：{_trunc(p.get('validity_scope',''), 130)}")


_SHOW_RENDERERS = {"L1": _show_L1, "L2": _show_L2, "L3": _show_L3, "L4": _show_L4,
                   "L5": _show_L5, "L6": _show_L6, "L7": _show_L7, "L8": _show_L8}
_show_env_cache = {}


def cmd_show(w, args):
    """人类可读的层包视图：不显示哈希/ID 细节，按阅读顺序渲染。"""
    env_path, env = w.resolve_pkg(args.pkg)
    i, v, lc = env["identity"], env["validation"], env["lifecycle"]
    mark = {"passed": "✅", "needs_decision": "⏸", "failed": "✗",
            "draft": "○", "invalidated": "⊘", "superseded": "⊘"}.get(lc["status"], "·")
    print(f"{mark} {i['package_id']}（{i['layer_id']}·{i.get('layer_name','')}）"
          f"  [{lc['status']} / 门禁:{v.get('aggregate_verdict','?')}]")
    print(f"  {i['title']}   ｜owner {i.get('owner_agent') or '?'}")
    print(f"  意图：{_trunc(env['design_spec']['intent'], 120)}")
    for a in env["design_spec"].get("assumptions", [])[:4]:
        print(f"  假设·{_trunc(a, 105)}")
    by_id = {e["identity"]["package_id"]: e for _, e in w.find_packages()}
    for pid in env.get("provenance", {}).get("parent_package_refs", []):
        pe = by_id.get(pid)
        if pe:
            print(f"  ↑ 消费上游：{pid} [{pe['validation'].get('aggregate_verdict')}] {_trunc(pe['identity']['title'], 60)}")
    arts = env.get("artifacts", [])
    print(f"  证据工件 {len(arts)} 件" + (f"（{'、'.join(Path(a['uri']).name for a in arts[:3])}{'…' if len(arts) > 3 else ''}）" if arts else ""))
    if v.get("test_result_refs"):
        results = w.results()
        last = v["test_result_refs"][-1]
        r = results.get(last)
        print(f"  测试：{len(v.get('test_definition_refs',[]))} 定义/{len(v.get('test_run_refs',[]))} 运行，最新结果 {last} = {r.get('verdict') if r else '?'}")
    if v.get("gate_report"):
        print(f"  门禁报告：{_trunc(v['gate_report'], 130)}")
    decs = w.decisions()
    for dr in env.get("decision_refs", []):
        d = decs.get(dr)
        if not d:
            continue
        tag = "待批" if d.get("reviewer") == "pending-user" else d.get("reviewer", "")
        print(f"  决策 {dr} [{d['decision_type']}·{d['confidence']}·{tag}] {_trunc(d.get('rationale',''), 100)}")
    _show_env_cache.clear()
    _show_env_cache["contract_manifest"] = env.get("contract_manifest")
    print("  —— 本层内容 ——")
    _SHOW_RENDERERS.get(i["layer_id"], lambda p: None)(env.get("payload", {}))
    if isinstance(env.get("payload", {}).get("measurement_frames_note"), dict):
        print(f"  （基架标签 {len(env['payload']['measurement_frames_note'])-1 if '_source' in env['payload']['measurement_frames_note'] else len(env['payload']['measurement_frames_note'])} 项）")


def cmd_review(w, args):
    """待批决策的人类审批卡片：全文 rationale + 证据解析成文件名 + 建议命令。"""
    pending = w.pending_decisions()
    arts = w.artifacts()
    if not pending:
        print("待批队列为空")
        return
    pkgs = {e["identity"]["package_id"]: e for _, e in w.find_packages()}
    for n, d in enumerate(pending, 1):
        host = [pid for pid, e in pkgs.items() if d["decision_id"] in e.get("decision_refs", [])]
        print("═" * 74)
        print(f"【{n}/{len(pending)}】{d['decision_id']}  [{d['decision_type']}·置信 {d['confidence']}]")
        print(f"  一句话：{decision_headline(d, w.decisions().get)[0]}")
        for pid in host:
            print(f"  所属包：{pid} —— {_trunc(pkgs[pid]['identity']['title'], 60)}")
        for s in d.get("selected_refs", []):
            print(f"  选定：{_trunc(s, 120)}")
        for r in d.get("rejected_refs", []):
            print(f"  驳回：{_trunc(r, 120)}")
        print(f"  理由：{d.get('rationale','')}")
        if d.get("scope"):
            print(f"  范围：{d['scope']}")
        ev = [arts[e]["uri"] for e in d.get("evidence_refs", []) if e in arts]
        flat = []
        for item in ev:
            flat.extend(x for x in str(item).split(","))
        if flat:
            print("  证据：" + "、".join(Path(x.strip()).name for x in flat[:6]))
        print(f"  批准：wf approve {d['decision_id']} --as <署名>")
        print(f"  驳回：wf reject  {d['decision_id']} --as <署名> --reason \"…\"")


# ---------------------------------------------------------------- CLI

def build_parser():
    p = argparse.ArgumentParser(prog="wf", description="通用微架构混合仿真平台流程 CLI（MVP-A）")
    p.add_argument("--root", default=None, help="项目根目录（默认自动推导或 WF_ROOT）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init-store", help="初始化不可变工件与证据存储")
    s.set_defaults(fn=cmd_init_store)

    s = sub.add_parser("new-domain", help="创建领域包骨架（--from-template 带环境/护栏/契约桩）")
    s.add_argument("--name", required=True)
    s.add_argument("--description", default="")
    s.add_argument("--from-template", action="store_true")
    s.set_defaults(fn=cmd_new_domain)

    s = sub.add_parser("new-pkg", help="创建层包脚手架")
    s.add_argument("--domain", default="", help="层包存放位置（一般不用给；默认位置对用户不可见）")
    s.add_argument("--layer", required=True)
    s.add_argument("--slug", required=True)
    s.add_argument("--title", default="")
    s.add_argument("--owner", default="")
    s.add_argument("--intent", default="")
    s.add_argument("--iteration", default="",
                   help="归到哪个迭代（ITER-… 或 TASK-…/<seq>）；不给则该域恰有一个进行中的迭代时归它")
    s.set_defaults(fn=cmd_new_pkg)

    s = sub.add_parser("register-artifact", help="登记工件（哈希入册，小文件默认复制入 store）")
    s.add_argument("path")
    s.add_argument("--type", default="other")
    s.add_argument("--copy", dest="copy", action="store_true", default=None)
    s.add_argument("--no-copy", dest="copy", action="store_false")
    s.add_argument("--note", default="")
    s.add_argument("--role", default="")
    s.add_argument("--agent", default="")
    s.set_defaults(fn=cmd_register_artifact)

    s = sub.add_parser("register-git", help="把 git 仓库 HEAD+diff 摘要登记为环境工件")
    s.add_argument("git", help="git 仓库路径")
    s.add_argument("--name", required=True, help="环境名，如 gem5")
    s.add_argument("--agent", default="")
    s.set_defaults(fn=cmd_register_git)

    s = sub.add_parser("link", help="追加证据 DAG 边")
    s.add_argument("src")
    s.add_argument("dst")
    s.add_argument("--edge", required=True, choices=EDGE_TYPES)
    s.add_argument("--note", default="")
    s.add_argument("--agent", default="")
    s.set_defaults(fn=cmd_link)

    s = sub.add_parser("decide", help="写结构化决策记录并挂到层包或任务（--summary 一句话结论必填）")
    s.add_argument("--pkg", default="", help="挂到哪个层包（与 --task 二选一）")
    s.add_argument("--task", default="", help="挂到哪个任务：章程 / 开迭代 / 封板类决策（与 --pkg 二选一）")
    s.add_argument("--summary", required=True,
                   help=f"一句话结论（≤{DECISION_SUMMARY_MAX} 字）：决定了什么")
    s.add_argument("--type", required=True,
                   choices=["select", "reject", "override", "ratify", "defer"])
    s.add_argument("--select", action="append", default=[])
    s.add_argument("--reject", action="append", default=[])
    s.add_argument("--constraint", action="append", default=[])
    s.add_argument("--evidence", action="append", default=[])
    s.add_argument("--rationale", default="", help="详细依据（可省略；一句话结论已足够时）")
    s.add_argument("--confidence", required=True, choices=["low", "medium", "high"])
    s.add_argument("--reviewer", required=True,
                   help="批准人；pending-user 表示待人类批准")
    s.add_argument("--scope", default="")
    s.add_argument("--agent", default="")
    s.set_defaults(fn=cmd_decide)

    s = sub.add_parser("test-register", help="登记测试定义（可一并挂运行+结果）")
    s.add_argument("--pkg", required=True)
    s.add_argument("--file", required=True, help="测试定义 JSON 文件")
    s.add_argument("--run-artifact", default=None, help="运行证据文件（stats/log）")
    s.add_argument("--verdict", default="pass", choices=["pass", "fail", "unresolved", "not_applicable"])
    s.add_argument("--measured", action="append", default=[], help="k=v 实测效应")
    s.add_argument("--env-hash", default="")
    s.add_argument("--note", default="")
    s.add_argument("--agent", default="")
    s.set_defaults(fn=cmd_test_register)

    s = sub.add_parser("gate", help="对层包执行门禁校验")
    s.add_argument("pkg", nargs="?", default=None)
    s.add_argument("--all", action="store_true", help="对全部（可过滤）层包执行")
    s.add_argument("--domain", default=None)
    s.add_argument("--layer", default=None)
    s.add_argument("--override", action="store_true",
                   help="署名覆盖：解除包自身的 invalidated/superseded 并重新门禁（须 --as 与 --reason）")
    s.add_argument("--as", dest="as", default="", help="覆盖署名")
    s.add_argument("--reason", default="", help="覆盖理由")
    s.add_argument("--agent", default="")
    s.set_defaults(fn=cmd_gate)

    s = sub.add_parser("invalidate", help="失效传播（上游变化→下游 invalidated）")
    s.add_argument("ref", help="PKG-… 或 ART-…")
    s.add_argument("--reason", required=True)
    s.add_argument("--agent", default="")
    s.set_defaults(fn=cmd_invalidate)

    s = sub.add_parser("approve", help="批准一条待批决策（追加 ratify + supersedes，不改历史）")
    s.add_argument("dec", help="DEC-…")
    s.add_argument("--as", dest="as", required=True, help="批准人署名")
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("reject", help="驳回一条待批决策")
    s.add_argument("dec", help="DEC-…")
    s.add_argument("--as", dest="as", required=True)
    s.add_argument("--reason", required=True)
    s.set_defaults(fn=cmd_reject)

    s = sub.add_parser("sweep", help="全量哈希巡查 + 漂移自动失效 + 重新门禁")
    s.add_argument("--domain", default=None)
    s.set_defaults(fn=cmd_sweep)

    s = sub.add_parser("walk", help="八层推进纪律总览：前沿、阻塞项、消费规则警告")
    s.add_argument("--domain", default=None)
    s.add_argument("--iteration", default=None, help="只看这个迭代（ITER-… 或 TASK-…/<seq>）；不给则看进行中的迭代")
    s.set_defaults(fn=cmd_walk)

    s = sub.add_parser("vcheck", help="层包验证强度评估（V0-V3，对照层推荐值）")
    s.add_argument("pkg")
    s.set_defaults(fn=cmd_vcheck)

    s = sub.add_parser("next", help="执行面调度：当前前沿层→角色→待做包；外加任务提示（待批章程、Mission Review）")
    s.add_argument("--domain", default=None)
    s.add_argument("--iteration", default=None, help="只看这个迭代（ITER-… 或 TASK-…/<seq>）；不给则看进行中的迭代")
    s.set_defaults(fn=cmd_next)

    s = sub.add_parser("brief", help="生成给执行面 agent 的工作简报（markdown）")
    s.add_argument("pkg")
    s.set_defaults(fn=cmd_brief)

    s = sub.add_parser("new-mission", help="按场景模板建层包（层总线 v2）。新任务请用 task-new")
    s.add_argument("--template", required=True, choices=list(FLOW_TEMPLATES))
    s.add_argument("--domain", default="", help="层包存放位置（一般不用给；默认位置对用户不可见）")
    s.add_argument("--cases", type=int, default=1, help="批量模式的案例数")
    s.add_argument("--owner", default="")
    s.add_argument("--intent", default="")
    s.set_defaults(fn=cmd_new_mission)

    s = sub.add_parser("bench-import", help="导入 benchmark：负载文件哈希入册；题集目录按文件清单入册（全局清单）")
    s.add_argument("--name", required=True, help="benchmark 名字（全局清单里的唯一键）")
    s.add_argument("--kind", default="", help="种类 / 说明（如 虚拟原型、CVDP agentic）")
    s.add_argument("--type", default="", help="文件类工件的类型（默认按扩展名推断）")
    s.add_argument("--note", default="")
    s.add_argument("--agent", default="")
    s.add_argument("paths", nargs="+", help="文件或目录（可多个）")
    s.set_defaults(fn=cmd_bench_import)

    s = sub.add_parser("task-new",
                       help="立项：标题 + 目标（不变量）+ benchmark + 流程模板；写成任务章程决策。没有成功判据")
    s.add_argument("--title", required=True)
    s.add_argument("--slug", default="", help="任务 ID 里的短名（默认取标题里的 ASCII 部分）")
    s.add_argument("--goal", required=True, help="目标原话（不变量，原样显示在每一层）")
    s.add_argument("--benchmark", action="append", default=[],
                   help="全局清单里的 benchmark 名或已登记的 ART- id（可多条）")
    s.add_argument("--template", default=DEFAULT_TEMPLATE, choices=list(FLOW_TEMPLATES),
                   help="流程模板：任务长什么样只由它决定")
    s.add_argument("--create", action="store_true", help="按模板生成本迭代的层包")
    s.add_argument("--owner", default="")
    s.add_argument("--as", dest="as", default="", help="章程署名；不给则章程待批（pending-user）")
    s.add_argument("--note", default="")
    s.add_argument("--agent", default="")
    s.set_defaults(fn=cmd_task_new)

    s = sub.add_parser("iter-new", help="开下一轮迭代（理由必填；纪律六：一次有理由的再迭代）")
    s.add_argument("--task", required=True)
    s.add_argument("--reason", required=True, help="上一轮 L8 / Mission Review 的结论")
    s.add_argument("--title", default="")
    s.add_argument("--template", default="", choices=[""] + list(FLOW_TEMPLATES))
    s.add_argument("--create", action="store_true", help="按模板生成本迭代的层包")
    s.add_argument("--owner", default="")
    s.add_argument("--as", dest="as", default="", help="开迭代决策的署名；不给则待批")
    s.add_argument("--agent", default="")
    s.set_defaults(fn=cmd_iter_new)

    s = sub.add_parser("task-seal", help="封板（SHIP）：任务及其进行中的迭代一并结束，留署名决策")
    s.add_argument("--task", required=True)
    s.add_argument("--as", dest="as", required=True)
    s.add_argument("--reason", required=True)
    s.add_argument("--agent", default="")
    s.set_defaults(fn=cmd_task_seal)

    s = sub.add_parser("iter-seal", help="结束一轮已完成的迭代（留署名决策）")
    s.add_argument("iteration", help="ITER-… 或 TASK-…/<seq>")
    s.add_argument("--as", dest="as", required=True)
    s.add_argument("--reason", required=True)
    s.add_argument("--agent", default="")
    s.set_defaults(fn=cmd_iter_seal)

    s = sub.add_parser("iter-abort", help="终止一轮不再推进的迭代（留署名决策；数据保留，默认折叠）")
    s.add_argument("iteration", help="ITER-… 或 TASK-…/<seq>")
    s.add_argument("--as", dest="as", required=True)
    s.add_argument("--reason", required=True)
    s.add_argument("--agent", default="")
    s.set_defaults(fn=cmd_iter_abort)

    s = sub.add_parser("task-list", help="列出任务与迭代")
    s.add_argument("--domain", default=None)
    s.set_defaults(fn=cmd_task_list)

    s = sub.add_parser("task-show", help="任务的章程、benchmark、逐迭代逐层状态与任务级决策")
    s.add_argument("task", help="TASK-…（或短名）")
    s.set_defaults(fn=cmd_task_show)

    s = sub.add_parser("card", help="层包的人读卡片：状态、这一层是什么、交付/证据/人的决定、核对回执")
    s.add_argument("pkg", help="PKG-…")
    s.set_defaults(fn=cmd_card)

    s = sub.add_parser("guide", help="每一层回答什么、由谁做、必须交出什么、怎样算已验证；层接口卡骨架")
    s.add_argument("layer", nargs="?", default=None, help="只看某一层（L1…L8）")
    s.set_defaults(fn=cmd_guide)

    s = sub.add_parser("show", help="人类可读的层包视图（不显示哈希等机器细节）")
    s.add_argument("pkg")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("review", help="待批决策审批卡片（全文+证据+命令）")
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("status", help="层包状态总览 + 决策队列 + DAG")
    s.add_argument("--domain", default=None)
    s.add_argument("--layer", default=None)
    s.add_argument("--mermaid", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("state-export", help="聚合领域工作流状态为机器可读 workflow-state.json（只读投影）")
    s.add_argument("--domain", default=None)
    s.add_argument("--out", default=None, help="落盘路径（默认 stdout）")
    s.set_defaults(fn=cmd_state_export)

    s = sub.add_parser("recommend-flow", help="编排 Agent 最小 flow 推荐：读层包状态判断复用/重建")
    s.add_argument("--goal", required=True, choices=list(GOAL_FLOWS), help="使用目标")
    s.add_argument("--domain", default=None)
    s.add_argument("--iteration", default=None, help="整条 flow 限定在这个迭代：自有包优先，其次沿用的上游包")
    s.set_defaults(fn=cmd_recommend_flow)

    s = sub.add_parser("evo-new", help="创建演进候选（RSI §11.3）")
    s.add_argument("--component", required=True, help="演进目标组件（如 orchestration_policy）")
    s.add_argument("--change-type", required=True, choices=EVO_CHANGE_TYPES)
    s.add_argument("--change-set", required=True, help="变更内容描述")
    s.add_argument("--expected-benefit", default="", help="预期收益")
    s.add_argument("--parent", default=None, help="父版本")
    s.add_argument("--scope", default="", help="影响范围（逗号分隔的 Case/领域包）")
    s.add_argument("--eval-plan", default="", help="评测计划引用（EVAL-…）")
    s.add_argument("--by", default="human", choices=["agent", "human"])
    s.add_argument("--agent", default="")
    s.set_defaults(fn=cmd_evo_new)

    s = sub.add_parser("evo-eval", help="记录 Champion/Challenger 评测对比（§11.4）")
    s.add_argument("--candidate", required=True)
    s.add_argument("--verdict", required=True, choices=["promote", "reject", "keep"])
    s.add_argument("--champion", default=None, help="Champion 指标 JSON")
    s.add_argument("--challenger", default=None, help="Challenger 指标 JSON")
    s.set_defaults(fn=cmd_evo_eval)

    s = sub.add_parser("evo-promote", help="晋级 Challenger 为新 Champion（须评测结论为 promote 且带指标）")
    s.add_argument("--candidate", required=True)
    s.add_argument("--agent", default="")
    s.set_defaults(fn=cmd_evo_promote)

    s = sub.add_parser("evo-rollback", help="回滚当前 Champion，并恢复它取代的旧 Champion")
    s.add_argument("--candidate", required=True)
    s.add_argument("--reason", default="")
    s.add_argument("--agent", default="")
    s.set_defaults(fn=cmd_evo_rollback)

    s = sub.add_parser("evo-list", help="列出演进谱系")
    s.set_defaults(fn=cmd_evo_list)

    s = sub.add_parser("evo-state", help="导出演进状态（演进中心前端数据源）")
    s.add_argument("--out", default=None)
    s.set_defaults(fn=cmd_evo_state)

    s = sub.add_parser("selftest", help="故障注入自检（设计文档 §9）")
    s.set_defaults(fn=cmd_selftest)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    root = args.root or default_root()
    w = WF(root)
    if getattr(args, "fn", None) is cmd_selftest:
        return args.fn(w, args)
    if not args.cmd == "init-store" and not w.store.exists():
        raise SystemExit("错误：store 未初始化，请先 wf init-store")
    return args.fn(w, args)


if __name__ == "__main__":
    main()
