"""综述生成 prompt 集中管理。"""

DECOMPOSE_TOPIC = """你是一个学术研究助手。请将以下研究主题分解为 3-5 个检索子查询，
每个子查询覆盖不同的研究角度或维度。

研究主题: {topic}
用户要求: {requirements}

每行一个查询，不要编号，不要解释。"""

REVISE_SEARCH_QUERIES = """你是一个学术研究助手。用户对上一轮检索结果不满意，请根据反馈重新生成 3-5 个检索子查询。

研究主题: {topic}
用户要求: {requirements}

上一轮使用的查询:
{previous_queries}

用户反馈:
{feedback}

请根据用户反馈调整查询策略。例如，如果用户要求更多中文文献，请生成中文关键词查询。
每行一个查询，不要编号，不要解释。"""

ANALYZE_PAPER = """你是一个学术文献分析专家。请分析以下论文并提取关键信息。

论文标题: {title}
作者: {authors}
摘要: {abstract}

请提取:
1. 核心研究问题
2. 主要方法/方法论
3. 关键发现/结论
4. 与研究主题"{topic}"的相关性（高/中/低）
5. 可引用的关键观点

以 JSON 输出: {{"research_question": "", "methodology": "", "key_findings": "", "relevance": "", "quotable_points": []}}"""

GENERATE_OUTLINE = """你是一个学术综述写作专家。根据以下文献分析结果，生成一份结构化的综述大纲。

研究主题: {topic}
用户要求: {requirements}

已分析的文献:
{analyses_summary}

请生成 Markdown 格式的大纲，包含:
- 引言（研究背景、问题提出）
- 文献分类与分析（按主题/方法/时间线组织）
- 讨论（趋势、争议、研究空白）
- 结论与展望

在每个小节标注应引用哪些文献。"""

REVISE_OUTLINE = """你是一个学术综述写作专家。用户对当前大纲提出了修改意见，请根据反馈修改大纲。

研究主题: {topic}

当前大纲:
{current_outline}

用户反馈:
{feedback}

可参考的文献分析:
{analyses_summary}

请根据用户反馈修改大纲，保持 Markdown 格式。只修改需要调整的部分，保留合理的结构。"""

GENERATE_REVIEW = """你是一个学术综述写作专家。根据以下大纲和文献分析，撰写一篇完整的学术综述。

研究主题: {topic}
用户要求: {requirements}

大纲:
{outline}

文献分析:
{analyses_summary}

要求:
- 学术规范，逻辑严谨
- 每个论点有文献支撑
- 合理使用过渡句连接段落
- 字数约 3000-5000 字
- 结尾包含参考文献列表"""

REVISE_REVIEW = """你是一个学术综述写作专家。用户对当前综述提出了修改意见，请根据反馈修改。

研究主题: {topic}

大纲:
{outline}

当前综述:
{current_draft}

用户反馈:
{feedback}

请根据用户反馈修改综述。只修改需要调整的部分，保持整体结构和学术规范。"""
