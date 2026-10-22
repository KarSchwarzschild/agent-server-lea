#!/usr/bin/env python3
"""造一个嵌「无文字图形」的 PDF —— 分辨 OCR 路径和图片路径。

本机跑：
    python scripts/make_photopdf_fixture.py [输出目录]

## 为什么需要第二个样本

第一个样本（imgpdf.pdf）嵌的图里写着 CRIMSONKEEL-5518。换上有效 MinerU
token 后短语确实提取到了，但看落库形态：

    Milvus 1 行 {'text': 1}        ← 没有 image_caption
    chunk 正文 = 文字层 + 图里的字  ← 拼在一起了

说明 MinerU 把那张图判成**文字区域**，OCR 出来并进正文。对那个样本是对的，
但不能由此说"PDF 里的图片进得去" —— 真实的图表/照片 OCR 不出文字，
必须走 image_items + VLM 描述那条路才进得去。

## 这个样本怎么分辨

画一个**没有任何文字**的几何图形（三色扇形 + 环），OCR 无从下手。然后：

    出现 image_caption 行    → 图片路径通，真图表进得去
    只有 text 行、且描述不含图形特征 → 图片被丢了

文字层放一个唯一短语做对照，确认文本那条路仍然正常。
"""

import json
import os
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.environ.get("USERPROFILE", "."), "fmt_fixtures"
)
os.makedirs(OUT, exist_ok=True)

TEXT_PHRASE = "蝉蜕银壶编目三七"   # 文字层，验文本路径仍通

from PIL import Image, ImageDraw  # noqa: E402

# 无文字图形：三色饼图 + 同心环。VLM 该能描述出"饼图/扇形/颜色"，
# 而 OCR 在这张图上无从下手 —— 这正是用来分辨两条路的。
img_path = os.path.join(OUT, "_photo.png")
img = Image.new("RGB", (480, 480), (255, 255, 255))
dr = ImageDraw.Draw(img)
dr.pieslice([40, 40, 440, 440], start=0, end=140, fill=(220, 60, 50))
dr.pieslice([40, 40, 440, 440], start=140, end=250, fill=(60, 130, 220))
dr.pieslice([40, 40, 440, 440], start=250, end=360, fill=(240, 190, 60))
dr.ellipse([160, 160, 320, 320], fill=(255, 255, 255))
dr.ellipse([200, 200, 280, 280], outline=(30, 30, 30), width=6)
img.save(img_path, "PNG")

import fitz  # noqa: E402

doc = fitz.open()
page = doc.new_page()
page.insert_text((72, 90), "图表测试文档", fontsize=18, fontname="china-s")
page.insert_text((72, 128), f"正文标识：{TEXT_PHRASE}", fontsize=13, fontname="china-s")
page.insert_text((72, 162), "下面是一张不含任何文字的饼图。", fontsize=11, fontname="china-s")
page.insert_image(fitz.Rect(72, 190, 352, 470), filename=img_path)

pdf_path = os.path.join(OUT, "photopdf.pdf")
doc.save(pdf_path)
doc.close()
os.unlink(img_path)

chk = fitz.open(pdf_path)
txt = chk[0].get_text()
n_img = len(chk[0].get_images(full=True))
chk.close()

print(f"{pdf_path}  {os.path.getsize(pdf_path):,} B")
print(f"  文字层 {len(txt)} 字符，含短语: {TEXT_PHRASE in txt}")
print(f"  嵌入图片 {n_img} 张（无文字）")

if TEXT_PHRASE not in txt or n_img < 1:
    raise SystemExit("样本坏了")

mp = os.path.join(OUT, "photopdf_manifest.json")
with open(mp, "w", encoding="utf-8") as f:
    json.dump({
        "file": "photopdf.pdf",
        "text_phrase": TEXT_PHRASE,
        # VLM 描述里该出现的词。给一组同义候选而不是单个词 ——
        # 判定按"命中任一"算，不然是在考 VLM 的用词习惯而不是链路。
        "image_keywords": ["饼", "扇形", "圆", "环", "图表", "红", "蓝", "黄", "pie", "chart"],
        "embedded_images": n_img,
    }, f, ensure_ascii=False, indent=2)
print(f"清单 {mp}")
