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


# ═════════════════════════════════════════════════════════════════
# JSON 输出模式 —— 对齐前端 SlideRenderer.vue 的 SlideData schema
# 6 种 legacy layout：title / content / two_column / image_left / comparison / blank
# ═════════════════════════════════════════════════════════════════

SLIDE_JSON_SYSTEM_PROMPT = """\
你是资深教育课件设计专家，擅长将知识点转化为逻辑清晰、层次分明的演示文稿。

# 输出规范
必须输出严格的 JSON 对象（且仅 JSON，无任何解释、无 markdown 包裹），根字段：
{
  "version": 1,
  "theme": {"id": "edu-warm", "primary": "#1a73e8", "secondary": "#fbbc04",
            "background": "#ffffff", "text": "#202124"},
  "metadata": {"title": "演示文稿总标题", "total_pages": N},
  "slides": [ ... ]
}

# 每张 slide 结构
{
  "layout": "title" | "content" | "two_column" | "image_left" | "comparison" | "blank",
  "title": "本页标题",
  "subtitle": "副标题（可选）",
  "elements": [ ... ],
  "column_labels": {"L": "左栏标签", "R": "右栏标签"},
  "speaker_notes": "演讲者备注（50-100 字讲授脚本）"
}

# elements 数组内的对象类型
- {"type": "heading", "content": "小标题", "level": 2}
- {"type": "text",    "content": "段落文本"}
- {"type": "list",    "items": ["要点1", "要点2"], "ordered": false}
- {"type": "callout", "content": "重要提示", "style": "info"}
- two_column 布局下，元素额外带 "column": "L" 或 "R"
- image_left 布局的 image 元素只需 {"type": "image", "caption": "图注", "alt": "描述"}
  不要给 url 字段（前端占位）

# Layout 使用规范
| layout      | 用途                                        |
|-------------|---------------------------------------------|
| title       | 封面页（大标题 + 副标题）                   |
| content     | 标准单栏正文，最常用                        |
| two_column  | 双栏对比（如"传统方法 vs 新方法"）需 column_labels |
| image_left  | 图文页（左图 + 右文）                       |
| comparison  | 多维度结构化对比                            |
| blank       | 过渡/思考题/开放提问                        |

# 教育调性要求
1. **信息密度**：每页 3-5 要点，禁止堆砌
2. **叙事结构**：title → content 引入 → 2-3 页核心 (content/two_column) → 案例 (image_left/comparison) → 总结 (content 或 blank)
3. **speaker_notes 必写**：每页 50-100 字，明确"想表达什么、怎么讲、可以问学生什么问题"
4. **list.items 每条 ≤ 20 字**，多个短句而非一长段
5. **禁用**：emoji / markdown 语法 (`**加粗**` 会被当字面字符) / 外部 URL

# 严格禁令
- 不要输出任何 JSON 之外的文字
- 不要用 ```json 包裹
- 第一个字符必须是 `{`，最后一个必须是 `}`
"""


SLIDES_JSON_USER_PROMPT = """\
请根据以下主题设计一份完整的教学演示文稿：

主题：{topic}

要求：
- 页数：如用户未指定，默认 5-7 页
- 严格遵循 6 种 layout 白名单：title / content / two_column / image_left / comparison / blank
- 首页必须是 title 布局，末页 content 或 blank 做总结
- 每页必须包含 speaker_notes

直接输出完整 JSON，第一个字符必须是 `{`。"""


VALID_LAYOUTS = {"title", "content", "two_column", "image_left", "comparison", "blank"}

DEFAULT_THEME = {
    "id": "edu-warm",
    "primary": "#1a73e8",
    "secondary": "#fbbc04",
    "background": "#ffffff",
    "text": "#202124",
}


def extract_json_object(raw: str) -> str:
    """从 LLM 输出中提取 JSON 对象。处理思考标签、markdown 包裹等干扰。"""
    raw = raw.strip()

    # qwen 的 </think>
    think_end = raw.rfind("</think>")
    if think_end >= 0:
        raw = raw[think_end + 8:].strip()

    # markdown 代码块
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()

    # 直接以 { 开头
    if raw.startswith("{"):
        return raw

    # 找第一个 { 到最后一个 }
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return raw[start : end + 1]

    return raw


def normalize_slide_data(data: Any, topic: str = "") -> dict:
    """校验并归一 LLM 输出。未知 layout 强制为 content，缺字段补默认。"""
    if not isinstance(data, dict):
        return _fallback_slide_data(topic)

    slides = data.get("slides")
    if not isinstance(slides, list) or not slides:
        return _fallback_slide_data(topic)

    normalized_slides = []
    for i, s in enumerate(slides):
        if not isinstance(s, dict):
            continue
        layout = s.get("layout", "content")
        if layout not in VALID_LAYOUTS:
            layout = "content"
        elements = s.get("elements") or []
        if not isinstance(elements, list):
            elements = []
        slide = {
            "layout": layout,
            "title": str(s.get("title", "")),
            "elements": elements,
            "speaker_notes": str(s.get("speaker_notes", "")),
        }
        if s.get("subtitle"):
            slide["subtitle"] = str(s["subtitle"])
        if s.get("column_labels") and isinstance(s["column_labels"], dict):
            slide["column_labels"] = s["column_labels"]
        normalized_slides.append(slide)

    if not normalized_slides:
        return _fallback_slide_data(topic)

    theme = data.get("theme")
    if not isinstance(theme, dict):
        theme = DEFAULT_THEME.copy()
    else:
        for k, v in DEFAULT_THEME.items():
            theme.setdefault(k, v)

    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.setdefault("title", topic or "演示文稿")
    metadata["total_pages"] = len(normalized_slides)

    return {
        "version": 1,
        "theme": theme,
        "metadata": metadata,
        "slides": normalized_slides,
    }


def _fallback_slide_data(topic: str) -> dict:
    """LLM 完全失败时的兜底 3 页演示文稿。"""
    return {
        "version": 1,
        "theme": DEFAULT_THEME.copy(),
        "metadata": {"title": topic or "演示文稿", "total_pages": 3},
        "slides": [
            {
                "layout": "title",
                "title": topic or "演示文稿",
                "subtitle": "自动生成",
                "elements": [],
                "speaker_notes": "开场介绍主题",
            },
            {
                "layout": "content",
                "title": "核心内容",
                "elements": [
                    {"type": "text", "content": "内容生成失败，请重新生成"},
                ],
                "speaker_notes": "此页为兜底占位，请重新触发生成",
            },
            {
                "layout": "blank",
                "title": "总结",
                "elements": [],
                "speaker_notes": "总结与提问环节",
            },
        ],
    }


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
