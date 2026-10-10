"""把 lea-authkit 的源码同步进本目录的 vendor/，让 lea-tools 能脱离 monorepo 单独构建。

为什么要 vendor：lea-tools 依赖 lea-authkit（服务间鉴权），而它是 monorepo 里的
workspace 成员，没有发布到任何包仓库。只拷 packages/lea-tools 到服务器时，镜像里就
没有这个包，服务起不来（import lea_authkit 失败）。

用法（在 monorepo 里跑，需要能看到 ../lea-authkit）：
    python scripts/sync_vendor.py          # 同步
    python scripts/sync_vendor.py --check  # 只检查，不一致时退出码 1（CI 用）

⚠️ 这是安全代码的一份拷贝。改了 packages/lea-authkit 之后必须重新跑一次同步，否则
lea-tools 会静默跑着旧版鉴权逻辑——tests/test_vendor_authkit_in_sync.py 会在 monorepo
里把这种漂移拦下来。长期的解法是把 lea-authkit 发布成可安装的包（内部 PyPI / git tag），
到那时删掉 vendor/ 和这个脚本，requirements.txt 里加一行依赖即可。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # packages/lea-tools
SRC = HERE.parent / "lea-authkit" / "src" / "lea_authkit"
DST = HERE / "vendor" / "lea_authkit"


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
        for p in sorted(root.rglob("*.py"))
    }


def in_sync() -> bool:
    return DST.is_dir() and _files(SRC) == _files(DST)


def main() -> int:
    if not SRC.is_dir():
        print(f"找不到 {SRC}——这个脚本只能在 monorepo 里跑", file=sys.stderr)
        return 2
    if "--check" in sys.argv:
        if in_sync():
            print("vendor/lea_authkit 与源码一致")
            return 0
        print("vendor/lea_authkit 与 packages/lea-authkit 不一致，请运行 python scripts/sync_vendor.py", file=sys.stderr)
        return 1
    if DST.exists():
        shutil.rmtree(DST)
    shutil.copytree(SRC, DST, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"已同步 {SRC} -> {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
