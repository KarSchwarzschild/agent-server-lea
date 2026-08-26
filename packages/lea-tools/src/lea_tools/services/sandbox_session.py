"""Sandbox Session Manager — 管理持久沙箱实例的生命周期。

不用 with 语法（避免自动销毁），session 内复用同一个沙箱实例。
超时未使用自动回收。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()

# 本地闲置超时（秒），超过后主动 kill 沙箱。
#
# Cube 侧另有 lifecycle.timeout=900 (15min) 触发 pause（内存冻结、下次请求自动
# resume，见 _cube_sandbox.py）。本地 kill 兜住"用户彻底走了"的情况 —— 单纯 pause
# 不释放 Cube master 侧的 sandbox slot，长期堆积会撑爆 client 上限。
#
# 分层：
#   0-15min idle : running（正常）
#   15-60min idle: Cube 自动 pause（0 CPU，占 slot 不占算力）
#   60min+       : 本地 kill（释放 slot，文件已经 push 到 S3 用户仍能看）
IDLE_TIMEOUT = 3600  # 1 小时


@dataclass
class SandboxSession:
    session_id: str
    sandbox_id: str
    skill_id: str | None = None
    user_id: str = ""
    tenant_id: str = ""
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    _backend: Any = field(default=None, repr=False)

    def touch(self):
        self.last_used_at = time.time()

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_used_at


class SandboxSessionManager:
    """管理多个沙箱 session，提供创建/执行/销毁 API。

    内部维护 session_id → SandboxSession 映射。
    后台定时清理超时实例。
    """

    def __init__(self):
        self._sessions: dict[str, SandboxSession] = {}
        self._cleanup_task: asyncio.Task | None = None

    async def start(self):
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()
        for sess in list(self._sessions.values()):
            await self._kill_backend(sess)
        self._sessions.clear()

    async def _adopt(self, session_id: str) -> SandboxSession | None:
        """本进程没有这个 session 时，去 Cube 把沙箱认回来。

        这是让 lea-tools 真正成为**无状态中间层**的那一步。沙箱住在 Cube 里，
        `_sessions` 只是一份句柄缓存 —— 以前的代码把缓存当成了真源，于是缓存没命中
        就 404。单副本时缓存永远命中所以看不出来；一旦扩容，建沙箱的副本和后续 exec
        的副本不是同一个，用户会随机撞到 "sandbox session not found"，而沙箱明明好好
        活着。重启也一样：dict 清空，此后这个会话的沙箱谁都碰不到了。

        只对 cube 后端有意义 —— mock 沙箱没有远端实体可认，e2b 走的是 SDK 自己的
        那套（要接的话是另一个函数，别在这里混）。
        """
        import os
        if os.getenv("SANDBOX_BACKEND", "mock").lower() != "cube":
            return None

        from lea_tools.services._cube_sandbox import CubeSandbox, find_session_sandbox
        found = await asyncio.to_thread(find_session_sandbox, session_id)
        if not found:
            return None

        backend = CubeSandbox(
            session_id=session_id,
            tenant_id=found["tenant_id"],
            sandbox_id=found["sandbox_id"],
        )
        sess = SandboxSession(
            session_id=session_id,
            sandbox_id=found["sandbox_id"],
            tenant_id=found["tenant_id"],
            _backend=backend,
        )
        self._sessions[session_id] = sess
        logger.info("sandbox_session_adopted",
                    session_id=session_id, sandbox_id=found["sandbox_id"])
        return sess

    async def _require(self, session_id: str) -> SandboxSession:
        """取一个可用的 session，本进程没有就去 Cube 认回来。找不到才 KeyError。"""
        sess = self._sessions.get(session_id)
        if sess and sess._backend:
            sess.touch()
            return sess
        sess = await self._adopt(session_id)
        if not sess or not sess._backend:
            raise KeyError(f"sandbox session not found: {session_id}")
        sess.touch()
        return sess

    async def create(
        self,
        session_id: str,
        skill_id: str | None = None,
        user_id: str = "",
        tenant_id: str = "",
        skills: list[dict] | None = None,
    ) -> SandboxSession:
        if session_id in self._sessions:
            sess = self._sessions[session_id]
            sess.touch()
            return sess

        # 本进程没有，但别的副本（或者重启前的自己）可能已经建过了。不先问一句的话
        # 同一个会话会有两个沙箱：两个 /workspace，用户看到的文件取决于这次请求落到
        # 谁手上 —— 时有时无，最难查的那种。
        adopted = await self._adopt(session_id)
        if adopted:
            # 注意：认回来的沙箱里，skill 是**它创建时**那一份。agent 的 skill 绑定
            # 后来改过的话这里是旧的。重投递也没用 —— host-mount 在沙箱创建那一刻
            # 就固定了（见下面那段注释）。要换 skill 只能重建沙箱，那是另一个决定，
            # 不该由一次普通的 exec 顺手触发。
            adopted.skill_id = skill_id or adopted.skill_id
            adopted.user_id = user_id or adopted.user_id
            return adopted

        # skill 必须在建 backend 之前落盘：Cube 的 host-mount 在沙箱创建那一刻
        # 生效，之后再往 hostPath 写，已起的沙箱看不到。
        if skills:
            from lea_tools.services.skill_provisioner import provision_session_skills
            try:
                # 下载 + 解压 bundle 到共享存储，同步 IO；见 execute 里那段说明。
                await asyncio.to_thread(
                    provision_session_skills, tenant_id, session_id, skills
                )
            except Exception as e:
                # 投递整体失败也要让沙箱起来 —— LLM 读不到 skill 文件是降级，
                # 沙箱起不来是彻底不可用。
                logger.warning("skill_provision_failed",
                               session_id=session_id, error=str(e))

        # 建沙箱是这里面最慢的一步（httpx.post，timeout=30，冷启动实测 >10s）。
        backend = await asyncio.to_thread(
            self._create_backend, session_id=session_id, tenant_id=tenant_id
        )
        sandbox_id = getattr(backend, "sandbox_id", uuid.uuid4().hex[:12])

        sess = SandboxSession(
            session_id=session_id,
            sandbox_id=sandbox_id,
            skill_id=skill_id,
            user_id=user_id,
            tenant_id=tenant_id,
            _backend=backend,
        )
        self._sessions[session_id] = sess
        logger.info("sandbox_session_created",
                    session_id=session_id, sandbox_id=sandbox_id,
                    skill_id=skill_id)
        return sess

    async def execute(
        self, session_id: str, code: str, language: str = "python", timeout: int = 60
    ) -> dict:
        sess = await self._require(session_id)
        backend = sess._backend
        # 后端是同步的（httpx.Client，见 _cube_sandbox.py），而这里跑在事件循环上。
        # 直接调会独占循环整个 timeout —— 一次 60s 的 run_code 期间，本进程的所有
        # 请求（包括 /health）全部排队，k8s 会把一个"只是慢"的 pod 探活失败杀掉。
        # to_thread 把阻塞挪到线程池：调用本身照样慢，但只占一个线程。
        result = await asyncio.to_thread(
            backend.run_code, code, language=language, timeout=timeout
        )

        if hasattr(result, "logs"):
            stdout = "".join(result.logs.stdout)
            stderr = "".join(result.logs.stderr)
            exit_code = 1 if result.error else 0
            return {"stdout": stdout, "stderr": stderr, "exit_code": exit_code,
                    "artifacts": [], "duration_ms": 0}
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "artifacts": getattr(result, "artifacts", []),
            "duration_ms": getattr(result, "duration_ms", 0),
        }

    async def upload_file(self, session_id: str, path: str, data: bytes) -> None:
        sess = await self._require(session_id)
        backend = sess._backend
        if hasattr(backend, "files"):
            await asyncio.to_thread(backend.files.write, path, data)
        else:
            await asyncio.to_thread(backend.write_file, path, data)

    async def download_file(self, session_id: str, path: str) -> bytes:
        sess = await self._require(session_id)
        backend = sess._backend
        if hasattr(backend, "files"):
            return await asyncio.to_thread(backend.files.read, path)
        return await asyncio.to_thread(backend.read_file, path)

    async def destroy(self, session_id: str) -> None:
        sess = self._sessions.pop(session_id, None)
        if sess:
            await self._kill_backend(sess)
            logger.info("sandbox_session_destroyed", session_id=session_id)

    def get(self, session_id: str) -> SandboxSession | None:
        return self._sessions.get(session_id)

    def _create_backend(self, session_id: str = "", tenant_id: str = ""):
        import os
        backend_type = os.getenv("SANDBOX_BACKEND", "mock").lower()
        if backend_type == "e2b":
            self._patch_cube_network()
            from e2b_code_interpreter import Sandbox
            sbx = Sandbox.create(
                template=os.getenv("CUBE_TEMPLATE_ID", ""),
                api_url=os.getenv("E2B_API_URL") or os.getenv("CUBE_API_URL"),
            )
            return sbx
        elif backend_type == "cube":
            from lea_tools.services._cube_sandbox import CubeSandbox
            return CubeSandbox(session_id=session_id, tenant_id=tenant_id)
        else:
            from lea_tools.services._mock_sandbox import MockSandbox
            return MockSandbox()

    def _patch_cube_network(self):
        """Dev-only: resolve *.cube.app → proxy IP, skip TLS for cube.app only."""
        import os
        if os.getenv("CUBE_SKIP_PATCH", "").lower() in ("1", "true", "yes"):
            return
        proxy_host = os.getenv("CUBE_PROXY_HOST", "")
        if not proxy_host:
            from urllib.parse import urlparse
            url = os.getenv("E2B_API_URL") or os.getenv("CUBE_API_URL", "")
            proxy_host = urlparse(url).hostname or ""
        if not proxy_host or getattr(self, "_cube_patched", False):
            return
        self._cube_patched = True

        import ssl, httpcore
        _orig_conn = httpcore._backends.sync.SyncBackend.connect_tcp
        def _conn(self_, host, port, **kw):
            if isinstance(host, str) and host.endswith(".cube.app"):
                host = proxy_host
            return _orig_conn(self_, host, port, **kw)
        httpcore._backends.sync.SyncBackend.connect_tcp = _conn

        _orig_tls = httpcore._backends.sync.SyncStream.start_tls
        def _tls(self_, ssl_context=None, **kw):
            sni = kw.get("server_hostname", "") or ""
            if sni.endswith(".cube.app"):
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                return _orig_tls(self_, ssl_context=ctx, **kw)
            return _orig_tls(self_, ssl_context=ssl_context, **kw)
        httpcore._backends.sync.SyncStream.start_tls = _tls

    # _upload_bundle 已删除：往沙箱 /workspace/skill/ 逐文件 write 的老方案。
    # 现在 skill 走 NFS —— provision_session_skills 拷到 session skills 目录，
    # RO 挂成 /opt/skills。写 /workspace 是错的（用户可写，且会混进产物列表）。

    async def _kill_backend(self, sess: SandboxSession) -> None:
        try:
            if hasattr(sess._backend, "kill"):
                # httpx.delete，同步；回收循环里跑，卡住会连带拖慢所有回收。
                await asyncio.to_thread(sess._backend.kill)
        except Exception as e:
            logger.warning("sandbox_kill_failed",
                          session_id=sess.session_id, error=str(e))

    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(60)
            self._evict_stale_handles()
            await self._reap_abandoned_sandboxes()

    def _evict_stale_handles(self):
        """本进程长时间没用到的**句柄**从缓存里丢掉，但不动远端沙箱。

        这里以前是 destroy()（连沙箱一起 kill）。沙箱变成跨副本共享之后那样做是错的：
        `last_used_at` 只记录**本副本**服务过的请求，负载均衡把流量都给了别的副本时，
        本副本这份句柄看起来"闲置一小时"，而沙箱其实一直在忙 —— 于是把别人正在用的
        沙箱杀了。用户看到的是执行到一半沙箱没了，且只在多副本下偶发。

        淘汰缓存不该销毁背后的实体。真正该不该 kill 由 _reap_abandoned_sandboxes 按
        Cube 自己的时间数据判断 —— 那个视角是全局的。
        """
        now = time.time()
        stale = [sid for sid, s in self._sessions.items()
                 if now - s.last_used_at > IDLE_TIMEOUT]
        for sid in stale:
            self._sessions.pop(sid, None)
            logger.info("sandbox_handle_evicted", session_id=sid)

    async def _reap_abandoned_sandboxes(self):
        """按 Cube 的状态回收没人要的沙箱。

        判据完全来自 Cube，不看本地缓存 —— 所以任何副本跑都得出同样的结论，重启也
        不影响。以前的回收靠进程内那个 dict，于是 lea-tools 一重启，之前建的沙箱就
        再没人管了（实测线上留了 4 个，3 个 paused、过期一天还在占 slot）。

        只碰打了 lea.session_id 标记的（我们建的），且只碰已经 paused 的 —— Cube 的
        on_timeout=pause 意味着 paused 本身就说明它已经闲了 15 分钟没人碰。running
        的一律不动，哪怕本地觉得它闲，因为"本地觉得"正是上面那个 bug 的来源。

        DELETE 是幂等的，多个副本同时跑没关系。
        """
        import os
        if os.getenv("SANDBOX_BACKEND", "mock").lower() != "cube":
            return
        from lea_tools.services._cube_sandbox import list_reapable_sandboxes

        try:
            rows = await asyncio.to_thread(list_reapable_sandboxes, IDLE_TIMEOUT)
        except Exception as e:
            # 回收是尽力而为 —— 问不到 Cube 就下一轮再说，绝不能让它把整个
            # 后台循环带崩（那样连句柄淘汰也停了）。
            logger.warning("sandbox_reap_scan_failed", error=str(e))
            return

        for row in rows:
            sid = row["session_id"]
            # 本副本正在用的不碰。Cube 说它 paused 但我们刚服务过请求，说明
            # auto_resume 刚把它唤醒，状态还没同步过来。
            local = self._sessions.get(sid)
            if local and local.idle_seconds < IDLE_TIMEOUT:
                continue
            try:
                await asyncio.to_thread(row["kill"])
                self._sessions.pop(sid, None)
                logger.info("sandbox_reaped", session_id=sid,
                            sandbox_id=row["sandbox_id"],
                            paused_for=int(row["idle_seconds"]))
            except Exception as e:
                logger.warning("sandbox_reap_failed",
                               sandbox_id=row["sandbox_id"], error=str(e))


_manager: SandboxSessionManager | None = None


def get_sandbox_manager() -> SandboxSessionManager:
    global _manager
    if _manager is None:
        _manager = SandboxSessionManager()
    return _manager
