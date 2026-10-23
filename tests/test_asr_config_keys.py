"""ASR 的 config 键名：写错不会报错，只会静默退回默认值。

## 踩过两次的形状

1. `model.extra` —— RetrievalModel 没有 extra 列，getattr 永远取到空字典，
   于是 protocol 配置读不到、静默走默认。
2. `max_segment_seconds` —— 代码读的是 `segment_seconds`。库里写成前者时
   读不到 → 退回默认 60.0 → **行为恰好正确**，所以短音频测试照样通过。

第二个尤其阴：它在"配置看起来设了 600、实际生效 60"的状态下跑了一段时间。
要是有人哪天把键名"修正"成 segment_seconds 但保留 600 这个值，长音频就又
开始静默截断（spark-asr 约 120-130s 封顶，HTTP 仍是 200）。

## 这个文件钉的

  - 代码实际读的键名集合（改名就红，提醒同步改 seed 脚本和线上 config）
  - seed 脚本写的键必须都在代码读的集合里（不许有死键）
  - 默认值必须安全：读不到 segment_seconds 时的回退值不能超过封顶线
"""

from __future__ import annotations

import inspect
import json
import re
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "packages" / "lea-rag" / "src")
)

from lea_rag.services.kb_processor import DocumentProcessor  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

#: spark-asr 实测约 120-130s 之后静默截断（见 scripts/probe_base_asr_cap.py）。
#: 分段长度的默认值必须明显小于它。
TRUNCATION_FLOOR = 120


def _keys_read_by_code() -> set[str]:
    """从源码里抠出 extra.get("…") 的键名。"""
    src = inspect.getsource(DocumentProcessor._asr_transcribe)
    return set(re.findall(r'extra\.get\(\s*"([^"]+)"', src))


def test_code_reads_the_expected_keys():
    """钉住键名集合。改名时这条会红 —— 提醒同步改 seed 脚本和线上 config。"""
    assert _keys_read_by_code() == {
        "protocol",
        "timeout",
        "segment_seconds",
        "language",
    }


def test_seed_script_writes_no_dead_keys():
    """seed 脚本写进 config 的键，必须都是代码会读的。

    写了代码不读的键 = 配置看着设了、实际没生效，而且不报错。
    """
    seed = REPO / "scripts" / "seed_asr_model.py"
    if not seed.exists():
        return

    src = seed.read_text(encoding="utf-8")
    m = re.search(r"CONFIG\s*=\s*\{(.*?)\n\}", src, re.S)
    assert m, "seed_asr_model.py 里找不到 CONFIG 字面量，这条测试该更新了"

    written = set(re.findall(r'"([^"]+)"\s*:', m.group(1)))
    dead = written - _keys_read_by_code()
    assert not dead, (
        f"seed 脚本写了代码不读的键: {sorted(dead)}。"
        "这类键不会报错，只会让配置静默失效。"
    )


def test_segment_seconds_default_is_below_the_truncation_floor():
    """读不到 segment_seconds 时的回退值必须安全。

    这个默认值是最后一道防线：键名写错、config 为空、新建的模型行没填，
    全都落到它身上。它要是大于封顶线，长音频就静默丢内容。
    """
    src = inspect.getsource(DocumentProcessor._asr_transcribe)
    m = re.search(r'extra\.get\(\s*"segment_seconds"\s*,\s*([0-9.]+)\s*\)', src)
    assert m, "找不到 segment_seconds 的默认值"

    default = float(m.group(1))
    assert default < TRUNCATION_FLOOR, (
        f"默认分段 {default}s 不小于实测封顶线 {TRUNCATION_FLOOR}s —— "
        "配置缺失时会静默截断"
    )


def test_timeout_default_covers_a_long_segment():
    """timeout 要够一段音频转写完。

    60s 音频在 spark-asr 上实测 3-5s 返回，但排队和重传要留余量。
    timeout 太小的表现是"长视频转到一半抛错"，和服务故障分不开。
    """
    src = inspect.getsource(DocumentProcessor._asr_transcribe)
    m = re.search(r'extra\.get\(\s*"timeout"\s*,\s*([0-9.]+)\s*\)', src)
    assert m
    assert float(m.group(1)) >= 60, "timeout 默认值太小"


def test_config_field_is_config_not_extra():
    """字段名是 config。RetrievalModel 没有 extra 列 —— 写错了不报错，
    只会永远取到空字典，于是所有 ASR 配置静默失效。
    """
    from lea_rag.db.models import RetrievalModel

    cols = {c.name for c in RetrievalModel.__table__.columns}
    assert "config" in cols
    assert "extra" not in cols, "加了 extra 列的话，_asr_transcribe 要跟着改"


def test_deployed_config_shape_is_documented():
    """线上那份 config 的形状记在这里，改线上配置时对照。

    实际值（2026-10-22 验过，ASR 10/10 关键词）：
        {"language": "zh_cn", "protocol": "base_pcm", "segment_seconds": 60}
    """
    deployed = {"language": "zh_cn", "protocol": "base_pcm", "segment_seconds": 60}

    assert set(deployed) <= _keys_read_by_code(), "线上 config 有死键"
    assert deployed["segment_seconds"] < TRUNCATION_FLOOR
    assert deployed["protocol"] == "base_pcm", (
        "基座走 base_pcm（JSON + base64 裸 PCM）。openai 那条是 multipart，"
        "指向基座会被拒"
    )
    # 能 json 往返 —— config 列是 JSON 类型
    assert json.loads(json.dumps(deployed)) == deployed
