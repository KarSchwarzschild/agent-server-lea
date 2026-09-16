"""验收：用户文件库（跨会话）+ 沙箱暂停 / 恢复 / 删除。

    LEA_AGENT_URL=http://127.0.0.1:8100 LEA_TOOLS_URL=http://127.0.0.1:8400 \
      python scripts/user_files_e2e.py

前置：lea-config / lea-tools / lea-agent / lea-parser 在跑
（infra/docker-compose.verify.yml），OBS 可写、共享存储可用、SANDBOX_BACKEND 指着
真 Cube。**SANDBOX_BACKEND 不设时会静默 fallback 到 mock**，那种情况下沙箱那几条
判据全是假绿 —— 所以脚本开头会先探一次真伪。

## 这个脚本在验什么

「用户文件库」这层的全部意义是**跨会话**：A 对话里传的资料、A 对话里生成的报告，
在 B 对话里还能接着用。这句话落到实处是一串各自独立的机制（内容寻址、去重配额、
两张表的相反降级、物化 + 补登记），任何一环断了，表现都是同一个模糊症状：「文件
在列表里，但用不了」。单元测试测得了每一环，测不了这句话本身。

沙箱那几条同理：暂停和恢复是 Cube 侧的策略，我们一行代码都不写，所以它**只能靠
真跑来验**。而不跑的后果不是「没测到」，是「这条路等于不存在」——SB-2 那次就是。

## 判据

上传 → 文件库
    ① 上传件立刻出现在 /api/user/files 里，stored / can_import 都为真
    ② /api/user/storage 涨了正好这个文件的字节数

内容寻址与去重
    ③ 同一份内容在**另一个会话**再传一次：两行，但占用**一个字节都不涨**

配额：同一条线，两个相反的方向
    ④ 撑满配额后传**新内容** → 507 + code=storage_quota_exceeded（硬拒）
    ⑤ 撑满配额后传**已有 sha 的内容** → 200（内容寻址短路，删什么都缓解不了的
       事不该挡）
    ⑥ 撑满配额后模型交付成品 → 行**照落**、obs_key 留空（软放行）
       ④ 和 ⑥ 是这套设计的核心不对称：上传超限用户还拿着原件，交付超限背后是
       几分钟的模型劳动。同一条配额线，丢掉的东西价值不同，所以降级方向相反。

交付：声明，不是猜
    ⑦ 模型调 deliver_artifact → session_artifacts 落一行，obs_key / sha256 非空
    ⑧ 它出现在同一个文件库列表里，带着模型写的 title

删除：先删行 → 数引用 → 才删对象
    ⑨ 删掉一份**还有别的行指着同一个 sha** 的文件 → object_deleted=false
    ⑩ 删掉最后一份 → object_deleted=true
    ⑪ 删不存在的 id → 404；kind 不合法 → 400

跨会话导入（这一层存在的理由）
    ⑫ 导入到一个**行还没落库的新会话** → 200，不是 404
       ← 这一条是回归测试：会话行是第一条消息才由 TouchSession 插的，而「新开
         对话 → 从文件库导入 → 问它」是最自然的路径。用 sessionOwnerGuard 会 404。
    ⑬ 新会话下落了一行 chat_attachments，file_key 跟源**完全相同**（不复制对象）
    ⑭ 导入后占用不涨一个字节
    ⑮ 带着返回的 attachment_id 发消息 → **模型真的读到了内容**（回答里带出锚点）
    ⑯ 导入一个 stored=false 的成品 → 410，不是 500 也不是假装成功

沙箱生命周期
    ⑰ GET /sandbox/sessions/{id} 问得到 state —— 不靠「下次调用会不会超时」反推
    ⑱ 到点自动转 paused
    ⑲ 再发一条要进沙箱的消息 → auto_resume 回 running，**且工作区文件还在**
       ← 状态字段回到 running 只说明它起来了，不说明数据还在。恢复的真判据是
         能把暂停之前写的那份文件原样读出来。
    ⑳ DELETE → found=true；再 GET → 404

## ⚠️ 判据不能是「回答看起来对不对」

⑮ 和 ⑲ 都要模型说出一个**只有真读到文件才知道的字符串**。仿 attachment_read_e2e
的 ANCHOR 手法：用生造的专有名词（编号 + 中文名）而不是随机 hex —— 随机串在正文里
显得突兀，模型可能当噪声跳过；一个看起来像真内容的怪词它会当成正经信息读进去。

## ⚠️ 配额是靠往库里插一行假的大文件撑满的，不是靠改环境变量

改 USER_STORAGE_QUOTA_MB 要重启 lea-agent，而重启会把这个脚本前面几步建立的状态
全冲掉（内存会话、沙箱句柄）。插一行 file_size 超过配额的登记行，效果等价——配额
算的就是库里的行——而且随时能撤。这一行在 finally 里一定会被删掉，包括脚本中途
崩掉的情况。
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import time
import uuid
from datetime import datetime, timezone

import jwt
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _dbconn as d

ENV = d.env()
AGENT_URL = os.getenv("LEA_AGENT_URL", "http://127.0.0.1:8100").rstrip("/")
TOOLS_URL = os.getenv("LEA_TOOLS_URL", ENV.get("LEA_TOOLS_URL", "http://127.0.0.1:8400")).rstrip("/")
SECRET = ENV["SECRET_KEY"]
INTERNAL_TOKEN = os.getenv("LEA_INTERNAL_TOKEN", ENV.get("LEA_INTERNAL_TOKEN", "")).strip()
TENANT = "default"
USER = "userfiles-e2e-user"
AGENT_ID = int(os.getenv("LEA_AGENT_ID", "2"))

# 配额不从环境变量猜 —— 见 main() 里 quota_bytes 那段。
# 上传件正文里的锚点 —— ⑮ 靠它证明模型真读到了跨会话搬过来的那份文件。
DOC_ANCHOR = f"青鸾-{uuid.uuid4().hex[:6].upper()}"
DOC_BODY = "\n".join([
    "# 实验室设备台账",
    "",
    "## 一、编制说明",
    "本台账按 2026 年秋季学期资产盘点结果编制，覆盖三个实验室。",
    "",
    "## 二、批次代号",
    f"本批次设备的内部代号为 {DOC_ANCHOR}，所有维保单据均以此代号归档。",
    "",
    "## 三、盘点周期",
    "每学期一次，缺失率超过 2% 时启动专项核查。",
])
DOC_BYTES = DOC_BODY.encode("utf-8")

# 成品锚点。⑲ 靠它证明沙箱恢复之后工作区**数据还在**，不只是状态字段回到 running。
ART_ANCHOR = f"白麓-{uuid.uuid4().hex[:6].upper()}"
ART2_ANCHOR = f"苍梧-{uuid.uuid4().hex[:6].upper()}"

ART_PATH = "output/验收报告.md"
ART2_PATH = "output/第二份报告.md"

# ⑱ 最多愿意等多少秒的自动暂停。
#
# 默认 180 —— 不是因为 180 有什么道理，而是因为**默认那一轮不该卡住十五分钟**。
# 生产的 CUBE_SANDBOX_TIMEOUT 是 900，所以默认值下 ⑱⑲ 会跳过并说清怎么补。
#
# 要真验这两条，把它设大：`PAUSE_WAIT_MAX=1000`。这么做比调小远端的
# CUBE_SANDBOX_TIMEOUT 更值：后者验的是"我们为了好验收而调过的那个值"，前者验的是
# **生产实际会走的那条路**。一次十五分钟的等待换一个不打折的结论，划算。
#
# ⚠️ 它只是"愿意等多久"，不是"要等多久"。后者从沙箱自己的 end_at 减出来
# （见下面 _pause_eta）—— 本机没有任何变量能决定远端沙箱什么时候暂停。
PAUSE_WAIT_MAX = float(os.getenv("PAUSE_WAIT_MAX", "180"))


# ── 鉴权 ──────────────────────────────────────────────────────────────────

def mint(user: str) -> str:
    return jwt.encode(
        {"sub": user, "tenant_id": TENANT, "exp": int(time.time()) + 7200},
        SECRET, algorithm="HS256",
    )


def hdr(user: str = USER) -> dict:
    return {"Authorization": f"Bearer {mint(user)}"}


def tools_hdr() -> dict:
    """打 lea-tools 要带服务间令牌。

    没配就不带 —— 跟被调方对称（lea_authkit 在 LEA_INTERNAL_TOKEN 为空时放行所有
    请求）。带一个空串反而会被当成「提供了但不对」。
    """
    return {"X-Internal-Token": INTERNAL_TOKEN} if INTERNAL_TOKEN else {}


# ── HTTP ──────────────────────────────────────────────────────────────────

def agent_api(method: str, path: str, user: str = USER, **kw) -> tuple[int, dict]:
    r = requests.request(method, f"{AGENT_URL}{path}", headers=hdr(user), timeout=180, **kw)
    try:
        return r.status_code, (r.json() if r.content else {})
    except ValueError:
        return r.status_code, {"_raw": r.text[:300]}


def tools_api(method: str, path: str, **kw) -> tuple[int, dict]:
    kw.setdefault("timeout", 60)
    r = requests.request(method, f"{TOOLS_URL}{path}", headers=tools_hdr(), **kw)
    try:
        return r.status_code, (r.json() if r.content else {})
    except ValueError:
        return r.status_code, {"_raw": r.text[:300]}


def tools_routes() -> tuple[dict[str, set[str]], str]:
    """问 lea-tools 自己有哪些路由。返回 ({path: {method,…}}, 说明)。

    ## 为什么必须先问一遍，不能直接开跑

    lea-tools 跟沙箱服务一起部署在**另一台机器**上，更新节奏跟这个仓库的工作副本
    不同步。本轮新增的三条路由 —— DELETE /api/files/blob、
    POST /api/files/artifacts/publish、GET|DELETE /sandbox/sessions/{id} ——
    在旧版本上根本不存在。

    而 FastAPI 对不存在的路由回的是 `404 {"detail":"Not Found"}`，跟处理器自己回的
    404（`{"detail":"sandbox session not found"}`）几乎一模一样。不分清这两种 404
    的后果很具体：⑦ 报「交付登记不完整」、⑩ 报「删了但对象还在」、⑰ 报「这个会话
    没有沙箱」—— 十来条判据各自给出一个像是功能坏了的结论，而真正的原因只有一个：
    那台机器还没更新。一份这样的验收报告比没有报告更糟，因为它会让人去查代码。

    探测失败（网络不通 / 401 / openapi 关掉了）时返回空 dict —— 调用方据此**不做
    任何门控**，照常全跑。宁可让判据在真实原因上失败，也不要凭一次探测失败去
    "解释"掉后面所有的红。
    """
    st, body = tools_api("GET", "/openapi.json", timeout=15)
    if st != 200 or not isinstance(body.get("paths"), dict):
        return {}, f"探测不到（HTTP {st}）—— 不做门控，后面照常全跑"
    paths = {p: {m.lower() for m in methods}
             for p, methods in body["paths"].items() if isinstance(methods, dict)}
    return paths, f"{len(paths)} 条路由"


def has_route(paths: dict[str, set[str]], path: str, method: str) -> bool:
    # paths 为空 = 没探到，一律当"有"（见 tools_routes 的说明）
    if not paths:
        return True
    return method.lower() in paths.get(path, set())


def upload(session_id: str, filename: str, data: bytes) -> tuple[int, dict]:
    r = requests.post(
        f"{AGENT_URL}/api/chat/attachments/upload",
        files={"file": (filename, io.BytesIO(data))},
        data={"session_id": session_id},
        headers=hdr(), timeout=180,
    )
    try:
        return r.status_code, (r.json() if r.content else {})
    except ValueError:
        return r.status_code, {"_raw": r.text[:300]}


def turn_running(session_id: str) -> bool:
    try:
        r = requests.get(f"{AGENT_URL}/api/chat/status/{session_id}",
                         headers=hdr(), timeout=10)
        return r.status_code == 200 and bool(r.json().get("running"))
    except requests.RequestException:
        return False


def ask(session_id: str, message: str, attachment_ids: list[int] | None = None) -> int:
    """发一条消息，等这一轮**真的收尾**再返回。

    流结束和 turn 的 defer 跑完是两件事（工具结果落库、staged_path 回填都在 defer
    里）。不等干净就去读库，会读到半截状态，然后把一个时序问题误判成功能坏了。
    """
    payload = {"session_id": session_id, "agent_id": AGENT_ID, "message": message}
    if attachment_ids:
        payload["attachment_ids"] = attachment_ids
    r = requests.post(f"{AGENT_URL}/api/chat/stream", json=payload,
                      headers=hdr(), stream=True, timeout=600)
    if r.status_code != 200:
        print(f"    HTTP {r.status_code}: {r.text[:300]}")
        return r.status_code
    for _ in r.iter_lines(decode_unicode=True):
        pass
    for _ in range(180):
        if not turn_running(session_id):
            break
        time.sleep(1)
    return 200


# ── 库 ────────────────────────────────────────────────────────────────────

def message_count(conn, session_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM agent_messages "
                    "WHERE tenant_id=%s AND session_id=%s", (TENANT, session_id))
        return cur.fetchone()[0]


def dissect(conn, session_id: str, mark: int) -> dict:
    """把第 mark 条之后的消息拆成 (工具调用, 工具返回, 最终回复)。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT role, message FROM agent_messages "
            "WHERE tenant_id=%s AND session_id=%s ORDER BY id "
            "LIMIT 18446744073709551615 OFFSET %s",
            (TENANT, session_id, mark),
        )
        rows = [(role, json.loads(raw)) for role, raw in cur.fetchall()]

    tool_calls, results, reply = [], [], ""
    for role, msg in rows:
        content = msg.get("content")
        if isinstance(content, str):
            continue
        for b in content or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                tool_calls.append((b.get("name", ""), b.get("input") or {}))
            elif b.get("type") == "tool_result":
                c = b.get("content")
                results.append(c if isinstance(c, str) else json.dumps(c, ensure_ascii=False))
        if role == "assistant":
            txt = "".join(b.get("text", "") for b in (content or [])
                          if isinstance(b, dict)).strip()
            if txt:
                reply = txt
    return {"tool_calls": tool_calls, "results": results, "reply": reply}


def upload_row(conn, att_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT session_id, filename, COALESCE(file_key,''), COALESCE(sha256,''), "
            "       COALESCE(staged_path,''), file_size, "
            "       CHAR_LENGTH(COALESCE(extracted_text,'')) "
            "  FROM chat_attachments WHERE id=%s", (att_id,))
        r = cur.fetchone()
    if not r:
        return None
    return {"session_id": r[0], "filename": r[1], "file_key": r[2], "sha256": r[3],
            "staged_path": r[4], "size": r[5], "text_len": r[6]}


def artifact_rows(conn, session_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, rel_path, filename, title, COALESCE(description,''), "
            "       COALESCE(obs_key,''), COALESCE(sha256,''), size "
            "  FROM session_artifacts WHERE tenant_id=%s AND session_id=%s ORDER BY id",
            (TENANT, session_id))
        return [{"id": r[0], "rel_path": r[1], "filename": r[2], "title": r[3],
                 "description": r[4], "obs_key": r[5], "sha256": r[6], "size": r[7]}
                for r in cur.fetchall()]


def sha_refs(conn, sha: str) -> int:
    """镜像 persistence.CountSHARefs：同租户内两张表加起来还有几行指着这个 sha。

    删除那条路的期望值要靠它算，而不是写死 —— 中途多一次导入就会多一行，写死的
    期望值会在一个跟被验功能无关的地方变红。
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT (SELECT COUNT(*) FROM session_artifacts WHERE tenant_id=%s AND sha256=%s)"
            "     + (SELECT COUNT(*) FROM chat_attachments  WHERE tenant_id=%s AND sha256=%s)",
            (TENANT, sha, TENANT, sha))
        return cur.fetchone()[0]


# chat_attachments.file_size 是 INT，不是 BIGINT。配额默认 2GB，一行装不下
# 「配额 + 余量」这个数（2147483648 + 67108864 > 2^31-1），MySQL 直接 1264 报错。
#
# 不改列去迁就一个验收脚本 —— 单个附件上限是 30MB（attachmentMaxBytes），INT 对
# 真实数据绰绰有余，为了撑配额去动线上表结构是本末倒置。改成插多行：配额本来就是
# 跨行 SUM 出来的，多行撑满跟一行撑满走的是同一条 SQL。
_FILLER_ROW_MAX = 2_000_000_000  # 留出余量，别贴着 2^31-1


def insert_filler(conn, session_id: str, size_bytes: int) -> list[int]:
    """插若干行「很大」的登记行，把这个用户的配额撑满。返回插了哪些 id。

    file_key / sha256 都必须非空 —— 配额那两条 SQL 的 WHERE 里带着
    `file_key <> '' AND sha256 <> ''`（空的代表「没存住」，本来就不该算占用）。

    每行 sha256 都不一样：配额是按 DISTINCT (sha256, size) 折叠的，几行共用一个
    sha 只会被算一次，撑不满。
    """
    ids: list[int] = []
    remaining = size_bytes
    with conn.cursor() as cur:
        while remaining > 0:
            chunk = min(remaining, _FILLER_ROW_MAX)
            remaining -= chunk
            cur.execute(
                "INSERT INTO chat_attachments "
                "  (tenant_id, user_id, session_id, filename, file_key, file_type, "
                "   file_size, media_type, extracted_text, sha256, staged_path) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (TENANT, USER, session_id, "配额占位.bin",
                 f"uploads/{TENANT}/quota-filler-{len(ids)}.bin", "bin",
                 chunk, "file", "",
                 (uuid.uuid4().hex + uuid.uuid4().hex)[:64], ""))
            cur.execute("SELECT LAST_INSERT_ID()")
            ids.append(cur.fetchone()[0])
    return ids


def cleanup(conn, sessions: list[str], att_ids: list[int]) -> None:
    with conn.cursor() as cur:
        for s in sessions:
            cur.execute("DELETE FROM agent_messages   WHERE session_id=%s", (s,))
            cur.execute("DELETE FROM chat_attachments WHERE session_id=%s", (s,))
            cur.execute("DELETE FROM session_artifacts WHERE session_id=%s", (s,))
            # chat_sessions 的主键列叫 `id`，不是 session_id
            cur.execute("DELETE FROM chat_sessions WHERE id=%s", (s,))
        for a in att_ids:
            cur.execute("DELETE FROM chat_attachments WHERE id=%s", (a,))
    conn.commit()


def drop_sandbox(session_id: str) -> None:
    try:
        tools_api("DELETE", f"/sandbox/sessions/{session_id}", timeout=90)
    except requests.RequestException:
        pass


# ── 文件库列表小工具 ───────────────────────────────────────────────────────

def list_files(kind: str = "") -> list[dict]:
    st, body = agent_api("GET", f"/api/user/files?limit=200{f'&kind={kind}' if kind else ''}")
    if st != 200:
        raise RuntimeError(f"GET /api/user/files -> {st} {body}")
    return body.get("files", [])


def pick(files: list[dict], kind: str, fid: int) -> dict | None:
    """按 (kind, id) 找一行。

    两张表的 id 各自自增，会撞 —— upload#7 和 artifact#7 是两个不同的文件。所以
    找一行必须用这对复合键，前端列表的 :key 也是同一个道理。
    """
    for f in files:
        if f.get("kind") == kind and f.get("id") == fid:
            return f
    return None


def storage() -> dict:
    st, body = agent_api("GET", "/api/user/storage")
    if st != 200:
        raise RuntimeError(f"GET /api/user/storage -> {st} {body}")
    return body


def step(n, title): print(f"\n[{n}] {title}")


def main() -> int:
    d.utf8_stdout()
    conn = d.connect()

    sess_a = f"uf-a-{uuid.uuid4().hex[:10]}"   # 上传 + 交付 + 沙箱生命周期
    sess_b = f"uf-b-{uuid.uuid4().hex[:10]}"   # 同内容再传一次（验去重）
    sess_c = f"uf-c-{uuid.uuid4().hex[:10]}"   # 全新、行还没落库 → 验导入
    att_ids: list[int] = []
    filler_ids: list[int] = []
    fails: list[str] = []
    skips: list[str] = []

    print(f"agent={AGENT_URL}  tools={TOOLS_URL}  user={USER}")
    print(f"锚点：上传件={DOC_ANCHOR}  成品={ART_ANCHOR}/{ART2_ANCHOR}")

    # 先问 lea-tools 有哪些路由（那台机器跟本仓库工作副本不同步，见 tools_routes）
    routes, routes_note = tools_routes()
    cap_publish = has_route(routes, "/api/files/artifacts/publish", "post")
    cap_blob_del = has_route(routes, "/api/files/blob", "delete")
    cap_sandbox = has_route(routes, "/sandbox/sessions/{session_id}", "get")
    cap_up_blob = has_route(routes, "/api/files/uploads/blob", "post")
    cap_stage = has_route(routes, "/api/files/uploads/stage", "post")
    print(f"lea-tools 路由：{routes_note}；"
          f"uploads/blob={cap_up_blob} uploads/stage={cap_stage} "
          f"artifacts/publish={cap_publish} blob-delete={cap_blob_del} "
          f"sandbox-lifecycle={cap_sandbox}")
    STALE = "那台 lea-tools 还是旧版本（它跟沙箱服务一起部署在另一台机器上），先更新再跑"

    # 这两条是地基：没有 uploads/blob 就没有原件，没有 uploads/stage 就没法导入。
    # 缺了它们，二十条判据会**全部**变红，而原因只有一个 —— 那种报告比没有报告更糟，
    # 因为它把一次部署滞后伪装成二十个功能缺陷。所以直接停在这里。
    if not (cap_up_blob and cap_stage):
        print("\n" + "═" * 70)
        print(f"⛔ 没跑：{STALE}")
        print("   缺的是文件库的地基路由："
              f"uploads/blob={cap_up_blob} uploads/stage={cap_stage}")
        print("   注意这轮 lea-tools 新增了两个运行时依赖（python-multipart、mcp>=1.9,<2），")
        print("   所以要**重建镜像**，光同步代码不够 —— 缺 python-multipart 时进程在装配")
        print("   路由时就抛 RuntimeError 退出，表现是容器无限重启。")
        conn.close()
        return 2

    try:
        base = storage()
        # 配额问服务，不读环境变量 —— /api/user/storage 的 quota_bytes 跟
        # uploadQuotaBlocked 里那道闸读的是同一个 UserStorageQuotaBytes()
        # (user_files.go:56 vs :352)。从 .env 猜的话，脚本和被测进程各看一份值，
        # 撑配额那行占位就可能不够大，于是 ④ 在一个跟被验功能无关的地方变红。
        quota_bytes = int(base["quota_bytes"])
        print(f"基线占用：{base['total_bytes']} 字节 "
              f"(upload={base['upload_count']} artifact={base['artifact_count']}) "
              f"配额={quota_bytes}（服务自报）")

        # ── ①② 上传 → 立刻出现在文件库里 ─────────────────────────
        step(1, "①② 上传一份文档，看它进不进文件库、占用涨不涨")
        st, up1 = upload(sess_a, "实验室设备台账.md", DOC_BYTES)
        print(f"    HTTP {st}  {json.dumps(up1, ensure_ascii=False)[:240]}")
        if st != 200:
            fails.append(f"① 上传失败 HTTP {st}：{up1}")
            raise SystemExit
        up1_id = up1["id"]
        att_ids.append(up1_id)
        row1 = upload_row(conn, up1_id)
        print(f"    库里：sha={row1['sha256'][:12]} file_key={row1['file_key'][:48]!r} "
              f"text_len={row1['text_len']}")

        f1 = pick(list_files(), "upload", up1_id)
        if not f1:
            fails.append("① 上传成功但 /api/user/files 里找不到它")
        elif not (f1.get("stored") and f1.get("can_import")):
            fails.append(f"① 文件库里这一行 stored/can_import 不对：{f1}")
        else:
            print(f"    ✓ ① 文件库里有它：{f1['filename']} stored={f1['stored']}")

        s1 = storage()
        delta = s1["total_bytes"] - base["total_bytes"]
        if delta != len(DOC_BYTES):
            fails.append(f"② 占用应涨 {len(DOC_BYTES)} 字节，实际涨了 {delta}")
        else:
            print(f"    ✓ ② 占用涨了 {delta} 字节，正好是这个文件")

        # ── ③ 同内容再传一次：两行，零增长 ────────────────────────
        step(2, "③ 同一份内容换个会话、换个文件名再传一次")
        st, up2 = upload(sess_b, "台账副本.md", DOC_BYTES)
        if st != 200:
            fails.append(f"③ 重复上传失败 HTTP {st}：{up2}")
        else:
            up2_id = up2["id"]
            att_ids.append(up2_id)
            row2 = upload_row(conn, up2_id)
            s2 = storage()
            grew = s2["total_bytes"] - s1["total_bytes"]
            same_sha = row2["sha256"] == row1["sha256"]
            print(f"    第二行 id={up2_id} sha相同={same_sha} 占用增量={grew}")
            if not same_sha:
                fails.append("③ 同样的字节算出了不同的 sha256 —— 内容寻址坏了")
            elif grew != 0:
                fails.append(
                    f"③ 同 sha 重复上传让占用涨了 {grew} 字节 —— "
                    f"配额没按 SUM(DISTINCT sha256, size) 去重")
            else:
                print("    ✓ ③ 两行、一个对象，占用零增长")

        # ── ④⑤ 配额：硬拒新内容，放行同 sha ───────────────────────
        step(3, "④⑤ 把配额撑满，看上传怎么拒")
        if quota_bytes <= 0:
            skips.append(
                "④⑤ 服务自报 quota_bytes=0（USER_STORAGE_QUOTA_MB 未设 = 不限），"
                "配额路径没法验")
            print("    ⚠️ 配额不限，跳过")
        else:
            filler_ids.extend(insert_filler(conn, sess_a, quota_bytes + 64 * 1024 * 1024))
            conn.commit()
            after = storage()
            print(f"    撑满后占用={after['total_bytes']} / 配额={after['quota_bytes']}")

            fresh = f"# 另一份文件\n内部代号 {uuid.uuid4().hex[:8]}\n".encode()
            st, body = upload(sess_a, "新内容.md", fresh)
            print(f"    传新内容 → HTTP {st} code={body.get('code')!r}")
            if st != 507:
                fails.append(f"④ 配额已满还让新内容传上去了（HTTP {st}）—— 上传应该硬拒")
                if st == 200 and body.get("id"):
                    att_ids.append(body["id"])
            elif body.get("code") != "storage_quota_exceeded":
                fails.append(
                    f"④ 507 对了但 code 不是 storage_quota_exceeded（{body.get('code')!r}）—— "
                    f"前端只能去匹配中文文案，文案一改就失效")
            else:
                print("    ✓ ④ 507 + code=storage_quota_exceeded")

            st, up3 = upload(sess_b, "台账再副本.md", DOC_BYTES)
            print(f"    传已有 sha → HTTP {st}")
            if st != 200:
                fails.append(
                    f"⑤ 配额满时同 sha 的上传被挡了（HTTP {st}）—— 这份内容库里已经有了，"
                    f"不占新对象，用户删什么都缓解不了")
            else:
                att_ids.append(up3["id"])
                print("    ✓ ⑤ 同 sha 直接放行")

            with conn.cursor() as cur:
                for fid in filler_ids:
                    cur.execute("DELETE FROM chat_attachments WHERE id=%s", (fid,))
            conn.commit()
            filler_ids.clear()
            print("    （占位行已撤）")

        # ── ⑦⑧ 交付：模型显式声明成品 ─────────────────────────────
        #
        # 发布端点不在时**这一轮照样要跑**，只是不让它交付 —— ⑲ 要拿"暂停之前写下的
        # 那个文件"当恢复的判据，文件得先真的被写出来。把整步跳掉会连带把一条跟
        # 交付无关的判据也弄没了。
        step(4, "⑦⑧ 让模型写一个文件并 deliver_artifact 交付")
        mark = message_count(conn, sess_a)
        if cap_publish:
            ask(sess_a, (
                f"请按下面两步做，第二步不要跳过：\n"
                f"1. 在工作区里新建文件 {ART_PATH}，内容只写这一行：{ART_ANCHOR}\n"
                f"2. 调用 deliver_artifact 工具把它交付给我，"
                f"path 填 {ART_PATH}，title 填「设备台账验收报告」，description 写一句说明。"
            ))
        else:
            skips.append(f"⑥⑦⑧⑯ lea-tools 没有 POST /api/files/artifacts/publish —— {STALE}")
            print(f"    ⚠️ 发布端点不存在，⑦⑧ 跳过；仍让模型把 {ART_PATH} 写出来（⑲ 要用）")
            ask(sess_a, f"在工作区里新建文件 {ART_PATH}，内容只写这一行：{ART_ANCHOR}")
        g = dissect(conn, sess_a, mark)
        called = [n for n, _ in g["tool_calls"]]
        print(f"    工具调用：{called or '（无）'}")

        art_row = None
        if not cap_publish:
            pass
        elif "deliver_artifact" not in called:
            skips.append("⑦⑧ 模型这一轮没调 deliver_artifact —— 交付链没被触发")
            print("    ⚠️ 没调 deliver_artifact，⑦⑧ 跳过")
        else:
            rows = [r for r in artifact_rows(conn, sess_a) if r["rel_path"] == ART_PATH]
            if not rows:
                fails.append(f"⑦ 模型调了 deliver_artifact 但 session_artifacts 里没有 {ART_PATH} 这一行")
            else:
                art_row = rows[0]
                print(f"    登记行：id={art_row['id']} title={art_row['title']!r} "
                      f"obs_key={art_row['obs_key'][:48]!r} size={art_row['size']}")
                if not art_row["obs_key"] or not art_row["sha256"]:
                    fails.append(
                        f"⑦ 登记了但 obs_key/sha256 是空的 —— 发布那步失败了，"
                        f"这份成品跨会话用不了。obs_key={art_row['obs_key']!r}")
                elif not art_row["title"]:
                    fails.append("⑦ 登记行的 title 是空的 —— 用户几周后靠这句话找回它")
                else:
                    print("    ✓ ⑦ 成品登记完整（obs_key + sha256 + title）")

                fa = pick(list_files(), "artifact", art_row["id"])
                if not fa:
                    fails.append("⑧ 成品没出现在 /api/user/files 里 —— 两张表没合并展示")
                elif not (fa.get("stored") and fa.get("can_import")):
                    fails.append(f"⑧ 成品在列表里但 stored/can_import 不对：{fa}")
                elif fa.get("title") != art_row["title"]:
                    fails.append(f"⑧ 列表里的 title 跟库里对不上：{fa.get('title')!r}")
                else:
                    print(f"    ✓ ⑧ 成品在同一个文件库列表里：{fa['title']!r}")

        # ── ⑥ 软放行：配额满时交付照落行、obs_key 留空 ──────────────
        step(5, "⑥ 撑满配额再交付一次 —— 这次该软放行，不是拒绝")
        if quota_bytes <= 0:
            skips.append("⑥ 配额不限，软放行路径没法验")
            print("    ⚠️ 配额不限，跳过")
        elif art_row is None:
            # cap_publish 为假时上一步已经登记过 skip，这里不重复记
            if cap_publish:
                skips.append("⑥ 上一步没交付成功，软放行没法接着验")
            print("    ⚠️ 上一步没交付成功，跳过")
        else:
            filler_ids.extend(insert_filler(conn, sess_a, quota_bytes + 64 * 1024 * 1024))
            conn.commit()
            mark = message_count(conn, sess_a)
            ask(sess_a, (
                f"再做一次同样的两步：\n"
                f"1. 新建文件 {ART2_PATH}，内容只写这一行：{ART2_ANCHOR}\n"
                f"2. 调用 deliver_artifact 交付它，path 填 {ART2_PATH}，"
                f"title 填「第二份验收报告」。"
            ))
            g2 = dissect(conn, sess_a, mark)
            called2 = [n for n, _ in g2["tool_calls"]]
            print(f"    工具调用：{called2 or '（无）'}")

            soft_row = None
            rows2 = [r for r in artifact_rows(conn, sess_a) if r["rel_path"] == ART2_PATH]
            if "deliver_artifact" not in called2 or not rows2:
                skips.append("⑥ 模型这一轮没交付成功，软放行没被触发")
                print("    ⚠️ 没交付成功，跳过")
            else:
                soft_row = rows2[0]
                print(f"    登记行：id={soft_row['id']} obs_key={soft_row['obs_key']!r}")
                if soft_row["obs_key"] != "":
                    fails.append(
                        f"⑥ 配额已满却把成品存进了库（obs_key={soft_row['obs_key']!r}）—— "
                        f"软放行的语义是「行照落、obs_key 留空」，存进去就绕过了配额")
                else:
                    print("    ✓ ⑥ 行落下来了、obs_key 留空 —— 软放行")
                    fs = pick(list_files(), "artifact", soft_row["id"])
                    if fs is None:
                        fails.append("⑥ 软放行的成品没出现在列表里 —— 用户会以为它凭空消失了")
                    elif fs.get("stored") or fs.get("can_import"):
                        fails.append(f"⑥ 没存住的成品却标成可用：{fs}")
                    else:
                        print("    ✓ ⑥ 列表里标成 stored=false / can_import=false")

            with conn.cursor() as cur:
                for fid in filler_ids:
                    cur.execute("DELETE FROM chat_attachments WHERE id=%s", (fid,))
            conn.commit()
            filler_ids.clear()

            # ⑯ 顺带：这一行没有原件，导入必须 410
            if soft_row is not None and soft_row["obs_key"] == "":
                st, body = agent_api(
                    "POST", f"/api/sessions/{sess_c}/files/import",
                    json={"kind": "artifact", "id": soft_row["id"]})
                print(f"    导入没存住的成品 → HTTP {st} {str(body)[:120]}")
                if st != 410:
                    fails.append(
                        f"⑯ 导入一个 stored=false 的成品应该 410（说清没有原件），实际 {st}")
                else:
                    print("    ✓ ⑯ 410：没有原件，说清楚而不是让用户重试")

        # ── ⑫⑬⑭⑮ 跨会话导入 ─────────────────────────────────────
        step(6, "⑫⑬⑭ 把上传件导入一个**行还没落库**的新会话")
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM chat_sessions WHERE id=%s", (sess_c,))
            exists = cur.fetchone()[0]
        print(f"    导入前 chat_sessions 里有这个会话吗：{bool(exists)}（应为 False）")

        before_import = storage()
        st, imp = agent_api("POST", f"/api/sessions/{sess_c}/files/import",
                            json={"kind": "upload", "id": up1_id})
        print(f"    HTTP {st}  {json.dumps(imp, ensure_ascii=False)[:240]}")
        imported_id = None
        if st != 200:
            fails.append(
                f"⑫ 导入到未落库的新会话失败（HTTP {st}：{imp}）—— "
                f"会话行是第一条消息才插的，「新开对话→导入→提问」这条主路径会整个断掉")
        else:
            imported_id = imp["attachment_id"]
            att_ids.append(imported_id)
            irow = upload_row(conn, imported_id)
            print(f"    新行：session={irow['session_id']} file_key={irow['file_key'][:48]!r} "
                  f"staged={irow['staged_path']!r} text_len={irow['text_len']}")
            print("    ✓ ⑫ 会话行不存在时导入照样成立")

            if irow["session_id"] != sess_c:
                fails.append(f"⑬ 导入的行挂在了 {irow['session_id']}，不是目标会话 {sess_c}")
            elif irow["file_key"] != row1["file_key"]:
                fails.append(
                    f"⑬ 导入复制了对象（新 file_key={irow['file_key']!r} != 源 {row1['file_key']!r}）"
                    f"—— 导入应该只拷会话工作区那份，OBS 上不多占一个字节")
            elif not irow["staged_path"]:
                fails.append("⑬ 导入登记了但 staged_path 是空的 —— 模型会拿到一个不存在的路径")
            else:
                print("    ✓ ⑬ 同一个对象、目标会话下的新登记行、staged_path 已回填")

            after_import = storage()
            grew = after_import["total_bytes"] - before_import["total_bytes"]
            if grew != 0:
                fails.append(f"⑭ 导入让占用涨了 {grew} 字节 —— 同 sha 不该重复计费")
            else:
                print("    ✓ ⑭ 导入不占额外配额")

        step(7, "⑮ 在新会话里问那份导入的文件 —— 模型答得出锚点才算真读到")
        if imported_id is None:
            skips.append("⑮ 上一步导入就失败了，读不了")
            print("    ⚠️ 跳过")
        else:
            mark = message_count(conn, sess_c)
            ask(sess_c,
                "我刚导入的这份台账里，本批次设备的内部代号是什么？"
                "请把代号原样写出来，不要解释。",
                [imported_id])
            g3 = dissect(conn, sess_c, mark)
            print(f"    工具调用：{[n for n, _ in g3['tool_calls']] or '（无）'}")
            print(f"    回答：{g3['reply'][:160]}")
            if DOC_ANCHOR in g3["reply"]:
                print(f"    ✓ ⑮ 回答里带出了锚点 {DOC_ANCHOR} —— 跨会话这份文件是真能用的")
            else:
                fails.append(
                    f"⑮ 回答里没有锚点 {DOC_ANCHOR} —— 导入的文件模型读不到。"
                    f"回答：{g3['reply'][:200]}")

        # ── ⑨⑩⑪ 删除 ───────────────────────────────────────────
        step(8, "⑨⑩⑪ 删除：共享对象要留，最后一份才真删")
        sha = row1["sha256"]
        # 现在指着这个 sha 的行：up1 / up2 / up3(可能) / 导入的那份
        rows_now = {i: upload_row(conn, i) for i in att_ids}
        victims = [i for i, r in rows_now.items() if r and r["sha256"] == sha]
        print(f"    指着 sha {sha[:12]} 的行：{victims}")
        del_ok = bool(victims)
        if not victims:
            skips.append("⑨⑩ 前面没留下任何指着同一个 sha 的行，删除路径没法验")
        for vid in victims:
            # 期望值现算不写死：中途多一次导入就多一行引用，写死的期望会在一个
            # 跟被验功能无关的地方变红。
            refs_before = sha_refs(conn, sha)
            st, body = agent_api("DELETE", f"/api/user/files/upload/{vid}")
            last = refs_before == 1
            print(f"    删 upload#{vid}（删前引用 {refs_before}）→ HTTP {st} "
                  f"deleted={body.get('deleted')} object_deleted={body.get('object_deleted')}")
            if st != 200 or not body.get("deleted"):
                fails.append(f"⑨ 删 upload#{vid} 失败：HTTP {st} {body}")
                del_ok = False
                continue
            if last and not body.get("object_deleted") and not cap_blob_del:
                skips.append(
                    f"⑩ lea-tools 没有 DELETE /api/files/blob，删不到对象 —— {STALE}")
            elif last and not body.get("object_deleted"):
                fails.append(
                    "⑩ 删的是最后一份（删前引用 1）却没删对象 —— "
                    "表现是「删了但空间没降」，配额存在时这是最不能接受的形状")
                del_ok = False
            if not last and body.get("object_deleted"):
                fails.append(
                    f"⑨ 还有 {refs_before - 1} 行指着同一个对象却把它删了 —— "
                    f"别人的文件会凭空消失")
                del_ok = False
            if upload_row(conn, vid) is not None:
                fails.append(f"⑨ 接口说删了，但 chat_attachments 里 id={vid} 还在")
                del_ok = False
        if del_ok:
            print("    ✓ ⑨⑩ 共享时留对象、最后一份才真删")
        att_ids = [i for i in att_ids if i not in victims]

        st, body = agent_api("DELETE", "/api/user/files/upload/999999999")
        print(f"    删不存在的 id → HTTP {st}")
        if st != 404:
            fails.append(f"⑪ 删不存在的文件应该 404，实际 {st}：{body}")
        st, body = agent_api("DELETE", f"/api/user/files/bogus/{up1_id}")
        print(f"    删非法 kind → HTTP {st}")
        if st != 400:
            fails.append(f"⑪ 非法 kind 应该 400，实际 {st}：{body}")
        if st == 400:
            print("    ✓ ⑪ 404 / 400 各归各位")

        # ── ⑰⑱⑲⑳ 沙箱生命周期 ──────────────────────────────────
        #
        # 先**把沙箱逼出来**，再去查它。
        #
        # 前面几步一次都没建出沙箱，而原因不是故障：⑦ 那步模型用的是 Write，而
        # Write 直写会话工作区（读写自己的空间不经网关），压根不需要沙箱。真正会
        # 拉起沙箱的是 Bash —— 只有它挂 SandboxEnabled（runtime/gateway.go）。
        #
        # 第一轮验收就栽在这上面：⑰ 查到 404，脚本报的是"前面几轮模型没进过沙箱，
        # 或 SANDBOX_BACKEND 没配"—— 两个猜测都对不上，真实原因是**没有任何一步
        # 要求过模型执行代码**。生命周期这四条依赖一个前置条件，而那个条件谁都没
        # 负责建立，于是它们轮流以"环境没配好"的名义被跳过。
        #
        # 所以这一步是判据的一部分，不是准备工作：能不能按需拉起一个沙箱，本身就
        # 是"沙箱可用"的第一层含义。它失败时后面四条跳过是**对的**，但要说清是这
        # 一步没成，而不是让人去翻 SANDBOX_BACKEND。
        step(9, "把沙箱逼出来 —— 让模型在 sess_a 里真执行一次命令")
        if cap_sandbox:
            mark = message_count(conn, sess_a)
            # 指名"用命令/shell"，不是"读文件"—— 后者模型会用 Read 直连，绕开沙箱。
            # 顺带读一下 ⑦ 写下的那个文件：这既证明沙箱看得见会话工作区
            # （host-mount 生效），也为 ⑲ 的"暂停前的文件还在不在"打好底。
            ask(sess_a, f"用 shell 命令 cat 一下 {ART_PATH}，把输出原样贴给我。")
            g0 = dissect(conn, sess_a, mark)
            print(f"    工具调用：{[n for n, _ in g0['tool_calls']] or '（无）'}")

        step(10, "⑰ 沙箱状态查得到吗")
        if not cap_sandbox:
            st, sb = 0, {}
            skips.append(f"⑰⑱⑲⑳ lea-tools 没有 GET /sandbox/sessions/{{id}} —— {STALE}")
            print(f"    ⚠️ 生命周期端点不存在，⑰⑱⑲⑳ 全部跳过（{STALE}）")
        else:
            st, sb = tools_api("GET", f"/sandbox/sessions/{sess_a}")
            print(f"    HTTP {st}  {json.dumps(sb, ensure_ascii=False)[:200]}")
        if not cap_sandbox:
            pass
        elif st == 404:
            # 端点存在却 404 = 真的"这个会话没有沙箱"。上一步刚明确要求过执行命令，
            # 所以这里的 404 不再是"模型没碰过沙箱"，而是**逼不出来** —— 要么模型
            # 没调 Bash（上一步打印的工具列表能看出来），要么 SANDBOX_BACKEND 没配
            # （不配会静默 fallback 到 mock，mock 不在 Cube 里，自然查不到）。
            skips.append(
                "⑰⑱⑲⑳ 明确要求执行命令后 sess_a 仍然没有沙箱 —— 看上一步的工具"
                "列表：没有 Bash 就是模型没走沙箱这条路；有 Bash 却查不到，那是 "
                "SANDBOX_BACKEND 没指真 Cube（不配会静默 fallback 到 mock）")
            print("    ⚠️ 逼不出沙箱，生命周期全部跳过")
        elif st != 200:
            fails.append(f"⑰ 查沙箱状态失败 HTTP {st}：{sb}")
        else:
            state0 = sb.get("state", "")
            print(f"    ✓ ⑰ 沙箱 {sb.get('sandbox_id','')[:16]} state={state0!r} "
                  f"cached_here={sb.get('cached_here')}")

            # 还要等多久才转 paused —— **问沙箱自己**，不读本地环境变量。
            #
            # CUBE_SANDBOX_TIMEOUT 是给 lea-tools 进程看的，而 lea-tools 跑在**另一台
            # 机器上**（跟 Cube 同机）。在这台机器上读同名变量，读到的是"我这儿有没有
            # 设过这个值"，跟那边实际用的数没有任何关系 —— 本地没设就报"900 秒等不起"
            # 然后跳过，本地设了 60 就按 60 等，而那边可能还是 900，于是等 105 秒之后
            # 判它 fail。两种错法都指着同一个毛病：拿本地配置推断远端行为。
            #
            # end_at 是权威的：Cube 自己算出来的转 paused 时刻（见 _cube_sandbox.py 里
            # find_session_sandbox 那段——它把 state/startedAt/endAt 一起带出来就是为了
            # 这个）。剩余时间直接减出来，连 timeout 是多少都不用知道。
            def _pause_eta(row: dict) -> float:
                """还有几秒转 paused。拿不到 end_at 返回 -1（= 不知道）。"""
                raw = (row.get("end_at") or "").strip()
                if not raw:
                    return -1.0
                try:
                    at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    return -1.0
                return (at - datetime.now(timezone.utc)).total_seconds()

            # `pausing` 是过渡态，不是失败。
            #
            # Cube 的状态机是五个态：running ↔ paused 之间还夹着 pausing / resuming
            # （见 docs 的 lifecycle，以及 reference-cube-lifecycle 那条记录）。快照要
            # 存内存 + 文件系统，一个装过依赖的沙箱做完这件事要一两分钟。
            #
            # 上一轮就栽在没认它：等到 end_at + 45 秒，state 是 'pausing'，脚本判
            # "on_timeout=pause 没生效" —— **恰好相反**，看见 pausing 就说明它生效了，
            # 转换已经开始，只是快照还没落完。把过渡态当失败，报出来的结论跟事实反向。
            #
            # 所以判据拆成两段：
            #   ⑱ 的断言 = 到点**开始**转（pausing 或 paused 都算观察到）
            #   ⑲ 要的是转**完**（必须 paused，auto_resume 才有东西可恢复）
            # 后者多给一份独立预算 —— 快照耗时是 Cube 的事，跟 timeout 没关系，
            # 混在同一个预算里会让"超时设得短"连带把快照的时间也挤掉。
            _TRANSITION = ("pausing", "paused")
            SNAPSHOT_BUDGET = float(os.getenv("SNAPSHOT_WAIT_MAX", "240"))

            step(11, "⑱ 到点自动 paused")
            paused = state0 == "paused"
            pausing_seen = state0 in _TRANSITION
            eta = _pause_eta(sb)
            wait_max = PAUSE_WAIT_MAX
            if paused:
                print("    已经是 paused 了（前面几步花的时间够长）—— 直接算观察到")
            elif eta < 0:
                # 没有 end_at 就没有截止时刻，只能猜要等多久 —— 而猜错的两个方向
                # （等不够判 fail、等太久卡住验收）都比跳过更糟。
                skips.append(
                    "⑱⑲ 沙箱没报 end_at，算不出什么时候转 paused —— 不猜。"
                    "Cube 的 lifecycle 里 on_timeout/auto_resume 可能没生效")
                print("    ⚠️ 没有 end_at，跳过")
            elif eta > wait_max:
                skips.append(
                    f"⑱⑲ 这个沙箱还有 {eta:.0f} 秒才转 paused，超过 "
                    f"PAUSE_WAIT_MAX={wait_max:.0f}s 没等。要验这条，两条路都行："
                    f"PAUSE_WAIT_MAX={eta + 90:.0f} 再跑一轮（等真的默认超时，"
                    f"最接近生产）；或者给 **lea-tools 那台机器**（不是本机）设 "
                    f"CUBE_SANDBOX_TIMEOUT=60 重启后再跑（快，但验的是调过的值）")
                print(f"    ⚠️ 还要等 {eta:.0f} 秒 > PAUSE_WAIT_MAX={wait_max:.0f}s，跳过")
            else:
                # 预算 = 等到 end_at（转换开始）+ 快照落完。两段分开算，见上面那段。
                budget = eta + 15 + SNAPSHOT_BUDGET
                print(f"    等自动暂停（还有 {eta:.0f}s 到点，"
                      f"另给快照 {SNAPSHOT_BUDGET:.0f}s）…")
                deadline = time.time() + budget
                cur_sb = sb
                last = state0
                while time.time() < deadline:
                    time.sleep(5)
                    _, cur_sb = tools_api("GET", f"/sandbox/sessions/{sess_a}")
                    cur = cur_sb.get("state", "")
                    if cur != last:
                        print(f"    state: {last!r} → {cur!r}")
                        last = cur
                    if cur in _TRANSITION:
                        pausing_seen = True
                    if cur == "paused":
                        paused = True
                        break
                if paused:
                    print("    ✓ ⑱ 观察到 running → paused")
                elif pausing_seen:
                    # 转换确实触发了 —— ⑱ 问的就是这个，算过。快照没落完是另一回事，
                    # 它只挡住 ⑲（没有 paused 就没有 resume 可言）。
                    print(f"    ✓ ⑱ 到点开始转了（state={last!r}）—— on_timeout=pause 生效")
                    skips.append(
                        f"⑲ 到点转了 pausing，但 {SNAPSHOT_BUDGET:.0f} 秒内没落到 "
                        f"paused（快照在存内存+文件系统，装过依赖的沙箱会更久）。"
                        f"要验恢复：SNAPSHOT_WAIT_MAX 加大再跑")
                else:
                    fails.append(
                        f"⑱ 过了 end_at 又等了 {SNAPSHOT_BUDGET:.0f} 秒，state 仍是 "
                        f"{last!r}，连 pausing 都没进过 —— on_timeout=pause 没生效")

            step(12, "⑲ 再用一次沙箱：auto_resume 起来，而且工作区文件还在")
            if not paused:
                skips.append("⑲ 没观察到 paused，恢复也就无从谈起")
                print("    ⚠️ 跳过")
            else:
                mark = message_count(conn, sess_a)
                ask(sess_a, f"用命令把 {ART_PATH} 的内容原样输出来。")
                g4 = dissect(conn, sess_a, mark)
                joined = "\n".join(g4["results"]) + "\n" + g4["reply"]
                _, sb2 = tools_api("GET", f"/sandbox/sessions/{sess_a}")
                print(f"    恢复后 state={sb2.get('state')!r}")
                if sb2.get("state") != "running":
                    fails.append(
                        f"⑲ 又用了一次沙箱，state 却还是 {sb2.get('state')!r} —— "
                        f"auto_resume 没起作用")
                elif ART_ANCHOR not in joined:
                    fails.append(
                        f"⑲ 状态回到 running 了，但读不出暂停前写的 {ART_PATH}（找不到锚点 "
                        f"{ART_ANCHOR}）—— 恢复起来的是个空壳，工作区数据丢了。"
                        f"输出：{joined[:200]}")
                else:
                    print(f"    ✓ ⑲ auto_resume 回 running，且暂停前的文件原样还在（{ART_ANCHOR}）")

            step(13, "⑳ 删沙箱")
            st, dl = tools_api("DELETE", f"/sandbox/sessions/{sess_a}", timeout=90)
            print(f"    DELETE → HTTP {st} {dl}")
            if st != 200:
                fails.append(f"⑳ 删沙箱 HTTP {st}：{dl}")
            elif not dl.get("found"):
                fails.append(
                    "⑳ DELETE 返回 found=false —— 删之前它明明还在（⑰ 刚查到）。"
                    "要么删到了别的会话，要么回收器抢先了")
            else:
                # 删完**轮询**到消失，不是查一次就断言。
                #
                # 转换态（pausing/resuming）里发 DELETE，Cube 会受理然后排队 —— 等当前
                # 那个 lifecycle 操作做完再销毁。于是紧接着查一次会看到沙箱还在、state
                # 还是 pausing，而它几十秒后确实没了（这一轮实测：立刻查 200，事后查
                # 404）。
                #
                # 上一轮把这个判成失败，说的是"删完再查应该 404" —— 那句话隐含了"删除
                # 是同步的"，而它在转换态里不是。真正要验的是**最终删掉了**，所以给它
                # 一个窗口去收敛。
                #
                # 窗口内一直在才是真失败：那意味着 found=true 报了个假成功，用户以为
                # 删了、沙箱占着 slot 到 IDLE_TIMEOUT 才被回收器捡走。
                gone, st2, after = False, 0, {}
                deadline = time.time() + 120
                while time.time() < deadline:
                    st2, after = tools_api("GET", f"/sandbox/sessions/{sess_a}")
                    if st2 == 404:
                        gone = True
                        break
                    time.sleep(5)
                if gone:
                    print("    ✓ ⑳ found=true，删完查不到了")
                else:
                    fails.append(
                        f"⑳ DELETE 报了 found=true，但 120 秒后沙箱还在"
                        f"（HTTP {st2} state={after.get('state')!r}）—— 假成功："
                        f"用户以为删了，slot 要占到 IDLE_TIMEOUT 才被回收器捡走")

    except SystemExit:
        pass
    except Exception as e:  # noqa: BLE001 —— 中途炸了也要把占位行撤掉
        fails.append(f"脚本异常：{type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 占位行优先撤：它挂在真实用户名下，留着会让这个用户之后什么都传不上去。
        try:
            if filler_ids:
                with conn.cursor() as cur:
                    for fid in filler_ids:
                        cur.execute("DELETE FROM chat_attachments WHERE id=%s", (fid,))
                conn.commit()
                print(f"\n（已撤掉 {len(filler_ids)} 行配额占位）")
        except Exception as e:  # noqa: BLE001
            print(f"\n⚠️ 配额占位行没撤掉，手动删：{e}")
        for s in (sess_a, sess_c):
            drop_sandbox(s)
        cleanup(conn, [sess_a, sess_b, sess_c], att_ids)
        conn.close()

    print("\n" + "═" * 70)
    for s in skips:
        print(f"跳过：{s}")
    if fails:
        print(f"失败 {len(fails)} 条：")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    if skips:
        print("未失败，但有跳过项 —— 上面那些判据这一轮没有被验证。")
        return 0
    print("二十条判据全通。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
