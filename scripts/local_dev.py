"""本地联调启动器 — 一键拉起所有 Python 微服务。

使用方式：
  python scripts/local_dev.py

会启动：
  - lea-config  :8500
  - lea-rag     :8300
  - lea-tools   :8400 (如果可用)

所有服务用 SQLite，无需 MySQL/Milvus/Redis。
按 Ctrl+C 停止全部。
"""

import os
import subprocess
import sys
import signal
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ENV = {
    **os.environ,
    "DB_HOST": "sqlite",
    "DB_NAME": str(ROOT / ".local" / "dev"),
    "JWT_SECRET": "dev-secret-12345",
    "PYTHONDONTWRITEBYTECODE": "1",
}

SERVICES = [
    {
        "name": "lea-config",
        "port": 8500,
        "src": ROOT / "packages" / "lea-config" / "src",
        "module": "lea_config.server:app",
    },
    {
        "name": "lea-rag",
        "port": 8300,
        "src": ROOT / "packages" / "lea-rag" / "src",
        "module": "lea_rag.server:app",
    },
    {
        "name": "lea-tools",
        "port": 8400,
        "src": ROOT / "packages" / "lea-tools" / "src",
        "module": "lea_tools.server:app",
    },
]


def main():
    (ROOT / ".local").mkdir(exist_ok=True)
    procs = []

    for svc in SERVICES:
        src_path = svc["src"]
        if not src_path.exists():
            print(f"  [SKIP] {svc['name']} — src not found")
            continue

        env = {**ENV, "PYTHONPATH": str(src_path)}
        cmd = [
            sys.executable, "-m", "uvicorn",
            svc["module"],
            "--host", "0.0.0.0",
            "--port", str(svc["port"]),
            "--reload",
            "--reload-dir", str(src_path),
        ]

        print(f"  [START] {svc['name']} → http://localhost:{svc['port']}")
        p = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        procs.append((svc["name"], p))

    if not procs:
        print("No services started!")
        return

    print(f"\n  All {len(procs)} services running. Ctrl+C to stop.\n")

    def shutdown(*_):
        print("\n  Shutting down...")
        for name, p in procs:
            p.terminate()
            print(f"  [STOP] {name}")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while True:
        for name, p in procs:
            ret = p.poll()
            if ret is not None:
                print(f"  [DIED] {name} exited with code {ret}")
        time.sleep(2)


if __name__ == "__main__":
    main()
