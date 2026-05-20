"""平台公共Skill注册表。"""

from lea_graph.tools.knowledge import kb_search
from lea_graph.tools.files import write_file, read_file

PLATFORM_TOOLS = [kb_search, write_file, read_file]

__all__ = ["PLATFORM_TOOLS", "kb_search", "write_file", "read_file"]
