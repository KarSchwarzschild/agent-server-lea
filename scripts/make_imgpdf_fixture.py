#!/usr/bin/env python3
"""造一个图文混排 PDF，测 PDF 里的图片会不会被提取。

本机跑：
    python scripts/make_imgpdf_fixture.py [输出目录]

为什么需要这个：MINERU_TOKEN 失效（401 user token expired），PDF 降级走
parse_pdf_pymupdf，而那个函数里 `"image_items": []` 是**写死的空**。
如果成立，PDF 里的图表就进不了知识库 —— 而 PDF 恰好是图文混排最常见的载体。

这是代码读出来的推论，要实测。设计成两个短语互相区分：

    正文短语  写在 PDF 的文字层  → 验文本提取（降级路径也该有）
    图片短语  画在嵌入的 PNG 里  → 验图片提取 + VLM 描述（降级路径怀疑没有）

两个短语都命中 = PDF 图文全都进得去。
只有正文短语命中 = 文本进得去、图片丢了，正是推论要证的。
"""

import json
import os
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.environ.get("USERPROFILE", "."), "fmt_fixtures"
)
os.makedirs(OUT, exist_ok=True)

TEXT_PHRASE = "鹤顶漏刻卷号八二"      # PDF 文字层
IMAGE_PHRASE = "CRIMSONKEEL-5518"     # 嵌入图片里（英文，VLM 对合成中文体不稳）

from PIL import Image, ImageDraw  # noqa: E402

# 先造一张带字的图
img_path = os.path.join(OUT, "_embedded.png")
img = Image.new("RGB", (560, 200), (255, 255, 255))
dr = ImageDraw.Draw(img)
dr.rectangle([6, 6, 554, 194], outline=(0, 0, 0), width=4)
dr.text((40, 70), IMAGE_PHRASE, fill=(0, 0, 0))
dr.text((40, 110), "CHART EMBEDDED IN PDF", fill=(0, 0, 0))
img.save(img_path, "PNG")

import fitz  # noqa: E402

doc = fitz.open()
page = doc.new_page()
page.insert_text((72, 90), "图文混排测试文档", fontsize=18, fontname="china-s")
page.insert_text((72, 130), f"正文标识：{TEXT_PHRASE}", fontsize=13, fontname="china-s")
page.insert_text((72, 165), "下面嵌入一张图片，图里写着另一个标识。", fontsize=11, fontname="china-s")
page.insert_image(fitz.Rect(72, 200, 352, 300), filename=img_path)
page.insert_text((72, 330), "图片下方的正文，保证文字层有多段内容。", fontsize=11, fontname="china-s")

pdf_path = os.path.join(OUT, "imgpdf.pdf")
doc.save(pdf_path)
doc.close()
os.unlink(img_path)

# 回读校验：文字提得出来、图片数得出来。两样都要确认，否则测的是坏样本。
chk = fitz.open(pdf_path)
txt = chk[0].get_text()
n_img = len(chk[0].get_images(full=True))
chk.close()

print(f"{pdf_path}  {os.path.getsize(pdf_path):,} B")
print(f"  文字层 {len(txt)} 字符，含正文短语: {TEXT_PHRASE in txt}")
print(f"  嵌入图片 {n_img} 张")

if TEXT_PHRASE not in txt:
    raise SystemExit("样本坏了：文字层提不出正文短语")
if n_img < 1:
    raise SystemExit("样本坏了：PDF 里没有嵌入图片")

mp = os.path.join(OUT, "imgpdf_manifest.json")
with open(mp, "w", encoding="utf-8") as f:
    json.dump({
        "file": "imgpdf.pdf",
        "text_phrase": TEXT_PHRASE,
        "image_phrase": IMAGE_PHRASE,
        "embedded_images": n_img,
    }, f, ensure_ascii=False, indent=2)
print(f"清单 {mp}")
