# 数据库索引迁移执行脚本
# 远程数据库：202.205.160.9:3307
# 用户名：root
# 密码：123456
# 数据库：lea-platform-test

# 需要 Python 的 pymysql 或者本地安装 mysql-client

import io
import sys

import pymysql

# Windows 终端 UTF-8 输出修复
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def execute_migration(sql_file):
    """执行数据库迁移脚本"""
    try:
        # 连接数据库
        conn = pymysql.connect(
            host='202.205.160.9',
            port=3307,
            user='root',
            password='123456',
            database='lea-platform-test',
            charset='utf8mb4'
        )

        print("[OK] Connected to database")

        # 读取 SQL 文件
        with open(sql_file, encoding='utf-8') as f:
            sql_content = f.read()

        cursor = conn.cursor()

        # 执行 SQL（pymysql 需要逐条执行）
        statements = []
        current = []

        for line in sql_content.split('\n'):
            line = line.strip()
            if not line or line.startswith('--'):
                continue
            current.append(line)
            if line.endswith(';'):
                statements.append(' '.join(current))
                current = []

        for i, stmt in enumerate(statements, 1):
            if stmt.strip():
                print(f"\nExecuting statement {i}/{len(statements)}...")
                cursor.execute(stmt)
                result = cursor.fetchall()
                if result:
                    print(f"Result: {result}")
                print("[OK] Done")

        conn.commit()
        print("\n[OK] Migration completed successfully")

        cursor.close()
        conn.close()

        return True

    except Exception as e:
        print(f"[ERROR] Migration failed: {e}", file=sys.stderr)
        return False

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python run_migration.py <migration_file.sql>")
        sys.exit(1)

    sql_file = sys.argv[1]
    success = execute_migration(sql_file)
    sys.exit(0 if success else 1)
