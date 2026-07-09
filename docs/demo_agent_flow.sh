#!/usr/bin/env bash
# LEA Platform · Agent 全链路 e2e Demo（内部开发/QA 版）
#
# 覆盖能力（每步 HTTP 后断言关键字段，失败立即退出）：
#   Auth → Tool 创建 → Skill 创建 → Agent 创建
#   → 关系编织（skill_bind / tool_bind）
#   → Chat 三态（普通对话 / 含 PII / 含注入 / 学号 Luhn 挡）
#   → 审计 DB 断言（pii_kinds / injection_patterns）
#   → 清理（DELETE agent/skill/tool）
#
# 前置：
#   1. uvicorn 已跑在 :8100，健康检查通过
#   2. admin/admin123 账号已建（Ph1 初始 seed）
#   3. python 环境能 import lea_server（uv sync 或 pip install -e 已跑）
#
# 用法：
#   bash docs/demo_agent_flow.sh              # 默认 http://localhost:8100
#   LEA_BASE=http://x.y:9000 bash docs/demo_agent_flow.sh
#   KEEP_ARTIFACTS=1 bash docs/demo_agent_flow.sh   # 跳过清理，便于人工回查
#
# 退出码：0 = 全绿；非 0 = 某步失败（stderr 有详情）

set -euo pipefail

# ── 常量 ────────────────────────────────────────────
BASE="${LEA_BASE:-http://localhost:8100}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="${SCRIPT_DIR}/demo"
PAYLOAD_DIR="${DEMO_DIR}/payloads"
TMP_DIR="$(mktemp -d -t lea-demo.XXXXXX)"
LIB="python \"${DEMO_DIR}/_lib.py\""

# Demo 的 python 子进程用 uv run 环境（能 import lea_graph 全部依赖，含 boto3/structlog）。
# PYTHON 是数组以便含空格；覆盖示例：PYTHON=(python) bash docs/demo_agent_flow.sh
if [ -n "${PYTHON:-}" ]; then
  # 用户传字符串（如 PYTHON=python），拆成数组
  read -ra PYTHON_ARR <<< "${PYTHON}"
else
  PYTHON_ARR=(uv run --project packages/lea-server python)
fi

# Windows GBK 会把中文/emoji 打炸，强制 python 走 UTF-8
export PYTHONIOENCODING="utf-8"

# 时间戳后缀，避免重跑冲突
TS="$(date +%Y%m%d%H%M%S)"
export TS

# 采集副作用产物（用于最后清理）
CREATED_TOOL_ID=""
CREATED_SKILL_ID=""
CREATED_AGENT_ID=""

# ── UI ─────────────────────────────────────────────
if [ -t 1 ]; then
  GREEN=$'\e[32m'; RED=$'\e[31m'; YEL=$'\e[33m'; DIM=$'\e[2m'; NC=$'\e[0m'
else
  GREEN=''; RED=''; YEL=''; DIM=''; NC=''
fi

fail() { echo "${RED}[FAIL]${NC} $*" >&2; cleanup_on_exit; exit 1; }
ok()   { echo "${GREEN}[ OK ]${NC} $*"; }
info() { echo "${DIM}      $*${NC}"; }
step() { echo; echo "${YEL}## $*${NC}"; }

# ── 小工具 ──────────────────────────────────────────

# 渲染 payload 模板（占位符 __KEY__ ← 环境变量）
render() {
  local tmpl="$1" out="$2"
  "${PYTHON_ARR[@]}" "${DEMO_DIR}/_lib.py" render "${tmpl}" "${out}" \
    || fail "render template failed: ${tmpl}"
}

# 从 JSON 字符串取字段：get_json '$body' 'field.subfield'
get_json() {
  "${PYTHON_ARR[@]}" "${DEMO_DIR}/_lib.py" get "$1" "$2"
}

# 发一个带 Auth 的 POST，body 从文件读；打印响应体
curl_post_auth() {
  local url="$1" body_file="$2"
  curl -sS -X POST "${url}" \
    -H "Content-Type: application/json; charset=utf-8" \
    -H "Authorization: Bearer ${TOKEN}" \
    --data-binary "@${body_file}"
}

# 发无认证 POST（登录用）
curl_post() {
  local url="$1" body_file="$2"
  curl -sS -X POST "${url}" \
    -H "Content-Type: application/json; charset=utf-8" \
    --data-binary "@${body_file}"
}

# 消费 SSE 流一段时间，写到文件（简化版：limit 大小）
# --max-time 30s，读到 event: done 或 8KB 就足够验证
sse_capture() {
  local url="$1" body_file="$2" out_file="$3"
  # -N 关闭缓冲；--max-time 兜底；-D 拿 header
  curl -sS -N --max-time 30 -X POST "${url}" \
    -H "Content-Type: application/json; charset=utf-8" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Accept: text/event-stream" \
    --data-binary "@${body_file}" \
    > "${out_file}" || true  # SSE 有时 curl 会返非零（连接被 server 主动关），忽略
}

# SSE 文件里 grep 事件名
sse_has_event() {
  grep -q "^event: $1" "$2"
}

# ── 清理 ────────────────────────────────────────────
cleanup_on_exit() {
  if [ "${KEEP_ARTIFACTS:-0}" = "1" ]; then
    info "KEEP_ARTIFACTS=1 → 跳过 DELETE；产物："
    info "  tool_id=${CREATED_TOOL_ID}  skill_id=${CREATED_SKILL_ID}  agent_id=${CREATED_AGENT_ID}"
    info "  tmp: ${TMP_DIR}"
    return 0
  fi
  # 已有产物才删；失败静默
  [ -n "${CREATED_AGENT_ID}" ] && \
    curl -sS -X DELETE "${BASE}/api/admin/agents/${CREATED_AGENT_ID}" \
      -H "Authorization: Bearer ${TOKEN:-}" > /dev/null 2>&1 || true
  [ -n "${CREATED_SKILL_ID}" ] && \
    curl -sS -X DELETE "${BASE}/api/admin/skills/${CREATED_SKILL_ID}" \
      -H "Authorization: Bearer ${TOKEN:-}" > /dev/null 2>&1 || true
  [ -n "${CREATED_TOOL_ID}" ] && \
    curl -sS -X DELETE "${BASE}/api/admin/tools/${CREATED_TOOL_ID}" \
      -H "Authorization: Bearer ${TOKEN:-}" > /dev/null 2>&1 || true
  rm -rf "${TMP_DIR}"
}
trap cleanup_on_exit EXIT

# ═══════════════════════════════════════════════════
#  开跑
# ═══════════════════════════════════════════════════
echo "${YEL}════════════════════════════════════════════════${NC}"
echo "${YEL} LEA Platform · Agent 全链路 Demo${NC}"
echo "${YEL} BASE=${BASE}  TS=${TS}${NC}"
echo "${YEL}════════════════════════════════════════════════${NC}"

# ── Step 0: 健康检查 ────────────────────────────────
step "0. 健康检查"
HEALTH_CODE=$(curl -sS -o /dev/null -w "%{http_code}" "${BASE}/health" || echo "000")
[ "${HEALTH_CODE}" = "200" ] || fail "健康检查失败：http=${HEALTH_CODE} (uvicorn 是否在 ${BASE} ？)"
ok "health=200"

# ── Step 1: 登录 ────────────────────────────────────
step "1. 登录 admin/admin123 → JWT"
LOGIN_RESP=$(curl_post "${BASE}/api/auth/login" "${PAYLOAD_DIR}/login.json")
TOKEN=$(get_json "${LOGIN_RESP}" "access_token") || fail "解析 access_token 失败: ${LOGIN_RESP}"
[ -n "${TOKEN}" ] || fail "空 token"
ok "token=${TOKEN:0:32}..."

# ── Step 2: 创建 Tool ───────────────────────────────
step "2. 创建 demo Tool（source=api）"
render "${PAYLOAD_DIR}/tool_create.json.tmpl" "${TMP_DIR}/tool_create.json"
TOOL_RESP=$(curl_post_auth "${BASE}/api/admin/tools" "${TMP_DIR}/tool_create.json")
CREATED_TOOL_ID=$(get_json "${TOOL_RESP}" "id") || fail "create tool: ${TOOL_RESP}"
[ -n "${CREATED_TOOL_ID}" ] || fail "空 tool_id"
ok "tool_id=${CREATED_TOOL_ID}"
info "$(get_json "${TOOL_RESP}" "display_name")"

# ── Step 3: 创建 Skill ──────────────────────────────
step "3. 创建 demo Skill（ReAct 模式）"
render "${PAYLOAD_DIR}/skill_create.json.tmpl" "${TMP_DIR}/skill_create.json"
SKILL_RESP=$(curl_post_auth "${BASE}/api/admin/skills" "${TMP_DIR}/skill_create.json")
CREATED_SKILL_ID=$(get_json "${SKILL_RESP}" "id") || fail "create skill: ${SKILL_RESP}"
[ -n "${CREATED_SKILL_ID}" ] || fail "空 skill_id"
ok "skill_id=${CREATED_SKILL_ID}  status=$(get_json "${SKILL_RESP}" "status")"

# ── Step 4: 创建 Agent ──────────────────────────────
step "4. 创建 demo Agent"
render "${PAYLOAD_DIR}/agent_create.json.tmpl" "${TMP_DIR}/agent_create.json"
AGENT_RESP=$(curl_post_auth "${BASE}/api/admin/agents" "${TMP_DIR}/agent_create.json")
CREATED_AGENT_ID=$(get_json "${AGENT_RESP}" "id") || fail "create agent: ${AGENT_RESP}"
[ -n "${CREATED_AGENT_ID}" ] || fail "空 agent_id"
ok "agent_id=${CREATED_AGENT_ID}  name=$(get_json "${AGENT_RESP}" "name")"

# ── Step 5: 绑 Skill 到 Agent ───────────────────────
step "5. 绑定 Skill → Agent"
export SKILL_ID="${CREATED_SKILL_ID}"
render "${PAYLOAD_DIR}/skill_bind.json.tmpl" "${TMP_DIR}/skill_bind.json"
BIND_RESP=$(curl_post_auth "${BASE}/api/admin/agents/${CREATED_AGENT_ID}/skills" "${TMP_DIR}/skill_bind.json")
BIND_ID=$(get_json "${BIND_RESP}" "binding_id") || fail "bind skill: ${BIND_RESP}"
ok "skill_binding_id=${BIND_ID}"

# ── Step 6: 绑 Tool 到 Agent ────────────────────────
step "6. 绑定 Tool → Agent"
export TOOL_ID="${CREATED_TOOL_ID}"
render "${PAYLOAD_DIR}/tool_bind.json.tmpl" "${TMP_DIR}/tool_bind.json"
TBIND_RESP=$(curl_post_auth "${BASE}/api/admin/agents/${CREATED_AGENT_ID}/tools" "${TMP_DIR}/tool_bind.json")
ok "tool bind resp: ${TBIND_RESP}"

# ── Step 7: Chat 普通对话 ───────────────────────────
step "7. Chat · 普通对话（应触发 skill / fallback，SSE 有 content）"
export AGENT_ID="${CREATED_AGENT_ID}"
render "${PAYLOAD_DIR}/chat_normal.json.tmpl" "${TMP_DIR}/chat_normal.json"
SESSION_NORMAL="demo-normal-${TS}"
sse_capture "${BASE}/api/super-agent/chat/stream" \
  "${TMP_DIR}/chat_normal.json" "${TMP_DIR}/sse_normal.txt"

sse_has_event "content" "${TMP_DIR}/sse_normal.txt" \
  || fail "普通对话 SSE 无 content 事件；文件：${TMP_DIR}/sse_normal.txt"
ok "SSE 收到 content 事件"
INTENT_LINE=$(grep -m1 "^data:" "${TMP_DIR}/sse_normal.txt" | head -c 200)
info "首个 data: ${INTENT_LINE}"

# audit：普通对话应有 user_message，pii_kinds 为空
"${PYTHON_ARR[@]}" "${DEMO_DIR}/_lib.py" assert_audit "${SESSION_NORMAL}" "" \
  || fail "普通对话 audit 断言失败"
ok "audit: user_message 无 PII 命中 ✓"

# ── Step 8: Chat PII ────────────────────────────────
step "8. Chat · 含 PII（手机+邮箱）→ audit 应含 phone,email"
render "${PAYLOAD_DIR}/chat_pii.json.tmpl" "${TMP_DIR}/chat_pii.json"
SESSION_PII="demo-pii-${TS}"
sse_capture "${BASE}/api/super-agent/chat/stream" \
  "${TMP_DIR}/chat_pii.json" "${TMP_DIR}/sse_pii.txt"
sse_has_event "content" "${TMP_DIR}/sse_pii.txt" \
  || fail "PII 对话 SSE 无 content 事件（chat 不该被阻断，只审计脱敏）"
ok "SSE 收到 content"

"${PYTHON_ARR[@]}" "${DEMO_DIR}/_lib.py" assert_audit "${SESSION_PII}" "phone,email" \
  || fail "PII audit 断言失败：应命中 phone+email"
ok "audit: PII 命中 phone,email ✓"

# ── Step 9: Chat 学号（Luhn 挡） ────────────────────
step "9. Chat · 学号 16 位（应被 Luhn 挡下 → 无 bank_card 命中）"
render "${PAYLOAD_DIR}/chat_student.json.tmpl" "${TMP_DIR}/chat_student.json"
SESSION_STU="demo-stu-${TS}"
sse_capture "${BASE}/api/super-agent/chat/stream" \
  "${TMP_DIR}/chat_student.json" "${TMP_DIR}/sse_stu.txt"
sse_has_event "content" "${TMP_DIR}/sse_stu.txt" \
  || fail "学号对话 SSE 无 content"
ok "SSE 收到 content"
"${PYTHON_ARR[@]}" "${DEMO_DIR}/_lib.py" assert_audit "${SESSION_STU}" "" \
  || fail "学号 audit 断言失败：Luhn 应该挡下 16 位数字"
ok "audit: 学号 pii_kinds=- （Luhn 挡下）✓"

# ── Step 10: Chat 注入 ──────────────────────────────
step "10. Chat · Prompt 注入（warn 模式：chat 继续，audit 有 injection_hit）"
render "${PAYLOAD_DIR}/chat_injection.json.tmpl" "${TMP_DIR}/chat_injection.json"
SESSION_INJ="demo-inj-${TS}"
sse_capture "${BASE}/api/super-agent/chat/stream" \
  "${TMP_DIR}/chat_injection.json" "${TMP_DIR}/sse_inj.txt"
# warn 模式下 chat 不阻断
sse_has_event "content" "${TMP_DIR}/sse_inj.txt" \
  || fail "注入对话 SSE 无 content（warn 模式不该阻断）"
ok "SSE 收到 content（warn 模式不阻断）"

"${PYTHON_ARR[@]}" "${DEMO_DIR}/_lib.py" assert_injection "${SESSION_INJ}" \
  || fail "注入 audit 断言失败：应存在 injection_hit 行"
ok "audit: injection_hit 已记录 ✓"

# ── Step 11: 可视化事件 · SSE 应含 intent（Sprint B） ─────────
step "11. SSE 可视化 · intent 事件（普通对话流里应至少一次）"
sse_has_event "intent" "${TMP_DIR}/sse_normal.txt" \
  || fail "普通对话 SSE 无 intent 事件（Sprint B: supervisor 路由应 emit）"
ok "SSE 收到 intent 事件 ✓"

# ── Step 12: 会话历史 · sessions 列表 ──────────────────
step "12. Sessions 列表（前端左侧历史）"
SESSIONS_RESP=$(curl -sS -H "Authorization: Bearer ${TOKEN}" "${BASE}/api/super-agent/sessions")
# curl_post_auth 的 GET 版本内联；数组应包含 SESSION_NORMAL
echo "${SESSIONS_RESP}" | grep -q "${SESSION_NORMAL}" \
  || fail "sessions 列表不含 ${SESSION_NORMAL}: ${SESSIONS_RESP:0:200}"
ok "sessions 列表包含 ${SESSION_NORMAL} ✓"

# ── Step 13: chat_messages.extra · 可视化字段落库 ──────
step "13. chat_messages.extra · 落库可视化字段（Sprint B 核心）"
"${PYTHON_ARR[@]}" "${DEMO_DIR}/_lib.py" assert_session_extras "${SESSION_NORMAL}" \
  || fail "session=${SESSION_NORMAL} 的 assistant.extra 里 intent/skill/tool_calls/references 全空"
ok "assistant.extra 至少含一个可视化字段 ✓"

# ── Step 14: Sandbox · 数据分析对话触发 sandbox_exec（Sprint F）─────────
step "14. Chat · 数据分析（触发 sandbox_exec，SSE 有 sandbox_start/done）"
render "${PAYLOAD_DIR}/chat_sandbox.json.tmpl" "${TMP_DIR}/chat_sandbox.json"
SESSION_SBX="demo-sandbox-${TS}"
sse_capture "${BASE}/api/super-agent/chat/stream" \
  "${TMP_DIR}/chat_sandbox.json" "${TMP_DIR}/sse_sbx.txt"
sse_has_event "content" "${TMP_DIR}/sse_sbx.txt" \
  || fail "沙箱对话 SSE 无 content"
sse_has_event "sandbox_start" "${TMP_DIR}/sse_sbx.txt" \
  || fail "沙箱对话 SSE 无 sandbox_start（LLM 没调 sandbox_exec？或事件没串通）"
sse_has_event "sandbox_done" "${TMP_DIR}/sse_sbx.txt" \
  || fail "沙箱对话 SSE 无 sandbox_done"
ok "SSE 收到 sandbox_start + sandbox_done ✓"

# ── Step 15: chat_messages.extra.sandbox_calls · 落库（Sprint F）───────
step "15. chat_messages.extra.sandbox_calls · 落库沙箱执行历史（Sprint F 核心）"
"${PYTHON_ARR[@]}" "${DEMO_DIR}/_lib.py" assert_sandbox "${SESSION_SBX}" \
  || fail "session=${SESSION_SBX} 的 assistant.extra.sandbox_calls 里没有 success 记录"
ok "assistant.extra.sandbox_calls 至少一条 success ✓"

# ── Step 16: Admin API · 上传 script skill bundle（Sprint G）──────────
step "16. Admin · 上传 counter script skill bundle（tar.gz → OBS + DB 版本）"
# 用 Python 确定性打包 counter fixture → tar.gz
COUNTER_SRC="${DEMO_DIR}/skill_fixtures/counter-skill"
[ -d "${COUNTER_SRC}" ] || fail "counter fixture 不存在: ${COUNTER_SRC}"

COUNTER_TARBALL="${TMP_DIR}/counter-skill.tar.gz"
"${PYTHON_ARR[@]}" "${DEMO_DIR}/_lib.py" pack_bundle "${COUNTER_SRC}" "${COUNTER_TARBALL}" \
  || fail "打包 counter skill 失败"
[ -f "${COUNTER_TARBALL}" ] || fail "tarball 不存在"
ok "已打包 counter-skill.tar.gz ($(wc -c < "${COUNTER_TARBALL}") bytes)"

# 用 Step 3 创建的 skill_id 承载新 bundle（skill.md 会覆盖 system_prompt 但 DB 元数据保留）
# Windows Git Bash 里 curl 是 Windows 二进制，需要 Win 路径；用 cygpath 转
if command -v cygpath >/dev/null 2>&1; then
  UPLOAD_TARBALL_PATH="$(cygpath -w "${COUNTER_TARBALL}")"
else
  UPLOAD_TARBALL_PATH="${COUNTER_TARBALL}"
fi
UPLOAD_RESP=$(curl -sS -X POST "${BASE}/api/admin/skills/${CREATED_SKILL_ID}/bundle" \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "file=@${UPLOAD_TARBALL_PATH};type=application/gzip" \
  -F "change_log=demo SG-7 upload") \
  || fail "上传 bundle 失败: ${UPLOAD_RESP}"
CHANGED=$(get_json "${UPLOAD_RESP}" "changed") || fail "解析 changed 失败: ${UPLOAD_RESP}"
BUNDLE_HASH=$(get_json "${UPLOAD_RESP}" "bundle_hash") || fail "解析 bundle_hash 失败: ${UPLOAD_RESP}"
[ -n "${BUNDLE_HASH}" ] || fail "bundle_hash 空"
[ "${CHANGED}" = "True" ] || fail "changed 应该是 True（新 bundle）: ${UPLOAD_RESP}"
ok "上传成功 bundle_hash=${BUNDLE_HASH:0:16}... changed=${CHANGED}"

# 版本列表应至少 1 条 is_current=True
VERSIONS_RESP=$(curl -sS -H "Authorization: Bearer ${TOKEN}" "${BASE}/api/admin/skills/${CREATED_SKILL_ID}/versions")
echo "${VERSIONS_RESP}" | grep -qE '"is_current":\s*true' \
  || fail "版本列表没有 is_current=true: ${VERSIONS_RESP:0:400}"
ok "版本列表含 is_current=true ✓"

# ── Step 17: Script skill · chat 显式 skill_id 触发 sandbox 挂 counter（Sprint G）
step "17. Chat 显式 skill_id · sandbox 挂 counter.py 执行"
# 需要给 agent 也 bind sandbox_exec tool；先看 agent 是否已绑（Step 6 绑的是 CREATED_TOOL_ID）
# sandbox_exec 是内置 tool 通过 BUILTIN_TOOLS 自动挂到 skill，无需 bind
export SKILL_ID="${CREATED_SKILL_ID}"
render "${PAYLOAD_DIR}/chat_script_skill.json.tmpl" "${TMP_DIR}/chat_script_skill.json"
SESSION_SCRIPT="demo-script-${TS}"
sse_capture "${BASE}/api/super-agent/chat/stream" \
  "${TMP_DIR}/chat_script_skill.json" "${TMP_DIR}/sse_script.txt"
sse_has_event "content" "${TMP_DIR}/sse_script.txt" \
  || fail "script skill chat 无 content"
sse_has_event "sandbox_start" "${TMP_DIR}/sse_script.txt" \
  || fail "script skill 未触发 sandbox_start（LLM 应调 sandbox_exec 执行 counter）"
sse_has_event "sandbox_done" "${TMP_DIR}/sse_script.txt" \
  || fail "script skill 未触发 sandbox_done"
ok "SSE 收到 sandbox_start + sandbox_done ✓"

# ── Step 18: sandbox stdout 应含 COUNTER_OUTPUT= 前缀（挂进去的 script 真的被 import）
step "18. sandbox stdout · 含 COUNTER_OUTPUT= 前缀（证明 counter.py 挂进沙箱执行了）"
"${PYTHON_ARR[@]}" "${DEMO_DIR}/_lib.py" assert_sandbox_stdout "${SESSION_SCRIPT}" "COUNTER_OUTPUT=" \
  || fail "sandbox stdout 里没有 COUNTER_OUTPUT= 标记；LLM 可能没 import 挂载的 script"
ok "sandbox stdout 命中 COUNTER_OUTPUT= 前缀 ✓（counter.py 从 OBS bundle 挂进沙箱执行）"

# ═══════════════════════════════════════════════════
#  全绿
# ═══════════════════════════════════════════════════
echo
echo "${GREEN}════════════════════════════════════════════════${NC}"
echo "${GREEN} ✔ 全部 18 步通过${NC}"
echo "${GREEN}════════════════════════════════════════════════${NC}"
echo
echo "本次产物："
echo "  tool_id=${CREATED_TOOL_ID}"
echo "  skill_id=${CREATED_SKILL_ID}"
echo "  agent_id=${CREATED_AGENT_ID}"
echo "  sessions: ${SESSION_NORMAL} / ${SESSION_PII} / ${SESSION_STU} / ${SESSION_INJ} / ${SESSION_SBX} / ${SESSION_SCRIPT}"
echo
echo "${DIM}（KEEP_ARTIFACTS=1 可保留产物；默认 trap EXIT 会 DELETE）${NC}"
