#!/usr/bin/env python3
"""验证部署态能收视频 —— 上一轮 mp4 是被 400 挡在门外的。

在 lea-rag 容器里跑：
    docker exec lea-rag-test python /tmp/verify_video_upload_deployed.py

判定分三层，每层都带反证：

  1. 镜像里有 ffmpeg，且白名单因此包含视频。
     反证：把 _has_ffmpeg 打成 False，白名单必须立刻不含 mp4。
  2. HTTP 上传 mp4 返回 2xx 而不是 400。
     反证：同一个请求换成 .xyz 扩展名必须被拒 —— 证明这道关卡真的在拦，
     mp4 通过不是因为校验被整体关掉了。
  3. 抽音轨真的出 PCM：长度对得上源视频时长、无 RIFF 头、非静音。

第 4 层（转写出文字）这里查不了：retrieval_models 还没有 asr 行，
文档会停在"没有可用 ASR 模型"。脚本会把这件事显式报出来，
而不是让它看起来像成功。
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/app/src")

BASE = "http://127.0.0.1:8300"
TENANT = "verify-video"
#: 第一个参数指定视频。默认那个是 440Hz 正弦，只能验"进得来"；
#: 要验转写内容对不对得用有人声的（/tmp/asrtest.mp4）。
VIDEO = sys.argv[1] if len(sys.argv) > 1 else "/tmp/kbtest.mp4"
#: 第二个参数给期望关键词（逗号分隔），用来判断转写内容是否正确 ——
#: 只检测"不是二进制垃圾"还不够，内容可能是另一段音频的转写。
EXPECT = [w for w in (sys.argv[2].split(",") if len(sys.argv) > 2 else []) if w]

ok = True


def bad(msg: str) -> None:
    global ok
    ok = False
    print(f"  ✗ {msg}")


def good(msg: str) -> None:
    print(f"  ✓ {msg}")


def _headers(extra: dict | None = None) -> dict:
    h = {"X-Internal-Token": os.getenv("LEA_INTERNAL_TOKEN", "")}
    if extra:
        h.update(extra)
    return h


def call(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    hdr = _headers({"Content-Type": "application/json"}) if body is not None else _headers()
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def upload(kb_id: int, filename: str, content: bytes, mime: str):
    boundary = "----verifyvideo0123456789"
    body = io.BytesIO()
    body.write(f"--{boundary}\r\n".encode())
    body.write(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    )
    body.write(f"Content-Type: {mime}\r\n\r\n".encode())
    body.write(content)
    body.write(f"\r\n--{boundary}\r\n".encode())
    body.write(b'Content-Disposition: form-data; name="tenant_id"\r\n\r\n')
    body.write(TENANT.encode())
    body.write(f"\r\n--{boundary}--\r\n".encode())
    payload = body.getvalue()

    req = urllib.request.Request(
        f"{BASE}/kb/{kb_id}/upload",
        data=payload,
        method="POST",
        headers=_headers({"Content-Type": f"multipart/form-data; boundary={boundary}"}),
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def fetch_chunk_sample(doc_id: int) -> str | None:
    """直接读库拿 chunk 正文 —— 没有"列出 chunk"的 HTTP 接口。"""
    try:
        from lea_rag.db.database import SessionLocal
        from sqlalchemy import text

        db = SessionLocal()
        try:
            row = db.execute(
                text(
                    "SELECT content FROM kb_document_chunks "
                    "WHERE document_id = :d ORDER BY id LIMIT 1"
                ),
                {"d": doc_id},
            ).fetchone()
            return row[0] if row else None
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        print(f"  （读库失败：{type(e).__name__}: {e}）")
        return None


def fetch_all_chunks(doc_id: int) -> str | None:
    """拼起这份文档的所有 chunk 正文。

    只看第一条不够：spark-asr 对长音频静默截断，而截断的表现是**后面的段没了**，
    第一条 chunk 完全正常。要判断转写完整就得看全文。
    """
    try:
        from lea_rag.db.database import SessionLocal
        from sqlalchemy import text

        db = SessionLocal()
        try:
            rows = db.execute(
                text(
                    "SELECT content FROM kb_document_chunks "
                    "WHERE document_id = :d ORDER BY id"
                ),
                {"d": doc_id},
            ).fetchall()
            return "".join(r[0] or "" for r in rows)
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        print(f"  （读全文失败：{type(e).__name__}: {e}）")
        return None


def looks_binary(s: str) -> bool:
    """这段文本是不是二进制被当文本读出来的。

    两个信号，命中任一即判定：
      - 有 NUL 或大量不可打印字符（文本正文不会有）
      - 出现容器格式/编码器的特征串
    """
    if "\x00" in s:
        return True
    head = s[:400]
    unprintable = sum(1 for c in head if ord(c) < 32 and c not in "\r\n\t")
    if head and unprintable / len(head) > 0.05:
        return True
    markers = (
        "ftypisom", "ftypmp4", "moov", "mdat", "ID3", "Lavc", "Lavf",
        # x264/x265 的 options 行是纯 ASCII 可打印的，不含 NUL，靠前两个信号
        # 抓不到。实测第一个 chunk 带 ftypisom 所以能命中，但不该依赖 chunk 顺序。
        "x264 - core", "x265 - core", "cabac=", "me_range=", "keyint=", "qcomp=",
    )
    return any(mk in s[:600] for mk in markers)


print("=" * 68)
print("1. 白名单（含反证）")
print("=" * 68)

from lea_rag.parsers import parser as P  # noqa: E402

if P._has_ffmpeg():
    good("镜像里探测到 ffmpeg")
else:
    bad("镜像里没有 ffmpeg —— 后面全部无意义")

up = P.supported_upload_exts()
if "mp4" in up:
    good(f"白名单含 mp4（媒体格式共 {len(P.media_exts())} 个）")
else:
    bad("白名单不含 mp4")

# 反证：没有 ffmpeg 时白名单必须收回视频，否则是写死的
_orig = P._has_ffmpeg
P._has_ffmpeg = lambda: False
try:
    if "mp4" in P.supported_upload_exts():
        bad("反证失败：_has_ffmpeg=False 时 mp4 仍在白名单 —— 说明是写死的")
    else:
        good("反证通过：_has_ffmpeg=False 时 mp4 退出白名单")
finally:
    P._has_ffmpeg = _orig

print()
print("=" * 68)
print("2. 抽音轨（含反证）")
print("=" * 68)

if not os.path.exists(VIDEO):
    bad(f"{VIDEO} 不存在，先 docker cp 进来")
else:
    from lea_rag.services.kb_processor import DocumentProcessor as DP  # noqa: E402

    pcm = DP._to_pcm16k(VIDEO)
    secs = len(pcm) / 2 / 16000

    # 比的必须是**音轨流**时长，不是 format=duration。
    #
    # format=duration 是容器时长 = 所有流里最长的那个。用 lavfi 造的测试视频
    # 视频流常比音轨长（asrtest.mp4：音轨 12.248s / 视频 13.960s），拿它比
    # 会把正确的抽取判成失败 —— 第一版就是这样误报的。
    def _probe(*args: str) -> float | None:
        p = subprocess.run(
            ["ffprobe", "-v", "error", *args, "-of", "default=nw=1:nk=1", VIDEO],
            capture_output=True, text=True, check=False,
        )
        try:
            return float(p.stdout.strip().splitlines()[0])
        except (ValueError, IndexError):
            return None

    src = _probe("-select_streams", "a:0", "-show_entries", "stream=duration")
    container = _probe("-show_entries", "format=duration")

    if src and abs(secs - src) < 0.3:
        extra_note = ""
        if container and abs(container - src) > 0.3:
            extra_note = f"（容器 {container:.3f}s 更长，因为视频流更长 —— 不该拿它比）"
        good(f"PCM 时长 {secs:.3f}s 对得上音轨 {src:.3f}s {extra_note}")
    elif src:
        bad(f"PCM 时长 {secs:.3f}s 和音轨 {src:.3f}s 差太多")
    else:
        good(f"PCM {len(pcm)} 字节 = {secs:.3f}s（ffprobe 没读到源时长，跳过比对）")

    if pcm[:4] == b"RIFF":
        bad("输出带 RIFF 头 —— 基座要的是裸 PCM，带头会被当成数据")
    else:
        good("裸 PCM，无 RIFF 头")

    if sum(1 for b in pcm[:4000] if b != 0) > 100:
        good("非静音")
    else:
        bad("全静音 —— 音轨没抽出来")

    # 反证：无音轨的视频必须抛错，不能静默返回空
    d = tempfile.mkdtemp()
    silent = os.path.join(d, "noaudio.mp4")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:s=160x120:d=1", "-c:v", "libx264", silent],
        check=True, capture_output=True,
    )
    try:
        out = DP._to_pcm16k(silent)
        bad(f"反证失败：无音轨视频返回了 {len(out)} 字节而没抛错")
    except Exception as e:
        good(f"反证通过：无音轨视频抛错（{type(e).__name__}）")

print()
print("=" * 68)
print("3. HTTP 上传（含反证）")
print("=" * 68)

st, kb = call("POST", "/kb/create", {
    "name": f"video-verify-{int(time.time())}",
    "tenant_id": TENANT,
    "description": "mp4 上传验证，可删",
})
if st not in (200, 201) or not isinstance(kb, dict):
    bad(f"建库失败 {st}: {kb}")
    kb_id = None
else:
    kb_id = kb.get("id")
    good(f"建库 id={kb_id}")

if kb_id and os.path.exists(VIDEO):
    with open(VIDEO, "rb") as f:
        blob = f.read()

    st, resp = upload(kb_id, "kbtest.mp4", blob, "video/mp4")
    if st in (200, 201, 202):
        good(f"mp4 上传被接受 {st}: {str(resp)[:120]}")
        doc_id = resp.get("id") or resp.get("document_id") if isinstance(resp, dict) else None
    elif st == 400:
        bad(f"mp4 仍被 400 挡住：{resp}")
        doc_id = None
    else:
        bad(f"mp4 上传返回 {st}: {resp}")
        doc_id = None

    # 反证：换个该被拒的扩展名，必须 400
    st2, resp2 = upload(kb_id, "kbtest.xyz", blob[:2000], "application/octet-stream")
    if st2 == 400:
        good("反证通过：.xyz 被 400 拒绝 —— 校验确实在拦，mp4 通过不是因为关了校验")
    else:
        bad(f"反证失败：.xyz 也返回了 {st2} —— 白名单校验形同虚设")

    # 看 mp4 这份文档最终落到什么状态
    if doc_id:
        print()
        print("  等解析落地（最多 90s）…")
        final = None
        for _ in range(18):
            time.sleep(5)
            st3, docs = call("GET", f"/kb/{kb_id}/documents")
            if st3 == 200 and isinstance(docs, list):
                for d_ in docs:
                    if d_.get("id") == doc_id:
                        final = d_
                        break
            if final and final.get("status") in ("ready", "error"):
                break

        if final:
            status = final.get("status")
            err = (final.get("error") or final.get("error_message") or "")[:300]
            chunks = final.get("chunk_count") or final.get("chunks") or 0
            print(f"  状态={status} chunks={chunks}")
            if err:
                print(f"  错误={err}")

            # ⚠ 这里**必须看 chunk 正文**。只判断 status==ready and chunks>0 是
            # 空判定：第一次跑出来 ready / 138 chunks，看着像成功，实际是 mp4
            # 二进制被 errors="ignore" 当文本切的 —— 正文是 `ftypisom`、
            # `x264 - core 164`。假成功比失败难认，因为它长得像成功。
            if status == "ready" and chunks:
                sample = fetch_chunk_sample(doc_id)
                if sample is None:
                    print("  ⚠ 读不到 chunk 正文，无法判断内容真假")
                elif looks_binary(sample):
                    bad(
                        "ready 但 chunk 正文是二进制垃圾（容器格式/码流参数），"
                        f"不是转写文本：{sample[:120]!r}"
                    )
                elif EXPECT:
                    # 「不是二进制」还不够：内容可能是别的音频的转写，或者只转了
                    # 开头一小截（spark-asr 对长音频静默截断）。所以要按已知原文
                    # 的关键词算覆盖率。
                    allsrc = fetch_all_chunks(doc_id) or sample
                    hit = [w for w in EXPECT if w in allsrc]
                    rate = len(hit) / len(EXPECT)
                    print(f"  转写全文 {len(allsrc)} 字符: {allsrc[:200]!r}")

                    # ⚠ 关键词覆盖率**对截断是瞎的**。实测 300s 视频（同一段
                    # 12.25s 语音循环 24 遍）：转 1 遍和转 24 遍都是 10/10，
                    # 因为 10 个关键词全在第一遍里。
                    #
                    # 所以长音频还要看**量**：spark-asr 不分段时封顶约 462 字符，
                    # 分段生效后 300s 应该出 ~1150 字符。超过封顶才说明分段真跑了。
                    CAP = 470          # 实测封顶 462-464，留点余量
                    if secs > 150 and len(allsrc) <= CAP:
                        bad(
                            f"{secs:.0f}s 音频只转出 {len(allsrc)} 字符（≤{CAP} 封顶线）"
                            " —— 分段没生效，后半截被静默丢掉了"
                        )
                    elif secs > 150:
                        good(
                            f"长音频分段生效：{secs:.0f}s → {len(allsrc)} 字符，"
                            f"远超不分段的 ~462 封顶"
                        )

                    if rate >= 0.8:
                        good(f"转写内容正确：关键词 {len(hit)}/{len(EXPECT)} 命中 {hit}")
                    else:
                        bad(
                            f"转写内容不全：关键词只命中 {len(hit)}/{len(EXPECT)} "
                            f"{hit}，缺 {[w for w in EXPECT if w not in allsrc]}"
                        )
                else:
                    good(f"转写出真实文本：{sample[:80]!r}")
            elif "ASR" in err or "asr" in err.lower():
                print("  ⚠ 停在 ASR 缺模型（retrieval_models 没有 asr 行）——"
                      "这是已知前置依赖。关键是它现在**显式报错**而不是假装 ready")
            else:
                print(f"  ⚠ 停在 {status}，需要看 worker 日志")
        else:
            print("  ⚠ 90s 内没拿到终态")

print()
print("=" * 68)
print("PASS —— 视频进得来" if ok else "FAIL")
print("=" * 68)

# 自己收尾。DELETE 在 /knowledge-bases/{id}（不在 /kb 前缀下）。
if kb_id:
    st, _ = call("DELETE", f"/knowledge-bases/{kb_id}")
    print(f"\n清理测试库 id={kb_id}: {st}")
sys.exit(0 if ok else 1)
