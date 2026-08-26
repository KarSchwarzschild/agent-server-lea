"""端到端验证 skill 投递：S3 → NFS 缓存 → session 目录 → 沙箱 /opt/skills。

跑法（BASE 指向部署了新代码的 lea-tools）：
    BASE=http://10.192.241.137:8400 python scripts/verify_skill_delivery.py

验的是这次修的那个 bug 的核心断言：**沙箱里真能读到 references/*.md**。
之前 activate_skill 让 LLM 去 /workspace/skill/ 读，那目录永远是空的
（下发端点从未实现，404 被静默丢弃），LLM 因此跳过了所有依赖 reference
文件的步骤。所以 T4/T5 不过，这次修改就等于没生效。

隔离断言（T6）同样关键：只投递 agent 绑定的 skill，没绑定的沙箱内必须不存在
—— 否则用户 `ls /opt/skills` 就能摸清平台全部 skill。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("BASE", "http://10.192.241.137:8400")
TENANT = "default"
# 只绑一个；另外三个（pptx / xlsx / skill-creator）用来验隔离
BOUND = [{
    "name": "pptx-generator",
    "bundle_uri": "s3://lea-uploads/skills/default/pptx-generator/"
                  "2b83b437d3b819d560d1038c0dfbe59880f7a11ae1ceae6d8d25ff65febaa326.zip",
}]
UNBOUND = ["pptx", "xlsx", "skill-creator"]

_passed, _failed = 0, 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"  -- {detail}" if detail else ""))


def api(method: str, path: str, payload: dict | None = None, timeout: int = 240):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
        return r.status, (json.loads(body) if body.strip() else {})


def run(sid: str, code: str) -> dict:
    _, r = api("POST", f"/sandbox/sessions/{sid}/exec",
               {"code": code, "language": "python", "timeout": 90})
    return r


def main() -> int:
    sid = f"skillverify-{uuid.uuid4().hex[:8]}"
    print(f"BASE={BASE}\nsession={sid}\n")

    # T1 contract: 服务端认不认 skills 字段（老代码会忽略它，后面 T4 就会挂）
    print("== T1 create with skills ==")
    try:
        st, r = api("POST", "/sandbox/sessions", {
            "session_id": sid, "tenant_id": TENANT,
            "user_id": "verifier", "skills": BOUND,
        })
        check("sandbox created", st in (200, 201), f"status={st}")
        print(f"        sandbox_id={r.get('sandbox_id')}")
    except urllib.error.HTTPError as e:
        check("sandbox created", False, f"HTTP {e.code}: {e.read().decode()[:200]}")
        return 1
    except Exception as e:
        check("sandbox created", False, repr(e)[:200])
        return 1

    try:
        # T2 挂载点存在
        print("== T2 mount points ==")
        r = run(sid, "import os\n"
                     "print(os.path.isdir('/workspace'), os.path.isdir('/opt/skills'))")
        out = (r.get("stdout") or "").strip()
        check("/workspace + /opt/skills both mounted", out.startswith("True True"),
              f"stdout={out!r} stderr={(r.get('stderr') or '')[:200]!r}")

        # T3 skill 目录名
        print("== T3 provisioned skill dirs ==")
        r = run(sid, "import os\nprint(sorted(os.listdir('/opt/skills')))")
        out = (r.get("stdout") or "").strip()
        check("pptx-generator present", "pptx-generator" in out, f"stdout={out!r}")

        # T4 核心：references 真的可读
        print("== T4 references readable (THE bug) ==")
        r = run(sid, "import os\n"
                     "d='/opt/skills/pptx-generator/references'\n"
                     "print(sorted(os.listdir(d)) if os.path.isdir(d) else 'MISSING')")
        out = (r.get("stdout") or "").strip()
        for f in ("slide-types.md", "design-system.md", "pitfalls.md"):
            check(f"references/{f} exists", f in out, f"listing={out!r}")

        # T5 内容对得上（不只是文件在）
        print("== T5 content sane ==")
        r = run(sid, "p='/opt/skills/pptx-generator/references/slide-types.md'\n"
                     "t=open(p,encoding='utf-8').read()\n"
                     "print(len(t)); print('Cover' in t or 'cover' in t)")
        lines = (r.get("stdout") or "").strip().splitlines()
        size = int(lines[0]) if lines and lines[0].isdigit() else 0
        check("slide-types.md non-trivial (>5KB)", size > 5000, f"size={size}")
        check("slide-types.md mentions page types",
              len(lines) > 1 and lines[1].strip() == "True", f"stdout={lines}")

        # T6 隔离：没绑定的不能在
        print("== T6 isolation ==")
        r = run(sid, "import os\nprint(sorted(os.listdir('/opt/skills')))")
        out = (r.get("stdout") or "").strip()
        for name in UNBOUND:
            check(f"unbound '{name}' absent", f"'{name}'" not in out, f"listing={out!r}")

        # T7 RO：用户改不了 skill
        print("== T7 read-only ==")
        r = run(sid, "try:\n"
                     "    open('/opt/skills/pptx-generator/SKILL.md','a').write('x')\n"
                     "    print('WRITABLE')\n"
                     "except OSError as e:\n"
                     "    print('blocked', e.errno)")
        out = (r.get("stdout") or "").strip()
        check("/opt/skills is read-only", "WRITABLE" not in out, f"stdout={out!r}")
    finally:
        try:
            api("DELETE", f"/sandbox/sessions/{sid}", timeout=60)
            print("\n(sandbox cleaned up)")
        except Exception as e:
            print(f"\n(cleanup failed: {e!r})")

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
