"""删掉长期不活跃会话的 NFS 工作区目录。

    tenants/<tenant_id>/sessions/<session_id>/{workspace,uploads,skills}

## 这个脚本存在的理由

会话工作区**只增不减**。今天没有任何东西会回收它：沙箱被 kill 只是壳没了，
host-mount 那份文件原封不动留在 NFS 上；会话归档（is_archived）只改一个标志位。
一个跑过几十次 code_interpreter 的会话能留下几百 MB 的中间产物，而它在用户
关掉那个对话之后就再也不会被读一次。

## 为什么按 last_active_at，不按 is_archived

归档是用户的一个动作，绝大多数用户从不点它 —— 按它筛等于筛不到东西。
last_active_at 是"这个会话最后一次收到消息"的真实时间，而且 TouchSession 每条
消息都在写它，所以它不会陈旧。

## 为什么不打包成 tar.gz 存档

早期方案是删之前先归档到 OBS。后来砍了：**该留的东西已经在 OBS 里了**。
模型显式 deliver_artifact 过的成品在 library/ 前缀下，用户上传的原件在 uploads/
前缀下，两份都不受这个脚本影响。剩下的是中间脚本、调试 csv、pip 装的包 ——
为它们做一份永不有人取的压缩包，只是把"只增不减"换个地方继续。

⚠️ 反过来说，**没有 deliver 过的产物删了就没了**。这是 deliver_artifact 那个
显式动作的代价，也是它的意义：留什么由模型声明，而不是我们猜。30 天的窗口是
给这件事留的余量。

## 默认 dry-run

删文件的脚本默认不删。`--apply` 才真删 —— 因为这个脚本第一次在一套新环境上跑的时候，
NFS_ROOT 配错、或者库连到了另一个环境，表现都是"匹配到一大堆目录"，而那时候
唯一的区别就是有没有真的执行。

用法：
    python scripts/reap_stale_sessions.py                 # 看会删什么
    python scripts/reap_stale_sessions.py --days 60       # 换窗口
    python scripts/reap_stale_sessions.py --apply         # 真删
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _dbconn import connect, env, utf8_stdout  # noqa: E402


def dir_bytes(path: pathlib.Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n}B"


def main() -> int:
    utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="不活跃多少天算过期（默认 30）")
    ap.add_argument("--apply", action="store_true", help="真删。不给就只报告")
    ap.add_argument("--db", default="lea-platform-test")
    ap.add_argument("--limit", type=int, default=0, help="最多处理几个，0=不限")
    args = ap.parse_args()

    # 进程环境优先于 .env：容器里是用环境变量配的，而仓库根的 .env 是开发机的值。
    # 反过来的话，在容器里跑这个脚本会去删（或者找不到）开发机的路径。
    root = pathlib.Path(
        os.environ.get("NFS_ROOT") or env().get("NFS_ROOT", "/data/cubelet/lea")
    )
    if not root.is_dir():
        print(f"✗ NFS_ROOT 不存在：{root}")
        print("  这个脚本必须在能看到共享存储的机器上跑（生产是 lea-tools 那台）。")
        return 2

    conn = connect(args.db)
    with conn.cursor() as cur:
        # 只取有工作区目录可能存在的那些。tenant_id 也要 —— 路径里有它。
        #
        # 用 last_active_at 的 COALESCE 兜一手：老行可能是 NULL（TouchSession 之前
        # 建的），那时候拿 created_at 当最后活跃时间。少了这个 COALESCE，NULL 行
        # 永远不满足 `< 阈值`，于是最老的那批反而永远删不掉。
        cur.execute(
            """
            SELECT id, tenant_id,
                   COALESCE(last_active_at, created_at) AS last_seen
              FROM chat_sessions
             WHERE COALESCE(last_active_at, created_at) < NOW() - INTERVAL %s DAY
             ORDER BY last_seen ASC
            """,
            (args.days,),
        )
        rows = cur.fetchall()
    conn.close()

    print(f"库里有 {len(rows)} 个会话超过 {args.days} 天没活动")
    print(f"NFS_ROOT = {root}")
    print(f"模式     = {'真删' if args.apply else 'dry-run（只报告，加 --apply 才删）'}")
    print()

    seen = freed = 0
    for session_id, tenant_id, last_seen in rows:
        if args.limit and seen >= args.limit:
            break
        d = root / "tenants" / str(tenant_id) / "sessions" / str(session_id)
        if not d.is_dir():
            continue  # 库里有行但没目录：从没跑过代码的纯聊天会话，正常
        size = dir_bytes(d)
        seen += 1
        freed += size
        print(f"  {session_id}  {human(size):>9}  最后活跃 {last_seen}")
        if args.apply:
            shutil.rmtree(d, ignore_errors=True)
            if d.exists():
                # ignore_errors 会把失败吞掉，所以自己确认一次 —— 否则这个脚本
                # 会报告"释放了 3GB"而磁盘一个字节都没少。
                print("    ✗ 删除失败，目录还在（权限？有进程占着？）")
                freed -= size

    print()
    print(f"{'已释放' if args.apply else '可释放'} {human(freed)}，涉及 {seen} 个会话目录")
    if not args.apply and seen:
        print("确认无误后加 --apply 真删。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
