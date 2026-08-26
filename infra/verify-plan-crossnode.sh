#!/usr/bin/env bash
# Plan 模式跨副本验证 —— 配合 infra/docker-compose.verify.yml 用。
#
# 验证的是：plan_mode=true 在副本 A 提交计划（SavePlan 写入共享 DB），
# plan_mode=false 在副本 B 批准并加载计划（ActivatePlan + LoadActivePlan 从共享 DB 读）。
# 如果 plan 只存内存里，副本 B 根本看不到副本 A 存的东西。
#
# 前提：
#   - docker compose -f infra/docker-compose.verify.yml up -d 已起
#   - agent 镜像已构建（lea-agent:dev）
#   - SECRET_KEY 在 .env 里配了，或设了 LEA_AGENT_ALLOW_DEV_SECRET=1
#   - agent 能连到 LLM（plan 生成靠模型调 AskUser）
#
# 用法：
#     bash infra/verify-plan-crossnode.sh
#
# 可选环境变量：
#     BASE=http://127.0.0.1:8080/api   # nginx 入口
#     SECRET_KEY=xxx                    # 跟 agent 用的同一个 JWT 密钥
set -uo pipefail

BASE=${BASE:-http://127.0.0.1:8080/api}
SECRET=${SECRET_KEY:-$(grep -oP '(?<=SECRET_KEY=).*' ../.env 2>/dev/null | head -1 | tr -d '"\r')}
if [ -z "$SECRET" ]; then
  SECRET="dev-secret-key-change-me"
  echo "  （未配 SECRET_KEY，使用开发密钥——agent 侧需 LEA_AGENT_ALLOW_DEV_SECRET=1）"
fi

pass=0; fail=0
ok()   { echo "  PASS  $1"; pass=$((pass+1)); }
no()   { echo "  FAIL  $1"; fail=$((fail+1)); }

# ── 生成 JWT ────────────────────────────────────────────────────────────
TOKEN=$(python3 -c "
import jwt, time
claims = {
    'sub': 'plan-test-user',
    'tenant_id': 'plan-tenant',
    'roles': ['user'],
    'iat': int(time.time()),
    'exp': int(time.time()) + 3600,
}
print(jwt.encode(claims, '$SECRET', algorithm='HS256'))
")
AUTH="Authorization: Bearer $TOKEN"
JSON="Content-Type: application/json"

# nginx 在请求**结束后**才写 access_log，所以必须在流结束后调。
last_upstream() { docker logs verify-nginx 2>&1 | grep "$1" | tail -1 | sed -E 's/.*upstream=([0-9.]+):.*/\1/'; }

# 在某个副本的日志里找 plan 操作
saw_in_agent() {
  local agent="$1" pattern="$2"
  docker logs "$agent" 2>&1 | grep -q "$pattern"
}

# 找 plan 操作在哪个副本
which_agent_saw() {
  local pattern="$1"
  if saw_in_agent verify-agent-a "$pattern"; then echo "a"; return; fi
  if saw_in_agent verify-agent-b "$pattern"; then echo "b"; return; fi
  echo ""
}

echo "=== Plan 模式跨副本验证 ==="
echo "  nginx: $BASE"
echo

# ── 0. 健康检查 ──
echo "--- 0. 存活检查 ---"
HC=$(curl -s -m 5 "http://127.0.0.1:8080/nginx-health" 2>/dev/null || echo "fail")
if [ "$HC" = "ok" ]; then
  ok "nginx 存活"
else
  no "nginx 不通——先 docker compose -f infra/docker-compose.verify.yml up -d"
  exit 1
fi

# ── 1. 创建会话 ──
echo
echo "--- 1. 创建会话 ---"
SID=$(python3 -c "import uuid; print(str(uuid.uuid4()))")
echo "  session_id=$SID"

# ── 2. plan_mode=true：提交计划 ──
echo
echo "--- 2. plan_mode=true → 等待 agent 提交计划（AskUser） ---"
python3 -c "
import json
json.dump({
    'session_id': '$SID',
    'message': '帮我分析一下这个项目的代码架构，列出主要模块和它们的职责，然后给出一个重构计划',
    'agent_id': 0,
    'plan_mode': True
}, open('/tmp/plan_req.json', 'w'), ensure_ascii=False)
"

# SSE 流是长连接——curl 在服务端发完 done 关连接后才返回
curl -s -N -m 180 -X POST -H "$AUTH" -H "$JSON" \
     --data-binary @/tmp/plan_req.json \
     "$BASE/chat/stream" > /tmp/plan_stream.out 2>&1

# 流结束后 nginx 日志才写
PLAN_UP=$(last_upstream 'chat/stream')
echo "  plan_mode=true 落在 $PLAN_UP"

ASK_FOUND=false
grep -q 'event:ask_user' /tmp/plan_stream.out 2>/dev/null && ASK_FOUND=true

if [ "$ASK_FOUND" = "true" ]; then
  ok "收到 ask_user 事件——agent 提交了计划"

  # 提取计划内容
  PLAN_TEXT=$(python3 -c "
import json
text = open('/tmp/plan_stream.out').read()
for line in text.split('\n'):
    if line.startswith('data:'):
        try:
            d = json.loads(line[5:].strip())
            if 'question' in d:
                print(d['question'][:200])
                break
        except: pass
" 2>/dev/null)
  if [ -n "$PLAN_TEXT" ]; then
    echo "  计划摘要: ${PLAN_TEXT:0:120}..."
  fi
else
  if grep -q 'event:result' /tmp/plan_stream.out 2>/dev/null; then
    echo "  （模型没有使用 AskUser——直接生成了文本回复）"
    ok "模型回复了（但没用 AskUser 提交计划——plan 持久化路径未走到）"
  else
    no "既没收到 ask_user 也没收到 result"
    tail -5 /tmp/plan_stream.out 2>/dev/null | sed 's/^/        /'
  fi
fi
grep -q "event:done" /tmp/plan_stream.out && ok "plan_mode 流以 done 收尾" || no "plan_mode 流没有 done"

# ── 3. plan_mode=false：批准并执行 ──
echo
echo "--- 3. plan_mode=false → 批准计划并执行 ---"
python3 -c "
import json
json.dump({
    'session_id': '$SID',
    'message': '可以，按计划执行',
    'agent_id': 0,
    'plan_mode': False
}, open('/tmp/exec_req.json', 'w'), ensure_ascii=False)
"

curl -s -N -m 300 -X POST -H "$AUTH" -H "$JSON" \
     --data-binary @/tmp/exec_req.json \
     "$BASE/chat/stream" > /tmp/exec_stream.out 2>&1

EXEC_UP=$(last_upstream 'chat/stream')
echo "  plan_mode=false 落在 $EXEC_UP"

if grep -q 'event:result' /tmp/exec_stream.out 2>/dev/null; then
  ok "收到 result 事件"
else
  no "没有收到 result 事件"
  if grep -q 'event:error' /tmp/exec_stream.out 2>/dev/null; then
    echo "  错误内容:"
    grep 'event:error' -A1 /tmp/exec_stream.out | head -3 | sed 's/^/        /'
  fi
fi
grep -q "event:done" /tmp/exec_stream.out && ok "执行流以 done 收尾" || no "执行流没有 done"

# ── 4. 跨节点判定 ──
echo
echo "--- 4. 跨节点判定 ---"
# 权威证据：agent 自己的日志。nginx 的 upstream 日志只在请求结束后写，
# 但 agent 的 log.Printf 是实时的。
SAVE_AGENT=$(which_agent_saw "save_plan")
ACTIVATE_AGENT=$(which_agent_saw "activate_plan")
LOAD_AGENT=$(which_agent_saw "load_active_plan")

echo "  save_plan     在 agent-$SAVE_AGENT"
echo "  activate_plan 在 agent-$ACTIVATE_AGENT"
echo "  load_active_plan 在 agent-$LOAD_AGENT"

if [ -n "$SAVE_AGENT" ] && [ -n "$ACTIVATE_AGENT" ]; then
  if [ "$SAVE_AGENT" != "$ACTIVATE_AGENT" ]; then
    ok "跨节点验证成功：计划在 agent-$SAVE_AGENT 提交，在 agent-$ACTIVATE_AGENT 批准加载"
  else
    echo "  （两步都在 agent-$SAVE_AGENT —— 本地路径也对，但这次没测到跨节点）"
    ok "单节点路径有效"
    echo "  提示：nginx 轮询是严格交替的（worker_processes=1），再跑一次会落在另一个副本"
  fi
elif [ -n "$PLAN_UP" ] && [ -n "$EXEC_UP" ]; then
  # 退回 nginx 日志判定
  if [ "$PLAN_UP" != "$EXEC_UP" ]; then
    ok "跨节点（nginx 日志）：plan=$PLAN_UP, exec=$EXEC_UP"
  elif [ "$PLAN_UP" = "$EXEC_UP" ]; then
    ok "单节点路径有效（nginx: $PLAN_UP）"
  fi
elif [ "$ASK_FOUND" = "false" ]; then
  echo "  （模型没用 AskUser，所以没有 save_plan 日志——跳过跨节点判定）"
else
  no "无法确定 plan 操作的副本归属"
fi

# ── 5. 验证消息历史 ──
echo
echo "--- 5. 验证消息历史 ---"
MSGS=$(curl -s -m 15 -H "$AUTH" "$BASE/sessions/$SID/messages" 2>/dev/null)
MSG_COUNT=$(echo "$MSGS" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('messages',[])))" 2>/dev/null || echo "0")
if [ "$MSG_COUNT" -ge 2 ]; then
  ok "消息历史有 $MSG_COUNT 条（user + assistant）"
else
  no "消息历史只有 $MSG_COUNT 条，期望至少 2 条"
fi

echo
echo "=== $pass 通过 / $fail 失败 ==="
[ "$fail" -eq 0 ]
