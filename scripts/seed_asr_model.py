#!/usr/bin/env python3
"""给 retrieval_models 插/更新 asr 行。

在 lea-rag 容器里跑：
    docker exec lea-rag-test python /tmp/seed_asr_model.py /tmp/.asrkey

key 从文件读，不走命令行参数（参数会进 ps 和 history），脚本也不打印它。

## 为什么是 spark-asr 而不是 funasr

文档推荐 funasr（号称支持 3 小时音频），但实测它稳定 500「接收消息超时」——
连打 3 次全失败，每次卡 30 秒。不是模型名写错：错名字会明确回"模型不存在"，
而 funasr 回的是超时，说明网关认识它但上游引擎不应答。

spark-asr 用同一个请求体返回 200，12.25s 音频 10/10 关键词命中、2.6 秒返回。
spark-speech 可用但当天配额已用尽；spark-mulspeech 同样配额用尽。

代价是 spark-asr 对长音频**静默截断**（约 120-130s 封顶，HTTP 仍是 200），
所以 kb_processor 按 60s 分段提交。segment_seconds 写进 config，
哪天 funasr 修好了可以改成 funasr 并把分段调大。
"""

import json
import sys

sys.path.insert(0, "/app/src")

from lea_rag.db.database import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

key_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/.asrkey"
with open(key_file) as f:
    KEY = f.read().strip()

NAME = "spark-asr"
API_BASE = "https://tenantapi-ai.ouchn.edu.cn"
CONFIG = {
    "protocol": "base_pcm",
    "language": "zh_cn",
    # 实测约 120-130s 之后静默截断，取一半余量
    "segment_seconds": 60,
    "timeout": 600,
}

db = SessionLocal()
try:
    existing = db.execute(
        text("SELECT id, model_name FROM retrieval_models WHERE model_type = 'asr'")
    ).fetchall()

    if existing:
        print(f"已有 asr 行: {[(r[0], r[1]) for r in existing]}，改成更新")
        db.execute(
            text(
                "UPDATE retrieval_models SET name=:n, model_name=:m, api_base=:b, "
                "api_key=:k, enabled=1, config=:c WHERE model_type='asr'"
            ),
            {"n": NAME, "m": NAME, "b": API_BASE, "k": KEY,
             "c": json.dumps(CONFIG, ensure_ascii=False)},
        )
    else:
        db.execute(
            text(
                "INSERT INTO retrieval_models "
                "(name, model_type, api_base, api_key, model_name, enabled, is_default, config) "
                "VALUES (:n, 'asr', :b, :k, :m, 1, 1, :c)"
            ),
            {"n": NAME, "m": NAME, "b": API_BASE, "k": KEY,
             "c": json.dumps(CONFIG, ensure_ascii=False)},
        )
    db.commit()

    # 回读确认（key 只报长度）
    rows = db.execute(
        text(
            "SELECT id, name, model_type, model_name, api_base, enabled, "
            "CHAR_LENGTH(COALESCE(api_key,'')), config "
            "FROM retrieval_models WHERE model_type='asr'"
        )
    ).fetchall()
    print()
    for r in rows:
        print(f"  id={r[0]} name={r[1]} type={r[2]} model={r[3]}")
        print(f"  api_base={r[4]} enabled={r[5]} keylen={r[6]}")
        print(f"  config={r[7]}")
    print()
    print("✓ asr 行就位")
finally:
    db.close()
