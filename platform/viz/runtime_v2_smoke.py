#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime_v2_smoke — L1 LangGraph 运行时冒烟（假模型，全程临时副本）。

用法：用带 LangGraph 的 venv 跑：
  .venv-runtime/bin/python platform/viz/runtime_v2_smoke.py

它复制一份 store/domains/tasks 到临时目录，启动一个假 OpenAI 接口与 server.py，
派发一个 L1 包，断言：engine=langgraph、没有 bash 工具、payload/interface 落盘、gate 有 verdict、
checkpoint 存在。不碰真实 store。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeModel(BaseHTTPRequestHandler):
    """按工具名推进：brief → register → payload → interface → gate → finish。"""
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n).decode())
        msgs = body.get("messages", [])
        if msgs and "请只输出 1–2 句自然语言思考" in str(msgs[-1].get("content") or ""):
            data = json.dumps({"choices": [{"message": {
                "role": "assistant", "content": "观察：冒烟上下文已就绪，下一步按工具推进。"}}]})
            data = data.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        tools = [m for m in msgs if m.get("role") == "tool"]
        names = [m.get("name") for m in tools]
        aid = "ART-fake"
        for m in tools:
            if m.get("name") == "wf_register_artifact":
                try:
                    aid = json.loads(m.get("content") or "{}").get("artifact_id", aid)
                except json.JSONDecodeError:
                    pass
        if "wf_brief" not in names:
            name, args = "wf_brief", {}
        elif "wf_register_artifact" not in names:
            name, args = "wf_register_artifact", {"path": "domains/cvfpu/baseline_demo.txt",
                                                  "artifact_type": "stats"}
        elif "wf_payload_patch" not in names:
            name, args = "wf_payload_patch", {"patch": {
                "benchmarks": [{"name": "smoke-bench", "kind": "smoke", "artifact_refs": [aid]}],
                "metric_definitions": [{"name": "m", "definition": "d", "unit": "u"}],
                "baseline_refs": [aid]}}
        elif "wf_interface_patch" not in names:
            name, args = "wf_interface_patch", {"interface": {
                "question": "冒烟：量什么、基线是多少",
                "gives": [{"id": "S1", "text": "冒烟结论", "truth": "assumed", "evidence": [aid]}],
                "open": [], "limits": []}}
        elif "wf_gate" not in names:
            name, args = "wf_gate", {}
        else:
            name, args = "finish", {"summary": "冒烟完成"}
        i = len(tools)
        msg = {"role": "assistant", "content": "", "tool_calls": [{
            "id": f"c{i}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
        data = json.dumps({"choices": [{"message": msg}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def setup(root):
    (root / "platform").mkdir(parents=True)
    for name in ("tools", "schemas", "store", "agents", "viz"):
        shutil.copytree(REPO / "platform" / name, root / "platform" / name,
                        ignore=shutil.ignore_patterns("__pycache__", "idempotency.jsonl"))
    for name in ("domains", "tasks", ".agents"):
        shutil.copytree(REPO / name, root / name)
    (root / "domains" / "cvfpu" / "baseline_demo.txt").write_text("smoke baseline\n", encoding="utf-8")


def request(port, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main():
    try:
        import langgraph  # noqa: F401
    except ImportError:
        print("SKIP：当前 Python 没有 langgraph；请用 .venv-runtime/bin/python 跑")
        return 2
    sandbox = Path(tempfile.mkdtemp(prefix="viz-v2-smoke-"))
    fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    proc = None
    try:
        setup(sandbox)
        port = free_port()
        env = dict(os.environ, WF_ROOT=str(sandbox), RUNTIME_ENGINE="langgraph",
                   RUNTIME_MAX_TURNS="20", RUNTIME_MODEL="fake", RUNTIME_API_KEY="x",
                   RUNTIME_BASE_URL=f"http://127.0.0.1:{fake.server_address[1]}/v1")
        proc = subprocess.Popen([PY, str(sandbox / "platform" / "viz" / "server.py"),
                                 "--port", str(port)], cwd=sandbox, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        for _ in range(80):
            try:
                st = request(port, "GET", "/api/state")
                if st.get("store_root") == str(sandbox):
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.25)
        pkgs = [json.load(open(p))["identity"]["package_id"]
                for p in (sandbox / "domains").glob("*/L1_*/*/envelope.json")]
        if not pkgs:
            print("SKIP：副本里没有 L1 包")
            return 2
        pkg = next((p for p in pkgs if p.endswith("-r1")), pkgs[0])
        r = request(port, "POST", "/api/action", {"action": "dispatch", "pkg": pkg,
                                                  "auto": False, "agent": "smoke"})
        if not r.get("ok"):
            print("FAIL dispatch:", r)
            return 1
        jid = r["result"]["job_id"]
        job = {}
        for _ in range(120):
            job = request(port, "GET", f"/api/job/{jid}")
            if job.get("status") in ("passed", "needs_decision", "gate_failed", "error",
                                     "stopped", "max_turns"):
                break
            time.sleep(0.1)
        events = [e.get("event") for e in job.get("log", [])]
        tools = [e.get("name") for e in job.get("log", []) if e.get("event") == "tool_call"]
        thoughts = [e.get("text") for e in job.get("log", []) if e.get("event") == "thought"]
        env_path = next((sandbox / "domains").glob(f"*/L1_*/{pkg}/envelope.json"))
        envp = json.load(open(env_path))
        ref = next((a for a in (envp.get("artifacts") or [])
                    if "baseline_demo" in str(a.get("uri") or "")), {})
        aid = ref.get("artifact_id", "")
        art = request(port, "GET", f"/api/artifact?pkg={pkg}&id={aid}") if aid else {}
        saved = request(port, "POST", "/api/action", {
            "action": "artifact_save", "pkg": pkg, "artifact": aid, "agent": "smoke",
            "content": "smoke baseline edited\n",
            "base_hash": art.get("content_hash", ""),
        }) if aid else {}
        envp2 = json.load(open(env_path))
        new_ref = next((a for a in (envp2.get("artifacts") or [])
                        if "baseline_demo" in str(a.get("uri") or "")), {})
        new_aid = new_ref.get("artifact_id", "")
        checks = {
            "engine=langgraph": job.get("engine") == "langgraph",
            "没有 bash 工具": "bash" not in tools and "write_file" not in tools,
            "L1 没有通用仓库/命令工具": "repo_read" not in tools and "run_command" not in tools,
            "有 ReAct thought": bool(thoughts),
            "payload 已写": bool(envp.get("payload", {}).get("benchmarks")),
            "interface 已写": bool(envp.get("interface")),
            "gate 有 verdict": job.get("gate_verdict") in ("pass", "fail", "needs_decision"),
            "作业结束": job.get("status") in ("passed", "needs_decision", "gate_failed"),
            "checkpoint 存在": (sandbox / "platform" / "runtime" / "checkpoints.sqlite").exists(),
            "工件可打开": art.get("text") == "smoke baseline\n",
            "编辑生成新版本": bool(saved.get("ok") and (saved.get("result") or {}).get("changed")
                               and new_aid and new_aid != aid
                               and (sandbox / "domains" / "cvfpu" / "baseline_demo.txt").read_text()
                               == "smoke baseline\n"),
        }
        for k, ok in checks.items():
            print(("  ✓ " if ok else "  ✗ ") + k)
        print(f"  job={jid} status={job.get('status')} steps={job.get('turns')} thoughts={len(thoughts)} tools={tools}")
        return 0 if all(checks.values()) else 1
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        fake.shutdown()
        shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
