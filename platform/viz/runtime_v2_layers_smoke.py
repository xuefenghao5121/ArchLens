#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime_v2_layers_smoke — L1–L8 LangGraph 工具面冒烟（假模型，临时副本）。

不追求每层 gate pass（真实交付由模型完成），只验证：
  - 每一层都走 engine=langgraph；
  - L1 没有通用 bash/repo 读；L2/L5/L6/L7 有受控 run_command 与文件写；
  - 每层工具调用能进入 reflect/finish，作业不崩；
  - L4 能写 contract_manifest，L2/L5/L6/L7 能登记测试。

用法：.venv-runtime/bin/python platform/viz/runtime_v2_layers_smoke.py
"""
import json
import os
import re
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
LAYERS = ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PAYLOADS = {
    "L1": {
        "benchmarks": [{"name": "smoke-bench", "kind": "smoke", "artifact_refs": ["ART-fake"]}],
        "metric_definitions": [{"name": "m", "definition": "d", "unit": "u"}],
        "baseline_refs": ["ART-fake"],
    },
    "L2": {
        "proposition": {"statement": "smoke", "predicted_direction": "up",
                        "failure_condition": "flat", "validity_scope": "smoke"},
        "experiment_matrix": [{"id": "E1"}],
        "alternative_explanations": [{"id": "A1", "text": "smoke"}],
        "attribution_verdict": "model_supported",
    },
    "L3": {"candidates": [{"candidate_id": "C1", "expected_mechanism": "smoke",
                           "targets_bottleneck": "PKG-L2-cvfpu-cast-overflow-attribution"}]},
    "L4": {"resource_params": [{"name": "p", "value": 1, "verification": "assumed"}]},
    "L5": {"rtl_modules": [{"name": "m"}], "testbench_refs": ["tb"]},
    "L6": {"simobject_mappings": [{"simobject": "S", "rtl_module": "m", "tick_rules": "1/tick"}]},
    "L7": {"entry_refs": ["smoke"], "environment": {"tool": "smoke"}, "reproduce_command": "true"},
    "L8": {"conclusions": [{"statement": "smoke", "model_causal_status": "model_only",
                            "hardware_evidence_status": "none"}],
           "validity_scope": "smoke"},
}


class FakeLayerModel(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n).decode())
        msgs = body.get("messages", [])
        if msgs and "请只输出 1–2 句自然语言思考" in str(msgs[-1].get("content") or ""):
            data = json.dumps({"choices": [{"message": {
                "role": "assistant", "content": "观察：冒烟上下文已就绪，下一步按层推进。"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        system = next((m.get("content") or "" for m in msgs if m.get("role") == "system"), "")
        m = re.search(r"（(L[1-8]) 层", system)
        layer = m.group(1) if m else "L1"
        done = [m.get("name") for m in msgs if m.get("role") == "tool"]
        if "wf_brief" not in done:
            name, args = "wf_brief", {}
        elif layer == "L1" and "wf_register_artifact" not in done:
            name, args = "wf_register_artifact", {"path": "domains/cvfpu/baseline_demo.txt",
                                                  "artifact_type": "data"}
        elif layer == "L4" and "wf_contract_patch" not in done:
            name, args = "wf_contract_patch", {"patch": {
                "manifest_id": "MAN-smoke", "validity_scope": "smoke",
                "interfaces": [{"name": "smoke"}]}}
        elif layer in ("L2", "L5", "L6", "L7") and "run_command" not in done:
            name, args = "run_command", {"command": "true"}
        elif layer in ("L2", "L5", "L6", "L7") and "repo_write" not in done:
            name, args = "repo_write", {"path": "designs/smoke-run.txt", "content": "run ok\n"}
        elif layer in ("L2", "L5", "L6", "L7") and "wf_test_register" not in done:
            name, args = "wf_test_register", {
                "title": "smoke", "proposition": "smoke", "metric": "m",
                "predicted_direction": "up", "failure_condition": "flat",
                "validity_scope": "smoke", "run_artifact": "designs/smoke-run.txt",
                "verdict": "pass",
            }
        elif "wf_payload_patch" not in done:
            name, args = "wf_payload_patch", {"patch": PAYLOADS[layer]}
        elif "wf_interface_patch" not in done:
            name, args = "wf_interface_patch", {"interface": {
                "question": "smoke", "gives": [{"id": "S1", "text": "smoke",
                                                "truth": "assumed", "evidence": []}]}}
        elif "wf_gate" not in done:
            name, args = "wf_gate", {}
        else:
            name, args = "finish", {"summary": f"{layer} 冒烟完成"}
        i = len(done)
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
        import shutil
        shutil.copytree(REPO / "platform" / name, root / "platform" / name,
                        ignore=shutil.ignore_patterns("__pycache__", "idempotency.jsonl"))
    import shutil
    for name in ("domains", "tasks", ".agents"):
        shutil.copytree(REPO / name, root / name)
    (root / "domains" / "cvfpu" / "baseline_demo.txt").write_text("smoke baseline\n", encoding="utf-8")
    env = dict(os.environ, WF_ROOT=str(root))
    for layer in LAYERS[1:]:
        subprocess.run([PY, str(root / "platform" / "tools" / "wf.py"), "new-pkg",
                        "--domain", "cvfpu", "--layer", layer,
                        "--slug", f"i1-{layer.lower()}-v2smoke",
                        "--title", f"{layer} v2 smoke",
                        "--iteration", "TASK-cvfpu-overflow/1"],
                       cwd=root, env=env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)


def request(port, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def pkg_for(root, layer):
    hits = sorted((root / "domains").glob(f"*/{layer}_*/*/envelope.json"))
    for p in hits:
        e = json.load(open(p))
        if e.get("identity", {}).get("package_id", "").endswith("v2smoke") or layer == "L1":
            return e["identity"]["package_id"]
    return json.load(open(hits[0]))["identity"]["package_id"] if hits else ""


def main():
    try:
        import langgraph  # noqa: F401
    except ImportError:
        print("SKIP：当前 Python 没有 langgraph；请用 .venv-runtime/bin/python 跑")
        return 2
    sandbox = Path(tempfile.mkdtemp(prefix="viz-v2-layers-"))
    fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeLayerModel)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    proc = None
    try:
        setup(sandbox)
        port = free_port()
        env = dict(os.environ, WF_ROOT=str(sandbox), RUNTIME_ENGINE="langgraph",
                   RUNTIME_MAX_TURNS="24", RUNTIME_MODEL="fake", RUNTIME_API_KEY="x",
                   RUNTIME_BASE_URL=f"http://127.0.0.1:{fake.server_address[1]}/v1")
        proc = subprocess.Popen([PY, str(sandbox / "platform" / "viz" / "server.py"),
                                 "--port", str(port)], cwd=sandbox, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        for _ in range(80):
            try:
                if request(port, "GET", "/api/state").get("store_root") == str(sandbox):
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.25)
        ok = True
        for layer in LAYERS:
            pkg = pkg_for(sandbox, layer)
            r = request(port, "POST", "/api/action", {"action": "dispatch", "pkg": pkg,
                                                       "auto": False, "agent": "layers-smoke"})
            if not r.get("ok"):
                print(f"  ✗ {layer} dispatch: {r.get('error')}")
                ok = False
                continue
            jid = r["result"]["job_id"]
            job = {}
            for _ in range(200):
                job = request(port, "GET", f"/api/job/{jid}")
                if job.get("status") in ("passed", "needs_decision", "gate_failed", "error",
                                         "stopped", "max_turns"):
                    break
                time.sleep(0.05)
            tools = [e.get("name") for e in job.get("log", []) if e.get("event") == "tool_call"]
            thought = any(e.get("event") == "thought" for e in job.get("log", []))
            good = (job.get("engine") == "langgraph" and "bash" not in tools
                    and "write_file" not in tools and job.get("status") != "error"
                    and thought and "finish" in tools)
            if layer == "L1":
                good = good and "repo_read" not in tools and "run_command" not in tools
            if layer in ("L2", "L5", "L6", "L7"):
                good = good and "run_command" in tools and "wf_test_register" in tools
            if layer == "L4":
                good = good and "wf_contract_patch" in tools
            ok = ok and good
            print(f"  {'✓' if good else '✗'} {layer}  status={job.get('status')} "
                  f"steps={job.get('turns')} tools={tools}")
        return 0 if ok else 1
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        fake.shutdown()
        import shutil
        shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
