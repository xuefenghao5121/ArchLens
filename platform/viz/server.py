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
    前端开发：Vite 跑在 5173（/api 代理到 8899）；8899 本身是 API + 仓库静态目录，不是第二个前端。

只读端点（GET）：
    /api/state                           → workflow-state.json（0.4：每个层包带 state 状态词与 card 卡片；
                                           每个迭代带模板顺序的 nodes；每个任务带 receipts 任务级决定回执。
                                           ?domain= 仅为兼容旧参数，界面不再传）
    /api/recommend-flow?goal=research&iteration=ITER-…
                                         → 最小 flow 推荐（给 iteration 则整条 flow 限定在该迭代）
    /api/evo-state                       → 演进状态（含谱系）
    /api/evidence                        → 证据 DAG（兼容保留；界面上的证据页读的是 /api/state 里的卡片）
    /api/benchmarks                      → benchmark 清单（全局一份）
    /api/artifact?pkg=…&id=ART-…         → 本包引用的工件文本内容（只读；二进制/超大文件只给元数据）
    /api/templates                       → 流程模板：每个模板有哪些框（层）
    /api/guide                           → 每层的固定说明，以及几个标记（实测 / 推断 / 拍定、F0–F3）的人读含义
    /api/package/<package-id>            → 层包详情：卡片 + 原始记录

写操作端点（POST，application/json）：
    /api/action    body = { action, ..., idempotency_key?, agent?, target_version? }

    动作（常用）：
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
      brief_out    开工简报留档：{pkg, by} 写成 <包目录>/brief.md，并把指纹与上游记进信封
      run_log      记一条执行记录：{pkg, agent, model?, duration?, note?}
      artifact_save  人工编辑工件：{pkg, artifact, content, base_hash, by}。不覆盖共享输入，
                     写到 designs/<pkg>/edits/… 生成新 ART；本包引用切到新版本，下游直接依赖包作废，
                     保存后自动重跑 gate。
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
ART_ID_RE = re.compile(r"ART-[A-Za-z0-9._-]+")
ARTIFACT_TEXT_LIMIT = 512 * 1024
ARTIFACT_TYPES = {"rtl_source", "kernel", "config", "script", "stats", "log", "report",
                  "spec_doc", "data", "binary", "manifest", "test_artifact",
                  "environment", "other"}


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
    """流程模板：任务长什么样、怎样算完成，只由模板决定。每个模板列出它的框（层）与原始节点。
    去重规则与引擎的 template_nodes 一致（L5d 归到 L5；同一层出现多次只算一个框）。
    模板是数据（platform/agents/flow_templates.json），这里每次按当前文件读。"""
    wfmod, w = _import_wf()
    cfg = wfmod.collect_flow_templates(w)
    out = []
    for tpl in cfg["templates"]:
        layers, seen = [], set()
        for nd in tpl["nodes"]:
            L = str(nd.get("component", "")).rstrip("d")
            if L in wfmod.LAYER_DIR and L not in seen:
                seen.add(L)
                layers.append({"layer": L, "name": wfmod.LAYER_GUIDE[L]["name"],
                               "alias": nd.get("alias", "")})
        out.append(dict(tpl, layers=layers))
    return {"templates": out, "default": cfg["default"] or wfmod.default_template(),
            "components": cfg["components"], "sha": cfg["sha"], "errors": cfg["errors"]}


def agents_config():
    """Agent 管理页：九份定义（含正文与当前指纹）+ 整组校验错误。
    不依赖 agent_defs()——定义写坏时页面仍要能打开，否则没法在界面里修。"""
    wfmod, w = _import_wf()
    return wfmod.collect_agent_config()


def skills_shelf():
    """技能货架（只读）：用途、正文、方法论源、被哪些 agent 使用。"""
    wfmod, w = _import_wf()
    return wfmod.collect_skills()


def tools_shelf():
    """工具包货架：包定义、被哪些 agent 使用、引用错误。"""
    wfmod, w = _import_wf()
    return wfmod.collect_toolpacks()


def archived_tasks():
    """已归档任务：状态导出里不包含它们，但总览要能恢复/删除，所以单独列一份摘要。"""
    wfmod, w = _import_wf()
    tasks = w.tasks()
    pkgs = list(w.find_packages())
    out = []
    for tid, t in tasks.items():
        if not t.get("archived"):
            continue
        it_ids = {it.get("iteration_id") for it in t.get("iterations", [])}
        mine = [e for _, e in pkgs
                if (e.get("provenance") or {}).get("task_id") == tid
                or (e.get("provenance") or {}).get("iteration_id") in it_ids]
        out.append({"task_id": tid, "title": t.get("title", ""), "status": t.get("status", ""),
                    "archived_at": t.get("archived_at", ""),
                    "archived_reason": t.get("archived_reason", ""),
                    "iteration_count": len(t.get("iterations", [])),
                    "package_count": len({e["identity"]["package_id"] for e in mine})})
    out.sort(key=lambda x: x.get("archived_at", ""), reverse=True)
    return {"tasks": out}


def config_history(kind, target, sha=None):
    """配置版本历史：不带 sha 列历史与当前指纹；带 sha 取该版本内容。"""
    wfmod, w = _import_wf()
    if sha:
        return wfmod.read_version(kind, target, sha)
    entries = wfmod.list_versions(kind, target)
    path = wfmod._config_path(kind, target)
    cur = path.read_text(encoding="utf-8") if path.exists() else ""
    return {"kind": kind, "target": target, "entries": entries,
            "current_sha": wfmod._sha(cur), "current_lines": len(cur.splitlines()),
            "current_bytes": len(cur.encode("utf-8")), "current": cur}


def _runtime():
    """按需导入平台运行时（它依赖 wf，且只在派发/看作业时才需要）。"""
    import runtime as runtimemod
    return runtimemod


def runtime_status():
    return _runtime().runtime_status()


def jobs_list():
    rt = _runtime()
    return {"active": rt.MANAGER.active, "jobs": rt.list_jobs(20)}


def job_detail(job_id):
    job = _runtime().get_job(job_id, tail=300)
    if job is None:
        raise SystemExit(f"找不到作业：{job_id}")
    return job


def guide():
    """每层的固定说明（界面与 wf brief 共用这一份）、每层的 agent 定义，以及几个标记的人读含义。
    agent 带正文：界面可以展开完整定义，人看得到这一层要做什么、用什么、不许做什么。"""
    wfmod, w = _import_wf()
    defs = wfmod.agent_defs()
    layers = []
    for L, g in wfmod.LAYER_GUIDE.items():
        a = defs["layers"][L]
        layers.append({"layer": L, "name": g["name"], "question": g["question"], "role": g["role"],
                       "role_label": wfmod.ROLE_LABEL.get(g["role"], g["role"]),
                       "skills": list(a.get("skills", [])), "consumes": g["consumes"],
                       "produces": g["produces"],
                       "human": bool(g["human"]), "needs_test": bool(g["needs_test"]),
                       "deliverables": [name for name, _ in g["deliverables"]],
                       "agent": dict(wfmod.agent_summary(a), body=a["body"])})
    orch = defs["orchestrator"]
    return {"layers": layers,
            "orchestrator": dict(wfmod.agent_summary(orch), body=orch["body"]),
            "truth": wfmod.TRUTH_LABEL, "frames": wfmod.FRAME_LABEL,
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


# ---------------------------------------------------------------- 工件查看 / 编辑

def _pkg_artifact_ids(env):
    """本包允许在工作台打开的工件：信封 artifacts、接口卡 gives.evidence、payload 里引用的 ART。"""
    ids = set()

    def walk(v):
        if isinstance(v, str):
            if ART_ID_RE.fullmatch(v):
                ids.add(v)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)

    for a in env.get("artifacts", []) or []:
        if isinstance(a, dict) and a.get("artifact_id"):
            ids.add(str(a["artifact_id"]))
    walk((env.get("interface") or {}).get("gives") or [])
    walk(env.get("payload") or {})
    return ids


def _artifact_file(w, rec):
    """登记记录 → 仓库内可读文件。只接受仓库/商店内的路径，拒绝任意绝对路径。"""
    root = Path(w.root).resolve()
    for key in ("relpath", "store_path"):
        raw = rec.get(key)
        if not raw:
            continue
        p = (root / str(raw)).resolve()
        if p == root or root not in p.parents:
            continue
        if p.is_file():
            return p
    raw = rec.get("uri")
    if raw:
        p = Path(str(raw)).resolve()
        if (p == root or root in p.parents) and p.is_file():
            return p
    raise SystemExit(f"错误：工件 {rec.get('artifact_id')} 没有可读文件（可能只在对象库里）")


def _artifact_ref(w, pkg, aid):
    env_path, env = _pkg_by_id(w, pkg)
    if not ART_ID_RE.fullmatch(str(aid or "")):
        raise SystemExit("错误：artifact 参数须是 ART-… 编号")
    if aid not in _pkg_artifact_ids(env):
        raise SystemExit(f"错误：{aid} 不是层包 {pkg} 引用的工件")
    rec = w.artifacts().get(aid)
    if not rec:
        raise SystemExit(f"错误：工件不在登记册：{aid}")
    return env_path, env, rec


def _safe_artifact_type(rec, dest, wfmod):
    at = str(rec.get("artifact_type") or "")
    if at in ARTIFACT_TYPES:
        return at
    guessed = wfmod._guess_artifact_type(str(dest))
    return guessed if guessed in ARTIFACT_TYPES else "other"


def artifact_read(pkg, aid):
    """读一个已登记工件的文本内容；二进制/超大文件只返回元数据，不把内容塞进界面。"""
    _, w = _import_wf()
    _, _, rec = _artifact_ref(w, pkg, aid)
    path = _artifact_file(w, rec)
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    text = None
    binary = False
    truncated = False
    if len(raw) <= ARTIFACT_TEXT_LIMIT:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            binary = True
    else:
        truncated = True
    stored = rec.get("content_hash") or ""
    return {
        "ok": True, "artifact_id": rec.get("artifact_id"), "package_id": pkg,
        "artifact_type": rec.get("artifact_type", ""), "title": rec.get("note") or rec.get("artifact_type", ""),
        "path": rec.get("relpath") or rec.get("store_path") or rec.get("uri", ""),
        "content_hash": sha, "registered_hash": stored, "stale": bool(stored and stored != sha),
        "size": len(raw), "binary": binary, "truncated": truncated,
        "editable": not binary and not truncated, "text": text,
        "source": "共享输入" if not str(rec.get("relpath") or "").startswith("designs/") else "包内文件",
    }


def _replace_art(v, old, new):
    if isinstance(v, str):
        return new if v == old else v
    if isinstance(v, list):
        return [_replace_art(x, old, new) for x in v]
    if isinstance(v, dict):
        return {k: _replace_art(x, old, new) for k, x in v.items()}
    return v


def _direct_downstream(w, pkg):
    """直接依赖这个包的层包；编辑证据后它们必须作废，不能继续用旧结论。"""
    kids = set()
    for e in w.graph():
        if e.get("edge_type") == "depends_on" and e.get("to") == pkg and e.get("from"):
            kids.add(str(e["from"]))
    for _, env in w.find_packages():
        pid = env["identity"]["package_id"]
        if pid != pkg and pkg in (env.get("provenance") or {}).get("parent_package_refs", []):
            kids.add(pid)
    return sorted(kids)


def _h_artifact_save(w, wfmod, p):
    """人工编辑一个工件：不覆盖共享输入，写到 designs/<pkg>/edits/ 下形成新 ART；
    当前包内对该 ART 的引用切到新版本，旧工件与旧文件保留。保存后自动重跑 gate。"""
    pkg = (p.get("pkg") or "").strip()
    aid = (p.get("artifact") or p.get("artifact_id") or "").strip()
    content = p.get("content")
    if not pkg or not aid:
        raise SystemExit("artifact_save 需要 pkg 与 artifact（ART-…）")
    if not isinstance(content, str):
        raise SystemExit("artifact_save 需要 content 字符串")
    if not content.strip():
        raise SystemExit("内容为空，拒绝保存")
    if len(content.encode("utf-8")) > ARTIFACT_TEXT_LIMIT:
        raise SystemExit(f"内容超过 {ARTIFACT_TEXT_LIMIT // 1024}KB，拒绝保存")
    by = _require_by(p, "保存工件")
    env_path, env, rec = _artifact_ref(w, pkg, aid)
    lc = (env.get("lifecycle") or {}).get("status", "")
    if lc in ("invalidated", "superseded"):
        raise SystemExit("层包已作废/被取代，不能编辑工件")
    path = _artifact_file(w, rec)
    raw = path.read_bytes()
    cur_sha = hashlib.sha256(raw).hexdigest()
    base = str(p.get("base_hash") or rec.get("content_hash") or "")
    if base and base != cur_sha:
        raise SystemExit("文件在打开后被外部改动，拒绝覆盖；请刷新后再编辑")
    if cur_sha == hashlib.sha256(content.encode("utf-8")).hexdigest():
        return {"changed": False, "artifact_id": aid, "message": "内容没有变化"}
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(rec.get("relpath") or rec.get("uri") or "artifact.txt").name)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest_dir = (Path(w.root) / "designs" / pkg / "edits" / stamp).resolve()
    root = Path(w.root).resolve()
    if dest_dir != root and root not in dest_dir.parents:
        raise SystemExit("编辑路径越界")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (name or "artifact.txt")
    dest.write_text(content, encoding="utf-8")
    art_type = _safe_artifact_type(rec, dest, wfmod)
    new = w.register_artifact(str(dest), artifact_type=art_type,
                              note=f"人工编辑 {aid}（原文件保留）", role="evidence", agent=by)

    def mutate(e):
        arts = e.setdefault("artifacts", [])
        cur = next((a for a in arts if a.get("artifact_id") == aid), None)
        if cur is None:
            arts.append({"artifact_id": new["artifact_id"], "artifact_type": art_type,
                         "content_hash": new.get("content_hash", ""),
                         "uri": new.get("relpath", ""), "role": "evidence"})
        else:
            cur.update({"artifact_id": new["artifact_id"], "artifact_type": art_type,
                        "content_hash": new.get("content_hash", ""),
                        "uri": new.get("relpath", ""), "role": "evidence"})
        # 接口卡与 payload 里对旧 ART 的引用一起切到新版本；证据关系不变，只换内容哈希。
        e["interface"] = _replace_art(e.get("interface"), aid, new["artifact_id"])
        e["payload"] = _replace_art(e.get("payload"), aid, new["artifact_id"])
        e["evidence"] = _replace_art(e.get("evidence"), aid, new["artifact_id"])
        prov = e.setdefault("provenance", {})
        prov.setdefault("manual_edits", []).append({
            "at": wfmod.now(), "by": by, "old_artifact": aid, "new_artifact": new["artifact_id"],
            "source_path": rec.get("relpath") or rec.get("uri", ""), "edit_path": new.get("relpath", ""),
        })

    w.update_envelope(env_path, mutate, agent=by)
    verdict, report = w.gate(pkg, verbose=False)
    invalidated = []
    reason = f"上游 {pkg} 的工件 {aid} 被人工编辑为新版本 {new['artifact_id']}"
    for child in _direct_downstream(w, pkg):
        try:
            _, ce = w.resolve_pkg(child)
            if (ce.get("lifecycle") or {}).get("status") in ("invalidated", "superseded"):
                continue
            res = w.invalidate(child, reason, agent=by)
            invalidated.extend(res.get("affected", []))
        except SystemExit:
            continue
    return {"changed": True, "artifact_id": new["artifact_id"], "old_artifact_id": aid,
            "path": new.get("relpath", ""), "invalidated": sorted(set(invalidated)),
            "gate": {"verdict": verdict,
            "blocks": (report or {}).get("blocks", []), "warns": (report or {}).get("warns", [])}}


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
        scope=p.get("scope") or pkg_id,
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


def _has_stale_signoff(w, wfmod, ref):
    """本包是否还挂着一条“内容已经改过”的待签事项。"""
    try:
        _, env = _pkg_by_id(w, ref)
    except SystemExit:
        return False
    cur = wfmod.interface_hash(env)
    decs = w.decisions()
    for did in env.get("decision_refs", []):
        d = decs.get(did)
        if (d and d.get("reviewer") == "pending-user" and d.get("content_sha")
                and d["content_sha"] != cur):
            return True
    return False


def _h_validate(w, wfmod, p):
    ref = p.get("pkg") or p.get("ref")
    if not ref:
        raise SystemExit("validate 需要 pkg 参数")
    _pkg_by_id(w, ref)
    verdict, report = w.gate(ref, verbose=False)
    signoff = None
    if verdict in ("pass", "needs_decision") or _has_stale_signoff(w, wfmod, ref):
        # 手动点“核对”也要维护待签队列：内容改过后换掉旧待签；已经通过则清掉遗留的当前待签。
        signoff = wfmod.ensure_signoff_pending(w, ref, by=_agent(p) or "平台")
    # 人读/机器交付也跟着当前核对结果刷新；失败也刷新，页面不会显示旧状态。
    try:
        wfmod.ensure_layer_report(w, ref, by=_agent(p) or "平台")
    except (SystemExit, AttributeError):
        pass
    return {"package_id": ref, "verdict": verdict, "report": report,
            "signoff_decision_id": signoff}


def _h_layer_report(w, wfmod, p):
    ref = p.get("pkg") or p.get("ref")
    if not ref:
        raise SystemExit("layer_report 需要 pkg 参数")
    _pkg_by_id(w, ref)
    return wfmod.ensure_layer_report(w, ref, by=_require_by(p, "生成报告"))


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
    # 批准要留判断：只记「谁点的」看不出人核对了什么，签过等于没签
    note = str(p.get("note") or "").strip()
    if not note:
        raise SystemExit("approve 需要批注（note）：用一句话写明你核对了什么")
    new, affected = w.resolve_decision(dec, user, reject=False, reason=note)
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
            "flow_template": task.get("flow_template", ""), "warnings": task.get("_warnings", []),
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


def _h_task_archive(w, wfmod, p):
    task = (p.get("task") or "").strip()
    if not task:
        raise SystemExit("task_archive 需要 task")
    restore = bool(p.get("restore"))
    _require_by(p, "恢复任务" if restore else "归档任务")
    _call(wfmod.cmd_task_archive, w, _make_ns(
        task=task, reason=p.get("reason") or "", restore=restore))
    return {"task_id": task, "archived": not restore}


def _h_task_delete(w, wfmod, p):
    """彻底删除已归档任务。必须把任务 ID 原样填进 confirm，避免误点。"""
    task = (p.get("task") or "").strip()
    if not task:
        raise SystemExit("task_delete 需要 task")
    if (p.get("confirm") or "").strip() != task:
        raise SystemExit(f"错误：删除需要把任务 ID「{task}」原样填进确认框")
    by = _require_by(p, "删除任务")
    return wfmod.purge_task(w, task, by)


def _h_brief_out(w, wfmod, p):
    """开工简报留档：把这一层的简报写成 <包目录>/brief.md，并在信封里记时间、署名、指纹与上游。"""
    ref = p.get("pkg") or p.get("ref")
    if not ref:
        raise SystemExit("brief_out 需要 pkg 参数")
    _pkg_by_id(w, ref)
    by = (p.get("by") or _agent(p) or "").strip()
    return _call(wfmod.cmd_brief, w, _make_ns(pkg=ref, out=True, by=by))


def _h_run_log(w, wfmod, p):
    """记一条执行记录：谁、用什么模型、跑了多久。只记事实，不判断好坏。"""
    ref = p.get("pkg") or p.get("ref")
    if not ref:
        raise SystemExit("run_log 需要 pkg 参数")
    _pkg_by_id(w, ref)
    return _call(wfmod.cmd_run_log, w, _make_ns(
        pkg=ref, agent=_agent(p), model=p.get("model") or "",
        duration=p.get("duration"), note=p.get("note") or ""))


# ---- 配置管理（Agent 管理 / 技能与工具 / 编排配置）----
#
# 这些动作改的是平台配置（agent 定义、工具包、流程模板），不是研究数据。共同规则：
# 记名、带 base_sha 防并发覆盖、写盘前校验、保存前快照、保存后重投影。

def _require_by(p, what):
    by = (p.get("by") or _agent(p) or "").strip()
    if not by:
        raise SystemExit(f"{what}要记名：请先填操作人（agent）")
    return by


def _h_agent_save(w, wfmod, p):
    target = (p.get("target") or p.get("layer") or "").strip()
    if not target:
        raise SystemExit("agent_save 需要 target（如 L5_rtl 或 orchestrator）")
    meta = {}
    path = wfmod._agent_path(target)
    if path.exists():
        meta, _ = wfmod._parse_agent_frontmatter(path.read_text(encoding="utf-8"))
    meta.update(p.get("meta") or {})
    return wfmod.save_agent_definition(target, meta, p.get("body") or "",
                                       _require_by(p, "保存定义"), p.get("base_sha") or "")


def _h_agent_restore(w, wfmod, p):
    target = (p.get("target") or p.get("layer") or "").strip()
    sha = (p.get("sha") or "").strip()
    if not target or not sha:
        raise SystemExit("agent_restore 需要 target 与 sha")
    return wfmod.restore_config_version("agent", target, sha, _require_by(p, "回滚"))


def _h_toolpack_save(w, wfmod, p):
    name = (p.get("name") or "").strip()
    if not name:
        raise SystemExit("toolpack_save 需要 name")
    cmds = p.get("commands") or []
    if isinstance(cmds, str):
        cmds = [x.strip() for x in cmds.splitlines() if x.strip()]
    data = {"what": p.get("what") or "", "commands": cmds, "ref": p.get("ref") or ""}
    return wfmod.save_toolpack(name, data, _require_by(p, "保存工具包"), p.get("base_sha") or "")


def _h_toolpack_delete(w, wfmod, p):
    name = (p.get("name") or "").strip()
    if not name:
        raise SystemExit("toolpack_delete 需要 name")
    return wfmod.delete_toolpack(name, _require_by(p, "删除工具包"), p.get("base_sha") or "")


def _h_template_save(w, wfmod, p):
    tid = (p.get("id") or "").strip()
    if not tid:
        raise SystemExit("template_save 需要 id")
    tpl = {"label": p.get("label") or "", "description": p.get("description") or "",
           "nodes": p.get("nodes") or [], "gate_overrides": p.get("gate_overrides") or {}}
    return wfmod.save_flow_template(tid, tpl, _require_by(p, "保存模板"),
                                    p.get("base_sha") or "", bool(p.get("create")))


def _h_template_duplicate(w, wfmod, p):
    src = (p.get("from") or p.get("from_id") or "").strip()
    new = (p.get("id") or p.get("new_id") or "").strip()
    if not src or not new:
        raise SystemExit("template_duplicate 需要 from 与 id")
    return wfmod.duplicate_flow_template(src, new, p.get("label") or "",
                                         _require_by(p, "复制模板"), p.get("base_sha") or "")


def _h_reproject(w, wfmod, p):
    """手动重跑 .zcode/agents 投影（改了文件但没走界面保存时用）。"""
    _require_by(p, "重投影")
    return wfmod._reproject_hosts()


def _h_redo_layer(w, wfmod, p):
    """把已作废/被取代的层包重做成同层新包；旧包留在 store 里作审计。"""
    ref = (p.get("pkg") or p.get("ref") or "").strip()
    if not ref:
        raise SystemExit("redo_layer 需要 pkg 参数")
    _pkg_by_id(w, ref)
    return wfmod.redo_layer(w, ref, _require_by(p, "重做本层"))


# ---- 平台内部运行时：派发层 agent / 作业状态 ----

def _h_runtime_config(w, wfmod, p):
    _require_by(p, "保存运行时配置")
    rt = _runtime()
    try:
        return rt.save_config({
            "base_url": p.get("base_url"), "model": p.get("model"),
            "api_key": p.get("api_key"), "api_key_env": p.get("api_key_env"),
            "max_turns": p.get("max_turns"), "command_timeout_s": p.get("command_timeout_s"),
            "engine": p.get("engine"),
        })
    except ValueError as e:
        raise SystemExit(f"错误：{e}")


def _h_dispatch(w, wfmod, p):
    """把一个层包派给平台自己的运行时：它读 agent.md、调模型、执行工具，跑完平台自己 gate。"""
    ref = (p.get("pkg") or p.get("ref") or "").strip()
    if not ref:
        raise SystemExit("dispatch 需要 pkg 参数")
    _pkg_by_id(w, ref)
    rt = _runtime()
    try:
        job = rt.MANAGER.start(ref, auto=bool(p.get("auto")), by=_require_by(p, "派发"),
                              agent=(p.get("as") or p.get("agent") or "").strip())
    except RuntimeError as e:
        raise SystemExit(f"错误：{e}")
    return job


def _h_stop_job(w, wfmod, p):
    job_id = (p.get("job") or "").strip()
    if not job_id:
        raise SystemExit("stop_job 需要 job 参数")
    _require_by(p, "停止作业")
    ok = _runtime().MANAGER.request_stop(job_id)
    return {"job": job_id, "stopping": ok}


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
    "task_archive": _h_task_archive,
    "task_delete": _h_task_delete,
    "brief_out": _h_brief_out,
    "run_log": _h_run_log,
    "agent_save": _h_agent_save,
    "agent_restore": _h_agent_restore,
    "toolpack_save": _h_toolpack_save,
    "toolpack_delete": _h_toolpack_delete,
    "template_save": _h_template_save,
    "template_duplicate": _h_template_duplicate,
    "reproject_hosts": _h_reproject,
    "redo_layer": _h_redo_layer,
    "dispatch": _h_dispatch,
    "stop_job": _h_stop_job,
    "artifact_save": _h_artifact_save,
    "layer_report": _h_layer_report,
    "runtime_config": _h_runtime_config,
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

    # 派发是“开始一个新作业”，不是可重放的幂等写：同一包重派必须真的再跑一次。
    # 并发由 JobManager 自己保证（同一时刻只允许一个作业）。
    idem_key = None if action == "dispatch" else body.get("idempotency_key")
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
        elif path == "/api/artifact":
            self._guard(artifact_read, qs.get("pkg", [None])[0], qs.get("id", [None])[0])
        elif path == "/api/archived-tasks":
            self._guard(archived_tasks)
        elif path == "/api/templates":
            self._guard(templates)
        elif path == "/api/agents":
            self._guard(agents_config)
        elif path == "/api/skills":
            self._guard(skills_shelf)
        elif path == "/api/tools":
            self._guard(tools_shelf)
        elif path == "/api/config/history":
            self._guard(config_history, qs.get("kind", [""])[0],
                        qs.get("target", [""])[0], qs.get("sha", [None])[0])
        elif path == "/api/runtime":
            self._guard(runtime_status)
        elif path == "/api/jobs":
            self._guard(jobs_list)
        elif path.startswith("/api/job/"):
            self._guard(job_detail, unquote(path[len("/api/job/"):]))
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
