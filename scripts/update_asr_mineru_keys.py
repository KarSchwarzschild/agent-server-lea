#!/usr/bin/env python3
"""把基座 ASR 的 key 写进 retrieval_models 的 asr 行。

## 两个 key 是不同平台的，别混

    基座 ASR    tenant-xxx…   tenantapi-ai.ouchn.edu.cn   → retrieval_models.asr
    MinerU 云   sk-xxx…       mineru.net                  → .env 的 MINERU_TOKEN

混用过一次：asr 行里塞了 MinerU 的 sk-xxx，于是基座返回 401，而且 **body 为空**，
看不出是权限问题还是 token 失效。所以下面按前缀做形态检查。

## 这个脚本只管 asr 行

MinerU 的 token 由 parser._get_mineru_token() 从环境变量读，**不读这张表**
（而且读完缓存在模块级全局里）。所以往表里插 `model_type='mineru'` 是死配置：
看着配好了，实际没有任何消费者。我插过一行，又删了 —— 留着比没有更坏，
下一个人会以为改它有用。要换 MinerU token 就改 .env 然后重启容器。

## key 从文件读，不写在源码里

这个脚本进 git，硬编码的 key 会进提交历史，之后轮换也删不掉。
我还踩过一次：凭记忆把 key 写进脚本，前 30 字符对、后面是编的 ——
因为我只在 `LEFT(api_key, 30)` 的输出里见过它。所以一律从文件读、按指纹核对。

    docker exec lea-rag-test python /tmp/update_asr_mineru_keys.py \\
        --asr-key-file /tmp/.asrkey
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys

sys.path.insert(0, "/app/src")

from lea_rag.db.database import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

NAME = "spark-asr"
API_BASE = "https://tenantapi-ai.ouchn.edu.cn"
EXPECT_PREFIX = "tenant-"

#: 代码实际读的 config 键（kb_processor._asr_transcribe 里的 extra.get）。
#: 写别的名字不会报错，只会静默退回默认值 —— max_segment_seconds 踩过这个坑：
#: 库里写 600、代码读 segment_seconds 读不到、退回默认 60，于是"配置显示 600
#: 实际生效 60"，而短音频测试照样通过。
CONFIG = {
    "protocol": "base_pcm",
    "language": "zh_cn",
    # spark-asr 实测约 120-130s 后静默截断（HTTP 仍是 200），取一半余量。
    # 见 scripts/probe_base_asr_cap.py
    "segment_seconds": 60,
    "timeout": 600,
}

#: funasr 号称支持 3 小时，但实测稳定 500「接收消息超时」（连打 3 次全失败）。
#: 哪天它修好了，把 model_name 换成 funasr、segment_seconds 调大即可。
MODEL_NAME = "spark-asr"


def fp(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:12] if s else "(空)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asr-key-file", required=True)
    args = ap.parse_args()

    try:
        with open(args.asr_key_file) as f:
            key = f.read().strip()
    except FileNotFoundError:
        print(f"✗ 找不到 {args.asr_key_file}")
        return 1

    if not key:
        print(f"✗ {args.asr_key_file} 是空的")
        return 1

    # 形态检查。两个平台的 key 前缀不同，拿错时这里就能拦住。
    if not key.startswith(EXPECT_PREFIX):
        print(f"✗ key 不是 {EXPECT_PREFIX} 开头（实际 {key[:6]}…）")
        if key.startswith("sk-"):
            print("  sk- 是 MinerU 官方云的 key，它该进 .env 的 MINERU_TOKEN，")
            print("  不是基座 ASR 的凭证。基座要 tenant- 开头的。")
        return 1

    params = {
        "n": NAME,
        "m": MODEL_NAME,
        "b": API_BASE,
        "k": key,
        "c": json.dumps(CONFIG, ensure_ascii=False),
    }

    with SessionLocal() as db:
        row = db.execute(
            text("SELECT id FROM retrieval_models WHERE model_type = 'asr'")
        ).fetchone()

        if row:
            db.execute(
                text(
                    "UPDATE retrieval_models SET name=:n, model_name=:m, api_base=:b, "
                    "api_key=:k, config=:c, enabled=1 WHERE model_type='asr'"
                ),
                params,
            )
            print(f"✓ 更新 asr 行 id={row[0]}，key 指纹 {fp(key)}")
        else:
            db.execute(
                text(
                    "INSERT INTO retrieval_models "
                    "(name, model_type, api_base, api_key, model_name, enabled, config) "
                    "VALUES (:n, 'asr', :b, :k, :m, 1, :c)"
                ),
                params,
            )
            print(f"✓ 新增 asr 行，key 指纹 {fp(key)}")
        db.commit()

        r = db.execute(
            text(
                "SELECT id, name, model_name, api_base, LENGTH(api_key), enabled, config "
                "FROM retrieval_models WHERE model_type='asr'"
            )
        ).fetchone()
        print()
        print(f"  id={r[0]} name={r[1]} model={r[2]}")
        print(f"  api_base={r[3]} keylen={r[4]} enabled={r[5]}")
        print(f"  config={r[6]}")

    print()
    print("MinerU 的 token 不在这张表里 —— 改 .env 的 MINERU_TOKEN 然后重启容器。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
