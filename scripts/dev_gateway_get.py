"""过 gateway GET 任意路径，带上 stub 签的 dev token。

用法:  python scripts/dev_gateway_get.py /api/files/list?session_id=xxx
输出原样写到 .gw_get.txt（UTF-8）—— Windows 控制台是 GBK，中文直接 print 会花，
甚至会 UnicodeEncodeError 把整个脚本打挂。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
GATEWAY = "http://127.0.0.1:8080"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: dev_gateway_get.py <path> [tenant]")
    path = sys.argv[1]
    tenant = sys.argv[2] if len(sys.argv) > 2 else "default"

    minted = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "dev_gateway_stub.py"), "--mint"],
        capture_output=True, text=True, cwd=ROOT,
    )
    if minted.returncode != 0:
        sys.exit(f"mint failed: {minted.stderr}")
    token = minted.stdout.strip().splitlines()[-1]

    proc = subprocess.run(
        ["curl", "-s", "-w", "\n__STATUS__%{http_code}",
         "-H", f"Authorization: Bearer {token}",
         "-H", f"X-Selected-Tenant-ID: {tenant}",
         f"{GATEWAY}{path}"],
        capture_output=True,
    )
    body = proc.stdout.decode("utf-8", errors="replace")
    out = ROOT / ".gw_get.txt"
    out.write_text(f"GET {path}\n{body}\n", encoding="utf-8")
    status = body.rsplit("__STATUS__", 1)[-1] if "__STATUS__" in body else "?"
    print(f"{path} -> {status}  (body in {out.name})")


if __name__ == "__main__":
    main()
