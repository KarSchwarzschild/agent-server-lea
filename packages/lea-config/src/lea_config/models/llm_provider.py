"""LLM Provider 模型 — 租户级 LLM 接入配置。"""

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text, func

from lea_config.database import Base, TenantMixin


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
