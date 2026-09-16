# lea-tools —— 工具执行网关 :8400
#
# 构建（上下文是仓库根，不是本目录）：
#     docker build -f packages/lea-tools/Dockerfile -t lea-tools:dev .
#
# ── 两条跟 legacy packages/lea-server-legacy/Dockerfile 不一样的地方 ───────
#
# 1. **不拷 .env。** 那份里有一行 `COPY .env* ./`，等于把数据库密码、对象存储 key、
#    中转 API key 一起烤进镜像推到 registry。镜像层扒得出来，后续层删掉也没用。
#    配置全部运行时注入（k8s 用 Secret / ConfigMap，compose 用 env_file）。
#
# 2. **只装本服务的依赖。** 那份在仓库根跑 `uv sync`，把整个 workspace 的依赖全装上
#    —— lea-config 只要 41 个包，却会背上 lea-rag 那 126 个（milvus、embedding
#    那一堆）。这里用 `uv export --package` 导出单个包的依赖再装。
#
# 分层：依赖只依赖 pyproject.toml + uv.lock（实测 uv export 不需要成员源码），
# 所以改业务代码不会触发重装依赖。

# ── 阶段 1：解析出本服务的依赖清单 ────────────────────────────────────────
FROM python:3.11-slim AS deps

# uv 版本钉死。用 :latest 的话同一个 commit 今天和下个月构建出来的镜像可能不同，
# 而排查"上周还好的"最需要的就是能重现。
COPY --from=ghcr.io/astral-sh/uv:0.6.12 /uv /usr/local/bin/uv

WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv export --package lea-tools --no-dev --frozen --no-emit-workspace \
        -o /requirements.txt

# ── 阶段 2：运行镜像 ──────────────────────────────────────────────────────
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:0.6.12 /uv /usr/local/bin/uv
COPY --from=deps /requirements.txt /tmp/requirements.txt
ARG UV_INDEX_URL
RUN uv pip install --system --no-cache -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

WORKDIR /app
COPY packages/lea-tools/src ./src
# 共享的服务间鉴权包。两个 COPY 都落进 ./src，各自的顶层目录不同
# （lea_tools/ 和 lea_authkit/），所以合并到一个 PYTHONPATH 下就够了 ——
# 不需要 pip install，也不需要动上面的 requirements（`--no-emit-workspace`
# 本来就会把 workspace 成员排除掉，这正是我们要的）。
COPY packages/lea-authkit/src ./src
ENV PYTHONPATH=/app/src

# 这个服务**故意用 root 跑**，跟另外两个不一样。
#
# 它要往共享存储（NFS）建 session 目录，给沙箱做 host-mount。跑不了 root（或者
# PV 的属主 UID 不匹配）时的表现不是报错，是**静默降级** —— 日志里一行
# "host-mount skipped: shared storage not writable"，然后沙箱退回用容器内的
# 本地 fs。于是沙箱一死用户的文件就没了，而一切看起来正常。
#
# 想换成非 root：把 PV 的属主设成对应 UID，然后确认日志里没有那行 skipped。
# 别只看服务起没起来 —— 它照样会起来。

EXPOSE 8400

# 给 compose 用。k8s 走 readinessProbe，会忽略这条。slim 镜像没有 curl，用 python。
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8400/health',timeout=4).status==200 else 1)"

CMD ["uvicorn", "lea_tools.server:app", "--host", "0.0.0.0", "--port", "8400"]
