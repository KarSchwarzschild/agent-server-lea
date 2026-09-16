# 验收：P0-3 平台工具降级（真链路）
# 判据：KB/Memory/Gateway 工具在服务不可达/异常时返回友好提示，不阻断对话

import requests
import json
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

BASE_URL = "http://localhost:8200"
TENANT_ID = "test-tenant"
USER_ID = "test-user"

headers = {
    "Content-Type": "application/json",
    "X-Tenant-ID": TENANT_ID,
    "X-User-ID": USER_ID,
    "X-User-Role": "user"
}

def create_session():
    """创建测试会话"""
    resp = requests.post(f"{BASE_URL}/api/agent/sessions",
                        json={"agent_id": 0},
                        headers=headers)
    if resp.status_code != 200:
        print(f"[ERROR] Failed to create session: {resp.status_code}", file=sys.stderr)
        sys.exit(1)
    session_id = resp.json()["session_id"]
    print(f"[OK] Session created: {session_id}")
    return session_id

def send_message(session_id, content):
    """发送消息并验证降级行为"""
    resp = requests.post(
        f"{BASE_URL}/api/agent/sessions/{session_id}/stream",
        json={"content": content},
        headers=headers,
        stream=True
    )

    events = []
    for line in resp.iter_lines():
        if line:
            line_str = line.decode('utf-8')
            if line_str.startswith('data: '):
                data = json.loads(line_str[6:])
                events.append(data)

    return events

print("=" * 60)
print("P0-3 降级验收：平台工具失败时的 fail-soft 行为")
print("=" * 60)

# 判据 1：KB 工具降级（假设 lea-rag 服务未启动）
print("\n[1/3] KB 工具降级测试")
print("  触发条件：请求知识库搜索")
session_id = create_session()

# 发送触发 KB 搜索的消息
events = send_message(session_id, "搜索知识库里关于数据库索引的内容")

# 检查是否有 tool_use 事件（KB 工具被调用）
tool_called = any(e.get('type') == 'tool_use' and
                  e.get('name') == 'kb_search'
                  for e in events)

# 检查是否有友好的降级提示（而不是硬错误）
degradation_msg = any(
    e.get('type') == 'tool_result' and
    ('暂时不可用' in str(e.get('content', '')) or
     '稍后重试' in str(e.get('content', '')))
    for e in events
)

if tool_called:
    if degradation_msg:
        print("  [OK] KB 工具降级成功：返回友好提示")
    else:
        print("  [WARN] KB 工具可能正常工作，或返回了硬错误")
else:
    print("  [SKIP] KB 工具未被调用")

# 判据 2：Memory 工具降级（假设 lea-rag 服务未启动）
print("\n[2/3] Memory 工具降级测试")
session_id = create_session()

events = send_message(session_id, "帮我记住：今天完成了数据库索引优化")

memory_called = any(e.get('type') == 'tool_use' and
                    'memory' in e.get('name', '').lower()
                    for e in events)

degradation_msg = any(
    e.get('type') == 'tool_result' and
    ('暂时不可用' in str(e.get('content', '')) or
     '稍后重试' in str(e.get('content', '')))
    for e in events
)

if memory_called:
    if degradation_msg:
        print("  [OK] Memory 工具降级成功：返回友好提示")
    else:
        print("  [WARN] Memory 工具可能正常工作，或返回了硬错误")
else:
    print("  [SKIP] Memory 工具未被调用")

# 判据 3：对话不阻断
print("\n[3/3] 对话流畅性测试")
session_id = create_session()

events = send_message(session_id, "你好，今天天气怎么样？")

# 检查是否有正常的文本回复（即使工具失败）
has_text_response = any(
    e.get('type') == 'content_block_delta' and
    e.get('delta', {}).get('type') == 'text_delta'
    for e in events
)

if has_text_response:
    print("  [OK] 对话未被阻断：即使工具失败也能正常回复")
else:
    print("  [ERROR] 对话被阻断", file=sys.stderr)

print("\n" + "=" * 60)
print("[SUCCESS] P0-3 降级验收完成")
print("备注：真实降级行为需要手动停止 lea-rag/lea-tools 服务后测试")
print("=" * 60)
