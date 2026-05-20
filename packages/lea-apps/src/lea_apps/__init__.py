"""LEA Apps — 可插拔应用（分产品）"""

from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lea_graph.protocol import AppProtocol


def discover_apps() -> list[AppProtocol]:
    """扫描 lea_apps 下所有子包，加载实现了 AppProtocol 的 App。

    约定：每个App子包的 app.py 中有一个 create_app() 函数返回 App 实例。
    """
    import lea_apps

    apps: list[AppProtocol] = []
    for info in pkgutil.iter_modules(lea_apps.__path__):
        if not info.ispkg:
            continue
        try:
            mod = importlib.import_module(f"lea_apps.{info.name}.app")
            factory = getattr(mod, "create_app", None)
            if factory:
                apps.append(factory())
        except Exception as exc:
            import structlog
            structlog.get_logger().warning("app_load_failed", app=info.name, error=str(exc))
    return apps
