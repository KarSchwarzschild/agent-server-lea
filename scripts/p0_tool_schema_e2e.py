"""P0.0 验收：用户自定义 api 工具的 schema 真的到了模型面前。

    python scripts/p0_tool_schema_e2e.py [agent_id]

前置：lea-config:8500 / lea-tools:8400 / lea-agent:8100 都在跑，agent 绑了 get_weather。

## 为什么这一条必须真跑

改的是一个 struct tag。单测钉住了"解出来的 schema 非空"，但那是在**我们自己造的**
payload 上解的。真链路上还隔着 lea-config 的序列化、HTTP、以及最关键的一段：模型
到底能不能把参数**发出来**。

判据不是"请求成功" —— 空 schema 那版也会成功：模型会调一次 get_weather()，网关那边
jinja 把 {{ latitude }} 渲染成空串，open-meteo 返 400，模型接着道个歉说查不到。全程
HTTP 200。

所以判据是三条一起看：

    ① tool_calls 里有 get_weather
    ② 它的入参里 latitude / longitude 都是**数字**（这一条是 schema 到没到的直接证据）
    ③ 回复里有真实温度

只看 ③ 也不行 —— 模型编一个温度出来毫不费力。
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _dbconn as d

AGENT_URL = "http://127.0.0.1:8100/api/chat/stream"
SECRET = d.env()["SECRET_KEY"]  # 从仓库根 .env 读，别写死 —— 写死就是把密钥提交进库
TENANT = "default"
USER = "p0-schema-user"

# ⚠️ 提问里**不要**出现参数名或坐标。
#
# 第一版写的是"用 get_weather 查，北京的坐标是纬度 39.9042、经度 116.4074"——
# 那等于把 schema 的内容口述给模型。实测两个版本都能答对，只是坏的那版把经纬度
# 传成了**字符串**。也就是说空 schema 的后果不是「传不出参数」（我一开始是这么写
# 的，不对），而是「全靠猜」：
#
#     参数名   从工具名和描述里猜。get_weather + latitude/longitude 太好猜了，
#              换成 q / sku_id / dept_code 这种就没戏
#     类型     没有约束，数字会传成字符串。上游宽容就蒙混过关，严格就 400
#     必填     不强制，模型可以漏
#
# 所以判据要看**类型**，而不是"调没调成"。中性问法能让"猜"这件事显出来。
QUESTION = "北京现在天气怎么样？"


def mint_token() -> str:
    import jwt

    return jwt.encode(
        {"sub": USER, "tenant_id": TENANT, "exp": int(time.time()) + 3600},
        SECRET,
        algorithm="HS256",
    )


def send(session_id: str, agent_id: int, message: str, token: str) -> int:
    import requests

    r = requests.post(
        AGENT_URL,
        json={"session_id": session_id, "agent_id": agent_id, "message": message},
        headers={"Authorization": f"Bearer {token}"},
        stream=True,
        timeout=180,
    )
    if r.status_code != 200:
        print(f"  HTTP {r.status_code}: {r.text[:300]}")
        return r.status_code
    for _ in r.iter_lines(decode_unicode=True):
        pass  # 排空流；真相从库里读，SSE 的事件形状是另一回事
    return 200


def rows(conn, session_id: str) -> list[tuple[str, str]]:
    # agent_messages 只有 (id, session_id, tenant_id, role, message, created_at) ——
    # 没有 extra 列。工具调用就在 message 的 content 块里，不在别处。
    with conn.cursor() as cur:
        cur.execute(
            "SELECT role, message FROM agent_messages "
            "WHERE tenant_id=%s AND session_id=%s ORDER BY id",
            (TENANT, session_id),
        )
        return cur.fetchall()


def text_of(msg: dict) -> str:
    return "".join(
        b.get("text", "") for b in (msg.get("content") or []) if isinstance(b, dict)
    )


def tool_uses(msg: dict) -> list[tuple[str, dict]]:
    """从一条 assistant 消息里抽 (工具名, 入参)。"""
    out = []
    for b in msg.get("content") or []:
        if isinstance(b, dict) and b.get("type") == "tool_use":
            out.append((b.get("name", ""), b.get("input") or {}))
    return out


def main() -> int:
    d.utf8_stdout()
    agent_id = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    session_id = f"p0schema-{uuid.uuid4().hex[:12]}"
    conn = d.connect()

    print(f"会话 {session_id}  agent={agent_id}")
    print(f"问：{QUESTION}\n")

    try:
        t0 = time.time()
        code = send(session_id, agent_id, QUESTION, mint_token())
        print(f"HTTP {code}  {time.time()-t0:.1f}s\n")
        if code != 200:
            return 1

        calls, reply = [], ""
        for role, raw in rows(conn, session_id):
            msg = json.loads(raw)
            if role == "assistant":
                calls.extend(tool_uses(msg))
                if t := text_of(msg).strip():
                    reply = t

        print(f"工具调用 {len(calls)} 次：")
        for name, params in calls:
            print(f"  {name}({json.dumps(params, ensure_ascii=False)})")
        print(f"\n回复：{reply[:200]}")

        weather = [(n, p) for n, p in calls if n == "get_weather"]
        if not weather:
            print("\n✗ 模型没调 get_weather —— 工具没到它面前，或者它选择不调。")
            return 1

        _, params = weather[0]
        print()
        ok = True
        for key in ("latitude", "longitude"):
            v = params.get(key)
            if isinstance(v, (int, float)):
                print(f"✓ {key} = {v}（数字）")
            else:
                print(f"✗ {key} = {v!r} —— 参数没传出来，schema 是空的")
                ok = False

        if not ok:
            print("\n✗ 模型调了工具但传不出参数 —— 正是空 schema 的症状。")
            return 1

        # 温度是最后一格：参数传对了，网关才可能拿回真数据。
        import re

        if re.search(r"\d+(\.\d+)?\s*(°|度|摄氏)", reply):
            print("\n✓ 参数带类型、工具真调通、回复里带回了实测温度。")
            print("  对照：tag 错的那版同一个问题传的是字符串 "
                  '"39.9042"，而且**照样答对了** —— get_weather 的参数名太好猜、'
                  "open-meteo 又收字符串。所以这条测试盯的是类型，不是成败。")
            return 0
        print("\n⚠ 参数传对了，但回复里没看到温度。工具结果可能没回来，看 .p0agent.log。")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
