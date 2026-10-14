"""执行 migrations/015_skill_versions.sql。"""

import os
import re
from pathlib import Path

import pymysql

sql = (Path(__file__).parent.parent / "migrations" / "015_skill_versions.sql").read_text(
    encoding="utf-8"
)
sql = re.sub(r"(?m)^\s*--.*$", "", sql)
conn = pymysql.connect(
    host=os.getenv("DB_HOST", "localhost"),
    port=int(os.getenv("DB_PORT", "3306")),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "lea_platform"),
    charset="utf8mb4",
    autocommit=False,
    client_flag=pymysql.constants.CLIENT.MULTI_STATEMENTS,
)
try:
    with conn.cursor() as cur:
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement and not statement.startswith("--"):
                cur.execute(statement)
    conn.commit()
    print("015 skill_versions migration completed")
except Exception:
    conn.rollback()
    raise
finally:
    conn.close()
