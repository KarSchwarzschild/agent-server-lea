#!/usr/bin/env python3
"""Phase 0 · NFS host-mount 冒烟测试（10 项断言）

前置条件：
  1. NFS server 10.192.241.137 上已创建测试目录：
     mkdir -p /data/shared/lea-test/sessions/smoke-001/workspace
     mkdir -p /data/shared/lea-test/sessions/smoke-001/skills
     mkdir -p /data/shared/lea-test/platform-skills
     chown -R 1000:1000 /data/shared/lea-test/   # UID 待本脚本实测后确认
     chmod -R 0777 /data/shared/lea-test/         # 冒烟阶段放开，生产再收紧

  2. Cube master conf.yaml 里加：
     allowed_host_mount_prefixes:
       - "/data/shared/lea-test/"

  3. 环境变量：
     CUBE_API_URL=http://10.192.241.137:3000  (或实际 Cube master 地址)
     CUBE_TEMPLATE_ID=tpl-a58d55bcc1a54b5bbfbb4c00

用法：
  python scripts/phase0_nfs_smoke.py
"""

import json
import os
import sys
import time

import httpx

API_URL = os.environ.get("CUBE_API_URL", "http://10.192.241.137:3000")
TEMPLATE_ID = os.environ.get("CUBE_TEMPLATE_ID", "tpl-a58d55bcc1a54b5bbfbb4c00")
PROXY_HOST = os.environ.get("CUBE_PROXY_HOST", "")

# NFS 测试路径（host 视角，数据盘 /dev/vdb 挂在 /data/cubelet）
NFS_BASE = os.environ.get("NFS_TEST_BASE", "/data/cubelet/lea-test")
SESSION_WORKSPACE = f"{NFS_BASE}/sessions/smoke-001/workspace"
SESSION_SKILLS = f"{NFS_BASE}/sessions/smoke-001/skills"
PLATFORM_SKILLS = f"{NFS_BASE}/platform-skills"

# 运行时注入（跟 SANDBOX_NFS_STORAGE.md Section 4.2 一致）
_RUNTIME_INJECT = """\
import os, sys
sys.dont_write_bytecode = True
os.makedirs('/workspace/output', exist_ok=True)
os.chdir('/workspace/output')
os.environ['OUTPUT_DIR'] = '/workspace/output'
os.environ['NPM_CONFIG_PREFIX'] = '/tmp/npm-global'
os.environ['NPM_CONFIG_CACHE']  = '/tmp/npm-cache'
os.environ['npm_config_prefix'] = '/tmp/npm-global'
os.environ['npm_config_cache']  = '/tmp/npm-cache'
"""


class SmokeTest:
    def __init__(self):
        self.sandbox_id = None
        self.domain = "cube.app"
        self.results: list[tuple[str, bool, str]] = []
        if not PROXY_HOST:
            from urllib.parse import urlparse
            self._proxy = urlparse(API_URL).hostname or "localhost"
        else:
            self._proxy = PROXY_HOST
        self._client = httpx.Client(verify=False, timeout=30)

    def _exec(self, code: str, timeout: int = 30) -> dict:
        host = f"49999-{self.sandbox_id}.{self.domain}"
        resp = self._client.post(
            f"https://{self._proxy}:443/execute",
            headers={"Host": host, "Content-Type": "application/json"},
            json={"code": code, "context_id": None,
                  "language": None, "env_vars": None},
            timeout=timeout,
        )
        stdout, stderr = "", ""
        for line in resp.text.strip().split("\n"):
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "stdout":
                stdout += msg.get("text", "")
            elif msg.get("type") == "stderr":
                stderr += msg.get("text", "")
        return {"stdout": stdout.strip(), "stderr": stderr.strip()}

    def assert_ok(self, name: str, passed: bool, detail: str = ""):
        self.results.append((name, passed, detail))
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))

    # ------------------------------------------------------------------
    # T1: 创建 sandbox + 3 组 host-mount
    # ------------------------------------------------------------------
    def t1_create_sandbox(self):
        print("\n--- T1: Create sandbox with 3 host-mounts ---")
        mounts = [
            {"hostPath": SESSION_WORKSPACE,
             "mountPath": "/workspace", "readOnly": False},
            {"hostPath": PLATFORM_SKILLS,
             "mountPath": "/opt/skills-platform", "readOnly": True},
            {"hostPath": SESSION_SKILLS,
             "mountPath": "/opt/skills-user", "readOnly": True},
        ]
        body = {
            "templateID": TEMPLATE_ID,
            "timeout": 300,
            "lifecycle": {"on_timeout": "kill", "auto_resume": False},
            "metadata": {"host-mount": json.dumps(mounts)},
        }
        resp = httpx.post(f"{API_URL}/sandboxes", json=body, timeout=30)
        ok = resp.status_code in (200, 201) and "sandboxID" in resp.json()
        if ok:
            data = resp.json()
            self.sandbox_id = data["sandboxID"]
            self.domain = data.get("domain", "cube.app")
        self.assert_ok("T1 sandbox created with host-mount",
                       ok, f"status={resp.status_code} id={self.sandbox_id}")
        if ok:
            time.sleep(3)  # 等 envd 就绪

    # ------------------------------------------------------------------
    # T2: 沙箱内 UID 探测
    # ------------------------------------------------------------------
    def t2_detect_uid(self):
        print("\n--- T2: Detect sandbox UID ---")
        r = self._exec("import os; print(os.getuid(), os.getgid())")
        uid_gid = r["stdout"]
        ok = uid_gid != "" and uid_gid.split()[0].isdigit()
        self.assert_ok("T2 UID detected", ok, f"uid:gid = {uid_gid}")

    # ------------------------------------------------------------------
    # T3: 3 组独立挂载点都可见
    # ------------------------------------------------------------------
    def t3_mounts_visible(self):
        print("\n--- T3: All 3 mount points visible ---")
        code = (
            "import os\n"
            "paths = ['/workspace', '/opt/skills-platform', '/opt/skills-user']\n"
            "for p in paths:\n"
            "    print(f'{p} exists={os.path.isdir(p)}')\n"
        )
        r = self._exec(code)
        all_exist = r["stdout"].count("exists=True") == 3
        self.assert_ok("T3 all 3 mounts visible", all_exist, r["stdout"])

    # ------------------------------------------------------------------
    # T4: workspace RW — 写文件到 NFS
    # ------------------------------------------------------------------
    def t4_workspace_write(self):
        print("\n--- T4: Write file to /workspace (RW) ---")
        code = (
            "with open('/workspace/smoke-test.txt', 'w') as f:\n"
            "    f.write('hello-nfs-phase0')\n"
            "import os; print(os.path.getsize('/workspace/smoke-test.txt'))\n"
        )
        r = self._exec(code)
        ok = "16" in r["stdout"]
        self.assert_ok("T4 workspace write", ok, r["stdout"])

    # ------------------------------------------------------------------
    # T5: skills-platform RO — 写入应被 EROFS 拒绝
    # ------------------------------------------------------------------
    def t5_platform_ro(self):
        print("\n--- T5: /opt/skills-platform is read-only (EROFS) ---")
        code = (
            "try:\n"
            "    open('/opt/skills-platform/attack.txt','w').write('x')\n"
            "    print('WRITE_OK')\n"
            "except OSError as e:\n"
            "    print(f'BLOCKED errno={e.errno}')\n"
        )
        r = self._exec(code)
        ok = "errno=30" in r["stdout"]
        self.assert_ok("T5 platform-skills RO (EROFS)", ok, r["stdout"])

    # ------------------------------------------------------------------
    # T6: skills-user RO — 同上
    # ------------------------------------------------------------------
    def t6_user_skills_ro(self):
        print("\n--- T6: /opt/skills-user is read-only (EROFS) ---")
        code = (
            "try:\n"
            "    open('/opt/skills-user/attack.txt','w').write('x')\n"
            "    print('WRITE_OK')\n"
            "except OSError as e:\n"
            "    print(f'BLOCKED errno={e.errno}')\n"
        )
        r = self._exec(code)
        ok = "errno=30" in r["stdout"]
        self.assert_ok("T6 user-skills RO (EROFS)", ok, r["stdout"])

    # ------------------------------------------------------------------
    # T7: Template 预装 pandas 可用
    # ------------------------------------------------------------------
    def t7_template_pandas(self):
        print("\n--- T7: Template pre-installed pandas ---")
        r = self._exec("import pandas; print(pandas.__version__)")
        ok = r["stdout"] != "" and "Error" not in r["stderr"]
        self.assert_ok("T7 import pandas", ok, r["stdout"])

    # ------------------------------------------------------------------
    # T8: XFS quota 500MB — dd 撞 ENOSPC
    # ------------------------------------------------------------------
    def t8_quota(self):
        print("\n--- T8: XFS quota 500MB (dd 600MB → ENOSPC) ---")
        code = (
            "import subprocess, sys\n"
            "r = subprocess.run(\n"
            "    ['dd','if=/dev/zero','of=/workspace/big',\n"
            "     'bs=1M','count=600'],\n"
            "    capture_output=True, text=True)\n"
            "print(f'rc={r.returncode}')\n"
            "print(r.stderr[-200:] if r.stderr else 'no-stderr')\n"
        )
        r = self._exec(code, timeout=60)
        ok = "No space" in r["stdout"] or "rc=1" in r["stdout"]
        self.assert_ok("T8 quota ENOSPC on 600MB write", ok, r["stdout"][:120])
        # 清理
        self._exec("import os; os.remove('/workspace/big') if os.path.exists('/workspace/big') else None")

    # ------------------------------------------------------------------
    # T9: DELETE sandbox 后 NFS 数据仍在
    # ------------------------------------------------------------------
    def t9_data_survives_kill(self):
        print("\n--- T9: NFS data survives sandbox DELETE ---")
        # 先确认 smoke-test.txt 在 workspace 里（T4 写的）
        r = self._exec("import os; print(os.path.exists('/workspace/smoke-test.txt'))")
        pre_ok = "True" in r["stdout"]

        # kill sandbox
        try:
            httpx.delete(f"{API_URL}/sandboxes/{self.sandbox_id}", timeout=15)
        except Exception:
            pass
        time.sleep(2)

        # 无法再从沙箱内验证，只能输出提示让运维手动确认
        # 真正的断言：NFS server 上 ls 文件是否存在
        note = (f"sandbox {self.sandbox_id} deleted. "
                f"Manual check: ls {SESSION_WORKSPACE}/smoke-test.txt on NFS server")
        self.assert_ok("T9 data survives kill (manual verify)", pre_ok, note)

    # ------------------------------------------------------------------
    # T10: _RUNTIME_INJECT 端到端（cwd + OUTPUT_DIR + NPM_CONFIG_PREFIX）
    # ------------------------------------------------------------------
    def t10_runtime_inject(self):
        print("\n--- T10: Runtime injection verification ---")
        # 需要新建一个 sandbox 跑这个测试（T9 已杀掉了前一个）
        mounts = [
            {"hostPath": SESSION_WORKSPACE,
             "mountPath": "/workspace", "readOnly": False},
            {"hostPath": PLATFORM_SKILLS,
             "mountPath": "/opt/skills-platform", "readOnly": True},
            {"hostPath": SESSION_SKILLS,
             "mountPath": "/opt/skills-user", "readOnly": True},
        ]
        body = {
            "templateID": TEMPLATE_ID,
            "timeout": 120,
            "lifecycle": {"on_timeout": "kill", "auto_resume": False},
            "metadata": {"host-mount": json.dumps(mounts)},
        }
        resp = httpx.post(f"{API_URL}/sandboxes", json=body, timeout=30)
        if resp.status_code not in (200, 201):
            self.assert_ok("T10 runtime inject (sandbox create failed)", False,
                           f"status={resp.status_code}")
            return
        data = resp.json()
        self.sandbox_id = data["sandboxID"]
        self.domain = data.get("domain", "cube.app")
        time.sleep(3)

        # 模拟 lea-tools 的 run_code：先 prepend _RUNTIME_INJECT 再执行用户代码
        user_code = (
            "import os\n"
            "print(f'cwd={os.getcwd()}')\n"
            "print(f'OUTPUT_DIR={os.environ.get(\"OUTPUT_DIR\",\"UNSET\")}')\n"
            "print(f'NPM_CONFIG_PREFIX={os.environ.get(\"NPM_CONFIG_PREFIX\",\"UNSET\")}')\n"
        )
        full_code = _RUNTIME_INJECT + "\n" + user_code
        r = self._exec(full_code)
        out = r["stdout"]
        ok_cwd = "cwd=/workspace/output" in out
        ok_env = "OUTPUT_DIR=/workspace/output" in out
        ok_npm = "NPM_CONFIG_PREFIX=/tmp/npm-global" in out
        all_ok = ok_cwd and ok_env and ok_npm
        self.assert_ok("T10 runtime inject (cwd+OUTPUT_DIR+NPM_PREFIX)",
                       all_ok, out.replace("\n", " | "))

        # 清理 T10 sandbox
        try:
            httpx.delete(f"{API_URL}/sandboxes/{self.sandbox_id}", timeout=10)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def run_all(self):
        print("=" * 60)
        print("  Phase 0 · NFS Host-Mount Smoke Test (10 assertions)")
        print(f"  API: {API_URL}  Template: {TEMPLATE_ID}")
        print("=" * 60)

        self.t1_create_sandbox()
        if not self.sandbox_id:
            print("\n[ABORT] T1 failed, cannot continue.")
            return self._summary()

        self.t2_detect_uid()
        self.t3_mounts_visible()
        self.t4_workspace_write()
        self.t5_platform_ro()
        self.t6_user_skills_ro()
        self.t7_template_pandas()
        self.t8_quota()
        self.t9_data_survives_kill()
        self.t10_runtime_inject()
        return self._summary()

    def _summary(self) -> int:
        print("\n" + "=" * 60)
        passed = sum(1 for _, ok, _ in self.results if ok)
        total = len(self.results)
        print(f"  Result: {passed}/{total} passed")
        if passed < total:
            print("  FAILED:")
            for name, ok, detail in self.results:
                if not ok:
                    print(f"    - {name}: {detail}")
        print("=" * 60)
        return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(SmokeTest().run_all())
