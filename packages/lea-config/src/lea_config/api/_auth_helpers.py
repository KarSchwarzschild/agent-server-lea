"""共享授权 helpers —— 现在只是 lea_authkit 的转发。

这两个函数已经提到 packages/lea-authkit 里了，因为 **lea-rag 也需要它们**
（它的 /admin/knowledge-bases、/admin/memories 同样要角色门），而它拿不到
lea-config 的 api/ 目录。

这个模块留着只为了不动本包里那二十多个调用点。新代码直接 from lea_authkit import。
"""

from __future__ import annotations

from lea_authkit import ensure_tenant, require_admin

__all__ = ["require_admin", "ensure_tenant"]
