# 环境容器描述（跨机重建凭据；首次迁移时按 build-instructions.md 构建填入）
# 基底镜像需满足 manifest.lock；项目目录（含 toolchain/ 预装产物与源码）以卷挂载或 COPY 进入
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y build-essential python3.12 python3-pip scons cmake \
    git device-tree-compiler libglib2.0-dev libpixman-1-dev flex bison ninja-build
# 注：g++ 13.3 为 24.04 默认；pyCircuit/Verilator 用 toolchain/ 预装产物（ABI 锁）
# COPY . /opt/sim-agent
# WORKDIR /opt/sim-agent && RUN bash -c "source env/toolchain.sh && make -C designs/combined_path/model install"
