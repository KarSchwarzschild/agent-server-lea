"""SVG 幻灯片 Prompt 模板库。

基于 qwen3.6-plus 实测验证的 prompt 模板，用于生成高质量 SVG 演示文稿页面。
"""

from __future__ import annotations

import re
from typing import Any

VIEWBOX = "0 0 1280 720"

SYSTEM_PROMPT = """\
你是一位精通信息架构与 SVG 编码的演示文稿设计专家。
你的任务是将内容转化为高质量的 SVG 演示文稿页面。

通用规则：
1. SVG viewBox 固定为 0 0 1280 720
2. 字体: font-family="Microsoft YaHei, sans-serif"
3. 所有元素通过嵌套 <g transform="translate(x,y)"> 定位
4. 只输出 <svg>...</svg> 代码，不要任何解释或思考过程
5. 第一个字符必须是 <
6. 禁止使用 emoji 字符，用 SVG 矢量图标替代
7. 图标用 stroke 线条风格（stroke-width=2, fill=none），类似 Lucide 图标
"""

# --- Layout 模板 ---

LAYOUTS: dict[str, str] = {
    "title_page": """\
布局要求（封面页）：
- 深色渐变背景（#0f172a → #1e293b）
- 标题大字号(48-56px)白色，垂直居中偏上
- 副标题(24px)浅灰色(#94a3b8)，标题下方
- 底部可选：作者/日期信息，小字号(16px)
- 添加微妙的装饰元素（渐变圆形、线条等）""",

    "bento_grid": """\
布局要求（Bento Grid 卡片式）：
- 深蓝背景(#0f172a)
- 页面标题：白色大字号(40-44px)放在顶部(y<130)
- 2x2 网格卡片布局，卡片用白色(#ffffff)带圆角(16px)和阴影
- 每张卡片左侧有一条 6px 宽的彩色竖条（不同卡片不同色）
- 每张卡片内有自定义 SVG 矢量图标（32x32，stroke 线条风格）
- 卡片内文字：标题 22px 深色，描述 16-17px 灰色""",

    "timeline": """\
布局要求（时间线）：
- 深蓝背景(#0f172a)
- 页面标题：白色大字号(40px)放在顶部
- 垂直或水平时间线，节点用圆形标记
- 每个节点连接一个内容卡片（白色圆角矩形）
- 节点间用线条连接，颜色渐变
- 文字分层：节点标签(18px)、内容描述(15px)""",

    "comparison": """\
布局要求（对比布局）：
- 深蓝背景(#0f172a)
- 页面标题：白色大字号(40px)顶部居中
- 左右两栏对比，中间用竖线或 VS 标记分隔
- 每栏一个大卡片，不同强调色
- 卡片内：小标题(22px) + 要点列表(16px)
- 用图标或色块区分两侧""",

    "key_points": """\
布局要求（要点列表）：
- 深蓝背景(#0f172a)
- 页面标题：白色大字号(40px)顶部
- 纵向排列 3-5 个要点条目
- 每个条目：左侧圆形编号/图标 + 右侧标题和描述
- 条目间有适当间距，视觉层次清晰
- 可用不同强调色区分各条目""",

    "closing": """\
布局要求（结束页）：
- 深色渐变背景
- "谢谢" 或总结语大字号(48px)居中
- 下方可选：联系方式、二维码占位、要点回顾
- 保持简洁大气""",
}

# placeholder for edit prompt
EDIT_SLIDE_PROMPT = """\
你是 SVG 演示文稿编辑专家。下面是一张幻灯片的当前 SVG 代码和用户的修改要求。
请根据修改要求更新 SVG，保持整体风格、布局和配色不变，只做用户要求的改动。

当前 SVG：
```svg
{current_svg}
```

修改要求：{instruction}

只输出修改后的完整 <svg>...</svg> 代码，不要解释。第一个字符必须是 <。
"""

OUTLINE_PROMPT = """\
根据用户的主题，规划演示文稿大纲。输出 JSON 数组，每个元素代表一页。

要求：
- 第一页必须是 title_page（封面）
- 最后一页是 closing（结束页）
- 中间页根据内容选择合适的 layout
- 每页包含足够的要点内容（不要太空）
- 总页数由用户指定，如未指定默认 5 页

可选 layout: title_page, bento_grid, timeline, comparison, key_points, closing

输出格式（纯 JSON，无其他文字）：
[
  {{"title": "页面标题", "layout": "layout名", "points": ["要点1", "要点2", ...]}},
  ...
]

用户主题：{topic}
"""

PARSE_EDIT_INTENT_PROMPT = """\
分析用户对演示文稿的修改请求，输出 JSON。

当前演示文稿共 {total_pages} 页，大纲如下：
{outline_summary}

用户请求：{message}

输出格式（纯 JSON，无其他文字）：
{{"action": "edit|add|delete", "page": 页码数字, "instruction": "具体修改内容描述"}}

规则：
- page 从 1 开始计数
- "封面" = page 1，"最后一页" = page {total_pages}
- action=edit: 修改指定页内容
- action=add: 在指定 page 位置插入新页（原该位置及之后的页后移）
- action=delete: 删除指定页
"""


def build_slide_prompt(layout: str, title: str, points: list[str]) -> str:
    """构建单页 SVG 生成的用户 prompt。"""
    layout_desc = LAYOUTS.get(layout, LAYOUTS["bento_grid"])
    content_lines = "\n".join(f"- {p}" for p in points)

    return f"""{layout_desc}

内容：
页面标题：{title}
{content_lines}

只输出<svg>...</svg>代码，不要任何思考过程或解释。第一个字符必须是 <。"""


def validate_svg(svg: str) -> tuple[bool, str]:
    """校验 SVG 基本结构。返回 (is_valid, error_message)。"""
    svg = svg.strip()
    if not svg.startswith("<svg"):
        return False, "SVG must start with <svg"
    if not svg.endswith("</svg>"):
        return False, "SVG must end with </svg>"
    if "viewBox" not in svg:
        return False, "Missing viewBox attribute"
    if "<text" not in svg:
        return False, "No <text> elements found"
    return True, ""


def extract_svg(raw: str) -> str:
    """从 LLM 输出中提取 SVG 代码（处理思考过程、markdown 代码块等干扰）。"""
    raw = raw.strip()

    # 处理 qwen 系列的 </think> 标签：取 </think> 之后的内容
    think_end = raw.rfind("</think>")
    if think_end >= 0:
        raw = raw[think_end + 8:].strip()

    # 直接以 <svg 开头
    if raw.startswith("<svg"):
        end = raw.rfind("</svg>")
        if end > 0:
            return raw[: end + 6]
        return raw

    # 查找最后一个完整的 <svg>...</svg> 块（避免思考过程中的引用干扰）
    last_svg_start = raw.rfind("<svg")
    if last_svg_start >= 0:
        end = raw.rfind("</svg>")
        if end > last_svg_start:
            return raw[last_svg_start : end + 6]

    # fallback: 第一个 <svg
    start = raw.find("<svg")
    if start >= 0:
        end = raw.rfind("</svg>")
        if end > start:
            return raw[start : end + 6]

    return raw
