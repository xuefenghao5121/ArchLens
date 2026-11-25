#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qemu-aarch64-user 适配器（功能裁判）。

五接口中 build=预编译产物、stats=仅 exit/elapsed（无周期）、guard=stdout 小端 64 位校验和。
用法：
  python3 adapters/qemu-aarch64-user/adapter.py run --binary fs/rootfs/app/sve2_user
  python3 adapters/qemu-aarch64-user/adapter.py guard --stdout-file /tmp/x.out --expect 1064960
"""
import argparse
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QEMU = ROOT / "fs/build/qemu-11.1.3/build-user/qemu-aarch64"


def cmd_run(args):
    t0 = time.time()
    r = subprocess.run([str(QEMU), str(args.binary)], capture_output=True, timeout=args.timeout or 120)
    out = Path(args.out or "/tmp/qemu-adapter.out")
    out.write_bytes(r.stdout)
    elapsed = time.time() - t0
    code = struct.unpack("<q", r.stdout[:8])[0] if len(r.stdout) >= 8 else None
    print(f"exit={r.returncode} elapsed={elapsed:.1f}s stdout={len(r.stdout)}B")
    if code is not None:
        print(f"checksum(le64@0)={code}")
    if args.expect is not None and code is not None:
        ok = code == args.expect
        print(f"GUARD: {'PASS' if ok else 'FAIL'} code={code} expect={args.expect}")
        return 0 if ok else 2
    return r.returncode


def cmd_guard(args):
    data = Path(args.stdout_file).read_bytes()
    if len(data) < 8:
        print("GUARD: FAIL（stdout 不足 8 字节）")
        return 2
    code = struct.unpack("<q", data[:8])[0]
    ok = (args.expect is None) or (code == args.expect)
    print(f"GUARD: {'PASS' if ok else 'FAIL'} code={code} expect={args.expect}")
    return 0 if ok else 2


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="fn", required=True)
    r = sub.add_parser("run")
    r.add_argument("--binary", required=True)
    r.add_argument("--expect", type=int)
    r.add_argument("--out"); r.add_argument("--timeout", type=int, default=120)
    g = sub.add_parser("guard")
    g.add_argument("--stdout-file", required=True)
    g.add_argument("--expect", type=int)
    args = ap.parse_args()
    sys.exit(cmd_run(args) if args.fn == "run" else cmd_guard(args))


if __name__ == "__main__":
    main()
