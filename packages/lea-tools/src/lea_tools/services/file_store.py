"""Workspace 文件读写策略 — NFS 直接操作 或 envd HTTP。

两种 store 读写同一份数据（沙箱的 /workspace），只是路径不同：

- NFSFileStore: 直接操作共享存储。生产用；沙箱死了文件仍在。
  要求 lea-tools 跟 Cube 节点挂同一个 fs（同机 XFS / SFS Turbo / NFS）。
- SandboxFileStore: 走 envd HTTP（/execute 跑 os.walk + /files 读写内容）。
  开发用（本机 lea-tools + 远端 Cube，没有共享存储）；沙箱死了就读不到。

FILE_STORE=nfs|sandbox|auto 选择。auto = NFS 路径存在则 NFS，否则 sandbox。

写入的两道保护（多个子代理并行操作同一个 workspace，见 plan）：

1. **原子替换** —— 写临时文件再 os.replace。POSIX 保证 rename 原子，所以并发的读者
   要么看到旧的完整版、要么新的完整版，不会读到半个文件（撕裂读）。
2. **新鲜度校验** —— 覆盖已存在的文件必须带上你读到的 (mtime_ns, size)，不匹配就
   拒绝。这挡的是"丢失更新"：两个 edit 都基于 v1，后写者静默抹掉先写者。

这两条各管一段窗口，缺一不可：

- 新鲜度校验管**秒级**窗口 —— 模型"read 一眼 → 思考几秒 → edit"，这几秒里别人改了
  文件。文件锁管不到这一段（模型思考时没有任何东西持锁）。
- 进程内互斥管**毫秒级**窗口 —— 校验和 os.replace 之间是 TOCTOU：N 个请求可以同时
  通过校验（都读到同一个 mtime_ns），然后依次 replace，于是最后一个覆盖前面所有。
  实测 8 线程并发基于同一版覆写，全部通过校验 —— 所以这不是理论风险。

互斥只需进程内：同一 session 的文件操作都由这一个 lea-tools 进程服务（Go 侧的 turn
绑定单个 pod）。跨节点要分布式锁，但那个前提不成立时先不引入 Redis 依赖。
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from weakref import WeakValueDictionary

# 配额异常复用物化那条路已有的类型，不新造一个：调用方（api/files.py）已经有一段把
# 它翻成 507 加中文提示的代码，两个异常类型会让那段变成两个分支，而用户看到的应该是
# 同一句话 —— "这个会话的文件配额满了"，不该因为是谁写的而不同。
from lea_tools.services.file_stager import QuotaExceeded

_HIDDEN_DIRS = frozenset({
    "__pycache__", ".ipynb_checkpoints", ".cache",
    ".pytest_cache", ".mypy_cache", ".git", ".venv",
    "node_modules", ".next", "dist", "build",
})
_HIDDEN_SUFFIXES = frozenset({".pyc", ".pyo", ".log"})
MAX_ENTRIES = 2000

# skill bundle 在沙箱里的挂载点（nfs_provisioner 的 mountPath，RO）。读侧接受这个
# 前缀并映射到宿主的 `sessions/{sid}/skills`，因为模型看到的路径就是它。
SKILL_MOUNT = "/opt/skills"

# 用户上传的原件在沙箱里的挂载点（RO）。跟 SKILL_MOUNT 完全同一个机制：workspace 的
# 同级目录，通过绝对前缀让读侧放行、写侧仍走严格的 _resolve。
#
# ⚠️ 这个常量是**下发给模型的路径**的唯一来源：nfs_provisioner 用它做 mountPath、
# 读侧用它做前缀、lea-agent 用它拼注入文本里那句"文件在 X"。三处对不上的后果是模型
# 拿着一个不存在的路径去 open()，而报错会是"文件不存在"——看起来像物化失败，实际是
# 路径约定不一致，排查会绕远。所以别在任何地方硬写这个字符串。
UPLOADS_MOUNT = "/mnt/uploads"

# workspace 本身在沙箱里的挂载点（nfs_provisioner.py 的 mountPath，同一个
# 字符串，这里没法直接 import 常量共用——两个服务是分开部署的，只能各自
# 硬写、靠注释互相指路）。
#
# 为什么需要这个：Bash 工具跑在沙箱容器里，模型在那边天然会用 "/workspace/x"
# 这种绝对路径（因为那是真实存在的挂载点）；但 Write/Edit/Read 这几个文件
# 工具的 rel_path 参数要的是**相对于这个会话 workspace 根目录**的相对路径——
# 两套约定字面上都叫 "workspace"，模型很容易搞混，把 bash 那套绝对路径习惯
# 带过来。
#
# 不处理这个案例的后果：Python 的 pathlib 里 `/` 拼接一旦右边是绝对路径就会
# 直接丢掉左边只保留右边（语言本身的既定行为），于是 `self._ws / "/workspace/x"`
# 算出来的是 "/workspace/x" 本身，完全绕开了真正的 workspace 根，然后被下面
# 的越界检查正确拒绝——但那句报错听起来像安全拦截，模型很难只凭"路径越界"
# 四个字猜到"我应该去掉前面这段 /workspace/"，往往会换几种写法反复重试，
# 白白烧掉本来就紧张的 ReAct 轮次预算（多文件任务比如生成 PPT，本来就要
# 写好几个文件，max_turns 撞得更快）。
WORKSPACE_MOUNT = "/workspace"


def _normalize_rel_path(rel_path: str) -> str:
    """把模型可能传来的"沙箱视角的绝对路径"标准化成 workspace 根下的相对路径。

    两种写法都认，模型不需要自己纠正：

        /workspace              ->  .
        /workspace/slides/a.js  ->  slides/a.js
        slides/a.js             ->  slides/a.js（本来就是相对路径，原样返回）
    """
    stripped = rel_path.strip()
    if stripped == WORKSPACE_MOUNT:
        return "."
    if stripped.startswith(WORKSPACE_MOUNT + "/"):
        return stripped[len(WORKSPACE_MOUNT) + 1 :]
    return rel_path


@dataclass
class FileEntry:
    path: str
    size: int
    mtime: int

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "size": self.size,
            "mtime": self.mtime,
            "in_output": self.path.startswith("output/"),
        }


@dataclass
class Stat:
    """写入后的文件标识，调用方拿它更新自己的 ReadSet。

    用 mtime_ns 而不是 mtime：秒级精度在同一秒内的两次写会得到相同值，新鲜度校验
    就会把"文件已被改动"误判成"没变"—— 而这正是要防的那个场景。
    """

    size: int
    mtime_ns: int

    def as_dict(self) -> dict:
        return {"size": self.size, "mtime_ns": self.mtime_ns}


class StaleWriteError(Exception):
    """目标文件的当前状态与调用方声称读到的不一致 —— 拒绝覆盖。

    两种触发情形，错误信息要能指导模型下一步做什么：
    - 带了 expect 但对不上：别人在你读完之后改过它
    - 没带 expect 而文件已存在：盲写，你不知道自己要覆盖掉什么
    """


# 按绝对路径分配的写锁，把"校验 + 原子替换"围成一个临界区。
#
# 用 WeakValueDictionary：锁在没人持有时自动回收，否则一个长跑 session 写过几千个
# 文件就攒下几千把永不释放的锁。_LOCKS_GUARD 保护字典本身的 get-or-create —— 没有它
# 两个线程可能各建一把锁，然后各自"成功"加锁，互斥就没了。
_LOCKS: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()


def _path_lock(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def is_hidden(name: str) -> bool:
    if name in _HIDDEN_DIRS:
        return True
    return os.path.splitext(name)[1].lower() in _HIDDEN_SUFFIXES


class NFSFileStore:
    """readdir 共享存储上的 workspace 目录。"""

    kind = "nfs"

    def __init__(self, workspace: Path, quota_bytes: int = 0):
        self._ws = workspace
        # 0 = 不限。构造方（get_file_store）从 NFS_WORKSPACE_QUOTA_MB 取值；
        # 单测和一次性脚本可以直接不传。
        self._quota = quota_bytes

    def available(self) -> bool:
        return self._ws.is_dir()

    def session_bytes(self) -> int:
        """会话在共享存储上的占用：`workspace/` + `uploads/`。

        ## 为什么算整个会话目录，不只是 workspace

        配额要回答的是"这个会话在共享存储上占了多少"，而占用分在两个目录里：模型
        写的产物在 `workspace/`，用户上传的原件物化在 `uploads/`。只算一个的后果是
        两条路各自看到一半的世界 —— 传满 500MB 原件之后模型仍然能写 500MB 产物，
        实际占用一千兆，而两边的检查都显示"没超"。

        `skills/` **不算**：那是平台投递进去的资源（按 sha 缓存复用，用户没法控制大小
        也没法删），把它计入用户配额等于用平台自己的东西挤占用户的空间。

        ## 现算而不是维护计数器

        跟 file_stager.uploads_bytes 同一个理由，见那边的注释。多说一句这里更强的
        地方：写路径上的计数器要在 `_write_locked` 的异常分支里回滚，而那个分支正是
        进程可能被 kill 的地方 —— 漂移的表现是配额莫名其妙变紧，用户删了文件也不
        恢复。遍历一个会话目录是几十到几千个 inode，比一次 NFS 写便宜。
        """
        total = 0
        for sub in ("workspace", "uploads"):
            d = self._ws.parent / sub
            if not d.is_dir():
                continue
            for root, _dirs, files in os.walk(d):
                for name in files:
                    try:
                        total += os.path.getsize(os.path.join(root, name))
                    except OSError:
                        continue
        return total

    def _check_quota(self, target: Path, incoming: int) -> None:
        """写入前判配额。超了抛 QuotaExceeded，由 handler 翻成 507。

        ## 算的是**增量**，不是新文件的大小

        覆盖一个 200MB 的文件成 201MB，增量是 1MB。按新文件大小判的话，配额用到
        一半以上之后所有覆盖写都会失败 —— 而模型改一份大文件正是"读出来、改、写回去"，
        于是它会在一个明明没变大的写入上被反复拒绝，而错误信息说的是"配额满了"。

        ## 这一层是**软闸**，不是唯一防线

        沙箱里的 python 直接写 /workspace 根本不经过 lea-tools —— `open('/workspace/x',
        'wb').write(...)` 只走 cubelet 的 host-mount，这里看不到。所以真正的硬闸只能
        在文件系统层（XFS project quota / NFS 后端的目录配额），部署时必须配上，
        见 docs 里的存储章节。

        这一层的价值是**给模型一个能理解的错误**：文件系统配额满了报的是 ENOSPC
        ("No space left on device")，模型会以为是平台故障然后重试；这里报的是
        "会话文件配额已满（已用 X / 上限 Y），删掉不需要的文件再写"，它知道该做什么。
        """
        if self._quota <= 0:
            return
        used = self.session_bytes()
        try:
            existing = target.stat().st_size
        except OSError:
            existing = 0
        delta = incoming - existing
        if delta > 0 and used + delta > self._quota:
            raise QuotaExceeded(used, delta, self._quota)

    def list(self, show_hidden: bool = False) -> tuple[list[FileEntry], bool]:
        if not self._ws.is_dir():
            return [], False
        out: list[FileEntry] = []
        for root, dirs, files in os.walk(self._ws):
            if not show_hidden:
                dirs[:] = [d for d in dirs if not is_hidden(d)]
            for fname in sorted(files):
                if not show_hidden and is_hidden(fname):
                    continue
                if len(out) >= MAX_ENTRIES:
                    return out, True
                full = Path(root) / fname
                try:
                    st = full.stat()
                except OSError:
                    continue
                out.append(FileEntry(
                    path=full.relative_to(self._ws).as_posix(),
                    size=st.st_size,
                    mtime=int(st.st_mtime),
                ))
        return out, False

    def _resolve_readable(self, rel_path: str) -> Path:
        """解析一个**只读**路径：workspace 内，或两个 RO 挂载点之一。

        两个挂载点都是 workspace 的**同级**目录，不在 workspace 内，所以走 _resolve
        一定判越界：

            /opt/skills    sessions/{sid}/skills    skill bundle（平台投递的）
            /mnt/uploads   sessions/{sid}/uploads   用户上传的原件

        但模型需要读它们，而且它已经知道这两个路径 —— skill 是 activate_skill 的引导和
        正文 location header 写的，uploads 是附件注入文本里写的。所以读侧原样接受这两个
        前缀并映射到宿主目录，不另造一套约定。

        用绝对前缀而不是 `skills/` 这种相对形式，是为了不跟 workspace 里真有一个同名
        目录相撞。

        **只在读路径上放行**：write / edit / stat 仍走严格的 _resolve，所以这两个目录
        对 agent 都不可写 ——
          - skill 是投递进去的平台资源，被改掉会影响后续会话（bundle 按 sha 缓存复用，
            改坏了不会自动修复）；
          - uploads 是用户的原件，改完的东西必须写到 /workspace（那里才会被推 S3 进
            文件抽屉，而且原件保住了才能重来）。
        """
        raw = rel_path.strip()
        for mount, subdir in ((SKILL_MOUNT, "skills"), (UPLOADS_MOUNT, "uploads")):
            if raw == mount or raw.startswith(mount + "/"):
                return self._resolve_under_mount(raw, mount, subdir)
        return self._resolve(rel_path)

    def _resolve_under_mount(self, raw: str, mount: str, subdir: str) -> Path:
        """把 `<mount>/x/y` 映射到 `sessions/{sid}/<subdir>/x/y`，越界就拒绝。

        越界检查用 normpath 而不是 resolve —— 前者纯字符串消掉 `..`，后者会跟随符号
        链接。这个区别在这里是决定性的：skill 是 ensure_cached 解压到
        `{root}/cache/skills/<sha>/` 再 copytree 进 session 目录的，那条链上出现
        symlink 是正常的部署结构（实测生产 NFS 上就是），于是 resolve 后的真实文件
        落在挂载根之外，比对必然失败。

        症状很有误导性：同一个目录下**不存在**的文件报"文件不存在"（resolve 无链接可
        跟随，路径照原样拼），**真实存在**的文件报"路径越界"。看起来像投递没成功，
        实际是检查本身把正常路径拒了。

        安全性不因此降低：这里要防的是 `../` 穿出挂载点，normpath 消 `..` 完全够；
        而 symlink 是我们自己投递/物化造成的，不是 agent 能控制的输入。写侧仍走
        严格的 _resolve（含 resolve），所以这两个目录依旧不可写。

        uploads 走同一条路是有意的：物化今天是直接落盘（file_stager 里 os.replace），
        但哪天为了省空间改成"指向 OBS 缓存的硬链/符号链"，这里不需要跟着改。
        """
        root = os.path.normpath(str(self._ws.parent / subdir))
        target = os.path.normpath(os.path.join(root, raw[len(mount):].lstrip("/")))
        if target != root and not target.startswith(root + os.sep):
            raise PermissionError(f"path escapes {subdir}")
        return Path(target)

    def _resolve(self, rel_path: str) -> Path:
        """把相对路径解析到 workspace 内，越界就拒绝。

        用 is_relative_to 而不是字符串前缀匹配。前缀匹配是错的：workspace 是
        `…/sessions/{sid}/workspace`，那么 `…/sessions/{sid}/workspace-evil/x` 的
        前缀确实匹配得上，于是逃逸成功。读侧一直是这么写的（可利用性低——要有一个
        同级目录名以 workspace 开头，而 session 目录由 provisioner 建），但写侧复用
        同一个检查就是可写逃逸了。
        """
        rel_path = _normalize_rel_path(rel_path)
        root = self._ws.resolve()
        target = (self._ws / rel_path).resolve()
        if target != root and not target.is_relative_to(root):
            raise PermissionError("path escapes workspace")
        return target

    def read(self, rel_path: str) -> bytes:
        target = self._resolve_readable(rel_path)
        if not target.is_file():
            raise FileNotFoundError(rel_path)
        # Windows 上并发写者正在 os.replace 时，读者 open 会短暂拿到 WinError 5。
        # POSIX 不会（rename 只换目录项，open 要么拿到旧 inode 要么新的），所以这
        # 段重试只在开发机生效。注意这里**不能**吞掉逃逸检查抛的 PermissionError ——
        # 那个在上面的 _resolve 里已经抛完了，到这里的只可能是 IO 层的。
        for i in range(12):
            try:
                return target.read_bytes()
            except PermissionError:
                if os.name != "nt" or i == 11:
                    raise
                time.sleep(0.005 * (i + 1))
        raise AssertionError("unreachable")

    def edit(self, rel_path: str, old: str, new: str, expect: Stat | None = None) -> tuple[Stat, int]:
        """串替换，要求 old 恰好出现一次。返回 (新 Stat, 替换处数=1)。

        整个读-改-写在同一把锁内完成，所以两个并发 edit 不会都基于同一版
        （那样后者会静默抹掉前者）。放在 store 层而不是 HTTP handler 层就是为了这个：
        handler 里"先 read 再 write"即使两步各自加锁，中间仍有窗口。

        匹配次数不对时抛 ValueError，由调用方翻成 4xx —— 0 次和多次都不能猜着改，
        静默改第一处是最难发现的一类错误。
        """
        target = self._resolve(rel_path)
        with _path_lock(str(target)):
            if not target.is_file():
                raise FileNotFoundError(rel_path)
            raw = target.read_bytes()
            st = target.stat()
            current = Stat(size=st.st_size, mtime_ns=st.st_mtime_ns)
            if expect is not None and (expect.mtime_ns, expect.size) != (current.mtime_ns, current.size):
                raise StaleWriteError(
                    f"{rel_path} 在你读取之后被改动过"
                    f"（你基于 mtime_ns={expect.mtime_ns}，当前 {current.mtime_ns}）。"
                    f"请重新读取该文件后再改。"
                )
            text = raw.decode("utf-8")
            count = text.count(old)
            if count != 1:
                raise ValueError(str(count))
            payload = text.replace(old, new, 1).encode("utf-8")
            # edit 也要判：一次 edit 可以把 new 写成任意长的字符串（模型往一个文件里
            # 反复追加大段内容是常见形状），所以它跟 write 一样能把配额吃满。
            # 判在锁内、替换之前 —— 锁外判会跟另一个写者的 replace 交叉。
            self._check_quota(target, len(payload))
            return self._write_locked(target, payload), 1

    def _write_locked(self, target: Path, data: bytes) -> Stat:
        """原子替换。调用方必须已持有 _path_lock(target)。"""
        target.parent.mkdir(parents=True, exist_ok=True)
        # 临时文件必须跟目标同目录：os.replace 只在同一文件系统内保证原子，
        # /tmp 跟 NFS 挂载点通常不是同一个 fs，跨设备会退化成 copy+unlink。
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            self._replace_with_retry(tmp, target)
        except BaseException:
            # 失败时别把临时文件留在 workspace 里 —— 它会出现在用户的文件抽屉里，
            # 而且下次 list 会把它当成产物。
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        st = target.stat()
        return Stat(size=st.st_size, mtime_ns=st.st_mtime_ns)

    @staticmethod
    def _replace_with_retry(tmp: str, target: Path, attempts: int = 12) -> None:
        """os.replace，带针对 Windows 的短重试。

        POSIX 上 rename 只换目录项，别人已打开的 fd 继续指向旧 inode，所以并发读者
        不影响替换 —— 这正是原子替换安全的根据。Windows 不同：目标文件有任何打开的
        句柄时 replace 直接 WinError 5。生产跑 Linux，但开发机是 Windows，让它偶发
        失败会掩盖真实回归，所以退让重试一小会儿。

        非 Windows 上第一次就成功，这段等价于直接调 os.replace。
        """
        for i in range(attempts):
            try:
                os.replace(tmp, target)
                return
            except PermissionError:
                if os.name != "nt" or i == attempts - 1:
                    raise
                time.sleep(0.005 * (i + 1))

    def stat(self, rel_path: str) -> Stat | None:
        """返回文件当前的 (size, mtime_ns)，不存在则 None。"""
        target = self._resolve(rel_path)
        try:
            st = target.stat()
        except (OSError, ValueError):
            return None
        return Stat(size=st.st_size, mtime_ns=st.st_mtime_ns)

    def write(
        self,
        rel_path: str,
        data: bytes,
        expect: Stat | None = None,
        allow_create_only: bool = False,
    ) -> Stat:
        """原子写入，覆盖已存在的文件时校验新鲜度。

        expect 为 None 且目标已存在 → StaleWriteError（盲写）。
        expect 不为 None 且与磁盘现状不符 → StaleWriteError（丢失更新）。
        allow_create_only=True 时目标已存在即报错，用于"只创建新文件"的语义。
        """
        target = self._resolve(rel_path)

        # 校验和替换必须在同一个临界区里。分开做的话 N 个并发请求会同时通过校验
        # （都读到同一个 mtime_ns），然后依次 replace —— 最后一个覆盖前面所有，
        # 每一个都"成功"返回。实测 8 线程能复现。
        with _path_lock(str(target)):
            current = self.stat(rel_path)

            if current is not None:
                if allow_create_only:
                    raise StaleWriteError(
                        f"{rel_path} 已存在。若确实要覆盖，先读它、再带上读到的 mtime_ns 重写。"
                    )
                if expect is None:
                    raise StaleWriteError(
                        f"{rel_path} 已存在，但本次写入没有声明基于哪一版。"
                        f"先读取该文件（拿到 mtime_ns={current.mtime_ns}）再写，"
                        f"以免覆盖掉你没看过的内容。"
                    )
                if (expect.mtime_ns, expect.size) != (current.mtime_ns, current.size):
                    raise StaleWriteError(
                        f"{rel_path} 在你读取之后被改动过"
                        f"（你基于 mtime_ns={expect.mtime_ns} size={expect.size}，"
                        f"当前是 mtime_ns={current.mtime_ns} size={current.size}）。"
                        f"请重新读取该文件，确认改动仍然适用后再写。"
                    )
            elif expect is not None:
                raise StaleWriteError(
                    f"{rel_path} 不存在，但本次写入声明基于某个已有版本 —— "
                    f"文件可能已被删除，请重新读取确认。"
                )

            # 配额判在新鲜度校验**之后**：两个都会拒绝这次写入，但"你基于的版本过期了"
            # 是可以立即自救的（重读再写），而"配额满了"要先删东西。先报能自救的那个，
            # 模型少绕一圈。
            self._check_quota(target, len(data))
            return self._write_locked(target, data)


_WALK_TMPL = """\
import os
_H = {hidden_dirs!r}
_S = {hidden_sufs!r}
_SHOW = {show_hidden!r}
_MAX = {max_entries}
_n = 0
for root, dirs, files in os.walk('/workspace'):
    if not _SHOW:
        dirs[:] = [d for d in dirs if d not in _H]
    for f in sorted(files):
        if not _SHOW and (f in _H or os.path.splitext(f)[1].lower() in _S):
            continue
        if _n >= _MAX:
            print('__TRUNCATED__'); raise SystemExit
        p = os.path.join(root, f)
        try:
            st = os.stat(p)
        except OSError:
            continue
        print('%s\\t%d\\t%d' % (os.path.relpath(p, '/workspace'), st.st_size, st.st_mtime))
        _n += 1
"""


class SandboxFileStore:
    """走 envd HTTP 读活沙箱里的 /workspace。沙箱死了就读不到。"""

    kind = "sandbox"

    def __init__(self, backend):
        self._b = backend

    def available(self) -> bool:
        return self._b is not None and hasattr(self._b, "read_file")

    def list(self, show_hidden: bool = False) -> tuple[list[FileEntry], bool]:
        code = _WALK_TMPL.format(
            hidden_dirs=sorted(_HIDDEN_DIRS),
            hidden_sufs=sorted(_HIDDEN_SUFFIXES),
            show_hidden=show_hidden,
            max_entries=MAX_ENTRIES,
        )
        res = self._b.run_code(code, timeout=30)
        out: list[FileEntry] = []
        truncated = False
        for line in (res.stdout or "").splitlines():
            if line == "__TRUNCATED__":
                truncated = True
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            try:
                out.append(FileEntry(
                    path=parts[0].replace("\\", "/"),
                    size=int(parts[1]),
                    mtime=int(parts[2]),
                ))
            except ValueError:
                continue
        return out, truncated

    @staticmethod
    def _check(rel_path: str) -> str:
        # 同样的"模型把 bash 那套 /workspace 绝对路径习惯带进来"的问题，见
        # _normalize_rel_path 顶部的说明——这里的沙箱路径本来就长
        # "/workspace/x"，所以不是拼接出错，而是干脆先把这个前缀剥掉再走后面
        # 的越界检查，两种写法都认。
        rel_path = _normalize_rel_path(rel_path)
        if rel_path.startswith("/") or ".." in rel_path.split("/"):
            raise PermissionError("path escapes workspace")
        return f"/workspace/{rel_path}"

    @staticmethod
    def _check_readable(rel_path: str) -> str:
        """同 _check，但额外放行两个 RO 挂载点 —— 只用于读。

        沙箱里 /opt/skills 和 /mnt/uploads 就是真实路径（RO bind mount），所以这里是
        原样透传而不是映射（跟 NFSFileStore 那边需要映射到宿主目录不同）。

        仍要挡 `..`：`/opt/skills/../../etc/passwd` 会走出挂载点。
        """
        raw = rel_path.strip()
        for mount in (SKILL_MOUNT, UPLOADS_MOUNT):
            if raw == mount or raw.startswith(mount + "/"):
                if ".." in raw.split("/"):
                    raise PermissionError(f"path escapes {mount}")
                return raw
        return SandboxFileStore._check(rel_path)

    def read(self, rel_path: str) -> bytes:
        return self._b.read_file(self._check_readable(rel_path))

    def stat(self, rel_path: str) -> Stat | None:
        """跑一小段代码取 stat —— envd 的 /files 只有读写，没有 stat 接口。"""
        path = self._check(rel_path)
        code = (
            "import os\n"
            f"p = {path!r}\n"
            "try:\n"
            "    st = os.stat(p)\n"
            "    print('%d\\t%d' % (st.st_size, st.st_mtime_ns))\n"
            "except OSError:\n"
            "    print('__MISSING__')\n"
        )
        res = self._b.run_code(code, timeout=15)
        line = (res.stdout or "").strip().splitlines()
        if not line or line[-1] == "__MISSING__":
            return None
        parts = line[-1].split("\t")
        if len(parts) != 2:
            return None
        try:
            return Stat(size=int(parts[0]), mtime_ns=int(parts[1]))
        except ValueError:
            return None

    def write(
        self,
        rel_path: str,
        data: bytes,
        expect: Stat | None = None,
        allow_create_only: bool = False,
    ) -> Stat:
        """同 NFSFileStore.write 的契约，但少一层保证：envd 的 /files 写入不是原子的。

        校验和写入之间隔着一次 HTTP 往返，所以这里的新鲜度检查是尽力而为——它挡得住
        "模型读完思考几秒再写"这个主要窗口（那是秒级的），挡不住两个写入在毫秒级
        交错。sandbox 模式是开发用途（本机 lea-tools + 远端 Cube，无共享存储），
        生产走 NFS，所以不为此再加一层。
        """
        path = self._check(rel_path)
        current = self.stat(rel_path)

        if current is not None:
            if allow_create_only:
                raise StaleWriteError(f"{rel_path} 已存在。若确实要覆盖，先读它再带 mtime_ns 重写。")
            if expect is None:
                raise StaleWriteError(
                    f"{rel_path} 已存在，但本次写入没有声明基于哪一版。"
                    f"先读取该文件（拿到 mtime_ns={current.mtime_ns}）再写。"
                )
            if (expect.mtime_ns, expect.size) != (current.mtime_ns, current.size):
                raise StaleWriteError(
                    f"{rel_path} 在你读取之后被改动过"
                    f"（你基于 mtime_ns={expect.mtime_ns}，当前 {current.mtime_ns}）。"
                    f"请重新读取后再写。"
                )
        elif expect is not None:
            raise StaleWriteError(f"{rel_path} 不存在，但声明基于某个已有版本 —— 可能已被删除。")

        self._b.write_file(path, data)
        st = self.stat(rel_path)
        if st is None:
            raise OSError(f"write succeeded but {rel_path} is not readable back")
        return st

    def edit(self, rel_path: str, old: str, new: str, expect: Stat | None = None) -> tuple[Stat, int]:
        """同 NFSFileStore.edit 的契约。匹配次数不对时抛 ValueError(次数)。"""
        raw = self.read(rel_path)
        current = self.stat(rel_path)
        if expect is not None and current is not None and (
            (expect.mtime_ns, expect.size) != (current.mtime_ns, current.size)
        ):
            raise StaleWriteError(
                f"{rel_path} 在你读取之后被改动过（当前 mtime_ns={current.mtime_ns}）。请重新读取后再改。"
            )
        text = raw.decode("utf-8")
        count = text.count(old)
        if count != 1:
            raise ValueError(str(count))
        return self.write(rel_path, text.replace(old, new, 1).encode("utf-8"), expect=current), 1


def workspace_path(tenant_id: str, session_id: str) -> Path:
    from lea_tools.config import get_nfs_settings
    return (Path(get_nfs_settings().root) / "tenants" / tenant_id
            / "sessions" / session_id / "workspace")


def get_file_store(tenant_id: str, session_id: str):
    """按 FILE_STORE 选 store。

    nfs      — 只用共享存储（生产）
    sandbox  — 只用 envd（开发，无共享存储）
    auto     — NFS 目录存在则 NFS，否则回落 envd（默认）
    """
    mode = os.getenv("FILE_STORE", "auto").lower()

    # 配额只装在 NFS store 上。SandboxFileStore 走 envd HTTP，量不出会话目录的占用
    # （它看到的是沙箱里的 /workspace，而那是同一份共享存储的挂载 —— 但要遍历它得在
    # 沙箱里跑一段 os.walk，一次写入多打一次 /execute 往返）。sandbox 模式是开发用途
    # （本机 lea-tools + 远端 Cube，无共享存储），生产走 NFS，所以不为它补这一层。
    from lea_tools.config import get_nfs_settings
    quota = get_nfs_settings().workspace_quota_mb * 1024 * 1024

    nfs = (NFSFileStore(workspace_path(tenant_id, session_id), quota_bytes=quota)
           if tenant_id else None)
    if mode == "nfs":
        return nfs

    sbx = None
    try:
        from lea_tools.services.sandbox_session import get_sandbox_manager
        sess = get_sandbox_manager().get(session_id)
        if sess is not None and sess._backend is not None:
            cand = SandboxFileStore(sess._backend)
            sbx = cand if cand.available() else None
    except Exception:
        sbx = None

    if mode == "sandbox":
        return sbx
    if nfs is not None and nfs.available():
        return nfs
    return sbx or nfs
