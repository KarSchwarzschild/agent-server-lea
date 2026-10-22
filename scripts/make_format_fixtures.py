#!/usr/bin/env python3
"""造各格式的测试文件，每份带一个**唯一短语**。

本机跑（需要 fitz / python-docx / python-pptx / openpyxl / PIL / ffmpeg）：
    python scripts/make_format_fixtures.py <输出目录>

唯一短语是判定的关键：文件里写"紫貂电报机编号七三"这种不可能出现在别处的词，
上传之后
  - chunk 正文里能找到它 → 解析对了
  - 搜这个词能命中这份文档 → embedding + 索引 + 检索都通了

只检查"有没有 chunk"是空判定（之前 mp4 二进制被切成 138 个乱码 chunk 也满足），
只检查"chunk 不是二进制"也不够（内容可能是别的文件的）。唯一短语两样都能钉。
"""

import json
import os
import subprocess
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("USERPROFILE", "."), "fmt_fixtures")
os.makedirs(OUT, exist_ok=True)

#: 格式 → 唯一短语。短语要足够怪，保证不会被模型"猜"出来。
PHRASES = {
    "pdf":  "紫貂电报机编号七三",
    "txt":  "青鸾算盘代号五九",
    "md":   "赤狐灯塔序列二八",
    "docx": "白鹇砚台批次四一",
    "pptx": "墨鲤风筝票号六六",
    "png":  "YELLOWFIN-9042",      # 图片里画的是英文，VLM 识别中文手写体不稳
    "jpg":  "TEALWING-3317",
    "xlsx": "苍鹭罗盘账目三五",
    "html": "黛山渡口档案九七",
    "csv":  "朱雀窑口清单一二",
}

made: dict[str, str] = {}


def rec(ext: str, path: str) -> None:
    made[ext] = os.path.basename(path)
    print(f"  {ext:5s} {os.path.basename(path):24s} {os.path.getsize(path):>9,} B")


print(f"输出到 {OUT}")

# ── 纯文本 ──────────────────────────────────────────────────────────────────
for ext in ("txt", "md"):
    p = os.path.join(OUT, f"fixture.{ext}")
    head = "# 测试文档\n\n" if ext == "md" else ""
    with open(p, "w", encoding="utf-8") as f:
        f.write(
            f"{head}这是一份用于验证知识库解析的测试文档。\n\n"
            f"关键标识：{PHRASES[ext]}。\n\n"
            "第二段内容，用来保证切块逻辑有东西可切。知识库需要把这份文档"
            "解析成若干文本块，再交给 embedding 模型生成向量。\n"
        )
    rec(ext, p)

# ── PDF（PyMuPDF）────────────────────────────────────────────────────────────
import fitz  # noqa: E402

doc = fitz.open()
page = doc.new_page()
# 用 fitz 内置的简中字体（china-s），不要传 fontfile。
#
# 第一版传了 C:\Windows\Fonts\msyh.ttc，PDF 变成 19.6MB —— ttc 是字体**集合**，
# fitz 会把整包嵌进去。内置 china-s 走的是 PDF 标准 CJK 字体资源，几 KB。
page.insert_text((72, 100), "知识库解析测试文档", fontsize=18, fontname="china-s")
page.insert_text((72, 140), f"关键标识：{PHRASES['pdf']}", fontsize=14, fontname="china-s")
page.insert_text((72, 180), "第二行正文，保证有内容可切块。", fontsize=12, fontname="china-s")
p = os.path.join(OUT, "fixture.pdf")
doc.save(p)
doc.close()
rec("pdf", p)

# 回读确认文字真的提取得出来 —— 画不出字的 PDF 看起来也能保存成功
_chk = fitz.open(p)
_txt = _chk[0].get_text()
_chk.close()
if PHRASES["pdf"] not in _txt:
    raise SystemExit(f"PDF 里提不出关键短语，提到的是 {_txt[:120]!r}")
print(f"        （回读校验：提取到 {len(_txt)} 字符，含关键短语）")

# ── docx ────────────────────────────────────────────────────────────────────
from docx import Document  # noqa: E402

d = Document()
d.add_heading("知识库解析测试", level=1)
d.add_paragraph(f"关键标识：{PHRASES['docx']}。")
d.add_paragraph("第二段，保证切块有内容。")
p = os.path.join(OUT, "fixture.docx")
d.save(p)
rec("docx", p)

# ── pptx ────────────────────────────────────────────────────────────────────
from pptx import Presentation  # noqa: E402
from pptx.util import Inches  # noqa: E402

pr = Presentation()
s = pr.slides.add_slide(pr.slide_layouts[5])
s.shapes.title.text = "知识库解析测试"
tb = s.shapes.add_textbox(Inches(1), Inches(2), Inches(8), Inches(2)).text_frame
tb.text = f"关键标识：{PHRASES['pptx']}。"
tb.add_paragraph().text = "第二行，保证切块有内容。"
p = os.path.join(OUT, "fixture.pptx")
pr.save(p)
rec("pptx", p)

# ── 图片（PIL，画英文 —— VLM 对中文手写/合成体识别不稳）────────────────────
from PIL import Image, ImageDraw  # noqa: E402

for ext, mode in (("png", "PNG"), ("jpg", "JPEG")):
    img = Image.new("RGB", (640, 220), (255, 255, 255))
    dr = ImageDraw.Draw(img)
    dr.rectangle([8, 8, 632, 212], outline=(0, 0, 0), width=3)
    # 不指定字体用默认位图字体，字小但清晰；放大画布保证可读
    dr.text((30, 60), PHRASES[ext], fill=(0, 0, 0))
    dr.text((30, 110), "KNOWLEDGE BASE IMAGE TEST", fill=(0, 0, 0))
    p = os.path.join(OUT, f"fixture.{ext}")
    img.save(p, mode)
    rec(ext, p)

# ── xlsx / html / csv（预期被拒：云端 MinerU 模式只认 pdf）──────────────────
import openpyxl  # noqa: E402

wb = openpyxl.Workbook()
ws = wb.active
ws["A1"] = "项目"
ws["B1"] = "标识"
ws["A2"] = "知识库测试"
ws["B2"] = PHRASES["xlsx"]
p = os.path.join(OUT, "fixture.xlsx")
wb.save(p)
rec("xlsx", p)

p = os.path.join(OUT, "fixture.html")
with open(p, "w", encoding="utf-8") as f:
    f.write(
        "<!doctype html><html><head><meta charset='utf-8'><title>测试</title></head>"
        f"<body><h1>知识库解析测试</h1><p>关键标识：{PHRASES['html']}。</p></body></html>"
    )
rec("html", p)

p = os.path.join(OUT, "fixture.csv")
with open(p, "w", encoding="utf-8") as f:
    f.write(f"项目,标识\n知识库测试,{PHRASES['csv']}\n")
rec("csv", p)

# ── 音视频：从已有的 TTS wav 派生 ────────────────────────────────────────────
src_wav = os.path.join(os.environ.get("USERPROFILE", "."), "asrtest.wav")
if os.path.exists(src_wav):
    import shutil

    p = os.path.join(OUT, "fixture.wav")
    shutil.copy(src_wav, p)
    rec("wav", p)

    # mp3：验有损音频走 ffmpeg 解码那条路
    p = os.path.join(OUT, "fixture.mp3")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-i", src_wav, "-codec:a", "libmp3lame", "-b:a", "128k", p],
        check=True,
    )
    rec("mp3", p)

    # mp4：验视频抽音轨
    p = os.path.join(OUT, "fixture.mp4")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=navy:s=320x240",
         "-i", src_wav, "-c:v", "libx264", "-c:a", "aac", "-shortest", p],
        check=True,
    )
    rec("mp4", p)
else:
    print(f"  ⚠ 没找到 {src_wav}，跳过音视频（它们的短语来自 TTS 原文）")

# 音视频的"唯一短语"是 TTS 原文里的词，不是上面那张表
AUDIO_PHRASES = ["知识库", "多模态", "语音识别", "音轨"]

manifest = {
    "phrases": PHRASES,
    "audio_phrases": AUDIO_PHRASES,
    "files": made,
}
mp = os.path.join(OUT, "manifest.json")
with open(mp, "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)
print(f"\n清单 {mp}")
print(f"共 {len(made)} 个文件")
