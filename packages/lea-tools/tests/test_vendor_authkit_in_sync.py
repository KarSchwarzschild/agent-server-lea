"""vendor/lea_authkit 必须跟 packages/lea-authkit 保持一致。

vendor 里是安全代码（服务间鉴权中间件）的拷贝，源头改了没同步的话，lea-tools 会静默
跑着旧版逻辑，而且一切看起来正常。修法：python scripts/sync_vendor.py
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "sync_vendor.py"


def test_vendored_authkit_matches_source():
    spec = importlib.util.spec_from_file_location("sync_vendor", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    if not mod.SRC.is_dir():
        # 只拷了 packages/lea-tools 到服务器时没有源头可比，不算失败。
        pytest.skip("packages/lea-authkit 不在（非 monorepo 环境）")
    assert mod.in_sync(), "vendor/lea_authkit 与 packages/lea-authkit 不一致，请运行 python scripts/sync_vendor.py"
