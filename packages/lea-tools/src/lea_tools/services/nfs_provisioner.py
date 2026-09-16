"""NFS 目录供应 — 确保 session 目录存在 + 构建 host-mount metadata。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import structlog

from lea_tools.config import get_nfs_settings
from lea_tools.services.file_store import UPLOADS_MOUNT

logger = structlog.get_logger()

# tenant/session id 允许字母数字 + 短横 + 下划线（UUID 和 slug 都覆盖）。
# 关键是不含 / . \ 和 NUL —— 那些才会导致路径穿越。
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _assert_id(name: str, value: str) -> None:
    if not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {name}: {value!r}")


def ensure_session_dir(tenant_id: str, session_id: str) -> Path | None:
    """尽力建 session 级目录（workspace + skills），返回 session 根路径。

    lea-tools 未必跟 Cube 节点挂同一份存储（本机开发、或生产里 lea-tools 独立
    部署）。这时本机 mkdir 无意义甚至失败 —— hostPath 由 cubelet 在节点侧创建。
    所以这里是 best-effort：建不了就返 None，不阻塞沙箱创建。
    """
    _assert_id("tenant_id", tenant_id)
    _assert_id("session_id", session_id)

    cfg = get_nfs_settings()
    root = Path(cfg.root)
    if not root.is_dir():
        logger.debug("nfs root not present locally, skip mkdir", root=str(root))
        return None

    base = root / "tenants" / tenant_id / "sessions" / session_id
    try:
        (base / "workspace").mkdir(parents=True, exist_ok=True)
        (base / "skills").mkdir(parents=True, exist_ok=True)
        # uploads 是用户上传的原件，**workspace 的同级**。见 build_host_mount 的说明。
        (base / "uploads").mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("ensure_session_dir failed", base=str(base), error=str(e))
        return None
    return base


def build_host_mount(tenant_id: str, session_id: str) -> str:
    """构建 Cube sandbox 的 host-mount JSON 字符串（3 组挂载）。

    skill 只挂 session 自己那份（`skill_provisioner` 按 agent 绑定拷进来的），
    不挂全平台共享目录 —— 共享挂载会让任意 session `ls /opt/skills` 列出平台
    所有 skill，包括该用户没绑定/没买的，等于白送。没拷进来的 skill 在沙箱里
    根本不存在，是这一层唯一的隔离手段。

    ## uploads 为什么是 workspace 的同级，而且只读

    用户上传的原件（`file_stager` 从 OBS 物化下来的）挂在 /mnt/uploads。

    **不放进 workspace**，因为 `_cube_sandbox.py` 收尾时会把 /workspace 下的东西
    全推 S3 当产物：上传件落进去就是同一份字节存两遍，而且污染文件抽屉 ——
    用户看到自己刚传的文件出现在"AI 生成的产物"里。

    **只读**，因为原件必须可回溯。用户说"改第 3 页"时想要的是一份新文件，改坏了
    还能重来；原地覆盖之后原件就没了。代价是模型得 read → 改 → 写到 /workspace，
    多一步，但这一步同时让产物归属变得明确（凡是 /workspace 里的都是这一轮产出的）。

    顺带这条也让"改完的文件"自动进抽屉：写 /workspace 就会被推 S3，不需要模型
    额外声明什么。OpenAI 那边靠模型发 annotation 才能取回产物，漏发就拿不到 ——
    扫目录这个做法不依赖模型的配合。
    """
    _assert_id("tenant_id", tenant_id)
    _assert_id("session_id", session_id)

    cfg = get_nfs_settings()
    root = cfg.root
    tid, sid = tenant_id, session_id

    mounts = [
        {"hostPath": f"{root}/tenants/{tid}/sessions/{sid}/workspace",
         "mountPath": "/workspace", "readOnly": False},
        {"hostPath": f"{root}/tenants/{tid}/sessions/{sid}/skills",
         "mountPath": "/opt/skills", "readOnly": True},
        {"hostPath": f"{root}/tenants/{tid}/sessions/{sid}/uploads",
         "mountPath": UPLOADS_MOUNT, "readOnly": True},
    ]
    return json.dumps(mounts)
