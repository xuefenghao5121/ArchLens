#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""caselaw — 判例库：仿真平台积累的判断资产，情境检索 + 判例沉淀。

用法：
  caselaw.py search "done 信号是脉冲还是电平 规格没写"   # 情境检索（任务开始/失败时刻）
  caselaw.py show CL-001                                  # 看完整判例
  caselaw.py list [--domain rtl]                          # 列库
  caselaw.py add --title ... --situation ...              # 沉淀新判例（工作→判例→未来工作）

设计依据（RSI 实验 2026-11-20 结论）：
  经验价值来自"情境匹配的判例检索 + 挖掘过程参与"，规则清单下发给新代理无增益。
  因此本库在【任务开始的情境匹配】和【失败时刻的签名匹配】时被检索，不做预装载。
"""
import argparse
import json
import re
import sys
from pathlib import Path

CASELAW_DIR = Path(__file__).resolve().parent.parent / "caselaw"
CASES_FILE = CASELAW_DIR / "cases.jsonl"

FIELDS_FOR_SEARCH = ["title", "situations", "signals", "action", "lesson", "domains"]


def load_cases():
    if not CASES_FILE.exists():
        return []
    return [json.loads(l) for l in CASES_FILE.read_text().splitlines() if l.strip()]


def _terms(query):
    # 中文无空格：按标点/空格切，CJK 长词保留整词匹配 + 2-gram 兜底
    raw = [t for t in re.split(r"[\s,，、;；/]+", query.strip()) if t]
    terms = []
    for t in raw:
        terms.append(t)
        if re.search(r"[\u4e00-\u9fff]", t) and len(t) >= 3:
            terms += [t[i:i+2] for i in range(len(t) - 1)]
    return terms


def search(query, top=5):
    cases = load_cases()
    terms = [t.lower() for t in _terms(query)]
    scored = []
    for c in cases:
        hay = {f: " ".join(c.get(f, []) if isinstance(c.get(f), list) else [c.get(f, "")]).lower()
               for f in FIELDS_FOR_SEARCH}
        score = 0
        for t in terms:
            for f, h in hay.items():
                n = h.count(t)
                if n:
                    w = 3 if f == "situations" else 2 if f in ("signals", "title") else 1
                    score += n * w
        if score:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top]]


def render(c, brief=False):
    out = [f"[{c['id']}] {c['title']}"]
    if not brief:
        out.append(f"  情境: {'；'.join(c.get('situations', []))}")
        out.append(f"  信号/线索: {', '.join(c.get('signals', []))}")
        out.append(f"  做法: {c.get('action', '')}")
        out.append(f"  教训: {c.get('lesson', '')}")
        if c.get("outcome"):
            out.append(f"  实测: {json.dumps(c.get('outcome'), ensure_ascii=False)}")
        if c.get("evidence"):
            out.append(f"  证据: {c['evidence']}")
    return "\n".join(out)


def cmd_search(args):
    hits = search(args.query, args.top)
    if not hits:
        print("无命中判例（可正常推进；工作结束后用 caselaw add 沉淀新判例）")
        return
    print(f"命中 {len(hits)} 条（按情境相关度排序）：\n")
    for c in hits:
        print(render(c) + "\n")


def cmd_show(args):
    for c in load_cases():
        if c["id"] == args.id:
            print(render(c))
            return
    print(f"未找到 {args.id}")
    sys.exit(1)


def cmd_list(args):
    for c in load_cases():
        if args.domain and args.domain not in c.get("domains", []):
            continue
        print(render(c, brief=True))


def cmd_add(args):
    case = {
        "id": f"CL-{len(load_cases())+1:03d}",
        "title": args.title,
        "situations": args.situation.split(";") if args.situation else [],
        "signals": args.signals.split(",") if args.signals else [],
        "action": args.action or "",
        "lesson": args.lesson or "",
        "outcome": {},
        "domains": args.domain.split(",") if args.domain else ["general"],
        "evidence": args.evidence or "",
        "source": args.source or "manual",
    }
    with CASES_FILE.open("a") as f:
        f.write(json.dumps(case, ensure_ascii=False) + "\n")
    print(f"已沉淀 {case['id']}: {case['title']}")


def main():
    ap = argparse.ArgumentParser(description="仿真平台判例库")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("--top", type=int, default=5); s.set_defaults(fn=cmd_search)
    s = sub.add_parser("show"); s.add_argument("id"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("list"); s.add_argument("--domain"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("add")
    s.add_argument("--title", required=True); s.add_argument("--situation", default="")
    s.add_argument("--signals", default=""); s.add_argument("--action", default="")
    s.add_argument("--lesson", default=""); s.add_argument("--domain", default="")
    s.add_argument("--evidence", default=""); s.add_argument("--source", default="")
    s.set_defaults(fn=cmd_add)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
