#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""regress — 可视化服务（server.py）与 wf 写路径的回归用例。

每条用例对应一个真实出过的问题（2026-11-23 评审）："当初会出事，现在不应出事"。
wf.py 的 selftest 只覆盖门禁的故障注入，不覆盖这些 HTTP 面与状态机行为。

全程在临时沙箱里跑：把 store / schemas / tools / domains 复制到临时目录，以 WF_ROOT
指向该目录启动 server.py，所有写操作只落在副本上。开跑前先确认服务端报告的 store_root
就是沙箱——不是就立即中止，不执行任何写用例。结束后再核对真实 store 指纹未变。

用法：
    python3 platform/viz/regress.py          # 退出码 0 = 全部通过
"""
import collections
import glob
import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable


def store_fingerprint():
    """真实 store 的内容指纹（索引、证据图、层包信封、领域包、任务记录）。"""
    files = sorted(
        list((REPO / "platform" / "store").rglob("*.jsonl"))
        + list((REPO / "domains").rglob("envelope.json"))
        + list((REPO / "domains").rglob("pack.json"))
        + list((REPO / "domains").rglob("tasks/*.json"))
        + list((REPO / "tasks").glob("*.json")))
    h = hashlib.sha256()
    for p in files:
        h.update(str(p.relative_to(REPO)).encode())
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def build_sandbox(root):
    (root / "platform").mkdir(parents=True)
    for name in ("tools", "schemas", "store", "agents"):
        shutil.copytree(REPO / "platform" / name, root / "platform" / name,
                        ignore=shutil.ignore_patterns("__pycache__", "idempotency.jsonl"))
    # agent 定义会核对 skills 在不在磁盘上；沙箱里也给一份
    shutil.copytree(REPO / ".agents", root / ".agents")
    (root / "platform" / "viz").mkdir()
    shutil.copy2(REPO / "platform" / "viz" / "server.py", root / "platform" / "viz" / "server.py")
    shutil.copytree(REPO / "domains", root / "domains")
    if (REPO / "tasks").exists():
        shutil.copytree(REPO / "tasks", root / "tasks")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Ctx:
    """沙箱根 + 服务端口 + 一组检查辅助。"""

    def __init__(self, root, port):
        self.root, self.port = root, port
        self.passed = self.failed = 0

    # ---- 请求 ----
    def req(self, method, path, body=None, headers=None):
        try:
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
            h = dict(headers or {})
            data = (body if isinstance(body, bytes)
                    else json.dumps(body).encode() if body is not None else None)
            if data is not None:
                h["Content-Length"] = str(len(data))
            c.request(method, path, body=data, headers=h)
            r = c.getresponse()
            raw = r.read().decode()
            c.close()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
        except OSError as e:  # 服务挂了：记成一次失败请求，而不是让整个回归崩掉
            return 0, {"error": f"连接失败：{e}"}

    def post(self, body, headers=None):
        return self.req("POST", "/api/action", body, {"Content-Type": "application/json", **(headers or {})})

    def get(self, path):
        return self.req("GET", path)[1]

    def wf(self, *args, extra_env=None):
        env = dict(os.environ, WF_ROOT=str(self.root), **(extra_env or {}))
        p = subprocess.run([PY, str(self.root / "platform" / "tools" / "wf.py"), *args],
                           capture_output=True, text=True, env=env, cwd=self.root, timeout=180)
        return p.returncode, (p.stdout + p.stderr).strip()

    # ---- 断言 ----
    def check(self, name, cond, detail=""):
        self.passed += bool(cond)
        self.failed += (not cond)
        print(f"  [{'✓' if cond else '✗'}] {name}" + (f"  —— {detail}" if detail and not cond else ""))

    def env(self, pkg):
        return json.load(open(glob.glob(str(self.root / "domains" / "*" / "*" / pkg / "envelope.json"))[0]))

    def edit_env(self, pkg, fn):
        """直接改沙箱里的信封（模拟层 agent 写 payload / 接口卡，以及核对之后再改）。"""
        path = Path(glob.glob(str(self.root / "domains" / "*" / "*" / pkg / "envelope.json"))[0])
        env = json.load(open(path, encoding="utf-8"))
        fn(env)
        json.dump(env, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    def statuses(self, domain="sme2"):
        return collections.Counter(
            json.load(open(p))["lifecycle"]["status"]
            for p in glob.glob(str(self.root / "domains" / domain / "*" / "*" / "envelope.json")))


def run(ctx: Ctx):
    L1S = "PKG-L1-sme2-benchmark-attention"    # sme2 的上游根包（19 个包依赖它）
    L1C = "PKG-L1-cvfpu-cast-overflow"          # 高人类权重层、正等待用户批准
    L3C = "PKG-L3-cvfpu-cast-overflow-fix"
    L4C = "PKG-L4-cvfpu-cast-overflow-contract"

    # 真实 store 里只保留 cvfpu 任务，其余已归档。下面这些用例要用回填的历史迭代（沿用、折叠、待批），
    # 先在沙箱副本里把它们恢复；归档本身在 R17 里单独测。
    for tid in ("TASK-sme2-flow1", "TASK-cvfpu-bugfix-demo"):
        ctx.wf("task-archive", "--task", tid, "--restore")
    print("（沙箱夹具：已恢复真实 store 里归档的两个历史任务，供下面沿用 / 折叠 / 待批的用例使用）")
    LAYERS = ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]
    STATE_WORDS = {"未开始", "进行中", "待人决定", "未验证", "有问题", "已验证", "已作废"}

    def pkg_of(state, pkg):
        return next(p for L in LAYERS for p in state["layers"][L]["packages"] if p["package_id"] == pkg)

    def task_of(state, tid):
        return next(t for t in state["tasks"] if t["task_id"] == tid)

    print("R1 写端点来源校验（别的网页不能借浏览器对本机发写请求）")
    st, j = ctx.post('{"action":"validate","pkg":"%s"}' % L1C,
                     {"Content-Type": "text/plain", "Origin": "https://evil.example"})
    ctx.check("text/plain + 外站 Origin 的 validate 被拒", st == 403 and j.get("ok") is False, f"HTTP {st}")
    form = '{"action":"revise","pkg":"%s","reason":"x","x":"=' % L1S + '"}'
    st, j = ctx.post(form, {"Content-Type": "text/plain", "Origin": "https://evil.example"})
    ctx.check("表单 text/plain 形态的 revise 被拒", st == 403, f"HTTP {st}")
    st, j = ctx.post({"action": "validate", "pkg": L1C}, {"Origin": "https://evil.example"})
    ctx.check("application/json 但 Origin 为外站 → 被拒", st == 403, f"HTTP {st}")
    st, j = ctx.post({"action": "validate", "pkg": L1C}, {"Sec-Fetch-Site": "cross-site"})
    ctx.check("Sec-Fetch-Site: cross-site → 被拒", st == 403, f"HTTP {st}")
    st, j = ctx.req("GET", "/api/state", None, {"Host": "attacker.example:8899"})
    ctx.check("外域 Host 的读请求被拒（防 DNS rebinding）", st == 403, f"HTTP {st}")
    st, j = ctx.req("GET", "/api/state", None, {"Host": "localhost:%d" % ctx.port})
    ctx.check("本机 Host 的读请求正常", st == 200 and isinstance(j, dict) and "summary" in j)
    st, j = ctx.post({"action": "what_if", "pkg": L1C}, {"Origin": "http://localhost:5173"})
    ctx.check("Vite 代理形态（Origin=localhost:5173）的写请求正常", st == 200 and j.get("ok"), str(j)[:80])
    ctx.check("被拒的请求没有改动 store",
              ctx.statuses("cvfpu")["needs_decision"] == 6
              and ctx.env(L1C)["validation"].get("gated_at", "") < "2026-11-20",
              str(ctx.env(L1C)["validation"].get("gated_at")))

    print("R2 进程稳定性与路径")
    st, j = ctx.req("GET", "/api/package/PKG-L9-does-not-exist")
    ctx.check("查不存在的包 → 404 JSON，而非进程退出", st == 404 and "找不到" in str(j), f"HTTP {st} {str(j)[:60]}")
    try:
        s_ = socket.create_connection(("127.0.0.1", ctx.port), timeout=10)
        s_.sendall(b"GET /api/package/../../../etc/envelope.json HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
        resp = s_.recv(4096).decode(errors="replace")
        s_.close()
        ctx.check("带 .. 的穿越请求 → 400", resp.split()[1] == "400", resp.split()[1] if resp else "无响应")
    except OSError as e:
        ctx.check("带 .. 的穿越请求 → 400", False, str(e))
    st, _ = ctx.req("GET", "/api/state")
    ctx.check("以上请求之后服务仍可用", st == 200)
    outside = Path(tempfile.mkdtemp(prefix="viz-regress-outside-"))
    try:
        target = outside / "envelope.json"
        json.dump(ctx.env(L3C), open(target, "w"))
        st, j = ctx.req("GET", "/api/package/" + str(target))
        ctx.check("store 之外的绝对路径 → 被拒", st == 400 and "非法" in str(j), f"HTTP {st} {str(j)[:60]}")
        st, j = ctx.post({"action": "validate", "pkg": str(target)})
        ctx.check("写动作带路径 → 被拒，外部文件未被改写",
                  not j.get("ok") and "gated_at" in json.load(open(target))["validation"], str(j)[:60])
    finally:
        shutil.rmtree(outside, ignore_errors=True)

    print("R3 读模型正确性")
    s = ctx.get("/api/state")
    sme2 = ctx.get("/api/state?domain=sme2")     # domain 参数仅为兼容旧调用，界面上不再有领域
    cv = ctx.get("/api/state?domain=cvfpu")
    pk = {p["package_id"]: p for L in LAYERS for p in sme2["layers"][L]["packages"]}
    ctx.check("证据状态方向：被证伪的 L3-O3 = disputed",
              pk.get("PKG-L3-sme2-o3-solution-derivation", {}).get("evidence_status") == "disputed")
    ctx.check("证据状态方向：提供证伪测量的 L7 = valid",
              pk.get("PKG-L7-sme2-combined-verdict-measurement", {}).get("evidence_status") == "valid")
    lp = [n for n in cv["dag"]["nodes"] if n["kind"] == "layer_package"]
    ctx.check("cvfpu 视图 DAG 只含 cvfpu 的 11 个层包", len(lp) == 11 and all(n["layer"] for n in lp), f"{len(lp)}")
    ctx.check("cvfpu 视图待批决策只有自己的 1 条",
              cv["summary"]["pending_decisions"] == 1
              and all(p.startswith("PKG-L1-cvfpu") for d in cv["pending_decisions"] for p in d["package_ids"]))
    ctx.check("sme2 视图待批决策只有自己的 2 条", sme2["summary"]["pending_decisions"] == 2)
    ctx.check("全量视图：30 个包、3 个任务、待批 3 条",
              s["summary"]["total_packages"] == 30 and len(s["tasks"]) == 3 and s["summary"]["pending_decisions"] == 3,
              str(s["summary"]))
    ctx.check("待批事项都带所属的包或任务，以及一句话结论",
              all("package_ids" in d and "task_id" in d and "summary" in d and "headline" in d
                  for d in s["pending_decisions"]))
    ctx.check("store 信息如实暴露",
              s["store_isolated"] is True and s["store_root"] == str(ctx.root) and s["schema_version"] == "0.5")
    ctx.check("幽灵 ID 标为 dangling_ref，不冒充层包",
              any(n["kind"] == "dangling_ref" for n in sme2["dag"]["nodes"])
              and not any(n["kind"] == "layer_package" and n["layer"] is None for n in sme2["dag"]["nodes"]))
    r = ctx.get("/api/recommend-flow?goal=research")
    ctx.check("不指定域时推荐流程在同一域内",
              r.get("resolved_domain") == "sme2" and all("cvfpu" not in (f["package_id"] or "") for f in r["flow"]),
              str(r.get("resolved_domain")))
    ctx.check("推荐流程不含被证伪的包", all(f.get("evidence_status") in ("ok", None) for f in r["flow"]))
    rc, out = ctx.wf("walk", "--domain", "cvfpu")
    ctx.check("wf walk --domain cvfpu 不列出 sme2 的待批决策",
              rc == 0 and "DEC-20261117-092747" not in out and "DEC-20261117-093133" not in out)
    tp = ctx.get("/api/templates")
    ctx.check("/api/templates：7 个模板，默认完整八层，每个模板列出它的框",
              [t["id"] for t in tp["templates"]] == [
                  "microarch_optimize", "bug_fix_simple", "bug_fix_with_attribution", "comparative_study",
                  "component_design", "batch_calibration", "benchmark_only"]
              and tp["default"] == "microarch_optimize"
              and [l["layer"] for l in tp["templates"][0]["layers"]] == LAYERS
              and [l["layer"] for l in tp["templates"][2]["layers"]] == ["L1", "L2", "L5", "L8"]
              and tp["templates"][6]["layers"] == [], str(tp)[:200])
    g = ctx.get("/api/guide")
    ctx.check("/api/guide：八层固定说明 + 标记的人读含义",
              [l["layer"] for l in g["layers"]] == LAYERS and g["layers"][0]["question"] == "在什么负载上、量什么、基线是多少"
              and g["layers"][0]["deliverables"] == ["负载", "指标定义", "基线"]
              and g["truth"] == {"observed": "实测", "deduced": "推断", "assumed": "拍定"}
              and set(g["frames"]) == {"F0", "F1", "F2", "F3"} and g["states"][0] == "已作废", str(g)[:200])
    ctx.check("/api/benchmarks：全局清单，不带领域",
              sorted(b["name"] for b in ctx.get("/api/benchmarks")) == ["cvfpu-cast-overflow", "vp-int4-attention"])
    st_d, _ = ctx.req("GET", "/api/domains")
    st_r, _ = ctx.req("GET", "/api/decisions")
    ctx.check("/api/domains 与 /api/decisions 已停用 → 404", st_d == 404 and st_r == 404, f"HTTP {st_d} {st_r}")
    d = ctx.get("/api/package/" + L1C)
    ctx.check("/api/package：带卡片（接口卡、五行核对回执、证据、决定回执）与原始记录",
              d["card"]["package_id"] == L1C and d["card"]["state"]["word"] == "待人决定"
              and len(d["card"]["checks"]) == 5 and isinstance(d["card"]["interface"].get("gives"), list)
              and isinstance(d["card"]["receipts"], list) and "payload" in d["raw"] and "artifacts" in d["raw"],
              str(d["card"].get("state")))

    # 只读的迭代用例放在 R5 之前：R5 会把 sme2 的 19 个包全部置为失效，之后「沿用」就无可复用的通过包了
    print("R10 任务 / 迭代：归属、沿用、模板节点（历史回填的三个任务）")
    t1 = task_of(s, "TASK-sme2-flow1")
    ctx.check("导出任务 TASK-sme2-flow1：已封板、三轮迭代",
              t1["status"] == "sealed" and [i["seq"] for i in t1["iterations"]] == [1, 2, 3])
    own_n = [sum(len(i["layers"][L]["own"]) for L in i["layers"]) for i in t1["iterations"]]
    ctx.check("三轮迭代自有包数 9 / 5 / 5", own_n == [9, 5, 5], str(own_n))
    i2, i3 = t1["iterations"][1], t1["iterations"][2]

    def reused(it, L):
        return [p["reused_from"] for p in it["layers"][L]["reused"]]

    ctx.check("迭代 2 的 L4/L5/L6 沿用迭代 1",
              reused(i2, "L4") == ["迭代 1"] and reused(i2, "L5") == ["迭代 1"] and reused(i2, "L6") == ["迭代 1"],
              f"{reused(i2, 'L4')} {reused(i2, 'L5')} {reused(i2, 'L6')}")
    ctx.check("迭代 3 的 L1 沿用迭代 2、L4/L6 沿用迭代 1",
              reused(i3, "L1") == ["迭代 2"] and reused(i3, "L4") == ["迭代 1"] and reused(i3, "L6") == ["迭代 1"],
              f"{reused(i3, 'L1')} {reused(i3, 'L4')} {reused(i3, 'L6')}")
    ctx.check("同层不混：迭代 3 的 L2 只有它自己的归因包",
              [p["package_id"] for p in i3["layers"]["L2"]["own"]] == ["PKG-L2-sme2-i3-acchain-attribution"]
              and not i3["layers"]["L2"]["reused"])
    ctx.check("每个迭代带模板顺序的框：八层的任务八个框，框里列出自有包与沿用包",
              [n["layer"] for n in t1["iterations"][0]["nodes"]] == LAYERS
              and t1["iterations"][0]["nodes"][0]["name"] == "观测"
              and t1["iterations"][1]["nodes"][3]["own"] == [] and t1["iterations"][1]["nodes"][3]["reused"],
              str(t1["iterations"][1]["nodes"][3]))
    ctx.check("完成与否锚定模板：迭代 1、2 完成；迭代 3 未完成，停在待人决定的 L7",
              [i["complete"] for i in t1["iterations"]] == [True, True, False]
              and t1["iterations"][2]["frontier"] == "L7"
              and t1["iterations"][2]["nodes"][6]["state"] == "待人决定",
              str([(n["layer"], n["state"]) for n in t1["iterations"][2]["nodes"]]))
    ctx.check("任务带决定回执：sme2 的章程由 huawei 补签",
              any(r["kind"] == "章程" and r["by"].startswith("huawei") and not r["pending"] and r["at"][:10] == "2026-11-17"
                  for r in t1["receipts"]), str(t1["receipts"])[:200])
    ctx.check("任务记录放在 tasks/，没有类型、没有领域输入项",
              (ctx.root / "tasks/TASK-sme2-flow1.json").exists()
              and not (ctx.root / "domains/sme2/tasks").exists()
              and "kind" not in json.load(open(ctx.root / "tasks/TASK-sme2-flow1.json")))
    ctx.check("30 个包全部归位：没有未归迭代的包",
              s["summary"]["unassigned_packages"] == 0, str(s["summary"]))
    ctx.check("已封板任务默认折叠：默认视图 8 包（cvfpu 溢出修复）、折叠 22",
              s["summary"]["view_packages"] == 8 and s["summary"]["hidden_packages"] == 22, str(s["summary"]))
    ctx.check("默认迭代 = cvfpu 溢出修复的迭代 1", s["summary"]["active_iteration"] == "ITER-cvfpu-overflow-1")
    rc, out = ctx.wf("next")
    ctx.check("wf next 不再列出已终止迭代里的空包",
              rc == 0 and "PKG-L1-cvfpu-anchor" not in out and "PKG-L1-cvfpu-cast-overflow" in out, out[:160])
    rc, out = ctx.wf("next")
    ctx.check("任务封板了，等人签字的决策仍然列出来（3 条 = sme2 两条 + cvfpu 一条）",
              "另有人类批准点 3 条" in out, out[-200:])
    rc, out = ctx.wf("next", "--iteration", "ITER-sme2-flow1-3")
    ctx.check("wf next --iteration：只看该迭代，已封板的不再催 Mission Review",
              rc == 0 and "迭代 3" in out and "Mission Review" not in out and "PKG-L1-cvfpu" not in out, out[:200])
    r = ctx.get("/api/recommend-flow?goal=research&iteration=ITER-sme2-flow1-2")
    by = {f["layer"]: f for f in r["flow"]}
    ctx.check("recommend-flow --iteration：迭代 2 的 L4/L5/L6 标为沿用迭代 1，自有包不标",
              r["iteration"]["iteration_id"] == "ITER-sme2-flow1-2"
              and by["L4"].get("reused_from") == "迭代 1" and by["L5"].get("reused_from") == "迭代 1"
              and by["L6"].get("reused_from") == "迭代 1" and by["L7"].get("reused_from") is None,
              str({L: (by[L]["action"], by[L].get("reused_from")) for L in by}))

    print("R14 卡片：状态词由核对结果机械得出；旧包的卡片是从现有字段摘录的（真实 30 个包逐个比对）")
    allp = [p for L in LAYERS for p in s["layers"][L]["packages"]]
    bad = []
    for p in allp:
        c, w_ = p["card"], p["state"]
        pend = any(r["pending"] for r in c["receipts"])
        if w_ not in STATE_WORDS:
            bad.append((p["package_id"], "非法状态词"))
        if (w_ == "已验证") != (p["lifecycle"] == "passed" and p["gate_verdict"] == "pass" and not pend):
            bad.append((p["package_id"], w_))
        if pend and w_ != "待人决定":
            bad.append((p["package_id"], "有待批事项却不是待人决定"))
        if p["invalidated"] and w_ != "已作废":
            bad.append((p["package_id"], "作废的包不是已作废"))
        if len(c["checks"]) != 5 or not isinstance(c["interface"].get("gives"), list) or not c["guide"]["question"]:
            bad.append((p["package_id"], "卡片缺核对回执 / 接口卡 / 层说明"))
    ctx.check("30 个包：状态词与 核对结论 / 待人决定 / 作废 一致，卡片字段齐全", not bad, str(bad)[:300])
    dist = collections.Counter(p["state"] for p in allp)
    ctx.check("状态分布 19 已验证 / 7 待人决定 / 3 未开始 / 1 进行中",
              dist == {"已验证": 19, "待人决定": 7, "未开始": 3, "进行中": 1}, str(dist))
    l8c = pkg_of(s, "PKG-L8-cvfpu-cast-overflow-conclusion")["card"]
    ctx.check("签了字但没重新核对：进行中——已有人签字，还没重新核对（不再说「等人签字」）",
              l8c["state"]["word"] == "进行中" and l8c["state"]["why"] == "已有人签字，还没重新核对"
              and l8c["state"]["verdict"] == "needs_decision", str(l8c["state"]))
    o3 = pkg_of(s, "PKG-L3-sme2-o3-solution-derivation")
    ctx.check("旧包：卡片标明是摘录，每条结论写明摘自哪个字段",
              o3["card"]["interface"].get("derived") is True and bool(o3["card"]["interface"]["gives"])
              and all(g_["text"] for g_ in o3["card"]["interface"]["gives"]), str(o3["card"]["interface"])[:200])
    ctx.check("旧包的卡片只是读出来的：envelope 里没有被写入 interface 或 interface_sha",
              "interface" not in ctx.env("PKG-L3-sme2-o3-solution-derivation")
              and "interface_sha" not in ctx.env("PKG-L3-sme2-o3-solution-derivation")["validation"])
    cvl1 = pkg_of(s, L1C)["card"]
    ctx.check("cvfpu 的 L1：五行的核对回执里，签字一行是 ✗（等人签字）",
              [c["mark"] for c in cvl1["checks"] if c["id"] == "human"] == ["✗"]
              and cvl1["state"]["why"].startswith("等人决定："), str(cvl1["checks"]))

    print("R4 审批链路")
    st, j = ctx.post({"action": "propose", "pkg": L1C, "rationale": "自批尝试", "reviewer": "human"})
    ctx.check("propose 指定审批人=human → 被拒", not j.get("ok"))
    st, j = ctx.post({"action": "propose", "pkg": L1C, "rationale": "自批尝试2", "type": "ratify", "reviewer": "zhang"})
    ctx.check("propose type=ratify → 被拒", not j.get("ok"))
    st, j = ctx.post({"action": "propose", "pkg": L1C, "rationale": "正常的待批建议", "summary": "基准锚定按虚拟原型口径拍定", "agent": "tester"})
    ctx.check("正常 propose 成功且 reviewer 为 pending-user",
              j.get("ok") and j["result"]["reviewer"] == "pending-user")
    st, j = ctx.post({"action": "validate", "pkg": L1C})
    ctx.check("propose + validate 不能放行高人类权重包",
              j.get("result", {}).get("verdict") == "needs_decision"
              and ctx.env(L1C)["lifecycle"]["status"] == "needs_decision",
              str(j.get("result", {}).get("verdict")))
    D = "DEC-20261117-092747-373b"
    st, j = ctx.post({"action": "approve", "dec": D, "as": ""})
    ctx.check("署名为空的批准 → 被拒", not j.get("ok"))
    st, j = ctx.post({"action": "approve", "dec": D, "as": "pending-user"})
    ctx.check("署名=pending-user 的批准 → 被拒", not j.get("ok"))
    st, j = ctx.post({"action": "approve", "dec": D, "as": "评测者A"})
    ctx.check("正常署名批准成功", j.get("ok") and j["result"]["signed_by"] == "评测者A", str(j)[:80])
    st, j = ctx.post({"action": "approve", "dec": D, "as": "评测者B"})
    ctx.check("同一条决策再批一次 → 被拒", not j.get("ok"))

    print("R5 验证不再复活已失效包；恢复须署名覆盖")
    st, j = ctx.post({"action": "revise", "pkg": L1S, "reason": "预览", "dry_run": True})
    ctx.check("revise 预览只算不写",
              j.get("ok") and j["result"].get("dry_run") and len(j["result"]["affected"]) == 19
              and ctx.statuses()["passed"] == 19, f"affected={len((j.get('result') or {}).get('affected', []))}")
    st, j = ctx.post({"action": "revise", "pkg": L1S, "reason": "基线数据被替换", "agent": "tester"})
    ctx.check("revise 实际执行：19 包失效", j.get("ok") and ctx.statuses()["invalidated"] == 19)
    st, j = ctx.post({"action": "validate", "pkg": L1S})
    ctx.check("对已失效的根包点验证：verdict=fail，状态仍是 invalidated",
              j.get("result", {}).get("verdict") == "fail"
              and ctx.env(L1S)["lifecycle"]["status"] == "invalidated")
    ctx.check("作废的包在卡片上是「已作废」，原因取自失效理由",
              pkg_of(ctx.get("/api/state"), L1S)["state"] == "已作废"
              and "基线数据被替换" in pkg_of(ctx.get("/api/state"), L1S)["card"]["state"]["why"])
    rc, out = ctx.wf("gate", L1S, "--override")
    ctx.check("覆盖缺署名/理由 → 拒绝", rc != 0 and "署名" in out)
    rc, out = ctx.wf("gate", L1S, "--override", "--as", "reviewer-A", "--reason", "复核确认基线未变，解除误失效")
    ctx.check("署名覆盖后门禁通过", rc == 0 and ctx.env(L1S)["lifecycle"]["status"] == "passed", out.splitlines()[-1][:70])
    decs = [json.loads(l) for l in open(ctx.root / "platform/store/index/decisions.jsonl", encoding="utf-8") if l.strip()]
    ov = [x for x in decs if x.get("decision_type") == "override" and x.get("reviewer") == "reviewer-A"
          and "解除误失效" in x.get("rationale", "")]
    ctx.check("覆盖留下了带署名的 override 决策并挂在包上",
              len(ov) == 1 and ov[0]["decision_id"] in ctx.env(L1S)["decision_refs"])
    ctx.check("失效与解除都留在理由历史里",
              len(ctx.env(L1S)["lifecycle"]["invalidation_reasons"]) == 2
              and "署名覆盖解除" in ctx.env(L1S)["lifecycle"]["invalidation_reasons"][-1])

    print("R6 乐观并发")
    h0 = ctx.get("/api/state")["store_env_hash"]
    ctx.post({"action": "validate", "pkg": L3C})
    h1 = ctx.get("/api/state")["store_env_hash"]
    ctx.check("validate 改了 envelope → 哈希变化", h0 != h1)
    st, j = ctx.post({"action": "propose", "pkg": L3C, "rationale": "旧视图提交", "summary": "旧视图提交", "target_version": h0})
    ctx.check("带旧哈希的写请求被拒（409）", st == 409 and j.get("error") == "store_version_conflict", f"HTTP {st}")
    st, j = ctx.post({"action": "propose", "pkg": L3C, "rationale": "新视图提交", "summary": "新视图提交", "target_version": h1})
    ctx.check("带新哈希的写请求放行", j.get("ok"), str(j)[:60])

    print("R7 幂等键")
    k = "KEY-R7"
    st, a = ctx.post({"action": "what_if", "pkg": L3C, "idempotency_key": k})
    st, b = ctx.post({"action": "what_if", "pkg": L3C, "idempotency_key": k})
    ctx.check("同 key 同内容 → 幂等重放", a.get("ok") and b.get("idempotent_replay") is True)
    st, c = ctx.post({"action": "validate", "pkg": L4C, "idempotency_key": k})
    ctx.check("同 key 换内容 → 409 拒绝", st == 409 and not c.get("ok"), f"HTTP {st}")

    print("R8 演进状态机（CLI 建候选/评测/晋级，HTTP 回滚）")
    comp = "cmp_regress"

    def new_c(n):
        _, out = ctx.wf("evo-new", "--component", comp, "--change-type", "orchestrator",
                        "--change-set", f"候选{n}", "--scope", "cvfpu", "--by", "agent")
        return out.split()[0]

    def eva(c, verdict, champion=None, challenger=None):
        args = ["evo-eval", "--candidate", c, "--verdict", verdict]
        if champion:
            args += ["--champion", champion]
        if challenger:
            args += ["--challenger", challenger]
        return ctx.wf(*args)

    def promote(c):
        return ctx.wf("evo-promote", "--candidate", c)

    e1 = new_c(1)
    rc, out = promote(e1)
    ctx.check("未评测的候选晋级 → 拒绝", rc != 0 and "不可晋级" in out)
    eva(e1, "reject", '{"p":0.9}', '{"p":0.4}')
    rc, out = promote(e1)
    ctx.check("评测结论 reject 的候选晋级 → 拒绝", rc != 0 and "不可晋级" in out)
    rc, out = eva(e1, "promote", '{"p":0.9}', '{"p":0.99}')
    ctx.check("被否决的候选是终态，不可再评测", rc != 0 and "不可评测" in out)
    st, j = ctx.post({"action": "rollback", "candidate": e1})
    ctx.check("回滚非 Champion → 拒绝（HTTP）", not j.get("ok"))
    rc, out = ctx.wf("evo-rollback", "--candidate", e1)
    ctx.check("回滚非 Champion → 拒绝（CLI）", rc != 0 and "不是当前 champion" in out)
    e3 = new_c(3)
    eva(e3, "promote")
    rc, out = promote(e3)
    ctx.check("verdict=promote 但无指标的候选晋级 → 拒绝", rc != 0 and "没有评测指标" in out)
    rc, out = eva(e3, "keep", "not-json")
    ctx.check("非法 JSON 指标 → 明确报错而非 traceback", rc != 0 and "不是合法 JSON" in out and "Traceback" not in out)
    eva(e3, "promote", '{"pass":0.80}', '{"pass":0.85}')
    rc, _ = promote(e3)
    e = ctx.get("/api/evo-state")
    ctx.check("正常路径：评测 promote（带指标）→ 晋级为 Champion", rc == 0 and e["champions"] == {comp: e3}, str(e.get("champions")))
    rc, out = eva(e3, "reject")
    ctx.check("已晋级的 Champion 不可再评测", rc != 0 and "不可评测" in out)
    e4 = new_c(4)
    eva(e4, "promote", '{"pass":0.85}', '{"pass":0.90}')
    promote(e4)
    e = ctx.get("/api/evo-state")
    evs = {x["candidate_id"]: x for x in e["evolutions"]}
    ctx.check("E4 晋级后取代 E3",
              e["champions"] == {comp: e4} and evs[e3]["status"] == "superseded" and evs[e3]["superseded_by"] == e4)
    ctx.check("晋级谱系边进入证据图", [(x["from"], x["to"]) for x in e["lineage"]] == [(e4, e3)], str(e["lineage"]))
    h0 = ctx.get("/api/state")["store_env_hash"]
    st, j = ctx.post({"action": "rollback", "candidate": e4, "reason": "线上回归", "agent": "tester"})
    e = ctx.get("/api/evo-state")
    evs = {x["candidate_id"]: x for x in e["evolutions"]}
    ctx.check("回滚 E4：E4=rolled_back，旧 Champion E3 恢复",
              j.get("ok") and j["result"]["restored"] == [e3] and evs[e4]["status"] == "rolled_back"
              and e["champions"] == {comp: e3}, f"{j.get('result')} champions={e.get('champions')}")
    ctx.check("回滚谱系边进入证据图", {(x["from"], x["to"]) for x in e["lineage"]} == {(e4, e3), (e3, e4)}, str(e["lineage"]))
    ctx.check("回滚（只改 evolutions/graph）后 store 哈希变化", ctx.get("/api/state")["store_env_hash"] != h0)
    rc, out = ctx.wf("evo-rollback", "--candidate", e4)
    ctx.check("已回滚的候选再回滚 → 拒绝", rc != 0)

    print("R12 导入 benchmark 与立项（标题 + 目标 + benchmark + 流程模板；没有领域、类型、成功判据）")
    st, j = ctx.post({"action": "bench_import", "name": "outside", "paths": ["/etc/hostname"], "agent": "tester"})
    ctx.check("bench_import：仓库外的路径 → 被拒", not j.get("ok"), str(j)[:100])
    suite = ctx.root / "benchsuite" / "cases"
    suite.mkdir(parents=True)
    (suite / "a.json").write_text('{"n": 1}')
    (suite / "b.json").write_text('{"n": 2}')
    kern = ctx.root / "k.S"
    kern.write_text("entry:\n  ret\n")
    st, j = ctx.post({"action": "bench_import", "name": "suite-x", "kind": "题集",
                      "paths": [str(ctx.root / "benchsuite")], "agent": "tester"})
    st2, j2 = ctx.post({"action": "bench_import", "name": "kernel-x", "paths": [str(kern)], "agent": "tester"})
    bm = {b["name"]: b for b in ctx.get("/api/benchmarks")}
    ctx.check("题集目录按清单入册（2 个文件），负载文件哈希入册",
              j.get("ok") and j2.get("ok") and bm["suite-x"]["artifact_refs"][0]["file_count"] == 2
              and bm["kernel-x"]["artifact_refs"][0]["size"] == kern.stat().st_size,
              f"{str(j)[:100]} | {str(j2)[:100]} | {str(bm)[:200]}")
    idx = [json.loads(l) for l in open(ctx.root / "platform/store/index/benchmarks.jsonl", encoding="utf-8") if l.strip()]
    ctx.check("导入结果写进全局清单 benchmarks.jsonl（不再写进领域包）",
              {"suite-x", "kernel-x"} <= {b["name"] for b in idx}
              and [b["name"] for b in json.load(open(ctx.root / "domains/sme2/pack.json"))["benchmarks"]] == ["vp-int4-attention"],
              str([b["name"] for b in idx]))
    base = {"action": "task_new", "title": "回归测试任务", "slug": "regress-flow", "goal": "验证任务入口",
            "benchmarks": ["kernel-x"], "agent": "tester", "create": True}
    st, j = ctx.post({**base, "goal": " "})
    ctx.check("task_new 没有目标 → 被拒", not j.get("ok") and "不能为空" in str(j.get("error")), str(j)[:120])
    st, j = ctx.post({**base, "benchmarks": ["nope"]})
    ctx.check("task_new 的 benchmark 不认识 → 被拒", not j.get("ok") and "不认识" in str(j.get("error")), str(j)[:120])
    st, j = ctx.post({**base, "template": "nope"})
    ctx.check("task_new 的模板不认识 → 被拒", not j.get("ok") and "未知模板" in str(j.get("error")), str(j)[:120])
    st, j = ctx.post(base)
    ctx.check("task_new 成功：任务 draft、章程待批、迭代 1 已按模板建包",
              j.get("ok") and j["result"]["status"] == "draft" and j["result"]["task_id"] == "TASK-regress-flow"
              and j["result"]["flow_template"] == "microarch_optimize" and len(j["result"]["iterations"]) == 1,
              str(j)[:200])
    L1N, L2N = "PKG-L1-general-regress-flow-i1-l1", "PKG-L2-general-regress-flow-i1-l2"
    if j.get("ok"):
        TID = j["result"]["task_id"]
        CH = j["result"]["charter_decision_id"]
        IT1 = j["result"]["iterations"][0]["iteration_id"]
        s = ctx.get("/api/state")
        t = task_of(s, TID)
        nodes = t["iterations"][0]["nodes"]
        ctx.check("新任务进入 state：迭代 1 的八个框各有 1 个自有包，没有沿用",
                  [n["layer"] for n in nodes] == LAYERS and all(len(n["own"]) == 1 and not n["reused"] for n in nodes),
                  str(nodes)[:200])
        ctx.check("新建的包都还没开始",
                  all(n["state"] == "未开始" for n in nodes)
              and pkg_of(s, L1N)["card"]["state"]["why"] == "还没有产出")
        ctx.check("层包一建出来就有所属（task_id / iteration_id）",
                  all(pkg_of(s, n["own"][0])["task_id"] == TID and pkg_of(s, n["own"][0])["iteration_id"] == IT1 for n in nodes))
        rec = json.load(open(ctx.root / "tasks/TASK-regress-flow.json", encoding="utf-8"))
        ctx.check("任务记录放在 tasks/，层包放在默认位置；没有类型、没有领域输入项，目标只有原话",
                  (ctx.root / "domains/general/pack.json").exists() and not (ctx.root / "domains/general/tasks").exists()
                  and "kind" not in rec and "domain" not in rec and rec["goal"] == {"statement": "验证任务入口"},
                  str(rec)[:200])
        ctx.check("章程：任务级的待批回执（谁 · 何时 · 决定了什么）",
                  any(r["pending"] and r["kind"] == "章程" and r["decision_id"] == CH
                      and r["summary"] == "任务章程：回归测试任务" and r["by"] == "待人决定" for r in t["receipts"])
                  and t["status"] == "draft" and t["iterations"][0]["status"] == "draft", str(t["receipts"]))
        ctx.check("目标原话出现在每一层的卡片上",
                  all(pkg_of(s, n["own"][0])["card"]["goal"] == "验证任务入口" for n in nodes))
        # 层 agent 交齐本层交付（payload）；接口卡另外写，留着测强制规则
        def fill_l1(e):
            e["payload"].update({"benchmarks": [{"name": "kernel-x", "kind": "负载"}],
                                 "metric_definitions": [{"name": "cycles", "definition": "窗口周期", "unit": "cycles"}],
                                 "baseline_refs": ["ART-regress-baseline"]})

        def fill_l2(e):
            e["payload"].update({"proposition": {"statement": "解包流占主导", "predicted_direction": "解包周期占比上升",
                                                 "failure_condition": "占比不变", "validity_scope": "回归用"},
                                 "experiment_matrix": [{"id": "E1", "change": "去掉解包"}],
                                 "alternative_explanations": [{"id": "A1", "text": "访存延迟"}],
                                 "attribution_verdict": "model_supported"})

        ctx.edit_env(L1N, fill_l1)
        ctx.edit_env(L2N, fill_l2)
        rc, out = ctx.wf("next")
        ctx.check("wf next 提示章程待批，并列出新建的 L1",
                  rc == 0 and "章程待批" in out and CH in out and L1N in out, out[:300])
        rc, out = ctx.wf("brief", L2N)
        ctx.check("wf brief：下游拿到的是上游的卡片（目标原话、上游结论、留给下游的问题、接口卡要求）",
                  rc == 0 and "验证任务入口" in out and L1N in out and "接口卡" in out, out[:300])
        rc, out = ctx.wf("decide", "--pkg", L1N, "--type", "select", "--rationale", "x", "--confidence", "medium",
                         "--reviewer", "huawei")
        ctx.check("wf decide 缺 --summary → 拒绝", rc != 0 and "--summary" in out, out[:120])
        rc, out = ctx.wf("decide", "--task", TID, "--type", "defer", "--summary", "等 benchmark 校准",
                         "--confidence", "low", "--reviewer", "huawei", "--agent", "tester")
        ctx.check("wf decide --task：不写详细依据也行，决策挂在任务上", rc == 0 and TID in out, out[-120:])
        st, j = ctx.post({"action": "approve", "dec": CH, "as": "评测者C"})
        t_after = task_of(ctx.get("/api/state"), TID)
        ctx.check("批准章程 → 任务转 active，首轮一起 active；回执写明谁批准了章程",
                  j.get("ok") and t_after["status"] == "active" and t_after["iterations"][0]["status"] == "active"
                  and any(r["kind"] == "章程" and r["outcome"] == "批准" and r["by"] == "评测者C" and not r["pending"]
                          for r in t_after["receipts"]), str(t_after["receipts"]))

        print("R11 决定回执：谁 · 何时 · 决定了什么 · 所审内容的指纹")
        st, j = ctx.post({"action": "propose", "pkg": L2N, "rationale": "没有一句话结论"})
        ctx.check("propose 缺 summary → 被拒", not j.get("ok") and "summary" in str(j.get("error")), str(j)[:100])
        st, j = ctx.post({"action": "propose", "pkg": L2N, "summary": "长" * 81, "agent": "tester"})
        ctx.check("summary 超过 80 字 → 被拒", not j.get("ok"), str(j)[:100])
        st, j = ctx.post({"action": "propose", "pkg": L2N, "summary": "采用解包流主导的归因", "agent": "tester"})
        ctx.check("propose 带 summary 成功（详细依据可省），回执记下所审内容指纹",
                  j.get("ok") and len(j["result"].get("content_sha", "")) == 16, str(j)[:120])
        c = ctx.get("/api/package/" + L2N)["card"]
        ctx.check("卡片：待人决定；待批事项列在人的决定里，一句话结论原样显示",
                  c["state"]["word"] == "待人决定"
                  and any(r["pending"] and r["summary"] == "采用解包流主导的归因" and r["by"] == "待人决定"
                          and r["stale"] is False for r in c["receipts"]), str(c["receipts"]))
        ctx.edit_env(L2N, lambda e: e.__setitem__("interface", {"question": "慢在哪", "gives": [
            {"id": "S1", "text": "提请之后改过的一版", "truth": "deduced", "evidence": []}]}))
        c = ctx.get("/api/package/" + L2N)["card"]
        ctx.check("提请之后内容改过 → 待批回执标为过期（批之前就知道内容变了）",
                  next(r for r in c["receipts"] if r["pending"])["stale"] is True)
        st, j = ctx.post({"action": "approve", "dec": next(r["decision_id"] for r in c["receipts"] if r["pending"]),
                          "as": "评测者D"})
        c = ctx.get("/api/package/" + L2N)["card"]
        ctx.check("批准记录带同一指纹 → 仍然是过期回执；L2 需要人签 → 核对时要求重签",
                  j.get("ok") and next(r for r in c["receipts"] if not r["pending"])["stale"] is True
                  and any(r["outcome"] == "批准" for r in c["receipts"]), str(c["receipts"]))
        st, j = ctx.post({"action": "validate", "pkg": L2N})
        ctx.check("核对：本层交付没有 BLOCK（此时只被尚未验证的 L1 拦着），原因里写明需要重签",
                  all(b.startswith("upstream:") for b in j["result"]["report"]["blocks"])
                  and any("需要重签" in w_ for w_ in j["result"]["report"]["warns"]),
                  str(j.get("result", {}).get("report"))[:300])
        c = ctx.get("/api/package/" + L2N)["card"]
        ctx.check("卡片：签字一行 ✗「签字之后内容改过，需要重签」",
              [x["detail"] for x in c["checks"] if x["id"] == "human"] == ["签字之后内容改过，需要重签"], str(c["checks"]))
        st, j = ctx.post({"action": "decide", "pkg": L2N, "summary": "按改后的归因确认"})
        ctx.check("decide 缺署名 → 被拒", not j.get("ok") and "署名" in str(j.get("error")), str(j)[:100])
        st, j = ctx.post({"action": "decide", "pkg": L2N, "summary": "x", "as": "pending-user"})
        ctx.check("decide 署名=pending-user → 被拒", not j.get("ok"), str(j)[:100])
        st, j = ctx.post({"action": "decide", "pkg": L2N, "as": "huawei"})
        ctx.check("decide 缺一句话结论 → 被拒", not j.get("ok") and "summary" in str(j.get("error")), str(j)[:100])
        st, j = ctx.post({"action": "decide", "pkg": L2N, "summary": "按改后的归因确认", "as": "huawei", "agent": "tester"})
        c = ctx.get("/api/package/" + L2N)["card"]
        r_ = next(r for r in c["receipts"] if r["decision_id"] == j["result"]["decision_id"])
        ctx.check("decide 成功：回执 = huawei · 时间 · 决定了什么；内容未再改过 → 不过期",
                  j.get("ok") and j["result"]["signed_by"] == "huawei" and r_["by"] == "huawei"
                  and r_["summary"] == "按改后的归因确认" and r_["at"][:10] and r_["stale"] is False, str(r_))
        ctx.check("卡片：签字一行 ✓，写明谁 · 何时",
              [x["mark"] for x in c["checks"] if x["id"] == "human"] == ["✓"]
              and "huawei" in next(x["detail"] for x in c["checks"] if x["id"] == "human"), str(c["checks"]))

        print("R13 接口卡三条强制规则（新建任务下的层包）：实测要有登记测试、上游先验证、长度与引用")

        def iface(text="基线窗口 100 拍（两次一致）", truth="observed", evidence=(), takes=()):
            d = {"question": "在什么负载上、量什么、基线是多少",
                 "gives": [{"id": "S1", "text": text, "truth": truth, "evidence": list(evidence)}],
                 "limits": ["仅回归用途"]}
            if takes:
                d["takes"] = [{"from": f, "statement": "S1"} for f in takes]
            return d

        def set_iface(pkg, d):
            ctx.edit_env(pkg, lambda e: e.__setitem__("interface", d))

        def blocks(pkg):
            _, j = ctx.post({"action": "validate", "pkg": pkg})
            return j.get("result", {}).get("verdict"), [b for b in j.get("result", {}).get("report", {}).get("blocks", [])
                                                        if b.startswith("interface:") or b.startswith("upstream:")]

        v, b = blocks(L1N)                 # 交付已填齐：拦下来的只可能是接口卡
        ctx.check("不带接口卡 → 不通过，写明缺 interface", v == "fail" and any("缺 interface" in x for x in b), str(b))
        set_iface(L1N, iface())
        v, b = blocks(L1N)
        ctx.check("标了实测、却没有登记在册的测试结果 → 不通过", v == "fail" and any("标了实测" in x for x in b), str(b))
        set_iface(L1N, iface(evidence=["RES-does-not-exist"]))
        v, b = blocks(L1N)
        ctx.check("证据指向的结果不在登记册 → 不通过", v == "fail" and any("不在登记册" in x for x in b), str(b))
        set_iface(L1N, iface(text="长" * 141, truth="deduced"))
        v, b = blocks(L1N)
        ctx.check("结论超过 140 字 → 不通过", v == "fail" and any("超过 140 字" in x for x in b), str(b))
        set_iface(L1N, iface(truth="deduced", takes=["PKG-L9-nowhere"]))
        v, b = blocks(L1N)
        ctx.check("说用了某条上游结论，但那不是本包的上游 → 不通过", v == "fail" and any("不是本包的上游" in x for x in b), str(b))
        (ctx.root / "def.json").write_text(json.dumps({
            "test_id": "TST-regress-l1-baseline", "schema_version": "2.0", "title": "回归：L1 基线可复现",
            "layer_id": "L1", "proposition": "窗口周期可复现", "metric": "cycles",
            "predicted_direction": "两次运行一致", "failure_condition": "两次运行不一致",
            "validity_scope": "回归用", "strength": "V1"}))
        (ctx.root / "run-stats.txt").write_text("seed 1 cycles 100\nseed 2 cycles 100\n")
        rc, out = ctx.wf("test-register", "--pkg", L1N, "--file", str(ctx.root / "def.json"),
                         "--run-artifact", str(ctx.root / "run-stats.txt"), "--verdict", "pass")
        RES = (re.search(r"RES-[A-Za-z0-9-]+", out) or [None])[0]
        ctx.check("登记一条通过的测试", rc == 0 and bool(RES), out[-160:])
        set_iface(L1N, iface(evidence=[RES]))
        v, b = blocks(L1N)
        ctx.check("实测结论指向登记测试后 → 只剩等人签字", v == "needs_decision", f"{v} {b}")
        c = ctx.get("/api/package/" + L1N)["card"]
        ctx.check("证据清单：测试名、结论、强度、复现所用文件",
                  any(e_["kind"] == "test" and e_["verdict"] == "pass" and e_["strength"] == "自测"
                      and e_["for_statement"] == ["S1"] for e_ in c["evidence"]), str(c["evidence"])[:200])
        set_iface(L2N, {"question": "慢在哪、凭什么这么说", "takes": [{"from": L1N, "statement": "S1"}],
                        "gives": [{"id": "S1", "text": "解包流占主导", "truth": "deduced", "evidence": []}]})
        v, b = blocks(L2N)
        ctx.check("上游没验证完，下游不能通过", any("上游没验证完，下游不能通过" in x for x in b), str(b))
        c = ctx.get("/api/package/" + L2N)["card"]
        ctx.check("L2 卡片：上游一行 ✗，L1 是待人决定；回溯写明用了 L1 的 S1",
              [x["mark"] for x in c["checks"] if x["id"] == "upstream"] == ["✗"]
              and c["parents"] == [L1N] and c["interface"]["takes"][0]["from"] == L1N, str(c["checks"]))
        st, j = ctx.post({"action": "decide", "pkg": L1N, "summary": "基线按回归口径拍定", "as": "huawei", "agent": "tester"})
        ctx.check("给 L1 记一个署名的决定", j.get("ok") and j["result"]["signed_by"] == "huawei", str(j)[:120])
        v, b = blocks(L1N)
        ctx.check("L1 核对通过，卡片是「已验证」",
              v == "pass" and pkg_of(ctx.get("/api/state"), L1N)["state"] == "已验证", f"{v} {b}")
        v, b = blocks(L2N)
        ctx.check("L2 不再被上游拦下（轮到它自己的人签字）", not any("上游没验证完" in x for x in b) and v == "needs_decision", f"{v} {b}")
        # 核对通过之后上游改了接口卡
        set_iface(L1N, iface(evidence=[RES], takes=()) | {"limits": ["仅回归用途", "核对之后改过的一行"]})
        s = ctx.get("/api/state")
        ctx.check("上游核对之后改了接口卡 → 状态回到「未验证」",
                  pkg_of(s, L1N)["state"] == "未验证"
                  and pkg_of(s, L1N)["card"]["state"]["why"] == "接口卡在上次核对之后改过，需要重新核对")
        v, b = blocks(L2N)
        ctx.check("下游随之不能通过：上游核对之后接口卡改过", any("核对之后接口卡改过" in x for x in b), str(b))
        c = ctx.get("/api/package/" + L2N)["card"]
        ctx.check("L2 卡片：上游一行写明 L1「核对后改过」",
              "核对后改过" in next(x["detail"] for x in c["checks"] if x["id"] == "upstream"), str(c["checks"]))
        v, b = blocks(L1N)
        ctx.check("L1 重新核对：签字是在改动之前签的 → 需要重签",
                  v == "needs_decision" and pkg_of(ctx.get("/api/state"), L1N)["state"] == "待人决定", str(v))
        st, j = ctx.post({"action": "decide", "pkg": L1N, "summary": "确认改动后的基线口径", "as": "huawei", "agent": "tester"})
        v, b = blocks(L1N)
        ctx.check("重签并重新核对 → L1 回到「已验证」", v == "pass" and pkg_of(ctx.get("/api/state"), L1N)["state"] == "已验证")

        rc, out = ctx.wf("iter-new", "--task", TID, "--reason", "换模板重开", "--create", "--as", "评测者C")
        ctx.check("上一轮还在进行中 → 不能开下一轮", rc != 0 and "先结束它" in out, out[:120])
        rc, out = ctx.wf("iter-seal", TID + "/1", "--as", "评测者C", "--reason", "回归：结束首轮")
        ctx.check("结束首轮（署名留痕）", rc == 0, out[-120:])
        st, j = ctx.post({"action": "iter_new", "task": TID, "reason": "回归：开第二轮", "create": True,
                          "as": "评测者C", "agent": "tester"})
        t2 = task_of(ctx.get("/api/state"), TID)
        ctx.check("iter_new（HTTP）开第二轮并生成层包",
                  j.get("ok") and j["result"]["label"] == "迭代 2"
                  and [i["status"] for i in t2["iterations"]] == ["sealed", "active"]
                  and [n["layer"] for n in t2["iterations"][1]["nodes"]] == LAYERS
                  and all(len(n["own"]) == 1 for n in t2["iterations"][1]["nodes"]),
                  f"{str(j)[:160]} {[i['status'] for i in t2['iterations']]}")
        ctx.check("任务回执：按时间排列，写明是哪一类、谁签的（章程批准、任务上的一个决定、结束迭代 1、开迭代 2）",
                  [r["kind"] for r in t2["receipts"] if not r["pending"]] == ["", "章程", "结束迭代 1", "开迭代 2"]
                  and [r["by"] for r in t2["receipts"] if not r["pending"]] == ["huawei", "评测者C", "评测者C", "评测者C"]
                  and t2["receipts"][1]["outcome"] == "批准",
                  str([(r["kind"], r["by"], r["outcome"] if "outcome" in r else "") for r in t2["receipts"]]))
        envp = glob.glob(str(ctx.root / "domains/general/L8_analysis/PKG-L8-general-regress-flow-i2-l8/envelope.json"))[0]
        env = json.load(open(envp))
        env["lifecycle"]["status"] = "passed"
        json.dump(env, open(envp, "w"))
        rc, out = ctx.wf("next")
        ctx.check("迭代到了 L8 → wf next 提示做 Mission Review", rc == 0 and "Mission Review" in out, out[-260:])
        st, j = ctx.post({"action": "task_seal", "task": TID, "reason": "回归：封板", "agent": "tester"})
        ctx.check("封板缺署名 → 被拒", not j.get("ok") and "署名" in str(j.get("error")), str(j)[:100])
        st, j = ctx.post({"action": "task_seal", "task": TID, "reason": "回归：封板", "as": "评测者C", "agent": "tester"})
        s3 = ctx.get("/api/state")
        t3 = task_of(s3, TID)
        # 折叠数 = 历史封板的 22 个（sme2 19 + cvfpu 试建 3）+ 新任务两轮各 8 个
        ctx.check("封板后：任务与两轮都 sealed，两轮共 16 个包全部折叠（38 = 22 + 16）；默认迭代回到 cvfpu；回执里有「封板」",
                  j.get("ok") and t3["status"] == "sealed" and [i["status"] for i in t3["iterations"]] == ["sealed", "sealed"]
                  and s3["summary"]["hidden_packages"] == 22 + 16
                  and s3["summary"]["active_iteration"] == "ITER-cvfpu-overflow-1"
                  and any(r["kind"] == "封板" and r["by"] == "评测者C" and "回归：封板" in r["summary"]
                          for r in t3["receipts"]),
                  f"{s3['summary']} {[i['status'] for i in t3['iterations']]}")

    print("R15 仅 benchmark 的模板、给没开始的框补建层包、结束与封板（HTTP）")
    st, j = ctx.post({"action": "task_new", "title": "只看基准", "slug": "bench-only", "goal": "只看 benchmark 结果",
                      "benchmarks": ["suite-x"], "template": "benchmark_only", "create": True,
                      "as": "评测者C", "agent": "tester"})
    ctx.check("benchmark_only 立项：章程已签、任务 active、没有迭代",
              j.get("ok") and j["result"]["status"] == "active" and j["result"]["iterations"] == []
              and j["result"]["flow_template"] == "benchmark_only", str(j)[:200])
    st, j = ctx.post({"action": "iter_new", "task": "TASK-bench-only", "reason": "再跑一轮"})
    ctx.check("不走层的任务没有下一轮 → 被拒", not j.get("ok") and "不走层" in str(j.get("error")), str(j)[:120])
    tb = task_of(ctx.get("/api/state"), "TASK-bench-only")
    ctx.check("没有生成层包；任务卡上只有章程回执",
              not (ctx.root / "domains/general/L1_observation/PKG-L1-general-bench-only-i1-l1").exists()
              and tb["iterations"] == [] and [r["kind"] for r in tb["receipts"]] == ["章程"]
              and tb["receipts"][0]["by"] == "评测者C", str(tb["receipts"]))
    st, j = ctx.post({"action": "task_new", "title": "逐层补建", "slug": "step-by-step", "goal": "验证补建",
                      "benchmarks": ["kernel-x"], "create": False, "as": "评测者C", "agent": "tester"})
    TN, ITN = "TASK-step-by-step", "ITER-step-by-step-1"
    ctx.check("不预建层包的任务：迭代 1 的八个框都是未开始", j.get("ok")
              and all(n["state"] == "未开始" and n["own"] == [] for n in task_of(ctx.get("/api/state"), TN)["iterations"][0]["nodes"]))
    st, j = ctx.post({"action": "new_pkg", "iteration": ITN, "layer": "L3"})
    PN = "PKG-L3-general-step-by-step-i1-l3"
    ctx.check("new_pkg 给 L3 这个框建出层包，归到该迭代", j.get("ok") and j["result"]["package_id"] == PN
              and ctx.env(PN)["provenance"].get("task_id") == TN and ctx.env(PN)["provenance"].get("iteration_id") == ITN,
              str(j)[:160])
    n3 = next(n for n in task_of(ctx.get("/api/state"), TN)["iterations"][0]["nodes"] if n["layer"] == "L3")
    ctx.check("流程里 L3 这个框现在列出了新建的包", n3["own"] == [PN], str(n3))
    st, j = ctx.post({"action": "new_pkg", "iteration": ITN, "layer": "L3"})
    ctx.check("同一层再建一次 → 被拒", not j.get("ok"), str(j)[:100])
    st, j = ctx.post({"action": "new_pkg", "iteration": ITN, "layer": "L9"})
    ctx.check("层号不合法 → 被拒", not j.get("ok"))
    st, j = ctx.post({"action": "iter_seal", "iteration": ITN, "reason": "回归：结束"})
    ctx.check("结束迭代缺署名 → 被拒", not j.get("ok") and "署名" in str(j.get("error")), str(j)[:100])
    st, j = ctx.post({"action": "iter_seal", "iteration": TN + "/1", "reason": "回归：结束", "as": "评测者C"})
    tn = task_of(ctx.get("/api/state"), TN)
    ctx.check("结束一轮（TASK/<序号> 也可以）：迭代 sealed，回执「结束迭代 1」",
              j.get("ok") and tn["iterations"][0]["status"] == "sealed"
              and any(r["kind"] == "结束迭代 1" and r["by"] == "评测者C" for r in tn["receipts"]), str(tn["receipts"]))
    st, j = ctx.post({"action": "iter_abort", "iteration": ITN, "reason": "x", "as": "评测者C"})
    ctx.check("已结束的迭代不能再终止 → 被拒", not j.get("ok"))
    st, j = ctx.post({"action": "task_seal", "task": TN, "reason": "回归：封板", "as": "评测者C"})
    tn = task_of(ctx.get("/api/state"), TN)
    ctx.check("封板：任务 sealed，回执「封板」",
              j.get("ok") and tn["status"] == "sealed" and any(r["kind"] == "封板" for r in tn["receipts"]), str(tn["receipts"]))
    rc, out = ctx.wf("state-export")
    ctx.check("以上操作之后 state-export 仍通过机器契约自校验", rc == 0, out[-200:])

    print("R16 层 agent 定义：guide / 开工简报留档 / 回读留痕 / 执行记录 / 层 agent 不能替人签字 / 宿主投影")
    g = ctx.get("/api/guide")
    ags = [l.get("agent") or {} for l in g["layers"]]
    ctx.check("/api/guide：八层各带 agent 定义与正文",
              len(ags) == 8 and all(a.get("name") and a.get("host_name") and a.get("body") for a in ags),
              str([(a.get("name"), len(a.get("body") or "")) for a in ags])[:200])
    orc = g.get("orchestrator") or {}
    ctx.check("/api/guide：调度 agent 也在，且正文写明不做层内工作",
              bool(orc.get("name")) and "不做任何一层的层内工作" in (orc.get("body") or ""), str(orc.get("name")))
    l5 = next(l for l in g["layers"] if l["layer"] == "L5")
    ctx.check("L5 挂上了 rtl 判例 skill 与 caselaw 工具包（此前漏挂）",
              "rtl-generation-discipline" in [x["name"] for x in l5["agent"]["skills"]]
              and "caselaw" in [x["name"] for x in l5["agent"]["tools"]], str(l5["agent"]["skills"]))
    ctx.check("每层定义都写清 skills / tools / 可写范围 / 不许做的事",
              all(a.get("skills") and a.get("tools") and a.get("writes") and a.get("must_not") for a in ags + [orc]))
    ctx.check("skills 与工具包都有一句用途（界面上的悬浮提示）",
              all(x.get("what") for a in ags + [orc] for x in a.get("skills", []) + a.get("tools", [])))
    zc = sorted(p.name for p in (REPO / ".zcode" / "agents").glob("*.md"))
    ctx.check("宿主投影：.zcode/agents 下九份定义", len(zc) == 9 and "sim-orchestrator.md" in zc, str(zc))
    orc_txt = (REPO / ".zcode" / "agents" / "sim-orchestrator.md").read_text(encoding="utf-8")
    ctx.check("调度 agent 的宿主定义不给写文件能力（tools 里没有 Write/Edit）",
              "tools: Read, Bash, Glob, Grep" in orc_txt and "Write" not in orc_txt.split("---")[1])
    pr = subprocess.run([PY, str(REPO / "platform" / "tools" / "bind_hosts.py"), "--check"],
                        capture_output=True, text=True, cwd=REPO, timeout=120)
    ctx.check("投影与源一致（bind_hosts --check）", pr.returncode == 0, (pr.stdout + pr.stderr)[-160:])

    if j.get("ok"):
        st, jb = ctx.post({"action": "brief_out", "pkg": L1N, "by": "回归员"})
        bmd = ctx.root / "domains" / "general" / "L1_observation" / L1N / "brief.md"
        env_b = ctx.env(L1N)
        rec = (env_b.get("provenance") or {}).get("brief") or {}
        ctx.check("开工简报留档：brief.md 落盘，回执记时间 / 署名 / 指纹 / 上游",
                  jb.get("ok") and bmd.exists()
                  and rec.get("by") == "回归员" and len(rec.get("sha256", "")) == 16
                  and hashlib.sha256(bmd.read_bytes()).hexdigest()[:16] == rec.get("sha256")
                  and rec.get("upstream") == [], str(jb)[:160])
        c1 = ctx.get("/api/package/" + L1N)["card"]
        ctx.check("卡片带 context.brief（界面上的「开工简报」一行）",
                  (c1["context"]["brief"] or {}).get("by") == "回归员")
        rc, out = ctx.wf("show", L1N, "--for", L2N, "--by", "回归员")
        c2 = ctx.get("/api/package/" + L2N)["card"]
        ctx.check("接口卡之外另读上游：show --for 留痕，卡片上看得到",
                  rc == 0 and len(c2["context"]["pulls"]) == 1
                  and c2["context"]["pulls"][0]["pkg"] == L1N
                  and c2["context"]["pulls"][0]["by"] == "回归员", out[-120:])
        rc, out = ctx.wf("run-log", "--pkg", L2N, "--agent", "回归员", "--model", "glm-5.4", "--duration", "42")
        c2 = ctx.get("/api/package/" + L2N)["card"]
        r0 = (c2["runs"] or [{}])[-1]
        ctx.check("执行记录：谁 / 模型 / 耗时，进卡片",
                  rc == 0 and r0.get("agent") == "回归员" and r0.get("model") == "glm-5.4"
                  and r0.get("duration_s") == 42, out[-120:])

        rc, out = ctx.wf("approve", "DEC-nope", "--as", "x", extra_env={"WF_ACTOR": "layer:L2"})
        ctx.check("层 agent 不能批准（WF_ACTOR=layer:*）", rc != 0 and "人的动作" in out, out[:120])
        rc, out = ctx.wf("reject", "DEC-nope", "--as", "x", "--reason", "x", extra_env={"WF_ACTOR": "layer:L2"})
        ctx.check("层 agent 不能驳回", rc != 0 and "人的动作" in out, out[:120])
        rc, out = ctx.wf("invalidate", L1N, "--reason", "x", extra_env={"WF_ACTOR": "layer:L2"})
        ctx.check("层 agent 不能作废", rc != 0 and "人的动作" in out, out[:120])
        rc, out = ctx.wf("gate", L1N, "--override", "--as", "x", "--reason", "x",
                         extra_env={"WF_ACTOR": "layer:L2"})
        ctx.check("层 agent 不能署名覆盖", rc != 0 and "人的动作" in out, out[:120])
        rc, out = ctx.wf("decide", "--pkg", L2N, "--type", "select", "--summary", "层 agent 记一条",
                         "--confidence", "high", "--reviewer", "huawei", extra_env={"WF_ACTOR": "layer:L2"})
        decs = [json.loads(l) for l in (ctx.root / "platform/store/index/decisions.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        made = [d for d in decs if d.get("summary") == "层 agent 记一条"]
        ctx.check("层 agent 记决定被强制成待批（reviewer 不能填真人名）",
                  rc == 0 and made and made[-1]["reviewer"] == "pending-user", str(made[-1:])[:120])

        sv = ctx.get("/api/state")
        nodes = [n for t in sv["tasks"] for i in t["iterations"] for n in i["nodes"]]
        byname = {l["layer"]: l["agent"]["name"] for l in g["layers"]}
        ctx.check("状态导出 0.5：每个框带 agent，且与定义一致",
                  sv["schema_version"] == "0.5"
                  and all(n.get("agent") == byname[n["layer"]] for n in nodes), str(nodes[:1]))

        rc, out = ctx.wf("task-new", "--title", "过程目标测试", "--slug", "process-goal",
                         "--goal", "用完整 L1-L8 纪律修复两个 bug，每层建包走 gate", "--benchmark", "kernel-x")
        ctx.check("目标写成过程要求：照建，但给出提醒（不拦截）",
                  rc == 0 and "过程要求" in out and "每层" in out, out[:200])
        st, jw = ctx.post({"action": "task_new", "title": "过程目标 HTTP", "slug": "process-goal-http",
                           "goal": "按流程模板每层建包走 gate", "benchmarks": ["kernel-x"], "create": False})
        ctx.check("同一个提醒经 HTTP 也带回界面（warnings）",
                  jw.get("ok") and any("过程要求" in x for x in (jw.get("result") or {}).get("warnings", [])),
                  str(jw.get("result"))[:200])
        rc, out = ctx.wf("task-new", "--title", "具体目标测试", "--slug", "concrete-goal",
                         "--goal", "读请求在缺页时返回旧数据（应返回 404）", "--benchmark", "kernel-x")
        ctx.check("具体问题的目标不触发提醒", rc == 0 and "过程要求" not in out, out[:160])
        cvt = next(t for t in ctx.get("/api/state")["tasks"] if t["task_id"] == "TASK-cvfpu-overflow")
        ctx.check("cvfpu 任务的目标是具体问题，不是过程要求",
                  "溢出" in cvt["goal"]["statement"]
                  and not any(x in cvt["goal"]["statement"] for x in ("L1-L8", "每层", "gate")),
                  cvt["goal"]["statement"])

        bad = ctx.root / "platform" / "agents" / "L5_rtl" / "agent.md"
        orig = bad.read_text(encoding="utf-8")
        bad.write_text(orig.replace('"caselaw"', '"caselaw-nope"'), encoding="utf-8")
        rc, out = ctx.wf("guide")
        bad.write_text(orig, encoding="utf-8")
        ctx.check("定义里引用了不存在的工具包 → 直接报错，不静默降级",
                  rc != 0 and "caselaw-nope" in out and "工具包清单" in out, out[:160])
        rc, out = ctx.wf("guide")
        ctx.check("恢复定义后 guide 正常", rc == 0, out[:80])

    print("R17 归档：只保留 cvfpu，其余不在导出与界面呈现（store 里的记录保留）")
    sb = ctx.get("/api/state")
    before_tasks = {t["task_id"] for t in sb["tasks"]}
    before_pkgs, before_pending = sb["summary"]["total_packages"], sb["summary"]["pending_decisions"]
    before_hidden = sb["summary"]["hidden_packages"]
    ctx.wf("task-archive", "--task", "TASK-sme2-flow1", "--reason", "回归：只保留 cvfpu")
    ctx.wf("task-archive", "--task", "TASK-cvfpu-bugfix-demo", "--reason", "回归：只保留 cvfpu")
    sa = ctx.get("/api/state")
    after_tasks = {t["task_id"] for t in sa["tasks"]}
    ctx.check("归档后导出里少了这两个任务，其余都在",
              after_tasks == before_tasks - {"TASK-sme2-flow1", "TASK-cvfpu-bugfix-demo"},
              str(sorted(after_tasks)))
    ctx.check("归档任务的 22 个层包一并从导出里消失",
              sa["summary"]["total_packages"] == before_pkgs - 22
              and not any(p["package_id"] in ("PKG-L2-sme2-bt-dequant-attribution", "PKG-L1-cvfpu-anchor")
                          for g in sa["layers"].values() for p in g["packages"]),
              f"{before_pkgs} → {sa['summary']['total_packages']}")
    ctx.check("归档任务上的待批事项不进队列（仍是可批的，只是不展示）",
              sa["summary"]["pending_decisions"] < before_pending
              and not any("sme2" in p for d in sa["pending_decisions"] for p in d["package_ids"]),
              f"{before_pending} → {sa['summary']['pending_decisions']}")
    ctx.check("折叠数不再算归档的包", sa["summary"]["hidden_packages"] == before_hidden - 22,
              f"{before_hidden} → {sa['summary']['hidden_packages']}")
    rc, out = ctx.wf("task-list")
    ctx.check("wf task-list 默认不列归档任务，但提示有几个",
              rc == 0 and "已归档" in out and "TASK-sme2-flow1  [" not in out, out[:200])
    rc, out = ctx.wf("task-list", "--all")
    ctx.check("wf task-list --all 列出归档任务", rc == 0 and "TASK-sme2-flow1" in out, out[:120])
    rc, out = ctx.wf("next")
    ctx.check("wf next 不再提归档任务上的待批",
              rc == 0 and "DEC-20261117-092747" not in out and "DEC-20261117-093133" not in out, out[-200:])
    ctx.check("归档任务的层包仍可按包号查（审计）",
              ctx.get("/api/package/PKG-L2-sme2-bt-dequant-attribution")["card"]["package_id"]
              == "PKG-L2-sme2-bt-dequant-attribution")
    rc, out = ctx.wf("iter-new", "--task", "TASK-sme2-flow1", "--reason", "x")
    ctx.check("归档任务不能再开迭代", rc != 0 and "已归档" in out, out[:120])
    ctx.check("归档写进了任务记录（archived / 时间 / 原因）",
              (lambda t: t.get("archived") is True and bool(t.get("archived_at")) and "只保留 cvfpu" in (t.get("archived_reason") or ""))(
                  json.load(open(ctx.root / "tasks/TASK-sme2-flow1.json", encoding="utf-8"))))
    ctx.wf("task-archive", "--task", "TASK-sme2-flow1", "--restore")
    ctx.wf("task-archive", "--task", "TASK-cvfpu-bugfix-demo", "--restore")
    sr = ctx.get("/api/state")
    ctx.check("恢复后回到归档前的任务数 / 包数 / 待批数",
              {t["task_id"] for t in sr["tasks"]} == before_tasks
              and sr["summary"]["total_packages"] == before_pkgs
              and sr["summary"]["pending_decisions"] == before_pending)

    print("R9 引擎自检")
    rc, out = ctx.wf("selftest")
    ctx.check("wf selftest 全部符合预期", rc == 0 and "15/15" in out, out.splitlines()[-1][:60] if out else "无输出")


def main():
    before = store_fingerprint()
    sandbox = Path(tempfile.mkdtemp(prefix="viz-regress-"))
    proc = None
    try:
        build_sandbox(sandbox)
        port = free_port()
        # 双保险：环境里显式给出 WF_ROOT，且启动前先清掉可能继承来的同名变量
        env = {k: v for k, v in os.environ.items() if k != "WF_ROOT"}
        env["WF_ROOT"] = str(sandbox)
        proc = subprocess.Popen(
            [PY, str(sandbox / "platform" / "viz" / "server.py"), "--port", str(port)],
            cwd=sandbox, env=env, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ctx = Ctx(sandbox, port)
        state = None
        for _ in range(60):
            state = ctx.get("/api/state")
            if isinstance(state, dict) and "summary" in state:
                break
            time.sleep(0.25)
        if not isinstance(state, dict) or "summary" not in state:
            print("沙箱服务未能启动，回归中止（未执行任何写用例）")
            return 2
        # 写用例开跑前的硬性前提：服务端连的必须是沙箱
        if state.get("store_root") != str(sandbox) or state.get("store_isolated") is not True:
            print(f"服务端报告的 store 不是沙箱（store_root={state.get('store_root')!r}），回归中止")
            return 2
        print(f"沙箱：{sandbox}  端口：{port}\n")
        run(ctx)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(sandbox, ignore_errors=True)

    print(f"\n回归结果：{ctx.passed} 通过 / {ctx.failed} 失败")
    untouched = store_fingerprint() == before
    print(f"真实 store 指数{'未变' if untouched else '已变化！'}")
    return 0 if ctx.failed == 0 and untouched else 1


if __name__ == "__main__":
    sys.exit(main())
