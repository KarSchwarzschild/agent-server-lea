"""一键全链路集成测试：Go Agent → Skill → Tool → Sandbox → CubeSandbox

启动顺序：
1. lea-config (:8500) — SQLite, seed agent with sandbox_enabled
2. lea-tools  (:8400) — 真实 CubeSandbox 后端
3. Go Agent   (:8100) — 连接上面两个
4. 发 /debug/trace 请求触发 code_interpreter
"""

import os
import signal
import subprocess
import sys

# ─── 配置 ───
CUBE_API_URL = "http://10.192.241.137:3000"
CUBE_TEMPLATE = "tpl-8a2fbca58d6f4946a82c88fe"
LLM_BASE_URL = "https://tenantapi-ai.ouchn.edu.cn/v1"
LLM_API_KEY = "tenant-45018b36afb04526b8e881292b6ea978"
LLM_MODEL = "qwen3-max"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(BASE_DIR))

processes: list[subprocess.Popen] = []


def cleanup():
    for p in processes:
        try:
            p.terminate()
        except Exception:
            pass


signal.signal(signal.SIGINT, lambda *_: (cleanup(), sys.exit(1)))
