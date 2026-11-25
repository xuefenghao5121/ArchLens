#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""viz server — 可视化 agent 工作流本地 Web 服务。

只读投影（复用 wf state-export / recommend-flow / evo-state）+ 写操作面（复用 wf.py 的
写入命令，追加式 store，永不覆盖历史）。

零第三方依赖（stdlib-only）。

请求防护（本机单用户服务，但写端点落在证据 store 上）：
  - Host 校验：只接受回环地址或 IP 字面量，拒绝任意域名——防 DNS rebinding 读到响应
  - 写请求三重校验：Content-Type 必须是 application/json（跨站 fetch 需预检，本服务不应答
    CORS）、Origin 须为回环地址或 --allow-origin 白名单、Sec-Fetch-Site 不得是 cross-site
    ——防别的网页借浏览器对本机发写请求
  - 层包只按 package_id 解析，不接受文件路径
  - 单线程：请求串行执行，避免并发写 store

写操作用幂等键（idempotency_key，与请求内容绑定：同 key 换内容会被拒）防重复提交；
target_version（store_env_hash）做乐观并发校验（可选，界面始终携带）。

用法：
    python3 platform/viz/server.py --port 8899                         # 默认连真实 store
    WF_ROOT=/path/to/sandbox python3 platform/viz/server.py --port 8899  # 连隔离副本
    浏览器打开 http://127.0.0.1:8899（React 前端经 Vite 把 /api 代理到这里）

只读端点（GET）：
    /api/state                           → workflow-state.json（0.4：每个层包带 state 状态词与 card 卡片；
                                           每个迭代带模板顺序的 nodes；每个任务带 receipts 任务级决定回执。
                                           ?domain= 仅为兼容旧参数，界面不再传）
    /api/recommend-flow?goal=research&iteration=ITER-…
                                         → 最小 flow 推荐（给 iteration 则整条 flow 限定在该迭代）
    /api/evo-state                       → 演进状态（含谱系）
    /api/evidence                        → 证据 DAG（兼容保留；界面上的证据页读的是 /api/state 里的卡片）
    /api/benchmarks                      → benchmark 清单（全局一份）
    /api/templates                       → 流程模板：每个模板有哪些框（层）
    /api/guide                           → 每层的固定说明，以及几个标记（实测 / 推断 / 拍定、F0–F3）的人读含义
    /api/package/<package-id>            → 层包详情：卡片 + 原始记录

写操作端点（POST，application/json）：
    /api/action    body = { action, ..., idempotency_key?, agent?, target_version? }

    十七种动作：
      inspect  只读（等价 GET /api/package/<pkg>）
      ask      生成工作简报（wf brief：目标原话、本层说明、上游卡片）
      propose  提请决定：写待批事项（reviewer 固定 pending-user；批准只走 approve；须带 summary 一句话结论；
               回执记下所审内容的指纹）
      decide   记一个署名的决定（须署名 as 与一句话结论 summary）
      revise   作废并重做（wf invalidate）；dry_run=true 只预览影响范围
      what_if  假设性门禁评估（dry-run，不落库）
      validate 核对（wf gate，写 verdict；不会解除已作废包的状态）
      approve  批准待批事项（须署名 as）
      reject   驳回待批事项（须署名 as 与理由 reason）
      rollback 回滚当前 Champion 并恢复旧 Champion（wf evo-rollback）
      bench_import 导入 benchmark（文件哈希入册；题集目录按清单入册）；路径须在仓库目录内
      task_new     立项：标题 + 目标 + benchmark + 流程模板；create=true 时按模板生成迭代 1 的层包
      iter_new     开下一轮迭代（reason 必填）
      new_pkg      给流程里还没开始的一个框生成本层的层包
      iter_seal / iter_abort  结束 / 终止一轮迭代（须署名 as 与理由 reason）
      task_seal    封板（须署名 as 与理由 reason）
"""
import argparse
import contextlib
import hashlib
import io
import ipaddress
import json
import os
import re
import sys
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT.parent / "tools"
sys.path.insert(0, str(TOOLS))

IDEM_PATH = ROOT / "idempotency.jsonl"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
PKG_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _import_wf():
    import wf as wfmod
    return wfmod, wfmod.WF(wfmod.default_root())


def _make_ns(**kw):
    ns = argparse.Namespace()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _call(fn, w, args):
    """调 wf.py 的 cmd_* 并取返回值（屏蔽其 stdout；SystemExit 原样抛给调用方转成错误）。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return fn(w, args)


def _pkg_by_id(w, ref):
    """层包只按 package_id 解析。wf.resolve_pkg 另接受任意 envelope.json 路径——那是 CLI 的
    便利，不能暴露到 HTTP（否则读请求能读 store 之外的文件，写请求能改写它们）。"""
    if not isinstance(ref, str) or not PKG_ID_RE.fullmatch(ref) or ref.startswith("."):
        raise SystemExit("错误：非法层包标识（只接受 package_id，不接受路径）")
    for env_path, env in w.find_packages():
        if env["identity"]["package_id"] == ref:
            return env_path, env
    raise SystemExit(f"错误：找不到层包：{ref}")


# ---------------------------------------------------------------- 只读聚合（复用 wf.py）

def build_state(domain=None):
    """复用 cmd_state_export 的聚合逻辑，返回 state dict（屏蔽 stdout）。
    每个层包带 state（唯一的状态词）与 card（接口卡、核对回执、证据、决定回执）；每个迭代带模板顺序的
    nodes（流程编排里的框）；每个任务带 receipts（任务级决定回执）。domain 仅为兼容旧参数。"""
    wfmod, w = _import_wf()
    return _call(wfmod.cmd_state_export, w, _make_ns(domain=domain, out=None))


def recommend_flow(goal, domain=None, iteration=None):
    wfmod, w = _import_wf()
    return _call(wfmod.cmd_recommend_flow, w,
                 _make_ns(goal=goal, domain=domain, iteration=iteration or None))


def evo_state():
    wfmod, w = _import_wf()
    return _call(wfmod.cmd_evo_state, w, _make_ns(out=None))


def evidence(domain=None):
    return build_state(domain)["dag"]


def templates():
    """流程模板：任务长什么样、怎样算完成，只由模板决定。每个模板列出它的框（层）。
    去重规则与引擎的 template_nodes 一致（L5d 归到 L5；同一层出现多次只算一个框）。"""
    wfmod, w = _import_wf()
    bus = wfmod._load_layer_bus()
    out = []
    for tid in wfmod.FLOW_TEMPLATES:
        tpl = bus.SCENARIO_TEMPLATES[tid]
        layers, seen = [], set()
        for nd in tpl["nodes"]:
            L = str(nd.get("component", "")).rstrip("d")
            if L in wfmod.LAYER_DIR and L not in seen:
                seen.add(L)
                layers.append({"layer": L, "name": wfmod.LAYER_GUIDE[L]["name"],
                               "alias": nd.get("alias", "")})
        out.append({"id": tid, "label": wfmod.TEMPLATE_LABEL.get(tid, tid),
                    "description": tpl["description"], "layers": layers})
    return {"templates": out, "default": wfmod.DEFAULT_TEMPLATE}


def guide():
    """每层的固定说明（界面与 wf brief 共用这一份），以及几个标记的人读含义。"""
    wfmod, w = _import_wf()
    layers = []
    for L, g in wfmod.LAYER_GUIDE.items():
        layers.append({"layer": L, "name": g["name"], "question": g["question"], "role": g["role"],
                       "role_label": wfmod.ROLE_LABEL.get(g["role"], g["role"]),
                       "skills": g["skills"], "consumes": g["consumes"], "produces": g["produces"],
                       "human": bool(g["human"]), "needs_test": bool(g["needs_test"]),
                       "deliverables": [name for name, _ in g["deliverables"]]})
    return {"layers": layers, "truth": wfmod.TRUTH_LABEL, "frames": wfmod.FRAME_LABEL,
            "strength": wfmod.STRENGTH_LABEL, "states": wfmod.STATE_ORDER,
            "interface": {"limits": wfmod.IFACE_LIMITS, "gives_max": wfmod.IFACE_GIVES_MAX}}


def benchmarks():
    """benchmark 清单（全局一份）。认的范围与 wf task-new 相同：全局清单 + 旧位置里登记过的。
    每个带工件的类型、大小、登记时间；题集目录带文件数。"""
    wfmod, w = _import_wf()
    arts = w.artifacts()
    known = dict(w.benchmarks())
    for p in sorted(w.domains.glob("*/pack.json")):
        try:
            for b in json.loads(p.read_text(encoding="utf-8")).get("benchmarks", []):
                known.setdefault(b.get("name", ""), b)
        except (OSError, ValueError):
            continue
    out = []
    for name in sorted(known):
        if not name:
            continue
        b = known[name]
        refs = []
        for r in b.get("artifact_refs", []):
            a = arts.get(r, {})
            refs.append({"artifact_id": r, "artifact_type": a.get("artifact_type", ""),
                         "size": a.get("size"), "file_count": a.get("file_count"),
                         "uri": a.get("uri", ""), "registered_at": a.get("registered_at", "")})
        out.append({"name": name, "kind": b.get("kind", ""), "artifact_refs": refs,
                    "imported_at": b.get("imported_at", "")})
    return out


def package_detail(ref):
    """层包详情（层详情页的数据源）：人读的卡片 + 原始记录（界面默认折叠的那部分）。"""
    wfmod, w = _import_wf()
    env_path, env = _pkg_by_id(w, ref)
    ident = env["identity"]
    tasks = w.tasks()
    pkg_index = {e["identity"]["package_id"]: e for _, e in w.find_packages()}
    vc = w.vcheck(ident["package_id"])
    return {
        "package_id": ident["package_id"],
        "card": wfmod.layer_card(w, env, tasks, pkg_index),
        "raw": {"identity": ident, "payload": env.get("payload", {}),
                "interface": env.get("interface"), "design_spec": env.get("design_spec"),
                "contract_manifest": env.get("contract_manifest"),
                "artifacts": env.get("artifacts", []), "decision_refs": env.get("decision_refs", []),
                "provenance": env.get("provenance", {})},
        # 平铺字段：兼容按老字段名取数的调用方
        "title": ident.get("title", ""),
        "layer": ident["layer_id"],
        "layer_name": ident.get("layer_name", ""),
        "owner_agent": ident.get("owner_agent", ""),
        "lifecycle": env["lifecycle"]["status"],
        "verdict": env["validation"].get("aggregate_verdict", ""),
        "gate_report": env["validation"].get("gate_report", ""),
        "claim_level": ident.get("claim_level") or env.get("payload", {}).get("claim_level"),
        "parent_package_refs": env.get("provenance", {}).get("parent_package_refs", []),
        "artifacts": env.get("artifacts", []),
        "decision_refs": env.get("decision_refs", []),
        "validation": env.get("validation", {}),
        "vcheck": vc,
    }


# ---------------------------------------------------------------- 写操作

def _agent(p):
    return (p.get("agent") or "").strip()


def _h_inspect(w, wfmod, p):
    ref = p.get("pkg") or p.get("ref")
    if not ref:
        raise SystemExit("inspect 需要 pkg 参数")
    return package_detail(ref)


def _h_ask(w, wfmod, p):
    ref = p.get("pkg") or p.get("ref")
    if not ref:
        raise SystemExit("ask 需要 pkg 参数")
    _pkg_by_id(w, ref)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        wfmod.cmd_brief(w, _make_ns(pkg=ref))
    return {"package_id": ref, "brief": buf.getvalue()}


def _h_propose(w, wfmod, p):
    """propose 写入的永远是"待批事项"：审批人固定 pending-user。

    否则建议人可以自己填 reviewer=human/ratify，一步满足高人类权重门禁——
    批准只能走 approve（须署名）。
    必须带 summary（一句话结论）：审阅者先看「决定了什么」，长篇依据留在 rationale。
    回执记下此时接口卡的指纹：内容在其后改过，批准记录就显示为过期。
    """
    ref = p.get("pkg") or p.get("ref")
    if not ref:
        raise SystemExit("propose 需要 pkg 参数")
    if p.get("reviewer", "pending-user") != "pending-user":
        raise SystemExit("propose 写入的是待批事项，审批人固定为 pending-user；批准请用 approve 并署名")
    dtype = p.get("type", "select")
    if dtype == "ratify":
        raise SystemExit("propose 不能写 ratify 决策；批准请用 approve 并署名")
    summary = (p.get("summary") or "").strip()
    if not summary:
        raise SystemExit("propose 需要 summary：用一句话写明决定了什么（详细依据放 rationale）")
    env_path, env = _pkg_by_id(w, ref)
    pkg_id = env["identity"]["package_id"]
    task, _ = w.iteration_of(env)
    rec = w.add_decision(
        dtype,
        # 详细依据可以不写：记录里的 rationale 不允许为空，就拿一句话结论顶上
        (p.get("rationale") or "").strip() or summary,
        p.get("confidence", "medium"),
        "pending-user",
        selected=p.get("selected", []),
        rejected=p.get("rejected", []),
        constraints=p.get("constraints", []),
        evidence_refs=p.get("evidence", []),
        scope=p.get("scope", ""),
        agent=_agent(p),
        summary=summary,
        task_id=task["task_id"] if task else "",
        content_sha=wfmod.interface_hash(env),
    )

    def _mutate(e):
        e.setdefault("decision_refs", [])
        if rec["decision_id"] not in e["decision_refs"]:
            e["decision_refs"].append(rec["decision_id"])
    w.update_envelope(env_path, _mutate, agent=_agent(p))
    w.add_edge_safe(pkg_id, rec["decision_id"], "decided_by", note=dtype, agent=_agent(p))
    return {"decision_id": rec["decision_id"], "package_id": pkg_id,
            "type": dtype, "reviewer": "pending-user", "content_sha": rec.get("content_sha", "")}


def _h_decide(w, wfmod, p):
    """直接记一条署名的决定（人的决定窗口）。

    署名必填：决定是人做的，要留名。agent 可以代办录入，但署名仍然是人。
    驳回某个待批事项走 reject（它有明确的处理对象）；否决一个层包的结论走 revise（作废并重做）。
    回执记下所审接口卡的指纹。
    """
    ref = p.get("pkg") or p.get("ref")
    if not ref:
        raise SystemExit("decide 需要 pkg 参数")
    user = _signer(p, "decide")
    if user == "pending-user":
        raise SystemExit("decide 的署名不能是 pending-user——要提请别人决定请用 propose")
    summary = (p.get("summary") or "").strip()
    if not summary:
        raise SystemExit("decide 需要 summary：用一句话写明决定了什么")
    dtype = p.get("type") or "select"
    if dtype not in ("select", "ratify", "override", "defer"):
        raise SystemExit(f"decide 的 type 须为 select / ratify / override / defer（收到：{dtype}）")
    confidence = p.get("confidence") or "high"
    if confidence not in ("low", "medium", "high"):
        raise SystemExit("decide 的 confidence 须为 low / medium / high")
    _pkg_by_id(w, ref)
    rec = _call(wfmod.cmd_decide, w, _make_ns(
        pkg=ref, task=None, summary=summary, type=dtype,
        select=_str_list(p.get("selected")), reject=[], constraint=_str_list(p.get("constraints")),
        evidence=_str_list(p.get("evidence")), rationale=p.get("rationale") or "",
        confidence=confidence, reviewer=user, scope=p.get("scope") or "", agent=_agent(p)))
    return {"decision_id": rec["decision_id"], "package_id": ref, "type": dtype,
            "signed_by": user, "summary": rec.get("summary", summary)}


def _h_revise(w, wfmod, p):
    ref = p.get("pkg") or p.get("ref")
    reason = (p.get("reason") or "").strip()
    if not ref:
        raise SystemExit("revise 需要 pkg 参数")
    if not reason and not p.get("dry_run"):
        raise SystemExit("revise 需要修订原因（reason）")
    _pkg_by_id(w, ref)
    # dry_run：只算受影响集合，给界面做"执行前预览"
    return w.invalidate(ref, reason, agent=_agent(p), dry_run=bool(p.get("dry_run")))


def _h_what_if(w, wfmod, p):
    ref = p.get("pkg") or p.get("ref")
    if not ref:
        raise SystemExit("what_if 需要 pkg 参数")
    _pkg_by_id(w, ref)
    verdict, report = w.gate(ref, verbose=False, dry_run=True)
    return {"package_id": ref, "verdict": verdict, "report": report}


def _h_validate(w, wfmod, p):
    ref = p.get("pkg") or p.get("ref")
    if not ref:
        raise SystemExit("validate 需要 pkg 参数")
    _pkg_by_id(w, ref)
    verdict, report = w.gate(ref, verbose=False)
    return {"package_id": ref, "verdict": verdict, "report": report}


def _signer(p, action):
    """批准/驳回的署名即批准主体，必须由调用方显式给出。"""
    user = (p.get("as") or "").strip()
    if not user:
        raise SystemExit(f"{action} 需要署名（as）")
    return user


def _h_approve(w, wfmod, p):
    dec = p.get("dec")
    if not dec:
        raise SystemExit("approve 需要 dec 参数")
    user = _signer(p, "approve")
    new, affected = w.resolve_decision(dec, user, reject=False, reason=p.get("note", ""))
    verdicts = [w.gate(pid, verbose=False)[0] for pid in affected]
    return {"decision_id": new["decision_id"], "supersedes": dec, "signed_by": user,
            "affected": affected, "regate_verdicts": verdicts}


def _h_reject(w, wfmod, p):
    dec = p.get("dec")
    reason = (p.get("reason") or "").strip()
    if not dec:
        raise SystemExit("reject 需要 dec 参数")
    if not reason:
        raise SystemExit("reject 需要驳回理由（reason）")
    user = _signer(p, "reject")
    new, affected = w.resolve_decision(dec, user, reject=True, reason=reason)
    verdicts = [w.gate(pid, verbose=False)[0] for pid in affected]
    return {"decision_id": new["decision_id"], "supersedes": dec, "signed_by": user,
            "affected": affected, "regate_verdicts": verdicts}


def _h_rollback(w, wfmod, p):
    candidate = p.get("candidate")
    if not candidate:
        raise SystemExit("rollback 需要 candidate 参数")
    rec = wfmod.cmd_evo_rollback(w, _make_ns(candidate=candidate, reason=p.get("reason", ""),
                                             agent=_agent(p)))
    return {"candidate": candidate, "status": rec.get("status", "rolled_back"),
            "restored": rec.get("restored", [])}


# ---------------------------------------------------------------- 任务入口（纪律六：无章程不立项）

def _inside_root(w, raw):
    """bench_import 的路径约束：必须是仓库目录内的普通文件或目录。
    解析软链之后再比——否则一个指向仓库外的链接就能把任意文件哈希入册、复制进 store。"""
    if not isinstance(raw, str) or not raw.strip():
        raise SystemExit("错误：paths 里每一项须是非空的路径字符串")
    p = Path(raw.strip()).resolve()
    if w.root not in p.parents:
        raise SystemExit(f"错误：路径须在仓库目录内（{w.root}）：{raw.strip()}")
    if not p.exists():
        raise SystemExit(f"错误：路径不存在：{raw.strip()}")
    if not (p.is_file() or p.is_dir()):
        raise SystemExit(f"错误：不是普通文件或目录：{raw.strip()}")
    return str(p)


def _str_list(v):
    return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []


def _h_bench_import(w, wfmod, p):
    name = (p.get("name") or "").strip()
    paths = p.get("paths")
    if not name:
        raise SystemExit("bench_import 需要 name")
    if not isinstance(paths, list) or not paths:
        raise SystemExit("bench_import 需要 paths（文件或目录的路径列表）")
    resolved = [_inside_root(w, x) for x in paths]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = wfmod._bench_import(w, name, (p.get("kind") or "").strip(),
                                  (p.get("note") or "").strip(), (p.get("type") or "").strip(),
                                  resolved, agent=_agent(p))
    res["benchmarks"] = benchmarks()
    return res


def _h_task_new(w, wfmod, p):
    task = _call(wfmod.cmd_task_new, w, _make_ns(
        title=p.get("title") or "", slug=p.get("slug") or "", goal=p.get("goal") or "",
        benchmark=_str_list(p.get("benchmarks")), template=p.get("template") or "",
        create=bool(p.get("create")), owner=p.get("owner") or "",
        **{"as": p.get("as") or ""}, note=p.get("note") or "", agent=_agent(p)))
    return {"task_id": task["task_id"], "status": task["status"], "title": task["title"],
            "flow_template": task.get("flow_template", ""),
            "charter_decision_id": task.get("charter_decision_id", ""),
            "iterations": [{"iteration_id": it["iteration_id"], "label": it["label"], "status": it["status"]}
                           for it in task.get("iterations", [])]}


def _h_iter_new(w, wfmod, p):
    it = _call(wfmod.cmd_iter_new, w, _make_ns(
        task=(p.get("task") or "").strip(), reason=p.get("reason") or "", title=p.get("title") or "",
        template=p.get("template") or "", create=bool(p.get("create")), owner=p.get("owner") or "",
        **{"as": p.get("as") or ""}, agent=_agent(p)))
    return {"iteration_id": it["iteration_id"], "label": it["label"], "status": it["status"],
            "reason": it["reason"], "flow_template": it.get("flow_template", "")}


def _h_new_pkg(w, wfmod, p):
    """流程里还没开始的一个框：给这一层生成层包（脚手架），归到给定的迭代。"""
    iteration, layer = (p.get("iteration") or "").strip(), (p.get("layer") or "").strip()
    if not iteration or not layer:
        raise SystemExit("new_pkg 需要 iteration 与 layer")
    if layer not in wfmod.LAYER_DIR:
        raise SystemExit(f"new_pkg 的 layer 须为 {list(wfmod.LAYER_DIR)}")
    task, it = w.find_iteration(iteration)
    if it.get("status") not in wfmod.TASK_LIVE:
        raise SystemExit(f"错误：{it.get('label') or iteration} 已结束，不能再建层包；要再推进请新开一轮")
    ns = task.get("namespace") or wfmod.DEFAULT_NAMESPACE
    slug = f"{task['task_id'].removeprefix('TASK-')}-i{it.get('seq', 1)}-{layer.lower()}"
    res = _call(wfmod.cmd_new_pkg, w, _make_ns(
        domain=ns, layer=layer, slug=slug,
        title=f"{wfmod.LAYER_GUIDE[layer]['name']}（{it.get('label') or iteration}）",
        owner="", intent=(task.get("goal") or {}).get("statement", ""), iteration=iteration))
    return dict(res, layer=layer)


def _h_iter_close(w, wfmod, p, outcome):
    action = "iter_seal" if outcome == "sealed" else "iter_abort"
    iteration = (p.get("iteration") or (p.get("task") or "").strip())
    reason = (p.get("reason") or "").strip()
    if not iteration:
        raise SystemExit(f"{action} 需要 iteration（ITER-… 或 TASK-…/<序号>）")
    if not reason:
        raise SystemExit(f"{action} 需要理由（reason）")
    user = _signer(p, action)
    _call(wfmod.cmd_iter_seal if outcome == "sealed" else wfmod.cmd_iter_abort, w,
          _make_ns(iteration=iteration, reason=reason, agent=_agent(p), **{"as": user}))
    return {"iteration": iteration, "status": outcome, "signed_by": user, "reason": reason}


def _h_iter_seal(w, wfmod, p):
    return _h_iter_close(w, wfmod, p, "sealed")


def _h_iter_abort(w, wfmod, p):
    return _h_iter_close(w, wfmod, p, "aborted")


def _h_task_seal(w, wfmod, p):
    task = (p.get("task") or "").strip()
    reason = (p.get("reason") or "").strip()
    if not task:
        raise SystemExit("task_seal 需要 task")
    if not reason:
        raise SystemExit("task_seal 需要理由（reason）")
    user = _signer(p, "task_seal")
    _call(wfmod.cmd_task_seal, w, _make_ns(task=task, reason=reason, agent=_agent(p), **{"as": user}))
    return {"task_id": task, "status": "sealed", "signed_by": user, "reason": reason}


HANDLERS = {
    "inspect": _h_inspect,
    "ask": _h_ask,
    "propose": _h_propose,
    "decide": _h_decide,
    "revise": _h_revise,
    "what_if": _h_what_if,
    "validate": _h_validate,
    "approve": _h_approve,
    "reject": _h_reject,
    "rollback": _h_rollback,
    "bench_import": _h_bench_import,
    "task_new": _h_task_new,
    "iter_new": _h_iter_new,
    "new_pkg": _h_new_pkg,
    "iter_seal": _h_iter_seal,
    "iter_abort": _h_iter_abort,
    "task_seal": _h_task_seal,
}


def _load_idem():
    if not IDEM_PATH.exists():
        return {}
    out = {}
    for line in IDEM_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        out[rec.get("idempotency_key")] = rec
    return out


def _record_idem(rec):
    with open(IDEM_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _payload_hash(body):
    payload = {k: v for k, v in body.items() if k != "idempotency_key"}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def execute_action(body):
    """执行一个写动作，返回完整响应 dict（含幂等重放标记；http 字段由 HTTP 层取出）。"""
    action = body.get("action", "")
    if action not in HANDLERS:
        return {"ok": False, "action": action,
                "error": f"未知动作：{action!r}（可选：{', '.join(sorted(HANDLERS))}）"}

    idem_key = body.get("idempotency_key")
    if idem_key:
        cached = _load_idem().get(idem_key)
        if cached:
            # 幂等键必须与请求内容绑定：同一个 key 换了动作/目标，不能把旧结果当成新请求的成功
            if cached.get("payload_hash") and cached["payload_hash"] != _payload_hash(body):
                return {"ok": False, "action": action, "http": 409,
                        "error": "idempotency_key_conflict：该 idempotency_key 已用于另一个不同的请求"}
            return {"ok": True, "action": cached.get("action", action), "idempotent_replay": True,
                    "result": cached.get("result"), "output": cached.get("output", "")}

    # 乐观并发：target_version 与当前 store 不一致 → 拒绝并返回当前哈希（界面看到的不是最新状态）
    if body.get("target_version"):
        wfmod, w = _import_wf()
        cur = wfmod._store_env_hash(w)
        if cur != body["target_version"]:
            return {"ok": False, "action": action, "http": 409, "error": "store_version_conflict",
                    "current_store_env_hash": cur,
                    "message": "store 已变化，请刷新状态后重试"}

    wfmod, w = _import_wf()
    try:
        result = HANDLERS[action](w, wfmod, body)
    except SystemExit as e:
        code = e.code
        err = code if isinstance(code, str) else f"exit={code}"
        return {"ok": False, "action": action, "error": err}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "action": action, "error": f"执行异常：{type(e).__name__}: {e}"}

    resp = {"ok": True, "action": action, "result": result}
    if idem_key:
        _record_idem({"idempotency_key": idem_key, "action": action,
                      "ts": wfmod.now(), "payload_hash": _payload_hash(body),
                      "agent": _agent(body), "result": result})
    return resp


# ---------------------------------------------------------------- HTTP handler

class VizServer(HTTPServer):
    """单线程 HTTPServer：请求串行执行（写 store 不并发）。"""
    allow_origins: tuple = ()
    bind_loopback = True


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    # ---- 基础 ----
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > 4 * 1024 * 1024:
            raise ValueError("请求体过大")
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise ValueError("请求体不是合法 JSON")

    def _guard(self, fn, *args):
        """wf.py 用 SystemExit 报"找不到/参数错"，它不是 Exception 的子类——接不住会一路
        抛出 serve_forever，整个进程退出。统一在这里转成 JSON 错误。"""
        try:
            self._json(fn(*args))
        except SystemExit as e:
            msg = e.code if isinstance(e.code, str) else f"exit={e.code}"
            self._json({"error": msg}, 404 if ("找不到" in msg or "不存在" in msg) else 400)
        except Exception as e:  # noqa: BLE001
            self._json({"error": f"{getattr(fn, '__name__', 'handler')} 失败: {type(e).__name__}: {e}"}, 500)

    # ---- 请求来源校验 ----
    def _host_ok(self):
        """防 DNS rebinding：Host 只能是回环地址或 IP 字面量，不接受任意域名。"""
        host = self.headers.get("Host")
        if not host:
            return True  # HTTP/1.0 无 Host；rebinding 攻击必然带域名 Host
        hostname = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        hostname = hostname.strip("[]").lower()
        if hostname in LOOPBACK_HOSTS:
            return True
        if self.server.bind_loopback:
            return False
        try:  # 绑定非回环地址（局域网访问）时：只认 IP，不认域名
            ipaddress.ip_address(hostname)
            return True
        except ValueError:
            return False

    def _origin_ok(self, origin):
        if origin.rstrip("/") in self.server.allow_origins:
            return True
        try:
            u = urlparse(origin.rstrip("/"))
            return u.scheme in ("http", "https") and u.hostname in LOOPBACK_HOSTS
        except ValueError:
            return False

    def _write_ok(self):
        """写请求三重校验：JSON 类型 + Origin 白名单 + Sec-Fetch-Site。"""
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return False, "Content-Type 须为 application/json"
        origin = self.headers.get("Origin")
        if origin and not self._origin_ok(origin):
            return False, f"Origin 不被允许：{origin}（需要时用 --allow-origin 追加）"
        if (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
            return False, "拒绝跨站请求"
        return True, ""

    def _log_write(self, action, ok, agent, note=""):
        line = (f"[{datetime.now().isoformat(timespec='seconds')}] "
                f"{'ok  ' if ok else 'FAIL'} action={action} by={agent or '-'}")
        print(line + (f"  {note}" if note else ""), file=sys.stderr, flush=True)

    # ---- 路由 ----
    def do_GET(self):
        if not self._host_ok():
            self._json({"error": "Host 不被允许（防 DNS rebinding）"}, 403)
            return
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)
        if path.startswith("/api/package/"):
            self._guard(package_detail, unquote(path[len("/api/package/"):]))
        elif path == "/api/state":
            self._guard(build_state, qs.get("domain", [None])[0])
        elif path == "/api/recommend-flow":
            self._guard(recommend_flow, qs.get("goal", ["research"])[0],
                        qs.get("domain", [None])[0], qs.get("iteration", [None])[0])
        elif path == "/api/evo-state":
            self._guard(evo_state)
        elif path == "/api/evidence":
            self._guard(evidence, qs.get("domain", [None])[0])
        elif path == "/api/benchmarks":
            self._guard(benchmarks)
        elif path == "/api/templates":
            self._guard(templates)
        elif path == "/api/guide":
            self._guard(guide)
        elif path.startswith("/api/"):
            self._json({"error": f"未知端点：{path}"}, 404)
        else:
            super().do_GET()

    def do_POST(self):
        if not self._host_ok():
            self._json({"ok": False, "error": "Host 不被允许（防 DNS rebinding）"}, 403)
            return
        path = urlparse(self.path).path
        if path != "/api/action":
            self._json({"ok": False, "error": f"未知 POST 端点：{path}"}, 404)
            return
        ok, why = self._write_ok()
        if not ok:
            self._log_write("(rejected)", False, "", why)
            self._json({"ok": False, "rejected": True, "error": f"请求被拒绝：{why}"}, 403)
            return
        try:
            body = self._read_body()
            if not isinstance(body, dict):
                raise ValueError("请求体须是 JSON 对象")
            resp = execute_action(body)
        except ValueError as e:
            self._json({"ok": False, "error": str(e)}, 400)
            return
        except Exception as e:  # noqa: BLE001
            self._json({"ok": False, "error": f"内部错误: {type(e).__name__}: {e}"}, 500)
            return
        self._log_write(body.get("action", ""), resp.get("ok", False), _agent(body),
                        "" if resp.get("ok") else str(resp.get("error", ""))[:200])
        self._json(resp, resp.pop("http", 200))

    def log_message(self, fmt, *args):
        pass  # 读请求不记日志；写请求与被拒请求见 _log_write


def main():
    # nohup 重定向到文件时 stdout 是块缓冲：启动横幅（含"连的是真实 store"的警示）要按行落盘
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description="可视化 agent 工作流本地服务")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--allow-origin", action="append", default=[],
                    help="允许发写请求的额外 Origin（可重复；回环地址默认允许），"
                         "例如前端跑在别的机器上时加 http://192.168.1.10:5173")
    args = ap.parse_args()
    _, w = _import_wf()
    isolated = bool(os.environ.get("WF_ROOT"))
    print(f"🦐 viz server 启动：http://{args.host}:{args.port}")
    print(f"   store：{'隔离副本' if isolated else '真实 store'}  {w.root}")
    if not isolated:
        print("   ⚠ 写操作直接落在真实 store 上；只想试用界面请用 WF_ROOT 指向一份副本")
    print("   状态 API：/api/state（层包带状态词与卡片；迭代带模板节点；任务带决定回执）")
    print("   推荐 API：/api/recommend-flow?goal=research&iteration=ITER-…")
    print("   说明 API：/api/templates（流程模板）、/api/guide（每层固定说明）、/api/benchmarks（全局清单）")
    print("   写操作 API：POST /api/action（application/json；跨站请求一律拒绝）")
    srv = VizServer((args.host, args.port), Handler)
    srv.allow_origins = tuple(o.rstrip("/") for o in args.allow_origin)
    srv.bind_loopback = args.host in LOOPBACK_HOSTS
    srv.serve_forever()


if __name__ == "__main__":
    main()
