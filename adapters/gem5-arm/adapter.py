#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gem5-arm 适配器五接口实现（build/run/stats/guard/render_cmd）。

接口契约见 adapter.yaml；本文件是唯一允许硬编码 gem5 命令形态的地方——
领域包与层包一律通过本适配器渲染命令（跨平台架构 §3）。
用法：
  python3 adapters/gem5-arm/adapter.py render --mode se --binary <ELF> --cpu o3 \
      --caches --zpath --luti-wide --attn --fmopa-banks 4 --outdir /tmp/x
  python3 adapters/gem5-arm/adapter.py stats <outdir>
  python3 adapters/gem5-arm/adapter.py guard <outdir> --expect -1073742336
  python3 adapters/gem5-arm/adapter.py run ... [--timeout 600]   # = render 后执行
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ADAPTER_DIR = Path(__file__).resolve().parent
ROOT = ADAPTER_DIR.parents[1]
YAML = ADAPTER_DIR / "adapter.yaml"


def _params():
    """轻量解析 inject.params（无需 PyYAML：结构受控）。"""
    text = YAML.read_text(encoding="utf-8")
    sec = text.split("    params:")[1].split("    presets:")[0]
    out = {}
    for m in re.finditer(r"^      (\w+):\s*\{flag: (\S+), type: (\w+)(.*)\}$", sec, re.M):
        name, flag, typ, rest = m.groups()
        d = {"flag": flag, "type": typ}
        for kv in re.finditer(r"(\w+): ([^,}]+)", rest):
            d[kv.group(1)] = kv.group(2).strip()
        out[name] = d
    return out


def render_cmd(args):
    p = _params()
    flags = []
    for k, spec in p.items():
        v = getattr(args, k, None)
        if v is None or v is False or k in ("cpu",):
            continue
        v = 1 if v is True else v   # gem5 侧全部值式（type=int choices=(0,1)）
        flags += [spec["flag"], str(v)]
    if args.mode == "se":
        if args.cpu == "o3" and not (args.caches or getattr(args, "caches", None)):
            print("警告：O3 必须带 --caches 1（无 caches 的 snoop panic 已知缺陷），已自动补",
                  file=sys.stderr)
            flags += ["--caches", "1"]
        cmd = [str(ROOT / "src/gem5/build/ARM_glm/gem5.opt"), f"--outdir={args.outdir}",
               "configs/example/int4_se.py", "--binary", str(args.binary),
               "--cpu", args.cpu] + flags
        cwd = ROOT / "src/gem5"
    else:
        cmd = [str(ROOT / "src/gem5/build/ARM_glm/gem5.opt"), f"--outdir={args.outdir}",
               "configs/example/arm/fs_sme.py",
               "--kernel", str(ROOT / "fs/build/linux-6.18.63/vmlinux"),
               "--cpu", "atomic", "--roi-cpu", args.roi_cpu or "o3",
               "--release", "Armv93", "--rdinit", args.rdinit] + flags
        cwd = ROOT / "src/gem5"
    return cmd, cwd


def cmd_run(args):
    cmd, cwd = render_cmd(args)
    Path(args.outdir).mkdir(parents=True, exist_ok=True)
    print("RUN:", " ".join(cmd))
    r = subprocess.run(cmd, cwd=cwd, timeout=args.timeout or 600,
                       capture_output=not args.verbose, text=True)
    log = Path(args.outdir) / "run.log"
    if not args.verbose:
        log.write_text((r.stdout or "") + (r.stderr or ""), encoding="utf-8")
    print("exit:", r.returncode)
    return r.returncode


def cmd_stats(args):
    window_key = "system.cpu.numCycles"  # se；fs 由调用方传 --window 覆盖
    if args.window:
        window_key = args.window
    pat = re.compile(r"^(system[.\w]+)\s+(-?\d+)")
    got = {}
    f = Path(args.outdir) / "stats.txt"
    for line in f.read_text(errors="replace").splitlines():
        m = pat.match(line)
        if m:
            got[m.group(1)] = int(m.group(2))
    if "simInsts" in got or True:
        for line in f.read_text(errors="replace").splitlines():
            m = re.match(r"^(simInsts)\s+(\d+)", line)
            if m:
                got[m.group(1)] = int(m.group(2))
    if window_key not in got:
        sys.exit(f"window 统计缺失：{window_key}")
    print("window:", got[window_key])
    for k in args.counter or []:
        if k in got:
            print(f"{k}: {got[k]}")
    return got


def cmd_guard(args):
    pat = re.compile(r"m5_fail instruction encountered code (-?\d+)")
    codes = []
    for name in ("run.log", "simout", "console"):
        f = Path(args.outdir) / name
        if f.exists():
            codes += pat.findall(f.read_text(errors="replace"))
    if not codes:
        print("GUARD: FAIL（未找到 m5_fail 行）")
        return 2
    code = codes[-1]
    ok = (args.expect is None) or (int(code) == int(args.expect))
    print(f"GUARD: {'PASS' if ok else 'FAIL'} code={code} expect={args.expect}")
    return 0 if ok else 2


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="fn", required=True)
    r = sub.add_parser("render"); _add_run_args(r)
    r = sub.add_parser("run"); _add_run_args(r)
    r.add_argument("--timeout", type=int, default=600)
    r.add_argument("--verbose", action="store_true")
    s = sub.add_parser("stats")
    s.add_argument("outdir"); s.add_argument("--window"); s.add_argument("--counter", nargs="*")
    g = sub.add_parser("guard")
    g.add_argument("outdir"); g.add_argument("--expect", type=int)

    args = ap.parse_args()
    if args.fn == "render":
        cmd, cwd = render_cmd(args)
        print(" ".join(cmd)); print("cwd:", cwd)
    elif args.fn == "run":
        sys.exit(cmd_run(args))
    elif args.fn == "stats":
        cmd_stats(args)
    elif args.fn == "guard":
        sys.exit(cmd_guard(args))


def _add_run_args(p):
    p.add_argument("--mode", choices=["se", "fs"], default="se")
    p.add_argument("--binary"); p.add_argument("--rdinit")
    p.add_argument("--outdir", required=True)
    p.add_argument("--cpu", default="o3"); p.add_argument("--roi-cpu")
    for k, spec in _params().items():
        if k == "cpu":            # 已在上方显式定义
            continue
        p.add_argument(f"--{k.replace('_','-')}", dest=k)


if __name__ == "__main__":
    main()
