#!/usr/bin/env bash
# 命令行冒烟：层接口卡与去领域的命令行路径，在一份隔离副本上跑（ WF_ROOT 指向 /tmp 下的新副本，不碰真实 store）。
# 与 platform/viz/regress.py 互补：那里走 HTTP 面，这里走 wf 命令行本身（card / guide / brief / bench-import / decide …）。
# 任何意外的非零退出都让整轮 FAIL；本应被拒的调用用 expect_reject 声明（并核对拒绝的理由）。
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
SBX=$(mktemp -d /tmp/wf-cli-sbx-XXXXXX)
mkdir -p "$SBX/platform"
for n in tools schemas store agents; do cp -r "$REPO/platform/$n" "$SBX/platform/$n"; done
cp -r "$REPO/.agents" "$SBX/.agents"
cp -r "$REPO/domains" "$SBX/domains"
cp -r "$REPO/tasks" "$SBX/tasks"
find "$SBX" -name __pycache__ -type d -prune -exec rm -rf {} +
mkdir -p "$SBX/suite/cases/a" "$SBX/suite/cases/b"
printf '{"name":"a"}\n' > "$SBX/suite/cases/a/task.json"
printf '{"name":"b"}\n' > "$SBX/suite/cases/b/task.json"
printf 'entry:\n  mov x0, #1\n  ret\n' > "$SBX/k.S"
printf 'seed 1 cycles 100 guard 0\nseed 2 cycles 101 guard 0\n' > "$SBX/run-stats.txt"
cat > "$SBX/def.json" <<'EOF'
{"test_id": "TST-smoke-l1-baseline", "schema_version": "2.0", "title": "冒烟：L1 基线可复现",
 "layer_id": "L1", "proposition": "窗口周期可复现", "metric": "cycles", "predicted_direction": "两次运行一致",
 "failure_condition": "两次运行不一致", "validity_scope": "冒烟用", "strength": "V1"}
EOF
: > "$SBX/.fail"
WFR() { WF_ROOT="$SBX" python3 "$SBX/platform/tools/wf.py" --root "$SBX" "$@"; }
step() { echo; echo "### $*"; }
WF() {  # WF <tail-lines> <args…>: prints the last N lines; a non-zero exit is recorded in the marker file
  local n="$1"; shift
  local out rc
  out=$(WFR "$@" 2>&1); rc=$?
  echo "$out" | tail -n "$n" | cut -c1-230
  if [ "$rc" -ne 0 ]; then echo "unexpected failure (exit $rc): wf $*" >> "$SBX/.fail"; fi
  return 0
}
expect_reject() {  # $1 = label, $2 = expected substring, rest = command
  local label="$1" want="$2"; shift 2
  local out rc
  out=$("$@" 2>&1); rc=$?
  if [ "$rc" -eq 0 ]; then echo "  ✗ $label：本应被拒却成功了"; echo "reject missed: $label" >> "$SBX/.fail"
  elif [ -n "$want" ] && ! echo "$out" | grep -q -F "$want"; then echo "  ✗ $label：被拒了，但原因不是预期的「$want」"; echo "$out" | tail -n 2 | cut -c1-200; echo "wrong reason: $label" >> "$SBX/.fail"
  else echo "  ✓ $label 被拒：$(echo "$out" | tail -1 | cut -c1-150)"; fi
}
WFANY() {  # 同 WF，但不把非零退出当意外（「等人签字」「未验证」本来就会返回非零）
  local n="$1"; shift
  local out
  out=$(WFR "$@" 2>&1)
  echo "$out" | tail -n "$n" | cut -c1-230
  return 0
}
set_payload() {  # $1 = package id：给 L1 填上最小交付（负载 / 指标定义 / 基线）
  python3 -I - "$SBX" "$1" <<'EOF'
import json, sys, glob
sbx, pkg = sys.argv[1], sys.argv[2]
p = glob.glob(sbx + '/domains/*/*/' + pkg + '/envelope.json')[0]
e = json.load(open(p, encoding='utf-8'))
e['payload'].update({"benchmarks": [{"name": "test-kernel", "kind": "冒烟"}],
                     "metric_definitions": [{"name": "cycles", "definition": "窗口周期", "unit": "cycles"}],
                     "baseline_refs": ["ART-smoke-baseline"]})
json.dump(e, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
EOF
}
set_iface() {  # $1 = package id, $2 = python dict literal for interface (written into the sandbox envelope)
  python3 -I - "$SBX" "$1" "$2" <<'EOF'
import json, sys, glob
sbx, pkg, iface = sys.argv[1], sys.argv[2], sys.argv[3]
p = glob.glob(sbx + '/domains/*/*/' + pkg + '/envelope.json')[0]
e = json.load(open(p, encoding='utf-8')); e['interface'] = eval(iface)
json.dump(e, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
EOF
}

step "0. guide"
WF 400 guide | grep -c -E '^L[1-8] ' | sed 's/^/  layers documented: /'
WF 400 guide | grep -q '"gives"' && echo "  ✓ 接口卡骨架在 wf guide 里"

step "1. bench-import（全局清单，不再有领域）"
WF 1 bench-import --name test-kernel --kind "测试内核" "$SBX/k.S"
WF 1 bench-import --name test-suite --kind "题集" "$SBX/suite"
WF 3 bench-import --name test-kernel "$SBX/k.S" | grep -q '（内容已登记）' && echo "  ✓ 重复导入幂等"
expect_reject "路径不存在" "" env WF_ROOT="$SBX" python3 "$SBX/platform/tools/wf.py" --root "$SBX" bench-import --name nope "$SBX/does-not-exist"

step "2. task-new：没有目标 / benchmark / 模板不合法 → 拒绝"
expect_reject "没有 --goal" "" env WF_ROOT="$SBX" python3 "$SBX/platform/tools/wf.py" --root "$SBX" task-new --title t --benchmark test-kernel
expect_reject "没有 benchmark" "至少给一个" env WF_ROOT="$SBX" python3 "$SBX/platform/tools/wf.py" --root "$SBX" task-new --title t --goal g
expect_reject "benchmark 不认识" "不认识" env WF_ROOT="$SBX" python3 "$SBX/platform/tools/wf.py" --root "$SBX" task-new --title t --goal g --benchmark nope
expect_reject "模板不认识" "" env WF_ROOT="$SBX" python3 "$SBX/platform/tools/wf.py" --root "$SBX" task-new --title t --goal g --benchmark test-kernel --template nope

step "3. task-new（模板：完整八层；--create；章程待批）——没有成功判据"
WF 6 task-new --title "CLI smoke task" --goal "验证层接口卡与去领域" --benchmark test-kernel --benchmark test-suite --create
TID=TASK-cli-smoke-task; IT1=ITER-cli-smoke-task-1
L1=PKG-L1-general-cli-smoke-task-i1-l1; L2=PKG-L2-general-cli-smoke-task-i1-l2
test -f "$SBX/domains/general/pack.json" && echo "  ✓ 默认存放位置已自动创建（domains/general）"
python3 -I - "$SBX" "$L1" "$TID" "$IT1" <<'EOF'
import json, sys, glob
sbx, l1, tid, it1 = sys.argv[1:]
e = json.load(open(glob.glob(sbx + '/domains/*/*/' + l1 + '/envelope.json')[0], encoding='utf-8'))
ok = e['provenance'].get('task_id') == tid and e['provenance'].get('iteration_id') == it1
t = json.load(open(sbx + '/tasks/' + tid + '.json', encoding='utf-8'))
print('  %s 新包一建出来就有 task_id / iteration_id；任务记录无 kind、无 success_criteria' % ('✓' if ok and 'kind' not in t and not t['goal'].get('success_criteria') else '✗'))
EOF
WF 8 task-list
WF 9 task-show "$TID"

step "4. 卡片：新包与旧包"
WF 16 card "$L1"
WF 400 card PKG-L2-sme2-bt-dequant-attribution | grep -q '没有接口卡：从现有字段摘录' && echo "  ✓ 没有接口卡的包，卡片标明是从现有字段摘录"
WF 400 card PKG-L7-cvfpu-cast-overflow-experiment | grep -q '按模板顺序推得' && echo "  ✓ 没写上游引用的旧包，按模板顺序推出上游"

step "5. 接口卡三条强制规则"
set_payload "$L1"   # 先把本层交付填齐，这样拦下来的原因只可能是接口卡
expect_reject "不带接口卡" "缺 interface" env WF_ROOT="$SBX" python3 "$SBX/platform/tools/wf.py" --root "$SBX" gate "$L1"
set_iface "$L1" '{"question": "在什么负载上、量什么、基线是多少", "gives": [{"id": "S1", "text": "基线窗口 100 拍", "truth": "observed", "evidence": []}]}'
expect_reject "标实测但没有登记在册的测试结果" "标了实测" env WF_ROOT="$SBX" python3 "$SBX/platform/tools/wf.py" --root "$SBX" gate "$L1"
set_iface "$L1" '{"question": "在什么负载上、量什么、基线是多少", "gives": [{"id": "S1", "text": "基线窗口 100 拍", "truth": "observed", "evidence": ["RES-does-not-exist"]}]}'
expect_reject "证据不在登记册" "不在登记册" env WF_ROOT="$SBX" python3 "$SBX/platform/tools/wf.py" --root "$SBX" gate "$L1"
LONG=$(python3 -I -c 'print("长" * 141)')
set_iface "$L1" "{\"question\": \"在什么负载上、量什么、基线是多少\", \"gives\": [{\"id\": \"S1\", \"text\": \"$LONG\", \"truth\": \"deduced\", \"evidence\": []}]}"
expect_reject "结论超过 140 字" "超过 140 字" env WF_ROOT="$SBX" python3 "$SBX/platform/tools/wf.py" --root "$SBX" gate "$L1"
set_iface "$L1" "{\"question\": \"在什么负载上、量什么、基线是多少\", \"gives\": [{\"id\": \"S1\", \"text\": \"基线窗口 100 拍\", \"truth\": \"deduced\", \"evidence\": []}], \"takes\": [{\"from\": \"PKG-L9-nowhere\", \"statement\": \"S1\"}]}"
expect_reject "takes 指向的不是上游" "不是本包的上游" env WF_ROOT="$SBX" python3 "$SBX/platform/tools/wf.py" --root "$SBX" gate "$L1"
WF 2 test-register --pkg "$L1" --file "$SBX/def.json" --run-artifact "$SBX/run-stats.txt" --verdict pass | grep -q 'RES-' && echo "  ✓ 登记了一条通过的测试"
RES=$(grep -o 'RES-[A-Za-z0-9-]*' "$SBX/platform/store/index/results.jsonl" | tail -1)
IF1_HEAD='{"question": "在什么负载上、量什么、基线是多少", "gives": [{"id": "S1", "text": "基线窗口 100 拍（两次一致）", "truth": "observed", "evidence": ["'"$RES"'"]}], "limits": ["仅冒烟用途"'
set_iface "$L1" "$IF1_HEAD]}"
WFANY 2 gate "$L1" | grep -q 'needs_decision' && echo "  ✓ 实测结论指向登记测试后，只剩「等人签字」"

step "6. 上游没验证完，下游不能通过"
set_iface "$L2" '{"question": "慢在哪、凭什么这么说", "takes": [{"from": "'"$L1"'", "statement": "S1"}], "gives": [{"id": "S1", "text": "解包流占主导", "truth": "deduced", "evidence": []}]}'
expect_reject "上游尚未验证" "上游没验证完，下游不能通过" env WF_ROOT="$SBX" python3 "$SBX/platform/tools/wf.py" --root "$SBX" gate "$L2"
expect_reject "decide 缺 --summary" "" env WF_ROOT="$SBX" python3 "$SBX/platform/tools/wf.py" --root "$SBX" decide --pkg "$L1" --type select --rationale x --confidence medium --reviewer huawei
WF 1 decide --pkg "$L1" --type select --summary "基线按冒烟口径拍定" --rationale "详细依据" --confidence medium --reviewer huawei --agent tester
SHA=$(python3 -I -c '
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
r = [x for x in rows if x.get("summary") == "基线按冒烟口径拍定"][-1]
print(r.get("content_sha", ""))' "$SBX/platform/store/index/decisions.jsonl")
[ -n "$SHA" ] && echo "  ✓ 决定回执记下了所审内容指纹（${SHA:0:8}…）"
WF 1 gate "$L1" | grep -q -E 'pass|通过' && echo "  ✓ L1 有人签字后核对通过"
# 核对通过之后接口卡改了：「已验证」不再成立，下游被拦；签字只对签过的内容有效
set_iface "$L1" "$IF1_HEAD, \"改过一行\"]}"
WFR card "$L1" 2>/dev/null | grep -q '未验证——接口卡在上次核对之后改过' && echo "  ✓ 核对后改内容：状态回到未验证" || echo "card state after drift: unexpected" >> "$SBX/.fail"
expect_reject "上游核对后改过，下游不能通过" "核对之后接口卡改过" env WF_ROOT="$SBX" python3 "$SBX/platform/tools/wf.py" --root "$SBX" gate "$L2"
WFANY 4 gate "$L1" | grep -q '需要重签' && echo "  ✓ 签字只对签过的内容有效：改过要重签" || echo "stale signature not flagged: unexpected" >> "$SBX/.fail"
WF 1 decide --pkg "$L1" --type select --summary "确认改动后的基线口径" --confidence medium --reviewer huawei --agent tester
WFR gate "$L1" >/dev/null 2>&1 && echo "  ✓ 重签后重新核对通过" || echo "L1 re-gate after re-sign: unexpected" >> "$SBX/.fail"
WFR card "$L2" 2>/dev/null | grep -q '✓ 上游已验证' && echo "  ✓ 上游重新验证后，L2 的「上游已验证」一行转为通过" || echo "L2 upstream row after L1 re-verified: unexpected" >> "$SBX/.fail"
# L2 自己的交付还是空的：它仍然过不了，但拦它的是自己的交付，不再是上游
WFANY 1 gate "$L2" | grep -q '→ fail' && WFR gate "$L2" 2>&1 | grep -q '上游没验证完' && echo "L2 still blocked by upstream: unexpected" >> "$SBX/.fail"

step "7. 章程批准 → 任务与首轮转进行中；迭代生命周期"
CH=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["charter_decision_id"])' "$SBX/tasks/$TID.json")
WF 1 approve "$CH" --as tester
WF 400 task-list | grep -q "$TID.*active" && echo "  ✓ 章程批准后任务转 active"
expect_reject "开下一轮缺理由" "" env WF_ROOT="$SBX" python3 "$SBX/platform/tools/wf.py" --root "$SBX" iter-new --task "$TID"
WF 1 iter-seal "$TID/1" --as tester --reason "冒烟：结束首轮"
WF 4 iter-new --task "$TID" --reason "首轮结论指向新问题" --create --as tester
WF 1 task-seal --task "$TID" --as tester --reason "冒烟结束"
expect_reject "已封板任务不能再开迭代" "已 sealed" env WF_ROOT="$SBX" python3 "$SBX/platform/tools/wf.py" --root "$SBX" iter-new --task "$TID" --reason x

step "8. 仅 benchmark 的模板（不走层）"
WF 4 task-new --title "bench only" --slug bench-only --goal "只看 benchmark 结果" --benchmark test-suite --template benchmark_only --as tester
WF 400 task-list | grep -q 'bench-only.*bench only' && echo "  ✓ 立项成功"
WF 400 task-list | grep -A1 'bench-only' | grep -q '不走层' && echo "  ✓ 不走层，没有迭代"
test -d "$SBX/domains/general/L1_observation/PKG-L1-general-bench-only-i1-l1" && echo "✗ 不该生成层包" >> "$SBX/.fail" || echo "  ✓ 没有生成层包"

step "9. state-export：状态词、模板节点、完成与否"
WF_ROOT="$SBX" python3 "$SBX/platform/tools/wf.py" --root "$SBX" state-export 2>/dev/null | python3 -I -c '
import sys, json
d = json.load(sys.stdin)
bad = []
for g in d["layers"].values():
    for p in g["packages"]:
        st, pend = p["state"], any(r["pending"] for r in p["card"]["receipts"])
        if (st == "已验证") != (p["lifecycle"] == "passed" and p["gate_verdict"] == "pass" and not pend
                                and not p["card"]["interface_drift"]): bad.append((p["package_id"], st))
        if not p["card"]["interface"].get("gives") and st != "未开始": bad.append((p["package_id"], "无交付却不是未开始"))
print("  state consistency:", "✓ 全部 38 个包的状态词与核对结论 / 待人决定一致" if not bad else "✗ %s" % bad)
if bad: sys.exit(1)
t = next(t for t in d["tasks"] if t["task_id"] == "TASK-sme2-flow1")
print("  sme2 迭代完成标志:", [(i["label"], i["complete"]) for i in t["iterations"]])
' || echo "state-export consistency check failed" >> "$SBX/.fail"

step "10. 层 agent：定义、开工简报留档、回读留痕、执行记录、拦截"
WFR guide | grep -q '观测 agent' && echo "  ✓ wf guide 列出每层的 agent 与工具包"
WFR next | grep -q '开工简报：wf brief' && echo "  ✓ wf next 直接给出开工命令"
WFR brief "$L1" --out --by tester > /dev/null && test -f "$SBX/domains/general/L1_observation/$L1/brief.md" && echo "  ✓ 开工简报留档：brief.md 落盘"
python3 -I - "$SBX" "$L1" <<'EOF'
import hashlib, json, sys, glob
sbx, pkg = sys.argv[1], sys.argv[2]
env = json.load(open(glob.glob(sbx + '/domains/*/*/' + pkg + '/envelope.json')[0], encoding='utf-8'))
rec = (env.get('provenance') or {}).get('brief') or {}
md = open(glob.glob(sbx + '/domains/*/*/' + pkg + '/brief.md')[0], 'rb').read()
ok = rec.get('by') == 'tester' and rec.get('sha256') == hashlib.sha256(md).hexdigest()[:16]
print('  %s 回执记下署名与简报指纹' % ('✓' if ok else '✗'))
if not ok: print('bad brief receipt')
EOF
WFR show "$L1" --for "$L2" --by tester > /dev/null && echo "  ✓ 另读上游留痕（wf show --for）"
WFR run-log --pkg "$L2" --agent tester --model test-model --duration 5 > /dev/null && echo "  ✓ 执行记录入册（wf run-log）"
expect_reject "层 agent 不能批准" "人的动作" env WF_ACTOR=layer:L1 python3 "$SBX/platform/tools/wf.py" --root "$SBX" approve DEC-x --as tester
expect_reject "层 agent 不能作废" "人的动作" env WF_ACTOR=layer:L1 python3 "$SBX/platform/tools/wf.py" --root "$SBX" invalidate "$L1" --reason x
WF_ACTOR=layer:L1 WFR decide --pkg "$L1" --type select --summary "层 agent 记一条" --confidence high --reviewer tester > /dev/null && python3 -I - "$SBX" <<'EOF'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1] + '/platform/store/index/decisions.jsonl') if l.strip()]
d = [x for x in rows if x.get('summary') == '层 agent 记一条'][-1]
print('  %s 层 agent 记决定被强制成待批（reviewer=pending-user）' % ('✓' if d['reviewer'] == 'pending-user' else '✗'))
EOF

step "11. selftest"
WF 1 selftest

echo
if [ -s "$SBX/.fail" ]; then echo "CLI SMOKE FAILED:"; sed 's/^/  /' "$SBX/.fail"; echo "sandbox: $SBX"; exit 1; fi
echo "ALL CLI SMOKE CHECKS PASSED"
rm -rf "$SBX"
