"""Models package — import all models to register with Base.metadata."""

from lea_config.models.agent import Agent  # noqa: F401
from lea_config.models.binding import AgentKbBinding, AgentSkillBinding, AgentToolBinding  # noqa: F401
from lea_config.models.knowledge import KnowledgeBase  # noqa: F401
from lea_config.models.llm_provider import LLMProvider  # noqa: F401
from lea_config.models.mcp_server import MCPServer  # noqa: F401
from lea_config.models.publication import (  # noqa: F401
    AuditEvent,
    PublicationRequest,
    ResourceCheck,
    ResourcePublication,
)
from lea_config.models.retrieval_model import RetrievalModel  # noqa: F401
from lea_config.models.skill import Skill, SkillVersion  # noqa: F401
from lea_config.models.skill_publish import SkillPublishRequest  # noqa: F401
from lea_config.models.team import AgentTeam, AgentTeamMember  # noqa: F401
from lea_config.models.tool import Tool  # noqa: F401
from lea_config.models.user import Role, User, user_roles  # noqa: F401

__all__ = [
    "Agent",
    "AgentKbBinding",
    "AgentSkillBinding",
    "AgentTeam",
    "AgentTeamMember",
    "AgentToolBinding",
    "KnowledgeBase",
    "LLMProvider",
    "MCPServer",
    "AuditEvent",
    "PublicationRequest",
    "RetrievalModel",
    "Role",
    "SkillPublishRequest",
    "Skill",
    "SkillVersion",
    "Tool",
    "User",
    "user_roles",
]
