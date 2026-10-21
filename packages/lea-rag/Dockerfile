# lea-rag —— 知识库检索 / 记忆服务 :8300
#
# 构建（上下文是仓库根）：
#     docker build -f packages/lea-rag/Dockerfile -t lea-rag:dev .
#
# 结构跟 packages/lea-tools/Dockerfile 一样，那份里有为什么不拷 .env、
# 为什么按包装依赖的完整说明。
#
# 这个服务的依赖最重（126 个包，含 milvus / embedding 那一套），所以"按包装"在这里
# 省的不是自己而是**另外两个** —— 它们不用再背这 126 个。

FROM python:3.11-slim AS deps
COPY --from=ghcr.io/astral-sh/uv:0.6.12 /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv export --package lea-rag --no-dev --frozen --no-emit-workspace \
        -o /requirements.txt

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 依赖里有需要编译的（grpcio 之类），装完把工具链删掉 —— 留着会让镜像大一倍，
# 而且编译器留在运行镜像里本身就不该。
#
# ffmpeg 是**运行时**依赖，不能跟 build-essential 一起 purge：
#
#   - 视频入知识库靠它抽音轨（mp4 → wav → 基座 ASR）。没有它视频就只能走
#     跨服务异步编排（subtitle_api），那要解决 VideoUrl/轮询/租户映射三件事。
#   - 音频从「只支持 WAV」放开到 mp3/m4a/flac/ogg。标准库的 wave 只读 WAV。
#   - audioop 在 Python 3.13 被移除（PEP 594），届时 _wav_to_pcm16k 会在 import
#     处直接炸。有了 ffmpeg 那条退路就不再是唯一实现。
#
# 代价是镜像变大（ffmpeg + 它的编解码库约 100MB 量级）。换来的是三件事一起解决，
# 而且视频不用再跨服务。
COPY --from=ghcr.io/astral-sh/uv:0.6.12 /uv /usr/local/bin/uv
COPY --from=deps /requirements.txt /tmp/requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && apt-get install -y --no-install-recommends ffmpeg \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /tmp/requirements.txt

WORKDIR /app
COPY packages/lea-rag/src ./src
# 共享的服务间鉴权包 —— 两个 COPY 合并进同一个 ./src，见 lea-tools/Dockerfile 的说明。
COPY packages/lea-authkit/src ./src
ENV PYTHONPATH=/app/src

# 非 root：只读 DB / 向量库 / 对象存储，不写本地文件系统。
RUN useradd --system --uid 10001 --no-create-home lea
USER 10001

EXPOSE 8300

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8300/health',timeout=4).status==200 else 1)"

CMD ["uvicorn", "lea_rag.server:app", "--host", "0.0.0.0", "--port", "8300"]
