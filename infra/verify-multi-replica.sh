#!/usr/bin/env bash
# 双副本行为验证 —— 配合 infra/docker-compose.verify.yml 用。
#
# 这三条是单元测试覆盖不到的：它们依赖真实的 HTTP、真实的负载均衡分发、真实的
# Redis 控制面。单副本下每一条都会走进"本进程内存里就有"的快路径，于是跨副本
# 的代码一行都不执行 —— 测出来的绿是假的。
#
# 前提：nginx 必须是 worker_processes 1。默认 auto（这台机器上 34 个）时轮询状态
# 每个 worker 各一份，低流量下几乎全打到第一个副本，第二个副本根本摸不到。
#
# 用法：
#     bash infra/verify-multi-replica.sh
set -uo pipefail

BASE=${BASE:-http://127.0.0.1:8080/api}
TOK=$(cat "${TOKEN_FILE:-verify_token.txt}")
AUTH="Authorization: Bearer $TOK"
JSON="Content-Type: application/json"

pass=0; fail=0
ok()   { echo "  PASS  $1"; pass=$((pass+1)); }
no()   { echo "  FAIL  $1"; fail=$((fail+1)); }

# 哪个副本服务了最近一个匹配的请求。
#
# ⚠️ 只对**已经结束**的请求有效。nginx 的 access_log 是在请求收尾时才写的，
# 流式请求挂着的这段时间日志里根本没有它 —— 拿它去判断"当前这条流在哪个副本"
# 会读到之前某个请求的记录。踩过：一开始用它做跨副本的前提检查，结果两条流
# 明明在不同副本上，检查却说在同一个。
#
# 判断跨副本一律用**服务端自己的日志**（下面那两个 helper），那是权威的。
last_upstream() { docker logs verify-nginx 2>&1 | grep "$1" | tail -1 | sed -E 's/.*upstream=([0-9.]+):.*/\1/'; }

# 某个副本是否执行过"远端发来的停止"。只有跨副本那条路会打这条日志。
saw_remote_stop() {
  docker logs verify-agent-a 2>&1 | grep -q "remote_stop_applied session=$1" ||
  docker logs verify-agent-b 2>&1 | grep -q "remote_stop_applied session=$1"
}

# 某个副本是否请过远端让位。同上，只有跨副本抢锁那条路会打。
saw_cross_pod_interrupt() {
  docker logs verify-agent-a 2>&1 | grep -q "cross_pod_interrupt session=$1" ||
  docker logs verify-agent-b 2>&1 | grep -q "cross_pod_interrupt session=$1"
}

echo "=== 1. 跨副本停止一个正在跑的 turn ==="
# 长回答，给停止留出窗口
python - <<'PY'
import json, uuid
sid = str(uuid.uuid4())
json.dump({"session_id": sid, "message": "请写一篇不少于800字的散文，题目《雨》", "agent_id": 0},
          open("long.json", "w"), ensure_ascii=False)
open("long.sid", "w").write(sid)
PY
SID=$(cat long.sid)

# 起流（后台），它会占住一个副本
curl -s -N -m 120 -X POST -H "$AUTH" -H "$JSON" --data-binary @long.json \
     "$BASE/chat/stream" > stream.out 2>&1 &
STREAM_PID=$!
sleep 6   # 等它真的开始生成

# 停止是独立请求，轮询会把它送到另一个副本
STOP_BODY=$(curl -s -m 15 -X POST -H "$AUTH" "$BASE/chat/stop/$SID")
echo "  stop 返回: $STOP_BODY"

# forwarded=true 是权威的跨副本证据：服务端只在"本副本内存里没这个会话、
# 已通过 Redis 把停止信号发出去"时才设它。
case "$STOP_BODY" in
  *'"forwarded":true'*)
    ok "走了跨副本转发路径（forwarded=true）"
    sleep 1
    saw_remote_stop "$SID" && ok "另一个副本执行了远端停止（remote_stop_applied）" \
                           || no "没有副本记录 remote_stop_applied —— 信号没被执行？"
    ;;
  *'"stopped":true'*)
    echo "        （这次落到了持有会话的那个副本，本轮走的是本地路径）"
    ok "本地停止有效"
    ;;
  *) no "stop 的响应不对: $STOP_BODY" ;;
esac

wait $STREAM_PID 2>/dev/null
# 权威的完成信号在流上，不在 stop 的响应里
if grep -q '"stopped":true' stream.out; then
  ok "流上收到 result{stopped:true} —— 生成真的被掐断了"
else
  no "流上没有 stopped:true，回答可能还在继续生成"
  grep -E "^event:|^data:" stream.out | tail -4 | sed 's/^/        /'
fi
grep -q "event:done" stream.out && ok "流以 done 收尾" || no "流没有 done"

echo
echo "=== 2. 跨副本断线续传 ==="
python - <<'PY'
import json, uuid
sid = str(uuid.uuid4())
json.dump({"session_id": sid, "message": "请写一篇不少于600字的短文，题目《风》", "agent_id": 0},
          open("resume.json", "w"), ensure_ascii=False)
open("resume.sid", "w").write(sid)
PY
SID2=$(cat resume.sid)

# 只读前 4 秒就掐断，模拟断线
curl -s -N -m 4 -X POST -H "$AUTH" -H "$JSON" --data-binary @resume.json \
     "$BASE/chat/stream" > partial.out 2>&1
LAST_ID=$(grep '^id:' partial.out | tail -1 | cut -d: -f2)
PARTIAL_UP=$(last_upstream 'chat/stream')
echo "  断线前收到 $(grep -c '^id:' partial.out) 个事件，最后 id=$LAST_ID（流在 $PARTIAL_UP）"

if [ -z "$LAST_ID" ]; then
  no "断线前一个带 id 的事件都没收到 —— 后面的续传测不了"
else
  ok "断线前拿到了续传游标 id=$LAST_ID"
  # 用游标续传。这是独立的 GET，轮询会送到另一个副本
  curl -s -N -m 120 -H "$AUTH" "$BASE/chat/stream/$SID2?from=$LAST_ID" > resumed.out 2>&1
  RESUME_UP=$(last_upstream "chat/stream/$SID2")
  echo "  续传落在 $RESUME_UP"
  [ "$RESUME_UP" != "$PARTIAL_UP" ] && ok "续传确实落在另一个副本" \
                                    || no "续传落在同一副本，没测到跨副本"
  FIRST_RESUMED=$(grep '^id:' resumed.out | head -1 | cut -d: -f2)
  if [ "$FIRST_RESUMED" = "$((LAST_ID+1))" ]; then
    ok "从 id=$FIRST_RESUMED 接上，不重不漏"
  else
    no "续传的第一个 id=$FIRST_RESUMED，期望 $((LAST_ID+1)) —— 差一就会重复或缺字"
  fi
  grep -q "event:done" resumed.out && ok "续传的流收到了 done（本轮完整）" \
                                   || no "续传没收到 done"
fi

echo
echo "=== 3. 生成中再发消息 → 打断而不是 409 ==="
python - <<'PY'
import json, uuid
sid = str(uuid.uuid4())
json.dump({"session_id": sid, "message": "请写一篇不少于800字的散文，题目《雪》", "agent_id": 0},
          open("busy1.json", "w"), ensure_ascii=False)
json.dump({"session_id": sid, "message": "算了，只回复两个字：好的", "agent_id": 0},
          open("busy2.json", "w"), ensure_ascii=False)
PY
curl -s -N -m 120 -X POST -H "$AUTH" -H "$JSON" --data-binary @busy1.json \
     "$BASE/chat/stream" > busy1.out 2>&1 &
B1=$!
sleep 6
SID3=$(python -c "import json;print(json.load(open('busy2.json'))['session_id'])")
CODE=$(curl -s -N -m 90 -o busy2.out -w "%{http_code}" -X POST -H "$AUTH" -H "$JSON" \
        --data-binary @busy2.json "$BASE/chat/stream")
echo "  HTTP $CODE"
wait $B1 2>/dev/null

# 同上：用服务端日志判断跨副本，不用 nginx 的（流还挂着时它没记录）。
if saw_cross_pod_interrupt "$SID3"; then
  ok "走了跨副本打断路径（cross_pod_interrupt）"
else
  echo "        （这次两条落在同一副本，走的是本地打断 —— 也是对的行为）"
fi
if [ "$CODE" = "200" ]; then
  ok "第二条被接受（打断了远端的 turn），不是 409 拒绝"
elif [ "$CODE" = "409" ]; then
  no "返回 409 —— 跨副本让位失败，退回了拒绝"
else
  no "HTTP $CODE"
fi
grep -q '"stopped":true' busy1.out && ok "第一轮的流报告被打断" \
                                   || no "第一轮没有报告被打断"

echo
echo "=== $pass 通过 / $fail 失败 ==="
[ "$fail" -eq 0 ]
