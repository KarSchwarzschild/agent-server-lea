"""共用的库连接。仓库根的 .env 用绝对路径读 —— 这些脚本经常在不同 cwd 下被调起。"""

from __future__ import annotations

import pathlib
import sys

import pymysql

ROOT = pathlib.Path(__file__).resolve().parent.parent


def env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def connect(db: str = "lea-platform-test"):
    e = env()
    return pymysql.connect(
        host=e.get("DB_HOST", "202.205.160.9"),
        port=int(e.get("DB_PORT", "3307")),
        user=e.get("DB_USER", "root"),
        password=e.get("DB_PASSWORD", ""),
        database=db,
        charset="utf8mb4",
        autocommit=True,
    )


def utf8_stdout() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
