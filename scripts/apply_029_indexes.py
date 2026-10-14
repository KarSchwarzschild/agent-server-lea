# 029 关键索引迁移（Python 控制幂等）
import io
import sys

import pymysql

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

conn = pymysql.connect(
    host='202.205.160.9',
    port=3307,
    user='root',
    password='123456',
    database='lea-platform-test',
    charset='utf8mb4'
)

cursor = conn.cursor()

def index_exists(table, index_name):
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND INDEX_NAME = %s
    """, (table, index_name))
    return cursor.fetchone()[0] > 0

print("[1/3] chat_sessions.idx_sessions_list")
if not index_exists('chat_sessions', 'idx_sessions_list'):
    cursor.execute("""
        ALTER TABLE `chat_sessions`
        ADD KEY `idx_sessions_list` (`tenant_id`, `user_id`, `is_archived`, `last_active_at`, `id`)
    """)
    print("  [OK] Created")
else:
    print("  [SKIP] Already exists")

print("\n[2/3] session_attachments.idx_session (rebuild)")
if index_exists('session_attachments', 'idx_session'):
    # 检查是否包含 id 列
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'session_attachments'
          AND INDEX_NAME = 'idx_session'
          AND COLUMN_NAME = 'id'
    """)
    has_id = cursor.fetchone()[0] > 0

    if not has_id:
        print("  [DROP] Old index without id column")
        cursor.execute("ALTER TABLE `session_attachments` DROP KEY `idx_session`")
        cursor.execute("""
            ALTER TABLE `session_attachments`
            ADD KEY `idx_session` (`tenant_id`, `session_id`, `id`)
        """)
        print("  [OK] Rebuilt with id column")
    else:
        print("  [SKIP] Already has id column")
else:
    cursor.execute("""
        ALTER TABLE `session_attachments`
        ADD KEY `idx_session` (`tenant_id`, `session_id`, `id`)
    """)
    print("  [OK] Created")

print("\n[3/3] chat_attachments.idx_attachment_session")
if not index_exists('chat_attachments', 'idx_attachment_session'):
    cursor.execute("""
        ALTER TABLE `chat_attachments`
        ADD KEY `idx_attachment_session` (`tenant_id`, `session_id`, `id`)
    """)
    print("  [OK] Created")
else:
    print("  [SKIP] Already exists")

print("\n[VERIFY] Checking all indexes...")
cursor.execute("""
    SELECT
      (SELECT COUNT(*) FROM information_schema.STATISTICS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_sessions'
           AND INDEX_NAME = 'idx_sessions_list')       AS sessions_list_cols,
      (SELECT COUNT(*) FROM information_schema.STATISTICS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'session_attachments'
           AND INDEX_NAME = 'idx_session')             AS attachment_session_id_cols,
      (SELECT COUNT(*) FROM information_schema.STATISTICS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_attachments'
           AND INDEX_NAME = 'idx_attachment_session')  AS attachment_session_cols
""")
result = cursor.fetchone()
print(f"  sessions_list: {result[0]} cols")
print(f"  session_attachments.idx_session: {result[1]} cols")
print(f"  attachment_session: {result[2]} cols")

if all(x > 0 for x in result):
    print("\n[SUCCESS] All indexes verified")
else:
    print("\n[ERROR] Some indexes missing!", file=sys.stderr)
    sys.exit(1)

conn.commit()
cursor.close()
conn.close()
