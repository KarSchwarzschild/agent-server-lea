# lea-parser —— 文件解析 / 分块 / 限流 / 计费服务 :8600
#
# 构建（上下文是仓库根）：
#     docker build -f packages/lea-parser/Dockerfile -t lea-parser:dev .
#
# 结构照 packages/lea-rag/Dockerfile，那份里有为什么不拷 .env、为什么按包装依赖
# 的完整说明。
#
# 为什么不并进 lea-rag：这个服务有自己的鉴权（auth.py，用 JWT_SECRET 独立校验）、
# 自己的限流和计费（api/usage.py），依赖里那几个解析库（PyMuPDF / python-docx /
# python-pptx）只有它用。并进去等于让检索服务背上一套计费逻辑和一堆文档解析依赖。
#
# 用户解析接口继续由 auth.py 独立校验 JWT。管理控制台接口只复用 lea-authkit 的
# Actor/管理员判定，并额外验证 Gateway 的服务间 token；不安装全局 authkit 中间件，
# 避免改变现有 /parse 和 /usage 的 JWT 契约。

FROM python:3.11-slim AS deps
COPY --from=ghcr.io/astral-sh/uv:0.6.12 /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv export --package lea-parser --no-dev --frozen --no-emit-workspace \
        -o /requirements.txt

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# PyMuPDF 有 wheel，但 cryptography（python-jose[cryptography]）在某些平台要编译。
# 装完删掉工具链，理由同 lea-rag。
COPY --from=ghcr.io/astral-sh/uv:0.6.12 /uv /usr/local/bin/uv
COPY --from=deps /requirements.txt /tmp/requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /tmp/requirements.txt

WORKDIR /app
COPY packages/lea-parser/src ./parser-src
COPY packages/lea-authkit/src ./authkit-src
ENV PYTHONPATH=/app/parser-src:/app/authkit-src

# 非 root。解析是在内存里做的（parse 接口收 multipart、返 md_content），
# 不落本地文件，所以不需要可写目录。
RUN useradd --system --uid 10001 --no-create-home lea
USER 10001

EXPOSE 8600

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8600/health',timeout=4).status==200 else 1)"

CMD ["uvicorn", "lea_parser.server:app", "--host", "0.0.0.0", "--port", "8600"]
