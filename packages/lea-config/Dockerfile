# lea-config —— Agent / 技能 / 模型配置服务 :8500
#
# 构建（上下文是仓库根）：
#     docker build -f packages/lea-config/Dockerfile -t lea-config:dev .
#
# 结构跟 packages/lea-tools/Dockerfile 一样，那份里有为什么不拷 .env、
# 为什么按包装依赖的完整说明。这份的区别只有一处：用非 root 跑（见下）。

FROM python:3.11-slim AS deps
COPY --from=ghcr.io/astral-sh/uv:0.6.12 /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv export --package lea-config --no-dev --frozen --no-emit-workspace \
        -o /requirements.txt

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:0.6.12 /uv /usr/local/bin/uv
COPY --from=deps /requirements.txt /tmp/requirements.txt
RUN uv pip install --system --no-cache -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

WORKDIR /app
COPY packages/lea-config/src ./src
# 共享的服务间鉴权包 —— 两个 COPY 合并进同一个 ./src，见 lea-tools/Dockerfile 的说明。
COPY packages/lea-authkit/src ./src
COPY migrations ./migrations
COPY scripts/apply_skill_versions_migration.py ./scripts/apply_skill_versions_migration.py
ENV PYTHONPATH=/app/src

# 非 root。这个服务只读 DB、不写本地文件系统，所以降权没有代价。
# （lea-tools 不能这么做 —— 它要写 NFS，降权的后果是静默降级，那份里有说明。）
RUN useradd --system --uid 10001 --no-create-home lea
USER 10001

EXPOSE 8500

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8500/health',timeout=4).status==200 else 1)"

CMD ["uvicorn", "lea_config.server:app", "--host", "0.0.0.0", "--port", "8500"]
