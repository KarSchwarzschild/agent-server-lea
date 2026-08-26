#!/usr/bin/env bash
# 集成测试：workspace 文件端点（list / download / 越权防护 / 沙箱死后存活）
#
# 覆盖范围取决于 lea-tools 能否写共享存储（NFS_ROOT 存不存在）：
#
#   store=nfs      lea-tools 与 Cube 节点挂同一份存储（生产拓扑：同机 XFS，
#                  或 ECS/CCE 挂 SFS Turbo）。**完整链路**：沙箱写文件 →
#                  host-mount 落共享存储 → readdir 读到 → 沙箱死后仍在。
#
#   store=sandbox  无共享存储（本机开发）。走 envd 读活沙箱，真沙箱真文件，
#                  但**不经过 NFS**，且沙箱死后读不到 —— survive 一项跳过。
#
# 想测真 NFS 路径就必须让 lea-tools 挂上那块盘，没有捷径。
# mock 目录（.local/nfs-mock）只是前端 fixture，不是集成测试。
#
# 用法：BASE=http://127.0.0.1:8400 ./scripts/integ_test_files.sh

set -euo pipefail

BASE="${BASE:-http://127.0.0.1:8400}"
SID="integ-files-$$"
TID="integ-tenant-01"
Q="session_id=$SID&tenant_id=$TID"
fail=0

ok()   { echo "  PASS  $1"; }
bad()  { echo "  FAIL  $1"; fail=1; }

cleanup() { curl -sf -X DELETE "$BASE/sandbox/sessions/$SID" --max-time 60 >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== create sandbox =="
curl -sf -X POST "$BASE/sandbox/sessions" -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"tenant_id\":\"$TID\",\"user_id\":\"u1\"}" \
  --max-time 120 >/dev/null
ok "sandbox created"

echo "== exec: produce artifacts =="
CODE='import os
open("chart.png","w").write("fake png")
open("summary.xlsx","w").write("fake xlsx data")
os.makedirs("__pycache__", exist_ok=True)
open("__pycache__/junk.pyc","w").write("junk")
print(os.getcwd())'
CWD=$(python -c "
import json,sys,urllib.request
req=urllib.request.Request('$BASE/sandbox/sessions/$SID/exec',
  data=json.dumps({'code':'''$CODE'''}).encode(),
  headers={'Content-Type':'application/json'})
print(json.load(urllib.request.urlopen(req,timeout=120))['stdout'].strip())
")
[ "$CWD" = "/workspace/output" ] && ok "cwd injected -> /workspace/output" \
                                || bad "cwd is '$CWD', expected /workspace/output"

echo "== list =="
LIST=$(curl -sf "$BASE/api/files/list?$Q" --max-time 60)
echo "$LIST" | grep -q '"output/chart.png"'      && ok "chart.png listed"    || bad "chart.png missing"
echo "$LIST" | grep -q '"output/summary.xlsx"'   && ok "summary.xlsx listed" || bad "summary.xlsx missing"
echo "$LIST" | grep -q 'junk.pyc'                && bad ".pyc not hidden"    || ok ".pyc hidden"
echo "$LIST" | grep -q '"in_output": *true'      && ok "in_output flagged"   || bad "in_output missing"
echo "  store = $(echo "$LIST" | python -c 'import json,sys; print(json.load(sys.stdin)["store"])')"

echo "== list show_hidden =="
curl -sf "$BASE/api/files/list?$Q&show_hidden=true" --max-time 60 \
  | grep -q 'junk.pyc' && ok ".pyc visible with show_hidden" || bad "show_hidden broken"

echo "== download =="
BODY=$(curl -sf "$BASE/api/files/download?$Q&path=output/summary.xlsx" --max-time 60)
[ "$BODY" = "fake xlsx data" ] && ok "content matches" || bad "content='$BODY'"

echo "== traversal =="
for p in "../../../etc/passwd" "/etc/passwd"; do
  C=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/files/download?$Q&path=$p" --max-time 30)
  [ "$C" = "400" ] && ok "blocked $p ($C)" || bad "$p returned $C, expected 400"
done

# 核心断言：沙箱销毁后文件是否还在。这是 host-mount 存在的全部理由，
# 也是 mock 目录永远测不到的一环 —— 只有真沙箱写进真共享存储才算通过。
echo "== survive sandbox kill =="
STORE=$(echo "$LIST" | python -c 'import json,sys; print(json.load(sys.stdin)["store"])')
if [ "$STORE" != "nfs" ]; then
  echo "  SKIP  store=$STORE — 无共享存储，沙箱死后本就读不到"
  echo "        要覆盖这条，把 lea-tools 部到挂了同一份存储的机器上再跑"
else
  curl -sf -X DELETE "$BASE/sandbox/sessions/$SID" --max-time 60 >/dev/null
  sleep 2
  AFTER=$(curl -sf "$BASE/api/files/list?$Q" --max-time 60)
  echo "$AFTER" | grep -q '"output/summary.xlsx"' \
    && ok "file survived sandbox destruction" \
    || bad "file gone after kill — host-mount 没生效或写到了沙箱本地 fs"
  BODY2=$(curl -sf "$BASE/api/files/download?$Q&path=output/summary.xlsx" --max-time 60 || true)
  [ "$BODY2" = "fake xlsx data" ] && ok "content readable after kill" \
                                  || bad "content after kill='$BODY2'"
fi

echo
[ $fail -eq 0 ] && echo "ALL PASS" || echo "FAILURES"
exit $fail
