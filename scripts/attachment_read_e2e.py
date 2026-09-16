"""验收：上传附件之后，模型能不能真的拿到文件内容。

    LEA_AGENT_URL=http://127.0.0.1:8080 python scripts/attachment_read_e2e.py

前置：lea-config / lea-tools / lea-agent / lea-parser 在跑
（infra/docker-compose.verify.yml），且沙箱/共享存储可用（否则 ③④ 会被跳过）。

## 这个脚本在验什么

附件处理刚从"上传时把正文拼进消息"改成"只给路径，模型自己调 Read 取"。这个改动
把一个**确定性的行为**（正文一定在上下文里）换成了一个**概率性的行为**（模型得
自己决定去读）——而后者单元测试结构上测不到：注入文本长什么样可以断言，模型看完
之后干什么不行。

所以这条链必须真跑。

## 七条判据

    ① 上传落库：blob + 文本各自的状态如实反映在响应里（上传阶段**不**物化）
    ② 注入段不含正文 —— 而且无论文件多大都只有几百字节
    ③ **模型主动调 Read 去取内容**  ← 这个脚本存在的主要理由
    ④ 读到的内容是真的（回答里带出只有读过才知道的锚点）
    ⑤ 图片走 Read 得到"没有文本层"而不是报错，且不点名 OCR/PIL
    ⑥ 没物化的附件不给路径，模型不会去读一个不存在的文件
    ⑦ **进沙箱触发按需物化** —— 跑一条 bash 命令，staged_path 被回填

## 物化是按需的，不是上传时做的

上传只落 blob + 抽文本，`staged_path` 一直是空。文本靠 Read 从库里取（问答够用，
省一次物化）。只有模型**进沙箱**时（BashTool/CodeInterpreter 的 ensureSandbox）才
把该会话所有 `file_key!='' && staged_path==''` 的附件物化进 /mnt/uploads —— 判据 ⑦
就是验这条链。跟"读还是写"无关：ls 也进沙箱，也触发。

## ⚠️ ③ 的判据不能是"回答对不对"

模型完全可以从文件名猜出个大概然后编一段像样的话 —— "教学设计方案.docx" 这种名字
本身就有信息量。判据必须落在**两件不可伪造的事**上：

    a. agent_messages 里有 name="Read" 且 input.path 指向 /mnt/uploads/... 的 tool_use
    b. 回答里带出 ANCHOR —— 一个编不出来的字符串，只在文件正文里出现

只看 a 不够：Read 可能失败了（415、路径不对），模型照样会往下编。
只看 b 不够：如果哪天又把正文注回消息里，b 会过而 a 不会 —— 那正是这次要防的回归。

## ⚠️ 别把"没调 Read"直接判成失败

模型不调 Read 有两种可能，后果完全不同：

    注入文本没说清楚      → 我们的问题，要改措辞
    问题本身不需要读文件   → 模型是对的

所以问法必须是**只有读了文件才能回答**的（问正文里那个锚点值），把第二种可能排除掉。
第一版问的是"这份文件讲了什么"，模型看着文件名答了个大概就完事 —— 那不能证明任何事。
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _dbconn as d

AGENT_URL = os.getenv("LEA_AGENT_URL", "http://127.0.0.1:8100").rstrip("/")
SECRET = d.env()["SECRET_KEY"]
TENANT = "default"
USER = "attach-read-user"
AGENT_ID = int(os.getenv("LEA_AGENT_ID", "2"))

# ANCHOR 是这个脚本的核心：一个模型**编不出来**的字符串，只在文件正文里出现。
#
# 用生造的专有名词而不是随机 hex：随机串在正文里显得突兀，模型可能当噪声跳过；而
# 一个看起来像真内容的怪词（编号 + 中文名）它会当成正经信息读进去。
#
# 每次跑换一个，避免上一次的历史或缓存把结果染绿。
ANCHOR = f"翡翠-{uuid.uuid4().hex[:6].upper()}"

# 正文要够长，长到"不读就猜不出"，但也别长到触发 Read 的截断（那是另一条路径）。
DOC_BODY = "\n".join([
    "# 校本课程实施方案",
    "",
    "## 一、背景",
    "本方案依据 2026 年秋季学期教学工作安排制定，覆盖初中三个年级。",
    "",
    "## 二、课程代号",
    f"本次实施的课程内部代号为 {ANCHOR}，所有教学材料均以此代号归档。",
    "",
    "## 三、课时安排",
    "每周 2 课时，共 18 周，其中实践课占比不低于 40%。",
    "",
    "## 四、评价方式",
    "过程性评价占 60%，终结性评价占 40%。",
])


def mint(user: str) -> str:
    import jwt
    return jwt.encode(
        {"sub": user, "tenant_id": TENANT, "exp": int(time.time()) + 3600},
        SECRET, algorithm="HS256",
    )


def hdr(user: str) -> dict:
    return {"Authorization": f"Bearer {mint(user)}"}


# ── HTTP ──────────────────────────────────────────────────────────────────

def upload(session_id: str, filename: str, data: bytes, user: str = USER) -> dict:
    """上传一个附件，返回 (status, body)。"""
    import requests
    r = requests.post(
        f"{AGENT_URL}/api/chat/attachments/upload",
        files={"file": (filename, io.BytesIO(data))},
        data={"session_id": session_id},
        headers=hdr(user), timeout=180,
    )
    return {"status": r.status_code, "body": r.json() if r.content else {}}


def turn_running(session_id: str, user: str) -> bool:
    import requests
    try:
        r = requests.get(f"{AGENT_URL}/api/chat/status/{session_id}",
                         headers=hdr(user), timeout=10)
        return r.status_code == 200 and bool(r.json().get("running"))
    except requests.RequestException:
        return False


def ask(session_id: str, message: str, attachment_ids: list[int] | None = None,
        user: str = USER) -> int:
    """发一条消息（可带附件），等这一轮真的收尾再返回。

    等收尾的理由见 session_attach_e2e.py 的 ask()：流结束和 turn 的 defer 跑完是
    两件事，不等干净就读库会读到半截。
    """
    import requests
    payload = {"session_id": session_id, "agent_id": AGENT_ID, "message": message}
    if attachment_ids:
        payload["attachment_ids"] = attachment_ids
    r = requests.post(f"{AGENT_URL}/api/chat/stream", json=payload,
                      headers=hdr(user), stream=True, timeout=300)
    if r.status_code != 200:
        print(f"    HTTP {r.status_code}: {r.text[:300]}")
        return r.status_code
    for _ in r.iter_lines(decode_unicode=True):
        pass
    for _ in range(120):
        if not turn_running(session_id, user):
            break
        time.sleep(1)
    return 200


# ── 库 ────────────────────────────────────────────────────────────────────

def message_count(conn, session_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM agent_messages "
                    "WHERE tenant_id=%s AND session_id=%s", (TENANT, session_id))
        return cur.fetchone()[0]


def rows_since(conn, session_id: str, mark: int) -> list[tuple[str, dict]]:
    """(role, message) —— 第 mark 条之后新增的。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT role, message FROM agent_messages "
            "WHERE tenant_id=%s AND session_id=%s ORDER BY id "
            "LIMIT 18446744073709551615 OFFSET %s",
            (TENANT, session_id, mark),
        )
        return [(role, json.loads(raw)) for role, raw in cur.fetchall()]


def dissect(conn, session_id: str, mark: int) -> dict:
    """把第 mark 条之后的消息拆成 (用户消息文本, Read 调用, 工具返回, 最终回复)。

    用户消息文本是判据 ② 要看的东西 —— 注入段就拼在它前面。引擎存的是**拼过前缀的
    那一条**，所以库里读出来的就是模型真正看到的。
    """
    user_texts, reads, results, reply = [], [], [], ""
    tool_calls = []  # (name, input) —— 所有工具调用，判据 ⑦ 要看模型有没有调 Bash
    for role, msg in rows_since(conn, session_id, mark):
        content = msg.get("content")
        if isinstance(content, str):
            if role == "user":
                user_texts.append(content)
            continue
        for b in content or []:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text" and role == "user":
                user_texts.append(b.get("text", ""))
            elif t == "tool_use":
                name = b.get("name", "")
                tool_calls.append((name, b.get("input") or {}))
                if name == "Read":
                    reads.append(b.get("input") or {})
            elif t == "tool_result":
                c = b.get("content")
                results.append(c if isinstance(c, str) else json.dumps(c, ensure_ascii=False))
        if role == "assistant":
            txt = "".join(b.get("text", "") for b in (content or [])
                          if isinstance(b, dict)).strip()
            if txt:
                reply = txt
    return {"user_texts": user_texts, "reads": reads, "tool_calls": tool_calls,
            "results": results, "reply": reply}


def attachment_row(conn, att_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT filename, file_key, staged_path, media_type, "
            "       CHAR_LENGTH(COALESCE(extracted_text,'')) "
            "  FROM chat_attachments WHERE id=%s", (att_id,))
        r = cur.fetchone()
    if not r:
        return None
    return {"filename": r[0], "file_key": r[1], "staged_path": r[2],
            "media_type": r[3], "text_len": r[4]}


def cleanup(conn, sessions: list[str], att_ids: list[int]) -> None:
    with conn.cursor() as cur:
        for s in sessions:
            cur.execute("DELETE FROM agent_messages WHERE session_id=%s", (s,))
            cur.execute("DELETE FROM chat_attachments WHERE session_id=%s", (s,))
            # chat_sessions 的主键列叫 `id`，不是 session_id
            cur.execute("DELETE FROM chat_sessions WHERE id=%s", (s,))
        for a in att_ids:
            cur.execute("DELETE FROM chat_attachments WHERE id=%s", (a,))
    conn.commit()


def step(n, title): print(f"\n[{n}] {title}")


# 一个最小的合法 PNG（1x1 透明），用来验图片那条路。
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6360000002000100ffff0300000600"
    "05572bcebc0000000049454e44ae426082"
)


def main() -> int:
    d.utf8_stdout()
    conn = d.connect()
    sess = f"att-read-{uuid.uuid4().hex[:10]}"
    sess_img = f"att-img-{uuid.uuid4().hex[:10]}"
    sess7 = f"att-stage-{uuid.uuid4().hex[:10]}"
    att_ids: list[int] = []
    fails: list[str] = []
    skips: list[str] = []

    try:
        # ── ① 上传 ────────────────────────────────────────────────
        step(1, f"① 上传 md 文档（锚点 {ANCHOR}）")
        up = upload(sess, "校本课程实施方案.md", DOC_BODY.encode("utf-8"))
        print(f"    HTTP {up['status']}  {json.dumps(up['body'], ensure_ascii=False)[:300]}")
        if up["status"] != 200:
            fails.append(f"① 上传失败 HTTP {up['status']}")
            raise SystemExit
        att_id = up["body"]["id"]
        att_ids.append(att_id)

        row = attachment_row(conn, att_id)
        print(f"    库里: {row}")
        if not row:
            fails.append("① 附件没落库")
            raise SystemExit
        if row["text_len"] == 0:
            fails.append("① extracted_text 是空的 —— parser 没抽出文本，后面全白测")
            raise SystemExit
        print(f"    ✓ ① 落库，抽出 {row['text_len']} 字符")

        # ⚠️ 上传时 staged_path 必然为空 —— 物化是按需的（进沙箱才做），不是上传做的。
        # 这是**正确状态**，不再跳过 ③④：那两条现在走 attachment:<id> 从库里读
        # extracted_text，不依赖物化。物化本身由判据 ⑦ 单独验。
        if row["staged_path"]:
            fails.append("① 上传阶段就物化了 —— 物化应该是按需的（进沙箱才做）")

        # ── ② 注入段 ──────────────────────────────────────────────
        step(2, "② 带附件提问，检查注入进消息的是路径还是正文")
        mark = message_count(conn, sess)
        q = f"这份文件里写的课程内部代号是什么？请原样给出那个代号。"
        if ask(sess, q, [att_id]) != 200:
            fails.append("② 带附件提问失败")
            raise SystemExit

        got = dissect(conn, sess, mark)
        injected = next((t for t in got["user_texts"] if "[文件:" in t), "")
        if not injected:
            fails.append("② 库里找不到带附件 marker 的用户消息")
        else:
            preamble = injected[:len(injected) - len(q)] if injected.endswith(q) else injected
            print(f"    注入段（{len(preamble)} 字节）:")
            for line in preamble.strip().splitlines():
                print(f"      | {line}")
            if ANCHOR in preamble:
                fails.append("② 正文被注进消息了 —— 这正是这次要去掉的东西")
            elif len(preamble) > 600:
                fails.append(f"② 注入段 {len(preamble)} 字节，太长了（正文是不是回来了？）")
            else:
                print("    ✓ ② 注入段只有元信息和路径，不含正文")

        # ── ③④ 模型主动调 Read ───────────────────────────────────
        #
        # ③④ 走 attachment:<id> 从库里读 extracted_text，**不依赖物化**，所以
        # 无论有没有进过沙箱都该跑。
        step(3, "③ 模型有没有主动调 Read")
        for p in got["reads"]:
            print(f"    Read({json.dumps(p, ensure_ascii=False)})")
        upload_reads = [p for p in got["reads"]
                    if str(p.get("path", "")) == f"attachment:{att_id}"]
        if not upload_reads:
            fails.append(
                "③ 模型没调 Read 读上传件 —— 注入文本没能让它去取内容。"
                "这是这次改动最可能的失败形态：路径给了但它不去读。")
        else:
            print(f"    ✓ ③ 调了 Read，path=attachment:{att_id}")

        step(4, f"④ 读到的是不是真内容（锚点 {ANCHOR}）")
        in_results = any(ANCHOR in r for r in got["results"])
        in_reply = ANCHOR in got["reply"]
        print(f"    tool_result 里有锚点: {in_results}")
        print(f"    回复: {got['reply'][:200]}")
        if not in_results:
            fails.append("④ Read 的返回里没有锚点 —— 工具读到的不是文件正文")
        if not in_reply:
            fails.append("④ 回复里没带出锚点 —— 模型没真的用上读到的内容")
        if in_results and in_reply:
            print("    ✓ ④ 读到了真内容，并用在了回答里")

        # ── ⑤ 图片 ────────────────────────────────────────────────
        step(5, "⑤ 上传图片，Read 该说'没有文本层'而不是报错")
        up_img = upload(sess_img, "示意图.png", PNG_1X1)
        print(f"    HTTP {up_img['status']}  {json.dumps(up_img['body'], ensure_ascii=False)[:200]}")
        if up_img["status"] != 200:
            fails.append(f"⑤ 图片上传失败 HTTP {up_img['status']}")
        else:
            img_id = up_img["body"]["id"]
            att_ids.append(img_id)
            img_row = attachment_row(conn, img_id)
            if False:
                skips.append("图片未物化 → 跳过 ⑤ 的读取部分")
                print("    ⚠️ 图片未物化，跳过读取部分")
            else:
                mark_i = message_count(conn, sess_img)
                if ask(sess_img, "这张图上有什么？", [img_id]) != 200:
                    fails.append("⑤ 图片提问失败")
                else:
                    gi = dissect(conn, sess_img, mark_i)
                    # ⚠️ 只看**读这个附件的 Read** 的返回，不看全部 results。
                    #
                    # 模型进沙箱后可能自己去试 OCR、pip 装 pytesseract、cp 文件 ——
                    # 那些是它的自由，输出里出现 "OCR"/"pytesseract" 是正常的。
                    # 这条判据管的是 renderUpload（Read 读附件）说了什么，别把沙箱命令
                    # 的输出也算进来（上一版就是这么误判的：⑤ 明明干净，却被 ls 输出里
                    # 模型自己提的 OCR 拖挂）。
                    #
                    # renderUpload 的返回有稳定特征"没有文本层"，用它把那条 tool_result
                    # 挑出来单独检查。
                    read_returns = [r for r in gi["results"] if "没有文本层" in r]
                    render_out = read_returns[0] if read_returns else ""
                    print(f"    Read 调用: {gi['reads'] or '（无）'}")
                    print(f"    附件 Read 返回: {render_out[:200]}")
                    print(f"    回复: {gi['reply'][:150]}")
                    if gi["reads"]:
                        if not any(str(p.get("path", "")) == f"attachment:{img_id}" for p in gi["reads"]):
                            fails.append("⑤ 图片没有通过 attachment:<id> 调用 Read")
                        elif not render_out:
                            fails.append(f"⑤ 图片的 Read 返回没说清'没有文本层'")
                        else:
                            print("    ✓ ⑤ 图片走 Read 得到了性质说明，不是报错")
                        # 可配置项不能点名 —— 只查 renderUpload 的返回，不查沙箱输出
                        named = [w for w in ("OCR", "PIL", "Pillow", "pytesseract")
                                 if w in render_out]
                        if named:
                            fails.append(f"⑤ Read 返回点名了可配置项 {named}")
                    else:
                        # 图片不调 Read 是可接受的（模型可能直接说"我需要看图"），
                        # 但要记下来 —— 它意味着图片这条路目前没有真正的出口。
                        skips.append("⑤ 模型没对图片调 Read（可接受，但图片路径未被验证）")

        # ── ⑥ 没物化的附件 ────────────────────────────────────────
        step(6, "⑥ 没物化的附件不该给路径")
        with conn.cursor() as cur:
            cur.execute("UPDATE chat_attachments SET staged_path='' WHERE id=%s", (att_id,))
        conn.commit()
        mark6 = message_count(conn, sess)
        if ask(sess, "再确认一次那个代号。", [att_id]) != 200:
            fails.append("⑥ 提问失败")
        else:
            g6 = dissect(conn, sess, mark6)
            inj6 = next((t for t in g6["user_texts"] if "[文件:" in t), "")
            print(f"    注入段: {inj6[:200]}")
            if "/mnt/uploads" in inj6:
                fails.append("⑥ 没物化却给了沙箱路径 —— 模型会去开一个不存在的文件")
            elif f"path=attachment:{att_id}" not in inj6:
                fails.append("⑥ 没提供附件 ID 读取入口")
            else:
                print("    ✓ ⑥ 没物化时不给路径，仍提供 Read ID")

        # ── ⑦ 进沙箱触发按需物化 ──────────────────────────────────
        #
        # 物化不是上传时做的，是**第一次进沙箱**时做的（BashTool/CodeInterpreter 的
        # ensureSandbox 建完沙箱后调 stageSessionAttachments）。所以这里让模型跑一条
        # bash 命令 —— 哪怕只是 ls，也会走 ensureSandbox → 物化这条链。
        #
        # 用一个干净的新会话：前面几个会话要么被 ⑥ 改过 staged_path，要么没进过沙箱。
        # 这一步要的是"上传后从未物化 → 进沙箱 → 物化被触发 → staged_path 回填"的完整链。
        step(7, "⑦ 进沙箱触发按需物化（跑 ls 就够）")
        up7 = upload(sess7, "课程方案.md", DOC_BODY.encode("utf-8"))
        if up7["status"] != 200:
            fails.append(f"⑦ 上传失败 HTTP {up7['status']}")
        else:
            att7 = up7["body"]["id"]
            att_ids.append(att7)
            r7_before = attachment_row(conn, att7)
            print(f"    进沙箱前 staged_path={r7_before['staged_path']!r}（应为空）")

            mark7 = message_count(conn, sess7)
            # 明确让它 ls **/mnt/uploads**（附件的挂载点），不是默认的 /workspace。
            #
            # ⚠️ 这一条以前问"在工作区里执行 ls -la"，模型就 ls /workspace（cwd 默认在
            # 那），而附件挂在 /mnt/uploads —— 于是文件明明在、ls 却看不到，误判成
            # host-mount 没生效。工作区（/workspace）和上传挂载点（/mnt/uploads）是两个
            # 目录，问的时候要说清看哪个。
            ask(sess7, "用命令列出 /mnt/uploads 目录下的所有文件，把输出原样贴出来。", [att7])
            g7 = dissect(conn, sess7, mark7)

            called = [n for n, _ in g7["tool_calls"]]
            print(f"    工具调用: {called or '（无）'}")
            entered_sandbox = any(n in ("Bash", "code_interpreter") for n in called)
            if not entered_sandbox:
                skips.append("⑦ 模型没进沙箱（没调 Bash/code_interpreter）→ 物化未触发")
                print("    ⚠️ 模型没进沙箱，物化链没被触发")
            else:
                r7_after = attachment_row(conn, att7)
                print(f"    进沙箱后 staged_path={r7_after['staged_path']!r}")
                if not r7_after["staged_path"]:
                    fails.append(
                        "⑦ 进了沙箱但 staged_path 还是空 —— 物化没被触发或失败了。"
                        "查 lea-tools 日志的 stage 报错。")
                else:
                    print(f"    ✓ ⑦ 进沙箱触发了物化，staged_path 已回填")
                    # host-mount 生效则沙箱里 ls /mnt/uploads 能看到物化的文件。
                    # 物化在建沙箱**之前**发生（见 file_tools.go ensureSandbox 顺序），
                    # 所以 bind-mount 挂的是已有文件的目录。
                    joined7 = "\n".join(g7["results"])
                    staged = r7_after["staged_path"]
                    if staged in joined7:
                        print(f"    ✓ ⑦ 沙箱里 ls 看到了物化的文件 {staged}")
                    else:
                        fails.append(
                            f"⑦ staged_path 回填了但 ls /mnt/uploads 没看到 {staged} —— "
                            f"host-mount 时序问题（物化应先于建沙箱）。ls 输出：{joined7[:200]}")

    except SystemExit:
        pass
    finally:
        cleanup(conn, [sess, sess_img, sess7], att_ids)
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
    print("六条判据全通。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
