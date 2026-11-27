#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bind_hosts — 方法论单一事实源 → 多宿主格式投影（跨平台架构 §5）。

源：platform/methodology/*.md（含 YAML frontmatter：name/description）
出：ZCode skills（.agents/skills/，当前实践载体，沿用既有 skill 名保持连续）
    通用手册（host-bindings/manual/，任何 agent/人可直接读的纯 md——方法论本就是文件，无宿主耦合）
改内容请改源文件，然后重跑本脚本——宿主侧产物是生成物。
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "platform" / "methodology"
AGENTS = ROOT / "platform" / "agents"

# 方法论源名 → ZCode skill 名（沿用历史名，避免重复注册）
ZCODE_NAME = {"layer-discipline": "flow-layer-discipline",
              "l3-criteria": "l3-criteria-guard",
              "orchestration": "flow-orchestration"}

# 宿主子代理的工具限制：层 agent 要读写层包与跑仿真，调度 agent 只读与跑 wf（不给写文件的能力）
HOST_TOOLS = {"layer": "Read, Write, Edit, Bash, Glob, Grep",
              "orchestrator": "Read, Bash, Glob, Grep"}

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


def parse_agent_frontmatter(text):
    """agent.md 的 frontmatter：标量，或写成 JSON 数组的列表。"""
    fm, body = split_frontmatter(text)
    meta = {}
    for line in fm.splitlines():
        row = line.strip()
        if not row or row.startswith("#"):
            continue
        k, _, v = row.partition(":")
        k, v = k.strip(), v.strip()
        if k:
            meta[k] = json.loads(v) if v.startswith("[") else v
    return meta, body


def project_agents(check=False):
    """platform/agents/<层>/agent.md → .zcode/agents/<host_name>.md。

    宿主读 frontmatter 的 name / description / tools / skills；本平台的 writes、must_not
    与工具包写进正文末尾，让被派发的 agent 自己看得到边界。宿主对项目级定义不认
    permissionMode，所以边界靠 tools 限制加这段声明。
    返回 {"written": 写入份数, "drift": 与源不一致的产物, "checked": 检查份数}。"""
    results = {"written": 0, "drift": [], "checked": 0}
    packs = {}
    packs_path = AGENTS / "toolpacks.json"
    if packs_path.exists():
        packs = json.loads(packs_path.read_text(encoding="utf-8")).get("packs", {})
    for src in sorted(AGENTS.glob("*/agent.md")):
        meta, body = parse_agent_frontmatter(src.read_text(encoding="utf-8"))
        host = meta.get("host_name", "")
        if not host:
            continue
        layer = meta.get("layer", "")
        m = re.search(r"\*\*这一层回答的问题\*\*：(.+)", body)
        question = (m.group(1).strip() if m else "")
        rel = src.relative_to(ROOT).as_posix()
        desc = f"{meta.get('name', '')}（{layer}）：{question}" if question else meta.get("name", "")
        tools_line = HOST_TOOLS["orchestrator" if layer == "orchestrator" else "layer"]
        skills_line = ", ".join(meta.get("skills", []))
        tool_names = meta.get("tools", [])
        boundary = [
            "---",
            "",
            "## 这次派工的边界（投影器补；改源文件后重跑 platform/tools/bind_hosts.py）",
            "",
            "- 工具包（经 Bash 调）：" + ("、".join(
                f"{t}（{packs.get(t, {}).get('what', '')}）" for t in tool_names) or "—"),
            "- 可写：" + "；".join(meta.get("writes", [])),
            "- 不许做：" + "；".join(meta.get("must_not", [])),
            f"- 宿主工具限制：{tools_line}"
            + ("（没有写文件与改文件的能力）" if layer == "orchestrator" else ""),
            "",
        ]
        content = (
            "---\n"
            f"name: {host}\n"
            "description: >-\n"
            f"  {desc}\n"
            f"tools: {tools_line}\n"
            + (f"skills: {skills_line}\n" if skills_line else "")
            + "---\n\n"
            + f"<!-- GENERATED from {rel} — edit the source, then rerun platform/tools/bind_hosts.py -->\n\n"
            + body.strip() + "\n\n" + "\n".join(boundary)
        )
        out = ROOT / ".zcode" / "agents" / f"{host}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        results["checked"] += 1
        if check:
            if not out.exists() or out.read_text(encoding="utf-8") != content:
                results["drift"].append(str(out))
        else:
            out.write_text(content, encoding="utf-8")
            results["written"] += 1
    return results


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

    if "zcode" in args.hosts:
        projected = project_agents(args.check)
        results["written"] += projected["written"]
        results["checked"] += projected["checked"]
        results["drift"] += projected["drift"]

    print(f"written={results['written']} checked={results['checked']} drift={len(results['drift'])}")
    for d in results["drift"]:
        print("  DRIFT:", d)
    sys.exit(1 if results["drift"] else 0)


if __name__ == "__main__":
    main()
