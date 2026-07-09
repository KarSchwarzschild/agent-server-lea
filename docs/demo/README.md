# Demo · Agent 全链路 e2e 冒烟

**定位**：内部开发 / QA 用的可断言 curl 脚本。串起 Sprint A（三层后端）+ Sprint · 安全护栏 v1 的所有能力。

## 覆盖能力

| # | 步骤 | 断言点 |
|---|---|---|
| 0 | GET /health | http=200 |
| 1 | POST /api/auth/login | access_token 非空 |
| 2 | POST /api/admin/tools | id > 0（source=api） |
| 3 | POST /api/admin/skills | id > 0, status=published |
| 4 | POST /api/admin/agents | id > 0 |
| 5 | POST /api/admin/agents/{id}/skills | binding_id 非空 |
| 6 | POST /api/admin/agents/{id}/tools | 200 OK |
| 7 | POST /api/super-agent/chat/stream · 普通 | SSE 有 content + audit `user_message` 无 PII |
| 8 | 同上 · 含 PII | audit `pii_kinds=phone,email` |
| 9 | 同上 · 学号 16 位 | audit `pii_kinds=-`（Luhn 挡下）|
| 10 | 同上 · Prompt 注入 | audit 存在 `injection_hit` 行 |

## 前置

1. 后端已跑：`uvicorn lea_server.main:app --port 8100`（`infra/docker-compose.dev.yml` 或 `packages/lea-server` 里的 `run.sh`）
2. 默认 admin 账号已 seed：`admin / admin123`
3. Python 环境能 import lea_server（`uv sync` 或 `pip install -e ./packages/lea-server` 已跑）

## 运行

```bash
# 默认（http://localhost:8100，跑完清理产物）
bash docs/demo_agent_flow.sh

# 换 host
LEA_BASE=http://192.168.1.100:8100 bash docs/demo_agent_flow.sh

# 保留 tool/skill/agent 便于手工回查（不清理）
KEEP_ARTIFACTS=1 bash docs/demo_agent_flow.sh

# python 二进制路径需要指定时
PYTHON=/opt/uv/venv/bin/python bash docs/demo_agent_flow.sh
```

**退出码**：0 = 全绿；任何一步失败非 0。适合直接接进 CI。

## 输出示例（成功）

```
════════════════════════════════════════════════
 LEA Platform · Agent 全链路 Demo
 BASE=http://localhost:8100  TS=20260707153020
════════════════════════════════════════════════

## 0. 健康检查
[ OK ] health=200

## 1. 登录 admin/admin123 → JWT
[ OK ] token=eyJhbGciOiJIUzI1NiIsInR5cCI...

## 2. 创建 demo Tool（source=api）
[ OK ] tool_id=17
      Demo Echo 工具

## 3. 创建 demo Skill（ReAct 模式）
[ OK ] skill_id=42  status=published

... (中间步骤省略) ...

## 10. Chat · Prompt 注入（warn 模式：chat 继续，audit 有 injection_hit）
[ OK ] SSE 收到 content（warn 模式不阻断）
[ OK ] audit: injection_hit 已记录 ✓

════════════════════════════════════════════════
 ✔ 全部 10 步通过
════════════════════════════════════════════════
```

## 目录结构

```
docs/
├── demo_agent_flow.sh              # 主脚本（bash）
└── demo/
    ├── README.md                   # 本文件
    ├── _lib.py                     # 模板渲染 / JSON 提取 / audit DB 断言
    └── payloads/
        ├── login.json              # 静态 payload
        ├── tool_create.json.tmpl   # __TS__ / __TOOL_ID__ / __SKILL_ID__ / __AGENT_ID__ 会被环境变量替换
        ├── skill_create.json.tmpl
        ├── agent_create.json.tmpl
        ├── skill_bind.json.tmpl
        ├── tool_bind.json.tmpl
        ├── chat_normal.json.tmpl
        ├── chat_pii.json.tmpl
        ├── chat_student.json.tmpl
        └── chat_injection.json.tmpl
```

**为什么 payload 用文件不用内联 `-d`**：Windows 终端 `-d "{"message":"你好"}"` 中文会 GBK 化，只有 `--data-binary "@file"` 才能保 UTF-8。这是历史踩坑经验（见 `~/.claude/memory/feedback_windows_curl_chinese.md`）。

## 常见问题

**Q: `python -c "from lea_server..."` ImportError**
A: `_lib.py` 顶部会把 `packages/lea-*/src` 加到 sys.path，不需要 `pip install -e`。但如果你的 venv 没装依赖（sqlalchemy 等），会失败。跑 `uv sync` 补依赖。

**Q: Windows Git Bash 里 mktemp 报错**
A: Git Bash 自带 `mktemp`。如果找不到，用 WSL 跑；或改用固定路径 `TMP_DIR="/tmp/lea-demo-$$"`。

**Q: SSE 卡住 30 秒才继续**
A: `--max-time 30` 是兜底。如果后端 chat 走 LLM 耗时很久，这个数字可能需要调大。改脚本里 `sse_capture` 的 `--max-time`。

**Q: 想只跑其中几步**
A: 目前设计是原子的 —— 后面步骤依赖前面的 id。要选跑，就在中间某步 `exit 0` 手动截断。
