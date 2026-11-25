#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bind_hosts — 方法论单一事实源 → 多宿主格式投影（跨平台架构 §5）。

源：platform/methodology/*.md（含 YAML frontmatter：name/description）
出：ZCode skills（.agents/skills/，当前实践载体，沿用既有 skill 名保持连续）
    通用手册（host-bindings/manual/，任何 agent/人可直接读的纯 md——方法论本就是文件，无宿主耦合）
改内容请改源文件，然后重跑本脚本——宿主侧产物是生成物。
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "platform" / "methodology"

# 方法论源名 → ZCode skill 名（沿用历史名，避免重复注册）
ZCODE_NAME = {"layer-discipline": "flow-layer-discipline",
              "l3-criteria": "l3-criteria-guard"}

MARKER = "<!-- GENERATED from platform/methodology/{src}.md — edit the source, then rerun tools/bind_hosts.py -->\n"


def split_frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        return "", text
    return m.group(1), m.group(2)


def build_frontmatter(fm, name_override=None, description=None):
    # 解析出 name/description 两个键，重组为宿主 frontmatter
    keys = dict(re.findall(r"^(\w[\w-]*):\s*>?-?\s*\n?((?:.*?\n??)*?)(?=^\w[\w-]*:|\Z)", fm, re.M))
    name = name_override or keys.get("name", "").strip()
    desc = (description or keys.get("description", "")).strip()
    if desc.startswith(">-") or desc.startswith("|-"):
        desc = desc[2:].strip()
    desc = " ".join(desc.split())  # 压平多行
    return name, desc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosts", nargs="+", default=["zcode", "manual"])
    ap.add_argument("--check", action="true" if False else "store_true",
                    help="只校验产物与源一致（CI 用），不重写")
    args = ap.parse_args()

    sources = sorted(SRC.glob("*.md"))
    if not sources:
        sys.exit(f"未找到方法论源：{SRC}")
    results = {"written": 0, "checked": 0, "drift": []}

    for src in sources:
        if src.name == "README.md":
            continue
        slug = src.stem
        text = src.read_text(encoding="utf-8")
        fm, body = split_frontmatter(text)
        name, desc = build_frontmatter(fm)

        if "zcode" in args.hosts:
            zname = ZCODE_NAME.get(slug, slug)
            out = ROOT / ".agents" / "skills" / zname / "SKILL.md"
            content = f"---\nname: {zname}\ndescription: >-\n  {desc}\n---\n\n{MARKER.format(src=slug)}{body}"
            out.parent.mkdir(parents=True, exist_ok=True)
            if args.check:
                if not out.exists() or out.read_text(encoding='utf-8') != content:
                    results["drift"].append(str(out))
            else:
                out.write_text(content, encoding="utf-8")
                results["written"] += 1

        if "manual" in args.hosts:
            out = ROOT / "host-bindings" / "manual" / src.name
            out.parent.mkdir(parents=True, exist_ok=True)
            if args.check:
                if not out.exists() or out.read_bytes() != src.read_bytes():
                    results["drift"].append(str(out))
            else:
                shutil.copy2(src, out)
                results["written"] += 1

    print(f"written={results['written']} checked={results['checked']} drift={len(results['drift'])}")
    for d in results["drift"]:
        print("  DRIFT:", d)
    sys.exit(1 if results["drift"] else 0)


if __name__ == "__main__":
    main()
