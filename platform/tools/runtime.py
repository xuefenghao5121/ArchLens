#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime — 平台内部的层 agent 运行时。

平台不再把层内工作交给外部对话：
  - agent 定义来自 platform/agents/<层>/agent.md（每次派发重新读，按 mtime 缓存由 wf 负责）
  - 平台自己调 OpenAI 兼容接口，自己执行工具（bash / 读写文件 / 列目录）
  - 跑完由平台调 `wf gate` 核对；通过后可自动派下一层可开工的包（无包 / 已作废 / 已通过 / 待人决定就停）
  - 每个作业写 platform/runtime/jobs/<JOB>.json + .jsonl 日志

配置：platform/runtime/config.json（不进库；api_key 可写在这里或放环境变量 RUNTIME_API_KEY）：
  {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
   "api_key_env": "RUNTIME_API_KEY", "max_turns": 40, "command_timeout_s": 180}

用法：
  python3 platform/tools/runtime.py check
  python3 platform/tools/runtime.py run --pkg PKG-L1-... [--auto] [--agent 名字]
  python3 platform/tools/runtime.py jobs
"""
import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import wf  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = ROOT / "platform" / "runtime"
CONFIG_PATH = RUNTIME_DIR / "config.json"
JOBS_DIR = RUNTIME_DIR / "jobs"

DEFAULT_CONFIG = {
    "base_url": "https://api.openai.com/v1",
    "model": "",
    "api_key_env": "RUNTIME_API_KEY",
    "api_key": "",
    "max_turns": 40,
    "command_timeout_s": 180,
    "max_output_chars": 20000,
    # v1=通用工具循环；langgraph=L1–L8 分层领域工具
    "engine": "v1",
}

TOOLS = [
    {"type": "function", "function": {
        "name": "bash", "description": "在仓库根目录执行 shell 命令（stdout/stderr 会返回）。",
        "parameters": {"type": "object", "required": ["command"], "properties": {
            "command": {"type": "string", "description": "要执行的命令"}}},
    }},
    {"type": "function", "function": {
        "name": "read_file", "description": "读仓库内的文件。",
        "parameters": {"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}, "offset": {"type": "integer"},
            "limit": {"type": "integer"}}},
    }},
    {"type": "function", "function": {
        "name": "write_file", "description": "写仓库内的文件（自动建目录，原子写）。",
        "parameters": {"type": "object", "required": ["path", "content"], "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}}},
    }},
    {"type": "function", "function": {
        "name": "list_dir", "description": "列仓库内目录。",
        "parameters": {"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}}},
    }},
    {"type": "function", "function": {
        "name": "finish", "description": "本层工作完成时调用；summary 写做了什么、跑过什么核对。",
        "parameters": {"type": "object", "required": ["summary"], "properties": {
            "summary": {"type": "string"}}},
    }},
]


# ---------------------------------------------------------------- 配置

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    cfg["base_url"] = os.environ.get("RUNTIME_BASE_URL") or cfg.get("base_url", "")
    cfg["model"] = os.environ.get("RUNTIME_MODEL") or cfg.get("model", "")
    cfg["engine"] = os.environ.get("RUNTIME_ENGINE") or cfg.get("engine", "v1")
    if cfg["engine"] not in ("v1", "langgraph"):
        cfg["engine"] = "v1"
    env_name = cfg.get("api_key_env") or "RUNTIME_API_KEY"
    cfg["api_key"] = os.environ.get("RUNTIME_API_KEY") or os.environ.get(env_name) or cfg.get("api_key", "")
    try:
        cfg["max_turns"] = int(os.environ.get("RUNTIME_MAX_TURNS") or cfg.get("max_turns") or 40)
    except (TypeError, ValueError):
        cfg["max_turns"] = 40
    try:
        cfg["command_timeout_s"] = int(cfg.get("command_timeout_s") or 180)
    except (TypeError, ValueError):
        cfg["command_timeout_s"] = 180
    return cfg


def public_config():
    cfg = load_config()
    return {
        "configured": bool(cfg.get("base_url") and cfg.get("model") and cfg.get("api_key")),
        "base_url": cfg.get("base_url", ""), "model": cfg.get("model", ""),
        "api_key_env": cfg.get("api_key_env", "RUNTIME_API_KEY"),
        "has_key": bool(cfg.get("api_key")), "max_turns": cfg.get("max_turns", 40),
        "command_timeout_s": cfg.get("command_timeout_s", 180),
        "engine": cfg.get("engine", "v1"),
    }


def save_config(data):
    """保存运行时配置。api_key 不传就保留原值；文件权限 0600。"""
    cur = {}
    if CONFIG_PATH.exists():
        try:
            cur = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cur = {}
    for k in ("base_url", "model", "api_key_env", "engine"):
        if data.get(k) is not None:
            cur[k] = str(data.get(k)).strip()
    if cur.get("engine") not in (None, "", "v1", "langgraph"):
        raise ValueError("engine 只能是 v1 或 langgraph")
    if data.get("api_key"):
        cur["api_key"] = str(data["api_key"]).strip()
    for k in ("max_turns", "command_timeout_s"):
        if data.get(k) not in (None, ""):
            try:
                cur[k] = int(data[k])
            except (TypeError, ValueError):
                raise ValueError(f"{k} 须是整数")
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(cur)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    wf.save_json(CONFIG_PATH, cfg)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    return public_config()


# ---------------------------------------------------------------- 作业

def _now():
    return wf.now()


def _job_id():
    return "JOB-" + time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()


def _job_path(job_id):
    return JOBS_DIR / f"{job_id}.json"


def _log_path(job_id):
    return JOBS_DIR / f"{job_id}.jsonl"


def _log(job, event, **kw):
    rec = {"at": _now(), "event": event, **kw}
    with open(job["log_path"], "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _save_job(job):
    job["updated_at"] = _now()
    wf.save_json(_job_path(job["job_id"]), job)


def _pkg(w, ref):
    for _, env in w.find_packages():
        if env["identity"]["package_id"] == ref:
            return env
    raise RuntimeError(f"找不到层包：{ref}")


def _brief(w, pkg):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        wf.cmd_brief(w, argparse.Namespace(pkg=pkg, out=False, by=""))
    return buf.getvalue()


def _agent_prompt(w, env, max_turns=40):
    layer = env["identity"]["layer_id"]
    a = wf.agent_of(layer)
    task, it = w.iteration_of(env)
    goal = ((task or {}).get("goal") or {}).get("statement", "")
    skills = []
    for name in a.get("skills", []):
        p = wf.SKILLS_DIR / str(name) / "SKILL.md"
        if p.exists():
            skills.append(f"### {name}\n{p.read_text(encoding='utf-8')}")
    packs = wf.agent_defs()["packs"]
    tools = []
    for name in a.get("tools", []):
        pk = packs.get(name, {})
        line = f"- {name}：{pk.get('what', '')}"
        if pk.get("commands"):
            line += "；命令：" + "、".join(pk["commands"])
        if pk.get("ref"):
            line += f"；领域参考：{pk['ref']}"
        tools.append(line)
    sys_prompt = f"""你是「{a.get('name', layer)}」（{layer}），在通用微架构混合仿真平台内部工作。
你的定义（唯一职责来源，平台每次派发重新读取）：
{a.get('body', '')}

## 技能
{chr(10).join(skills) or '（无）'}

## 可用工具包（经 bash 调）
{chr(10).join(tools) or '（无）'}

## 边界
- 可写：{'；'.join(a.get('writes', []))}
- 不许做：{'；'.join(a.get('must_not', []))}
- 环境已设置 WF_ACTOR=layer:{layer}：批准、驳回、署名覆盖、作废只由人做；decide 只能是 pending-user。

## 当前任务
- 任务目标（原话，不改写）：{goal or '（无）'}
- 本层包：{env['identity']['package_id']}

## 开工简报
{_brief(w, env['identity']['package_id'])}

## 工作方式
- 只做本层；不改上游层包、不改别人的层包。
- 用 bash 调 `python3 platform/tools/wf.py ...` 与领域工具；写文件用 write_file 或 bash。
- 完成前跑 `python3 platform/tools/wf.py gate {env['identity']['package_id']}`；没通过就继续改，不隐藏失败。
- 完成时调用 finish 工具，summary 写清楚做了什么、跑了什么核对、还有什么没解决。
- 预算：最多 {max_turns} 步模型调用。目标是本层的最小可交付，不是把整个仓库读完；不要反复读同一文件、
  不要无限检索。接近预算时先停下来整理结果，再调用 finish；平台在最后一步会强制收工。
"""
    return sys_prompt


def _truncate(text, limit):
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + f"\n…（截断，共 {len(text)} 字符）"


def _inside_root(path):
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    p = p.resolve()
    if ROOT not in p.parents and p != ROOT:
        raise ValueError(f"路径须在仓库目录内：{path}")
    return p


def _tool_bash(job, args, cfg):
    cmd = str(args.get("command") or "")
    if not cmd.strip():
        return {"exit_code": 2, "error": "command 为空"}
    env = dict(os.environ)
    env["WF_ROOT"] = str(wf.default_root())
    env["WF_ACTOR"] = f"layer:{job['layer']}"
    try:
        p = subprocess.run(cmd, shell=True, cwd=str(ROOT), env=env, text=True,
                           capture_output=True, timeout=cfg["command_timeout_s"])
        return {"exit_code": p.returncode,
                "stdout": _truncate(p.stdout, cfg["max_output_chars"]),
                "stderr": _truncate(p.stderr, cfg["max_output_chars"])}
    except subprocess.TimeoutExpired:
        return {"exit_code": 124, "error": f"命令超时（>{cfg['command_timeout_s']}s）"}


def _tool_read_file(job, args, cfg):
    p = _inside_root(args.get("path", ""))
    if not p.is_file():
        return {"error": f"不是文件：{args.get('path')}"}
    text = p.read_text(encoding="utf-8", errors="replace")
    offset = max(0, int(args.get("offset") or 0))
    limit = int(args.get("limit") or 0)
    if offset or limit:
        lines = text.splitlines()
        text = "\n".join(lines[offset: offset + limit if limit else None])
    return {"path": str(p.relative_to(ROOT)), "content": _truncate(text, cfg["max_output_chars"])}


def _tool_write_file(job, args, cfg):
    p = _inside_root(args.get("path", ""))
    text = str(args.get("content") or "")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(p) + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
    return {"path": str(p.relative_to(ROOT)), "bytes": len(text.encode("utf-8"))}


def _tool_list_dir(job, args, cfg):
    p = _inside_root(args.get("path", "."))
    if not p.is_dir():
        return {"error": f"不是目录：{args.get('path')}"}
    out = []
    for x in sorted(p.iterdir(), key=lambda z: (not z.is_dir(), z.name))[:300]:
        out.append({"name": x.name, "dir": x.is_dir()})
    return {"path": str(p.relative_to(ROOT)), "entries": out}


def _run_tool(job, name, args, cfg):
    if name == "bash":
        return _tool_bash(job, args, cfg)
    if name == "read_file":
        return _tool_read_file(job, args, cfg)
    if name == "write_file":
        return _tool_write_file(job, args, cfg)
    if name == "list_dir":
        return _tool_list_dir(job, args, cfg)
    return {"error": f"未知工具：{name}"}


def _chat(cfg, messages, tools=None, tool_choice=None):
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    tool_list = TOOLS if tools is None else tools
    body = {"model": cfg["model"], "messages": messages, "temperature": 0.2}
    if tool_list:
        body["tools"] = tool_list
        if tool_choice:
            body["tool_choice"] = tool_choice
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['api_key']}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"模型接口 HTTP {e.code}：{detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"模型接口连不上：{e.reason}")
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    if not msg:
        raise RuntimeError(f"模型返回里没有 message：{str(data)[:500]}")
    return msg


def _run_agent(job, cfg):
    w = wf.WF(wf.default_root())
    env = _pkg(w, job["package_id"])
    job["layer"] = env["identity"]["layer_id"]
    max_turns = max(1, int(cfg["max_turns"]))
    system = _agent_prompt(w, env, max_turns)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": "现在开始这一层。先看简报与上游卡片，再动手。"}]
    started = time.time()
    for turn in range(1, max_turns + 1):
        if MANAGER.stop:
            _log(job, "stopped", turn=turn)
            return "stopped"
        job["turns"] = turn
        _save_job(job)
        _log(job, "turn", turn=turn)
        final_turn = turn == max_turns
        if turn == max(1, max_turns - 5):
            messages.append({"role": "user", "content":
                             f"提醒：只剩 {max_turns - turn + 1} 步模型调用（含本步）。停止扩大探索，"
                             "优先把本层最小可交付补齐、跑核对，然后调用 finish 写总结。"})
        if final_turn:
            _log(job, "budget_final", turn=turn)
            messages.append({"role": "user", "content":
                             "最后一步：不要再调用其他工具。用一段话总结你做了什么、改了什么、跑了什么核对、"
                             "还有什么没解决；然后调用 finish。"})
            msg = _chat(cfg, messages, tools=[TOOLS[-1]],
                        tool_choice={"type": "function", "function": {"name": "finish"}})
        else:
            msg = _chat(cfg, messages)
        content = msg.get("content") or ""
        calls = msg.get("tool_calls") or []
        if content:
            _log(job, "assistant", text=_truncate(content, 4000))
        assistant = {"role": "assistant", "content": content}
        if calls:
            assistant["tool_calls"] = calls
        messages.append(assistant)
        if not calls:
            _log(job, "final_text", text=_truncate(content, 4000))
            job["summary"] = _truncate(content, 2000)
            job["duration_s"] = round(time.time() - started, 1)
            return "finished" if not final_turn else "max_turns"
        for call in calls:
            fn = (call.get("function") or {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            _log(job, "tool_call", name=name, args=_truncate(json.dumps(args, ensure_ascii=False), 2000))
            if name == "finish":
                _log(job, "finish", summary=_truncate(args.get("summary", ""), 4000))
                job["summary"] = args.get("summary", "")
                job["duration_s"] = round(time.time() - started, 1)
                return "finished"
            try:
                result = _run_tool(job, name, args, cfg)
            except Exception as e:  # noqa: BLE001
                result = {"error": f"{type(e).__name__}: {e}"}
            _log(job, "tool_result", name=name, result=_truncate(json.dumps(result, ensure_ascii=False), 4000))
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "name": name, "content": json.dumps(result, ensure_ascii=False)})
    job["duration_s"] = round(time.time() - started, 1)
    return "max_turns"


def _gate(w, pkg):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        verdict, report = w.gate(pkg, verbose=False)
    return verdict, report


def _chain_dispatchable(env):
    """自动链只接“可以开工”的下一层包。

    作废/被取代的包不能在死包上继续跑；已通过或待人决定的包也不该被自动重跑：
    前者已经收口，后者必须等人签字，自动派发会绕过人的决定。
    """
    status = (env.get("lifecycle") or {}).get("status", "")
    return status not in wf.BAD_LIFECYCLE and status not in ("passed", "needs_decision")


def _next_package(w, pkg):
    env = _pkg(w, pkg)
    task, it = w.iteration_of(env)
    if not task or not it:
        return None
    nodes = wf.template_nodes(w, task, it)
    for i, nd in enumerate(nodes):
        ids = [e["identity"]["package_id"] for e in nd["own"] + nd["reused"]]
        if pkg in ids and i + 1 < len(nodes):
            for nxt in nodes[i + 1]["own"]:
                if _chain_dispatchable(nxt):
                    return nxt["identity"]["package_id"]
            return None
    return None


class JobManager:
    """同一时刻只允许一个作业（store 是追加式，没有并发锁）。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.active = None
        self.stop = False

    def start(self, pkg, auto=True, by="", agent=""):
        cfg = load_config()
        if not (cfg.get("base_url") and cfg.get("model") and cfg.get("api_key")):
            raise RuntimeError("运行时还没配置模型：先在 Agent 管理 → 运行时填 base_url / model / api_key")
        w = wf.WF(wf.default_root())
        env = _pkg(w, pkg)
        lifecycle = (env.get("lifecycle") or {}).get("status", "")
        if lifecycle in ("invalidated", "superseded"):
            raise RuntimeError("层包已作废/被取代，不能派发；请新开一轮迭代或建新的层包")
        layer = env["identity"]["layer_id"]
        agent_name = agent or wf.agent_of(layer).get("name", f"{layer} agent")
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        with self.lock:
            if self.active:
                raise RuntimeError(f"已有作业在跑：{self.active}；同一时刻只允许一个（store 无并发锁）")
            job_id = _job_id()
            job = {
                "job_id": job_id, "created_at": _now(), "updated_at": _now(),
                "package_id": pkg, "layer": layer, "auto": bool(auto),
                "by": by or "", "agent": agent_name, "model": cfg.get("model", ""),
                "engine": cfg.get("engine", "v1"),
                "status": "queued", "turns": 0, "gate_verdict": "",
                "summary": "", "error": "", "duration_s": None,
                "signoff_decision_id": "",
                "log_path": str(_log_path(job_id)),
                "chain": [pkg],
            }
            _save_job(job)
            _log(job, "created", package_id=pkg, auto=bool(auto), agent=agent_name)
            self.active = job_id
            self.stop = False
            threading.Thread(target=self._run, args=(job,), daemon=True).start()
            return job

    def request_stop(self, job_id):
        with self.lock:
            if self.active != job_id:
                return False
            self.stop = True
            return True

    def _run(self, job):
        job["status"] = "running"
        _save_job(job)
        current = job["package_id"]
        try:
            while current:
                if self.stop:
                    job["status"] = "stopped"
                    break
                job["package_id"] = current
                job["layer"] = _pkg(wf.WF(wf.default_root()), current)["identity"]["layer_id"]
                if current not in job["chain"]:
                    job["chain"].append(current)
                _save_job(job)
                _log(job, "dispatch", package_id=current, layer=job["layer"])
                cfg = load_config()
                if cfg.get("engine") == "langgraph":
                    from runtime_v2 import run_agent as run_v2_agent
                    result = run_v2_agent(job, cfg)
                else:
                    result = _run_agent(job, cfg)
                if result == "stopped":
                    job["status"] = "stopped"
                    break
                if job.get("layer") in ("L2", "L3", "L4", "L5", "L6", "L7", "L8"):
                    try:
                        lr = wf.ensure_layer_report(wf.WF(wf.default_root()), current,
                                                    by=job.get("agent", "") or "平台运行时")
                        _log(job, "layer_report", package_id=current,
                             artifact_id=lr.get("artifact_id", ""), path=lr.get("path", ""))
                    except Exception as e:  # noqa: BLE001
                        _log(job, "layer_report_error", error=f"{type(e).__name__}: {e}")
                verdict, report = _gate(wf.WF(wf.default_root()), current)
                job["gate_verdict"] = verdict
                _log(job, "gate", package_id=current, verdict=verdict,
                     report=_truncate(json.dumps(report, ensure_ascii=False, default=str), 4000))
                if verdict == "needs_decision":
                    # 只差签字：按本层已产出的结果生成待签事项，人再批准/驳回
                    sid = wf.ensure_signoff_pending(wf.WF(wf.default_root()), current, by=job["agent"])
                    if sid:
                        job["signoff_decision_id"] = sid
                        _log(job, "signoff_pending", package_id=current, decision_id=sid)
                if result == "max_turns":
                    job["status"] = ("passed" if verdict == "pass"
                                     else "needs_decision" if verdict == "needs_decision" else "max_turns")
                    job["summary"] = (job.get("summary") or "") + f"｜{current} 模型步数用尽，平台强制收工并核对：{verdict}"
                    break
                if verdict != "pass":
                    job["status"] = "needs_decision" if verdict == "needs_decision" else "gate_failed"
                    job["summary"] = (job.get("summary") or "") + f"｜{current} 核对：{verdict}"
                    break
                try:
                    wf.cmd_run_log(wf.WF(wf.default_root()), argparse.Namespace(
                        pkg=current, agent=job["agent"], model=job["model"],
                        duration=job.get("duration_s"), note="平台运行时"))
                except SystemExit:
                    pass
                if not job["auto"]:
                    job["status"] = "passed"
                    break
                nxt = _next_package(wf.WF(wf.default_root()), current)
                if not nxt:
                    job["status"] = "passed"
                    _log(job, "chain_end", package_id=current,
                         reason="下一层没有可自动派发的包（无包、已作废、已通过或待人决定）")
                    break
                nxt_env = _pkg(wf.WF(wf.default_root()), nxt)
                if not _chain_dispatchable(nxt_env):
                    # 防止检查后状态又变：链式跳转前再核实一次，绝不在作废/待签/已收口包上开工。
                    job["status"] = "passed"
                    _log(job, "chain_end", package_id=current,
                         reason=f"下一层 {nxt} 状态为 "
                                f"{(nxt_env.get('lifecycle') or {}).get('status', '未知')}，不自动派发")
                    break
                _log(job, "chain_next", from_package=current, to_package=nxt)
                current = nxt
        except Exception as e:  # noqa: BLE001
            job["status"] = "error"
            job["error"] = f"{type(e).__name__}: {e}"
            _log(job, "error", error=job["error"])
        finally:
            if job["status"] in ("queued", "running"):
                job["status"] = "passed" if job.get("gate_verdict") == "pass" else "error"
            job["finished_at"] = _now()
            _save_job(job)
            _log(job, "done", status=job["status"])
            with self.lock:
                self.active = None
                self.stop = False


MANAGER = JobManager()


def list_jobs(limit=20):
    if not JOBS_DIR.exists():
        return []
    items = []
    for p in sorted(JOBS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        j.pop("_stop", None)
        items.append(j)
    return items


def get_job(job_id, tail=200):
    p = _job_path(job_id)
    if not p.exists():
        return None
    j = json.loads(p.read_text(encoding="utf-8"))
    j.pop("_stop", None)
    logs = []
    lp = Path(j.get("log_path", ""))
    if lp.exists():
        lines = lp.read_text(encoding="utf-8").splitlines()
        for line in lines[-tail:]:
            try:
                logs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    j["log"] = logs
    j["active"] = MANAGER.active == job_id
    return j


def runtime_status():
    cfg = public_config()
    active = get_job(MANAGER.active) if MANAGER.active else None
    return {"config": cfg, "active_job": active, "jobs": list_jobs(10)}


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description="平台内部的层 agent 运行时")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("check", help="看配置是否齐全")
    sub.add_parser("jobs", help="列最近的作业")
    run = sub.add_parser("run", help="派一个层包并等待作业结束")
    run.add_argument("--pkg", required=True)
    run.add_argument("--auto", action="store_true",
                     help="核对通过后自动派下一个可开工的框（无包/作废/已通过/待人决定就停）")
    run.add_argument("--agent", default="", help="执行者署名（默认用 agent 定义名）")
    run.add_argument("--by", default="", help="发起人")
    args = ap.parse_args()
    if args.cmd == "check":
        print(json.dumps(public_config(), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "jobs":
        print(json.dumps(list_jobs(20), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "run":
        job = MANAGER.start(args.pkg, auto=args.auto, by=args.by, agent=args.agent)
        print(f"作业：{job['job_id']}（{job['package_id']}）")
        while True:
            cur = get_job(job["job_id"])
            if cur and cur["status"] in ("passed", "gate_failed", "needs_decision", "error",
                                          "stopped", "max_turns"):
                print(json.dumps({k: cur[k] for k in ("job_id", "status", "gate_verdict", "turns",
                                                     "summary", "error", "chain")},
                                 ensure_ascii=False, indent=2))
                for ev in cur.get("log", [])[-30:]:
                    print(json.dumps(ev, ensure_ascii=False))
                return 0 if cur["status"] == "passed" else 1
            time.sleep(1)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
