"""CubeSandbox SDK 适配层。

通过官方 ``cubesandbox`` SDK 管理实例、执行 Python 与 Shell 命令、读写文件。

产物持久化策略（v2 · NFS host-mount —— 详见 SANDBOX_NFS_STORAGE.md）：
- 2 组 host-mount：/workspace (RW session) + /opt/skills (RO session，只含本
  agent 绑定的 skill，由 skill_provisioner 建沙箱前投递)
- 沙箱死了 workspace 里的文件仍在 NFS 上，纯 readdir 即可给用户看
- run_code 前 prepend _RUNTIME_INJECT（cwd=/workspace，OUTPUT_DIR 指 output）
- 老方案（差集扫 + S3 push）已下线；ArtifactPusher 仅保留给冷层归档 worker 用
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import UTC

import httpx
import structlog

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


_SHELL_LANGUAGES = frozenset({"bash", "shell", "sh"})


@dataclass
class CubeExecution:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    # 相对 /workspace 的路径，如 "report.xlsx"、"charts/plot.png"、"src/App.vue"。
    # 差集扫描 + 剪枝目录后已 push 到 S3；前端拿来在气泡里显示"本次新增 N 个文件"
    # 提示（详细文件列表在右侧抽屉里从 S3 list_objects 拿）。
    #
    # ⚠️ 这些是"这次执行**碰过**哪些文件"，**不是**用户文件库意义上的"成品"。
    # 后者只认模型显式调 deliver_artifact 声明的那些（写进 session_artifacts）——
    # 不猜、不按扩展名扫、不看修改时间。中间产物、调试图、临时 csv 都会出现在这个
    # 列表里，但它们不该进用户的长期文件库。两个概念同名不同义，别混。
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

    ⚠️ 返回的 state 可能是**过渡态** `pausing` / `resuming`，不只是 running/paused
    —— Cube 的状态机是五个态，快照要存内存+文件系统，一个装过依赖的沙箱转完要一两
    分钟。调用方别拿 `state == "paused"` 当"暂停了没"的唯一判据：看见 pausing 就说明
    转换已经开始了。反过来把过渡态当异常，得到的结论会跟事实相反（验收脚本踩过：
    等到 end_at 之后读到 pausing，报的是"on_timeout=pause 没生效"，而它恰好生效了）。
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
        # state/startedAt/endAt 顺手带出来：它们本来就在这一行里，取它们不多花一次
        # 请求。调用方（GET /sandbox/sessions/{id}）要靠 state 才能观察到
        # running→paused→running 这条自动生命周期 —— 没有它，"暂停/恢复能不能用"
        # 这个问题只能靠等着看下一次调用会不会超时来猜。
        "state": row.get("state", ""),
        "started_at": row.get("startedAt", ""),
        "end_at": row.get("endAt", ""),
    }


def _sandbox_timeout() -> int:
    """建沙箱时给 Cube 的 timeout（秒）—— 到点转 paused（不是销毁，见 lifecycle）。

    这是三层生命周期的第一道线：0~timeout 运行中，timeout~IDLE_TIMEOUT 由 Cube 暂停
    着（下次调用 auto_resume 拉起来），再往后本地回收器真删。

    ## 为什么要能配

    原来是硬编码 900。900 秒对生产是对的（用户去开会回来还能接着用），但它让
    "暂停/恢复"这条路**在验收里根本走不到** —— 要观察一次自动暂停得干等 15 分钟，
    于是这条路一直没被真跑过，而没跑过的路等于不存在（SB-2 那次就是这么栽的）。

    调小它是验收暂停/恢复的唯一办法：设成 60，一分钟后沙箱进 paused，再发一次调用
    看它 auto_resume 起来。生产不设这个变量，就还是 900。
    """
    raw = os.getenv("CUBE_SANDBOX_TIMEOUT", "")
    if not raw:
        return 900
    try:
        val = int(raw)
    except ValueError:
        logger.warning("cube_sandbox_timeout_invalid", raw=raw, fallback=900)
        return 900
    # 下限兜一手：0 或负数在 Cube 那边的含义没验证过，而"沙箱建出来立刻就暂停"
    # 表现为每次调用都要等一次 resume，看起来像随机的超时。
    if val < 30:
        logger.warning("cube_sandbox_timeout_too_small", raw=val, clamped=30)
        return 30
    return val


def _kill_sandbox(api_url: str, sandbox_id: str) -> None:
    """真删一个沙箱。回收器和用户显式删除都走这里。

    ## paused 的沙箱也能删

    Cube 对 paused 沙箱的 DELETE 会先内部 restore 再删（实测 ≤5s），所以不需要
    先 resume 再删这种两步操作。但它多出两个状态码：

      409  restore 失败（快照坏了/资源不够）—— 沙箱还在，重试可能成功
      503  正在状态转换中，带 Retry-After

    这两个都**不算成功**，所以照旧 raise_for_status。回收器那边一次失败会记
    sandbox_reap_failed，下一轮（60s）自己再试 —— 所以这里不做重试，重试语义
    由调用循环承担。
    """
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

    from datetime import datetime
    now = datetime.now(UTC)
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

        接管路径存在的理由见 find_session_sandbox：SDK 的 ``Sandbox.connect`` 以
        sandbox_id 重新连接 Cube，paused 状态会自动恢复，所以换个进程也能继续执行。
        """
        self._api_url = os.getenv("CUBE_API_URL", "http://localhost:3000")
        self._proxy_host = os.getenv("CUBE_PROXY_HOST", "")
        self._template = os.getenv("CUBE_TEMPLATE_ID", "")
        self._domain = os.getenv("CUBE_DOMAIN", DEFAULT_DOMAIN)
        self._session_id = session_id
        self._tenant_id = tenant_id
        if not self._proxy_host:
            from urllib.parse import urlparse
            parsed = urlparse(self._api_url)
            self._proxy_host = parsed.hostname or "localhost"

        from cubesandbox import Config, Sandbox
        self._sdk_config = Config(
            api_url=self._api_url,
            template_id=self._template or None,
            proxy_node_ip=self._proxy_host or None,
            sandbox_domain=self._domain,
        )

        if sandbox_id:
            self.sandbox_id = sandbox_id
            self._sdk = Sandbox.connect(sandbox_id, config=self._sdk_config)
            self.host_mounted = True
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
                raise RuntimeError(
                    "NFS workspace is unavailable; refusing to create a sandbox "
                    "without the session host-mount"
                )
        self._sdk = Sandbox.create(
            template=self._template,
            timeout=_sandbox_timeout(),
            lifecycle={"on_timeout": "pause", "auto_resume": True},
            metadata=metadata or None,
            config=self._sdk_config,
        )
        self.sandbox_id = self._sdk.sandbox_id
        self._domain = self._sdk.domain

    def run_code(self, code: str, language: str = "python", timeout: int = 60) -> CubeExecution:
        t0 = time.time()

        # 运行时注入：cwd 到 output + 禁 pyc + npm 环境变量
        lang = (language or "python").lower()
        if lang in _SHELL_LANGUAGES:
            result = self._sdk.commands.run(
                code, cwd="/workspace", timeout=timeout, user="root"
            )
            return CubeExecution(
                stdout=result.stdout, stderr=result.stderr,
                exit_code=result.exit_code, artifacts=[],
                duration_ms=int((time.time() - t0) * 1000),
            )
        if lang == "python":
            code = _RUNTIME_INJECT + "\n" + code
        else:
            # 跟 MockSandbox 保持一致（exit_code=2）。以前是**静默透传**：不认识的
            # language 原样发给 envd，而 envd 只会当 Python 跑 —— 于是"不支持"表现成
            # 一个莫名其妙的 SyntaxError，看日志的人会去查用户的脚本。
            return CubeExecution(
                exit_code=2, stderr=f"unsupported language: {language}",
                duration_ms=int((time.time() - t0) * 1000),
            )

        result = self._sdk.run_code(code, timeout=timeout)
        stdout = "".join(result.logs.stdout)
        stderr = "".join(result.logs.stderr)
        if result.error:
            stderr += str(result.error)
        return CubeExecution(
            stdout=stdout, stderr=stderr,
            exit_code=1 if result.error else 0,
            artifacts=[], duration_ms=int((time.time() - t0) * 1000),
        )

    def write_file(self, path: str, data: bytes) -> None:
        self._sdk.files.write(path, data)

    def read_file(self, path: str) -> bytes:
        value = self._sdk.files.read(path)
        return value.encode() if isinstance(value, str) else value

    def kill(self) -> None:
        self._sdk.kill()
