#!/bin/bash
# 环境入口（宿主/机器无关包装：自动定位项目根，委托 toolchain/env.sh）
SIM_AGENT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export SIM_AGENT_ROOT
source "$SIM_AGENT_ROOT/toolchain/env.sh"
# 适配器路径提示
export SIM_ADAPTERS="$SIM_AGENT_ROOT/adapters"
