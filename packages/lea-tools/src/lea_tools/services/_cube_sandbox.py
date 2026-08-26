"""CubeSandbox 原生客户端 — 不依赖 E2B SDK，直接用 httpx 调 API。

通过 HTTPS + Host header 绕过 DNS，从任何机器都能连。

产物持久化策略（v2 · NFS host-mount —— 详见 SANDBOX_NFS_STORAGE.md）：
- 2 组 host-mount：/workspace (RW session) + /opt/skills (RO session，只含本
  agent 绑定的 skill，由 skill_provisioner 建沙箱前投递)
- 沙箱死了 workspace 里的文件仍在 NFS 上，纯 readdir 即可给用户看
- run_code 前 prepend _RUNTIME_INJECT（cwd=/workspace，OUTPUT_DIR 指 output）
- 老方案（差集扫 + S3 push）已下线；ArtifactPusher 仅保留给冷层归档 worker 用
"""

from __future__ import annotations

import json as _json
import os
import time
from dataclasses import dataclass, field

import httpx
import structlog

from lea_tools.services.artifact_pusher import ArtifactPusher
from lea_tools.services.nfs_provisioner import build_host_mount, ensure_session_dir

logger = structlog.get_logger()


# 运行时注入 —— 每次 run_code 前 prepend。
#
# cwd 是 /workspace 而不是 /workspace/output：工作区分两层语义
#   /workspace          干活的地方（脚本、依赖、中间文件、临时数据）
#   /workspace/output   只放最终产物（用户会想下载/查看的东西）
# 早先把 cwd 设成 output 导致 npm/pip 装的东西、中间脚本全落进 output，
# "产物"这个语义就废了 —— 抽屉里 352 个 .d.ts 跟一个 pptx 混在一起。
_RUNTIME_INJECT = """\
import os, sys
sys.dont_write_bytecode = True
os.makedirs('/workspace/output', exist_ok=True)
os.chdir('/workspace')
os.environ['OUTPUT_DIR'] = '/workspace/output'
os.environ['NPM_CONFIG_PREFIX'] = '/tmp/npm-global'
os.environ['NPM_CONFIG_CACHE']  = '/tmp/npm-cache'
os.environ['npm_config_prefix'] = '/tmp/npm-global'
os.environ['npm_config_cache']  = '/tmp/npm-cache'
"""


@dataclass
class CubeExecution:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    # 相对 /workspace 的路径，如 "report.xlsx"、"charts/plot.png"、"src/App.vue"。
    # 差集扫描 + 剪枝目录后已 push 到 S3；前端拿来在气泡里显示"本次新增 N 个文件"
    # 提示（详细文件列表在右侧抽屉里从 S3 list_objects 拿）。
    artifacts: list[str] = field(default_factory=list)
    duration_ms: int = 0


# os.walk 剪枝目录：这些目录里的文件几乎肯定不是"产物"（依赖、缓存、隐藏元数据）。
# 剪掉能大幅加速扫描 —— site-packages / node_modules 动辄数千文件。
_ARTIFACT_PRUNE_DIRS = frozenset({
    "__pycache__", ".cache", ".ipynb_checkpoints", ".git", ".venv",
    "venv", "node_modules", "site-packages", ".pytest_cache", ".mypy_cache",
    "skill",  # skill bundle 也剪掉
})
# 差集扫描的文件数上限
_ARTIFACT_MAX_FILES = 10000


# 打在沙箱 metadata 上的归属标记。
#
# 这两个键是**跨进程找回沙箱的唯一依据** —— lea-tools 多副本时，建沙箱的那个副本和
# 后续 exec 的副本通常不是同一个，后者要靠 `GET /sandboxes?metadata=lea.session_id=<id>`
# 把沙箱查回来（Cube 支持这个过滤，正反都验过；`?state=` 反而是被忽略的）。
#
# 前缀 `lea.` 是为了跟 Cube 自己的 `cube.*` 元数据分开 —— 那边有十来个键，混在一起
# 以后谁都不敢删。
META_SESSION = "lea.session_id"
META_TENANT = "lea.tenant_id"

# 接管已有沙箱时用的域名。
#
# 建沙箱时域名是从创建响应里拿的，但 `GET /sandboxes` 列表**不返回 domain**（实测），
# 所以接管的时候只能用默认值。同一集群里它是常量，所以实践上没问题；换集群时用
# CUBE_DOMAIN 覆盖，免得表现成"沙箱找得到但连不上"这种很难查的样子。
DEFAULT_DOMAIN = "cube.app"


class SandboxLookupError(RuntimeError):
    """问不到 Cube。跟"这个会话没有沙箱"是两回事，必须分开。

    混在一起的后果是具体的：查询失败当成"没有"，调用方就会去建**第二个**沙箱，
    于是同一会话两个 /workspace，用户看到的文件取决于请求落到哪个 —— 时有时无。
    """


def find_session_sandbox(session_id: str) -> dict | None:
    """问 Cube：这个会话有没有还活着的沙箱？有就返回 {sandbox_id, tenant_id}。

    这是"lea-tools 是无状态中间层"这句话得以成立的那一步：沙箱本来就住在 Cube 里，
    lea-tools 只拿着个句柄，所以任何副本都该能问出来，而不是各自记在进程内存里。

    paused 的沙箱也会被返回，这是对的 —— Cube 的 on_timeout=pause + auto_resume
    意味着 paused 只是"冻着"，下次请求会自动醒，工作区和装好的依赖都还在。
    """
    if not session_id:
        return None
    api_url = os.getenv("CUBE_API_URL", "http://localhost:3000")
    try:
        resp = httpx.get(
            f"{api_url}/sandboxes",
            params={"metadata": f"{META_SESSION}={session_id}"},
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:
        logger.warning("cube_lookup_failed", session_id=session_id, error=str(e))
        raise SandboxLookupError(f"cube lookup failed: {e}") from e

    if not isinstance(rows, list) or not rows:
        return None
    # 正常只会有一个。多于一个说明以前漏建过（比如这个补丁之前的版本），取最新的，
    # 别静默挑第一个 —— 那样每次挑到哪个取决于 Cube 的返回顺序。
    rows.sort(key=lambda r: r.get("startedAt") or "", reverse=True)
    if len(rows) > 1:
        logger.warning("cube_duplicate_sandboxes", session_id=session_id,
                       count=len(rows),
                       chosen=rows[0].get("sandboxID"))
    row = rows[0]
    sandbox_id = row.get("sandboxID")
    if not sandbox_id:
        return None
    return {
        "sandbox_id": sandbox_id,
        "tenant_id": (row.get("metadata") or {}).get(META_TENANT, ""),
    }


def _kill_sandbox(api_url: str, sandbox_id: str) -> None:
    resp = httpx.delete(f"{api_url}/sandboxes/{sandbox_id}", timeout=10)
    # 已经没了也算成功 —— 多副本同时回收时这是常态，不是错误。
    if resp.status_code not in (200, 202, 204, 404):
        resp.raise_for_status()


def list_reapable_sandboxes(idle_timeout: float) -> list[dict]:
    """列出可以回收的沙箱：我们建的、已经 paused、且 paused 超过 idle_timeout。

    判据全部取自 Cube，不掺任何本地状态 —— 这样任何副本得到的结论都一样，进程重启
    也不影响。以前的回收逻辑遍历的是 lea-tools 进程内的字典，于是重启一次，之前建的
    沙箱就永远没人回收了。

    只挑 paused 的：Cube 配的是 on_timeout=pause（15 分钟），所以"已 paused"本身就
    是"至少闲了 15 分钟"的权威证据。running 的一概不动 —— 判断一个 running 沙箱忙不忙
    需要本地视角，而多副本下没有哪个副本的视角是全的。

    endAt 是它转入 paused 的时刻（实测：startedAt 之后正好 900 秒），拿它当闲置起点。
    """
    api_url = os.getenv("CUBE_API_URL", "http://localhost:3000")
    # 全量列，归属过滤放本地做。
    #
    # 本来想用 ?metadata=lea.session_id= 让 Cube 帮忙筛"有这个键的"，实测那是
    # "值等于空串"的意思，返回 0 条 —— 而且是**静默**的 0 条：没有报错、没有异常，
    # 回收器看起来一直在正常运行，实际一个都扫不到，沙箱慢慢漏光。
    # Cube 的 metadata 过滤只支持精确的 key=value。
    resp = httpx.get(f"{api_url}/sandboxes", timeout=10)
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list):
        return []

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    out = []
    for row in rows:
        meta = row.get("metadata") or {}
        session_id = meta.get(META_SESSION)
        if not session_id:
            continue  # 不是我们建的，不碰
        if row.get("state") != "paused":
            continue
        end_at = row.get("endAt")
        if not end_at:
            continue
        try:
            paused_at = datetime.fromisoformat(end_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        idle = (now - paused_at).total_seconds()
        if idle < idle_timeout:
            continue
        sandbox_id = row.get("sandboxID")
        if not sandbox_id:
            continue
        out.append({
            "session_id": session_id,
            "sandbox_id": sandbox_id,
            "idle_seconds": idle,
            "kill": lambda sid=sandbox_id: _kill_sandbox(api_url, sid),
        })
    return out


class CubeSandbox:
    def __init__(self, session_id: str = "", tenant_id: str = "",
                 sandbox_id: str | None = None):
        """sandbox_id 为空则建新沙箱；给了就是**接管**一个已经存在的。

        接管路径存在的理由见 find_session_sandbox：句柄里除了 sandbox_id 没有别的
        不可重建的东西（每个方法都只用 sandbox_id + domain 拼 envd 的 Host 头，
        _client 是个普通 httpx.Client），所以换个进程照样能接着用。
        """
        self._api_url = os.getenv("CUBE_API_URL", "http://localhost:3000")
        self._proxy_host = os.getenv("CUBE_PROXY_HOST", "")
        self._template = os.getenv("CUBE_TEMPLATE_ID", "")
        self._domain = os.getenv("CUBE_DOMAIN", DEFAULT_DOMAIN)
        self._session_id = session_id
        self._tenant_id = tenant_id
        self._artifact_pusher = ArtifactPusher()

        if not self._proxy_host:
            from urllib.parse import urlparse
            parsed = urlparse(self._api_url)
            self._proxy_host = parsed.hostname or "localhost"

        self._client = httpx.Client(verify=False, timeout=60)

        if sandbox_id:
            self.sandbox_id = sandbox_id
            self.host_mounted = False  # 接管时不知道，也没人读这个字段
            logger.info("cube_sandbox_attached",
                        session_id=session_id, sandbox_id=sandbox_id)
            return

        # ── 以下是建新沙箱 ──────────────────────────────────────
        #
        # 归属标记无条件打上（只要有 session_id）—— 它跟 host-mount 是两件事，
        # 以前挤在同一个 if 里：没有共享存储时 metadata 整个不传，于是沙箱查不回来。
        # 本机开发正是"没有共享存储"的那种情况，所以这个耦合会让多副本的问题
        # 在开发环境下永远复现不了。
        metadata = {}
        if session_id:
            metadata[META_SESSION] = session_id
        if tenant_id:
            metadata[META_TENANT] = tenant_id

        # host-mount 只在"我们能建出目录"时请求 —— Cube 不会自动创建 hostPath，
        # 目录不存在会直接 500（mount: special device does not exist）。
        # 建得出目录 ⇒ lea-tools 跟节点共享存储 ⇒ 挂载有效（生产路径）。
        # 建不出   ⇒ 无共享存储（本机开发）⇒ 不挂，退化成沙箱生命周期内的
        #            本地 fs，文件走 envd 读（见 file_store.SandboxFileStore）。
        self.host_mounted = False
        if tenant_id and session_id:
            if ensure_session_dir(tenant_id, session_id) is not None:
                metadata["host-mount"] = build_host_mount(tenant_id, session_id)
                self.host_mounted = True
            else:
                logger.info(
                    "host-mount skipped: shared storage not writable from lea-tools",
                    session_id=session_id,
                )

        resp = httpx.post(
            f"{self._api_url}/sandboxes",
            json={
                "templateID": self._template,
                "timeout": 900,
                "lifecycle": {"on_timeout": "pause", "auto_resume": True},
                **({"metadata": metadata} if metadata else {}),
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self.sandbox_id = data["sandboxID"]
        self._domain = data.get("domain", self._domain)

    def _envd_host(self, port: int = 49999) -> str:
        return f"{port}-{self.sandbox_id}.{self._domain}"

    def _scan_workspace(self) -> set[str]:
        """在沙箱里跑 os.walk 扫 /workspace，返回相对路径集合。失败返空集不阻塞。

        剪枝大目录（node_modules/venv/skill/…）+ 上限保护，避免扫崩。
        """
        prune = sorted(_ARTIFACT_PRUNE_DIRS)
        code = (
            "import os\n"
            f"_PRUNE = set({prune!r})\n"
            f"_MAX = {_ARTIFACT_MAX_FILES}\n"
            "_n = 0\n"
            "for root, dirs, files in os.walk('/workspace'):\n"
            "    dirs[:] = [d for d in dirs if d not in _PRUNE]\n"
            "    for f in files:\n"
            "        p = os.path.join(root, f)\n"
            "        print(os.path.relpath(p, '/workspace'))\n"
            "        _n += 1\n"
            "        if _n >= _MAX:\n"
            "            import sys\n"
            "            print('__SCAN_TRUNCATED__', file=sys.stderr)\n"
            "            break\n"
            "    if _n >= _MAX:\n"
            "        break\n"
        )
        try:
            host = self._envd_host()
            resp = self._client.post(
                f"https://{self._proxy_host}:443/execute",
                headers={"Host": host, "Content-Type": "application/json"},
                json={"code": code, "context_id": None, "language": None, "env_vars": None},
                timeout=15,
            )
            files: set[str] = set()
            for line in resp.text.strip().split("\n"):
                if not line:
                    continue
                try:
                    msg = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                if msg.get("type") == "stdout":
                    for name in msg.get("text", "").splitlines():
                        name = name.strip().replace("\\", "/")
                        if name:
                            files.add(name)
            return files
        except Exception as e:
            logger.warning("cube_workspace_scan_failed", error=str(e))
            return set()

    def _push_artifacts(self, rel_paths: list[str]) -> list[str]:
        """把 rel_paths 里的文件从沙箱里读出来 push 到 S3。返回真正 push 成功的列表。

        失败的文件跳过（记 warn），不阻塞整体流程 —— 保证用户至少能看到部分产物。
        """
        if not rel_paths or not self._session_id:
            return []
        succeeded: list[str] = []
        for rel in rel_paths:
            try:
                data = self.read_file(f"/workspace/{rel}")
                self._artifact_pusher.upload(self._session_id, rel, data)
                succeeded.append(rel)
            except Exception as e:
                logger.warning("cube_artifact_push_failed",
                              session_id=self._session_id, path=rel, error=str(e))
        return succeeded

    def run_code(self, code: str, language: str = "python", timeout: int = 60) -> CubeExecution:
        t0 = time.time()

        # 运行时注入：cwd 到 output + 禁 pyc + npm 环境变量
        if language == "python":
            code = _RUNTIME_INJECT + "\n" + code

        host = self._envd_host()
        resp = self._client.post(
            f"https://{self._proxy_host}:443/execute",
            headers={"Host": host, "Content-Type": "application/json"},
            json={"code": code, "context_id": None, "language": None, "env_vars": None},
            timeout=timeout,
        )

        stdout, stderr, exit_code = "", "", 0
        for line in resp.text.strip().split("\n"):
            if not line:
                continue
            try:
                msg = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if msg.get("type") == "stdout":
                stdout += msg.get("text", "")
            elif msg.get("type") == "stderr":
                stderr += msg.get("text", "")
            elif msg.get("type") == "error":
                exit_code = 1
                stderr += msg.get("traceback", msg.get("value", ""))

        # NFS host-mount 模式下不再 push S3。文件直接在 NFS 上，
        # 前端抽屉 readdir NFS 即可。artifacts 字段从 NFS diff 构建（Phase 3）。
        return CubeExecution(
            stdout=stdout, stderr=stderr, exit_code=exit_code,
            artifacts=[],
            duration_ms=int((time.time() - t0) * 1000),
        )

    def write_file(self, path: str, data: bytes) -> None:
        host = self._envd_host(port=49983)
        resp = self._client.post(
            f"https://{self._proxy_host}:443/files",
            headers={"Host": host, "Content-Type": "application/octet-stream"},
            params={"path": path},
            content=data,
        )
        # 之前这里丢掉了响应，于是 4xx/5xx（磁盘满、路径不可写、envd 挂了）全都
        # 表现为"写成功"，调用方毫不知情。read_file 一直有这道，写侧漏了。
        resp.raise_for_status()

    def read_file(self, path: str) -> bytes:
        host = self._envd_host(port=49983)
        resp = self._client.get(
            f"https://{self._proxy_host}:443/files",
            headers={"Host": host},
            params={"path": path},
        )
        resp.raise_for_status()
        return resp.content

    def kill(self) -> None:
        try:
            httpx.delete(
                f"{self._api_url}/sandboxes/{self.sandbox_id}",
                timeout=10,
            )
        except Exception as e:
            logger.warning("cube_sandbox_delete_failed",
                          sandbox_id=self.sandbox_id, error=str(e))
