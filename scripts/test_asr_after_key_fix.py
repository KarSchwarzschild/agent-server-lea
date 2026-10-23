"""验证 ASR key 分离后能否正常转写。

两个 key 混用过一次：retrieval_models 的 asr 行曾被写成 MinerU 的 sk-TTq2...，
于是基座 ASR 返回 401（body 为空，看不出是权限问题）。分开之后在这里验。

运行：
  docker cp test_asr_after_key_fix.py lea-rag-test:/tmp/
  docker exec lea-rag-test python /tmp/test_asr_after_key_fix.py

判定不是"有没有返回文字"，而是**已知原文的关键词覆盖率** —— 返回了东西
不等于转写对了，可能是别的音频、也可能只转了开头一截。
"""
import os
import subprocess
import sys

sys.path.insert(0, "/app/src")

from lea_rag.db.database import SessionLocal  # noqa: E402
from lea_rag.db.models import RetrievalModel  # noqa: E402
from lea_rag.services.kb_processor import DocumentProcessor as DP  # noqa: E402

AUDIO = "/tmp/asrtest.wav"
# TTS 合成时的原文，用来算覆盖率
SOURCE = (
    "知识库的多模态处理，文档解析使用 MinerU，"
    "视频和音频通过 ffmpeg 抽取音轨之后交给语音识别服务。"
)
KEYWORDS = ["知识库", "多模态", "文档", "解析", "视频", "音频", "抽取", "音轨", "语音识别", "服务"]

# 容器重启会清 /tmp，没有就用 TTS 之外的办法重造一个（正弦波测不出转写对不对，
# 所以这里只在文件存在时才做内容判定）
if not os.path.exists(AUDIO):
    print(f"✗ {AUDIO} 不存在（容器重启清掉了 /tmp）")
    print("  需要重新 docker cp 一个有人声的音频进去，正弦波测不出转写质量")
    sys.exit(1)

dur = subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
     "-of", "default=nw=1:nk=1", AUDIO],
    capture_output=True, text=True, check=False,
).stdout.strip()
print(f"音频 {AUDIO}，时长 {dur}s")
print(f"原文 {SOURCE}")
print()

with SessionLocal() as db:
    model = (
        db.query(RetrievalModel)
        .filter(RetrievalModel.model_type == "asr", RetrievalModel.enabled == True)  # noqa: E712
        .first()
    )
    if not model:
        print("✗ retrieval_models 里没有启用的 asr 行")
        sys.exit(1)

    key = model.api_key or ""
    print(f"模型 {model.model_name or model.name}")
    print(f"api_base {model.api_base}")
    print(f"api_key {len(key)} 字符，形如 {key[:12]}…")
    print(f"config {model.config}")
    print()

    # 这个 key 用错过一次：asr 行里塞的是 MinerU 的 sk-xxx。形态能提前看出来。
    if key.startswith("sk-"):
        print("⚠ asr 行的 key 是 sk- 形态 —— 那是 MinerU 官方云的 key，"
              "基座 ASR 要的是 tenant- 开头的")
        print()

    # _asr_transcribe 是实例方法但不碰 self（里面都按类名调 staticmethod），
    # 所以可以传 None
    try:
        text = DP._asr_transcribe(None, model, AUDIO)
    except Exception as e:
        print(f"✗ 转写抛错 {type(e).__name__}: {e}")
        sys.exit(1)

if not text:
    print("✗ 转写返回空字符串")
    sys.exit(1)

hit = [k for k in KEYWORDS if k in text]
print(f"✓ 转写成功，{len(text)} 字符")
print(f"  {text}")
print()
print(f"关键词 {len(hit)}/{len(KEYWORDS)} 命中: {hit}")
miss = [k for k in KEYWORDS if k not in text]
if miss:
    print(f"未命中: {miss}")

if len(hit) >= len(KEYWORDS) * 0.8:
    print()
    print("PASS —— ASR 整条链通")
    sys.exit(0)
print()
print("FAIL —— 返回了文字但内容对不上原文")
sys.exit(1)
