"""LLM Provider 模型 — 租户级 LLM 接入配置。"""

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text, func

from lea_config.database import DEFAULT_TENANT_ID, Base, TenantMixin


class LLMProvider(TenantMixin, Base):
    __tablename__ = "llm_providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    base_url = Column(String(500), nullable=False)
    api_key = Column(String(500), default="")
    models = Column(Text, default="")

    model_limits = Column(JSON, nullable=True)
    """每个模型的上下文窗口：{"qwen3-max": 258048}。

    ── 为什么这个数必须是数据，不能写在 lea-agent 里 ──────────────────

    它原来是 Go 侧一个按模型名前缀匹配的 switch（contextWindowFor）。两个毛病：
    换模型要改代码发版；而且前缀表在模型名多一个后缀时会**静默**落进缺省分支，
    表现只是"压缩时机不对"，没人会注意到。

    也问不到 provider —— 实测这个网关的 /v1/models 返 Forbidden，只开了 chat
    completions。所以来源只能是配置。

    ── 填什么数 ────────────────────────────────────────────────────────

    填**最大输入长度**，不是"总窗口"。一次请求 = [系统提示 + 历史 + 本轮输入]
    + [生成的回复]，窗口管的是两段之和，而这个数只用来约束前一段。

        qwen3-max   258048   （总窗口 262144，输出上限 65536）
        glm-5.1     200000   （公开资料 ~203K，取整往小）

    拿不准就往**小**填：小了只是压缩早跑一次（一次摘要调用），大了会在 provider
    那里整轮失败，那是用户能看到的错误。

    没填这个模型不会拒服 —— lea-agent 走保守兜底（session.fallbackContextWindow）。
    """

    is_default = Column(Boolean, default=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())


def visible_to(tenant_id: str):
    """"这个租户能用哪些 provider"的判据，返回一组 SQLAlchemy 过滤条件。

    本租户 + default 两者都看，跟 skills / tools / mcp_servers / agents 一致
    （models/skill.py:81、models/tool.py:164、models/mcp_server.py:158、
    models/agent.py:72）。平台级资源挂在 default 下给所有租户共享是本仓库的
    一贯做法 —— provider 原先是唯一的例外，写的是 tenant_id 严格相等。

    那个例外的后果是一对叠在一起的故障：provider 只在 default 下建了一行，于是
    非 default 租户（实测 wjtest / test2 / seed-acme）既在 /llm-models 看到空
    列表、换不了模型，又在 internal.py 匹配不到 provider、退到只给 base_url 和
    api_key 的 env 兜底，把上下文窗口按 32k 算 —— 而中转上的模型实际有 200k+。
    现象是"模型选择器是空的"加"对话上下文莫名其妙短"，两者看不出同源。
    """
    return [
        LLMProvider.tenant_id.in_([tenant_id, DEFAULT_TENANT_ID]),
        LLMProvider.enabled == True,  # noqa: E712
    ]


def tenant_first(providers: list["LLMProvider"], tenant_id: str) -> list["LLMProvider"]:
    """把本租户自己的 provider 排在 default 的前面。

    visible_to 放进来两个租户的行，而下面两个消费者都是"取第一个匹配到的"。
    不定序的话同名模型在两个租户下都存在时，选中谁取决于数据库返回顺序 ——
    那是不确定的，而且错的那一侧会让租户自建的 provider 被 default 覆盖掉。

    在 Python 里排而不是 SQL ORDER BY：生产是 MySQL、测试是 SQLite，布尔表达式
    的排序行为在两边不一致，而这个顺序是正确性的一部分，不能让它跟着方言漂。
    """
    return sorted(providers, key=lambda p: 0 if p.tenant_id == tenant_id else 1)


def models_of(provider: "LLMProvider") -> list[str]:
    """provider.models 那一列按逗号切分成模型名列表。

    切分规则本身就是判据的一部分：/llm-models 列出来的名字必须跟 internal.py
    匹配时用的名字完全一致，否则用户选了一个列表里有、运行时选不中的名字,
    请求会悄悄走默认 provider —— 比没有这个列表还糟（见 llm_models.py 的说明）。
    """
    return [m.strip() for m in (provider.models or "").split(",") if m.strip()]


def window_of(provider: "LLMProvider", model_name: str) -> int:
    """从 provider.model_limits 取这个模型的上下文窗口，取不到返回 0。

    原样透出 0 而不是塞个缺省值：lea-agent 那边已经有一个兜底
    （session.fallbackContextWindow，32k），两边各有一个缺省的话它们迟早不一样。
    """
    limits = provider.model_limits if isinstance(provider.model_limits, dict) else {}
    try:
        return max(0, int(limits.get(model_name, 0)))
    except (TypeError, ValueError):
        return 0
