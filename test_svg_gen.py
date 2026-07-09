"""Test SVG generation with Bento Grid prompt."""
import asyncio


SVG_PROMPT = """作为精通信息架构与 SVG 编码的专家，将以下内容转化为一张高质量的 SVG 演示文稿页面。

严格要求：
1. SVG viewBox: 0 0 1280 720
2. 配色：深蓝色背景(#0f172a)，卡片用白色(#ffffff)带圆角(16px)和阴影
3. 使用 Bento Grid 卡片式布局（2x2网格）
4. 标题用大字号白色放在顶部(y<130)
5. 字体: font-family="Microsoft YaHei, sans-serif"
6. 每张卡片左侧有一条6px宽的彩色竖条
7. 每张卡片内有一个自定义的矢量图标，要求：
   - 图标用 <g transform="translate(卡片内x, 卡片内y)"> 包裹，确保坐标相对于卡片
   - 图标画布约 32x32，用 stroke 线条风格（stroke-width=2, fill=none），类似 Lucide/Feather 图标风格
   - 图标颜色与该卡片的强调色一致
8. 所有元素通过嵌套的 <g transform="translate(x,y)"> 定位，坐标必须是相对于父级的

只输出<svg>...</svg>代码，不要任何思考过程或解释。第一个字符必须是 < 。

内容：
页面标题：终身教育的核心内涵与意义
- 学习贯穿生命全程：从摇篮到坟墓，学习永不停止
- 空间多元化：学校、家庭、社区、职场均为学习场所
- 促进个人全面发展：提升素养、实现自我价值
- 推动学习型社会：全民终身学习是社会进步基石"""


async def main():
    from langchain_openai import ChatOpenAI
    from lea_graph.llm import _get_llm_settings

    settings = _get_llm_settings()

    # 用 qwen3.6-plus 测试 SVG 生成
    model = ChatOpenAI(
        model="qwen3.6-plus",
        openai_api_key=settings.tenant_api_key,
        openai_api_base=f"{settings.tenant_api_base}/v1",
        streaming=False,
        temperature=0.7,
        max_tokens=4096,
        request_timeout=300.0,
    )

    response = await model.ainvoke([{"role": "user", "content": SVG_PROMPT}])

    svg = response.content.strip()
    if "```" in svg:
        start = svg.find("<svg")
        end = svg.rfind("</svg>") + 6
        if start >= 0 and end > start:
            svg = svg[start:end]

    with open("/tmp/test_slide.svg", "w", encoding="utf-8") as f:
        f.write(svg)

    print(f"Length: {len(svg)} chars")
    print(f"Valid: {svg.startswith('<svg')}")
    print(f"Has cards: {'<rect' in svg}")
    print(f"Has text: {'<text' in svg}")
    print("---PREVIEW---")
    print(svg[:300])


asyncio.run(main())
